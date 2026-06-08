from pathlib import Path
ROOT = Path(__file__).parent.parent
DB_ERA5 = str(ROOT / "export/databases/era5_liege.db")
"""
ERA5 Ingestion — Precipitation, Temperature, Soil Moisture
Bounding box: Liège/Meuse basin
Stores in SQLite era5_liege.db
"""

import cdsapi
import netCDF4 as nc
import numpy as np
import sqlite3
import logging
from datetime import datetime, timedelta, timezone

# --- Config ---
DB_PATH = str(DB_ERA5)
DAYS_BACK = 30
NC_FILE   = "era5_download.nc"

# Bounding box: [N, W, S, E]
BBOX = [50.9, 5.0, 49.3, 6.5]

VARIABLES = [
    "total_precipitation",
    "2m_temperature",
    "volumetric_soil_water_layer_1",
    "mean_total_precipitation_rate",  # fallback name in some ERA5 products
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Database ──────────────────────────────────────────────────────────────────

def init_db(db_path):
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS grid_points (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            lat       REAL NOT NULL,
            lon       REAL NOT NULL,
            UNIQUE(lat, lon)
        );

        CREATE TABLE IF NOT EXISTS era5_observations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            grid_id      INTEGER NOT NULL REFERENCES grid_points(id),
            variable     TEXT    NOT NULL,
            timestamp    TEXT    NOT NULL,
            value        REAL,
            UNIQUE(grid_id, variable, timestamp)
        );

        CREATE INDEX IF NOT EXISTS idx_era5_var       ON era5_observations(variable);
        CREATE INDEX IF NOT EXISTS idx_era5_timestamp ON era5_observations(timestamp);
        CREATE INDEX IF NOT EXISTS idx_era5_grid      ON era5_observations(grid_id);
    """)
    con.commit()
    return con


def get_or_create_grid_point(con, lat, lon):
    cur = con.execute(
        "SELECT id FROM grid_points WHERE lat=? AND lon=?", (lat, lon)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO grid_points (lat, lon) VALUES (?,?)", (lat, lon)
    )
    con.commit()
    return cur.lastrowid


# ── ERA5 Download ─────────────────────────────────────────────────────────────

def download_era5(days_back, output_file):
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)

    # ERA5 has ~5 day latency — request up to 5 days ago
    end_date   = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    start_date = start.strftime("%Y-%m-%d")

    years  = sorted(set([start.strftime("%Y"), (now - timedelta(days=5)).strftime("%Y")]))
    months = sorted(set([
        d.strftime("%m")
        for d in [start + timedelta(days=i) for i in range(days_back)]
    ]))
    days   = [f"{d:02d}" for d in range(1, 32)]
    hours  = [f"{h:02d}:00" for h in range(0, 24)]

    log.info(f"Downloading ERA5: {start_date} → {end_date}")
    log.info(f"Variables: {VARIABLES}")
    log.info(f"Bbox: {BBOX}")

    c = cdsapi.Client()
    c.retrieve(
        "reanalysis-era5-single-levels",
        {
            "product_type": "reanalysis",
            "variable":     VARIABLES,
            "year":         years,
            "month":        months,
            "day":          days,
            "time":         hours,
            "area":         BBOX,
            "format":       "netcdf",
        },
        output_file,
    )
    log.info(f"Downloaded → {output_file}")


# ── Parse & Store ─────────────────────────────────────────────────────────────

VAR_MAP = {
    "tp":         "total_precipitation",      # m — hourly accumulation
    "t2m":        "2m_temperature",           # K
    "swvl1":      "soil_water_layer_1",       # m³/m³
    "avg_tprate": "mean_precipitation_rate",  # kg/m²/s
}


def parse_and_store(nc_file, con):
    ds = nc.Dataset(nc_file)

    log.info(f"NetCDF variables: {list(ds.variables.keys())}")

    lats = ds.variables["latitude"][:]
    lons = ds.variables["longitude"][:]
    # Time dimension is "valid_time" in new CDS format (seconds since 1970-01-01)
    time_var = "valid_time" if "valid_time" in ds.variables else "time"
    raw_times = ds.variables[time_var][:]
    t_units   = ds.variables[time_var].units
    try:
        times = nc.num2date(raw_times, units=t_units, calendar="standard")
    except Exception:
        # Fallback: treat as unix timestamps
        from datetime import datetime, timezone
        times = [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in raw_times]

    log.info(f"Grid: {len(lats)} lats × {len(lons)} lons")
    log.info(f"Timesteps: {len(times)}")
    log.info(f"Time range: {times[0]} → {times[-1]}")

    # Pre-cache grid point IDs
    grid_ids = {}
    for lat in lats:
        for lon in lons:
            gid = get_or_create_grid_point(con, float(lat), float(lon))
            grid_ids[(float(lat), float(lon))] = gid
    log.info(f"Grid points registered: {len(grid_ids)}")

    total_inserted = 0

    for nc_var, label in VAR_MAP.items():
        if nc_var not in ds.variables:
            log.warning(f"  Variable {nc_var} not in NetCDF — skipping")
            continue

        data = ds.variables[nc_var][:]  # shape: (time, lat, lon)
        log.info(f"  Processing {label} ({nc_var}) shape={data.shape}")

        rows = []
        for t_idx, t in enumerate(times):
            try:
                ts = datetime(t.year, t.month, t.day,
                              t.hour, getattr(t, "minute", 0),
                              tzinfo=timezone.utc).isoformat()
            except Exception:
                ts = str(t)
            for lat_idx, lat in enumerate(lats):
                for lon_idx, lon in enumerate(lons):
                    val = data[t_idx, lat_idx, lon_idx]
                    if np.ma.is_masked(val):
                        continue
                    gid = grid_ids[(float(lat), float(lon))]
                    rows.append((gid, label, ts, float(val)))

        con.executemany("""
            INSERT OR IGNORE INTO era5_observations
                (grid_id, variable, timestamp, value)
            VALUES (?,?,?,?)
        """, rows)
        con.commit()
        log.info(f"    → {len(rows)} rows inserted for {label}")
        total_inserted += len(rows)

    ds.close()
    return total_inserted


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 55)
    log.info("ERA5 Ingestion — Liège/Meuse basin")
    log.info("=" * 55)

    # Download
    if Path(NC_FILE).exists():
        log.info(f"NetCDF already exists ({NC_FILE}) — skipping download")
        log.info("Delete it to force re-download")
    else:
        download_era5(DAYS_BACK, NC_FILE)

    # Unzip if CDS returned a zip archive (newer API behaviour)
    nc_files = []
    with open(NC_FILE, "rb") as f:
        magic = f.read(2)
    if magic == b"PK":
        import zipfile
        log.info("CDS returned a ZIP archive — extracting all NC files...")
        with zipfile.ZipFile(NC_FILE, "r") as zf:
            members = [m for m in zf.namelist() if m.endswith(".nc")]
            log.info(f"  NC files in ZIP: {members}")
            for m in members:
                zf.extract(m, path="era5_extract")
                nc_files.append(f"era5_extract/{m}")
                log.info(f"  Extracted → era5_extract/{m}")
    else:
        nc_files = [NC_FILE]

    # Parse and store all NC files
    con = init_db(DB_PATH)
    n = 0
    for nc_path in nc_files:
        log.info(f"Parsing {nc_path}...")
        n += parse_and_store(nc_path, con)

    # Summary
    log.info("=" * 55)
    cur = con.execute(
        "SELECT variable, COUNT(*), COUNT(DISTINCT grid_id), "
        "MIN(timestamp), MAX(timestamp) "
        "FROM era5_observations GROUP BY variable"
    )
    for row in cur.fetchall():
        log.info(f"  {row[0]:<25} {row[1]:>8,} obs  "
                 f"{row[2]:>3} grid pts  {row[3][:10]} → {row[4][:10]}")

    cur = con.execute("SELECT COUNT(*) FROM grid_points")
    log.info(f"  Grid points: {cur.fetchone()[0]}")
    log.info(f"  DB → {Path(DB_PATH).resolve()}")
    con.close()
