"""
SPW Historical Ingest — Event-Driven Windows
==============================================

Sibling to historical_ingest.py, but instead of two fixed calendar
windows (2021_flood, 2023_2025), builds fetch windows dynamically from
the notable events catalog (events_catalog_notable.csv, produced by
build_event_catalog.py): every notable event gets a ±EVENT_BUFFER_DAYS
window around its [start, end], so you have lead-up and recession
context for each event, not just the event window itself.

Why this exists: the original two-window approach captures the known
big events (July 2021, the 2023-2025 span) but doesn't deliberately
give every notable event — including ones discovered later via
clustering — its own padded context window. This script closes that
gap without re-fetching everything: overlapping/adjacent event windows
are merged first, so a cluster of nearby events (e.g. the Feb 2024
flood that hit four stations within days of each other) becomes ONE
merged fetch window, not four overlapping ones.

All fetch/dedup/DB logic is reused unchanged from historical_ingest.py
(same STATIONS list, same already_fetched() cache check, same schema)
— this script only changes how WINDOWS is constructed.

Run
---
  cd ~/wwi
  python3 historical_ingest_events.py

Requires export/csvs/events_catalog_notable.csv to already exist
(run check_wave_propagation.py -> detect_events_clustered.py ->
build_event_catalog.py first).
"""

import requests
import sqlite3
import math
import time
import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
DB_HIST = str(ROOT / "export/databases/historical_liege.db")
EVENTS_NOTABLE_CSV = ROOT / "export/csvs/events_catalog_notable.csv"

BASE_URL = "https://hydrometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://hydrometrie.wallonie.be/home/observations/hauteur.html"
PAUSE = 0.4

EVENT_BUFFER_DAYS = 3  # fixed +/- buffer around every event's [start, end]

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
    "Referer": PORTAL_URL,
    "Accept": "application/json, text/plain, */*",
})

# ── Stations (identical list to historical_ingest.py — all 15 network
#    stations get fetched for every event window, per the decision to
#    keep network-wide context available for any event) ─────────────
STATIONS = [
    ("6387",  "EUPEN",           "DGH",  "Vesdre"),
    ("6228",  "CHAUDFONTAINE",   "DGH",  "Vesdre"),
    ("L7150", "VERVIERS",        "DCENN", "Vesdre"),   # was 6204/DGH (wrong code AND
                                                          # wrong network — confirmed via
                                                          # ts_path lookup: real path is
                                                          # DCENN/L7150/..., not DGH/...;
                                                          # other stations confirmed DGH-correct
    ("5904",  "COMBLAIN",        "DGH",  "Ourthe"),
    ("5826",  "SAUHEID",         "DGH",  "Ourthe"),
    ("5803",  "ANGLEUR",         "DGH",  "Ourthe"),    # was 5808 (Q-only station; 5803 has H)
    ("6732",  "STAVELOT",        "DGH",  "Amblève"),
    ("6651",  "REMOUCHAMPS",     "DGH",  "Amblève"),    # was 6821 (wrong/nonexistent code)
    ("6832",  "TROIS-PONTS",     "DGH",  "Salm"),
    ("7141",  "HUY",             "DGH",  "Meuse"),
    ("7133",  "LIEGE",           "DGH",  "Meuse"),
    ("8001",  "NAMUR",           "DGH",  "Meuse"),    # was 5647 (wrong code; 994 is also dead/legacy)
    ("6657",  "LOUVEIGNE",       "DGH",  "Ourthe"),
    ("6958",  "ROBERTVILLE",     "DGH",  "Vesdre"),
    ("6529",  "MONT-RIGI",       "DGH",  "Amblève"),
]

TIMESERIES = [
    ("H", "Day.Mean",   "Timestamp,Value,Quality Code", "daily_H_mean"),
    ("H", "h.Mean",     "Timestamp,Value,Quality Code", "hourly_H_mean"),
    ("Q", "Day.Mean",   "Timestamp,Value,Quality Code", "daily_Q_mean"),
    ("Precip", "Day.Total", "Timestamp,Value,Quality Code", "daily_Precip_total"),
]

