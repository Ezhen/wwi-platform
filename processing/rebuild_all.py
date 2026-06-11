"""
rebuild_all.py — WWI materialized indicators
Waits for DB lock to clear, then runs everything in one connection.
"""

#from config import DB_SPW

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
DB_SPW = str(ROOT / "export/databases/spw_liege.db")

import sqlite3, time, logging, os, sys
from pathlib import Path
from pyproj import Transformer
sys.path.insert(0, str(Path(__file__).parent.parent))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)
T0 = time.time()

def elapsed():
    return f"{time.time()-T0:.1f}s"

DB = str(DB_SPW)

# ── Step 0: Kill WAL files and wait for lock ──────────────────────────────────
log.info("Clearing WAL files...")
for ext in ["-wal", "-shm", "-journal"]:
    p = Path(DB + ext)
    if p.exists():
        p.unlink()
        log.info(f"  Removed {p}")

log.info("Waiting for DB lock to clear...")
con = None
for attempt in range(30):  # wait up to 5 minutes
    try:
        con = sqlite3.connect(DB, timeout=5)
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA cache_size=-64000")
        con.execute("SELECT COUNT(*) FROM observations").fetchone()
        con.commit()
        log.info(f"  DB open after {attempt * 10}s")
        break
    except sqlite3.OperationalError as e:
        log.warning(f"  Attempt {attempt+1}/30: {e} — retrying in 10s...")
        if con:
            try: con.close()
            except: pass
        time.sleep(10)
else:
    log.error("Could not acquire DB lock after 5 minutes. Check: lsof spw_liege.db")
    exit(1)

# ── Step 1: Indexes ───────────────────────────────────────────────────────────
log.info(f"Step 1/6 — Indexes  [{elapsed()}]")
existing = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='index'"
)]
for name, sql in {
    "idx_obs_sno_param_ts":
        "CREATE INDEX IF NOT EXISTS idx_obs_sno_param_ts "
        "ON observations(station_no, parameter, timestamp DESC)",
    "idx_obs_param_ts":
        "CREATE INDEX IF NOT EXISTS idx_obs_param_ts "
        "ON observations(parameter, timestamp DESC)",
}.items():
    if name not in existing:
        log.info(f"  Creating {name}...")
        t0 = time.time()
        con.execute(sql)
        con.commit()
        log.info(f"  Done in {time.time()-t0:.1f}s")
    else:
        log.info(f"  {name} OK")

# ── Step 2: Coords ────────────────────────────────────────────────────────────
log.info(f"Step 2/6 — Coordinates  [{elapsed()}]")
cols = [r[1] for r in con.execute("PRAGMA table_info(stations)")]
if "lat" not in cols:
    con.execute("ALTER TABLE stations ADD COLUMN lat REAL")
    con.execute("ALTER TABLE stations ADD COLUMN lon REAL")
    con.commit()
    tr = Transformer.from_crs("EPSG:31370", "EPSG:4326", always_xy=True)
    rows = con.execute(
        "SELECT station_no, local_x, local_y FROM stations "
        "WHERE local_x IS NOT NULL AND local_x != 0"
    ).fetchall()
    for sno, x, y in rows:
        try:
            lon, lat = tr.transform(float(x), float(y))
            con.execute("UPDATE stations SET lat=?, lon=? WHERE station_no=?",
                        (round(lat,6), round(lon,6), sno))
        except: pass
    con.commit()
    log.info(f"  Converted {len(rows)} stations")
else:
    n = con.execute(
        "SELECT COUNT(*) FROM stations WHERE lat IS NOT NULL"
    ).fetchone()[0]
    log.info(f"  {n} stations already have coords")

# ── Step 3: Drop old ──────────────────────────────────────────────────────────
log.info(f"Step 3/6 — Drop old tables/views  [{elapsed()}]")
for name in ["t_latest_H","t_latest_Q","t_antecedent_rain",
             "t_rise_rate","t_flood_context"]:
    con.execute(f"DROP TABLE IF EXISTS {name}")
for name in ["v_flood_context","v_antecedent_rainfall","v_river_rise_rate",
             "v_latest_H","v_latest_Q"]:
    con.execute(f"DROP VIEW IF EXISTS {name}")
con.commit()
log.info("  Done")

# ── Step 4: Latest H / Q ──────────────────────────────────────────────────────
log.info(f"Step 4/6 — Latest H and Q  [{elapsed()}]")

con.execute("""
CREATE TABLE t_latest_H AS
SELECT o.station_no, o.value AS level_m, o.timestamp,
       s.station_name, s.river_name, s.basin, s.lat, s.lon
FROM observations o JOIN stations s ON o.station_no = s.station_no
WHERE o.parameter = 'H'
  AND o.timestamp = (
      SELECT MAX(o2.timestamp) FROM observations o2
      WHERE o2.station_no = o.station_no AND o2.parameter = 'H'
  )
""")
con.execute("CREATE INDEX idx_tlH ON t_latest_H(station_no)")
n = con.execute("SELECT COUNT(*) FROM t_latest_H").fetchone()[0]
log.info(f"  t_latest_H: {n} rows")

