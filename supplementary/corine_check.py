import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_SPW, DB_PIEZ, DB_ERA5, DB_CORINE, DB_FORECAST

"""CORINE land cover DB check."""
import sqlite3
from pathlib import Path

DB_PATH = str(DB_CORINE)

if not Path(DB_PATH).exists():
    print(f"✗ {DB_PATH} not found")
    exit()

size = Path(DB_PATH).stat().st_size / 1024**2
con  = sqlite3.connect(DB_PATH)

n_polys   = con.execute("SELECT COUNT(*) FROM land_cover").fetchone()[0]
n_classes = con.execute("SELECT COUNT(DISTINCT clc_code) FROM land_cover").fetchone()[0]

print(f"{'='*60}")
print(f"CORINE DB — {DB_PATH}  ({size:.2f} MB)")
print(f"{'='*60}")
print(f"  Polygons : {n_polys:,}")
print(f"  Classes  : {n_classes}")

print(f"\n── By land cover class (sorted by area) {'─'*20}")
for row in con.execute("""
    SELECT c.code, c.level1, c.label,
           COUNT(l.id)           AS n_polys,
           ROUND(SUM(l.area_ha)) AS total_ha
    FROM land_cover l
    JOIN clc_classes c ON l.clc_code = c.code
    GROUP BY c.code
    ORDER BY total_ha DESC
"""):
    print(f"  {row[0]:3d}  {row[2]:<42}  {row[3]:>4} polys  {row[4]:>10,.0f} ha")

print(f"\n── Level-1 summary {'─'*40}")
for row in con.execute("""
    SELECT c.level1,
           COUNT(l.id)           AS n_polys,
           ROUND(SUM(l.area_ha)) AS total_ha
    FROM land_cover l
    JOIN clc_classes c ON l.clc_code = c.code
    GROUP BY c.level1
    ORDER BY total_ha DESC
"""):
    print(f"  {row[0]:<30}  {row[1]:>5} polys  {row[2]:>10,.0f} ha")

print(f"\n── Bbox coverage check {'─'*36}")
row = con.execute("""
    SELECT MIN(centroid_lon), MAX(centroid_lon),
           MIN(centroid_lat), MAX(centroid_lat)
    FROM land_cover
""").fetchone()
print(f"  Lon: {row[0]:.3f} → {row[1]:.3f}")
print(f"  Lat: {row[2]:.3f} → {row[3]:.3f}")

con.close()
print(f"{'='*60}")
