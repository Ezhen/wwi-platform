"""
Diagnose SPW precipitation units.
"""
import sqlite3
from pathlib import Path

ROOT   = Path(__file__).parent
DB_SPW = str(ROOT / "export/databases/spw_liege.db")

con = sqlite3.connect(DB_SPW)

# 1. Check what timeseries units are stored for Precip
print("=== Precip timeseries units ===")
for r in con.execute("""
    SELECT t.station_no, s.station_name, t.ts_path, t.ts_unit
    FROM timeseries t
    JOIN stations s ON t.station_no = s.station_no
    WHERE t.parameter = 'Precip'
    LIMIT 10
"""):
    print(f"  {r[0]:8s}  {r[1]:<28}  {r[2]:<50}  unit={r[3]}")

# 2. Look at raw values for one station
print("\n=== Raw Precip values — last 24h (station 5284 GEMMENICH) ===")
for r in con.execute("""
    SELECT timestamp, value, quality_code
    FROM observations
    WHERE station_no = '5284' AND parameter = 'Precip'
    ORDER BY timestamp DESC
    LIMIT 24
"""):
    print(f"  {r[0]}  value={r[1]}  qc={r[2]}")

# 3. Stats across all precip stations
print("\n=== Precip value statistics per station ===")
for r in con.execute("""
    SELECT o.station_no, s.station_name,
           COUNT(*)          AS n,
           MIN(o.value)      AS min_val,
           MAX(o.value)      AS max_val,
           AVG(o.value)      AS avg_val,
           SUM(o.value)      AS sum_val
    FROM observations o
    JOIN stations s ON o.station_no = s.station_no
    WHERE o.parameter = 'Precip'
    GROUP BY o.station_no
    ORDER BY sum_val DESC
    LIMIT 10
"""):
    print(f"  {r[0]:8s}  {r[1]:<25}  n={r[2]:>5}  "
          f"min={r[3]:.3f}  max={r[4]:.3f}  "
          f"avg={r[5]:.4f}  sum={r[6]:.2f}")

con.close()
