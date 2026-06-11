import requests, math
from datetime import datetime, timedelta, timezone

BASE_URL   = "https://hydrometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://hydrometrie.wallonie.be/home/observations/hauteur.html"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": PORTAL_URL, "Accept": "application/json",
})
SESSION.get(PORTAL_URL, timeout=15)

TS_PATH = "DGH/5826/Q/Cmd.RunOff.Comp-Alarmes"

def _f(x):
    if x is None: return None
    try:
        f = float(x); return None if math.isnan(f) else f
    except: return None

# Try several window strategies
strategies = [
    ("No date params — portal default",  {}),
    ("period=P1D (last 24h)",            {"period": "P1D"}),
    ("period=P7D (last 7 days)",         {"period": "P7D"}),
    ("returnfields without QC",          {"period": "P1D",
                                          "returnfields": "Timestamp,Value"}),
]

for label, extra in strategies:
    params = {
        "request": "getTimeseriesValues", "service": "kisters",
        "type": "queryServices", "datasource": "0", "format": "json",
        "ts_path": TS_PATH, "metadata": "true",
        "returnfields": "Timestamp,Absolute Value,AV Quality Code",
        **extra
    }
    r = SESSION.get(BASE_URL, params=params, timeout=20)
    block = r.json()[0]
    rows  = block.get("data", [])
    non_null = [_f(row[1]) for row in rows if _f(row[1]) is not None]
    print(f"\n[{label}]")
    print(f"  rows={len(rows)}  non-null={len(non_null)}")
    if rows:
        print(f"  first raw row: {rows[0]}")
        print(f"  last  raw row: {rows[-1]}")
