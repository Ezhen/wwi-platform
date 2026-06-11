"""
SPW Precipitation re-ingest using correct ts_path: Precip/5m.CmdTotal.P
Discovers precip stations via getTimeseriesList, fetches 5-min values.
Replaces wrongly labelled Precip data in spw_liege.db.
"""

import requests
import sqlite3
import math
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT   = Path(__file__).parent
DB_SPW = str(ROOT / "export/databases/spw_liege.db")

BASE_URL   = "https://hydrometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://hydrometrie.wallonie.be/home/observations/hauteur.html"
FETCH_DAYS = 7
PAUSE      = 0.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":         PORTAL_URL,
    "Accept":          "application/json, text/plain, */*",
})


def init_session():
    r = SESSION.get(PORTAL_URL, timeout=15)
    log.info(f"Portal: {r.status_code}  cookies: {list(SESSION.cookies.keys())}")


def _f(x):
    if x is None: return None
    try:
        f = float(x); return None if math.isnan(f) else f
    except: return None

def _i(x):
    if x is None: return None
    try:
        f = float(x); return None if math.isnan(f) else int(f)
    except: return None


def discover_precip_stations():
    """Find all stations that have Precip/5m.CmdTotal.P timeseries."""
    log.info("Discovering precipitation stations...")

    # Get all stations in our DB
    con = sqlite3.connect(DB_SPW, timeout=30)
    stations = con.execute(
        "SELECT DISTINCT station_no FROM stations"
    ).fetchall()
    con.close()

    station_nos = [r[0] for r in stations]
    log.info(f"  Checking {len(station_nos)} stations for Precip timeseries...")

    precip_stations = []
    for i, sno in enumerate(station_nos):
        try:
            r = SESSION.get(BASE_URL, params={
                "request":      "getTimeseriesList",
                "service":      "kisters",
                "type":         "queryServices",
                "datasource":   "0",
                "format":       "objson",
                "station_no":   sno,
                "returnfields": "ts_path,ts_name,ts_unitsymbol",
            }, timeout=15)
            if r.status_code != 200:
                continue
            ts_list = r.json()
            for ts in ts_list:
                path = ts.get("ts_path", "")
                if "/Precip/5m.CmdTotal.P" in path:
                    precip_stations.append({
                        "station_no": sno,
                        "ts_path":    path,
                        "ts_unit":    ts.get("ts_unitsymbol", "mm"),
                    })
                    log.info(f"  [{i+1}/{len(station_nos)}] ✓ {sno}: {path}")
                    break
            time.sleep(0.2)
        except Exception as e:
            log.warning(f"  {sno}: {e}")

    log.info(f"Found {len(precip_stations)} precip stations")
    return precip_stations


def fetch_precip(ts_path):
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=FETCH_DAYS)
    r = SESSION.get(BASE_URL, params={
        "request":      "getTimeseriesValues",
        "service":      "kisters",
        "type":         "queryServices",
        "datasource":   "0",
        "format":       "json",
        "ts_path":      ts_path,
        "metadata":     "true",
        "returnfields": "Timestamp,Value,Quality Code",
        "from":         start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to":           now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, timeout=30)
    r.raise_for_status()
    block     = r.json()[0]
    data_rows = block.get("data", [])
    if not data_rows:
        return [], block

    rows = []
    for row in data_rows:
        val = _f(row[1])
        if val is None: continue
        rows.append((
            str(pd_ts(row[0])),
            val,
            _i(row[2])
        ))
    return rows, block


def pd_ts(s):
    """Parse timestamp string."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except:
        return s


if __name__ == "__main__":
    import pandas as pd

    log.info("=" * 55)
    log.info("SPW Precipitation Re-ingest")
    log.info("=" * 55)

    init_session()

    con = sqlite3.connect(DB_SPW, timeout=60)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA synchronous=NORMAL")

    # Step 1: Remove wrongly labelled Precip data (those are Q stations)
    log.info("\nStep 1: Removing incorrectly labelled Precip observations...")
    n_old = con.execute(
        "SELECT COUNT(*) FROM observations WHERE parameter='Precip'"
    ).fetchone()[0]
    log.info(f"  Current Precip rows: {n_old}")

    # Remove only stations whose ts_path contains /Q/ (wrongly labelled)
    n_removed = con.execute("""
        DELETE FROM observations
        WHERE parameter = 'Precip'
          AND ts_id IN (
              SELECT ts_id FROM timeseries
              WHERE ts_path LIKE '%/Q/%'
          )
    """).rowcount
    con.commit()
    log.info(f"  Removed {n_removed} wrongly labelled Q-as-Precip rows")

    # Remove wrong timeseries entries
    con.execute("""
        DELETE FROM timeseries
        WHERE parameter = 'Precip'
          AND ts_path LIKE '%/Q/%'
    """)
    con.commit()

    # Step 2: Discover real precip stations
    precip_stations = discover_precip_stations()

    if not precip_stations:
        log.error("No precip stations found — check API connection")
        con.close()
        exit(1)

    # Step 3: Ingest
    log.info(f"\nStep 3: Ingesting {len(precip_stations)} precip stations...")
    ok = skipped = failed = 0

    for st in precip_stations:
        sno     = st["station_no"]
        ts_path = st["ts_path"]
        ts_id   = f"{sno}_Precip_5m"

        # Register timeseries
        con.execute("""
            INSERT INTO timeseries (ts_id, station_no, parameter, ts_path, ts_unit)
            VALUES (?,?,?,?,?)
            ON CONFLICT(ts_id) DO UPDATE SET ts_path=excluded.ts_path
        """, (ts_id, sno, "Precip", ts_path, st["ts_unit"]))
        con.commit()

        try:
            rows, block = fetch_precip(ts_path)
            if not rows:
                log.warning(f"  {sno}: no data")
                skipped += 1
            else:
                inserted = 0
                for ts_str, val, qc in rows:
                    try:
                        con.execute("""
                            INSERT OR IGNORE INTO observations
                                (ts_id, station_no, parameter, timestamp, value, quality_code)
                            VALUES (?,?,?,?,?,?)
                        """, (ts_id, sno, "Precip", ts_str, val, qc))
                        inserted += 1
                    except: pass
                con.commit()
                log.info(f"  {sno}: {len(rows)} records, {inserted} inserted")
                ok += 1
        except Exception as e:
            log.error(f"  {sno}: {e}")
            failed += 1

        time.sleep(PAUSE)

    # Summary
    n_new = con.execute(
        "SELECT COUNT(*) FROM observations WHERE parameter='Precip'"
    ).fetchone()[0]
    log.info(f"\n{'='*55}")
    log.info(f"Done. OK={ok} skipped={skipped} failed={failed}")
    log.info(f"Precip observations: {n_old} → {n_new}")

    # Sample check
    log.info("\nSample precip values (non-zero):")
    for r in con.execute("""
        SELECT o.station_no, s.station_name, o.timestamp, o.value
        FROM observations o
        JOIN stations s ON o.station_no = s.station_no
        WHERE o.parameter = 'Precip' AND o.value > 0
        ORDER BY o.value DESC
        LIMIT 10
    """):
        log.info(f"  {r[0]:8s}  {r[1]:<25}  {r[2][:16]}  {r[3]:.3f} mm")

    con.close()
    log.info("\nNext: python processing/rebuild_all.py")
