import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_SPW, DB_PIEZ, DB_ERA5, DB_CORINE, DB_FORECAST

"""
Derived indicators — SQL views across all platform databases.

Views created:
  spw_liege.db:
    v_antecedent_rainfall   — 3/7/14 day precip accumulation per station
    v_river_rise_rate       — ΔH/Δt hourly per station
    v_latest_H              — most recent water level per station
    v_latest_Q              — most recent discharge per station
    v_flood_context         — H + rise rate + antecedent rain joined

  piez_liege.db:
    v_groundwater_anomaly   — current depth vs mean depth
    v_latest_groundwater    — most recent depth per station

  forecast_liege.db:
    v_forecast_accumulation — 24h / 72h precip totals per point
    v_forecast_alert        — points where 24h precip > threshold
"""

import sqlite3
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── SPW views ─────────────────────────────────────────────────────────────────

SPW_VIEWS = """
-- ── Latest water level per station ──────────────────────────────────────────
DROP VIEW IF EXISTS v_latest_H;
CREATE VIEW v_latest_H AS
SELECT
    s.station_no,
    s.station_name,
    s.river_name,
    s.basin,
    o.timestamp,
    o.value          AS level_m,
    o.quality_code,
    t.ts_unit
FROM observations o
JOIN timeseries t   USING(ts_id)
JOIN stations s     USING(station_no)
WHERE o.parameter = 'H'
  AND o.timestamp = (
      SELECT MAX(o2.timestamp)
      FROM observations o2
      WHERE o2.station_no = o.station_no
        AND o2.parameter  = 'H'
  )
ORDER BY s.river_name, s.station_name;

-- ── Latest discharge per station ─────────────────────────────────────────────
DROP VIEW IF EXISTS v_latest_Q;
CREATE VIEW v_latest_Q AS
SELECT
    s.station_no,
    s.station_name,
    s.river_name,
    s.basin,
    o.timestamp,
    o.value          AS discharge_m3s,
    o.quality_code
FROM observations o
JOIN stations s USING(station_no)
WHERE o.parameter = 'Q'
  AND o.timestamp = (
      SELECT MAX(o2.timestamp)
      FROM observations o2
      WHERE o2.station_no = o.station_no
        AND o2.parameter  = 'Q'
  )
ORDER BY s.river_name, s.station_name;

-- ── River rise rate ΔH/Δt (m/h) ─────────────────────────────────────────────
-- Compares current H to H 1 hour ago and H 3 hours ago
DROP VIEW IF EXISTS v_river_rise_rate;
CREATE VIEW v_river_rise_rate AS
WITH latest AS (
    SELECT station_no, MAX(timestamp) AS t_latest
    FROM observations
    WHERE parameter = 'H'
    GROUP BY station_no
),
h_now AS (
    SELECT o.station_no, o.value AS h_now, o.timestamp AS t_now
    FROM observations o
    JOIN latest l ON o.station_no = l.station_no
                 AND o.timestamp  = l.t_latest
    WHERE o.parameter = 'H'
),
h_1h AS (
    SELECT o.station_no, o.value AS h_1h
    FROM observations o
    JOIN latest l ON o.station_no = l.station_no
    WHERE o.parameter = 'H'
      AND o.timestamp = datetime(l.t_latest, '-1 hour')
),
h_3h AS (
    SELECT o.station_no, o.value AS h_3h
    FROM observations o
    JOIN latest l ON o.station_no = l.station_no
    WHERE o.parameter = 'H'
      AND o.timestamp = datetime(l.t_latest, '-3 hours')
),
h_6h AS (
    SELECT o.station_no, o.value AS h_6h
    FROM observations o
    JOIN latest l ON o.station_no = l.station_no
    WHERE o.parameter = 'H'
      AND o.timestamp = datetime(l.t_latest, '-6 hours')
)
SELECT
    s.station_no,
    s.station_name,
    s.river_name,
    s.basin,
    n.t_now                                          AS timestamp,
    ROUND(n.h_now, 3)                                AS level_m,
    ROUND(n.h_now - r1.h_1h, 4)                     AS delta_1h_m,
    ROUND(n.h_now - r3.h_3h, 4)                     AS delta_3h_m,
    ROUND(n.h_now - r6.h_6h, 4)                     AS delta_6h_m,
    CASE
        WHEN n.h_now - r1.h_1h >  0.05 THEN 'RISING_FAST'
        WHEN n.h_now - r1.h_1h >  0.01 THEN 'RISING'
        WHEN n.h_now - r1.h_1h < -0.05 THEN 'FALLING_FAST'
        WHEN n.h_now - r1.h_1h < -0.01 THEN 'FALLING'
        ELSE 'STABLE'
    END                                              AS tendency
FROM h_now n
JOIN stations s   ON n.station_no = s.station_no
LEFT JOIN h_1h r1 ON n.station_no = r1.station_no
LEFT JOIN h_3h r3 ON n.station_no = r3.station_no
LEFT JOIN h_6h r6 ON n.station_no = r6.station_no
ORDER BY s.river_name, s.station_name;

-- ── Antecedent rainfall per station ──────────────────────────────────────────
-- 3/7/14 day accumulated precipitation
DROP VIEW IF EXISTS v_antecedent_rainfall;
CREATE VIEW v_antecedent_rainfall AS
WITH latest AS (
    SELECT MAX(timestamp) AS t_latest FROM observations WHERE parameter = 'Precip'
)
SELECT
    s.station_no,
    s.station_name,
    s.river_name,
    s.basin,
    ROUND(SUM(CASE
        WHEN o.timestamp >= datetime(l.t_latest, '-3 days')
        THEN o.value ELSE 0 END), 2)                AS rain_3d_mm,
    ROUND(SUM(CASE
        WHEN o.timestamp >= datetime(l.t_latest, '-7 days')
        THEN o.value ELSE 0 END), 2)                AS rain_7d_mm,
    ROUND(SUM(CASE
        WHEN o.timestamp >= datetime(l.t_latest, '-14 days')
        THEN o.value ELSE 0 END), 2)                AS rain_14d_mm,
    COUNT(o.id)                                     AS n_records,
    l.t_latest
FROM observations o
JOIN stations s ON o.station_no = s.station_no
CROSS JOIN latest l
WHERE o.parameter = 'Precip'
GROUP BY s.station_no
ORDER BY rain_7d_mm DESC;

-- ── Flood context — H + rise rate + nearest rain ──────────────────────────────
-- Joins latest H with rise rate and basin-level antecedent rain
DROP VIEW IF EXISTS v_flood_context;
CREATE VIEW v_flood_context AS
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
    -- Basin-mean antecedent rainfall
    (SELECT ROUND(AVG(ar.rain_7d_mm), 2)
     FROM v_antecedent_rainfall ar
     WHERE ar.basin = h.basin)                      AS basin_rain_7d_mm,
    -- Qualitative risk signal
    CASE
        WHEN r.tendency IN ('RISING_FAST')
         AND (SELECT AVG(ar.rain_7d_mm)
              FROM v_antecedent_rainfall ar
              WHERE ar.basin = h.basin) > 20
        THEN 'ELEVATED'
        WHEN r.tendency IN ('RISING_FAST', 'RISING')
        THEN 'WATCH'
        ELSE 'NORMAL'
    END                                             AS risk_signal
FROM v_latest_H h
LEFT JOIN v_river_rise_rate r USING(station_no)
ORDER BY
    CASE risk_signal
        WHEN 'ELEVATED' THEN 1
        WHEN 'WATCH'    THEN 2
        ELSE 3
    END,
    h.river_name;
"""

