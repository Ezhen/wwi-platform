"""
Assign SPW stations to official watersheds.
Reads spw_watersheds (already populated) and matches
each station coordinate against watershed polygons.
"""
import sqlite3
import json
import logging
from pathlib import Path
from shapely.geometry import Point
from shapely.wkt import loads as wkt_loads

ROOT     = Path(__file__).parent.parent
DB_CATCH = str(ROOT / "export/databases/catchments_liege.db")
DB_SPW   = str(ROOT / "export/databases/spw_liege.db")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

log.info("Loading watersheds...")
con = sqlite3.connect(DB_CATCH)

# Show what we have
for row in con.execute("""
    SELECT layer_id, COUNT(*) AS n, MIN(area_km2), MAX(area_km2)
    FROM spw_watersheds GROUP BY layer_id ORDER BY layer_id
"""):
    log.info(f"  Layer {row[0]}: {row[1]} polygons  "
             f"area {row[2]:.0f} → {row[3]:.0f} km²")

# Sample names
log.info("\nSample watershed names (Layer 2 & 3):")
for row in con.execute("""
    SELECT layer_id, name, code, area_km2
    FROM spw_watersheds
    WHERE layer_id IN (2,3)
    ORDER BY layer_id, area_km2 DESC
    LIMIT 20
"""):
    log.info(f"  L{row[0]}  {row[1]:<40} {row[2]:<15} {row[3]:>8.0f} km²")

# Load all watershed geometries
log.info("\nLoading watershed geometries...")
watersheds = []
for wid, lid, name, code, wkt in con.execute(
    "SELECT id, layer_id, name, code, geom_wkt FROM spw_watersheds"
):
    try:
        poly = wkt_loads(wkt) if wkt and not wkt.startswith('{') else None
        if poly is None: continue
        if poly.is_valid:
            watersheds.append((wid, lid, name, code, poly))
    except Exception as e:
        log.warning(f"  Invalid geometry for {name}: {e}")
log.info(f"  Loaded {len(watersheds)} valid geometries")

# Load stations from SPW DB
log.info("\nLoading SPW stations...")
con_spw = sqlite3.connect(DB_SPW)
stations = con_spw.execute("""
    SELECT station_no, station_name, river_name, lat, lon
    FROM stations
    WHERE lat IS NOT NULL AND lat != 0
    ORDER BY station_no
""").fetchall()
con_spw.close()
log.info(f"  {len(stations)} stations with coordinates")

# Clear old assignments
con.execute("DELETE FROM station_watershed")
con.commit()

# Assign each station to containing watersheds
log.info("\nAssigning stations to watersheds...")
assigned = 0
unassigned = []

for sno, sname, river, slat, slon in stations:
    pt = Point(slon, slat)
    found_layers = set()

    for wid, lid, wname, wcode, poly in watersheds:
        try:
            if poly.contains(pt) or poly.distance(pt) < 0.01:
                con.execute("""
                    INSERT OR IGNORE INTO station_watershed
                        (station_no, watershed_id, layer_id, distance_km)
                    VALUES (?,?,?,?)
                """, (sno, wid, lid, round(poly.distance(pt) * 111, 3)))
                found_layers.add(lid)
        except: pass

    if found_layers:
        assigned += 1
    else:
        unassigned.append(f"{sno} {sname}")

con.commit()
log.info(f"  Assigned: {assigned}/{len(stations)}")
if unassigned:
    log.warning(f"  Unassigned: {unassigned}")

# Also populate catchments table with watershed data for key stations
log.info("\nPopulating catchments table from watersheds (Layer 3)...")
con.execute("DELETE FROM catchments")

# Key stations with known coordinates
KEY_STATIONS = [
    ("6387",  "EUPEN",         "Vesdre",   50.640, 6.048),
    ("6228",  "CHAUDFONTAINE", "Vesdre",   50.583, 5.638),
    ("5904",  "COMBLAIN",      "Ourthe",   50.472, 5.578),
    ("5826",  "SAUHEID",       "Ourthe",   50.590, 5.530),
    ("6732",  "STAVELOT",      "Amblève",  50.390, 5.930),
    ("6832",  "TROIS-PONTS",   "Salm",     50.368, 5.863),
    ("7141",  "HUY",           "Meuse",    50.519, 5.239),
    ("7133",  "LIEGE",         "Meuse",    50.640, 5.573),
    ("6657",  "LOUVEIGNE",     "Ourthe",   50.551, 5.686),
    ("6958",  "ROBERTVILLE",   "Vesdre",   50.445, 6.096),
    ("6529",  "MONT-RIGI",     "Amblève",  50.497, 6.097),
]

