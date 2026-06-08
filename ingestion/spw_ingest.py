from pathlib import Path
ROOT = Path(__file__).parent.parent
DB_SPW = str(ROOT / "export/databases/spw_liege.db")
"""
SPW KiWIS — Multi-parameter ingestion (H, Q, Precip) into SQLite
Schema expanded with `parameter` column to hold all variables in one table.
"""

import requests
import pandas as pd
import sqlite3
import time
import logging
from datetime import datetime, timedelta, timezone

# --- Config ---
PROVINCE   = "LIEGE"
FETCH_DAYS = 7
PAUSE      = 1.0
DB_PATH = str(DB_SPW)

# Timeseries groups — fill in Q and Precip group IDs once confirmed from devtools
PARAMETER_GROUPS = {
    "H":      "1962373",   # Water level   — confirmed
    "Q":      "1962340",   # Discharge     — confirmed
    # Precip removed — uses direct ts_path ingestion below (group ID was wrong)
}

BASE_URL   = "https://hydrometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://hydrometrie.wallonie.be/home/observations/hauteur.html"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":         PORTAL_URL,
    "Origin":          "https://hydrometrie.wallonie.be",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "fr-BE,fr;q=0.9,en;q=0.8",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ── Database ─────────────────────────────────────────────────────────────────

