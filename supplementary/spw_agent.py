"""
SPW Q Diagnostic Agent
Systematically tries every known KiWIS parameter combination
until it finds one that returns non-null discharge values.
Reports exactly what worked.
"""

import requests
import math
import json
from datetime import datetime, timedelta, timezone
from itertools import product

BASE_URL   = "https://hydrometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://hydrometrie.wallonie.be/home/observations/hauteur.html"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":    PORTAL_URL,
    "Accept":     "application/json, text/plain, */*",
})
SESSION.get(PORTAL_URL, timeout=15)

now = datetime.now(timezone.utc)

def _f(x):
    if x is None: return None
    try:
        f = float(x); return None if math.isnan(f) else f
    except: return None

def probe(params: dict) -> dict:
    """Fire one request, return summary dict."""
    try:
        r = SESSION.get(BASE_URL, params=params, timeout=20)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        raw = r.json()
        if not raw or not isinstance(raw, list):
            return {"ok": False, "error": "unexpected response structure"}
        block = raw[0]
        rows  = block.get("data", [])
        if not rows:
            return {"ok": False, "error": "empty data array"}
        # Check first row structure
        first = rows[0]
        values = [_f(row[1]) for row in rows]
        non_null = [v for v in values if v is not None]
        return {
            "ok":       len(non_null) > 0,
            "rows":     len(rows),
            "non_null": len(non_null),
            "first_raw": first,
            "last_raw":  rows[-1],
            "unit":      block.get("ts_unitname", "?"),
            "station":   block.get("station_name", "?"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Dimension space to explore ────────────────────────────────────────────────

TS_PATHS = [
    "DGH/5826/Q/Cmd.RunOff.Comp-Alarmes",
    "DGH/5826/Q/Cmd.Rel.Abs.Comp-Alarmes",       # maybe same ts_name as H?
    "DGH/5826/Q/Cmd.RunOff.Abs.Comp-Alarmes",
    "DGH/5826/Q/Obs.RunOff.15",
    "DGH/5826/Q/Obs.Mean.15",
    "DGH/5826/Q/Cmd.Mean.15",
]

WINDOW_STRATEGIES = [
    {"period": "P1D"},
    {"period": "P7D"},
    {"period": "P1M"},
    {"from": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
     "to":   now.strftime("%Y-%m-%dT%H:%M:%SZ")},
    {"from": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
     "to":   now.strftime("%Y-%m-%dT%H:%M:%SZ")},
    # Try local time instead of UTC
    {"from": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+02:00"),
     "to":   now.strftime("%Y-%m-%dT%H:%M:%S+02:00")},
]

RETURNFIELDS_OPTIONS = [
    "Timestamp,Absolute Value,AV Quality Code",
    "Timestamp,Value,Quality Code",
    "Timestamp,Absolute Value",
]

BASE_PARAMS = {
    "request":    "getTimeseriesValues",
    "service":    "kisters",
    "type":       "queryServices",
    "datasource": "0",
    "format":     "json",
    "metadata":   "true",
}

# ── Phase 1: try all ts_path variants with P1D ───────────────────────────────
print("=" * 65)
print("PHASE 1 — ts_path variants")
print("=" * 65)

found = None

for ts_path in TS_PATHS:
    params = {**BASE_PARAMS,
              "ts_path":      ts_path,
              "returnfields": "Timestamp,Absolute Value,AV Quality Code",
              "period":       "P1D"}
    result = probe(params)
    status = "✓ DATA" if result["ok"] else f"✗ {result.get('error', 'no data')} rows={result.get('rows',0)}"
    print(f"  {ts_path:<50}  {status}")
    if result["ok"] and not found:
        found = {"ts_path": ts_path, "params": params, "result": result}

# ── Phase 2: window strategy sweep on confirmed ts_path ──────────────────────
print("\n" + "=" * 65)
print("PHASE 2 — window strategies on DGH/5826/Q/Cmd.RunOff.Comp-Alarmes")
print("=" * 65)

for ws in WINDOW_STRATEGIES:
    params = {**BASE_PARAMS,
              "ts_path":      "DGH/5826/Q/Cmd.RunOff.Comp-Alarmes",
              "returnfields": "Timestamp,Absolute Value,AV Quality Code",
              **ws}
    result = probe(params)
    label  = str(ws)[:55]
    status = f"✓ rows={result['rows']} non_null={result['non_null']}" \
             if result["ok"] else f"✗ {result.get('error','no data')} rows={result.get('rows',0)}"
    print(f"  {label:<57}  {status}")
    if result["ok"] and not found:
        found = {"window": ws, "params": params, "result": result}

# ── Phase 3: returnfields variants ───────────────────────────────────────────
print("\n" + "=" * 65)
print("PHASE 3 — returnfields variants")
print("=" * 65)

for rf in RETURNFIELDS_OPTIONS:
    params = {**BASE_PARAMS,
              "ts_path":      "DGH/5826/Q/Cmd.RunOff.Comp-Alarmes",
              "returnfields": rf,
              "period":       "P7D"}
    result = probe(params)
    status = f"✓ first_raw={result.get('first_raw')}" \
             if result["ok"] else f"✗ rows={result.get('rows',0)} first_raw={result.get('first_raw')}"
    print(f"  {rf:<50}  {status}")

# ── Phase 4: getTimeseriesList to see ALL available Q ts for 5826 ────────────
print("\n" + "=" * 65)
print("PHASE 4 — getTimeseriesList for station 5826")
print("=" * 65)

r = SESSION.get(BASE_URL, params={
    "request":      "getTimeseriesList",
    "service":      "kisters",
    "type":         "queryServices",
    "datasource":   "0",
    "format":       "objson",
    "station_no":   "5826",
    "returnfields": "ts_path,ts_name,ts_shortname,parametertype_name,ts_unitsymbol,ts_type_name",
}, timeout=20)
ts_list = r.json()
print(f"  Total timeseries for 5826: {len(ts_list)}")
q_ts = [t for t in ts_list if "/Q/" in t.get("ts_path", "")]
print(f"  Q timeseries:")
for t in q_ts:
    print(f"    {t.get('ts_path',''):<55}  type={t.get('ts_type_name','?')}")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
if found:
    print("✓ WORKING COMBINATION FOUND:")
    print(json.dumps(found["params"], indent=2))
    print(f"\nSample: {found['result'].get('first_raw')}")
    print(f"Station: {found['result'].get('station')}  Unit: {found['result'].get('unit')}")
else:
    print("✗ No combination returned non-null Q values.")
    print("  Discharge may not be available via this API for these stations.")
    print("  Check getTimeseriesList output above for actual available ts_paths.")
print("=" * 65)
