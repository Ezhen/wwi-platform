"""
WWI — CGLS NDVI 300m Ingest
Downloads 10-day NDVI composites for the Liège/Meuse basin
from Copernicus Global Land Service via direct HTTP + bbox clip.

Strategy: download full file, extract Belgium bbox, discard rest.
Files are ~600MB global but Belgium slice is ~1MB.

Requirements:
  - Copernicus account at land.copernicus.eu/global/access
  - pip install netCDF4 numpy requests

Usage:
  export CGLS_USER="your_username"
  export CGLS_PASS="your_password"
  python ndvi_ingest.py
"""

import os
import sqlite3
import netCDF4
import numpy as np
import requests
import logging
from pathlib import Path
from datetime import date, timedelta
from io import BytesIO

ROOT    = Path(__file__).parent.parent
DB_NDVI = str(ROOT / "export/databases/ndvi_liege.db")
TMP     = ROOT / "era5_extract" / "ndvi_tmp"
TMP.mkdir(parents=True, exist_ok=True)

# Liège/Meuse basin bbox
LAT_MIN, LAT_MAX = 49.5, 51.0
LON_MIN, LON_MAX =  4.5,  6.5

# Training period + current
YEAR_START = 2023
YEAR_END   = 2026

BASE_URL = "https://globalland.vito.be/download/netcdf/ndvi/ndvi_300m_v2_10daily"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def get_credentials():
    user = os.environ.get("CGLS_USER", "")
    pw   = os.environ.get("CGLS_PASS", "")
    if not user or not pw:
        log.error("Set environment variables: CGLS_USER and CGLS_PASS")
        log.error("Register free at: https://land.copernicus.eu/global/access")
        exit(1)
    return user, pw


def dekad_dates(year):
    """Generate 10-day composite dates for a year (1st, 11th, 21st of each month)."""
    dates = []
    for month in range(1, 13):
        for day in [1, 11, 21]:
            try:
                dates.append(date(year, month, day))
            except ValueError:
                pass
    return dates


def ndvi_url(dt):
    """Build download URL for a specific dekad date."""
    yyyymmdd = dt.strftime("%Y%m%d")
    year     = dt.strftime("%Y")
    # Version pattern — try V2.0.1 first, fall back if needed
    fname = f"c_gls_NDVI300_{yyyymmdd}0000_GLOBE_OLCI_V2.0.1.nc"
    return f"{BASE_URL}/{year}/{yyyymmdd}/{fname}", fname


