"""
WWI Catchment Statistics Aggregator
Uses SPW official watershed polygons (Layer 3) to compute:
  - Mean NDVI per catchment
  - Mean slope per catchment
  - CORINE land cover fractions per catchment

All using point-in-polygon intersection against existing databases.
"""

import sqlite3
import numpy as np
import json
import logging
from pathlib import Path
from shapely.geometry import Point, shape
from shapely.wkt import loads as wkt_loads

ROOT = Path(__file__).resolve().parent.parent
DB_CATCH = str(ROOT / "export/databases/catchments_liege.db")
DB_CORINE = str(ROOT / "export/databases/corine_liege.db")
CSV_NDVI  = str(ROOT / "export/csvs/ndvi_synthetic.csv")
SLOPE_TIF = ROOT / "supplementary/dem/slope_liege.tif"

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# Key stations
STATIONS = [
    ("6387",  "EUPEN",         "Vesdre",   50.640, 6.048),
    ("6228",  "CHAUDFONTAINE", "Vesdre",   50.583, 5.638),
    ("5904",  "COMBLAIN",      "Ourthe",   50.472, 5.578),
    ("5826",  "SAUHEID",       "Ourthe",   50.590, 5.530),
    ("6732",  "STAVELOT",      "Amblève",  50.390, 5.930),
    ("6832",  "TROIS-PONTS",   "Salm",     50.368, 5.863),
    ("7141",  "HUY",           "Meuse",    50.519, 5.239),
    ("6958",  "ROBERTVILLE",   "Vesdre",   50.445, 6.096),
    ("6529",  "MONT-RIGI",     "Amblève",  50.497, 6.097),
    ("6657",  "LOUVEIGNE",     "Ourthe",   50.551, 5.686),
]

# ── Load SPW watershed polygons ───────────────────────────────────────────────
log.info("Loading SPW watershed polygons...")
con_catch = sqlite3.connect(DB_CATCH)

# Get Layer 3 (sub-basins) and Layer 2 (main basins)
ws_rows = con_catch.execute("""
    SELECT id, layer_id, name, code, area_km2, geom_wkt
    FROM spw_watersheds
    WHERE layer_id IN (2, 3)
      AND geom_wkt IS NOT NULL
      AND geom_wkt NOT LIKE '{%'
    ORDER BY layer_id DESC, area_km2 ASC
""").fetchall()

watersheds = []
for wid, lid, name, code, area, wkt in ws_rows:
    try:
        poly = wkt_loads(wkt)
        if poly.is_valid:
            watersheds.append({
                "id": wid, "layer": lid, "name": name,
                "code": code, "area": area, "geom": poly
            })
    except Exception as e:
        pass

log.info(f"  Loaded {len(watersheds)} valid watershed polygons")

# ── Find best watershed per station ──────────────────────────────────────────
log.info("\nMatching stations to watersheds...")
station_ws = {}
for sno, label, river, lat, lon in STATIONS:
    pt = Point(lon, lat)
    best = None
    best_area = float('inf')
    # Find smallest containing polygon (most specific)
    for ws in watersheds:
        try:
            if ws["geom"].contains(pt) or ws["geom"].distance(pt) < 0.01:
                if ws["area"] and ws["area"] < best_area:
                    best_area = ws["area"]
                    best = ws
        except: pass

    if best:
        station_ws[sno] = best
        log.info(f"  {sno:8s} {label:<20} → {best['name'][:35]:<35} "
                 f"L{best['layer']} {best['area']:.0f} km²")
    else:
        log.warning(f"  {sno:8s} {label:<20} → NO WATERSHED FOUND")

# ── Slope per watershed polygon ───────────────────────────────────────────────
log.info("\nComputing slope per watershed...")
slope_by_station = {}

if SLOPE_TIF.exists():
    import rasterio
    from rasterio.mask import mask as rio_mask
    from shapely.geometry import mapping

    with rasterio.open(str(SLOPE_TIF)) as src:
        for sno, ws in station_ws.items():
            try:
                # Clip slope raster to watershed polygon
                geom = [mapping(ws["geom"])]
                clipped, _ = rio_mask(src, geom, crop=True, nodata=-9999)
                slope_vals = clipped[0]
                valid = slope_vals[(slope_vals > 0) & (slope_vals < 61)]
                if len(valid) > 0:
                    mean_slope = float(np.mean(valid))
                    slope_by_station[sno] = round(mean_slope, 2)
                    label = next(s[1] for s in STATIONS if s[0]==sno)
                    log.info(f"  {sno:8s} {label:<20} slope={mean_slope:.1f}°  "
                             f"({len(valid):,} pixels)")
            except Exception as e:
                log.warning(f"  {sno}: slope failed — {e}")
else:
    log.warning("  Slope raster not found — skipping")

# ── CORINE fractions per watershed ────────────────────────────────────────────
log.info("\nComputing CORINE fractions per watershed...")
corine_by_station = {}

