"""
Add lat/lon to piez stations and rebuild views with coords.
Run from project root.
"""
import sqlite3, time
from pathlib import Path
from pyproj import Transformer

ROOT    = Path(__file__).parent
DB_PIEZ = str(ROOT / "export/databases/piez_liege.db")

print("Opening piez_liege.db...")
con = sqlite3.connect(DB_PIEZ, timeout=30)
con.execute("PRAGMA journal_mode=DELETE")
con.commit()

# Step 1: Add lat/lon columns if missing
cols = [r[1] for r in con.execute("PRAGMA table_info(stations)")]
print(f"Current columns: {cols}")

if "lat" not in cols:
    con.execute("ALTER TABLE stations ADD COLUMN lat REAL")
    con.execute("ALTER TABLE stations ADD COLUMN lon REAL")
    con.commit()
    print("  Added lat/lon columns")

# Step 2: Convert coordinates
tr = Transformer.from_crs("EPSG:31370", "EPSG:4326", always_xy=True)
rows = con.execute(
    "SELECT station_no, local_x, local_y FROM stations "
    "WHERE local_x IS NOT NULL AND CAST(local_x AS REAL) != 0"
).fetchall()
print(f"  Converting {len(rows)} stations...")

updated = 0
for sno, x, y in rows:
    try:
        lon, lat = tr.transform(float(x), float(y))
        if 49 < lat < 52 and 2 < lon < 7:  # sanity check — Belgium bounds
            con.execute("UPDATE stations SET lat=?, lon=? WHERE station_no=?",
                        (round(lat,6), round(lon,6), sno))
            updated += 1
    except: pass
con.commit()

n_coords = con.execute(
    "SELECT COUNT(*) FROM stations WHERE lat IS NOT NULL"
).fetchone()[0]
print(f"  Updated: {updated}  total with coords: {n_coords}")

# Step 3: Rebuild views with lat/lon
con.execute("DROP VIEW IF EXISTS v_latest_groundwater")
con.execute("DROP VIEW IF EXISTS v_groundwater_anomaly")
con.commit()

con.execute("""
CREATE VIEW v_latest_groundwater AS
SELECT o.station_no, s.station_name, s.aquifer, s.commune, s.province,
       s.lat, s.lon, s.elevation,
       o.timestamp, o.value AS depth_m, o.quality_code
FROM observations o
JOIN stations s ON o.station_no = s.station_no
WHERE o.parameter = 'Prof_depth'
  AND o.timestamp = (
      SELECT MAX(o2.timestamp) FROM observations o2
      WHERE o2.station_no = o.station_no AND o2.parameter = 'Prof_depth'
  )
""")

con.execute("""
CREATE VIEW v_groundwater_anomaly AS
WITH stats AS (
    SELECT station_no,
           AVG(value) AS mean_depth, MIN(value) AS min_depth,
           MAX(value) AS max_depth,  COUNT(*)   AS n_records
    FROM observations WHERE parameter='Prof_depth' GROUP BY station_no
),
latest AS (
    SELECT station_no, value AS current_depth, timestamp
    FROM observations o
    WHERE parameter='Prof_depth'
      AND timestamp = (
          SELECT MAX(o2.timestamp) FROM observations o2
          WHERE o2.station_no=o.station_no AND o2.parameter='Prof_depth'
      )
)
SELECT s.station_no, s.station_name, s.aquifer, s.commune, s.province,
       s.lat, s.lon,
       l.timestamp,
       ROUND(l.current_depth,3)                    AS current_depth_m,
       ROUND(st.mean_depth,3)                       AS mean_depth_m,
       ROUND(l.current_depth - st.mean_depth,3)     AS anomaly_m,
       ROUND(st.min_depth,3)                        AS min_depth_m,
       ROUND(st.max_depth,3)                        AS max_depth_m,
       st.n_records,
       CASE WHEN st.max_depth > st.min_depth
            THEN ROUND((l.current_depth-st.min_depth)/(st.max_depth-st.min_depth),3)
            ELSE 0.5 END                            AS depth_percentile,
       CASE
           WHEN l.current_depth - st.mean_depth >  2.0 THEN 'VERY_LOW'
           WHEN l.current_depth - st.mean_depth >  0.5 THEN 'LOW'
           WHEN l.current_depth - st.mean_depth < -2.0 THEN 'VERY_HIGH'
           WHEN l.current_depth - st.mean_depth < -0.5 THEN 'HIGH'
           ELSE 'NORMAL'
       END                                          AS gw_state
FROM latest l
JOIN stats   st USING(station_no)
JOIN stations s USING(station_no)
""")
con.commit()

# Verify
n = con.execute(
    "SELECT COUNT(*) FROM v_groundwater_anomaly WHERE lat IS NOT NULL"
).fetchone()[0]
n_liege = con.execute(
    "SELECT COUNT(*) FROM v_groundwater_anomaly "
    "WHERE lat IS NOT NULL AND province='LIEGE'"
).fetchone()[0]
print(f"\nv_groundwater_anomaly: {n} rows with coords, {n_liege} in LIEGE")

sample = con.execute(
    "SELECT station_name, commune, current_depth_m, gw_state, lat, lon "
    "FROM v_groundwater_anomaly WHERE province='LIEGE' AND lat IS NOT NULL LIMIT 3"
).fetchall()
for r in sample:
    print(f"  {r}")

con.close()
print("\n✓ Done — run: python visualisation/build_map.py")