def init_db():
    con = sqlite3.connect(DB_NDVI)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS ndvi_grid (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            lat  REAL NOT NULL,
            lon  REAL NOT NULL,
            UNIQUE(lat, lon)
        );

        CREATE TABLE IF NOT EXISTS ndvi_observations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            grid_id      INTEGER NOT NULL,
            dekad_date   TEXT    NOT NULL,
            ndvi         REAL,
            quality_flag INTEGER,
            UNIQUE(grid_id, dekad_date)
        );

        CREATE TABLE IF NOT EXISTS ndvi_catchment_stats (
            station_no   TEXT    NOT NULL,
            dekad_date   TEXT    NOT NULL,
            ndvi_mean    REAL,
            ndvi_std     REAL,
            n_pixels     INTEGER,
            PRIMARY KEY (station_no, dekad_date)
        );

        CREATE INDEX IF NOT EXISTS idx_ndvi_grid
            ON ndvi_observations(grid_id);
        CREATE INDEX IF NOT EXISTS idx_ndvi_date
            ON ndvi_observations(dekad_date);
    """)
    con.commit()
    return con


def already_ingested(con, dekad_date):
    n = con.execute(
        "SELECT COUNT(*) FROM ndvi_observations WHERE dekad_date=?",
        (str(dekad_date),)
    ).fetchone()[0]
    return n > 50  # at least 50 pixels for Belgium


def upsert_grid(con, lats, lons):
    """Insert grid points, return id mapping."""
    grid_ids = {}
    for lat in lats:
        for lon in lons:
            lat_r = round(float(lat), 4)
            lon_r = round(float(lon), 4)
            con.execute(
                "INSERT OR IGNORE INTO ndvi_grid (lat, lon) VALUES (?,?)",
                (lat_r, lon_r)
            )
    con.commit()
    rows = con.execute("SELECT id, lat, lon FROM ndvi_grid").fetchall()
    for gid, lat, lon in rows:
        grid_ids[(round(lat, 4), round(lon, 4))] = gid
    return grid_ids


def download_and_extract(url, fname, user, pw):
    """
    Download full NetCDF, extract Belgium bbox, return arrays.
    Uses streaming to avoid loading 600MB into memory.
    """
    # Try to stream directly — if too slow, fall back to full download
    tmp_file = TMP / fname

    if not tmp_file.exists():
        log.info(f"  Downloading {fname} (~600MB, please wait)...")
        r = requests.get(url, auth=(user, pw), stream=True, timeout=120)
        if r.status_code == 404:
            # Try alternative version numbers
            for ver in ["V2.0.2", "V2.0.3", "V2.1.0", "V2.1.1"]:
                alt_url = url.replace("V2.0.1", ver)
                alt_fname = fname.replace("V2.0.1", ver)
                r2 = requests.get(alt_url, auth=(user, pw), stream=True, timeout=60)
                if r2.status_code == 200:
                    r = r2
                    fname = alt_fname
                    log.info(f"  Found version {ver}")
                    break
            else:
                log.warning(f"  Not found: {fname}")
                return None, None, None

        r.raise_for_status()
        size = 0
        with open(tmp_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024*1024):  # 1MB chunks
                f.write(chunk)
                size += len(chunk)
        log.info(f"  Downloaded {size/1024/1024:.0f} MB → {tmp_file.name}")
    else:
        log.info(f"  Using cached {fname}")

    # Extract Belgium bbox
    ds   = netCDF4.Dataset(str(tmp_file))
    lats = ds.variables["lat"][:]
    lons = ds.variables["lon"][:]

    # Find bbox indices
    lat_mask = (lats >= LAT_MIN) & (lats <= LAT_MAX)
    lon_mask = (lons >= LON_MIN) & (lons <= LON_MAX)
    lat_idx  = np.where(lat_mask)[0]
    lon_idx  = np.where(lon_mask)[0]

    if len(lat_idx) == 0 or len(lon_idx) == 0:
        log.error("  No pixels found in bbox — check coordinate names")
        ds.close()
        return None, None, None

    lat_slice = slice(lat_idx[0], lat_idx[-1]+1)
    lon_slice = slice(lon_idx[0], lon_idx[-1]+1)

    ndvi_raw = ds.variables["NDVI"][0, lat_slice, lon_slice]
    qflag    = ds.variables["QFLAG"][0, lat_slice, lon_slice] \
               if "QFLAG" in ds.variables else None

    # NDVI scaling: raw values are uint8, scale/offset in attributes
    scale  = getattr(ds.variables["NDVI"], "scale_factor",  0.004)
    offset = getattr(ds.variables["NDVI"], "add_offset",   -0.08)
    fill   = getattr(ds.variables["NDVI"], "_FillValue",    255)

    lats_sub = lats[lat_slice]
    lons_sub = lons[lon_slice]

    ds.close()

    # Convert to float NDVI
    ndvi_float = np.where(
        ndvi_raw == fill,
        np.nan,
        ndvi_raw.astype(float) * scale + offset
    )

    log.info(f"  Belgium pixels: {lat_slice.stop-lat_slice.start} × "
             f"{lon_slice.stop-lon_slice.start}  "
             f"NDVI range: {np.nanmin(ndvi_float):.3f} → {np.nanmax(ndvi_float):.3f}")

    # Delete full file to save disk space
    tmp_file.unlink()
    log.info(f"  Deleted {fname} (Belgium data extracted)")

    return lats_sub, lons_sub, ndvi_float, qflag


def ingest_dekad(con, dt, user, pw):
    """Download and ingest one 10-day composite."""
    url, fname = ndvi_url(dt)
    result = download_and_extract(url, fname, user, pw)

    if result[0] is None:
        return 0

    lats_sub, lons_sub, ndvi_float, qflag = result

    # Upsert grid
    grid_ids = upsert_grid(con, lats_sub, lons_sub)

    # Insert observations
    rows = []
    for lat_i, lat in enumerate(lats_sub):
        for lon_i, lon in enumerate(lons_sub):
            lat_r = round(float(lat), 4)
            lon_r = round(float(lon), 4)
            gid   = grid_ids.get((lat_r, lon_r))
            if gid is None: continue
            val   = float(ndvi_float[lat_i, lon_i])
            if np.isnan(val): continue
            qf    = int(qflag[lat_i, lon_i]) if qflag is not None else None
            rows.append((gid, str(dt), val, qf))

    con.executemany("""
        INSERT OR IGNORE INTO ndvi_observations
            (grid_id, dekad_date, ndvi, quality_flag)
        VALUES (?,?,?,?)
    """, rows)
    con.commit()
    log.info(f"  Inserted {len(rows)} pixels for {dt}")
    return len(rows)


def compute_catchment_ndvi(con):
    """Compute mean NDVI per watershed catchment from catchments_liege.db."""
    db_catch = str(ROOT / "export/databases/catchments_liege.db")
    if not Path(db_catch).exists():
        log.warning("catchments_liege.db not found — skipping catchment stats")
        return

    import json
    con_c = sqlite3.connect(db_catch)
    catchments = con_c.execute(
        "SELECT station_no, era5_weights FROM catchments WHERE era5_weights IS NOT NULL"
    ).fetchall()
    con_c.close()

    # For each station, get ERA5 bbox and average NDVI in that region
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT dekad_date FROM ndvi_observations ORDER BY dekad_date"
    ).fetchall()]

    log.info(f"\nComputing catchment NDVI for {len(catchments)} stations "
             f"× {len(dates)} dekads...")

    for sno, weights_json in catchments:
        weights = json.loads(weights_json)
        if not weights: continue

        # Get lat/lon range from ERA5 weights
        lats = [float(k.split("_")[0]) for k in weights.keys()]
        lons = [float(k.split("_")[1]) for k in weights.keys()]
        lat_min_c = min(lats) - 0.15
        lat_max_c = max(lats) + 0.15
        lon_min_c = min(lons) - 0.15
        lon_max_c = max(lons) + 0.15

        for dt_str in dates:
            result = con.execute("""
                SELECT AVG(o.ndvi), COALESCE(STDEV(o.ndvi), 0), COUNT(*)
                FROM ndvi_observations o
                JOIN ndvi_grid g ON o.grid_id = g.id
                WHERE o.dekad_date = ?
                  AND g.lat BETWEEN ? AND ?
                  AND g.lon BETWEEN ? AND ?
                  AND o.ndvi > -0.1
            """, (dt_str, lat_min_c, lat_max_c, lon_min_c, lon_max_c)).fetchone()

            if result and result[0] is not None:
                con.execute("""
                    INSERT OR REPLACE INTO ndvi_catchment_stats
                        (station_no, dekad_date, ndvi_mean, ndvi_std, n_pixels)
                    VALUES (?,?,?,?,?)
                """, (sno, dt_str,
                      round(result[0], 4),
                      round(result[1], 4) if result[1] else None,
                      result[2]))

    con.commit()
    log.info("  Catchment NDVI stats computed")

    # Sample
    for r in con.execute("""
        SELECT station_no, dekad_date, ndvi_mean, n_pixels
        FROM ndvi_catchment_stats
        ORDER BY dekad_date DESC, station_no
        LIMIT 10
    """):
        log.info(f"  {r[0]:8s}  {r[1]}  NDVI={r[2]:.3f}  n={r[3]}")


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("WWI CGLS NDVI 300m Ingest — 2023-2025")
    log.info("=" * 60)

    user, pw = get_credentials()
    log.info(f"User: {user}")

    con = init_db()

    total = 0
    for year in range(YEAR_START, YEAR_END + 1):
        log.info(f"\n── Year {year} ─────────────────────────────────────────")
        for dt in dekad_dates(year):
            if dt > date.today(): break
            if already_ingested(con, dt):
                log.info(f"  {dt} already ingested — skip")
                continue
            log.info(f"\n  Dekad: {dt}")
            n = ingest_dekad(con, dt, user, pw)
            total += n

    # Compute catchment stats
    compute_catchment_ndvi(con)

    # Summary
    log.info("\n" + "=" * 60)
    n_obs  = con.execute("SELECT COUNT(*) FROM ndvi_observations").fetchone()[0]
    n_grid = con.execute("SELECT COUNT(*) FROM ndvi_grid").fetchone()[0]
    n_stats = con.execute(
        "SELECT COUNT(*) FROM ndvi_catchment_stats"
    ).fetchone()[0]
    log.info(f"Grid points:         {n_grid:,}")
    log.info(f"Observations:        {n_obs:,}")
    log.info(f"Catchment stats:     {n_stats:,}")
    log.info(f"\nDB → {DB_NDVI}")
    log.info("✓ Done — run: python build_features_v2.py  (add NDVI features)")
    con.close()
