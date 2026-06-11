import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_SPW, DB_PIEZ, DB_ERA5, DB_CORINE, DB_FORECAST

"""
Fast derived indicators — materialized tables instead of chained views.
Computes everything in one pass, stores results, queries are instant.
"""
import sqlite3
import time
from pathlib import Path

def run(db, sql, label):
    t0 = time.time()
    db.executescript(sql)
    db.commit()
    print(f"  ✓ {label} ({time.time()-t0:.2f}s)")

# ── SPW ───────────────────────────────────────────────────────────────────────
print("=" * 55)
print("Building fast indicators — spw_liege.db")
print("=" * 55)

con = sqlite3.connect(str(DB_SPW))

# Drop all old views
for v in ["v_flood_context","v_antecedent_rainfall","v_river_rise_rate",
          "v_latest_H","v_latest_Q"]:
    con.execute(f"DROP VIEW IF EXISTS {v}")
# Drop old tables if exist
for t in ["t_latest_H","t_latest_Q","t_antecedent_rain",
          "t_rise_rate","t_flood_context"]:
    con.execute(f"DROP TABLE IF EXISTS {t}")
con.commit()

# 1. Latest H per station
run(con, """
CREATE TABLE t_latest_H AS
SELECT o.station_no, o.value AS level_m, o.timestamp, o.quality_code,
       s.station_name, s.river_name, s.basin, s.lat, s.lon, t.ts_unit
FROM observations o
JOIN stations s  ON o.station_no = s.station_no
JOIN timeseries t ON o.ts_id = t.ts_id
WHERE o.parameter = 'H'
  AND o.rowid IN (
      SELECT rowid FROM observations o2
      WHERE o2.station_no = o.station_no AND o2.parameter = 'H'
      ORDER BY o2.timestamp DESC LIMIT 1
  );
CREATE INDEX idx_tlH_sno ON t_latest_H(station_no);
""", "t_latest_H")

# 2. Latest Q per station
run(con, """
CREATE TABLE t_latest_Q AS
SELECT o.station_no, o.value AS discharge_m3s, o.timestamp,
       s.station_name, s.river_name, s.lat, s.lon
FROM observations o
JOIN stations s ON o.station_no = s.station_no
WHERE o.parameter = 'Q'
  AND o.rowid IN (
      SELECT rowid FROM observations o2
      WHERE o2.station_no = o.station_no AND o2.parameter = 'Q'
      ORDER BY o2.timestamp DESC LIMIT 1
  );
CREATE INDEX idx_tlQ_sno ON t_latest_Q(station_no);
""", "t_latest_Q")

# 3. Antecedent rainfall — single pass with conditional aggregation
run(con, """
CREATE TABLE t_antecedent_rain AS
SELECT
    o.station_no,
    s.station_name, s.river_name, s.basin, s.lat, s.lon,
    ROUND(SUM(CASE WHEN o.timestamp >= datetime('now','-3 days')  THEN o.value ELSE 0 END), 2) AS rain_3d_mm,
    ROUND(SUM(CASE WHEN o.timestamp >= datetime('now','-7 days')  THEN o.value ELSE 0 END), 2) AS rain_7d_mm,
    ROUND(SUM(CASE WHEN o.timestamp >= datetime('now','-14 days') THEN o.value ELSE 0 END), 2) AS rain_14d_mm,
    COUNT(*) AS n_records
FROM observations o
JOIN stations s ON o.station_no = s.station_no
WHERE o.parameter = 'Precip'
GROUP BY o.station_no;
CREATE INDEX idx_tar_basin ON t_antecedent_rain(basin);
""", "t_antecedent_rain")

