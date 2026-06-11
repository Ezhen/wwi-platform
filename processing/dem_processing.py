"""
WWI DEM Processing Pipeline
1. Download Copernicus GLO-30 tiles for Liège/Meuse basin
2. Mosaic and clip to bbox
3. Compute slope raster
4. Delineate catchments for SPW stations using pysheds
5. Compute per-catchment statistics (area, mean slope, ERA5 weights)
6. Save to catchments_liege.db

Bounding box: Liège/Meuse basin
  Lat: 49.5 → 51.0 N  (tiles N49, N50)
  Lon:  4.5 →  6.5 E  (tiles E004, E005, E006)
"""

import os
import sqlite3
import numpy as np
import requests
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ROOT     = Path(__file__).parent.parent
DEM_DIR  = ROOT / "supplementary" / "dem"
OUT_DIR  = ROOT / "export"
DB_CATCH = str(ROOT / "export" / "databases" / "catchments_liege.db")

DEM_DIR.mkdir(parents=True, exist_ok=True)

# Liège basin bounding box (WGS84)
BBOX = {"min_lat": 49.5, "max_lat": 51.0,
        "min_lon":  4.5, "max_lon":  6.5}

# SPW stations with known coordinates (from spw_liege.db)
# (station_no, label, river, lat, lon)
STATIONS = [
    ("6387",  "EUPEN",           "Vesdre",    50.640,  6.048),
    ("6228",  "CHAUDFONTAINE",   "Vesdre",    50.583,  5.638),
    ("5904",  "COMBLAIN",        "Ourthe",    50.472,  5.578),
    ("5826",  "SAUHEID",         "Ourthe",    50.590,  5.530),
    ("6732",  "STAVELOT",        "Amblève",   50.390,  5.930),
    ("6832",  "TROIS-PONTS",     "Salm",      50.368,  5.863),
    ("7141",  "HUY",             "Meuse",     50.519,  5.239),
    ("7133",  "LIEGE",           "Meuse",     50.640,  5.573),
    ("6657",  "LOUVEIGNE",       "Ourthe",    50.551,  5.686),
    ("6958",  "ROBERTVILLE",     "Vesdre",    50.445,  6.096),
    ("6529",  "MONT-RIGI",       "Amblève",   50.497,  6.097),
]

# ERA5 grid points over Liège basin
ERA5_POINTS = [
    (50.75, 5.00), (50.75, 5.25), (50.75, 5.50),
    (50.75, 5.75), (50.75, 6.00), (50.75, 6.25), (50.75, 6.50),
    (50.50, 5.00), (50.50, 5.25), (50.50, 5.50),
    (50.50, 5.75), (50.50, 6.00), (50.50, 6.25), (50.50, 6.50),
    (50.25, 5.00), (50.25, 5.25), (50.25, 5.50),
    (50.25, 5.75), (50.25, 6.00), (50.25, 6.25), (50.25, 6.50),
    (50.00, 5.00), (50.00, 5.25), (50.00, 5.50),
    (50.00, 5.75), (50.00, 6.00), (50.00, 6.25), (50.00, 6.50),
    (49.75, 5.00), (49.75, 5.25), (49.75, 5.50),
    (49.75, 5.75), (49.75, 6.00), (49.75, 6.25), (49.75, 6.50),
    (49.50, 5.00), (49.50, 5.25), (49.50, 5.50),
    (49.50, 5.75), (49.50, 6.00), (49.50, 6.25), (49.50, 6.50),
]


# ── Step 1: Download GLO-30 tiles ─────────────────────────────────────────────

