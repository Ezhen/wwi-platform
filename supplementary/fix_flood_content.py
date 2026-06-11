import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_SPW, DB_PIEZ, DB_ERA5, DB_CORINE, DB_FORECAST

"""
Replace v_flood_context with a fast version.
Pre-aggregates basin antecedent rain before joining — no correlated subquery.
"""
import sqlite3

con = sqlite3.connect(str(DB_SPW))

# Drop old slow view
con.execute("DROP VIEW IF EXISTS v_flood_context")
con.commit()

# Rewrite with CTE pre-aggregation — no correlated subquery
con.execute("""
CREATE VIEW v_flood_context AS
WITH basin_rain AS (
    SELECT s.basin,
           ROUND(AVG(ar.rain_7d_mm), 2) AS basin_rain_7d_mm
    FROM v_antecedent_rainfall ar
    JOIN stations s ON ar.station_no = s.station_no
    GROUP BY s.basin
)
SELECT
    h.station_no,
    h.station_name,
    h.river_name,
    h.basin,
    h.timestamp,
    h.level_m,
    r.delta_1h_m,
    r.delta_3h_m,
    r.tendency,
    b.basin_rain_7d_mm,
    CASE
        WHEN r.tendency = 'RISING_FAST' AND b.basin_rain_7d_mm > 20 THEN 'ELEVATED'
        WHEN r.tendency IN ('RISING_FAST','RISING')                  THEN 'WATCH'
        ELSE 'NORMAL'
    END AS risk_signal
FROM v_latest_H h
LEFT JOIN v_river_rise_rate r  USING(station_no)
LEFT JOIN basin_rain b         ON h.basin = b.basin
ORDER BY
    CASE WHEN r.tendency = 'RISING_FAST' AND b.basin_rain_7d_mm > 20 THEN 1
         WHEN r.tendency IN ('RISING_FAST','RISING') THEN 2
         ELSE 3 END,
    h.river_name
""")
con.commit()
print("✓ v_flood_context rebuilt")

# Quick test
import time
t0 = time.time()
rows = con.execute(
    "SELECT station_name, river_name, level_m, tendency, risk_signal "
    "FROM v_flood_context LIMIT 10"
).fetchall()
print(f"✓ Query returned {len(rows)} rows in {time.time()-t0:.2f}s")
for r in rows:
    print(f"  {r[1]:<20} {r[0]:<28} H={r[2]}m  {r[3]:<12} → {r[4]}")
con.close()
