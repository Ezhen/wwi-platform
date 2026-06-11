"""
WWI Database Shrink
ERA5: delete temperature, aggregate soil moisture to daily, remove duplicates
SPW:  add 30-day rolling window cleanup
"""
import sqlite3, time, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_ERA5 = str(ROOT / "export/databases/era5_liege.db")
DB_SPW  = str(ROOT / "export/databases/spw_liege.db")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

def db_size(path):
    return Path(path).stat().st_size / 1024 / 1024

# ── ERA5 ──────────────────────────────────────────────────────────────────────
log.info("=" * 55)
log.info(f"ERA5 before: {db_size(DB_ERA5):.0f} MB")
log.info("=" * 55)

con = sqlite3.connect(DB_ERA5, timeout=30)
con.execute("PRAGMA journal_mode=DELETE")

# Step 1: Delete temperature entirely
log.info("Deleting 2m_temperature...")
t0 = time.time()
n = con.execute(
    "DELETE FROM era5_observations WHERE variable='2m_temperature'"
).rowcount
con.commit()
log.info(f"  Deleted {n:,} rows in {time.time()-t0:.1f}s")

# Step 2: Delete duplicate soil moisture variable names
log.info("Deleting duplicate soil_water_layer_1...")
n = con.execute(
    "DELETE FROM era5_observations WHERE variable='soil_water_layer_1'"
).rowcount
con.commit()
log.info(f"  Deleted {n:,} rows")

# Step 3: Delete mean_precipitation_rate (we have total_precipitation)
log.info("Deleting mean_precipitation_rate...")
n = con.execute(
    "DELETE FROM era5_observations WHERE variable='mean_precipitation_rate'"
).rowcount
con.commit()
log.info(f"  Deleted {n:,} rows")

# Step 4: Aggregate swvl1 from hourly to daily mean
log.info("Aggregating swvl1 to daily means...")
t0 = time.time()

# Create daily aggregate table
con.execute("DROP TABLE IF EXISTS era5_daily_swvl1")
con.execute("""
    CREATE TABLE era5_daily_swvl1 AS
    SELECT grid_id,
           DATE(timestamp) AS date,
           ROUND(AVG(value), 6) AS swvl1_mean,
           COUNT(*) AS n_hours
    FROM era5_observations
    WHERE variable = 'volumetric_soil_water_layer_1'
    GROUP BY grid_id, DATE(timestamp)
""")
n_daily = con.execute("SELECT COUNT(*) FROM era5_daily_swvl1").fetchone()[0]
con.commit()
log.info(f"  Created {n_daily:,} daily rows in {time.time()-t0:.1f}s")

# Delete hourly swvl1
log.info("Deleting hourly swvl1...")
n = con.execute(
    "DELETE FROM era5_observations WHERE variable='volumetric_soil_water_layer_1'"
).rowcount
con.commit()
log.info(f"  Deleted {n:,} hourly rows")

# Step 5: VACUUM to reclaim space
log.info("Running VACUUM (reclaims deleted space)...")
t0 = time.time()
con.execute("VACUUM")
log.info(f"  Done in {time.time()-t0:.1f}s")

# Final state
log.info("\nERA5 final contents:")
for r in con.execute("""
    SELECT variable, COUNT(*) AS n,
           MIN(timestamp) AS t_min, MAX(timestamp) AS t_max
    FROM era5_observations GROUP BY variable
"""):
    log.info(f"  {r[0]:<45} n={r[1]:>7,}  {r[2][:10]} → {r[3][:10]}")

n_swvl = con.execute("SELECT COUNT(*) FROM era5_daily_swvl1").fetchone()[0]
log.info(f"  era5_daily_swvl1 (daily means)        n={n_swvl:>7,}")
con.close()
log.info(f"ERA5 after:  {db_size(DB_ERA5):.0f} MB")

# ── SPW rolling window ────────────────────────────────────────────────────────
log.info("\n" + "=" * 55)
log.info(f"SPW before: {db_size(DB_SPW):.0f} MB")
log.info("=" * 55)

con = sqlite3.connect(DB_SPW, timeout=30)
con.execute("PRAGMA journal_mode=DELETE")

# Keep 30 days of H/Q/Precip, delete older
log.info("Trimming observations older than 30 days...")
for param in ["H", "Q", "Precip"]:
    n = con.execute("""
        DELETE FROM observations
        WHERE parameter = ?
          AND timestamp < datetime('now', '-30 days')
    """, (param,)).rowcount
    log.info(f"  {param}: deleted {n:,} rows")

con.commit()

log.info("Running VACUUM...")
t0 = time.time()
con.execute("VACUUM")
log.info(f"  Done in {time.time()-t0:.1f}s")

# Final
log.info("\nSPW final contents:")
for r in con.execute("""
    SELECT parameter, COUNT(*) AS n,
           MIN(timestamp), MAX(timestamp)
    FROM observations GROUP BY parameter ORDER BY n DESC
"""):
    log.info(f"  {r[0]:<10} n={r[1]:>8,}  {r[2][:10]} → {r[3][:10]}")

con.close()
log.info(f"SPW after:  {db_size(DB_SPW):.0f} MB")

log.info("\n✓ Shrink complete")
log.info("Also update build_features_v2.py to read era5_daily_swvl1 instead of era5_observations")
