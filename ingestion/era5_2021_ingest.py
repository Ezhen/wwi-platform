"""
ERA5 swvl1 for July 2021 flood period — targeted download.
June-September 2021, soil moisture only, 4 timesteps/day.
~5MB download, fills the critical gap for flood model training.
"""

import cdsapi, netCDF4, numpy as np, sqlite3, zipfile, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_ERA5 = str(ROOT / "../export/databases/era5_liege.db")
TMP     = ROOT / "../era5_extract"
TMP.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

BBOX = [51.0, 4.5, 49.5, 6.5]

def already_have(con):
    n = con.execute("""
        SELECT COUNT(*) FROM era5_observations
        WHERE variable LIKE '%soil%'
          AND timestamp LIKE '2021%'
    """).fetchone()[0]
    return n > 100

con = sqlite3.connect(DB_ERA5, timeout=30)
con.execute("PRAGMA journal_mode=WAL")

if already_have(con):
    log.info("2021 swvl1 already in DB — skipping download")
    n = con.execute("""
        SELECT COUNT(*) FROM era5_observations
        WHERE variable LIKE '%soil%' AND timestamp LIKE '2021%'
    """).fetchone()[0]
    log.info(f"Existing 2021 soil moisture rows: {n:,}")
    con.close()
    exit(0)

log.info("Downloading ERA5 swvl1 for 2021 flood period...")
client = cdsapi.Client(quiet=True)
out = TMP / "era5_swvl1_2021.zip"

if not out.exists():
    client.retrieve(
        "reanalysis-era5-single-levels", {
            "product_type": "reanalysis",
            "variable":     ["volumetric_soil_water_layer_1"],
            "year":         "2021",
            "month":        ["05","06","07","08","09","10"],
            "day":          [f"{d:02d}" for d in range(1,32)],
            "time":         ["00:00","06:00","12:00","18:00"],
            "area":         BBOX,
            "format":       "netcdf",
        },
        str(out),
    )
    log.info(f"Downloaded {out.stat().st_size/1024/1024:.1f} MB")

# Extract
nc_path = TMP / "era5_swvl1_2021.nc"
if zipfile.is_zipfile(str(out)):
    with zipfile.ZipFile(out) as zf:
        nc_files = [n for n in zf.namelist() if n.endswith(".nc")]
        if nc_files:
            zf.extract(nc_files[0], TMP)
            (TMP / nc_files[0]).rename(nc_path)
            out.unlink()
            log.info(f"Extracted → {nc_path.name}")
else:
    out.rename(nc_path)

# Parse and ingest
log.info("Ingesting...")
ds  = netCDF4.Dataset(str(nc_path))
log.info(f"Variables: {list(ds.variables.keys())}")

lat_k = "latitude" if "latitude" in ds.variables else "lat"
lon_k = "longitude" if "longitude" in ds.variables else "lon"
lats  = ds.variables[lat_k][:]
lons  = ds.variables[lon_k][:]
tk    = "valid_time" if "valid_time" in ds.variables else "time"
times = netCDF4.num2date(
    ds.variables[tk][:], ds.variables[tk].units,
    only_use_cftime_datetimes=False,
    only_use_python_datetimes=True,
)

# Upsert grid points
for lat in lats:
    for lon in lons:
        con.execute("INSERT OR IGNORE INTO grid_points (lat,lon) VALUES (?,?)",
                    (round(float(lat),4), round(float(lon),4)))
con.commit()
grid_ids = {(round(r[1],4), round(r[2],4)): r[0]
            for r in con.execute("SELECT id,lat,lon FROM grid_points")}

# Find swvl1 variable
swvl_key = next((k for k in ds.variables
                 if "swvl" in k.lower() or "soil" in k.lower()), None)
if not swvl_key:
    log.error(f"No swvl variable found. Keys: {list(ds.variables.keys())}")
    ds.close(); con.close(); exit(1)

log.info(f"Using variable: {swvl_key}")
data = ds.variables[swvl_key][:]

# Aggregate to daily means
from collections import defaultdict
daily = defaultdict(lambda: defaultdict(list))
for t_idx, ts in enumerate(times):
    day_str = ts.strftime("%Y-%m-%d")
    for li, lat in enumerate(lats):
        for lo, lon in enumerate(lons):
            val = float(data[t_idx, li, lo])
            if np.ma.is_masked(val) or np.isnan(val): continue
            gid = grid_ids.get((round(float(lat),4), round(float(lon),4)))
            if gid:
                daily[day_str][(gid, "volumetric_soil_water_layer_1")].append(val)

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
ds.close()
nc_path.unlink()

log.info(f"Inserted {len(rows):,} daily swvl1 rows for 2021")

# Verify
for r in con.execute("""
    SELECT variable, COUNT(*), MIN(timestamp), MAX(timestamp)
    FROM era5_observations WHERE variable LIKE '%soil%'
    GROUP BY variable
"""):
    log.info(f"  {r[0]}: n={r[1]:,}  {r[2][:10]} → {r[3][:10]}")

con.close()
log.info("✓ Done — run: python build_features_v2.py && python train_model.py")