# ── Per-station path overrides ─────────────────────────────────────
# Confirmed via direct KiWIS getTimeseriesList lookups: the parameter
# FOLDER name in ts_path is not always the plain parameter type. Most
# stations use "H" for water level; these three don't:
#   - ANGLEUR (5803): folder is "H_sonde" (plain "H" doesn't exist there)
#   - NAMUR (8001): folder is "Habs_sonde", AND the ts_name itself has a
#     ".Abs" suffix (e.g. "Day.Mean.Abs", "h.Mean.Abs") rather than the
#     plain "Day.Mean"/"h.Mean" every other H station uses.
# REMOUCHAMPS (6651) uses the plain "H" folder with plain ts_names —
# confirmed identical to the working stations — so its HTTP 500s are
# NOT a path problem; see note in __main__ below.
PARAM_FOLDER_OVERRIDE = {
    "5803": {"H": "H_sonde"},
    "8001": {"H": "Habs_sonde"},
}
TS_NAME_SUFFIX_OVERRIDE = {
    "8001": {"H": ".Abs"},
}

# Stations confirmed to have ONLY Precip (and/or T) — no H or Q
# timeseries exist for them at all, so requesting H/Q is not an error
# to fix, it's an expected "no data" by design. Skip H/Q requests for
# these rather than logging a confusing failure every run.
PRECIP_ONLY_STATIONS = {"6657", "6958", "6529"}  # LOUVEIGNE, ROBERTVILLE, MONT-RIGI


def resolve_ts_path(network, sno, param, ts_name):
    """Build the ts_path for one station/param/ts_name, applying any
    per-station folder/suffix overrides discovered via direct KiWIS
    lookups (see PARAM_FOLDER_OVERRIDE / TS_NAME_SUFFIX_OVERRIDE)."""
    folder = PARAM_FOLDER_OVERRIDE.get(sno, {}).get(param, param)
    suffix = TS_NAME_SUFFIX_OVERRIDE.get(sno, {}).get(param, "")
    return f"{network}/{sno}/{folder}/{ts_name}{suffix}"


# ── Window construction ────────────────────────────────────────────
def build_event_windows(events_notable_csv, buffer_days=EVENT_BUFFER_DAYS):
    """
    Read the notable events catalog, pad each event's [start, end] by
    +/- buffer_days, then merge overlapping/adjacent windows into a
    minimal set of non-overlapping (window_label, date_from, date_to)
    tuples in the same string format historical_ingest.py's WINDOWS
    list uses ("YYYY-MM-DDTHH:MM:SSZ").

    Standard interval-merging: sort by start, then merge any window
    whose start falls at or before the current merged window's end.
    """
    df = pd.read_csv(events_notable_csv, parse_dates=["start", "end"])
    if df.empty:
        return []

    intervals = []
    for _, row in df.iterrows():
        padded_start = row["start"] - timedelta(days=buffer_days)
        padded_end = row["end"] + timedelta(days=buffer_days)
        intervals.append((padded_start, padded_end))

    intervals.sort(key=lambda x: x[0])

    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    windows = []
    for i, (start, end) in enumerate(merged):
        label = f"event_window_{i+1:03d}_{start.date()}_{end.date()}"
        windows.append((
            label,
            start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))

    return windows


# ── DB (identical to historical_ingest.py) ─────────────────────────
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


# ── Fetch (identical to historical_ingest.py) ──────────────────────
def _f(x):
    if x is None:
        return None
    try:
        f = float(x)
        return None if math.isnan(f) else f
    except Exception:
        return None


def _i(x):
    if x is None:
        return None
    try:
        f = float(x)
        return None if math.isnan(f) else int(f)
    except Exception:
        return None


def fetch(ts_path, returnfields, date_from, date_to):
    r = SESSION.get(BASE_URL, params={
        "request": "getTimeseriesValues",
        "service": "kisters",
        "type": "queryServices",
        "datasource": "0",
        "format": "json",
        "ts_path": ts_path,
        "metadata": "true",
        "returnfields": returnfields,
        "from": date_from,
        "to": date_to,
    }, timeout=30)
    r.raise_for_status()
    raw = r.json()
    block = raw[0]
    rows = block.get("data", [])
    return rows, block


