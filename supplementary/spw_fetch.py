"""
SPW Hydrométrie - KiWIS timeseries values fetcher
Station: DGH/5811 (Ourthe area)
Parameter: H (water level)
"""

import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

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

# Shared session — seeds cookies from the portal before KiWIS calls
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def init_session():
    """Visit the portal page to obtain session cookies."""
    print("  Seeding session from portal...")
    r = SESSION.get(PORTAL_URL, timeout=15)
    print(f"  Portal status: {r.status_code}  |  cookies: {list(SESSION.cookies.keys())}")


def fetch_timeseries_values(
    ts_path: str,
    last_n_days: int = 7,
    period_start: datetime = None,
    period_end: datetime = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Fetch timeseries values from SPW KiWIS API.

    Args:
        ts_path:      Full KiWIS ts_path e.g. 'DGH/5811/H/Cmd.Rel.Abs.Comp-Alarmes'
        last_n_days:  Window size if no explicit period given.
        period_start: Start datetime (UTC).
        period_end:   End datetime (UTC).

    Returns:
        (DataFrame[timestamp, value, quality_code], metadata dict)
    """
    now = datetime.now(timezone.utc)
    if period_end is None:
        period_end = now
    if period_start is None:
        period_start = period_end - timedelta(days=last_n_days)

    params = {
        "request":      "getTimeseriesValues",
        "service":      "kisters",
        "type":         "queryServices",
        "datasource":   "0",
        "format":       "json",
        "ts_path":      ts_path,
        "metadata":     "true",
        "returnfields": "Timestamp,Absolute Value,AV Quality Code",
        "from":         period_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to":           period_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    print(f"\n  Fetching : {ts_path}")
    print(f"  Window   : {params['from']} → {params['to']}")

    r = SESSION.get(BASE_URL, params=params, timeout=30)
    print(f"  HTTP     : {r.status_code}")
    r.raise_for_status()

    raw = r.json()

    if not raw or not isinstance(raw, list):
        raise ValueError(f"Unexpected response: {type(raw)}")

    block = raw[0]
    print(f"  Station  : {block.get('station_name', 'N/A')} (no={block.get('station_no', 'N/A')})")
    print(f"  TS name  : {block.get('ts_name', 'N/A')}")
    print(f"  Unit     : {block.get('ts_unitname', 'N/A')}")

    data_rows = block.get("data", [])
    if not data_rows:
        print("  WARNING: no data rows in this window.")
        return pd.DataFrame(columns=["timestamp", "value", "quality_code"]), block

    df = pd.DataFrame(data_rows, columns=["timestamp", "value", "quality_code"])
    df["timestamp"]    = pd.to_datetime(df["timestamp"])
    df["value"]        = pd.to_numeric(df["value"], errors="coerce")
    df["quality_code"] = pd.to_numeric(df["quality_code"], errors="coerce")

    print(f"  Records  : {len(df)}")
    print(f"  Latest   : {df['timestamp'].max()}  →  {df['value'].iloc[-1]} {block.get('ts_unitname','')}")

    return df, block


if __name__ == "__main__":

    TS_PATH = "DGH/5806/H/Cmd.Rel.Abs.Comp-Alarmes"

    print("=" * 60)
    print("SPW KiWIS — Water Level Ingestion")
    print("=" * 60)

    init_session()

    try:
        df, meta = fetch_timeseries_values(ts_path=TS_PATH, last_n_days=7)

        if not df.empty:
            print("\n--- Last 5 records ---")
            print(df.tail(5).to_string(index=False))

            out = "spw_5806_H_7d.csv"  # saves to current working directory
            df.to_csv(out, index=False)
            print(f"\nSaved → {out}")

            print("\n--- Stats ---")
            print(f"  Min  : {df['value'].min():.3f} m")
            print(f"  Max  : {df['value'].max():.3f} m")
            print(f"  Mean : {df['value'].mean():.3f} m")
            print(f"  NaN  : {df['value'].isna().sum()} records")

    except requests.HTTPError as e:
        print(f"\nHTTP error: {e}")
        print("Response body:", e.response.text[:500])
    except Exception as e:
        print(f"\nError: {e}")
        raise
