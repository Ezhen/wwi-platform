from pathlib import Path
ROOT = Path(__file__).parent.parent
DB_PIEZ = str(ROOT / "export/databases/piez_liege.db")

"""
SPW Piézométrie — Full ingestion into SQLite
263 DESO stations, Prof parameter, Province=LIEGE filter.
Two values per record: absolute level (NGF) and depth to water table.
"""

import requests
import pandas as pd
import sqlite3
import time
import math
import logging
from datetime import datetime, timedelta, timezone

# --- Config ---
PROVINCE           = "LIEGE"
FETCH_DAYS         = 7
PAUSE              = 0.5        # piezometrie updates daily, lighter load
DB_PATH            = str(DB_PIEZ)
TIMESERIESGROUP_ID = "1962272"

BASE_URL   = "https://piezometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://piezometrie.wallonie.be/home/observations/niveau-deau-souterraine.html"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":         PORTAL_URL,
    "Origin":          "https://piezometrie.wallonie.be",
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


# ── Database ──────────────────────────────────────────────────────────────────

def init_db(db_path):
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS stations (
            station_no      TEXT PRIMARY KEY,
            station_name    TEXT,
            aquifer         TEXT,    -- MASESO_LIBELLE
            aquifer_code    TEXT,    -- MASESO_WEB
            commune         TEXT,
            province        TEXT,
            local_x         REAL,
            local_y         REAL,
            elevation       REAL,    -- ground datum (NGF)
            gwref_datum     REAL,    -- measurement reference datum (NGF)
            well_depth      TEXT,    -- PROFPUITS
            codeso          TEXT,    -- official DESO code
            status          TEXT,
            equipment       TEXT
        );

        CREATE TABLE IF NOT EXISTS timeseries (
            ts_id        TEXT PRIMARY KEY,
            station_no   TEXT NOT NULL REFERENCES stations(station_no),
            parameter    TEXT NOT NULL,   -- Prof_abs (NGF level) or Prof_depth
            ts_path      TEXT NOT NULL,
            ts_unit      TEXT,
            last_fetched TEXT
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

        CREATE INDEX IF NOT EXISTS idx_obs_station   ON observations(station_no);
        CREATE INDEX IF NOT EXISTS idx_obs_timestamp ON observations(timestamp);
        CREATE INDEX IF NOT EXISTS idx_obs_param     ON observations(parameter);
    """)
    con.commit()
    return con


def upsert_stations(con, df):
    rows = []
    for _, r in df.iterrows():
        try: elev = float(str(r.get("station_elevation","")).replace(",","."))
        except: elev = None
        try: gwref = float(str(r.get("GWREF_DATUM","")).replace(",","."))
        except: gwref = None
        try: x = float(r.get("station_local_x") or 0) or None
        except: x = None
        try: y = float(r.get("station_local_y") or 0) or None
        except: y = None
        rows.append((
            str(r["station_no"]),
            r.get("station_name"),
            r.get("MASESO_LIBELLE"),
            r.get("MASESO_WEB"),
            r.get("COMMUNE_LOCALITE"),
            r.get("PROVINCE"),
            x, y, elev, gwref,
            r.get("PROFPUITS"),
            r.get("CODESO"),
            r.get("station_status"),
            r.get("GR_EQUIPEMENT"),
        ))
    con.executemany("""
        INSERT INTO stations
            (station_no, station_name, aquifer, aquifer_code, commune,
             province, local_x, local_y, elevation, gwref_datum,
             well_depth, codeso, status, equipment)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(station_no) DO UPDATE SET
            station_name = excluded.station_name,
            status       = excluded.status
    """, rows)
    con.commit()


def upsert_ts(con, station_no, parameter, ts_path, ts_unit, ts_id):
    con.execute("""
        INSERT INTO timeseries (ts_id, station_no, parameter, ts_path, ts_unit)
        VALUES (?,?,?,?,?)
        ON CONFLICT(ts_id) DO UPDATE SET ts_path=excluded.ts_path
    """, (ts_id, station_no, parameter, ts_path, ts_unit))
    con.commit()


def insert_obs(con, ts_id, station_no, parameter, df):
    if df.empty: return 0
    rows = [(ts_id, station_no, parameter,
             str(row["timestamp"]), row["value"], row["quality_code"])
            for _, row in df.iterrows()]
    con.executemany("""
        INSERT OR IGNORE INTO observations
            (ts_id, station_no, parameter, timestamp, value, quality_code)
        VALUES (?,?,?,?,?,?)
    """, rows)
    con.commit()
    return len(rows)


def mark_fetched(con, ts_id):
    con.execute("UPDATE timeseries SET last_fetched=? WHERE ts_id=?",
                (datetime.now(timezone.utc).isoformat(), ts_id))
    con.commit()


# ── API ───────────────────────────────────────────────────────────────────────

def init_session():
    r = SESSION.get(PORTAL_URL, timeout=15)
    log.info(f"Portal: {r.status_code}  cookies: {list(SESSION.cookies.keys())}")


def discover_stations(province):
    # Exact working parameters confirmed from browser devtools
    r = SESSION.get(BASE_URL, params={
        "request":             "getTimeseriesValueLayer",
        "service":             "kisters",
        "type":                "queryServices",
        "datasource":          "0",
        "format":              "objson",
        "metadata":            "true",
        "crs":                 "localxy",
        "md_returnfields":     "station_no,stationparameter_no",
        "ca_sta_returnfields": "",
        "returnfields":        "ts_value,occ_timestamp,timestamp",
        "timeseriesgroup_id":  TIMESERIESGROUP_ID,
    }, timeout=30)
    r.raise_for_status()
    raw = r.json()
    log.info(f"Total DESO entries: {len(raw)}")

    # Enrich with full station details via getStationList
    log.info("Fetching full station list...")
    r2 = SESSION.get(BASE_URL, params={
        "request":      "getStationList",
        "service":      "kisters",
        "type":         "queryServices",
        "datasource":   "0",
        "format":       "objson",
        "returnfields": "station_no,station_name,site_name",
    }, timeout=30)
    stations = {s["station_no"]: s for s in r2.json()}

    # Build DataFrame merging layer + station details
    rows = []
    for entry in raw:
        sno    = entry.get("station_no", "")
        sta    = stations.get(sno, {})
        # Build ts_path from station_no + known pattern
        ts_path = f"DESO/{sno}/Prof/Cmd.Rel.Abs.Comp.SolWEB"
        rows.append({
            "station_no":       sno,
            "station_name":     sta.get("station_name", sno),
            "ts_path":          ts_path,
            "ts_id":            entry.get("ts_id", f"{sno}_Prof"),
            "ts_unitsymbol":    "m",
            "station_local_x":  entry.get("station_local_x"),
            "station_local_y":  entry.get("station_local_y"),
            "station_elevation": entry.get("station_elevation"),
            "PROVINCE":         entry.get("PROVINCE", ""),
            "MASESO_LIBELLE":   entry.get("MASESO_LIBELLE", ""),
            "MASESO_WEB":       entry.get("MASESO_WEB", ""),
            "COMMUNE_LOCALITE": entry.get("COMMUNE_LOCALITE", ""),
            "GWREF_DATUM":      entry.get("GWREF_DATUM", ""),
            "PROFPUITS":        entry.get("PROFPUITS", ""),
            "CODESO":           entry.get("CODESO", ""),
            "GR_EQUIPEMENT":    entry.get("GR_EQUIPEMENT", ""),
            "station_status":   entry.get("station_status", ""),
        })
    df = pd.DataFrame(rows)

    # Province filter — use PROVINCE from layer if available,
    # otherwise keep all (we'll fetch per station and check)
    if "PROVINCE" in df.columns and df["PROVINCE"].str.strip().any():
        df = df[df["PROVINCE"].str.upper() == province.upper()].copy()
        log.info(f"After PROVINCE={province}: {len(df)}")
    else:
        log.warning("No PROVINCE in layer response — keeping all stations")
    return df.reset_index(drop=True)


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


def fetch_obs(ts_path, returnfields, days):
    now   = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    r = SESSION.get(BASE_URL, params={
        "request": "getTimeseriesValues", "service": "kisters",
        "type": "queryServices", "datasource": "0", "format": "json",
        "ts_path": ts_path, "metadata": "true",
        "returnfields": returnfields,
        "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to":   now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, timeout=30)
    r.raise_for_status()
    block     = r.json()[0]
    data_rows = block.get("data", [])
    if not data_rows:
        return pd.DataFrame(), block
    df = pd.DataFrame({
        "timestamp":    [pd.Timestamp(row[0]) for row in data_rows],
        "value":        [_f(row[1]) for row in data_rows],
        "quality_code": [_i(row[2]) for row in data_rows],
    })
    if all(v is None for v in df["value"]):
        return pd.DataFrame(), block
    return df, block


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 55)
    log.info(f"SPW Piézométrie Ingestion — {PROVINCE}")
    log.info("=" * 55)

    init_session()
    stations_df = discover_stations(PROVINCE)

    con = init_db(DB_PATH)
    upsert_stations(con, stations_df)

    ok, skipped, failed = 0, 0, 0

    for i, row in stations_df.iterrows():
        station_no = str(row["station_no"])
        ts_path    = row["ts_path"]
        ts_id      = str(row.get("ts_id", f"{station_no}_Prof"))
        name       = row.get("station_name", station_no)

        log.info(f"[{i+1:03d}/{len(stations_df)}] {station_no:12s}  {name}")

        # Fetch absolute level (NGF) — returnfields=Absolute Value
        upsert_ts(con, station_no, "Prof_abs", ts_path,
                  row.get("ts_unitsymbol"), f"{ts_id}_abs")
        # Fetch depth to water table — returnfields=Value
        upsert_ts(con, station_no, "Prof_depth", ts_path,
                  row.get("ts_unitsymbol"), f"{ts_id}_dep")

        try:
            # Absolute level
            df_abs, _ = fetch_obs(ts_path,
                                  "Timestamp,Absolute Value,AV Quality Code",
                                  FETCH_DAYS)
            n_abs = insert_obs(con, f"{ts_id}_abs", station_no,
                               "Prof_abs", df_abs)
            if not df_abs.empty:
                mark_fetched(con, f"{ts_id}_abs")

            # Depth to water table
            df_dep, _ = fetch_obs(ts_path,
                                  "Timestamp,Value,Quality Code",
                                  FETCH_DAYS)
            n_dep = insert_obs(con, f"{ts_id}_dep", station_no,
                               "Prof_depth", df_dep)
            if not df_dep.empty:
                mark_fetched(con, f"{ts_id}_dep")

            if df_abs.empty and df_dep.empty:
                log.warning("  → no data"); skipped += 1
            else:
                log.info(f"  → abs={len(df_abs)} recs  depth={len(df_dep)} recs  "
                         f"inserted: {n_abs}+{n_dep}")
                ok += 1

        except requests.HTTPError as e:
            log.error(f"  → HTTP {e.response.status_code}"); failed += 1
        except Exception as e:
            log.error(f"  → {e}"); failed += 1

        time.sleep(PAUSE)

    log.info("=" * 55)
    log.info(f"Done. OK={ok}  skipped={skipped}  failed={failed}")
    cur = con.execute(
        "SELECT parameter, COUNT(*), COUNT(DISTINCT station_no) "
        "FROM observations GROUP BY parameter"
    )
    for param, n_obs, n_sta in cur.fetchall():
        log.info(f"  {param:<12}  {n_obs:>6,} obs  {n_sta:>3} stations")
    log.info(f"DB → {Path(DB_PATH).resolve()}")
    con.close()