def init_db(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS stations (
            station_no      TEXT PRIMARY KEY,
            station_name    TEXT,
            site_name       TEXT,
            river_name      TEXT,
            basin           TEXT,
            local_x         REAL,
            local_y         REAL,
            elevation       REAL,
            catchment_km2   REAL,
            status          TEXT
        );

        CREATE TABLE IF NOT EXISTS timeseries (
            ts_id          TEXT PRIMARY KEY,
            station_no     TEXT NOT NULL REFERENCES stations(station_no),
            parameter      TEXT NOT NULL,   -- H, Q, QADM, Precip
            ts_path        TEXT NOT NULL,
            ts_unit        TEXT,
            last_fetched   TEXT
        );

        CREATE TABLE IF NOT EXISTS observations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_id        TEXT    NOT NULL REFERENCES timeseries(ts_id),
            station_no   TEXT    NOT NULL,
            parameter    TEXT    NOT NULL,
            timestamp    TEXT    NOT NULL,
            value        REAL,
            quality_code INTEGER,
            UNIQUE(ts_id, timestamp)
        );

        CREATE INDEX IF NOT EXISTS idx_obs_station
            ON observations(station_no);
        CREATE INDEX IF NOT EXISTS idx_obs_param
            ON observations(parameter);
        CREATE INDEX IF NOT EXISTS idx_obs_timestamp
            ON observations(timestamp);
    """)
    con.commit()
    return con


def upsert_stations(con: sqlite3.Connection, df: pd.DataFrame):
    rows = []
    for _, r in df.iterrows():
        catchment = None
        if pd.notna(r.get("CATCHMENT_SIZE")):
            try:
                catchment = float(str(r["CATCHMENT_SIZE"]).replace(",", ".").split()[0])
            except Exception:
                pass
        rows.append((
            str(r["station_no"]), r.get("station_name"), r.get("site_name"),
            r.get("river_name"), r.get("BASSIN_INFOCRUE"),
            r.get("station_local_x"), r.get("station_local_y"),
            r.get("station_elevation"), catchment, r.get("station_status"),
        ))
    con.executemany("""
        INSERT INTO stations
            (station_no, station_name, site_name, river_name, basin,
             local_x, local_y, elevation, catchment_km2, status)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(station_no) DO UPDATE SET
            station_name = excluded.station_name,
            status       = excluded.status
    """, rows)
    con.commit()


def upsert_timeseries(con: sqlite3.Connection, station_no: str,
                      parameter: str, ts_path: str,
                      ts_unit: str, ts_id: str):
    con.execute("""
        INSERT INTO timeseries (ts_id, station_no, parameter, ts_path, ts_unit)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ts_id) DO UPDATE SET
            ts_path = excluded.ts_path,
            ts_unit = excluded.ts_unit
    """, (ts_id, station_no, parameter, ts_path, ts_unit))
    con.commit()


def insert_observations(con: sqlite3.Connection, ts_id: str,
                        station_no: str, parameter: str,
                        df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    rows = [
        (ts_id, station_no, parameter, str(row["timestamp"]),
         row["value"], row["quality_code"])
        for _, row in df.iterrows()
    ]
    con.executemany("""
        INSERT OR IGNORE INTO observations
            (ts_id, station_no, parameter, timestamp, value, quality_code)
        VALUES (?,?,?,?,?,?)
    """, rows)
    con.commit()
    return len(rows)


def mark_fetched(con: sqlite3.Connection, ts_id: str):
    con.execute(
        "UPDATE timeseries SET last_fetched = ? WHERE ts_id = ?",
        (datetime.now(timezone.utc).isoformat(), ts_id),
    )
    con.commit()


# ── API ───────────────────────────────────────────────────────────────────────

def init_session():
    r = SESSION.get(PORTAL_URL, timeout=15)
    log.info(f"Portal: {r.status_code}  cookies: {list(SESSION.cookies.keys())}")


def discover_stations(province: str, group_id: str) -> pd.DataFrame:
    params = {
        "request":             "getTimeseriesValueLayer",
        "service":             "kisters",
        "type":                "queryServices",
        "datasource":          "0",
        "format":              "objson",
        "metadata":            "true",
        "crs":                 "localxy",
        "md_returnfields":     "station_id,site_name,station_name,station_no,"
                               "ts_name,ts_id,ts_path,ts_shortname,site_no,"
                               "stationparameter_name,stationparameter_no,"
                               "ca_sta,ts_unitsymbol,parametertype_name,"
                               "object_type_shortname",
        "ca_sta_returnfields": "",
        "timeseriesgroup_id":  group_id,
    }
    r = SESSION.get(BASE_URL, params=params, timeout=30)
    r.raise_for_status()
    raw = r.json()
    df = pd.DataFrame(raw) if isinstance(raw, list) else pd.DataFrame(raw["data"])
    return df[df["PROVINCE"].str.upper() == province.upper()].copy()


def fetch_observations(ts_path: str, days: int) -> pd.DataFrame:
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    # H uses "Absolute Value" / "AV Quality Code"
    # Q and Precip use "Value" / "Quality Code"
    is_H = "/H/" in ts_path or "/H_sonde/" in ts_path or "/Habs" in ts_path
    returnfields = ("Timestamp,Absolute Value,AV Quality Code"
                    if is_H else
                    "Timestamp,Value,Quality Code")
    params = {
        "request":      "getTimeseriesValues",
        "service":      "kisters",
        "type":         "queryServices",
        "datasource":   "0",
        "format":       "json",
        "ts_path":      ts_path,
        "metadata":     "true",
        "returnfields": returnfields,
        "from":         start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to":           now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    r = SESSION.get(BASE_URL, params=params, timeout=30)
    r.raise_for_status()
    raw = r.json()
    if not raw or not isinstance(raw, list):
        return pd.DataFrame(), {}
    block     = raw[0]
    data_rows = block.get("data", [])
    if not data_rows:
        return pd.DataFrame(), block
    import math

    def _f(x):
        """Raw value → Python float or None."""
        if x is None:
            return None
        try:
            f = float(x)
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None

    def _i(x):
        """Raw value → Python int or None."""
        if x is None:
            return None
        try:
            f = float(x)
            return None if math.isnan(f) else int(f)
        except (TypeError, ValueError):
            return None

    timestamps   = [pd.Timestamp(r[0]) for r in data_rows]
    values       = [_f(r[1]) for r in data_rows]
    quality_codes = [_i(r[2]) for r in data_rows]

    df = pd.DataFrame({
        "timestamp":    timestamps,
        "value":        values,
        "quality_code": quality_codes,
    })

    # Skip stations with no actual data in this window
    if all(v is None for v in values):
        return pd.DataFrame(), block

    return df, block


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 55)
    log.info(f"SPW KiWIS Multi-Parameter Ingestion — {PROVINCE}")
    log.info("=" * 55)

    init_session()
    con = init_db(DB_PATH)

    total_ok, total_skip, total_fail = 0, 0, 0

    for parameter, group_id in PARAMETER_GROUPS.items():

        if group_id == "XXXXXXX":
            log.warning(f"Skipping {parameter} — group ID not set yet")
            continue

        log.info(f"\n{'─'*55}")
        log.info(f"Parameter: {parameter}  (group {group_id})")
        log.info(f"{'─'*55}")

        try:
            stations_df = discover_stations(PROVINCE, group_id)
            log.info(f"Found {len(stations_df)} stations for {parameter}")
        except Exception as e:
            log.error(f"Discovery failed for {parameter}: {e}")
            continue

        upsert_stations(con, stations_df)

        for i, row in stations_df.iterrows():
            station_no = str(row["station_no"])
            ts_path    = row["ts_path"]
            ts_id      = str(row.get("ts_id", f"{station_no}_{parameter}"))
            ts_unit    = row.get("ts_unitsymbol", "")
            name       = row.get("station_name", station_no)
            river      = row.get("river_name", "")

            log.info(f"  [{parameter}] {station_no:8s}  {name:28s}  {river}")

            upsert_timeseries(con, station_no, parameter, ts_path, ts_unit, ts_id)

            try:
                df_obs, _ = fetch_observations(ts_path, days=FETCH_DAYS)
                if df_obs.empty:
                    log.warning(f"    → no data")
                    total_skip += 1
                else:
                    # DEBUG: print first row types
                    r0 = df_obs.iloc[0]
                    log.info(f"    → sample: val={r0['value']!r} ({type(r0['value']).__name__}) "
                             f"qc={r0['quality_code']!r} ({type(r0['quality_code']).__name__})")
                    n = insert_observations(con, ts_id, station_no, parameter, df_obs)
                    mark_fetched(con, ts_id)
                    log.info(f"    → {len(df_obs)} records, {n} inserted")
                    total_ok += 1
            except requests.HTTPError as e:
                log.error(f"    → HTTP {e.response.status_code}")
                total_fail += 1
            except Exception as e:
                log.error(f"    → {e}")
                total_fail += 1

            time.sleep(PAUSE)


    # ── Precipitation — direct ts_path ingestion ─────────────────────────────
    # 19 confirmed stations with 5-min production totals in 0.1mm units
    PRECIP_STATIONS = [('5284', 'DGH/5284/Precip/5m.CmdTotal.P'), ('5578', 'DGH/5578/Precip/5m.CmdTotal.P'), ('5596', 'DGH/5596/Precip/5m.CmdTotal.P'), ('5649', 'DGH/5649/Precip/5m.CmdTotal.P'), ('5757', 'DGH/5757/Precip/5m.CmdTotal.P'), ('6497', 'DGH/6497/Precip/5m.CmdTotal.P'), ('6529', 'DGH/6529/Precip/5m.CmdTotal.P'), ('6538', 'DGH/6538/Precip/5m.CmdTotal.P'), ('6550', 'DGH/6550/Precip/5m.CmdTotal.P'), ('6657', 'DGH/6657/Precip/5m.CmdTotal.P'), ('6712', 'DGH/6712/Precip/5m.CmdTotal.P'), ('6718', 'DGH/6718/Precip/5m.CmdTotal.P'), ('6958', 'DGH/6958/Precip/5m.CmdTotal.P'), ('6967', 'DGH/6967/Precip/5m.CmdTotal.P'), ('7003', 'DGH/7003/Precip/5m.CmdTotal.P'), ('7016', 'DGH/7016/Precip/5m.CmdTotal.P'), ('7228', 'DGH/7228/Precip/5m.CmdTotal.P'), ('9915', 'DGH/9915/Precip/5m.CmdTotal.P'), ('9922', 'DGH/9922/Precip/5m.CmdTotal.P')]

    log.info(f"\n{'─'*55}")
    log.info(f"Parameter: Precip (direct ts_path, 19 stations)")
    log.info(f"{'─'*55}")

    for sno, ts_path in PRECIP_STATIONS:
        ts_id = f"{sno}_Precip_5m"

        # Ensure station and timeseries are registered
        upsert_stations(con, stations_df[stations_df.station_no.astype(str) == sno]
                        if len(stations_df[stations_df.station_no.astype(str) == sno]) > 0
                        else stations_df.head(0))
        upsert_timeseries(con, sno, "Precip", ts_path, "mm", ts_id)

        log.info(f"  [Precip] {sno:<10}  {ts_path}")
        try:
            df_obs, _ = fetch_observations(ts_path, days=FETCH_DAYS)
            if df_obs.empty:
                log.warning("    → no data"); total_skip += 1
            else:
                # Convert 0.1mm → mm
                df_obs["value"] = df_obs["value"].apply(
                    lambda x: round(x / 10.0, 3) if x is not None else None
                )
                n = insert_observations(con, ts_id, sno, "Precip", df_obs)
                mark_fetched(con, ts_id)
                log.info(f"    → {len(df_obs)} records, {n} inserted")
                total_ok += 1
        except requests.HTTPError as e:
            log.error(f"    → HTTP {e.response.status_code}"); total_fail += 1
        except Exception as e:
            log.error(f"    → {e}"); total_fail += 1
        time.sleep(PAUSE)

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("=" * 55)
    log.info(f"Done. OK={total_ok}  skipped={total_skip}  failed={total_fail}")

    cur = con.execute(
        "SELECT parameter, COUNT(*) FROM observations GROUP BY parameter"
    )
    log.info("Observations by parameter:")
    for param, count in cur.fetchall():
        log.info(f"  {param:10s}  {count:>8,}")

    cur = con.execute("SELECT COUNT(DISTINCT station_no) FROM observations")
    log.info(f"Total stations with data: {cur.fetchone()[0]}")
    log.info(f"DB → {Path(DB_PATH).resolve()}")
    con.close()
