"""
WWI — Download official SPW watersheds from géoportail.wallonie.be
No registration, no DEM processing needed.
Replaces computed catchments with official boundaries.

Layers:
  0 - Districts hydrographiques (Meuse, Escaut, Rhine)
  1 - Bassins versants plan de gestion
  2 - Bassins versants principaux (Ourthe, Vesdre, Amblève etc.)
  3 - Sous-bassins versants principaux (finest resolution)
"""

import requests
import sqlite3
import json
import logging
from pathlib import Path
from pyproj import Transformer

ROOT     = Path(__file__).parent.parent
DB_CATCH = str(ROOT / "export/databases/catchments_liege.db")

BASE_URL = "https://geoservices.wallonie.be/arcgis/rest/services/EAU/BASSINS/MapServer"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Lambert 72 → WGS84
transformer = Transformer.from_crs("EPSG:31370", "EPSG:4326", always_xy=True)


def query_layer(layer_id, where="1=1", out_fields="*"):
    """Query ArcGIS REST layer and return GeoJSON features."""
    url = f"{BASE_URL}/{layer_id}/query"
    params = {
        "where":           where,
        "outFields":       out_fields,
        "outSR":           "4326",       # request WGS84 directly
        "f":               "geojson",
        "returnGeometry":  "true",
        "geometryPrecision": 6,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    features = data.get("features", [])
    log.info(f"  Layer {layer_id}: {len(features)} features")
    return features


def init_db(db_path):
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS spw_watersheds (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            layer_id     INTEGER,
            name         TEXT,
            code         TEXT,
            district     TEXT,
            area_km2     REAL,
            centroid_lon REAL,
            centroid_lat REAL,
            geom_wkt     TEXT,
            properties   TEXT   -- full JSON properties
        );

        CREATE TABLE IF NOT EXISTS station_watershed (
            station_no   TEXT,
            watershed_id INTEGER,
            layer_id     INTEGER,
            distance_km  REAL,
            PRIMARY KEY (station_no, watershed_id)
        );

        CREATE INDEX IF NOT EXISTS idx_ws_layer ON spw_watersheds(layer_id);
        CREATE INDEX IF NOT EXISTS idx_ws_name  ON spw_watersheds(name);
    """)
    con.commit()
    return con


def insert_features(con, layer_id, features):
    """Insert GeoJSON features into DB."""
    from shapely.geometry import shape
    import shapely.wkt

    rows = []
    for f in features:
        props = f.get("properties", {})
        geom  = f.get("geometry")
        if not geom:
            continue
        try:
            shp = shape(geom)
            centroid = shp.centroid
            area_deg = shp.area
            # Rough area in km² (degrees² → km² at 50°N)
            area_km2 = area_deg * (111.32 * 111.32 * 0.64)

            # Find name field
            name = (props.get("NOM") or props.get("NOM_FR") or
                    props.get("LIBELLE") or props.get("NAME") or
                    props.get("NOM_BASSIN") or str(props))[:100]
            code = (props.get("CODE") or props.get("CODE_BASSIN") or
                    props.get("CDBASSIN") or "")[:50]
            district = (props.get("DISTRICT") or props.get("NOM_DISTRICT") or "")[:50]

            rows.append((
                layer_id, name, code, district,
                round(area_km2, 1),
                round(centroid.x, 6),
                round(centroid.y, 6),
                shp.wkt,
                json.dumps(props),
            ))
        except Exception as e:
            log.warning(f"  Skipping feature: {e}")

    con.executemany("""
        INSERT INTO spw_watersheds
            (layer_id, name, code, district, area_km2,
             centroid_lon, centroid_lat, geom_wkt, properties)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, rows)
    con.commit()
    log.info(f"  Inserted {len(rows)} watersheds for layer {layer_id}")
    return len(rows)


