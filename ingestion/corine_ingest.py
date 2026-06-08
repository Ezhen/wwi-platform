from pathlib import Path
ROOT = Path(__file__).parent.parent
DB_CORINE = str(ROOT / "export/databases/corine_liege.db")
"""
CORINE Land Cover 2018 — Belgium
Download, clip to Liège province bounding box, store in SQLite.

Source: NGI Belgium / geo.be
Direct download — no registration required.
"""

import requests
import sqlite3
import zipfile
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = str(DB_CORINE)
ZIP_FILE = "clc2018_be_3812.zip"
EXTRACT  = "corine_extract"

# Liège province bounding box (WGS84)
# We'll clip to this after loading
BBOX_WGS84 = {
    "min_lon": 5.35, "max_lon": 6.40,
    "min_lat": 50.15, "max_lat": 50.75,
}

# Direct download URL — NGI Belgium open data
# CLC2018 Belgium Shapefile
DOWNLOAD_URL = "https://ac.ngi.be/remoteclient-open/ngi-standard-open/Vectordata/CorineLandcover/CLC2018/ad1ad903-f45d-43b0-8ced-d3c0e376efee_x-shapefile_3812.zip"

# Fallback: EEA ATOM feed direct link
FALLBACK_URL = "https://land.copernicus.eu/en/products/corine-land-cover/clc2018"

# CLC class labels (level 1-3 hierarchy)
CLC_CLASSES = {
    111: ("Urban fabric", "Continuous urban fabric", 1),
    112: ("Urban fabric", "Discontinuous urban fabric", 1),
    121: ("Industrial/commercial", "Industrial or commercial units", 1),
    122: ("Industrial/commercial", "Road and rail networks", 1),
    123: ("Industrial/commercial", "Port areas", 1),
    124: ("Industrial/commercial", "Airports", 1),
    131: ("Mine/dump/construction", "Mineral extraction sites", 1),
    132: ("Mine/dump/construction", "Dump sites", 1),
    133: ("Mine/dump/construction", "Construction sites", 1),
    141: ("Urban green", "Green urban areas", 1),
    142: ("Urban green", "Sport and leisure facilities", 1),
    211: ("Arable land", "Non-irrigated arable land", 2),
    212: ("Arable land", "Permanently irrigated land", 2),
    213: ("Arable land", "Rice fields", 2),
    221: ("Permanent crops", "Vineyards", 2),
    222: ("Permanent crops", "Fruit trees and berry plantations", 2),
    223: ("Permanent crops", "Olive groves", 2),
    231: ("Pastures", "Pastures", 2),
    241: ("Heterogeneous agricultural", "Annual crops associated", 2),
    242: ("Heterogeneous agricultural", "Complex cultivation patterns", 2),
    243: ("Heterogeneous agricultural", "Land principally occupied by agriculture", 2),
    244: ("Heterogeneous agricultural", "Agro-forestry areas", 2),
    311: ("Forests", "Broad-leaved forest", 3),
    312: ("Forests", "Coniferous forest", 3),
    313: ("Forests", "Mixed forest", 3),
    321: ("Scrub/herbaceous", "Natural grasslands", 3),
    322: ("Scrub/herbaceous", "Moors and heathland", 3),
    323: ("Scrub/herbaceous", "Sclerophyllous vegetation", 3),
    324: ("Scrub/herbaceous", "Transitional woodland-shrub", 3),
    331: ("Open spaces", "Beaches, dunes, sands", 3),
    332: ("Open spaces", "Bare rocks", 3),
    333: ("Open spaces", "Sparsely vegetated areas", 3),
    334: ("Open spaces", "Burnt areas", 3),
    335: ("Open spaces", "Glaciers and perpetual snow", 3),
    411: ("Inland wetlands", "Inland marshes", 4),
    412: ("Inland wetlands", "Peat bogs", 4),
    421: ("Maritime wetlands", "Salt marshes", 4),
    422: ("Maritime wetlands", "Salines", 4),
    423: ("Maritime wetlands", "Intertidal flats", 4),
    511: ("Water bodies", "Water courses", 5),
    512: ("Water bodies", "Water bodies", 5),
    521: ("Marine waters", "Coastal lagoons", 5),
    522: ("Marine waters", "Estuaries", 5),
    523: ("Marine waters", "Sea and ocean", 5),
}


