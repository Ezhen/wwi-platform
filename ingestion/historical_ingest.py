"""
SPW Historical Data Ingest v2 — 2023-2025 + July 2021
Fetches daily and hourly H, Q for key Liège basin stations.
Appends to existing historical_liege.db (safe to re-run).
"""

import requests
import sqlite3
import math
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

ROOT    = Path(__file__).parent.parent
DB_HIST = str(ROOT / "export/databases/historical_liege.db")

BASE_URL   = "https://hydrometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://hydrometrie.wallonie.be/home/observations/hauteur.html"
PAUSE      = 0.4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    PORTAL_URL,
    "Accept":     "application/json, text/plain, */*",
})

# ── Stations ──────────────────────────────────────────────────────────────────
STATIONS = [
    ("6387",  "EUPEN",           "DGH",  "Vesdre"),
    ("6228",  "CHAUDFONTAINE",   "DGH",  "Vesdre"),
    ("6204",  "VERVIERS",        "DGH",  "Vesdre"),
    ("5904",  "COMBLAIN",        "DGH",  "Ourthe"),
    ("5826",  "SAUHEID",         "DGH",  "Ourthe"),
    ("5808",  "ANGLEUR",         "DGH",  "Ourthe"),
    ("6732",  "STAVELOT",        "DGH",  "Amblève"),
    ("6821",  "REMOUCHAMPS",     "DGH",  "Amblève"),
    ("6832",  "TROIS-PONTS",     "DGH",  "Salm"),
    ("7141",  "HUY",             "DGH",  "Meuse"),
    ("7133",  "LIEGE",           "DGH",  "Meuse"),
    ("5647",  "NAMUR",           "DGH",  "Meuse"),
    # Extra stations for richer training data
    ("6657",  "LOUVEIGNE",       "DGH",  "Ourthe"),   # precip reference
    ("6958",  "ROBERTVILLE",     "DGH",  "Vesdre"),   # headwater precip
    ("6529",  "MONT-RIGI",       "DGH",  "Amblève"),  # Hautes Fagnes precip
]

# ── Date windows ──────────────────────────────────────────────────────────────
WINDOWS = [
    ("2021_flood",    "2021-06-01T00:00:00Z", "2021-09-30T00:00:00Z"),
    ("2023_2025",     "2023-01-01T00:00:00Z", "2025-06-08T00:00:00Z"),
]

# ── Timeseries to fetch ───────────────────────────────────────────────────────
TIMESERIES = [
    ("H", "Day.Mean",   "Timestamp,Value,Quality Code", "daily_H_mean"),
    ("H", "h.Mean",     "Timestamp,Value,Quality Code", "hourly_H_mean"),
    ("Q", "Day.Mean",   "Timestamp,Value,Quality Code", "daily_Q_mean"),
    ("Precip", "Day.Total", "Timestamp,Value,Quality Code", "daily_Precip_total"),
]


# ── DB ────────────────────────────────────────────────────────────────────────