# ── Piezometry views ──────────────────────────────────────────────────────────

PIEZ_VIEWS = """
-- ── Latest groundwater depth per station ─────────────────────────────────────
DROP VIEW IF EXISTS v_latest_groundwater;
CREATE VIEW v_latest_groundwater AS
SELECT
    s.station_no,
    s.station_name,
    s.aquifer,
    s.aquifer_code,
    s.commune,
    s.province,
    s.local_x,
    s.local_y,
    s.elevation,
    o.timestamp,
    o.value          AS depth_m,
    o.quality_code
FROM observations o
JOIN stations s USING(station_no)
WHERE o.parameter = 'Prof_depth'
  AND o.timestamp = (
      SELECT MAX(o2.timestamp)
      FROM observations o2
      WHERE o2.station_no = o.station_no
        AND o2.parameter  = 'Prof_depth'
  )
ORDER BY s.commune;

-- ── Groundwater anomaly — current vs window mean ──────────────────────────────
DROP VIEW IF EXISTS v_groundwater_anomaly;
CREATE VIEW v_groundwater_anomaly AS
WITH stats AS (
    SELECT
        station_no,
        AVG(value)    AS mean_depth,
        MIN(value)    AS min_depth,
        MAX(value)    AS max_depth,
        COUNT(*)      AS n_records
    FROM observations
    WHERE parameter = 'Prof_depth'
    GROUP BY station_no
),
latest AS (
    SELECT station_no, value AS current_depth, timestamp
    FROM observations o
    WHERE parameter = 'Prof_depth'
      AND timestamp = (
          SELECT MAX(o2.timestamp)
          FROM observations o2
          WHERE o2.station_no = o.station_no
            AND o2.parameter  = 'Prof_depth'
      )
)
SELECT
    s.station_no,
    s.station_name,
    s.aquifer,
    s.commune,
    s.province,
    l.timestamp,
    ROUND(l.current_depth, 3)                       AS current_depth_m,
    ROUND(st.mean_depth, 3)                         AS mean_depth_m,
    ROUND(l.current_depth - st.mean_depth, 3)       AS anomaly_m,
    ROUND(st.min_depth, 3)                          AS min_depth_m,
    ROUND(st.max_depth, 3)                          AS max_depth_m,
    st.n_records,
    -- Normalised anomaly (0=at min, 1=at max)
    CASE
        WHEN st.max_depth > st.min_depth
        THEN ROUND((l.current_depth - st.min_depth) /
                   (st.max_depth   - st.min_depth), 3)
        ELSE 0.5
    END                                             AS depth_percentile,
    CASE
        WHEN l.current_depth - st.mean_depth >  2.0 THEN 'VERY_LOW'
        WHEN l.current_depth - st.mean_depth >  0.5 THEN 'LOW'
        WHEN l.current_depth - st.mean_depth < -2.0 THEN 'VERY_HIGH'
        WHEN l.current_depth - st.mean_depth < -0.5 THEN 'HIGH'
        ELSE 'NORMAL'
    END                                             AS gw_state
FROM latest l
JOIN stats   st USING(station_no)
JOIN stations s USING(station_no)
ORDER BY anomaly_m DESC;
"""