def tile_name(lat, lon):
    """Copernicus GLO-30 tile naming: N50_E005 etc."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{ns}{abs(lat):02d}_00_{ew}{abs(lon):03d}_00"

def download_tile(lat, lon):
    """Download one GLO-30 tile from AWS public bucket."""
    name    = tile_name(lat, lon)
    folder  = f"Copernicus_DSM_COG_10_{name}_DEM"
    fname   = f"{folder}/{folder}.tif"
    url     = f"https://copernicus-dem-30m.s3.amazonaws.com/{fname}"
    out_path = DEM_DIR / f"dem_{name}.tif"

    if out_path.exists():
        log.info(f"  Tile {name} already downloaded")
        return str(out_path)

    log.info(f"  Downloading {name} from AWS...")
    r = requests.get(url, stream=True, timeout=60)
    if r.status_code == 404:
        log.warning(f"  Tile {name} not found (ocean tile?)")
        return None
    r.raise_for_status()

    size = 0
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            f.write(chunk)
            size += len(chunk)
    log.info(f"  Downloaded {name} ({size/1024/1024:.1f} MB)")
    return str(out_path)


def download_all_tiles():
    """Download all tiles covering the Liège bbox."""
    log.info("=" * 55)
    log.info("Step 1: Downloading GLO-30 DEM tiles")
    log.info("=" * 55)

    tiles = []
    for lat in range(int(BBOX["min_lat"]), int(BBOX["max_lat"]) + 1):
        for lon in range(int(BBOX["min_lon"]), int(BBOX["max_lon"]) + 1):
            path = download_tile(lat, lon)
            if path:
                tiles.append(path)

    log.info(f"  Downloaded {len(tiles)} tiles")
    return tiles


# ── Step 2: Mosaic and clip ───────────────────────────────────────────────────

def mosaic_and_clip(tile_paths):
    """Mosaic tiles and clip to bbox."""
    import rasterio
    from rasterio.merge import merge
    from rasterio.mask import mask
    from shapely.geometry import box
    import json

    log.info("\nStep 2: Mosaicking tiles...")
    mosaic_path = str(DEM_DIR / "dem_liege_mosaic.tif")

    if Path(mosaic_path).exists():
        log.info("  Mosaic already exists")
        return mosaic_path

    datasets = [rasterio.open(p) for p in tile_paths]
    mosaic, transform = merge(datasets)
    meta = datasets[0].meta.copy()
    meta.update({"driver": "GTiff", "height": mosaic.shape[1],
                 "width": mosaic.shape[2], "transform": transform,
                 "compress": "lzw"})

    with rasterio.open(mosaic_path, "w", **meta) as dst:
        dst.write(mosaic)

    for ds in datasets:
        ds.close()

    log.info(f"  Mosaic saved → {mosaic_path}")
    return mosaic_path


# ── Step 3: Compute slope ─────────────────────────────────────────────────────

def compute_slope(dem_path):
    """Compute slope in degrees from DEM."""
    import rasterio
    import numpy as np

    slope_path = str(DEM_DIR / "slope_liege.tif")
    if Path(slope_path).exists():
        log.info("\nStep 3: Slope already computed")
        return slope_path

    log.info("\nStep 3: Computing slope...")

    with rasterio.open(dem_path) as src:
        dem   = src.read(1).astype(float)
        res_x = abs(src.transform.a)  # pixel width in degrees
        res_y = abs(src.transform.e)  # pixel height in degrees
        nodata = src.nodata
        meta   = src.meta.copy()

    # Convert resolution to metres (approximate)
    res_m = res_x * 111320  # ~30m at these latitudes

    # Replace nodata
    if nodata:
        dem[dem == nodata] = np.nan

    # Sobel gradient
    from scipy.ndimage import sobel
    dz_dx = sobel(dem, axis=1) / (8 * res_m)
    dz_dy = sobel(dem, axis=0) / (8 * res_m)
    slope  = np.degrees(np.arctan(np.sqrt(dz_dx**2 + dz_dy**2)))

    meta.update({"dtype": "float32", "compress": "lzw", "nodata": -9999})
    with rasterio.open(slope_path, "w", **meta) as dst:
        slope_out = slope.astype("float32")
        slope_out[np.isnan(slope_out)] = -9999
        dst.write(slope_out, 1)

    log.info(f"  Slope range: {np.nanmin(slope):.1f}° → {np.nanmax(slope):.1f}°")
    log.info(f"  Mean slope: {np.nanmean(slope):.1f}°")
    log.info(f"  Slope saved → {slope_path}")
    return slope_path


# ── Step 4: Catchment delineation ─────────────────────────────────────────────

def delineate_catchments(dem_path):
    """Delineate catchments using pysheds 0.5 API."""
    from pysheds.grid import Grid
    import numpy as np

    log.info("\nStep 4: Delineating catchments with pysheds 0.5...")

    results = {}
    dirmap = (64, 128, 1, 2, 4, 8, 16, 32)

    def condition_dem(dem_path):
        """Fresh conditioning for each station."""
        grid = Grid.from_raster(dem_path)
        dem  = grid.read_raster(dem_path)
        pit  = grid.fill_pits(dem)
        dep  = grid.fill_depressions(pit)
        flat = grid.resolve_flats(dep)
        fdir = grid.flowdir(flat, dirmap=dirmap)
        acc  = grid.accumulation(fdir, dirmap=dirmap)
        return grid, fdir, acc

    log.info("  Initial DEM conditioning (this takes ~5 min)...")
    grid, fdir, acc = condition_dem(dem_path)
    log.info(f"  Flow accumulation range: {int(acc.min())} → {int(acc.max())} cells")

    for sno, label, river, lat, lon in STATIONS:
        log.info(f"\n  Delineating: {label} ({river}) at ({lat:.3f}, {lon:.3f})")
        try:
            # Snap pour point — search within 0.05° radius (~5km)
            # Use local maximum of accumulation near station
            import rasterio
            from rasterio.transform import rowcol

            with rasterio.open(dem_path) as src:
                transform = src.transform
                nrows, ncols = src.height, src.width

            # Find row/col of station
            row, col = rowcol(transform, lon, lat)
            window = 50  # search window in pixels (~1.5km at 30m)
            r0, r1 = max(0, row-window), min(nrows, row+window)
            c0, c1 = max(0, col-window), min(ncols, col+window)

            # Get accumulation in window
            acc_arr = np.array(acc)
            window_acc = acc_arr[r0:r1, c0:c1]
            max_idx = np.unravel_index(window_acc.argmax(), window_acc.shape)
            snap_row = r0 + max_idx[0]
            snap_col = c0 + max_idx[1]

            # Convert back to coordinates
            import affine
            snap_lon = transform.c + snap_col * transform.a + 0.5 * transform.a
            snap_lat = transform.f + snap_row * transform.e + 0.5 * transform.e
            snap_acc = int(acc_arr[snap_row, snap_col])

            log.info(f"    Snapped ({lon:.3f},{lat:.3f}) → ({snap_lon:.4f},{snap_lat:.4f})  acc={snap_acc:,}")

            if snap_acc < 100:
                raise ValueError(f"Low accumulation at snap point ({snap_acc}) — not on river")

            # Delineate catchment
            catch = grid.catchment(
                x=snap_lon, y=snap_lat,
                fdir=fdir, dirmap=dirmap,
                xytype="coordinate"
            )

            # pysheds 0.5: catch is a Raster object, convert to numpy
            catch_arr = np.array(catch).astype(np.uint8)
            n_cells   = int(catch_arr.sum())
            area_km2  = round(n_cells * 900 / 1e6, 1)  # 30m pixels = 900m²

            log.info(f"    Area: {area_km2:.1f} km²  ({n_cells:,} cells)")

            # Save catchment raster
            catch_path = str(DEM_DIR / f"catchment_{sno}.tif")
            with rasterio.open(dem_path) as src:
                meta = src.meta.copy()
                meta.update({"dtype": "uint8", "nodata": 0, "compress": "lzw"})
                with rasterio.open(catch_path, "w", **meta) as dst:
                    # Pad or crop to match DEM dimensions
                    out = np.zeros((src.height, src.width), dtype=np.uint8)
                    h = min(catch_arr.shape[0], src.height)
                    w = min(catch_arr.shape[1], src.width)
                    out[:h, :w] = catch_arr[:h, :w]
                    dst.write(out, 1)

            results[sno] = {
                "label":      label,
                "river":      river,
                "lat":        lat,
                "lon":        lon,
                "area_km2":   area_km2,
                "n_cells":    n_cells,
                "catch_path": catch_path,
            }

        except Exception as e:
            log.error(f"    Failed: {e}")
            results[sno] = {
                "label": label, "river": river,
                "lat": lat, "lon": lon,
                "area_km2": None, "error": str(e)
            }

        # Recondition for next station (accumulation unchanged, just reset grid view)
        grid, fdir, acc = condition_dem(dem_path)

    return results


# ── Step 5: Catchment statistics ──────────────────────────────────────────────

def compute_catchment_stats(catchment_results, slope_path):
    """Compute mean slope and ERA5 weights per catchment."""
    import rasterio
    import numpy as np
    from rasterio.transform import rowcol

    log.info("\nStep 5: Computing catchment statistics...")

    with rasterio.open(slope_path) as slope_src:
        slope_data = slope_src.read(1).astype(float)
        slope_data[slope_data == -9999] = np.nan
        slope_transform = slope_src.transform
        slope_crs = slope_src.crs

    for sno, info in catchment_results.items():
        if "error" in info or not info.get("catch_path"):
            continue

        try:
            with rasterio.open(info["catch_path"]) as catch_src:
                catch_mask  = catch_src.read(1).astype(bool)
                catch_trans = catch_src.transform
                catch_bounds = catch_src.bounds

            # Resample catchment mask to slope grid if needed
            # Simple approach: use catchment bounds to clip slope
            row_min, col_min = rowcol(slope_transform,
                                      catch_bounds.left, catch_bounds.top)
            row_max, col_max = rowcol(slope_transform,
                                      catch_bounds.right, catch_bounds.bottom)
            row_min, row_max = sorted([row_min, row_max])
            col_min, col_max = sorted([col_min, col_max])

            # Clip slope to catchment bbox
            row_min = max(0, row_min)
            col_min = max(0, col_min)
            row_max = min(slope_data.shape[0]-1, row_max)
            col_max = min(slope_data.shape[1]-1, col_max)

            slope_clip = slope_data[row_min:row_max, col_min:col_max]
            mean_slope = float(np.nanmean(slope_clip))

            info["mean_slope_deg"] = round(mean_slope, 2)
            log.info(f"  {info['label']:<20} area={info['area_km2']:>7.1f} km²  "
                     f"slope={mean_slope:.1f}°")

            # ERA5 weights — which grid cells fall in catchment
            era5_weights = {}
            for era5_lat, era5_lon in ERA5_POINTS:
                # Check if ERA5 cell centre falls within catchment bounds
                if (catch_bounds.left <= era5_lon <= catch_bounds.right and
                    catch_bounds.bottom <= era5_lat <= catch_bounds.top):
                    era5_weights[f"{era5_lat:.2f}_{era5_lon:.2f}"] = 1.0

            # Normalise weights
            if era5_weights:
                total = sum(era5_weights.values())
                era5_weights = {k: round(v/total, 4)
                                for k, v in era5_weights.items()}
            info["era5_weights"] = era5_weights
            log.info(f"    ERA5 cells in catchment: {len(era5_weights)}")

        except Exception as e:
            log.error(f"  {info['label']}: stats failed — {e}")

    return catchment_results


# ── Step 6: Save to DB ────────────────────────────────────────────────────────

def save_to_db(catchment_results):
    """Save catchment statistics to SQLite."""
    import json

    log.info("\nStep 6: Saving to database...")

    con = sqlite3.connect(DB_CATCH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS catchments (
            station_no    TEXT PRIMARY KEY,
            label         TEXT,
            river         TEXT,
            lat           REAL,
            lon           REAL,
            area_km2      REAL,
            mean_slope_deg REAL,
            n_cells       INTEGER,
            era5_weights  TEXT   -- JSON dict of ERA5 grid weights
        );

        CREATE TABLE IF NOT EXISTS era5_catchment_weights (
            station_no  TEXT,
            era5_lat    REAL,
            era5_lon    REAL,
            weight      REAL,
            PRIMARY KEY (station_no, era5_lat, era5_lon)
        );
    """)

    for sno, info in catchment_results.items():
        if "error" in info:
            continue
        weights = info.get("era5_weights", {})
        con.execute("""
            INSERT OR REPLACE INTO catchments
                (station_no, label, river, lat, lon, area_km2,
                 mean_slope_deg, n_cells, era5_weights)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (sno, info["label"], info["river"],
              info["lat"], info["lon"],
              info.get("area_km2"),
              info.get("mean_slope_deg"),
              info.get("n_cells"),
              json.dumps(weights)))

        for key, w in weights.items():
            lat_s, lon_s = key.split("_")
            con.execute("""
                INSERT OR REPLACE INTO era5_catchment_weights
                    (station_no, era5_lat, era5_lon, weight)
                VALUES (?,?,?,?)
            """, (sno, float(lat_s), float(lon_s), w))

    con.commit()

    # Summary
    log.info(f"\n── Catchment summary ────────────────────────────────")
    for row in con.execute("""
        SELECT label, river, area_km2, mean_slope_deg,
               json_array_length(era5_weights) AS n_era5
        FROM catchments ORDER BY river, label
    """):
        log.info(f"  {row[1]:<10} {row[0]:<20} "
                 f"area={row[2]:>7.1f} km²  "
                 f"slope={row[3]:>5.1f}°  "
                 f"ERA5={row[4]} cells")

    con.close()
    log.info(f"\nDB → {DB_CATCH}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 55)
    log.info("WWI DEM Processing Pipeline")
    log.info("=" * 55)

    # Install scipy if needed
    try:
        from scipy.ndimage import sobel
    except ImportError:
        log.info("Installing scipy...")
        os.system("pip install scipy --break-system-packages -q")

    # Step 1: Download
    tiles = download_all_tiles()
    if not tiles:
        log.error("No tiles downloaded — check internet connection")
        exit(1)

    # Step 2: Mosaic
    mosaic_path = mosaic_and_clip(tiles)

    # Step 3: Slope
    slope_path = compute_slope(mosaic_path)

    # Step 4: Catchments
    catchment_results = delineate_catchments(mosaic_path)

    # Step 5: Statistics
    catchment_results = compute_catchment_stats(catchment_results, slope_path)

    # Step 6: Save
    save_to_db(catchment_results)

    log.info("\n✓ DEM pipeline complete")
    log.info("Next: python build_features_v2.py  (add slope + ERA5 weights)")
