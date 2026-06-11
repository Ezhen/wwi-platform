"""
WWI — ERA5 Historical Ingest
Downloads ERA5 reanalysis for 2023-2025 for the Liège/Meuse basin.
Variables: total_precipitation, 2m_temperature, volumetric_soil_water_layer_1

Uses Copernicus CDS API (free, requires ~/.cdsapirc with key).
Register at: https://cds.climate.copernicus.eu
"""

import cdsapi
import sqlite3
import netCDF4
import numpy as np
import logging
import os
from pathlib import Path
from datetime import datetime

ROOT   = Path(__file__).parent.parent
DB_ERA5 = str(ROOT / "export/databases/era5_liege.db")
TMP    = ROOT / "era5_extract"
TMP.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Liège/Meuse basin bbox
BBOX  = [51.0, 4.5, 49.5, 6.5]  # N, W, S, E
YEARS = ["2023", "2024", "2025"]
MONTHS_ALL = [f"{m:02d}" for m in range(1, 13)]
MONTHS_2025 = [f"{m:02d}" for m in range(1, 7)]  # Jan-Jun 2025

VARIABLES = [
    "total_precipitation",
    "2m_temperature",
    "volumetric_soil_water_layer_1",
]

# ── DB setup ──────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_ERA5, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS grid_points (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            lat  REAL NOT NULL,
            lon  REAL NOT NULL,
            UNIQUE(lat, lon)
        );
        CREATE TABLE IF NOT EXISTS era5_observations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            grid_id   INTEGER NOT NULL,
            variable  TEXT    NOT NULL,
            timestamp TEXT    NOT NULL,
            value     REAL,
            UNIQUE(grid_id, variable, timestamp)
        );
        CREATE INDEX IF NOT EXISTS idx_era5_grid
            ON era5_observations(grid_id);
        CREATE INDEX IF NOT EXISTS idx_era5_var_ts
            ON era5_observations(variable, timestamp);
    """)
    con.commit()
    return con


def upsert_grid_points(con, lats, lons):
    """Insert grid points, return id mapping."""
    grid_ids = {}
    for lat in lats:
        for lon in lons:
            con.execute(
                "INSERT OR IGNORE INTO grid_points (lat, lon) VALUES (?,?)",
                (round(float(lat), 4), round(float(lon), 4))
            )
    con.commit()
    rows = con.execute("SELECT id, lat, lon FROM grid_points").fetchall()
    for gid, lat, lon in rows:
        grid_ids[(round(lat, 4), round(lon, 4))] = gid
    return grid_ids


def insert_observations(con, grid_ids, variable, timestamps, values_2d):
    """Bulk insert ERA5 observations."""
    rows = []
    for t_idx, ts in enumerate(timestamps):
        for lat_idx in range(values_2d.shape[1]):
            for lon_idx in range(values_2d.shape[2]):
                val = float(values_2d[t_idx, lat_idx, lon_idx])
                if np.isnan(val): continue
                # Identify grid point
                # (will match by position in loop — needs lat/lon arrays)
                rows.append((val, ts))  # placeholder

    con.executemany("""
        INSERT OR IGNORE INTO era5_observations
            (grid_id, variable, timestamp, value)
        VALUES (?,?,?,?)
    """, rows)
    con.commit()


def already_ingested(con, variable, year, month):
    """Check if data for this variable/year/month already exists."""
    ts_prefix = f"{year}-{month:02d}" if isinstance(month, int) else f"{year}-{month}"
    n = con.execute("""
        SELECT COUNT(*) FROM era5_observations
        WHERE variable=? AND timestamp LIKE ?
    """, (variable, f"{ts_prefix}%")).fetchone()[0]
    return n > 0


# ── Download ──────────────────────────────────────────────────────────────────

def download_year_month(client, year, months, variables, out_path):
    """Download ERA5 monthly batch via CDS API."""
    if out_path.exists():
        log.info(f"  Already downloaded: {out_path.name}")
        return True

    log.info(f"  Requesting ERA5 {year} months={months} vars={variables}...")
    try:
        client.retrieve(
            "reanalysis-era5-single-levels",
            {
                "product_type": "reanalysis",
                "variable":     variables,
                "year":         year,
                "month":        months,
                "day":          [f"{d:02d}" for d in range(1, 32)],
                "time":         [f"{h:02d}:00" for h in range(0, 24)],
                "area":         BBOX,
                "format":       "netcdf",
            },
            str(out_path),
        )
        log.info(f"  Downloaded {out_path.stat().st_size/1024/1024:.1f} MB")
        return True
    except Exception as e:
        log.error(f"  Download failed: {e}")
        return False


# ── Ingest NC file ────────────────────────────────────────────────────────────

def ingest_nc(con, nc_path, expected_vars):
    """Parse NetCDF and insert into DB."""
    log.info(f"  Ingesting {nc_path.name}...")

    ds    = netCDF4.Dataset(str(nc_path))
    lat_key = "latitude" if "latitude" in ds.variables else "lat"
    lon_key = "longitude" if "longitude" in ds.variables else "lon"
    lats  = ds.variables[lat_key][:]
    lons  = ds.variables[lon_key][:]
    # Detect time variable name
    time_key = next((k for k in ["time", "valid_time", "forecast_reference_time"]
                     if k in ds.variables), None)
    if time_key is None:
        log.error(f"    No time variable found. Available: {list(ds.variables.keys())}")
        ds.close()
        return
    log.info(f"    Time variable: {time_key}  ({len(ds.variables[time_key])} steps)")

    times = netCDF4.num2date(
        ds.variables[time_key][:],
        ds.variables[time_key].units,
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )

    # Ensure grid points exist
    grid_ids = upsert_grid_points(con, lats, lons)

    var_map = {
        "tp":    "total_precipitation",
        "t2m":   "2m_temperature",
        "swvl1": "volumetric_soil_water_layer_1",
        "total_precipitation":             "total_precipitation",
        "2m_temperature":                  "2m_temperature",
        "volumetric_soil_water_layer_1":   "volumetric_soil_water_layer_1",
    }
    # Log available variables
    log.info(f"    NC variables: {list(ds.variables.keys())}")

    total_inserted = 0
    for nc_var, db_var in var_map.items():
        if nc_var not in ds.variables:
            continue
        data = ds.variables[nc_var][:]  # shape: (time, lat, lon)

        rows = []
        for t_idx, ts in enumerate(times):
            ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S")
            for lat_idx, lat in enumerate(lats):
                for lon_idx, lon in enumerate(lons):
                    val = float(data[t_idx, lat_idx, lon_idx])
                    if np.ma.is_masked(val) or np.isnan(val):
                        continue
                    gid = grid_ids.get(
                        (round(float(lat), 4), round(float(lon), 4))
                    )
                    if gid is None:
                        continue
                    rows.append((gid, db_var, ts_str, val))

            if len(rows) > 50000:
                con.executemany("""
                    INSERT OR IGNORE INTO era5_observations
                        (grid_id, variable, timestamp, value)
                    VALUES (?,?,?,?)
                """, rows)
                con.commit()
                total_inserted += len(rows)
                rows = []

        if rows:
            con.executemany("""
                INSERT OR IGNORE INTO era5_observations
                    (grid_id, variable, timestamp, value)
                VALUES (?,?,?,?)
            """, rows)
            con.commit()
            total_inserted += len(rows)

        n_var = con.execute(
            "SELECT COUNT(*) FROM era5_observations WHERE variable=?",
            (db_var,)
        ).fetchone()[0]
        log.info(f"    {db_var}: {total_inserted:,} rows inserted  "
                 f"(total in DB: {n_var:,})")
        total_inserted = 0

    ds.close()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("WWI ERA5 Historical Ingest — 2023-2025")
    log.info("=" * 60)

    # Check CDS API key
    cdsapirc = Path.home() / ".cdsapirc"
    if not cdsapirc.exists():
        log.error("~/.cdsapirc not found!")
        log.error("Register at https://cds.climate.copernicus.eu")
        log.error("Then create ~/.cdsapirc with:")
        log.error("  url: https://cds.climate.copernicus.eu/api/v2")
        log.error("  key: YOUR-UID:YOUR-API-KEY")
        exit(1)

    log.info(f"CDS API key found at {cdsapirc}")

    client = cdsapi.Client(quiet=True)
    con    = init_db()

    # Current DB state
    n_existing = con.execute(
        "SELECT COUNT(*) FROM era5_observations"
    ).fetchone()[0]
    log.info(f"Existing observations in DB: {n_existing:,}")

    # Download and ingest year by year
    for year in YEARS:
        months = MONTHS_2025 if year == "2025" else MONTHS_ALL
        log.info(f"\n── Year {year} ({len(months)} months) ─────────────────────")

        # Download in two batches (H1 and H2) to keep file sizes manageable
        batches = [
            (months[:6], f"era5_{year}_H1.nc"),
            (months[6:], f"era5_{year}_H2.nc"),
        ] if len(months) > 6 else [
            (months, f"era5_{year}_all.nc"),
        ]

        for batch_months, fname in batches:
            if not batch_months:
                continue
            nc_path = TMP / fname

            ok = download_year_month(
                client, year, batch_months, VARIABLES, nc_path
            )
            if ok:
                # Check actual file format
                import struct
                with open(nc_path, "rb") as fh:
                    magic = fh.read(4)

                if magic[:2] == b"PK":
                    # It's a ZIP — extract NC inside
                    import zipfile
                    log.info(f"  File is ZIP — extracting...")
                    with zipfile.ZipFile(nc_path) as zf:
                        nc_files = [n for n in zf.namelist()
                                    if n.endswith(".nc")]
                        if nc_files:
                            extracted = TMP / nc_files[0]
                            zf.extract(nc_files[0], TMP)
                            nc_path.unlink()
                            nc_path = extracted
                            log.info(f"  Extracted → {nc_path.name}")
                        else:
                            log.error("  No .nc file in ZIP")
                            continue

                ingest_nc(con, nc_path, VARIABLES)

    # Final summary
    log.info("\n" + "=" * 60)
    log.info("Final DB state:")
    for row in con.execute("""
        SELECT variable, COUNT(*) AS n,
               MIN(timestamp) AS t_min,
               MAX(timestamp) AS t_max
        FROM era5_observations
        GROUP BY variable
    """):
        log.info(f"  {row[0]:<40} n={row[1]:>8,}  "
                 f"{row[2][:10]} → {row[3][:10]}")

    n_total = con.execute(
        "SELECT COUNT(*) FROM era5_observations"
    ).fetchone()[0]
    log.info(f"\nTotal: {n_total:,} observations")
    log.info(f"DB → {DB_ERA5}")
    log.info("\n✓ Done — run: python build_features_v2.py")
    con.close()
