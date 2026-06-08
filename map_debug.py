"""Quick debug — check what data build_map.py can actually read."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent

DB_SPW      = ROOT / "export" / "databases" / "spw_liege.db"
DB_PIEZ     = ROOT / "export" / "databases" / "piez_liege.db"
DB_ERA5     = ROOT / "export" / "databases" / "era5_liege.db"
DB_FORECAST = ROOT / "export" / "databases" / "forecast_liege.db"

print("=== DB existence ===")
for name, path in [("SPW", DB_SPW), ("PIEZ", DB_PIEZ),
                   ("ERA5", DB_ERA5), ("FORECAST", DB_FORECAST)]:
    print(f"  {name}: {path.exists()} — {path}")

print("\n=== SPW tables ===")
con = sqlite3.connect(str(DB_SPW))
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
)]
print(f"  Tables/views: {tables}")
for t in ["t_flood_context","t_latest_H","t_latest_Q","t_antecedent_rain"]:
    if t in tables:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        # Check lat/lon
        try:
            n_coords = con.execute(
                f"SELECT COUNT(*) FROM {t} WHERE lat IS NOT NULL"
            ).fetchone()[0]
            print(f"  {t}: {n} rows, {n_coords} with lat")
        except:
            print(f"  {t}: {n} rows")
    else:
        print(f"  {t}: MISSING")

print("\n=== Sample t_flood_context ===")
for r in con.execute("SELECT station_name, river_name, level_m, tendency, lat, lon FROM t_flood_context LIMIT 5"):
    print(f"  {r}")
con.close()

print("\n=== PIEZ tables ===")
con = sqlite3.connect(str(DB_PIEZ))
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
)]
print(f"  Tables/views: {tables}")
for t in ["t_groundwater_anomaly","t_latest_groundwater"]:
    if t in tables:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        try:
            n_coords = con.execute(
                f"SELECT COUNT(*) FROM {t} WHERE lat IS NOT NULL"
            ).fetchone()[0]
            print(f"  {t}: {n} rows, {n_coords} with lat")
        except:
            print(f"  {t}: {n} rows")
    else:
        print(f"  {t}: MISSING")
con.close()

print("\n=== FORECAST tables ===")
con = sqlite3.connect(str(DB_FORECAST))
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
)]
print(f"  Tables/views: {tables}")
for r in con.execute("SELECT point_id, lat, lon FROM forecast_points"):
    print(f"  {r}")
con.close()