def already_fetched(con, station_no, parameter, ts_name, date_from, date_to):
    n = con.execute("""
        SELECT COUNT(*) FROM observations
        WHERE station_no=? AND parameter=? AND ts_name=?
          AND timestamp >= ? AND timestamp <= ?
    """, (station_no, parameter, ts_name,
          date_from[:10], date_to[:10])).fetchone()[0]
    return n > 0


# ── Main ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not EVENTS_NOTABLE_CSV.exists():
        raise FileNotFoundError(
            f"{EVENTS_NOTABLE_CSV} not found. Run check_wave_propagation.py "
            f"-> detect_events_clustered.py -> build_event_catalog.py first."
        )

    WINDOWS = build_event_windows(EVENTS_NOTABLE_CSV, EVENT_BUFFER_DAYS)

    log.info("=" * 60)
    log.info("SPW Historical Ingest — Event-Driven Windows")
    log.info(f"Stations: {len(STATIONS)}  "
             f"Merged event windows: {len(WINDOWS)}  "
             f"(buffer: +/-{EVENT_BUFFER_DAYS} days per event)")
    log.info(f"DB: {DB_HIST}")
    log.info("=" * 60)

    for label, date_from, date_to in WINDOWS:
        log.info(f"  {label}: {date_from[:10]} -> {date_to[:10]}")

    r0 = SESSION.get(PORTAL_URL, timeout=15)
    log.info(f"Portal: {r0.status_code}  cookies: {list(SESSION.cookies.keys())}")

    con = init_db(DB_HIST)

    con.executemany(
        "INSERT OR IGNORE INTO stations VALUES (?,?,?,?)",
        [(s[0], s[1], s[3], s[2]) for s in STATIONS]
    )
    con.commit()

    total_ok = total_skip = total_fail = total_cached = 0

    for window_label, date_from, date_to in WINDOWS:
        log.info(f"\n{'='*60}")
        log.info(f"Window: {window_label}  ({date_from[:10]} -> {date_to[:10]})")
        log.info(f"{'='*60}")

        for sno, label, network, river in STATIONS:
            log.info(f"\n-- {label} ({sno}) - {river}")

            for param, ts_name, returnfields, ts_label in TIMESERIES:

                if param == "Precip" and sno not in [
                    "6657", "6958", "6529", "6967", "6550", "6712"
                ]:
                    continue

                # Precip-only stations (LOUVEIGNE, ROBERTVILLE, MONT-RIGI)
                # have no H or Q timeseries at all — confirmed via direct
                # KiWIS lookup. Skip these requests entirely rather than
                # log a confusing HTTP 500 for data that was never there.
                if param in ("H", "Q") and sno in PRECIP_ONLY_STATIONS:
                    continue

                ts_path = resolve_ts_path(network, sno, param, ts_name)

                if already_fetched(con, sno, param, ts_name, date_from, date_to):
                    log.info(f"  {ts_label:<22} already cached - skip")
                    total_cached += 1
                    continue

                log.info(f"  {ts_label:<22} {ts_path}")

                try:
                    rows, meta = fetch(ts_path, returnfields, date_from, date_to)
                    non_null = [r for r in rows if r[1] is not None]

                    if not non_null:
                        log.warning("    -> no data")
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

                    if param == "H" and non_null:
                        peak = max(non_null, key=lambda x: x[1] or 0)
                        log.info(f"    -> {len(non_null):>5} records  "
                                 f"peak={peak[1]:.3f}m  "
                                 f"on {str(peak[0])[:10]}  qc={peak[2]}")
                    else:
                        log.info(f"    -> {len(non_null):>5} records")

                    total_ok += 1

                except requests.HTTPError as e:
                    log.error(f"    -> HTTP {e.response.status_code}")
                    total_fail += 1
                except Exception as e:
                    log.error(f"    -> {e}")
                    total_fail += 1

                time.sleep(PAUSE)

    log.info(f"\n{'='*60}")
    log.info("Done.")
    log.info(f"  OK={total_ok}  skipped={total_skip}  "
             f"failed={total_fail}  cached={total_cached}")

    n_total = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    log.info(f"  Total observations in DB: {n_total:,}")

    con.close()