# 4. Rise rate — fetch H-1h, H-3h, H-6h in one pass using window functions
run(con, """
CREATE TABLE t_rise_rate AS
WITH ranked AS (
    SELECT station_no, value, timestamp,
           ROW_NUMBER() OVER (PARTITION BY station_no ORDER BY timestamp DESC) AS rn
    FROM observations
    WHERE parameter = 'H'
),
h0 AS (SELECT station_no, value AS h0, timestamp AS t0 FROM ranked WHERE rn=1),
h1 AS (SELECT station_no, value AS h1 FROM ranked WHERE rn=13),
h3 AS (SELECT station_no, value AS h3 FROM ranked WHERE rn=37),
h6 AS (SELECT station_no, value AS h6 FROM ranked WHERE rn=73)
SELECT
    h0.station_no,
    h0.t0 AS timestamp,
    ROUND(h0.h0, 3)            AS level_m,
    ROUND(h0.h0 - h1.h1, 4)   AS delta_1h_m,
    ROUND(h0.h0 - h3.h3, 4)   AS delta_3h_m,
    ROUND(h0.h0 - h6.h6, 4)   AS delta_6h_m,
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

# 5. Flood context — simple join, no subqueries
run(con, """
CREATE TABLE t_flood_context AS
WITH basin_rain AS (
    SELECT basin, ROUND(AVG(rain_7d_mm),2) AS basin_rain_7d_mm
    FROM t_antecedent_rain GROUP BY basin
)
SELECT
    h.station_no, h.station_name, h.river_name, h.basin,
    h.timestamp, h.level_m, h.lat, h.lon,
    r.delta_1h_m, r.delta_3h_m, r.delta_6h_m, r.tendency,
    b.basin_rain_7d_mm,
    q.discharge_m3s,
    CASE
        WHEN r.tendency = 'RISING_FAST' AND b.basin_rain_7d_mm > 20 THEN 'ELEVATED'
        WHEN r.tendency IN ('RISING_FAST','RISING')                  THEN 'WATCH'
        ELSE 'NORMAL'
    END AS risk_signal
FROM t_latest_H h
LEFT JOIN t_rise_rate r    ON h.station_no = r.station_no
LEFT JOIN basin_rain b     ON h.basin = b.basin
LEFT JOIN t_latest_Q q     ON h.station_no = q.station_no;
CREATE INDEX idx_tfc_sno ON t_flood_context(station_no);
""", "t_flood_context")

# Verify
print("\nRow counts:")
for t in ["t_latest_H","t_latest_Q","t_antecedent_rain","t_rise_rate","t_flood_context"]:
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t:<25} {n:>4} rows")

# Sample flood context
print("\nFlood context sample:")
for r in con.execute("""
    SELECT station_name, river_name, level_m, tendency,
           basin_rain_7d_mm, discharge_m3s, risk_signal
    FROM t_flood_context
    ORDER BY CASE risk_signal
        WHEN 'ELEVATED' THEN 1
        WHEN 'WATCH'    THEN 2
        ELSE 3 END
    LIMIT 10
"""):
    print(f"  {r[1]:<20} {r[0]:<25} H={r[2]}m  "
          f"{str(r[3]):<12} rain={r[4]}mm  Q={r[5]}  → {r[6]}")

con.close()

# ── Piez ──────────────────────────────────────────────────────────────────────
print("\n" + "=" * 55)
print("Building fast indicators — piez_liege.db")
print("=" * 55)

if DB_PIEZ.exists():
    con = sqlite3.connect(str(DB_PIEZ))
    for v in ["v_latest_groundwater","v_groundwater_anomaly"]:
        con.execute(f"DROP VIEW IF EXISTS {v}")
    for t in ["t_latest_groundwater","t_groundwater_anomaly"]:
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.commit()

    run(con, """
CREATE TABLE t_latest_groundwater AS
SELECT o.station_no, o.value AS depth_m, o.timestamp, o.quality_code,
       s.station_name, s.aquifer, s.aquifer_code, s.commune,
       s.province, s.lat, s.lon, s.elevation
FROM observations o
JOIN stations s ON o.station_no = s.station_no
WHERE o.parameter = 'Prof_depth'
  AND o.rowid IN (
      SELECT rowid FROM observations o2
      WHERE o2.station_no = o.station_no AND o2.parameter = 'Prof_depth'
      ORDER BY o2.timestamp DESC LIMIT 1
  );
CREATE INDEX idx_tlgw_sno ON t_latest_groundwater(station_no);
    """, "t_latest_groundwater")

    run(con, """
CREATE TABLE t_groundwater_anomaly AS
WITH stats AS (
    SELECT station_no,
           AVG(value)  AS mean_depth,
           MIN(value)  AS min_depth,
           MAX(value)  AS max_depth,
           COUNT(*)    AS n_records
    FROM observations
    WHERE parameter = 'Prof_depth'
    GROUP BY station_no
)
SELECT
    l.station_no, l.station_name, l.aquifer, l.commune, l.province,
    l.lat, l.lon, l.timestamp,
    ROUND(l.depth_m, 3)                              AS current_depth_m,
    ROUND(st.mean_depth, 3)                          AS mean_depth_m,
    ROUND(l.depth_m - st.mean_depth, 3)              AS anomaly_m,
    ROUND(st.min_depth, 3)                           AS min_depth_m,
    ROUND(st.max_depth, 3)                           AS max_depth_m,
    st.n_records,
    CASE WHEN st.max_depth > st.min_depth
         THEN ROUND((l.depth_m - st.min_depth)/(st.max_depth - st.min_depth), 3)
         ELSE 0.5 END                                AS depth_percentile,
    CASE
        WHEN l.depth_m - st.mean_depth >  2.0 THEN 'VERY_LOW'
        WHEN l.depth_m - st.mean_depth >  0.5 THEN 'LOW'
        WHEN l.depth_m - st.mean_depth < -2.0 THEN 'VERY_HIGH'
        WHEN l.depth_m - st.mean_depth < -0.5 THEN 'HIGH'
        ELSE 'NORMAL'
    END                                              AS gw_state
FROM t_latest_groundwater l
JOIN stats st USING(station_no);
    """, "t_groundwater_anomaly")

    for t in ["t_latest_groundwater","t_groundwater_anomaly"]:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<30} {n} rows")
    con.close()

print("\n✓ All indicators built. Run: python build_map.py")
