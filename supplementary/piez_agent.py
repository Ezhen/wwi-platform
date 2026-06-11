"""
Piezometrie Discovery Agent
Systematically finds the correct md_returnfields combination
for getTimeseriesValueLayer on piezometrie.wallonie.be
"""

import requests
import json

BASE_URL   = "https://piezometrie.wallonie.be/services/KiWIS/KiWIS"
PORTAL_URL = "https://piezometrie.wallonie.be/home/observations/niveau-deau-souterraine.html"
GROUP_ID   = "1962272"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer":         PORTAL_URL,
    "Origin":          "https://piezometrie.wallonie.be",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "fr-BE,fr;q=0.9,en;q=0.8",
})
r0 = SESSION.get(PORTAL_URL, timeout=15)
print(f"Portal: {r0.status_code}  cookies: {list(SESSION.cookies.keys())}\n")

BASE_PARAMS = {
    "request":             "getTimeseriesValueLayer",
    "service":             "kisters",
    "type":                "queryServices",
    "datasource":          "0",
    "format":              "objson",
    "metadata":            "true",
    "crs":                 "localxy",
    "ca_sta_returnfields": "",
    "timeseriesgroup_id":  GROUP_ID,
}

def probe(md_returnfields, ca_sta_returnfields=""):
    params = {**BASE_PARAMS,
              "md_returnfields":     md_returnfields,
              "ca_sta_returnfields": ca_sta_returnfields}
    r = SESSION.get(BASE_URL, params=params, timeout=30)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:120]}"
    try:
        data = r.json()
        return data, None
    except Exception as e:
        return None, f"parse error: {e}"

found_md = None

# ── Phase 1: minimal safe md_returnfields ────────────────────────────────────
print("=" * 65)
print("PHASE 1 — minimal md_returnfields (known safe fields)")
print("=" * 65)

minimal = "station_no,station_name,ts_path,ts_id,ts_unitsymbol,station_local_x,station_local_y,station_elevation"
data, err = probe(minimal)
if err:
    print(f"  ✗ minimal: {err}")
else:
    print(f"  ✓ minimal: {len(data)} entries")
    print(f"  Columns: {list(data[0].keys()) if data else 'empty'}")
    found_md = minimal

# ── Phase 2: add fields one by one ───────────────────────────────────────────
print("\n" + "=" * 65)
print("PHASE 2 — add optional fields one by one")
print("=" * 65)

optional_fields = [
    "stationparameter_name",
    "stationparameter_no",
    "ts_name",
    "ts_shortname",
    "site_no",
    "site_name",
    "parametertype_name",
    "object_type_shortname",
    "ts_unitsymbol",
]

working_fields = minimal
for field in optional_fields:
    test = working_fields + "," + field
    data, err = probe(test)
    if err:
        print(f"  ✗ +{field:<30} {err[:80]}")
    else:
        print(f"  ✓ +{field:<30} {len(data)} entries")
        working_fields = test

print(f"\nFinal working md_returnfields:\n  {working_fields}")
found_md = working_fields

# ── Phase 3: ca_sta_returnfields ─────────────────────────────────────────────
print("\n" + "=" * 65)
print("PHASE 3 — ca_sta_returnfields variants")
print("=" * 65)

ca_candidates = [
    "PROVINCE",
    "PROVINCE,MASESO_LIBELLE",
    "PROVINCE,MASESO_LIBELLE,MASESO_WEB",
    "PROVINCE,MASESO_LIBELLE,MASESO_WEB,COMMUNE_LOCALITE",
    "PROVINCE,MASESO_LIBELLE,MASESO_WEB,COMMUNE_LOCALITE,GWREF_DATUM,PROFPUITS,CODESO",
    "PROVINCE,MASESO_LIBELLE,MASESO_WEB,COMMUNE_LOCALITE,GWREF_DATUM,PROFPUITS,CODESO,GR_EQUIPEMENT",
]

working_ca = ""
for ca in ca_candidates:
    data, err = probe(found_md, ca)
    if err:
        print(f"  ✗ {ca[:70]}")
        print(f"    {err[:100]}")
        break  # stop at first failure — fields are cumulative
    else:
        sample_keys = list(data[0].keys()) if data else []
        new_keys = [k for k in sample_keys if k not in found_md.split(",") + ["timestamp","req_timestamp","ts_value","station_local_x","station_local_y"]]
        print(f"  ✓ {ca[:70]}")
        print(f"    new keys in response: {new_keys[:8]}")
        working_ca = ca

print(f"\nFinal working ca_sta_returnfields:\n  {working_ca}")

# ── Phase 4: filter by PROVINCE ───────────────────────────────────────────────
print("\n" + "=" * 65)
print("PHASE 4 — PROVINCE filter check")
print("=" * 65)

if working_ca and "PROVINCE" in working_ca:
    data, err = probe(found_md, working_ca)
    if data:
        import pandas as pd
        df = pd.DataFrame(data)
        if "PROVINCE" in df.columns:
            print(f"  Province distribution:\n{df['PROVINCE'].value_counts().to_string()}")
            liege = df[df["PROVINCE"].str.upper() == "LIEGE"]
            print(f"\n  LIEGE stations: {len(liege)}")
            if not liege.empty:
                print(f"  Sample ts_paths:")
                for _, r in liege.head(5).iterrows():
                    print(f"    {r.get('ts_path','')}  {r.get('MASESO_LIBELLE','')[:40]}")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("WORKING CONFIGURATION:")
print(f"  md_returnfields:     {found_md}")
print(f"  ca_sta_returnfields: {working_ca}")
print("=" * 65)

# Save config
config = {"md_returnfields": found_md, "ca_sta_returnfields": working_ca}
with open("piez_config.json", "w") as f:
    json.dump(config, f, indent=2)
print("Saved → piez_config.json")