ERA5_POINTS = [
    (50.75,5.00),(50.75,5.25),(50.75,5.50),(50.75,5.75),
    (50.75,6.00),(50.75,6.25),(50.75,6.50),
    (50.50,5.00),(50.50,5.25),(50.50,5.50),(50.50,5.75),
    (50.50,6.00),(50.50,6.25),(50.50,6.50),
    (50.25,5.00),(50.25,5.25),(50.25,5.50),(50.25,5.75),
    (50.25,6.00),(50.25,6.25),(50.25,6.50),
    (50.00,5.00),(50.00,5.25),(50.00,5.50),(50.00,5.75),
    (50.00,6.00),(50.00,6.25),(50.00,6.50),
    (49.75,5.00),(49.75,5.25),(49.75,5.50),(49.75,5.75),
    (49.75,6.00),(49.75,6.25),(49.75,6.50),
    (49.50,5.00),(49.50,5.25),(49.50,5.50),(49.50,5.75),
    (49.50,6.00),(49.50,6.25),(49.50,6.50),
]

for sno, label, river, lat, lon in KEY_STATIONS:
    pt = Point(lon, lat)

    # Find best matching Layer 3 watershed
    best_ws = None
    best_area = float('inf')
    for wid, lid, wname, wcode, poly in watersheds:
        if lid != 3: continue
        if poly.contains(pt) or poly.distance(pt) < 0.02:
            if poly.area < best_area:
                best_area = poly.area
                best_ws = (wid, wname, poly)

    if not best_ws:
        # Fall back to Layer 2
        for wid, lid, wname, wcode, poly in watersheds:
            if lid != 2: continue
            if poly.contains(pt) or poly.distance(pt) < 0.05:
                if poly.area < best_area:
                    best_area = poly.area
                    best_ws = (wid, wname, poly)

    # Compute ERA5 weights within watershed polygon
    era5_weights = {}
    if best_ws:
        ws_poly = best_ws[2]
        for era5_lat, era5_lon in ERA5_POINTS:
            era5_pt = Point(era5_lon, era5_lat)
            if ws_poly.contains(era5_pt) or ws_poly.distance(era5_pt) < 0.15:
                era5_weights[f"{era5_lat:.2f}_{era5_lon:.2f}"] = 1.0
        if era5_weights:
            total = sum(era5_weights.values())
            era5_weights = {k: round(v/total, 4) for k, v in era5_weights.items()}

    area_km2 = best_ws[2].area * 111.32 * 111.32 * 0.64 if best_ws else None
    ws_name  = best_ws[1] if best_ws else "NOT FOUND"

    con.execute("""
        INSERT OR REPLACE INTO catchments
            (station_no, label, river, lat, lon, area_km2,
             mean_slope_deg, n_cells, era5_weights)
        VALUES (?,?,?,?,?,?,NULL,NULL,?)
    """, (sno, label, river, lat, lon,
          round(area_km2, 1) if area_km2 else None,
          json.dumps(era5_weights)))

    # ERA5 weights table
    for key, w in era5_weights.items():
        lat_s, lon_s = key.split("_")
        con.execute("""
            INSERT OR REPLACE INTO era5_catchment_weights
                (station_no, era5_lat, era5_lon, weight)
            VALUES (?,?,?,?)
        """, (sno, float(lat_s), float(lon_s), w))

    log.info(f"  {sno:8s}  {label:<20} → {ws_name:<35}  "
             f"area={area_km2:.0f} km²  ERA5={len(era5_weights)} cells"
             if area_km2 else
             f"  {sno:8s}  {label:<20} → NOT FOUND")

con.commit()

# Final summary
log.info("\n" + "="*60)
log.info("Final catchment table:")
for row in con.execute("""
    SELECT station_no, label, river, area_km2, era5_weights
    FROM catchments ORDER BY river, label
"""):
    weights = json.loads(row[4]) if row[4] else {}
    log.info(f"  {row[0]:8s}  {row[1]:<20} {row[2]:<10} "
             f"area={row[3] or 'N/A':>8}  ERA5={len(weights)} cells")