if Path(DB_CORINE).exists():
    con_corine = sqlite3.connect(DB_CORINE)

    # CLC → vegetation type mapping
    CORINE_NDVI = {
        111: (0.12, 0.08, 180), 112: (0.25, 0.15, 180),
        211: (0.55, 0.10, 160), 231: (0.60, 0.22, 165),
        311: (0.78, 0.45, 175), 312: (0.72, 0.62, 180),
        313: (0.75, 0.52, 175), 322: (0.48, 0.32, 165),
        412: (0.48, 0.28, 160), 511: (0.20, 0.15, 180),
    }

    for sno, ws in station_ws.items():
        bounds = ws["geom"].bounds  # minx, miny, maxx, maxy
        label = next(s[1] for s in STATIONS if s[0]==sno)

        # Get CORINE polygons within watershed bounds
        corine_rows = con_corine.execute("""
            SELECT clc_code, area_ha, centroid_lon, centroid_lat
            FROM land_cover
            WHERE centroid_lon BETWEEN ? AND ?
              AND centroid_lat BETWEEN ? AND ?
        """, (bounds[0], bounds[2], bounds[1], bounds[3])).fetchall()

        # Filter to points actually inside watershed
        inside = [(r[0], r[1]) for r in corine_rows
                  if ws["geom"].contains(Point(r[2], r[3]))]

        if not inside:
            log.warning(f"  {sno} {label}: no CORINE inside polygon")
            continue

        total_ha = sum(r[1] for r in inside)
        fractions = {}
        for code, ha in inside:
            fractions[code] = fractions.get(code, 0) + ha

        fractions = {k: v/total_ha for k,v in fractions.items()}
        corine_by_station[sno] = fractions

        # Top 3 classes
        top3 = sorted(fractions.items(), key=lambda x: -x[1])[:3]
        log.info(f"  {sno:8s} {label:<20} "
                 f"{', '.join(f'{k}:{v*100:.0f}%' for k,v in top3)}")

    con_corine.close()

# ── Update catchments DB ──────────────────────────────────────────────────────
log.info("\nUpdating catchments database...")

# Add columns if missing
cols = [r[1] for r in con_catch.execute("PRAGMA table_info(catchments)")]
for col, typ in [("watershed_name","TEXT"), ("watershed_area_km2","REAL"),
                  ("slope_watershed_deg","REAL"), ("corine_forest_frac","REAL"),
                  ("corine_urban_frac","REAL"), ("corine_agri_frac","REAL")]:
    if col not in cols:
        con_catch.execute(f"ALTER TABLE catchments ADD COLUMN {col} {typ}")
con_catch.commit()

for sno, label, river, lat, lon in STATIONS:
    ws    = station_ws.get(sno)
    slope = slope_by_station.get(sno)
    corine = corine_by_station.get(sno, {})

    # Forest = 311+312+313, Urban = 111+112, Agri = 211+231+242
    forest = sum(corine.get(c,0) for c in [311,312,313,324])
    urban  = sum(corine.get(c,0) for c in [111,112,121,131])
    agri   = sum(corine.get(c,0) for c in [211,212,231,241,242])

    # Response class from watershed slope
    if slope:
        if slope > 12:   response = "FAST (<6h)"
        elif slope > 6:  response = "MODERATE (6-12h)"
        elif slope > 2:  response = "SLOW (12-24h)"
        else:            response = "VERY SLOW (>24h)"
    else:
        response = None

    con_catch.execute("""
        UPDATE catchments SET
            watershed_name      = ?,
            watershed_area_km2  = ?,
            slope_watershed_deg = ?,
            response_class      = ?,
            corine_forest_frac  = ?,
            corine_urban_frac   = ?,
            corine_agri_frac    = ?
        WHERE station_no = ?
    """, (
        ws["name"] if ws else None,
        ws["area"] if ws else None,
        slope,
        response,
        round(forest, 3) if forest else None,
        round(urban,  3) if urban  else None,
        round(agri,   3) if agri   else None,
        sno
    ))

con_catch.commit()

# ── Summary ───────────────────────────────────────────────────────────────────
log.info("\n" + "=" * 70)
log.info("Final catchment statistics:")
log.info(f"  {'Station':<8} {'Label':<20} {'Slope°':>8} {'Forest%':>8} "
         f"{'Urban%':>8} {'Response'}")
log.info("  " + "─"*68)

for r in con_catch.execute("""
    SELECT station_no, label, slope_watershed_deg,
           corine_forest_frac, corine_urban_frac,
           response_class, watershed_name
    FROM catchments
    WHERE slope_watershed_deg IS NOT NULL
    ORDER BY slope_watershed_deg DESC
"""):
    log.info(f"  {r[0]:<8} {r[1]:<20} {r[2]:>7.1f}°  "
             f"{(r[3] or 0)*100:>6.0f}%  "
             f"{(r[4] or 0)*100:>6.0f}%  "
             f"{r[5] or 'N/A'}")

con_catch.close()
log.info("\n✓ Done")
