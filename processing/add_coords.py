from pathlib import Path
ROOT = Path(__file__).parent.parent
DB_SPW = str(ROOT / "export/databases/spw_liege.db")
DB_PIEZ = str(ROOT / "export/databases/piez_liege.db")

"""
Add WGS84 coordinates to SPW and Piezometry databases.

SPW:   convert Belgian Lambert 72 (EPSG:31370) → WGS84
Piez:  recover from piez_layer2.json (already in Lambert 72 or local coords)
"""

import sqlite3
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

try:
    from pyproj import Transformer
    HAS_PYPROJ = True
except ImportError:
    log.warning("pyproj not installed — run: pip install pyproj --break-system-packages")
    HAS_PYPROJ = False


def add_wgs84_columns(con, table="stations"):
    """Add lat/lon columns if they don't exist."""
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    if "lat" not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN lat REAL")
        log.info(f"  Added lat column to {table}")
    if "lon" not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN lon REAL")
        log.info(f"  Added lon column to {table}")
    con.commit()


# ── SPW: Lambert 72 → WGS84 ───────────────────────────────────────────────────

def convert_spw_coords():
    if not DB_SPW.exists():
        log.warning("spw_liege.db not found"); return
    if not HAS_PYPROJ:
        log.warning("Skipping SPW — pyproj needed"); return

    con = sqlite3.connect(str(DB_SPW))
    add_wgs84_columns(con)

    transformer = Transformer.from_crs("EPSG:31370", "EPSG:4326", always_xy=True)

    rows = con.execute(
        "SELECT station_no, local_x, local_y FROM stations "
        "WHERE local_x IS NOT NULL AND local_x != 0"
    ).fetchall()

    log.info(f"Converting {len(rows)} SPW stations Lambert72 → WGS84...")
    updated = 0
    for station_no, x, y in rows:
        try:
            lon, lat = transformer.transform(float(x), float(y))
            con.execute(
                "UPDATE stations SET lat=?, lon=? WHERE station_no=?",
                (round(lat, 6), round(lon, 6), station_no)
            )
            updated += 1
        except Exception as e:
            log.error(f"  {station_no}: {e}")

    con.commit()
    log.info(f"  SPW: {updated} stations updated")

    # Verify
    sample = con.execute(
        "SELECT station_no, station_name, lat, lon FROM stations "
        "WHERE lat IS NOT NULL LIMIT 5"
    ).fetchall()
    for r in sample:
        log.info(f"  {r[0]:8s}  {r[1]:<28}  lat={r[2]}  lon={r[3]}")
    con.close()


# ── Piezometry: recover from piez_layer2.json ─────────────────────────────────

def recover_piez_coords():
    if not DB_PIEZ.exists():
        log.warning("piez_liege.db not found"); return

    # Try piez_layer2.json first
    json_path = None
    for candidate in ["piez_layer2.json", "era5_extract/piez_layer2.json"]:
        if Path(candidate).exists():
            json_path = candidate
            break

    if not json_path:
        log.warning("piez_layer2.json not found — trying to re-fetch coords from KiWIS")
        recover_piez_coords_api()
        return

    log.info(f"Loading piezometry coords from {json_path}...")
    with open(json_path) as f:
        data = json.load(f)

    log.info(f"  {len(data)} entries in JSON")

    con = sqlite3.connect(str(DB_PIEZ))
    add_wgs84_columns(con)

    # The JSON has station_local_x/y in Belgian Lambert 72 or already WGS84
    # Check magnitude — Lambert 72 x is ~150,000-300,000
    sample_x = next((float(d["station_local_x"]) for d in data
                     if d.get("station_local_x")), None)
    log.info(f"  Sample x value: {sample_x}")

    needs_conversion = sample_x and sample_x > 1000  # Lambert 72 range

    if needs_conversion and HAS_PYPROJ:
        transformer = Transformer.from_crs("EPSG:31370", "EPSG:4326", always_xy=True)
        log.info("  Coordinates in Lambert 72 — converting to WGS84")
    elif needs_conversion:
        log.warning("  Lambert 72 coords but pyproj missing — storing as-is")
        transformer = None
    else:
        log.info("  Coordinates appear to be WGS84 already")
        transformer = None

    updated = 0
    for entry in data:
        sno = entry.get("station_no", "")
        x   = entry.get("station_local_x")
        y   = entry.get("station_local_y")
        if not x or not y:
            continue
        try:
            x, y = float(x), float(y)
            if transformer:
                lon, lat = transformer.transform(x, y)
            elif x > 180:  # still Lambert, no transformer
                continue
            else:
                lon, lat = x, y
            con.execute(
                "UPDATE stations SET lat=?, lon=? WHERE station_no=?",
                (round(lat, 6), round(lon, 6), sno)
            )
            updated += 1
        except Exception as e:
            log.error(f"  {sno}: {e}")

    con.commit()
    log.info(f"  Piezometry: {updated} stations updated")

    # Verify
    n_with = con.execute(
        "SELECT COUNT(*) FROM stations WHERE lat IS NOT NULL"
    ).fetchone()[0]
    log.info(f"  Stations with WGS84 coords: {n_with} / {len(data)}")

    sample = con.execute(
        "SELECT station_no, station_name, commune, lat, lon "
        "FROM stations WHERE lat IS NOT NULL LIMIT 5"
    ).fetchall()
    for r in sample:
        log.info(f"  {r[0]:10s}  {r[1]:<30}  {r[2]:<15}  lat={r[3]}  lon={r[4]}")
    con.close()