def init_db(db_path):
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS clc_classes (
            code        INTEGER PRIMARY KEY,
            level1      TEXT,
            label       TEXT,
            runoff_type INTEGER  -- 1=high, 2=medium, 3=low (impervious→pervious)
        );

        CREATE TABLE IF NOT EXISTS land_cover (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            clc_code    INTEGER NOT NULL REFERENCES clc_classes(code),
            area_ha     REAL,
            centroid_lon REAL,
            centroid_lat REAL,
            geom_wkt    TEXT    -- WKT geometry for Power BI / PostGIS later
        );

        CREATE INDEX IF NOT EXISTS idx_lc_code ON land_cover(clc_code);
    """)
    # Load class labels
    con.executemany(
        "INSERT OR IGNORE INTO clc_classes (code, level1, label, runoff_type) "
        "VALUES (?,?,?,?)",
        [(k, v[0], v[1], v[2]) for k, v in CLC_CLASSES.items()]
    )
    con.commit()
    return con


def download_clc():
    if Path(ZIP_FILE).exists():
        log.info(f"ZIP already exists ({ZIP_FILE}) — skipping download")
        return True

    log.info(f"Downloading CLC2018 Belgium...")
    log.info(f"URL: {DOWNLOAD_URL}")

    try:
        r = requests.get(DOWNLOAD_URL, stream=True, timeout=60)
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(ZIP_FILE, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
        size_mb = Path(ZIP_FILE).stat().st_size / 1024**2
        log.info(f"Downloaded → {ZIP_FILE} ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        log.error(f"Download failed: {e}")
        log.warning("Manual download required:")
        log.warning("  1. Go to: https://www.geo.be/catalog/details/ad1ad903-f45d-43b0-8ced-d3c0e376efee")
        log.warning("  2. Download the Shapefile package")
        log.warning(f"  3. Save as {ZIP_FILE} in this directory")
        return False


def find_shapefile(extract_dir):
    """Find the main CLC shapefile in the extracted directory."""
    for pattern in ["*CLC18*.shp", "*clc18*.shp", "*CLC2018*.shp", "*.shp"]:
        matches = list(Path(extract_dir).rglob(pattern))
        if matches:
            # Prefer the status layer (not change layer)
            status = [m for m in matches if "CHA" not in m.name.upper()]
            return status[0] if status else matches[0]
    return None


def process_clc(shp_path, con):
    """Load shapefile, clip to Liège bbox, store in DB."""
    try:
        import geopandas as gpd
        from shapely.geometry import box

        log.info(f"Loading {shp_path}...")
        gdf = gpd.read_file(shp_path)
        log.info(f"  Total polygons: {len(gdf)}  CRS: {gdf.crs}")
        log.info(f"  Columns: {list(gdf.columns)}")

        # Reproject to WGS84 for bbox clip
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            log.info(f"  Reprojecting to WGS84...")
            gdf = gdf.to_crs(epsg=4326)

        # Clip to Liège bounding box
        bbox = box(BBOX_WGS84["min_lon"], BBOX_WGS84["min_lat"],
                   BBOX_WGS84["max_lon"], BBOX_WGS84["max_lat"])
        gdf_liege = gdf[gdf.geometry.intersects(bbox)].copy()
        log.info(f"  After Liège bbox clip: {len(gdf_liege)} polygons")

        # Find CLC code column
        code_col = None
        for col in ["CODE_18", "code_18", "CLC_CODE", "CODE", "clc_code"]:
            if col in gdf_liege.columns:
                code_col = col
                break
        if not code_col:
            log.warning(f"  CLC code column not found. Columns: {list(gdf_liege.columns)}")
            code_col = gdf_liege.columns[0]

        log.info(f"  Using code column: {code_col}")
        log.info(f"  Unique classes: {sorted(gdf_liege[code_col].unique())}")

        # Store polygons
        rows = []
        for _, row in gdf_liege.iterrows():
            try:
                code = int(str(row[code_col]).replace(".", ""))
                geom = row.geometry
                centroid = geom.centroid
                area_ha  = geom.area * (111320**2) / 10000  # rough m² → ha in WGS84
                rows.append((
                    code,
                    round(area_ha, 2),
                    round(centroid.x, 6),
                    round(centroid.y, 6),
                    geom.wkt[:500],  # truncate very large geometries
                ))
            except Exception as e:
                continue

        con.executemany("""
            INSERT INTO land_cover (clc_code, area_ha, centroid_lon, centroid_lat, geom_wkt)
            VALUES (?,?,?,?,?)
        """, rows)
        con.commit()
        log.info(f"  Inserted {len(rows)} polygons into DB")
        return len(rows)

    except ImportError:
        log.error("geopandas not installed. Run: pip install geopandas --break-system-packages")
        return 0


def summarise(con):
    print("\n── Land cover summary (Liège bbox) ──────────────────")
    for row in con.execute("""
        SELECT c.level1, c.label, c.code,
               COUNT(l.id) AS n_polys,
               ROUND(SUM(l.area_ha), 1) AS total_ha
        FROM land_cover l
        JOIN clc_classes c ON l.clc_code = c.code
        GROUP BY c.code
        ORDER BY total_ha DESC
    """):
        print(f"  {row[2]:3d}  {row[1]:<45}  "
              f"{row[3]:>4} polys  {row[4]:>10.1f} ha")


if __name__ == "__main__":
    log.info("=" * 55)
    log.info("CORINE Land Cover 2018 — Liège")
    log.info("=" * 55)

    # 1. Download
    ok = download_clc()
    if not ok:
        log.error("Cannot proceed without data file.")
        exit(1)

    # 2. Extract
    if not Path(EXTRACT).exists():
        log.info(f"Extracting {ZIP_FILE}...")
        with zipfile.ZipFile(ZIP_FILE) as zf:
            zf.extractall(EXTRACT)
            log.info(f"  Files: {zf.namelist()[:10]}")

    # 3. Find shapefile
    shp = find_shapefile(EXTRACT)
    if not shp:
        log.error(f"No shapefile found in {EXTRACT}/")
        log.info(f"Contents: {list(Path(EXTRACT).rglob('*'))}")
        exit(1)
    log.info(f"Shapefile: {shp}")

    # 4. Process
    con = init_db(DB_PATH)
    n   = process_clc(shp, con)

    # 5. Summary
    if n > 0:
        summarise(con)
        log.info(f"\nDB → {Path(DB_PATH).resolve()}")

    con.close()
