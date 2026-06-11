import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_SPW, DB_PIEZ, DB_ERA5, DB_CORINE, DB_FORECAST

"""
Fast indicators — test on 5 Ourthe stations only.
Confirms logic before running on full DB.
"""
import sqlite3, time

con = sqlite3.connect(str(DB_SPW))

# Drop test tables
for t in ["t_latest_H","t_latest_Q","t_antecedent_rain","t_rise_rate","t_flood_context"]:
    con.execute(f"DROP TABLE IF EXISTS {t}")
con.commit()

# Get 5 Ourthe station IDs
stations = [r[0] for r in con.execute("""
    SELECT station_no FROM stations
    WHERE river_name LIKE '%Ourthe%'
    LIMIT 5
""")]
print(f"Test stations: {stations}")
placeholders = ",".join(f"'{s}'" for s in stations)

def run(sql, label):
    t0 = time.time()
    con.executescript(sql)
    con.commit()
    n = con.execute(f"SELECT COUNT(*) FROM {label}").fetchone()[0]
    print(f"  ✓ {label}: {n} rows in {time.time()-t0:.2f}s")

# 1. Latest H
run(f"""
CREATE TABLE t_latest_H AS
SELECT o.station_no, o.value AS level_m, o.timestamp,
       s.station_name, s.river_name, s.basin, s.lat, s.lon
FROM observations o
JOIN stations s ON o.station_no = s.station_no
WHERE o.parameter = 'H'
  AND o.station_no IN ({placeholders})
  AND o.rowid IN (
      SELECT rowid FROM observations o2
      WHERE o2.station_no = o.station_no AND o2.parameter = 'H'
      ORDER BY o2.timestamp DESC LIMIT 1
  );
CREATE INDEX idx_tlH_sno ON t_latest_H(station_no);
""", "t_latest_H")

# 2. Latest Q
run(f"""
CREATE TABLE t_latest_Q AS
SELECT o.station_no, o.value AS discharge_m3s, o.timestamp
FROM observations o
WHERE o.parameter = 'Q'
  AND o.station_no IN ({placeholders})
  AND o.rowid IN (
      SELECT rowid FROM observations o2
      WHERE o2.station_no = o.station_no AND o2.parameter = 'Q'
      ORDER BY o2.timestamp DESC LIMIT 1
  );
CREATE INDEX idx_tlQ_sno ON t_latest_Q(station_no);
""", "t_latest_Q")

# 3. Antecedent rain
run(f"""
CREATE TABLE t_antecedent_rain AS
SELECT o.station_no,
       s.station_name, s.river_name, s.basin, s.lat, s.lon,
       ROUND(SUM(CASE WHEN o.timestamp >= datetime('now','-3 days')  THEN o.value ELSE 0 END),2) AS rain_3d_mm,
       ROUND(SUM(CASE WHEN o.timestamp >= datetime('now','-7 days')  THEN o.value ELSE 0 END),2) AS rain_7d_mm,
       ROUND(SUM(CASE WHEN o.timestamp >= datetime('now','-14 days') THEN o.value ELSE 0 END),2) AS rain_14d_mm
FROM observations o
JOIN stations s ON o.station_no = s.station_no
WHERE o.parameter = 'Precip'
  AND o.station_no IN ({placeholders})
GROUP BY o.station_no;
CREATE INDEX idx_tar_basin ON t_antecedent_rain(basin);
""", "t_antecedent_rain")

# 4. Rise rate
run(f"""
CREATE TABLE t_rise_rate AS
WITH ranked AS (
    SELECT station_no, value, timestamp,
           ROW_NUMBER() OVER (PARTITION BY station_no ORDER BY timestamp DESC) AS rn
    FROM observations
    WHERE parameter = 'H' AND station_no IN ({placeholders})
),
h0 AS (SELECT station_no, value AS h0, timestamp AS t0 FROM ranked WHERE rn=1),
h1 AS (SELECT station_no, value AS h1 FROM ranked WHERE rn=13),
h3 AS (SELECT station_no, value AS h3 FROM ranked WHERE rn=37),
h6 AS (SELECT station_no, value AS h6 FROM ranked WHERE rn=73)
SELECT h0.station_no, h0.t0 AS timestamp,
       ROUND(h0.h0,3) AS level_m,
       ROUND(h0.h0 - h1.h1, 4) AS delta_1h_m,
       ROUND(h0.h0 - h3.h3, 4) AS delta_3h_m,
       ROUND(h0.h0 - h6.h6, 4) AS delta_6h_m,
       CASE
           WHEN h0.h0 - h1.h1 >  0.05 THEN 'RISING_FAST'
           WHEN h0.h0 - h1.h1 >  0.01 THEN 'RISING'
           WHEN h0.h0 - h1.h1 < -0.05 THEN 'FALLING_FAST'
           WHEN h0.h0 - h1.h1 < -0.01 THEN 'FALLING'
           ELSE 'STABLE'
       END AS tendency
FROM h0
LEFT JOIN h1 USING(station_no)
LEFT JOIN h3 USING(station_no)
LEFT JOIN h6 USING(station_no);
CREATE INDEX idx_trr_sno ON t_rise_rate(station_no);
""", "t_rise_rate")

# 5. Flood context
run(f"""
CREATE TABLE t_flood_context AS
WITH basin_rain AS (
    SELECT basin, ROUND(AVG(rain_7d_mm),2) AS basin_rain_7d_mm
    FROM t_antecedent_rain GROUP BY basin
)
SELECT h.station_no, h.station_name, h.river_name, h.basin,
       h.timestamp, h.level_m, h.lat, h.lon,
       r.delta_1h_m, r.delta_3h_m, r.delta_6h_m, r.tendency,
       b.basin_rain_7d_mm, q.discharge_m3s,
       CASE
           WHEN r.tendency = 'RISING_FAST' AND b.basin_rain_7d_mm > 20 THEN 'ELEVATED'
           WHEN r.tendency IN ('RISING_FAST','RISING')                  THEN 'WATCH'
           ELSE 'NORMAL'
       END AS risk_signal
FROM t_latest_H h
LEFT JOIN t_rise_rate r ON h.station_no = r.station_no
LEFT JOIN basin_rain b  ON h.basin = b.basin
LEFT JOIN t_latest_Q q  ON h.station_no = q.station_no;
""", "t_flood_context")

print("\nResult:")
for r in con.execute("""
    SELECT station_name, river_name, level_m, tendency,
           basin_rain_7d_mm, discharge_m3s, risk_signal
    FROM t_flood_context
"""):
    print(f"  {r[1]:<22} {r[0]:<25} H={r[2]}m  {str(r[3]):<12} "
          f"rain={r[4]}mm  Q={r[5]}  → {r[6]}")

con.close()
print("\n✓ Test passed — run fast_indicators.py for full DB")
