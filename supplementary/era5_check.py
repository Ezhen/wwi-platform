import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_SPW, DB_PIEZ, DB_ERA5, DB_CORINE, DB_FORECAST

"""
ERA5 DB + NetCDF diagnostic
"""
import sqlite3
from pathlib import Path

DB_PATH = str(DB_ERA5)
NC_FILE = "era5_download.nc"

# ── 1. Check DB ───────────────────────────────────────────────────────────────
print("=" * 55)
print("ERA5 DB Check")
print("=" * 55)

if not Path(DB_PATH).exists():
    print(f"✗ {DB_PATH} not found")
else:
    size = Path(DB_PATH).stat().st_size / 1024**2
    print(f"✓ {DB_PATH}  ({size:.2f} MB)")
    con = sqlite3.connect(DB_PATH)

    n_grid = con.execute("SELECT COUNT(*) FROM grid_points").fetchone()[0]
    n_obs  = con.execute("SELECT COUNT(*) FROM era5_observations").fetchone()[0]
    print(f"  Grid points : {n_grid}")
    print(f"  Observations: {n_obs:,}")

    if n_obs > 0:
        print("\n  By variable:")
        for row in con.execute("""
            SELECT variable, COUNT(*), MIN(timestamp), MAX(timestamp),
                   AVG(value), MIN(value), MAX(value)
            FROM era5_observations GROUP BY variable
        """):
            print(f"    {row[0]:<28} n={row[1]:>6,}  "
                  f"{row[2][:13]} → {row[3][:13]}  "
                  f"mean={row[4]:.4f}  min={row[5]:.4f}  max={row[6]:.4f}")
    else:
        print("  ✗ No observations in DB")
    con.close()

# ── 2. Check NetCDF ───────────────────────────────────────────────────────────
print("\n" + "=" * 55)
print("NetCDF Check")
print("=" * 55)

if not Path(NC_FILE).exists():
    print(f"✗ {NC_FILE} not found")
else:
    size = Path(NC_FILE).stat().st_size / 1024**2
    print(f"✓ {NC_FILE}  ({size:.2f} MB)")
    try:
        import netCDF4 as nc
        ds = nc.Dataset(NC_FILE)
        print(f"  Variables : {list(ds.variables.keys())}")
        print(f"  Dimensions: {dict(ds.dimensions)}")
        for var in ["latitude","longitude","time"]:
            if var in ds.variables:
                arr = ds.variables[var][:]
                print(f"  {var:<12}: {len(arr)} values  "
                      f"[{float(arr.min()):.3f} → {float(arr.max()):.3f}]")
        if "time" in ds.variables:
            import netCDF4 as nc2
            times = nc2.num2date(
                ds.variables["time"][:],
                units=ds.variables["time"].units,
            )
            print(f"  Time range: {times[0]} → {times[-1]}")
        # Check for data variables
        for var in ["tp","t2m","swvl1"]:
            if var in ds.variables:
                data = ds.variables[var][:]
                import numpy as np
                masked = np.ma.is_masked(data)
                print(f"  {var:<10}: shape={data.shape}  "
                      f"min={float(data.min()):.4f}  max={float(data.max()):.4f}  "
                      f"has_mask={masked}")
        ds.close()
    except Exception as e:
        print(f"  ✗ Failed to open NetCDF: {e}")
        print("  File is likely corrupt — delete and re-download")
