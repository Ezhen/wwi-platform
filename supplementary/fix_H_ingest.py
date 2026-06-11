"""
Fix SPW H ingest — change from Absolute Value (NGF elevation ~67m)
to Value (gauge-relative height ~0.3-1.5m).

Also clears the bad H observations and re-fetches correctly.
Run once, then normal update.sh takes over.
"""
import requests, sqlite3, math, time, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent.parent
DB_SPW = str(ROOT / "export/databases/spw_liege.db")
BASE_URL   = "https://hydrometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://hydrometrie.wallonie.be/home/observations/hauteur.html"
FETCH_DAYS = 14

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": PORTAL_URL,
})

def _f(x):
    if x is None: return None
    try:
        f = float(x)
        return None if math.isnan(f) else f
    except: return None

def _i(x):
    if x is None: return None
    try: return int(float(x))
    except: return None

log.info("Seeding portal session...")
SESSION.get(PORTAL_URL, timeout=15)

con = sqlite3.connect(DB_SPW, timeout=30)
con.execute("PRAGMA journal_mode=DELETE")

# Get all H timeseries
h_ts = con.execute("""
    SELECT ts_id, station_no, ts_path FROM timeseries
    WHERE parameter = 'H'
""").fetchall()
log.info(f"Found {len(h_ts)} H timeseries")

# Check current values
sample = con.execute("""
    SELECT o.station_no, s.station_name, o.value, o.timestamp
    FROM observations o JOIN stations s ON o.station_no = s.station_no
    WHERE o.parameter = 'H'
    ORDER BY o.timestamp DESC LIMIT 5
""").fetchall()
log.info("Current H sample:")
for r in sample:
    log.info(f"  {r[0]:8s} {r[1]:<25} H={r[2]}  {r[3][:16]}")

# Determine if values are NGF (>10m = likely NGF elevation)
avg_h = con.execute(
    "SELECT AVG(value) FROM observations WHERE parameter='H' AND value IS NOT NULL"
).fetchone()[0]
log.info(f"Average H value: {avg_h:.2f}m")

if avg_h and avg_h > 10:
    log.info("Values appear to be NGF elevations — re-ingesting with Value returnfields")

    # Delete bad H observations (keep structure, clear data)
    n_del = con.execute("DELETE FROM observations WHERE parameter='H'").rowcount
    con.commit()
    log.info(f"Deleted {n_del} NGF H observations")

    # Re-fetch with correct returnfields
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=FETCH_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    ok = skipped = failed = 0
    for ts_id, sno, ts_path in h_ts:
        try:
            r = SESSION.get(BASE_URL, params={
                "request":      "getTimeseriesValues",
                "service":      "kisters",
                "type":         "queryServices",
                "datasource":   "0",
                "format":       "json",
                "ts_path":      ts_path,
                "returnfields": "Timestamp,Value,Quality Code",  # ← KEY FIX
                "from":         start,
                "to":           end,
            }, timeout=30)
            r.raise_for_status()
            block = r.json()[0]
            data  = block.get("data", [])
            non_null = [(d[0], _f(d[1]), _i(d[2]))
                        for d in data if d[1] is not None]

            if not non_null:
                skipped += 1
                time.sleep(0.2)
                continue

            con.executemany("""
                INSERT OR IGNORE INTO observations
                    (ts_id, station_no, parameter, timestamp, value, quality_code)
                VALUES (?,?,?,?,?,?)
            """, [(ts_id, sno, "H", d[0], d[1], d[2]) for d in non_null])
            con.commit()
            log.info(f"  {sno:8s}: {len(non_null)} records  "
                     f"sample={non_null[0][1]:.3f}m")
            ok += 1

        except Exception as e:
            log.error(f"  {sno}: {e}")
            failed += 1
        time.sleep(0.3)

    log.info(f"\nDone: OK={ok} skipped={skipped} failed={failed}")

    # Verify fix
    sample2 = con.execute("""
        SELECT o.station_no, s.station_name, o.value
        FROM observations o JOIN stations s ON o.station_no = s.station_no
        WHERE o.parameter = 'H' ORDER BY o.timestamp DESC LIMIT 5
    """).fetchall()
    log.info("\nFixed H sample:")
    for r in sample2:
        log.info(f"  {r[0]:8s} {r[1]:<25} H={r[2]:.3f}m")

else:
    log.info("H values already look correct — no fix needed")

con.close()
log.info("\nNext: python processing/rebuild_all.py && python build_alerts.py")
