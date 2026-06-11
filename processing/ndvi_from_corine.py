"""
WWI — Synthetic NDVI from CORINE Land Cover
Computes a seasonal NDVI proxy per catchment using:
  - CORINE land cover class fractions per catchment
  - Seasonal sinusoidal cycle per vegetation type
  - Outputs daily NDVI time series 2021-2025

No satellite download needed.
"""

import sqlite3
import pandas as pd
import numpy as np
import json
import logging
from pathlib import Path
from datetime import date

def find_root():
    """Find project root (wwi/) regardless of script location."""
    p = Path(__file__).resolve()
    while p.name != "wwi" and p.parent != p:
        p = p.parent
    return p if p.name == "wwi" else Path(__file__).resolve().parent

ROOT = find_root()


DB_CATCH = str(ROOT / "export/databases/catchments_liege.db")
DB_CORINE = str(ROOT / "export/databases/corine_liege.db")
OUT_CSV  = str(ROOT / "export/csvs/ndvi_synthetic.csv")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── CORINE CLC code → NDVI parameters ────────────────────────────────────────
# (summer_peak, winter_trough, peak_doy)
# CLC numeric codes: https://www.eea.europa.eu/data-and-maps/data/corine-land-cover-5
CORINE_NDVI = {
    # Artificial surfaces
    111: (0.12, 0.08, 180),   # Continuous urban fabric
    112: (0.25, 0.15, 180),   # Discontinuous urban fabric
    121: (0.08, 0.05, 180),   # Industrial/commercial
    122: (0.05, 0.04, 180),   # Road/rail networks
    123: (0.05, 0.04, 180),   # Port areas
    124: (0.15, 0.10, 180),   # Airports
    131: (0.08, 0.05, 180),   # Mineral extraction
    132: (0.10, 0.05, 180),   # Dump sites
    133: (0.10, 0.05, 180),   # Construction sites
    141: (0.45, 0.25, 165),   # Green urban areas
    142: (0.35, 0.20, 165),   # Sport/leisure facilities
    # Agricultural
    211: (0.55, 0.10, 160),   # Non-irrigated arable land
    212: (0.60, 0.15, 160),   # Permanently irrigated land
    213: (0.50, 0.10, 160),   # Rice fields
    221: (0.55, 0.10, 165),   # Vineyards
    222: (0.65, 0.20, 170),   # Fruit trees
    223: (0.60, 0.15, 165),   # Olive groves
    231: (0.60, 0.22, 165),   # Pastures
    241: (0.52, 0.15, 160),   # Annual crops + permanent crops
    242: (0.52, 0.15, 160),   # Complex cultivation patterns
    243: (0.50, 0.18, 160),   # Agriculture + natural vegetation
    244: (0.55, 0.30, 165),   # Agro-forestry
    # Forests
    311: (0.78, 0.45, 175),   # Broad-leaved forest
    312: (0.72, 0.62, 180),   # Coniferous forest
    313: (0.75, 0.52, 175),   # Mixed forest
    # Shrub / herbaceous
    321: (0.55, 0.20, 165),   # Natural grasslands
    322: (0.48, 0.32, 165),   # Moors and heathland  ← Hautes Fagnes!
    323: (0.55, 0.40, 175),   # Sclerophyllous vegetation
    324: (0.55, 0.25, 170),   # Transitional woodland-shrub
    # Open spaces
    331: (0.10, 0.08, 180),   # Beaches/dunes
    332: (0.08, 0.06, 180),   # Bare rocks
    333: (0.20, 0.12, 165),   # Sparsely vegetated areas
    334: (0.05, 0.04, 180),   # Burnt areas
    335: (0.05, 0.04, 180),   # Glaciers
    # Wetlands
    411: (0.58, 0.22, 160),   # Inland marshes
    412: (0.48, 0.28, 160),   # Peat bogs  ← Hautes Fagnes!
    421: (0.45, 0.20, 155),   # Salt marshes
    422: (0.35, 0.15, 155),   # Salines
    423: (0.25, 0.10, 155),   # Intertidal flats
    # Water
    511: (0.20, 0.15, 180),   # Water courses
    512: (0.05, 0.05, 180),   # Water bodies
    521: (0.05, 0.05, 180),   # Coastal lagoons
    522: (0.05, 0.05, 180),   # Estuaries
    523: (0.05, 0.05, 180),   # Sea/ocean
    # Default
    "_default": (0.45, 0.20, 170),
}


def seasonal_ndvi(doy, summer, winter, peak_doy=180):
    """Sinusoidal NDVI as function of day of year."""
    amplitude = (summer - winter) / 2
    mean      = (summer + winter) / 2
    # Peak at peak_doy, trough 6 months later
    phase = 2 * np.pi * (doy - peak_doy) / 365.25
    return mean + amplitude * np.cos(phase)