con.execute("""
CREATE TABLE t_latest_Q AS
SELECT o.station_no, o.value AS discharge_m3s, o.timestamp
FROM observations o
WHERE o.parameter = 'Q'
  AND o.value IS NOT NULL
  AND o.timestamp = (
      SELECT MAX(o2.timestamp) FROM observations o2
      WHERE o2.station_no = o.station_no
        AND o2.parameter = 'Q'
        AND o2.value IS NOT NULL
  )
""")
con.execute("CREATE INDEX idx_tlQ ON t_latest_Q(station_no)")
n = con.execute("SELECT COUNT(*) FROM t_latest_Q").fetchone()[0]
log.info(f"  t_latest_Q: {n} rows  [{elapsed()}]")
con.commit()

# ── Step 5: Antecedent rain + rise rate ───────────────────────────────────────
log.info(f"Step 5/6 — Rain + rise rate  [{elapsed()}]")

con.execute("""
CREATE TABLE t_antecedent_rain AS
SELECT o.station_no,
       s.station_name, s.river_name, s.basin, s.lat, s.lon,
       ROUND(SUM(CASE WHEN o.timestamp >= datetime('now','-3 days')
                 THEN o.value ELSE 0 END),2) AS rain_3d_mm,
       ROUND(SUM(CASE WHEN o.timestamp >= datetime('now','-7 days')
                 THEN o.value ELSE 0 END),2) AS rain_7d_mm,
       ROUND(SUM(CASE WHEN o.timestamp >= datetime('now','-14 days')
                 THEN o.value ELSE 0 END),2) AS rain_14d_mm
FROM observations o JOIN stations s ON o.station_no = s.station_no
WHERE o.parameter = 'Precip'
GROUP BY o.station_no
""")
con.execute("CREATE INDEX idx_tar ON t_antecedent_rain(basin)")
n = con.execute("SELECT COUNT(*) FROM t_antecedent_rain").fetchone()[0]
log.info(f"  t_antecedent_rain: {n} rows  [{elapsed()}]")
con.commit()

# Rise rate — per-station indexed fetch
stations = [r[0] for r in con.execute("SELECT station_no FROM t_latest_H")]
log.info(f"  Computing rise rate for {len(stations)} stations...")
rows = []
for sno in stations:
    recs = con.execute("""
        SELECT value FROM observations
        WHERE station_no=? AND parameter='H'
        ORDER BY timestamp DESC LIMIT 73
    """, (sno,)).fetchall()
    if not recs: continue
    h0 = recs[0][0]
    if h0 is None: continue
    h1 = recs[12][0] if len(recs) > 12 else None
    h3 = recs[36][0] if len(recs) > 36 else None
    h6 = recs[72][0] if len(recs) > 72 else None
    d1 = round(h0 - h1, 4) if h1 is not None else None
    d3 = round(h0 - h3, 4) if h3 is not None else None
    d6 = round(h0 - h6, 4) if h6 is not None else None
    if d1 is None:   t = "STABLE"
    elif d1 >  0.05: t = "RISING_FAST"
    elif d1 >  0.01: t = "RISING"
    elif d1 < -0.05: t = "FALLING_FAST"
    elif d1 < -0.01: t = "FALLING"
    else:            t = "STABLE"
    rows.append((sno, round(h0,3), d1, d3, d6, t))

con.execute("""
CREATE TABLE t_rise_rate (
    station_no TEXT PRIMARY KEY,
    level_m    REAL, delta_1h_m REAL,
    delta_3h_m REAL, delta_6h_m REAL, tendency TEXT
)""")
con.executemany("INSERT INTO t_rise_rate VALUES (?,?,?,?,?,?)", rows)
log.info(f"  t_rise_rate: {len(rows)} rows  [{elapsed()}]")
con.commit()

# ── Step 6: Flood context ─────────────────────────────────────────────────────
log.info(f"Step 6/6 — Flood context  [{elapsed()}]")
con.execute("""
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
           WHEN r.tendency='RISING_FAST' AND b.basin_rain_7d_mm > 20 THEN 'ELEVATED'
           WHEN r.tendency IN ('RISING_FAST','RISING')               THEN 'WATCH'
           ELSE 'NORMAL'
       END AS risk_signal
FROM t_latest_H h
LEFT JOIN t_rise_rate r ON h.station_no = r.station_no
LEFT JOIN basin_rain b  ON h.basin = b.basin
LEFT JOIN t_latest_Q q  ON h.station_no = q.station_no
""")
n = con.execute("SELECT COUNT(*) FROM t_flood_context").fetchone()[0]
log.info(f"  t_flood_context: {n} rows  [{elapsed()}]")
con.commit()

# ── Sample ────────────────────────────────────────────────────────────────────
log.info(f"\nSample — top 10 stations:")
for r in con.execute("""
    SELECT station_name, river_name, level_m, tendency,
           basin_rain_7d_mm, discharge_m3s, risk_signal
    FROM t_flood_context
    ORDER BY CASE risk_signal
        WHEN 'ELEVATED' THEN 1 WHEN 'WATCH' THEN 2 ELSE 3 END, river_name
    LIMIT 10
"""):
    log.info(f"  {r[1]:<20} {r[0]:<25} H={r[2]}  "
             f"{str(r[3]):<12} rain={r[4]}mm  → {r[6]}")

con.close()
log.info(f"\n✓ All done in {elapsed()}")
log.info("Next: python build_map.py")

#from pathlib import Path
##DB_SPW = str(ROOT / "export/databases/spw_liege.db")

"""
rebuild_all.py — WWI materialized indicators
Waits for DB lock to clear, then runs everything in one connection.
"""
import sqlite3, time, logging, os, sys
from pyproj import Transformer




logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)
T0 = time.time()

def elapsed():
    return f"{time.time()-T0:.1f}s"

DB = str(DB_SPW)