def init_db(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS stations (
            station_no  TEXT PRIMARY KEY,
            label       TEXT,
            river       TEXT,
            network     TEXT
        );

        CREATE TABLE IF NOT EXISTS observations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            station_no   TEXT    NOT NULL,
            parameter    TEXT    NOT NULL,
            ts_name      TEXT    NOT NULL,
            timestamp    TEXT    NOT NULL,
            value        REAL,
            quality_code INTEGER,
            UNIQUE(station_no, parameter, ts_name, timestamp)
        );

        CREATE INDEX IF NOT EXISTS idx_hist_sno
            ON observations(station_no);
        CREATE INDEX IF NOT EXISTS idx_hist_ts
            ON observations(timestamp);
        CREATE INDEX IF NOT EXISTS idx_hist_param
            ON observations(parameter);
        CREATE INDEX IF NOT EXISTS idx_hist_sno_param
            ON observations(station_no, parameter, ts_name, timestamp);
    """)
    con.commit()
    return con


# ── Fetch ─────────────────────────────────────────────────────────────────────

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


def fetch(ts_path, returnfields, date_from, date_to):
    r = SESSION.get(BASE_URL, params={
        "request":      "getTimeseriesValues",
        "service":      "kisters",
        "type":         "queryServices",
        "datasource":   "0",
        "format":       "json",
        "ts_path":      ts_path,
        "metadata":     "true",
        "returnfields": returnfields,
        "from":         date_from,
        "to":           date_to,
    }, timeout=30)
    r.raise_for_status()
    raw   = r.json()
    block = raw[0]
    rows  = block.get("data", [])
    return rows, block


def already_fetched(con, station_no, parameter, ts_name, date_from, date_to):
    """Check if we already have data for this station/param/window."""
    n = con.execute("""
        SELECT COUNT(*) FROM observations
        WHERE station_no=? AND parameter=? AND ts_name=?
          AND timestamp >= ? AND timestamp <= ?
    """, (station_no, parameter, ts_name,
          date_from[:10], date_to[:10])).fetchone()[0]
    return n > 0


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 60)
    log.info("SPW Historical Ingest v2")
    log.info(f"Stations: {len(STATIONS)}  Windows: {len(WINDOWS)}")
    log.info(f"DB: {DB_HIST}")
    log.info("=" * 60)

    r0 = SESSION.get(PORTAL_URL, timeout=15)
    log.info(f"Portal: {r0.status_code}  cookies: {list(SESSION.cookies.keys())}")

    con = init_db(DB_HIST)

    # Register stations
    con.executemany(
        "INSERT OR IGNORE INTO stations VALUES (?,?,?,?)",
        [(s[0], s[1], s[3], s[2]) for s in STATIONS]
    )
    con.commit()

    total_ok = total_skip = total_fail = total_cached = 0

    for window_label, date_from, date_to in WINDOWS:
        log.info(f"\n{'='*60}")
        log.info(f"Window: {window_label}  ({date_from[:10]} → {date_to[:10]})")
        log.info(f"{'='*60}")

        for sno, label, network, river in STATIONS:
            log.info(f"\n── {label} ({sno}) — {river}")

            for param, ts_name, returnfields, ts_label in TIMESERIES:

                # Skip precip for non-precip stations
                if param == "Precip" and sno not in [
                    "6657","6958","6529","6967","6550","6712"
                ]:
                    continue

                ts_path = f"{network}/{sno}/{param}/{ts_name}"

                # Skip if already in DB
                if already_fetched(con, sno, param, ts_name, date_from, date_to):
                    log.info(f"  {ts_label:<22} already cached — skip")
                    total_cached += 1
                    continue

                log.info(f"  {ts_label:<22} {ts_path}")

                try:
                    rows, meta = fetch(ts_path, returnfields, date_from, date_to)
                    non_null = [r for r in rows if r[1] is not None]

                    if not non_null:
                        log.warning(f"    → no data")
                        total_skip += 1
                        time.sleep(PAUSE)
                        continue

                    insert_rows = [
                        (sno, param, ts_name, str(r[0]), _f(r[1]), _i(r[2]))
                        for r in non_null
                    ]
                    con.executemany("""
                        INSERT OR IGNORE INTO observations
                            (station_no, parameter, ts_name,
                             timestamp, value, quality_code)
                        VALUES (?,?,?,?,?,?)
                    """, insert_rows)
                    con.commit()

                    # Find peak for context
                    if param == "H" and non_null:
                        peak = max(non_null, key=lambda x: x[1] or 0)
                        log.info(f"    → {len(non_null):>5} records  "
                                 f"peak={peak[1]:.3f}m  "
                                 f"on {str(peak[0])[:10]}  qc={peak[2]}")
                    else:
                        log.info(f"    → {len(non_null):>5} records")

                    total_ok += 1

                except requests.HTTPError as e:
                    log.error(f"    → HTTP {e.response.status_code}")
                    total_fail += 1
                except Exception as e:
                    log.error(f"    → {e}")
                    total_fail += 1

                time.sleep(PAUSE)

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info(f"\n{'='*60}")
    log.info(f"Done.")
    log.info(f"  OK={total_ok}  skipped={total_skip}  "
             f"failed={total_fail}  cached={total_cached}")

    n_total = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    log.info(f"  Total observations in DB: {n_total:,}")

    log.info("\n── Coverage by station/parameter ───────────────────")
    for r in con.execute("""
        SELECT s.label, s.river, o.parameter, o.ts_name,
               COUNT(*) AS n,
               MIN(o.timestamp) AS t_min,
               MAX(o.timestamp) AS t_max
        FROM observations o
        JOIN stations s ON o.station_no = s.station_no
        GROUP BY s.station_no, o.parameter, o.ts_name
        ORDER BY s.river, s.label, o.parameter, o.ts_name
    """):
        log.info(f"  {r[1]:<10} {r[0]:<18} {r[2]:<8} {r[3]:<18} "
                 f"n={r[4]:>6}  {r[5][:10]} → {r[6][:10]}")

    log.info(f"\nDB → {Path(DB_HIST).resolve()}")
    con.close()
