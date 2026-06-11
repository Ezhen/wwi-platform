"""
Extract mean slope per station from slope_liege.tif
Saves to catchments_liege.db and prints summary.
"""
import sqlite3, numpy as np, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SLOPE_TIF = ROOT / "supplementary" / "dem" / "slope_liege.tif"
DB_CATCH  = str(ROOT / "export/databases/catchments_liege.db")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# Key stations with coordinates
STATIONS = [
    ("6387",  "EUPEN",         50.640, 6.048),
    ("6228",  "CHAUDFONTAINE", 50.583, 5.638),
    ("5904",  "COMBLAIN",      50.472, 5.578),
    ("5826",  "SAUHEID",       50.590, 5.530),
    ("6732",  "STAVELOT",      50.390, 5.930),
    ("6832",  "TROIS-PONTS",   50.368, 5.863),
    ("7141",  "HUY",           50.519, 5.239),
    ("7133",  "LIEGE",         50.640, 5.573),
    ("6958",  "ROBERTVILLE",   50.445, 6.096),
    ("6529",  "MONT-RIGI",     50.497, 6.097),
    ("6657",  "LOUVEIGNE",     50.551, 5.686),
]

if not SLOPE_TIF.exists():
    log.error(f"Slope raster not found: {SLOPE_TIF}")
    log.error("Run dem_processing.py first")
    exit(1)

import rasterio
from rasterio.transform import rowcol

log.info(f"Loading slope raster: {SLOPE_TIF}")
with rasterio.open(str(SLOPE_TIF)) as src:
    slope_data = src.read(1).astype(float)
    slope_data[slope_data < 0] = np.nan
    transform = src.transform
    nrows, ncols = src.height, src.width
    log.info(f"  Shape: {slope_data.shape}  "
             f"range: {np.nanmin(slope_data):.1f}° → {np.nanmax(slope_data):.1f}°")

# Extract mean slope in a window around each station
WINDOW_KM = 5   # 5km radius
WINDOW_PX = int(WINDOW_KM * 1000 / 30)  # pixels at 30m resolution

results = {}
log.info(f"\nExtracting slope (±{WINDOW_KM}km window per station):")
log.info(f"  {'Station':<20} {'Label':<15} {'Slope°':>8}  Response class")

for sno, label, lat, lon in STATIONS:
    try:
        row, col = rowcol(transform, lon, lat)
        r0 = max(0, row - WINDOW_PX)
        r1 = min(nrows, row + WINDOW_PX)
        c0 = max(0, col - WINDOW_PX)
        c1 = min(ncols, col + WINDOW_PX)

        window = slope_data[r0:r1, c0:c1]
        mean_slope = float(np.nanmean(window))
        std_slope  = float(np.nanstd(window))

        # Response class based on slope
        if mean_slope > 12:
            response = "FAST (<6h)"
        elif mean_slope > 6:
            response = "MODERATE (6-12h)"
        elif mean_slope > 2:
            response = "SLOW (12-24h)"
        else:
            response = "VERY SLOW (>24h)"

        results[sno] = {
            "label":      label,
            "mean_slope": round(mean_slope, 2),
            "std_slope":  round(std_slope, 2),
            "response":   response,
        }
        log.info(f"  {sno:<8} {label:<20} {mean_slope:>7.1f}°  {response}")

    except Exception as e:
        log.error(f"  {sno} {label}: {e}")

# Save to DB
log.info(f"\nSaving to {DB_CATCH}...")
con = sqlite3.connect(DB_CATCH)

# Add slope columns if missing
cols = [r[1] for r in con.execute("PRAGMA table_info(catchments)")]
if "mean_slope_deg" not in cols:
    con.execute("ALTER TABLE catchments ADD COLUMN mean_slope_deg REAL")
if "response_class" not in cols:
    con.execute("ALTER TABLE catchments ADD COLUMN response_class TEXT")
con.commit()

for sno, info in results.items():
    con.execute("""
        INSERT OR REPLACE INTO catchments
            (station_no, label, river, lat, lon,
             mean_slope_deg, response_class)
        VALUES (
            ?, ?, ?,
            (SELECT lat FROM catchments WHERE station_no=?),
            (SELECT lon FROM catchments WHERE station_no=?),
            ?, ?
        )
    """, (sno, info["label"],
          {"6387":"Vesdre","6228":"Vesdre","5904":"Ourthe",
           "5826":"Ourthe","6732":"Amblève","6832":"Salm",
           "7141":"Meuse","7133":"Meuse","6958":"Vesdre",
           "6529":"Amblève","6657":"Ourthe"}.get(sno,""),
          sno, sno,
          info["mean_slope"], info["response"]))

con.commit()

# Print final table
log.info("\nFinal slope table:")
log.info(f"  {'Station':<8} {'Label':<20} {'Slope°':>8} {'Response':<20}")
log.info(f"  {'─'*60}")
for r in con.execute("""
    SELECT station_no, label, mean_slope_deg, response_class
    FROM catchments WHERE mean_slope_deg IS NOT NULL
    ORDER BY mean_slope_deg DESC
"""):
    log.info(f"  {r[0]:<8} {r[1]:<20} {r[2]:>7.1f}°  {r[3]}")

con.close()
log.info("\n✓ Slopes extracted — updating build_alerts.py")