log.info(f"\n✓ Done — run: python validate_catchments.py")
con.close()
"""
Assign SPW stations to official watersheds.
Reads spw_watersheds (already populated) and matches
each station coordinate against watershed polygons.
"""
import sqlite3
import json
import logging
from pathlib import Path
from shapely.geometry import Point
from shapely.wkt import loads as wkt_loads

ROOT     = Path(__file__).parent.parent
DB_CATCH = str(ROOT / "export/databases/catchments_liege.db")
DB_SPW   = str(ROOT / "export/databases/spw_liege.db")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

log.info("Loading watersheds...")
con = sqlite3.connect(DB_CATCH)

# Show what we have
for row in con.execute("""
    SELECT layer_id, COUNT(*) AS n, MIN(area_km2), MAX(area_km2)
    FROM spw_watersheds GROUP BY layer_id ORDER BY layer_id
"""):
    log.info(f"  Layer {row[0]}: {row[1]} polygons  "
             f"area {row[2]:.0f} → {row[3]:.0f} km²")

# Sample names
log.info("\nSample watershed names (Layer 2 & 3):")
for row in con.execute("""
    SELECT layer_id, name, code, area_km2
    FROM spw_watersheds
    WHERE layer_id IN (2,3)
    ORDER BY layer_id, area_km2 DESC
    LIMIT 20
"""):
    log.info(f"  L{row[0]}  {row[1]:<40} {row[2]:<15} {row[3]:>8.0f} km²")

# Load all watershed geometries
log.info("\nLoading watershed geometries...")
watersheds = []
for wid, lid, name, code, wkt in con.execute(
    "SELECT id, layer_id, name, code, geom_wkt FROM spw_watersheds"
):
    try:
        poly = wkt_loads(wkt) if wkt and not wkt.startswith('{') else None
        if poly is None: continue
        if poly.is_valid:
            watersheds.append((wid, lid, name, code, poly))
    except Exception as e:
        log.warning(f"  Invalid geometry for {name}: {e}")
log.info(f"  Loaded {len(watersheds)} valid geometries")

# Load stations from SPW DB
log.info("\nLoading SPW stations...")
con_spw = sqlite3.connect(DB_SPW)
stations = con_spw.execute("""
    SELECT station_no, station_name, river_name, lat, lon
    FROM stations
    WHERE lat IS NOT NULL AND lat != 0
    ORDER BY station_no
""").fetchall()
con_spw.close()
log.info(f"  {len(stations)} stations with coordinates")

# Clear old assignments
con.execute("DELETE FROM station_watershed")
con.commit()

# Assign each station to containing watersheds
log.info("\nAssigning stations to watersheds...")
assigned = 0
unassigned = []

for sno, sname, river, slat, slon in stations:
    pt = Point(slon, slat)
    found_layers = set()

    for wid, lid, wname, wcode, poly in watersheds:
        try:
            if poly.contains(pt) or poly.distance(pt) < 0.01:
                con.execute("""
                    INSERT OR IGNORE INTO station_watershed
                        (station_no, watershed_id, layer_id, distance_km)
                    VALUES (?,?,?,?)
                """, (sno, wid, lid, round(poly.distance(pt) * 111, 3)))
                found_layers.add(lid)
        except: pass

    if found_layers:
        assigned += 1
    else:
        unassigned.append(f"{sno} {sname}")

con.commit()
log.info(f"  Assigned: {assigned}/{len(stations)}")
if unassigned:
    log.warning(f"  Unassigned: {unassigned}")

# Also populate catchments table with watershed data for key stations
log.info("\nPopulating catchments table from watersheds (Layer 3)...")
con.execute("DELETE FROM catchments")

# Key stations with known coordinates
KEY_STATIONS = [
    ("6387",  "EUPEN",         "Vesdre",   50.640, 6.048),
    ("6228",  "CHAUDFONTAINE", "Vesdre",   50.583, 5.638),
    ("5904",  "COMBLAIN",      "Ourthe",   50.472, 5.578),
    ("5826",  "SAUHEID",       "Ourthe",   50.590, 5.530),
    ("6732",  "STAVELOT",      "Amblève",  50.390, 5.930),
    ("6832",  "TROIS-PONTS",   "Salm",     50.368, 5.863),
    ("7141",  "HUY",           "Meuse",    50.519, 5.239),
    ("7133",  "LIEGE",         "Meuse",    50.640, 5.573),
    ("6657",  "LOUVEIGNE",     "Ourthe",   50.551, 5.686),
    ("6958",  "ROBERTVILLE",   "Vesdre",   50.445, 6.096),
    ("6529",  "MONT-RIGI",     "Amblève",  50.497, 6.097),
]

