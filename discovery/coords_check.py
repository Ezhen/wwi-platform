import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_SPW, DB_PIEZ, DB_ERA5, DB_CORINE, DB_FORECAST

"""Check coordinate coverage across all databases."""
import sqlite3
from pathlib import Path

print("=" * 65)
print("Coordinate coverage check")
print("=" * 65)

# ── SPW ───────────────────────────────────────────────────────────────────────
if DB_SPW.exists():
    con = sqlite3.connect(str(DB_SPW))
    print("\n── SPW stations ─────────────────────────────────────────────")
    row = con.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN local_x IS NOT NULL AND local_x != 0 THEN 1 ELSE 0 END),
               MIN(local_x), MAX(local_x),
               MIN(local_y), MAX(local_y)
        FROM stations
    """).fetchone()
    print(f"  Total stations    : {row[0]}")
    print(f"  With coordinates  : {row[1]}")
    print(f"  X range           : {row[2]} → {row[3]}")
    print(f"  Y range           : {row[4]} → {row[5]}")

    # Sample a few
    print("\n  Sample (station, name, x, y):")
    for r in con.execute("""
        SELECT station_no, station_name, river_name, local_x, local_y
        FROM stations
        WHERE local_x IS NOT NULL AND local_x != 0
        LIMIT 5
    """):
        print(f"    {r[0]:8s}  {r[1]:<28}  {r[2]:<18}  x={r[3]}  y={r[4]}")
    con.close()

# ── Piezometry ────────────────────────────────────────────────────────────────
if DB_PIEZ.exists():
    con = sqlite3.connect(str(DB_PIEZ))
    print("\n── Piezometry stations ──────────────────────────────────────")
    row = con.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN local_x IS NOT NULL AND local_x != 0 THEN 1 ELSE 0 END),
               MIN(local_x), MAX(local_x),
               MIN(local_y), MAX(local_y)
        FROM stations
    """).fetchone()
    print(f"  Total stations    : {row[0]}")
    print(f"  With coordinates  : {row[1]}")
    print(f"  X range           : {row[2]} → {row[3]}")
    print(f"  Y range           : {row[4]} → {row[5]}")

    print("\n  Sample:")
    for r in con.execute("""
        SELECT station_no, station_name, commune, local_x, local_y, elevation
        FROM stations
        WHERE local_x IS NOT NULL AND local_x != 0
        LIMIT 5
    """):
        print(f"    {r[0]:10s}  {r[1]:<30}  {r[2]:<15}  x={r[3]}  y={r[4]}  elev={r[5]}")
    con.close()

# ── ERA5 ──────────────────────────────────────────────────────────────────────
if DB_ERA5.exists():
    con = sqlite3.connect(str(DB_ERA5))
    print("\n── ERA5 grid points ─────────────────────────────────────────")
    row = con.execute("SELECT COUNT(*), MIN(lat), MAX(lat), MIN(lon), MAX(lon) FROM grid_points").fetchone()
    print(f"  Grid points : {row[0]}")
    print(f"  Lat range   : {row[1]} → {row[2]}")
    print(f"  Lon range   : {row[3]} → {row[4]}")
    con.close()

# ── Forecast ──────────────────────────────────────────────────────────────────
if DB_FORECAST.exists():
    con = sqlite3.connect(str(DB_FORECAST))
    print("\n── Forecast points ──────────────────────────────────────────")
    for r in con.execute("SELECT point_id, lat, lon, description FROM forecast_points"):
        print(f"  {r[0]:<22}  lat={r[1]}  lon={r[2]}  {r[3]}")
    con.close()

# ── CORINE ────────────────────────────────────────────────────────────────────
if Path(str(DB_CORINE)).exists():
    con = sqlite3.connect(str(DB_CORINE))
    print("\n── CORINE polygons ──────────────────────────────────────────")
    row = con.execute("""
        SELECT COUNT(*),
               MIN(centroid_lon), MAX(centroid_lon),
               MIN(centroid_lat), MAX(centroid_lat)
        FROM land_cover
        WHERE centroid_lon IS NOT NULL
    """).fetchone()
    print(f"  Polygons with centroid: {row[0]}")
    print(f"  Lon range: {row[1]:.3f} → {row[2]:.3f}")
    print(f"  Lat range: {row[3]:.3f} → {row[4]:.3f}")
    con.close()

print("\n" + "=" * 65)
print("CRS notes:")
print("  SPW/Piez: Belgian Lambert 72 (EPSG:31370) — needs conversion to WGS84")
print("  ERA5:     WGS84 (EPSG:4326) — ready")
print("  Forecast: WGS84 (EPSG:4326) — ready")
print("  CORINE:   WGS84 after reprojection — ready")
print("=" * 65)
