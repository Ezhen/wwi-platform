from pathlib import Path
ROOT = Path(__file__).parent.parent
DB_FORECAST = str(ROOT / "export/databases/forecast_liege.db")
"""
Open-Meteo Forecast Ingestion
Fetches 7-day hourly forecast for key Liège basin points.
No API key required.

Variables:
  - precipitation          (mm/h)
  - temperature_2m         (°C)
  - soil_moisture_0_to_1cm (m³/m³)
  - wind_speed_10m         (km/h)
  - precipitation_probability (%)

Stores in forecast_liege.db
"""

import requests
import sqlite3
import logging
from datetime import datetime, timezone

DB_PATH = str(DB_FORECAST)

# Representative points across the Liège basin
# Covers Ourthe, Vesdre, Amblève, Meuse, High Fens
FORECAST_POINTS = [
    ("liege_city",     50.633,  5.567, "Liège city centre"),
    ("ourthe_sauheid", 50.590,  5.530, "Ourthe at Sauheid"),
    ("vesdre_eupen",   50.632,  6.032, "Vesdre headwater — Eupen"),
    ("ambleve_stavelot",50.392, 5.932, "Amblève at Stavelot"),
    ("hautes_fagnes",  50.497,  6.097, "Hautes Fagnes — Baraque Michel"),
    ("meuse_liege",    50.651,  5.573, "Meuse at Liège"),
    ("ourthe_comblain",50.472,  5.578, "Ourthe at Comblain"),
]

HOURLY_VARS = [
    "precipitation",
    "temperature_2m",
    "soil_moisture_0_to_1cm",
    "wind_speed_10m",
    "precipitation_probability",
    "weather_code",
]

BASE_URL = "https://api.open-meteo.com/v1/forecast"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Database ──────────────────────────────────────────────────────────────────

def init_db(db_path):
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS forecast_points (
            point_id    TEXT PRIMARY KEY,
            lat         REAL,
            lon         REAL,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS forecasts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            point_id     TEXT    NOT NULL REFERENCES forecast_points(point_id),
            fetched_at   TEXT    NOT NULL,   -- when we pulled this forecast
            valid_time   TEXT    NOT NULL,   -- what hour this forecast is for
            variable     TEXT    NOT NULL,
            value        REAL,
            UNIQUE(point_id, fetched_at, valid_time, variable)
        );

        CREATE INDEX IF NOT EXISTS idx_fc_point   ON forecasts(point_id);
        CREATE INDEX IF NOT EXISTS idx_fc_valid   ON forecasts(valid_time);
        CREATE INDEX IF NOT EXISTS idx_fc_var     ON forecasts(variable);
        CREATE INDEX IF NOT EXISTS idx_fc_fetched ON forecasts(fetched_at);
    """)
    # Register forecast points
    con.executemany("""
        INSERT OR IGNORE INTO forecast_points (point_id, lat, lon, description)
        VALUES (?,?,?,?)
    """, FORECAST_POINTS)
    con.commit()
    return con


# ── API ───────────────────────────────────────────────────────────────────────

def fetch_forecast(point_id, lat, lon):
    params = {
        "latitude":       lat,
        "longitude":      lon,
        "hourly":         ",".join(HOURLY_VARS),
        "timezone":       "Europe/Brussels",
        "forecast_days":  7,
        "wind_speed_unit":"kmh",
    }
    r = requests.get(BASE_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def parse_and_store(con, point_id, data, fetched_at):
    hourly     = data["hourly"]
    times      = hourly["time"]          # list of "2026-06-05T00:00"
    n_inserted = 0

    rows = []
    for i, t in enumerate(times):
        valid_time = t + ":00+02:00"     # make timezone-aware
        for var in HOURLY_VARS:
            if var not in hourly:
                continue
            val = hourly[var][i]
            if val is None:
                continue
            rows.append((point_id, fetched_at, valid_time, var, float(val)))

    con.executemany("""
        INSERT OR IGNORE INTO forecasts
            (point_id, fetched_at, valid_time, variable, value)
        VALUES (?,?,?,?,?)
    """, rows)
    con.commit()
    return len(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 55)
    log.info("Open-Meteo Forecast Ingestion — Liège basin")
    log.info("=" * 55)

    con        = init_db(DB_PATH)
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total      = 0

    for point_id, lat, lon, desc in FORECAST_POINTS:
        log.info(f"  {point_id:<22}  {desc}")
        try:
            data = fetch_forecast(point_id, lat, lon)
            n    = parse_and_store(con, point_id, data, fetched_at)
            log.info(f"    → {n} rows  "
                     f"elevation={data.get('elevation','?')}m  "
                     f"model={data.get('hourly_units',{}).get('precipitation','?')}")
            total += n
        except Exception as e:
            log.error(f"    → {e}")

    log.info(f"\nTotal inserted: {total:,}")

    # Quick summary
    log.info("\n── Forecast summary ─────────────────────────────")
    for row in con.execute("""
        SELECT variable, COUNT(*), MIN(value), MAX(value)
        FROM forecasts GROUP BY variable ORDER BY variable
    """):
        log.info(f"  {row[0]:<35} n={row[1]:>5}  "
                 f"min={row[2]:>8.3f}  max={row[3]:>8.3f}")

    # Show next 24h precipitation for Liège city
    log.info("\n── Next 24h precipitation — Liège city ──────────")
    for row in con.execute("""
        SELECT valid_time, value
        FROM forecasts
        WHERE point_id='liege_city' AND variable='precipitation'
        ORDER BY valid_time
        LIMIT 24
    """):
        bar = "█" * int(row[1] * 10)
        log.info(f"  {row[0][:16]}  {row[1]:5.2f} mm  {bar}")

    log.info(f"\nDB → {Path(DB_PATH).resolve()}")
    con.close()