# ── Forecast views ────────────────────────────────────────────────────────────

FORECAST_VIEWS = """
-- ── Forecast accumulation — 24h and 72h totals ───────────────────────────────
DROP VIEW IF EXISTS v_forecast_accumulation;
CREATE VIEW v_forecast_accumulation AS
WITH latest_fetch AS (
    SELECT MAX(fetched_at) AS t_fetch FROM forecasts
)
SELECT
    p.point_id,
    p.description,
    p.lat,
    p.lon,
    ROUND(SUM(CASE
        WHEN f.valid_time <= datetime(l.t_fetch, '+24 hours')
        THEN f.value ELSE 0 END), 2)                AS precip_24h_mm,
    ROUND(SUM(CASE
        WHEN f.valid_time <= datetime(l.t_fetch, '+72 hours')
        THEN f.value ELSE 0 END), 2)                AS precip_72h_mm,
    ROUND(SUM(f.value), 2)                          AS precip_7d_mm,
    ROUND(MAX(CASE
        WHEN f.variable = 'temperature_2m' THEN f.value END), 1)
                                                    AS temp_max_c,
    ROUND(MIN(CASE
        WHEN f.variable = 'temperature_2m' THEN f.value END), 1)
                                                    AS temp_min_c,
    l.t_fetch                                       AS forecast_issued
FROM forecasts f
JOIN forecast_points p USING(point_id)
CROSS JOIN latest_fetch l
WHERE f.variable = 'precipitation'
  AND f.fetched_at = l.t_fetch
GROUP BY p.point_id
ORDER BY precip_24h_mm DESC;

-- ── Forecast alert — points exceeding thresholds ─────────────────────────────
DROP VIEW IF EXISTS v_forecast_alert;
CREATE VIEW v_forecast_alert AS
SELECT
    point_id,
    description,
    lat,
    lon,
    precip_24h_mm,
    precip_72h_mm,
    precip_7d_mm,
    CASE
        WHEN precip_24h_mm > 30 THEN 'HIGH'
        WHEN precip_24h_mm > 15 THEN 'MODERATE'
        WHEN precip_24h_mm >  5 THEN 'LOW'
        ELSE 'NONE'
    END                                             AS alert_24h,
    CASE
        WHEN precip_72h_mm > 60 THEN 'HIGH'
        WHEN precip_72h_mm > 30 THEN 'MODERATE'
        WHEN precip_72h_mm > 10 THEN 'LOW'
        ELSE 'NONE'
    END                                             AS alert_72h
FROM v_forecast_accumulation
ORDER BY precip_24h_mm DESC;
"""


