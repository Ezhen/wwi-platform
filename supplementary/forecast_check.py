import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_SPW, DB_PIEZ, DB_ERA5, DB_CORINE, DB_FORECAST

"""Open-Meteo forecast DB check."""
import sqlite3
from pathlib import Path

DB_PATH = str(DB_FORECAST)

if not Path(DB_PATH).exists():
    print(f"✗ {DB_PATH} not found")
    exit()

size = Path(DB_PATH).stat().st_size / 1024**2
con  = sqlite3.connect(DB_PATH)

n_rows   = con.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
n_points = con.execute("SELECT COUNT(*) FROM forecast_points").fetchone()[0]

print(f"{'='*60}")
print(f"Forecast DB — {DB_PATH}  ({size:.2f} MB)")
print(f"{'='*60}")
print(f"  Forecast points : {n_points}")
print(f"  Total rows      : {n_rows:,}")

print(f"\n── By variable ──────────────────────────────────────────")
for row in con.execute("""
    SELECT variable, COUNT(*),
           ROUND(MIN(value),3), ROUND(MAX(value),3),
           ROUND(AVG(value),3)
    FROM forecasts GROUP BY variable ORDER BY variable
"""):
    print(f"  {row[0]:<35} n={row[1]:>5}  "
          f"min={row[2]:>8}  max={row[3]:>8}  mean={row[4]:>8}")

print(f"\n── By point ─────────────────────────────────────────────")
for row in con.execute("""
    SELECT p.point_id, p.description,
           COUNT(f.id) AS n_rows,
           MIN(f.valid_time), MAX(f.valid_time)
    FROM forecast_points p
    LEFT JOIN forecasts f USING(point_id)
    GROUP BY p.point_id
    ORDER BY p.point_id
"""):
    print(f"  {row[0]:<22} {row[1]:<35} "
          f"n={row[2]:>5}  {str(row[3])[:13]} → {str(row[4])[:13]}")

print(f"\n── Next 24h precip — Liège city ─────────────────────────")
rows = con.execute("""
    SELECT valid_time, value
    FROM forecasts
    WHERE point_id='liege_city' AND variable='precipitation'
    ORDER BY valid_time LIMIT 24
""").fetchall()
for t, v in rows:
    bar = "█" * int(v * 10)
    print(f"  {t[:16]}  {v:5.2f} mm  {bar}")

print(f"\n── Next 24h precip — Hautes Fagnes ──────────────────────")
rows = con.execute("""
    SELECT valid_time, value
    FROM forecasts
    WHERE point_id='hautes_fagnes' AND variable='precipitation'
    ORDER BY valid_time LIMIT 24
""").fetchall()
for t, v in rows:
    bar = "█" * int(v * 10)
    print(f"  {t[:16]}  {v:5.2f} mm  {bar}")

print(f"{'='*60}")
con.close()
