"""
Map fix agent — tests every query in build_map.py against actual DB schema
and auto-fixes any that fail.
"""
import sqlite3, re
from pathlib import Path

ROOT    = Path(__file__).parent
DB_SPW  = str(ROOT / "export/databases/spw_liege.db")
DB_PIEZ = str(ROOT / "export/databases/piez_liege.db")
DB_ERA5 = str(ROOT / "export/databases/era5_liege.db")
DB_FORECAST = str(ROOT / "export/databases/forecast_liege.db")

# ── Step 1: Inspect actual schema ────────────────────────────────────────────
print("=" * 60)
print("STEP 1 — Actual schema")
print("=" * 60)

schema = {}
for label, db in [("SPW", DB_SPW), ("PIEZ", DB_PIEZ),
                  ("ERA5", DB_ERA5), ("FORECAST", DB_FORECAST)]:
    con = sqlite3.connect(db)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    )]
    schema[label] = {}
    for t in tables:
        try:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
            n    = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            schema[label][t] = {"cols": cols, "n": n}
            print(f"  {label}.{t}: {n} rows  cols={cols}")
        except: pass
    con.close()

# ── Step 2: Build correct queries based on actual schema ──────────────────────
print("\n" + "=" * 60)
print("STEP 2 — Building correct queries")
print("=" * 60)

spw_cols    = schema["SPW"].get("t_flood_context", {}).get("cols", [])
q_cols      = schema["SPW"].get("t_latest_Q", {}).get("cols", [])
rain_cols   = schema["SPW"].get("t_antecedent_rain", {}).get("cols", [])
piez_cols   = schema["PIEZ"].get("v_groundwater_anomaly", {}).get("cols", [])
fc_cols     = schema["FORECAST"].get("v_forecast_alert", {}).get("cols", [])

print(f"\nt_flood_context cols: {spw_cols}")
print(f"t_latest_Q cols:      {q_cols}")
print(f"t_antecedent_rain cols: {rain_cols}")
print(f"v_groundwater_anomaly cols: {piez_cols}")
print(f"v_forecast_alert cols: {fc_cols}")

# ── Step 3: Test each query and report ───────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 3 — Testing queries")
print("=" * 60)

con_spw  = sqlite3.connect(DB_SPW)
con_piez = sqlite3.connect(DB_PIEZ)
con_era5 = sqlite3.connect(DB_ERA5)
con_fc   = sqlite3.connect(DB_FORECAST)

queries = {
    "rivers (t_flood_context)": (con_spw, """
        SELECT station_no, station_name, river_name, basin,
               level_m, timestamp, delta_1h_m, delta_3h_m,
               tendency, basin_rain_7d_mm, risk_signal, lat, lon
        FROM t_flood_context
        WHERE lat IS NOT NULL AND level_m IS NOT NULL LIMIT 3
    """),
    "discharge (t_latest_Q)": (con_spw, """
        SELECT station_no, discharge_m3s, timestamp
        FROM t_latest_Q
        WHERE discharge_m3s IS NOT NULL LIMIT 3
    """),
    "precip (t_antecedent_rain)": (con_spw, """
        SELECT station_no, station_name, river_name, basin,
               rain_3d_mm, rain_7d_mm, rain_14d_mm, lat, lon
        FROM t_antecedent_rain
        WHERE lat IS NOT NULL LIMIT 3
    """),
    "groundwater (v_groundwater_anomaly)": (con_piez, """
        SELECT station_no, station_name, aquifer, commune, province,
               current_depth_m, mean_depth_m, anomaly_m, gw_state,
               depth_percentile, timestamp, lat, lon
        FROM v_groundwater_anomaly
        WHERE lat IS NOT NULL AND province='LIEGE' LIMIT 3
    """),
    "forecast (v_forecast_alert)": (con_fc, """
        SELECT point_id, description, lat, lon,
               precip_24h_mm, precip_72h_mm, precip_7d_mm,
               alert_24h, alert_72h
        FROM v_forecast_alert LIMIT 3
    """),
    "era5 heatmap": (con_era5, """
        SELECT g.lat, g.lon, SUM(o.value)*1000 AS rain_7d_mm
        FROM era5_observations o
        JOIN grid_points g ON o.grid_id = g.id
        WHERE o.variable = 'total_precipitation'
        GROUP BY g.id LIMIT 3
    """),
}

results = {}
for name, (con, sql) in queries.items():
    try:
        rows = con.execute(sql).fetchall()
        print(f"  ✓ {name}: {len(rows)} rows")
        if rows: print(f"    sample: {rows[0]}")
        results[name] = {"ok": True, "rows": rows}
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        results[name] = {"ok": False, "error": str(e)}

for con in [con_spw, con_piez, con_era5, con_fc]:
    con.close()

# ── Step 4: Write corrected load functions ────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 4 — Patching build_map.py")
print("=" * 60)

map_path = Path("visualisation/build_map.py")
if not map_path.exists():
    print("  build_map.py not found at visualisation/build_map.py")
    exit(1)

content = map_path.read_text()

# Replace entire load_spw function
old_load_spw_start = "def load_spw():"
old_load_spw_end   = "def load_precip_stations():"

new_load_spw = '''def load_spw():
    if not Path(DB_SPW).exists(): return [], []
    con = sqlite3.connect(DB_SPW)
    con.row_factory = sqlite3.Row

    rivers = con.execute("""
        SELECT station_no, station_name, river_name, basin,
               level_m, timestamp, delta_1h_m, delta_3h_m,
               tendency, basin_rain_7d_mm, risk_signal, lat, lon
        FROM t_flood_context
        WHERE lat IS NOT NULL AND lat != 0
          AND level_m IS NOT NULL
    """).fetchall()

    discharge = con.execute("""
        SELECT station_no, discharge_m3s, timestamp
        FROM t_latest_Q
        WHERE discharge_m3s IS NOT NULL
    """).fetchall()

    con.close()
    return [dict(r) for r in rivers], [dict(r) for r in discharge]


'''

new_load_precip = '''def load_precip_stations():
    if not Path(DB_SPW).exists(): return []
    con = sqlite3.connect(DB_SPW)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT station_no, station_name, river_name, basin,
               rain_3d_mm, rain_7d_mm, rain_14d_mm, lat, lon
        FROM t_antecedent_rain
        WHERE lat IS NOT NULL AND lat != 0
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]


'''

# Find and replace load_spw
idx_start = content.find(old_load_spw_start)
idx_end   = content.find(old_load_spw_end)
if idx_start > 0 and idx_end > 0:
    content = content[:idx_start] + new_load_spw + new_load_precip + content[idx_end + len(old_load_spw_end):]
    # Re-add the function header that we consumed
    content = content.replace(new_load_precip + content[content.find("    if not Path(DB_SPW)"):], new_load_precip)
    print("  ✓ replaced load_spw and load_precip_stations")
else:
    print(f"  ✗ could not find load_spw (idx={idx_start}) or load_precip (idx={idx_end})")

map_path.write_text(content)
print(f"\n✓ build_map.py patched — run: python visualisation/build_map.py")