def assign_stations_to_watersheds(con):
    """
    For each SPW station, find which watershed it falls in
    at each layer level.
    """
    from shapely.geometry import Point
    from shapely.wkt import loads as wkt_loads

    log.info("\nAssigning stations to watersheds...")

    # Load stations from spw_liege.db
    spw_db = str(ROOT / "export/databases/spw_liege.db")
    con_spw = sqlite3.connect(spw_db)
    stations = con_spw.execute("""
        SELECT station_no, station_name, lat, lon
        FROM stations
        WHERE lat IS NOT NULL AND lat != 0
    """).fetchall()
    con_spw.close()

    log.info(f"  {len(stations)} SPW stations to assign")

    # Load all watersheds
    watersheds = con.execute("""
        SELECT id, layer_id, name, geom_wkt FROM spw_watersheds
    """).fetchall()

    for sno, sname, slat, slon in stations:
        pt = Point(slon, slat)
        for wid, lid, wname, wkt in watersheds:
            try:
                poly = wkt_loads(wkt)
                if poly.contains(pt):
                    con.execute("""
                        INSERT OR IGNORE INTO station_watershed
                            (station_no, watershed_id, layer_id, distance_km)
                        VALUES (?,?,?,0)
                    """, (sno, wid, lid))
            except: pass

    con.commit()

    # Report
    log.info("\n── Station → Watershed assignment (Layer 3) ───────────")
    for row in con.execute("""
        SELECT sw.station_no, w.name AS wname, w.area_km2
        FROM station_watershed sw
        JOIN spw_watersheds w ON sw.watershed_id = w.id
        WHERE sw.layer_id = 3
        ORDER BY sw.station_no
    """):
        log.info(f"  {row[0]:8s} → {row[1]:<35}  {row[2]:>8.1f} km²")


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("SPW Watershed Download — géoportail.wallonie.be")
    log.info("=" * 60)

    try:
        from shapely.geometry import shape, Point
    except ImportError:
        log.info("Installing shapely...")
        import os
        os.system("pip install shapely --break-system-packages -q")
        from shapely.geometry import shape, Point

    con = init_db(DB_CATCH)

    # Clear old watershed data
    con.execute("DELETE FROM spw_watersheds")
    con.execute("DELETE FROM station_watershed")
    con.commit()

    total = 0
    for layer_id, desc in [
        (0, "Districts hydrographiques"),
        (1, "Bassins versants plan de gestion"),
        (2, "Bassins versants principaux"),
        (3, "Sous-bassins versants principaux"),
    ]:
        log.info(f"\nLayer {layer_id}: {desc}")
        try:
            features = query_layer(layer_id)
            if features:
                n = insert_features(con, layer_id, features)
                total += n

                # Show what we got
                sample = con.execute(
                    "SELECT name, code, area_km2 FROM spw_watersheds "
                    "WHERE layer_id=? ORDER BY area_km2 DESC LIMIT 5",
                    (layer_id,)
                ).fetchall()
                for r in sample:
                    log.info(f"  {r[0]:<40} {r[1]:<15} {r[2]:>8.1f} km²")
        except Exception as e:
            log.error(f"  Layer {layer_id} failed: {e}")

    log.info(f"\nTotal watersheds stored: {total}")

    # Assign stations
    assign_stations_to_watersheds(con)

    # Final summary
    log.info("\n── Watershed summary by layer ──────────────────────────")
    for row in con.execute("""
        SELECT layer_id, COUNT(*) AS n,
               MIN(area_km2) AS min_area,
               MAX(area_km2) AS max_area
        FROM spw_watersheds
        GROUP BY layer_id
    """):
        log.info(f"  Layer {row[0]}: {row[1]:>4} polygons  "
                 f"area {row[2]:.0f} → {row[3]:.0f} km²")

    log.info(f"\nDB → {DB_CATCH}")
    log.info("✓ Official SPW watersheds downloaded and assigned to stations")
    log.info("Next: use watershed polygons for catchment-weighted ERA5 precip")
    con.close()
