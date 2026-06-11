import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_SPW, DB_PIEZ, DB_ERA5, DB_CORINE, DB_FORECAST

"""
SPW — Database summary report
Reads spw_liege.db and prints structured stats.
"""

import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = str(DB_SPW)


def report(db_path: str):
    if not Path(db_path).exists():
        print(f"ERROR: {db_path} not found.")
        return

    con = sqlite3.connect(db_path)

    # ── Overview ─────────────────────────────────────────────────────────────
    n_stations = con.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
    n_ts       = con.execute("SELECT COUNT(*) FROM timeseries").fetchone()[0]
    n_obs      = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    db_size    = Path(db_path).stat().st_size / 1024**2

    print("=" * 60)
    print(f"SPW Liège DB Report — {db_path}  ({db_size:.1f} MB)")
    print("=" * 60)
    print(f"  Stations    : {n_stations}")
    print(f"  Timeseries  : {n_ts}")
    print(f"  Observations: {n_obs:,}")

    # ── By parameter ─────────────────────────────────────────────────────────
    print("\n── Observations by parameter ──────────────────────────")
    df = pd.read_sql("""
        SELECT parameter,
               COUNT(*)                                  AS n_obs,
               COUNT(DISTINCT station_no)                AS n_stations,
               MIN(timestamp)                            AS earliest,
               MAX(timestamp)                            AS latest,
               ROUND(AVG(CASE WHEN value IS NULL THEN 1.0 ELSE 0.0 END)*100, 1) AS pct_null
        FROM observations
        GROUP BY parameter
        ORDER BY parameter
    """, con)
    print(df.to_string(index=False))

    # ── By basin ─────────────────────────────────────────────────────────────
    print("\n── Stations by basin ──────────────────────────────────")
    df = pd.read_sql("""
        SELECT s.basin,
               COUNT(DISTINCT s.station_no)  AS n_stations,
               GROUP_CONCAT(DISTINCT o.parameter) AS parameters
        FROM stations s
        LEFT JOIN observations o USING(station_no)
        GROUP BY s.basin
        ORDER BY n_stations DESC
    """, con)
    print(df.to_string(index=False))

    # ── Station detail ───────────────────────────────────────────────────────
    print("\n── Per-station summary ────────────────────────────────")
    df = pd.read_sql("""
        SELECT s.station_no,
               s.station_name,
               s.river_name,
               o.parameter,
               COUNT(*)          AS n_obs,
               MIN(o.timestamp)  AS from_ts,
               MAX(o.timestamp)  AS to_ts,
               ROUND(AVG(o.value), 3) AS mean_val,
               t.ts_unit         AS unit
        FROM observations o
        JOIN stations s USING(station_no)
        JOIN timeseries t USING(ts_id)
        GROUP BY s.station_no, o.parameter
        ORDER BY s.river_name, s.station_name, o.parameter
    """, con)
    print(df.to_string(index=False))

    # ── Data gaps ────────────────────────────────────────────────────────────
    print("\n── Stations with NO observations ──────────────────────")
    df = pd.read_sql("""
        SELECT s.station_no, s.station_name, s.river_name, s.basin,
               t.parameter, t.ts_path
        FROM timeseries t
        JOIN stations s USING(station_no)
        WHERE t.last_fetched IS NULL
           OR NOT EXISTS (
               SELECT 1 FROM observations o
               WHERE o.ts_id = t.ts_id
           )
        ORDER BY t.parameter, s.river_name
    """, con)
    if df.empty:
        print("  None — all timeseries have data.")
    else:
        print(f"  {len(df)} timeseries with no data:")
        print(df.to_string(index=False))

    # ── Quality code breakdown ────────────────────────────────────────────────
    print("\n── Quality code distribution ──────────────────────────")
    df = pd.read_sql("""
        SELECT parameter, quality_code, COUNT(*) AS n
        FROM observations
        GROUP BY parameter, quality_code
        ORDER BY parameter, quality_code
    """, con)
    print(df.to_string(index=False))

    con.close()
    print("\n" + "=" * 60)


if __name__ == "__main__":
    report(DB_PATH)
