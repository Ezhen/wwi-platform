"""
Fix precipitation:
1. Remove old Q-as-Precip observations (wrong ts_ids from original ingest)
2. Divide all Precip values by 10 (0.1mm → mm)
"""
import sqlite3, logging
from pathlib import Path

ROOT   = Path(__file__).parent
DB_SPW = str(ROOT / "export/databases/spw_liege.db")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

con = sqlite3.connect(DB_SPW, timeout=30)
con.execute("PRAGMA journal_mode=DELETE")
con.commit()

# Step 1: Show what Precip timeseries exist
log.info("Current Precip timeseries:")
for r in con.execute("""
    SELECT t.ts_id, t.station_no, t.ts_path, t.ts_unit,
           COUNT(o.id) AS n_obs
    FROM timeseries t
    LEFT JOIN observations o ON t.ts_id = o.ts_id
    WHERE t.parameter = 'Precip'
    GROUP BY t.ts_id
    ORDER BY n_obs DESC
"""):
    log.info(f"  {r[0]:<25}  {r[1]:8s}  {r[2]:<50}  n={r[4]}")

# Step 2: Remove Q-as-Precip (ts_path contains /Q/)
n = con.execute("""
    DELETE FROM observations
    WHERE parameter = 'Precip'
      AND ts_id NOT LIKE '%_Precip_5m'
""").rowcount
con.commit()
log.info(f"\nRemoved {n} old Q-as-Precip observations")

con.execute("""
    DELETE FROM timeseries
    WHERE parameter = 'Precip'
      AND ts_id NOT LIKE '%_Precip_5m'
""")
con.commit()

# Step 3: Divide values by 10 (0.1mm → mm)
log.info("Dividing Precip values by 10 (0.1mm → mm)...")
n = con.execute("""
    UPDATE observations
    SET value = ROUND(value / 10.0, 3)
    WHERE parameter = 'Precip'
      AND value IS NOT NULL
""").rowcount
con.commit()
log.info(f"  Updated {n} records")

# Step 4: Verify
log.info("\nVerification — top values after fix:")
for r in con.execute("""
    SELECT o.station_no, s.station_name, o.timestamp, o.value
    FROM observations o
    JOIN stations s ON o.station_no = s.station_no
    WHERE o.parameter = 'Precip' AND o.value > 0
    ORDER BY o.value DESC LIMIT 5
"""):
    log.info(f"  {r[0]:8s}  {r[1]:<25}  {r[2][:16]}  {r[3]:.3f} mm/5min")

log.info("\n7-day totals per station:")
for r in con.execute("""
    SELECT o.station_no, s.station_name,
           ROUND(SUM(o.value),1) AS total_mm,
           COUNT(*) AS n_records
    FROM observations o
    JOIN stations s ON o.station_no = s.station_no
    WHERE o.parameter = 'Precip'
    GROUP BY o.station_no
    ORDER BY total_mm DESC
"""):
    log.info(f"  {r[0]:8s}  {r[1]:<25}  7d_total={r[2]:>7.1f}mm  n={r[3]}")

con.close()
log.info("\n✓ Done — run: python processing/rebuild_all.py")