def recover_piez_coords_api():
    """Fallback: fetch coordinates directly from piezometrie KiWIS."""
    import requests
    log.info("Fetching piezometry coordinates from KiWIS...")

    PORTAL = "https://piezometrie.wallonie.be/home/observations/niveau-deau-souterraine.html"
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer": PORTAL,
    })
    s.get(PORTAL, timeout=15)

    r = s.get("https://piezometrie.wallonie.be/services/KiWIS/KiWIS", params={
        "request": "getStationList", "service": "kisters",
        "type": "queryServices", "datasource": "0", "format": "objson",
        "returnfields": "station_no,station_name,station_local_x,station_local_y,station_elevation",
    }, timeout=30)

    if r.status_code != 200:
        log.error(f"API returned {r.status_code}"); return

    data = r.json()
    log.info(f"  {len(data)} stations from API")

    con = sqlite3.connect(str(DB_PIEZ))
    add_wgs84_columns(con)

    if HAS_PYPROJ:
        transformer = Transformer.from_crs("EPSG:31370", "EPSG:4326", always_xy=True)
    else:
        transformer = None

    updated = 0
    for entry in data:
        sno = entry.get("station_no", "")
        x   = entry.get("station_local_x")
        y   = entry.get("station_local_y")
        if not x or not y or str(x).strip() == "":
            continue
        try:
            x, y = float(x), float(y)
            if x == 0 and y == 0:
                continue
            if transformer and x > 1000:
                lon, lat = transformer.transform(x, y)
            else:
                lon, lat = x, y
            con.execute(
                "UPDATE stations SET lat=?, lon=? WHERE station_no=?",
                (round(lat, 6), round(lon, 6), sno)
            )
            updated += 1
        except Exception as e:
            continue

    con.commit()
    n_with = con.execute(
        "SELECT COUNT(*) FROM stations WHERE lat IS NOT NULL AND lat != 0"
    ).fetchone()[0]
    log.info(f"  Updated: {updated}  with coords: {n_with}")
    con.close()


if __name__ == "__main__":
    log.info("=" * 55)
    log.info("Adding WGS84 coordinates to databases")
    log.info("=" * 55)

    log.info("\n── SPW ──────────────────────────────────────────────────")
    convert_spw_coords()

    log.info("\n── Piezometry ───────────────────────────────────────────")
    recover_piez_coords()

    # Final summary
    log.info("\n" + "=" * 55)
    log.info("Summary")
    log.info("=" * 55)
    for db, table in [(str(DB_SPW),"stations"), (str(DB_PIEZ),"stations")]:
        if Path(db).exists():
            con = sqlite3.connect(db)
            total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            n_coords = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE lat IS NOT NULL AND lat != 0"
            ).fetchone()[0]
            log.info(f"  {db:<20} {n_coords:>3}/{total} stations with WGS84 coords")
            con.close()
