"""
WWI — Download river centrelines from OpenStreetMap via Overpass API
Saves GeoJSON to export/maps/rivers_liege.geojson
Used by plot_network_errors.py for accurate river polylines.
"""
import requests, json, time, logging
from pathlib import Path
from collections import defaultdict

ROOT     = Path(__file__).resolve().parent.parent
OUT_FILE = str(ROOT / "export/maps/rivers_liege.geojson")
Path(ROOT / "export/maps").mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# Liège basin bbox: S,W,N,E
BBOX = "50.20,4.90,50.85,6.40"

# Rivers to fetch — OSM name tags
RIVERS = {
    "Meuse":     "Meuse",
    "Ourthe":    "Ourthe",
    "Amblève":   "Amblève",
    "Vesdre":    "Vesdre",
    "Salm":      "Salm",
    "Méhaigne":  "Méhaigne",
    "Hoëgne":    "Hoëgne",
    "Gileppe":   "Gileppe",
    "Lesse":     "Lesse",
}

OVERPASS_URL = "https://overpass.private.coffee/api/interpreter"

def query_river(name, bbox):
    """Query OSM via GET with manually encoded URL (same as working curl)."""
    import urllib.parse
    query = (
        f'[out:json][timeout:30];'
        f'(way["waterway"="river"]["name"="{name}"]({bbox});'
        f'way["waterway"="stream"]["name"="{name}"]({bbox}););'
        f'out geom;'
    )
    url = OVERPASS_URL + "?data=" + urllib.parse.quote(query)
    try:
        r = requests.get(url, timeout=40)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"  {name}: {e}")
        return None

def ways_to_linestring(elements):
    """
    Convert OSM way elements to ordered coordinate list.
    Tries to chain ways end-to-end (river is split into multiple ways).
    """
    if not elements:
        return []

    # Build segments from way geometries
    segments = []
    for el in elements:
        if el.get("type") == "way" and "geometry" in el:
            coords = [(pt["lon"], pt["lat"]) for pt in el["geometry"]]
            if coords:
                segments.append(coords)

    if not segments:
        return []

    # Chain segments: find which end of each segment connects to others
    def dist(a, b):
        return ((a[0]-b[0])**2 + (a[1]-b[1])**2)**0.5

    # Start with longest segment
    segments.sort(key=len, reverse=True)
    result = list(segments[0])
    remaining = segments[1:]

    for _ in range(len(remaining)):
        best_seg   = None
        best_dist  = 0.05  # max gap degrees (~5km)
        best_pos   = None
        best_flip  = False

        for seg in remaining:
            # Try connecting to start or end of result
            for flip in [False, True]:
                s = seg[::-1] if flip else seg
                d_end_start   = dist(result[-1], s[0])
                d_start_start = dist(result[0],  s[0])
                d_end_end     = dist(result[-1], s[-1])
                d_start_end   = dist(result[0],  s[-1])

                if d_end_start < best_dist:
                    best_dist = d_end_start
                    best_seg  = s
                    best_pos  = "end"
                    best_flip = False

                if d_start_end < best_dist:
                    best_dist = d_start_end
                    best_seg  = s[::-1]
                    best_pos  = "start"
                    best_flip = False

        if best_seg:
            if best_pos == "end":
                result.extend(best_seg[1:])
            else:
                result = list(best_seg) + result[1:]
            remaining = [s for s in remaining if s is not best_seg
                         and s[::-1] != best_seg]
        else:
            break

    return result

# ── Download ──────────────────────────────────────────────────────────────────
log.info("Querying Overpass API for Liège river network...")
features = []

for key, name in RIVERS.items():
    log.info(f"  Fetching {name}...")
    data = query_river(name, BBOX)
    if not data:
        continue

    elements = data.get("elements", [])
    log.info(f"    {len(elements)} way segments found")

    segments = ways_to_segments(elements)
    if not segments:
        log.warning(f"    Could not build segments for {name}")
        continue

    total_pts = sum(len(s) for s in segments)
    log.info(f"    → {len(segments)} segments, {total_pts} total points")

    # Store as MultiLineString — preserves all segments without artifacts
    features.append({
        "type": "Feature",
        "properties": {"name": name, "key": key},
        "geometry": {
            "type": "MultiLineString",
            "coordinates": segments
        }
    })
    time.sleep(1)  # be polite to Overpass

# Save GeoJSON
geojson = {"type": "FeatureCollection", "features": features}
with open(OUT_FILE, "w") as f:
    json.dump(geojson, f)

log.info(f"\n✓ Saved {len(features)} rivers → {OUT_FILE}")
for feat in features:
    n = len(feat["geometry"]["coordinates"])
    log.info(f"  {feat['properties']['name']:<15} {n:>5} points")
