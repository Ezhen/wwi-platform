"""
WWI ERA5 Lean Ingest — targets ~50MB instead of 700MB
Strategy:
  - Daily means only (not hourly) — 24x smaller
  - Only swvl1 (soil moisture) + tp (precipitation)
  - No temperature (captured by sin_doy/cos_doy already)
  - 2023-2025 only
  - One request per year
"""

import cdsapi, netCDF4, numpy as np, sqlite3, zipfile, logging
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
DB_ERA5 = str(ROOT / "export/databases/era5_liege.db")
TMP     = ROOT / "era5_extract"
TMP.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

BBOX  = [51.0, 4.5, 49.5, 6.5]   # N W S E — Liège basin
YEARS = ["2023", "2024", "2025"]

# ── DB init ───────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_ERA5, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS grid_points (
            id  INTEGER PRIMARY KEY AUTOINCREMENT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
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
        CREATE INDEX IF NOT EXISTS idx_era5_var_ts
            ON era5_observations(variable, timestamp);
        CREATE INDEX IF NOT EXISTS idx_era5_grid
            ON era5_observations(grid_id);
    """)
    con.commit()
    return con


def upsert_grid(con, lats, lons):
    for lat in lats:
        for lon in lons:
            con.execute("INSERT OR IGNORE INTO grid_points (lat,lon) VALUES (?,?)",
                        (round(float(lat),4), round(float(lon),4)))
    con.commit()
    return {(round(r[1],4), round(r[2],4)): r[0]
            for r in con.execute("SELECT id,lat,lon FROM grid_points")}


def already_ingested(con, variable, year):
    n = con.execute("""
        SELECT COUNT(*) FROM era5_observations
        WHERE variable=? AND timestamp LIKE ?
    """, (variable, f"{year}%")).fetchone()[0]
    return n > 100


def ingest_nc(con, nc_path, grid_ids):
    """Ingest NC file — aggregate hourly to daily mean on the fly."""
    log.info(f"  Parsing {nc_path.name}...")
    ds = netCDF4.Dataset(str(nc_path))
    log.info(f"  Variables: {list(ds.variables.keys())}")

    lat_k = "latitude" if "latitude" in ds.variables else "lat"
    lon_k = "longitude" if "longitude" in ds.variables else "lon"
    lats  = ds.variables[lat_k][:]
    lons  = ds.variables[lon_k][:]

    # Update grid IDs with this file's grid
    grid_ids = upsert_grid(con, lats, lons)

    tk = next((k for k in ["valid_time","time"] if k in ds.variables), None)
    if not tk:
        log.error(f"  No time variable"); ds.close(); return 0

    times = netCDF4.num2date(
        ds.variables[tk][:], ds.variables[tk].units,
        only_use_cftime_datetimes=False,
        only_use_python_datetimes=True,
    )

    # Variable name mapping
    var_map = {
        "tp":    "total_precipitation",
        "swvl1": "volumetric_soil_water_layer_1",
    }

    total = 0
    for nc_var, db_var in var_map.items():
        if nc_var not in ds.variables:
            continue

        data = ds.variables[nc_var][:]  # (time, lat, lon)

        # Aggregate to daily means
        from collections import defaultdict
        daily = defaultdict(lambda: defaultdict(list))

        for t_idx, ts in enumerate(times):
            day_str = ts.strftime("%Y-%m-%d")
            for li, lat in enumerate(lats):
                for lo, lon in enumerate(lons):
                    val = float(data[t_idx, li, lo])
                    if np.ma.is_masked(val) or np.isnan(val):
                        continue
                    gid = grid_ids.get((round(float(lat),4),
                                        round(float(lon),4)))
                    if gid:
                        daily[day_str][(gid, db_var)].append(val)

        # Insert daily means
        rows = []
        for day_str, cells in daily.items():
            for (gid, var), vals in cells.items():
                rows.append((gid, var, day_str, round(np.mean(vals), 8)))

        con.executemany("""
            INSERT OR IGNORE INTO era5_observations
                (grid_id, variable, timestamp, value)
            VALUES (?,?,?,?)
        """, rows)
        con.commit()
        log.info(f"  {db_var}: {len(rows):,} daily rows inserted")
        total += len(rows)

    ds.close()
    nc_path.unlink()
    log.info(f"  Deleted {nc_path.name}")
    return total


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    from pathlib import Path as _P

    log.info("=" * 55)
    log.info("WWI ERA5 Lean Ingest — daily means, swvl1 + tp only")
    log.info("=" * 55)

    # Delete corrupted DB if exists
    db_path = _P(DB_ERA5)
    for ext in ["", "-wal", "-shm", "-journal"]:
        f = _P(DB_ERA5 + ext)
        if f.exists():
            f.unlink()
            log.info(f"Removed {f.name}")

    client = cdsapi.Client(quiet=True)
    con    = init_db()
    grid_ids = {}

    for year in YEARS:
        months = ["01","02","03","04","05","06"] \
                 if year == "2025" else \
                 [f"{m:02d}" for m in range(1,13)]

        log.info(f"\n── {year} ({len(months)} months) ─────────────────────")

        # Single request per year — both variables together
        out_zip = TMP / f"era5_lean_{year}.zip"

        if not out_zip.exists():
            log.info(f"Requesting swvl1 + tp for {year} (4 timesteps/day)...")
            client.retrieve(
                "reanalysis-era5-single-levels", {
                    "product_type": "reanalysis",
                    "variable":     [
                        "volumetric_soil_water_layer_1",
                        "total_precipitation",
                    ],
                    "year":   year,
                    "month":  months,
                    "day":    [f"{d:02d}" for d in range(1,32)],
                    "time":   ["00:00","06:00","12:00","18:00"],
                    "area":   BBOX,
                    "format": "netcdf",
                },
                str(out_zip),
            )
            sz = out_zip.stat().st_size / 1024 / 1024
            log.info(f"Downloaded {sz:.1f} MB")

        # Extract NC
        nc_path = None
        if zipfile.is_zipfile(str(out_zip)):
            with zipfile.ZipFile(out_zip) as zf:
                nc_files = [n for n in zf.namelist() if n.endswith(".nc")]
                if nc_files:
                    zf.extract(nc_files[0], TMP)
                    nc_path = TMP / nc_files[0]
                    out_zip.unlink()
        else:
            nc_path = out_zip.with_suffix(".nc")
            out_zip.rename(nc_path)

        if nc_path and nc_path.exists():
            ingest_nc(con, nc_path, grid_ids)
        else:
            log.error(f"No NC file for {year}")

    # Summary
    log.info("\n" + "=" * 55)
    for r in con.execute("""
        SELECT variable, COUNT(*) AS n,
               MIN(timestamp), MAX(timestamp)
        FROM era5_observations GROUP BY variable
    """):
        log.info(f"  {r[0]:<45} n={r[1]:>7,}  {r[2][:10]} → {r[3][:10]}")

    import os
    sz = os.path.getsize(DB_ERA5)/1024/1024
    log.info(f"\nDB size: {sz:.0f} MB")
    log.info("✓ Done")
    con.close()
