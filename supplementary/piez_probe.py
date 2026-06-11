import requests, json

BASE_URL   = "https://piezometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://hydrometrie.wallonie.be/home/observations/debit.html"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": PORTAL_URL, "Origin": "https://hydrometrie.wallonie.be",
    "Accept": "application/json",
})
SESSION.get(PORTAL_URL, timeout=15)

# ── 1. Full station list — filter for DESO/piezometry sites ──────────────────
print("=== Full station list — DESO/groundwater stations ===")
r = SESSION.get(BASE_URL, params={
    "request": "getStationList", "service": "kisters",
    "type": "queryServices", "datasource": "0", "format": "objson",
    "returnfields": "station_no,station_name,site_name,station_local_x,station_local_y,ca_sta",
}, timeout=20)
data = r.json()
print(f"Total: {len(data)}")

# Find groundwater stations by site_name keywords
keywords = ["souterrain", "DESO", "piézo", "piez", "nappes", "groundwater", "eaux souterr"]
piez = [s for s in data if any(k.lower() in str(s.get("site_name","")).lower() for k in keywords)]
print(f"Groundwater-related stations: {len(piez)}")
for s in piez[:10]:
    print(f"  {s}")

# Also show all unique site_names
site_names = sorted(set(s.get("site_name","") for s in data))
print(f"\nAll unique site_names ({len(site_names)}):")
for sn in site_names:
    print(f"  {sn}")

# ── 2. Try getTimeseriesGroupList ─────────────────────────────────────────────
print("\n=== getTimeseriesGroupList ===")
r = SESSION.get(BASE_URL, params={
    "request": "getTimeseriesGroupList", "service": "kisters",
    "type": "queryServices", "datasource": "0", "format": "objson",
    "returnfields": "group_id,group_name,group_type",
}, timeout=20)
print(f"HTTP: {r.status_code}")
if r.status_code == 200:
    groups = r.json()
    print(f"Total groups: {len(groups)}")
    # Filter for groundwater/piezometry related
    for g in groups:
        name = str(g.get("group_name","")).lower()
        if any(k in name for k in ["piez","prof","groundwater","nappe","souterr","water level","depth"]):
            print(f"  *** {g}")
    print("\nAll groups:")
    for g in groups:
        print(f"  {g}")
else:
    print(f"Body: {r.text[:200]}")

# ── 3. Try station PZ4814 with getStationList to see its real station_no ──────
print("\n=== Search for PZ4814 in station list ===")
r = SESSION.get(BASE_URL, params={
    "request": "getStationList", "service": "kisters",
    "type": "queryServices", "datasource": "0", "format": "objson",
    "station_name": "*PZ4814*",
    "returnfields": "station_no,station_name,site_name",
}, timeout=20)
print(f"HTTP: {r.status_code}  body: {r.text[:300]}")