# ── Apply views ───────────────────────────────────────────────────────────────

def apply_views(db_path, sql, label):
    if not Path(db_path).exists():
        log.warning(f"  {db_path} not found — skipping")
        return
    con = sqlite3.connect(db_path)
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    ok = 0
    for stmt in statements:
        if not stmt:
            continue
        try:
            con.execute(stmt)
            con.commit()
            if stmt.upper().startswith("CREATE VIEW"):
                name = stmt.split("\n")[1].strip().replace("CREATE VIEW ", "")
                log.info(f"  ✓ {name}")
            ok += 1
        except Exception as e:
            log.error(f"  ✗ {e}\n    SQL: {stmt[:80]}")
    con.close()
    log.info(f"  {label}: {ok} statements applied")


def verify_views(db_path, view_names):
    """Quick row count check on each view."""
    if not Path(db_path).exists():
        return
    con = sqlite3.connect(db_path)
    print(f"\n  {db_path}")
    for v in view_names:
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
            # Sample first row
            row = con.execute(f"SELECT * FROM {v} LIMIT 1").fetchone()
            cols = [d[0] for d in con.execute(
                f"SELECT * FROM {v} LIMIT 0").description]
            print(f"    {v:<35} {n:>5} rows  "
                  f"cols={len(cols)}")
        except Exception as e:
            print(f"    {v:<35} ERROR: {e}")
    con.close()


if __name__ == "__main__":
    log.info("=" * 55)
    log.info("Derived Indicators — SQL Views")
    log.info("=" * 55)

    log.info("\nSPW hydrology views:")
    apply_views(str(DB_SPW), SPW_VIEWS, "SPW")

    log.info("\nPiezometry views:")
    apply_views(str(DB_PIEZ), PIEZ_VIEWS, "PIEZ")

    log.info("\nForecast views:")
    apply_views(str(DB_FORECAST), FORECAST_VIEWS, "FORECAST")

    # ── Verification ─────────────────────────────────────────────────────────
    log.info("\n" + "=" * 55)
    log.info("View verification")
    log.info("=" * 55)

    verify_views(str(DB_SPW), [
        "v_latest_H", "v_latest_Q",
        "v_river_rise_rate", "v_antecedent_rainfall",
        "v_flood_context",
    ])
    verify_views(str(DB_PIEZ), [
        "v_latest_groundwater", "v_groundwater_anomaly",
    ])
    verify_views(str(DB_FORECAST), [
        "v_forecast_accumulation", "v_forecast_alert",
    ])

    # ── Sample outputs ────────────────────────────────────────────────────────
    log.info("\n" + "=" * 55)
    log.info("Sample: v_flood_context (risk signals)")
    log.info("=" * 55)
    if DB_SPW.exists():
        con = sqlite3.connect(str(DB_SPW))
        for row in con.execute("""
            SELECT station_name, river_name, level_m,
                   tendency, basin_rain_7d_mm, risk_signal
            FROM v_flood_context
            WHERE risk_signal != 'NORMAL'
            LIMIT 10
        """):
            print(f"  {row[1]:<20} {row[0]:<25} "
                  f"H={row[2]}m  {row[3]:<12} "
                  f"rain7d={row[4]}mm  → {row[5]}")
        con.close()

    log.info("\nSample: v_forecast_alert")
    if DB_FORECAST.exists():
        con = sqlite3.connect(str(DB_FORECAST))
        for row in con.execute(
            "SELECT description, precip_24h_mm, precip_72h_mm, "
            "alert_24h, alert_72h FROM v_forecast_alert"
        ):
            print(f"  {row[0]:<40} "
                  f"24h={row[1]:5.1f}mm  72h={row[2]:5.1f}mm  "
                  f"→ {row[3]}/{row[4]}")
        con.close()

    log.info("\nSample: v_groundwater_anomaly (LIEGE province)")
    if DB_PIEZ.exists():
        con = sqlite3.connect(str(DB_PIEZ))
        for row in con.execute("""
            SELECT station_name, commune, current_depth_m,
                   anomaly_m, gw_state
            FROM v_groundwater_anomaly
            WHERE province = 'LIEGE'
            LIMIT 10
        """):
            print(f"  {row[1]:<20} {row[0]:<30} "
                  f"depth={row[2]}m  anom={row[3]:+.2f}m  → {row[4]}")
        con.close()

    log.info("\n✓ All views created. Ready for Power BI.")
