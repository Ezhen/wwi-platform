"""
SPW KiWIS — Station discovery with Province filter
Fetches full station catalogue and filters to target province.
"""

import requests
import pandas as pd

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

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# --- Config ---
PROVINCE            = "LIEGE"          # change to NAMUR, HAINAUT, LUXEMBOURG, etc.
TIMESERIESGROUP_ID  = "1962373"        # H (water level) group
OUTPUT_FILE         = "spw_stations_liege.csv"

KEEP_COLS = [
    "station_no", "station_name", "site_name",
    "stationparameter_name", "ts_path", "ts_unitsymbol",
    "river_name", "BASSIN_INFOCRUE", "BASSIN_GESTION_TEXT",
    "station_local_x", "station_local_y", "station_elevation",
    "station_status", "CATCHMENT_SIZE", "ts_id",
]


def init_session():
    r = SESSION.get(PORTAL_URL, timeout=15)
    print(f"  Portal: {r.status_code}  |  cookies: {list(SESSION.cookies.keys())}")


def discover_stations(province: str = PROVINCE) -> pd.DataFrame:
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
        "timeseriesgroup_id":  TIMESERIESGROUP_ID,
    }

    print(f"\n  Querying station catalogue...")
    r = SESSION.get(BASE_URL, params=params, timeout=30)
    print(f"  HTTP: {r.status_code}")
    r.raise_for_status()

    raw = r.json()
    df = pd.DataFrame(raw) if isinstance(raw, list) else pd.DataFrame(raw["data"])
    print(f"  Total stations returned: {len(df)}")

    # --- Province filter ---
    if "PROVINCE" not in df.columns:
        print("  WARNING: PROVINCE column not found — returning full catalogue")
        return df

    df_filtered = df[df["PROVINCE"].str.upper() == province.upper()].copy()
    print(f"  After filter PROVINCE={province}: {len(df_filtered)} stations")

    # --- Keep relevant columns only ---
    keep = [c for c in KEEP_COLS if c in df_filtered.columns]
    df_filtered = df_filtered[keep].reset_index(drop=True)

    return df_filtered


if __name__ == "__main__":
    print("=" * 60)
    print(f"SPW KiWIS — Station Discovery ({PROVINCE})")
    print("=" * 60)

    init_session()
    df = discover_stations(PROVINCE)

    if df.empty:
        print("No stations found.")
    else:
        print(f"\n--- Basin breakdown ---")
        if "BASSIN_INFOCRUE" in df.columns:
            print(df["BASSIN_INFOCRUE"].value_counts().to_string())

        print(f"\n--- River breakdown ---")
        if "river_name" in df.columns:
            print(df["river_name"].value_counts().to_string())

        print(f"\n--- All ts_paths ---")
        cols = [c for c in ["station_no","station_name","river_name","ts_path"] if c in df.columns]
        print(df[cols].to_string(index=False))

        df.to_csv(OUTPUT_FILE, index=False)
        print(f"\nSaved {len(df)} stations → {OUTPUT_FILE}")