ERA5_POINTS = [
    (50.75,5.00),(50.75,5.25),(50.75,5.50),(50.75,5.75),
    (50.75,6.00),(50.75,6.25),(50.75,6.50),
    (50.50,5.00),(50.50,5.25),(50.50,5.50),(50.50,5.75),
    (50.50,6.00),(50.50,6.25),(50.50,6.50),
    (50.25,5.00),(50.25,5.25),(50.25,5.50),(50.25,5.75),
    (50.25,6.00),(50.25,6.25),(50.25,6.50),
    (50.00,5.00),(50.00,5.25),(50.00,5.50),(50.00,5.75),
    (50.00,6.00),(50.00,6.25),(50.00,6.50),
    (49.75,5.00),(49.75,5.25),(49.75,5.50),(49.75,5.75),
    (49.75,6.00),(49.75,6.25),(49.75,6.50),
    (49.50,5.00),(49.50,5.25),(49.50,5.50),(49.50,5.75),
    (49.50,6.00),(49.50,6.25),(49.50,6.50),
]

for sno, label, river, lat, lon in KEY_STATIONS:
    pt = Point(lon, lat)

    # Find best matching Layer 3 watershed
    best_ws = None
    best_area = float('inf')
    for wid, lid, wname, wcode, poly in watersheds:
        if lid != 3: continue
        if poly.contains(pt) or poly.distance(pt) < 0.02:
            if poly.area < best_area:
                best_area = poly.area
                best_ws = (wid, wname, poly)

    if not best_ws:
        # Fall back to Layer 2
        for wid, lid, wname, wcode, poly in watersheds:
            if lid != 2: continue
            if poly.contains(pt) or poly.distance(pt) < 0.05:
                if poly.area < best_area:
                    best_area = poly.area
                    best_ws = (wid, wname, poly)

    # Compute ERA5 weights within watershed polygon
    era5_weights = {}
    if best_ws:
        ws_poly = best_ws[2]
        for era5_lat, era5_lon in ERA5_POINTS:
            era5_pt = Point(era5_lon, era5_lat)
            if ws_poly.contains(era5_pt) or ws_poly.distance(era5_pt) < 0.15:
                era5_weights[f"{era5_lat:.2f}_{era5_lon:.2f}"] = 1.0
        if era5_weights:
            total = sum(era5_weights.values())
            era5_weights = {k: round(v/total, 4) for k, v in era5_weights.items()}

    area_km2 = best_ws[2].area * 111.32 * 111.32 * 0.64 if best_ws else None
    ws_name  = best_ws[1] if best_ws else "NOT FOUND"

    con.execute("""
        INSERT OR REPLACE INTO catchments
            (station_no, label, river, lat, lon, area_km2,
             mean_slope_deg, n_cells, era5_weights)
        VALUES (?,?,?,?,?,?,NULL,NULL,?)
    """, (sno, label, river, lat, lon,
          round(area_km2, 1) if area_km2 else None,
          json.dumps(era5_weights)))

    # ERA5 weights table
    for key, w in era5_weights.items():
        lat_s, lon_s = key.split("_")
        con.execute("""
            INSERT OR REPLACE INTO era5_catchment_weights
                (station_no, era5_lat, era5_lon, weight)
            VALUES (?,?,?,?)
        """, (sno, float(lat_s), float(lon_s), w))

    log.info(f"  {sno:8s}  {label:<20} → {ws_name:<35}  "
             f"area={area_km2:.0f} km²  ERA5={len(era5_weights)} cells"
             if area_km2 else
             f"  {sno:8s}  {label:<20} → NOT FOUND")

con.commit()

# Final summary
log.info("\n" + "="*60)
log.info("Final catchment table:")
for row in con.execute("""
    SELECT station_no, label, river, area_km2, era5_weights
    FROM catchments ORDER BY river, label
"""):
    weights = json.loads(row[4]) if row[4] else {}
    log.info(f"  {row[0]:8s}  {row[1]:<20} {row[2]:<10} "
             f"area={row[3] or 'N/A':>8}  ERA5={len(weights)} cells")

log.info(f"\n✓ Done — run: python validate_catchments.py")
con.close()