def get_corine_fractions_for_catchment(con_corine, lat, lon, radius_deg=0.3):
    """
    Get CORINE class area fractions within radius of station.
    Uses centroid proximity as proxy for catchment coverage.
    """
    # Detect label column name from schema
    cols = [r[1] for r in con_corine.execute("PRAGMA table_info(land_cover)")]
    label_col = next((c for c in cols if "label" in c.lower() or
                      "class" in c.lower() or "nom" in c.lower() or
                      "code" in c.lower()), cols[2] if len(cols) > 2 else cols[0])
    area_col  = next((c for c in cols if "area" in c.lower() or
                      "ha" in c.lower() or "surf" in c.lower()), None)

    if area_col:
        sql = f"""
            SELECT {label_col}, SUM({area_col}) AS total_ha
            FROM land_cover
            WHERE centroid_lat BETWEEN ? AND ?
              AND centroid_lon BETWEEN ? AND ?
              AND {label_col} IS NOT NULL
            GROUP BY {label_col}
            ORDER BY total_ha DESC
        """
    else:
        # No area column — just count polygons
        sql = f"""
            SELECT {label_col}, COUNT(*) AS total_ha
            FROM land_cover
            WHERE centroid_lat BETWEEN ? AND ?
              AND centroid_lon BETWEEN ? AND ?
              AND {label_col} IS NOT NULL
            GROUP BY {label_col}
            ORDER BY total_ha DESC
        """

    rows = con_corine.execute(sql,
        (lat - radius_deg, lat + radius_deg,
         lon - radius_deg, lon + radius_deg)).fetchall()

    if not rows:
        return {"_default": 1.0}

    total = sum(r[1] for r in rows)
    # Convert numeric CLC codes to int for CORINE_NDVI lookup
    result = {}
    for code, area in rows:
        try:
            key = int(float(code))
        except (ValueError, TypeError):
            key = str(code)
        result[key] = area / total
    return result


def compute_ndvi_series(fractions, date_range):
    """
    Compute daily NDVI time series as weighted average of class NDVIs.
    """
    doys = date_range.dayofyear
    ndvi_series = np.zeros(len(date_range))

    for class_name, fraction in fractions.items():
        params = CORINE_NDVI.get(class_name, CORINE_NDVI["_default"])
        summer, winter, peak_doy = params
        class_ndvi = seasonal_ndvi(doys, summer, winter, peak_doy)
        ndvi_series += fraction * class_ndvi

    return ndvi_series


# ── Main ──────────────────────────────────────────────────────────────────────

log.info("=" * 55)
log.info("WWI Synthetic NDVI from CORINE")
log.info("=" * 55)

# Load catchments
con_catch = sqlite3.connect(DB_CATCH)
stations = con_catch.execute("""
    SELECT station_no, label, river, lat, lon
    FROM catchments
    WHERE lat IS NOT NULL
""").fetchall()
con_catch.close()
log.info(f"Stations: {len(stations)}")

# Open CORINE DB
if not Path(DB_CORINE).exists():
    log.error(f"CORINE DB not found: {DB_CORINE}")
    exit(1)

con_corine = sqlite3.connect(DB_CORINE)

# Check CORINE schema
cols = [r[1] for r in con_corine.execute("PRAGMA table_info(land_cover)")]
log.info(f"CORINE columns: {cols}")
# Show sample row to understand schema
sample = con_corine.execute("SELECT * FROM land_cover LIMIT 1").fetchone()
log.info(f"Sample row: {sample}")

# Date range: full training + flood window
date_range = pd.date_range("2021-01-01", "2025-12-31", freq="D")
log.info(f"Date range: {date_range[0].date()} → {date_range[-1].date()} ({len(date_range)} days)")

# Compute NDVI per station
results = {}
for sno, label, river, lat, lon in stations:
    log.info(f"\n  {label} ({river}) lat={lat:.3f} lon={lon:.3f}")

    fractions = get_corine_fractions_for_catchment(con_corine, lat, lon)

    # Show dominant classes
    top3 = sorted(fractions.items(), key=lambda x: -x[1])[:3]
    for cls, frac in top3:
        log.info(f"    {cls:<45} {frac*100:.1f}%")

    ndvi = compute_ndvi_series(fractions, date_range)
    results[f"NDVI_{sno}_{label}"] = ndvi

    # Stats
    log.info(f"    NDVI range: {ndvi.min():.3f} → {ndvi.max():.3f}  "
             f"mean={ndvi.mean():.3f}")

con_corine.close()

# Build DataFrame
df = pd.DataFrame(results, index=date_range)
df.index.name = "date"

# Add derived features
for col in df.columns:
    sno  = col.split("_")[1]
    label = "_".join(col.split("_")[2:])
    # Anomaly vs 30-day rolling mean (captures greening/browning events)
    df[f"{col}_anom"] = df[col] - df[col].rolling(30, center=True, min_periods=15).mean()

# Save
df.to_csv(OUT_CSV)
log.info(f"\n✓ Saved → {OUT_CSV}")
log.info(f"  Shape: {df.shape}")

# Preview seasonal cycle for SAUHEID
log.info("\nSAUHEID NDVI seasonal preview:")
sauheid_col = [c for c in df.columns if "5826" in c and "anom" not in c]
if sauheid_col:
    s = df[sauheid_col[0]]
    for month in [1, 3, 6, 7, 9, 12]:
        val = s[s.index.month == month].mean()
        bar = "█" * int(val * 30)
        log.info(f"  Month {month:>2}: NDVI={val:.3f}  {bar}")

log.info("\nNext: update build_features_v2.py to add NDVI columns")
