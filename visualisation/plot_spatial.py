"""
WWI Spatial Maps — Cartopy
Plots slope raster and synthetic NDVI for Liège province.
Output: export/maps/wwi_spatial_maps.png
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import date

ROOT      = Path(__file__).parent.parent
SLOPE_TIF = ROOT / "supplementary" / "dem" / "slope_liege.tif"
DB_SPW    = str(ROOT / "export/databases/spw_liege.db")
DB_CATCH  = str(ROOT / "export/databases/catchments_liege.db")
CSV_NDVI  = str(ROOT / "export/csvs/ndvi_synthetic.csv")
OUT_DIR   = ROOT / "export" / "maps"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE  = str(OUT_DIR / "wwi_spatial_maps.png")

# Liège bbox
LAT_MIN, LAT_MAX = 50.0, 50.85
LON_MIN, LON_MAX =  5.0,  6.40

print("=" * 55)
print("WWI Spatial Maps")
print("=" * 55)

# ── Check cartopy ─────────────────────────────────────────────────────────────
try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_CARTOPY = True
    print("✓ cartopy available")
except ImportError:
    HAS_CARTOPY = False
    print("✗ cartopy not available — using plain matplotlib")

# ── Load slope raster ─────────────────────────────────────────────────────────
print("\nLoading slope raster...")
if SLOPE_TIF.exists():
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.open(str(SLOPE_TIF)) as src:
        window = from_bounds(LON_MIN, LAT_MIN, LON_MAX, LAT_MAX,
                             src.transform)
        slope_data = src.read(1, window=window).astype(float)
        slope_data[slope_data < 0] = np.nan  # nodata
        transform = src.window_transform(window)
        height, width = slope_data.shape
        lons_slope = np.array([transform.c + i * transform.a
                               for i in range(width)])
        lats_slope = np.array([transform.f + i * transform.e
                               for i in range(height)])
    print(f"  Slope shape: {slope_data.shape}  "
          f"range: {np.nanmin(slope_data):.1f}° → {np.nanmax(slope_data):.1f}°")
else:
    print("  slope_liege.tif not found — slope panel will be empty")
    slope_data = None

# ── Load SPW stations ─────────────────────────────────────────────────────────
print("\nLoading SPW stations...")
con = sqlite3.connect(DB_SPW)
stations = pd.read_sql("""
    SELECT station_no, station_name, river_name, lat, lon
    FROM stations
    WHERE lat IS NOT NULL AND lat != 0
      AND lat BETWEEN 49.8 AND 51.1
      AND lon BETWEEN 4.5 AND 6.6
""", con)
con.close()
print(f"  {len(stations)} stations loaded")

# Key forecast stations
KEY_STATIONS = {
    "5826": "SAUHEID",
    "6387": "EUPEN",
    "6228": "CHAUDFONTAINE",
    "6732": "STAVELOT",
    "6832": "TROIS-PONTS",
    "7141": "HUY",
    "6958": "ROBERTVILLE",
    "6529": "MONT-RIGI",
}

# ── Load NDVI CSV ─────────────────────────────────────────────────────────────
print("\nLoading NDVI data...")
ndvi_stations = {}
if Path(CSV_NDVI).exists():
    ndvi_df = pd.read_csv(CSV_NDVI, index_col=0, parse_dates=True)
    # Get June peak values per station
    june_mask = ndvi_df.index.month == 6
    for col in ndvi_df.columns:
        if "anom" in col: continue
        parts = col.split("_")
        if len(parts) >= 3:
            sno = parts[1]
            ndvi_stations[sno] = float(ndvi_df.loc[june_mask, col].mean())
    print(f"  NDVI loaded for {len(ndvi_stations)} stations")

# ── Load CORINE ───────────────────────────────────────────────────────────────
print("\nLoading CORINE land cover...")
db_corine = str(ROOT / "export/databases/corine_liege.db")
corine_pts = None
if Path(db_corine).exists():
    con = sqlite3.connect(db_corine)
    corine_pts = pd.read_sql("""
        SELECT clc_code, area_ha, centroid_lon AS lon, centroid_lat AS lat
        FROM land_cover
        WHERE centroid_lat BETWEEN ? AND ?
          AND centroid_lon BETWEEN ? AND ?
    """, con, params=(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX))
    con.close()
    print(f"  {len(corine_pts)} CORINE polygons in bbox")

# ── Build figure ──────────────────────────────────────────────────────────────
print("\nBuilding figure...")

if HAS_CARTOPY:
    proj = ccrs.PlateCarree()
    fig = plt.figure(figsize=(18, 13), dpi=150)
    fig.patch.set_facecolor("#f8f8f6")

    ax1 = fig.add_subplot(221, projection=proj)
    ax2 = fig.add_subplot(222, projection=proj)
    ax3 = fig.add_subplot(223, projection=proj)
    ax4 = fig.add_subplot(224, projection=proj)
    axes = [ax1, ax2, ax3, ax4]

    for ax in axes:
        ax.set_extent([LON_MIN, LON_MAX, LAT_MIN, LAT_MAX], crs=proj)
        ax.add_feature(cfeature.BORDERS.with_scale("10m"),
                       linewidth=0.5, color="#aaa")
        ax.add_feature(cfeature.RIVERS.with_scale("10m"),
                       linewidth=0.6, color="#4488cc", alpha=0.6)
        ax.add_feature(cfeature.LAKES.with_scale("10m"),
                       color="#4488cc", alpha=0.4)
        gl = ax.gridlines(draw_labels=True, linewidth=0.3,
                          color="gray", alpha=0.4, linestyle="--")
        gl.top_labels = False
        gl.right_labels = False
        gl.xlabel_style = {"size": 7}
        gl.ylabel_style = {"size": 7}

    # ── Panel 1: Slope ────────────────────────────────────────────────────────
    ax1.set_title("Terrain slope (°)", fontsize=11, pad=6, fontweight="normal")
    if slope_data is not None:
        slope_clip = np.clip(slope_data, 0, 30)
        im1 = ax1.imshow(slope_clip,
                         extent=[lons_slope[0], lons_slope[-1],
                                 lats_slope[-1], lats_slope[0]],
                         transform=proj,
                         cmap="YlOrRd", vmin=0, vmax=25,
                         origin="upper", aspect="auto", zorder=2)
        cb1 = plt.colorbar(im1, ax=ax1, orientation="horizontal",
                           pad=0.02, fraction=0.04, shrink=0.8)
        cb1.set_label("degrees", fontsize=8)
        cb1.ax.tick_params(labelsize=7)

    # Station dots
    for _, row in stations.iterrows():
        ax1.plot(row.lon, row.lat, "o",
                 markersize=3, color="#333", zorder=5, transform=proj)
    # Key stations labelled
    for sno, label in KEY_STATIONS.items():
        st = stations[stations.station_no == sno]
        if len(st):
            ax1.plot(st.iloc[0].lon, st.iloc[0].lat, "^",
                     markersize=6, color="#222", zorder=6, transform=proj)

    # ── Panel 2: NDVI June peak ───────────────────────────────────────────────
    ax2.set_title("Peak NDVI (June, synthetic)", fontsize=11, pad=6,
                  fontweight="normal")

    cmap_ndvi = mcolors.LinearSegmentedColormap.from_list(
        "ndvi", ["#d4e6b5", "#5aab35", "#1a6b1a"], N=256)
    norm_ndvi = mcolors.Normalize(vmin=0.35, vmax=0.65)

    for _, row in stations.iterrows():
        sno = row.station_no
        ndvi_val = ndvi_stations.get(sno, 0.45)
        color = cmap_ndvi(norm_ndvi(ndvi_val))
        ax2.scatter(row.lon, row.lat,
                    s=60, c=[color], zorder=5,
                    transform=proj, edgecolors="#555", linewidths=0.5)

    # Colorbar
    sm = plt.cm.ScalarMappable(cmap=cmap_ndvi, norm=norm_ndvi)
    sm.set_array([])
    cb2 = plt.colorbar(sm, ax=ax2, orientation="horizontal",
                       pad=0.02, fraction=0.04, shrink=0.8)
    cb2.set_label("NDVI", fontsize=8)
    cb2.ax.tick_params(labelsize=7)

    # Labels for key stations
    for sno, label in KEY_STATIONS.items():
        st = stations[stations.station_no == sno]
        if len(st):
            ndvi_val = ndvi_stations.get(sno, 0.45)
            ax2.annotate(f"{label}\n{ndvi_val:.2f}",
                         xy=(st.iloc[0].lon, st.iloc[0].lat),
                         xytext=(4, 4), textcoords="offset points",
                         fontsize=6.5, color="#222",
                         transform=proj,
                         zorder=7)

    # ── Panel 3: CORINE land cover ────────────────────────────────────────────
    ax3.set_title("CORINE land cover (dominant classes)", fontsize=11,
                  pad=6, fontweight="normal")

    # CLC code colour map (simplified)
    clc_colors = {
        111: "#e60000", 112: "#ff0000",        # Urban
        121: "#cc4df2", 122: "#cc0000",         # Industrial
        131: "#e6cccc", 133: "#f2cca6",         # Extraction/construction
        141: "#ffffa8", 142: "#e6e6e6",         # Green/sport
        211: "#ffff00", 212: "#ffffa8",         # Arable
        221: "#e6a600", 222: "#e68000",         # Vineyards/orchards
        231: "#e6e600",                          # Pastures
        241: "#ffe6a6", 242: "#ffe64d",         # Complex agriculture
        243: "#e6cc4d", 244: "#e6cc4d",
        311: "#80ff00",                          # Broad-leaved forest
        312: "#00a600",                          # Coniferous forest
        313: "#4dff00",                          # Mixed forest
        321: "#ccf24d", 322: "#a6ff80",         # Grassland/heath
        324: "#a6e64d",                          # Transitional woodland
        411: "#a6a6ff", 412: "#4d4dff",         # Wetlands
        511: "#00ccf2", 512: "#0080ff",         # Water
    }

    if corine_pts is not None and len(corine_pts):
        for code, color in clc_colors.items():
            pts = corine_pts[corine_pts.clc_code == code]
            if len(pts):
                ax3.scatter(pts.lon, pts.lat,
                            s=pts.area_ha.clip(0.5, 20) * 0.3,
                            c=color, alpha=0.5, zorder=3,
                            transform=proj, linewidths=0)

    # Legend patches
    legend_patches = [
        Patch(color="#ff0000", label="Urban (112)"),
        Patch(color="#e6e600", label="Pastures (231)"),
        Patch(color="#ffff00", label="Arable (211)"),
        Patch(color="#00a600", label="Coniferous (312)"),
        Patch(color="#4dff00", label="Mixed forest (313)"),
        Patch(color="#80ff00", label="Broad-leaved (311)"),
        Patch(color="#4d4dff", label="Peat bog (412)"),
        Patch(color="#0080ff", label="Water (512)"),
    ]
    ax3.legend(handles=legend_patches, loc="lower left",
               fontsize=6, framealpha=0.8, ncol=2)

    # ── Panel 4: Station network + river system ───────────────────────────────
    ax4.set_title("Station network & risk (current)", fontsize=11,
                  pad=6, fontweight="normal")

    # Background slope
    if slope_data is not None:
        ax4.imshow(np.clip(slope_data, 0, 30),
                   extent=[lons_slope[0], lons_slope[-1],
                            lats_slope[-1], lats_slope[0]],
                   transform=proj,
                   cmap="Greys", vmin=0, vmax=30,
                   alpha=0.25, origin="upper", aspect="auto", zorder=1)

    # All stations
    ax4.scatter(stations.lon, stations.lat,
                s=20, c="#1565c0", zorder=5,
                transform=proj, alpha=0.7,
                edgecolors="none", label="H/Q stations")

    # Forecast stations labelled by NDVI
    for sno, label in KEY_STATIONS.items():
        st = stations[stations.station_no == sno]
        if len(st):
            ndvi_val = ndvi_stations.get(sno, 0.45)
            c = cmap_ndvi(norm_ndvi(ndvi_val))
            ax4.scatter(st.iloc[0].lon, st.iloc[0].lat,
                        s=100, c=[c], zorder=6,
                        transform=proj,
                        edgecolors="#222", linewidths=0.8)
            ax4.annotate(label,
                         xy=(st.iloc[0].lon, st.iloc[0].lat),
                         xytext=(3, 3), textcoords="offset points",
                         fontsize=7, fontweight="bold", color="#111",
                         transform=proj, zorder=7)

    ax4.legend(fontsize=7, loc="lower left", framealpha=0.8)

else:
    # Plain matplotlib fallback (no cartopy)
    fig, axes = plt.subplots(2, 2, figsize=(16, 12), dpi=150)
    fig.patch.set_facecolor("#f8f8f6")
    ax1, ax2, ax3, ax4 = axes.flat

    for ax in [ax1, ax2, ax3, ax4]:
        ax.set_xlim(LON_MIN, LON_MAX)
        ax.set_ylim(LAT_MIN, LAT_MAX)
        ax.set_xlabel("Longitude", fontsize=8)
        ax.set_ylabel("Latitude", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3, linewidth=0.3)
        ax.set_aspect("equal")

    ax1.set_title("Terrain slope (°)", fontsize=11, fontweight="normal")
    if slope_data is not None:
        im1 = ax1.imshow(np.clip(slope_data, 0, 30),
                         extent=[lons_slope[0], lons_slope[-1],
                                 lats_slope[-1], lats_slope[0]],
                         cmap="YlOrRd", vmin=0, vmax=25,
                         origin="upper", aspect="auto")
        plt.colorbar(im1, ax=ax1, label="degrees", shrink=0.8)
    ax1.scatter(stations.lon, stations.lat, s=8, c="#333", zorder=5)

    ax2.set_title("Peak NDVI (June)", fontsize=11, fontweight="normal")
    cmap_ndvi = mcolors.LinearSegmentedColormap.from_list(
        "ndvi", ["#d4e6b5", "#5aab35", "#1a6b1a"])
    norm_ndvi = mcolors.Normalize(vmin=0.35, vmax=0.65)
    for _, row in stations.iterrows():
        sno = row.station_no
        ndvi_val = ndvi_stations.get(sno, 0.45)
        color = cmap_ndvi(norm_ndvi(ndvi_val))
        ax2.scatter(row.lon, row.lat, s=50, c=[color], zorder=5,
                    edgecolors="#555", linewidths=0.5)
    sm = plt.cm.ScalarMappable(cmap=cmap_ndvi, norm=norm_ndvi)
    sm.set_array([])
    plt.colorbar(sm, ax=ax2, label="NDVI", shrink=0.8)
    for sno, label in KEY_STATIONS.items():
        st = stations[stations.station_no == sno]
        if len(st):
            ndvi_val = ndvi_stations.get(sno, 0.45)
            ax2.annotate(f"{label}\n{ndvi_val:.2f}",
                         xy=(st.iloc[0].lon, st.iloc[0].lat),
                         xytext=(4, 4), textcoords="offset points",
                         fontsize=6.5)

    ax3.set_title("CORINE land cover", fontsize=11, fontweight="normal")
    clc_colors = {
        112: "#ff0000", 211: "#ffff00", 231: "#e6e600",
        311: "#80ff00", 312: "#00a600", 313: "#4dff00",
        322: "#a6ff80", 412: "#4d4dff", 511: "#00ccf2",
    }
    if corine_pts is not None:
        for code, color in clc_colors.items():
            pts = corine_pts[corine_pts.clc_code == code]
            if len(pts):
                ax3.scatter(pts.lon, pts.lat,
                            s=pts.area_ha.clip(0.5, 20) * 0.3,
                            c=color, alpha=0.5, linewidths=0)
    legend_patches = [
        Patch(color="#ff0000", label="Urban"),
        Patch(color="#e6e600", label="Pastures"),
        Patch(color="#00a600", label="Coniferous"),
        Patch(color="#4dff00", label="Mixed forest"),
        Patch(color="#4d4dff", label="Peat bog"),
    ]
    ax3.legend(handles=legend_patches, fontsize=7, loc="lower left")

    ax4.set_title("Station network", fontsize=11, fontweight="normal")
    if slope_data is not None:
        ax4.imshow(np.clip(slope_data, 0, 30),
                   extent=[lons_slope[0], lons_slope[-1],
                           lats_slope[-1], lats_slope[0]],
                   cmap="Greys", vmin=0, vmax=30,
                   alpha=0.25, origin="upper", aspect="auto")
    ax4.scatter(stations.lon, stations.lat, s=15, c="#1565c0",
                zorder=5, alpha=0.7)
    for sno, label in KEY_STATIONS.items():
        st = stations[stations.station_no == sno]
        if len(st):
            ax4.scatter(st.iloc[0].lon, st.iloc[0].lat,
                        s=80, c="#d32f2f", zorder=6, marker="^")
            ax4.annotate(label,
                         xy=(st.iloc[0].lon, st.iloc[0].lat),
                         xytext=(3, 3), textcoords="offset points",
                         fontsize=7, fontweight="bold")

fig.suptitle(
    f"Wallonia Water Intelligence Platform — Liège Basin Spatial Overview\n"
    f"Slope: GLO-30 DEM 30m · NDVI: synthetic from CORINE 2018 · "
    f"Generated {date.today()}",
    fontsize=10, y=1.01
)
plt.tight_layout(rect=[0, 0, 1, 1])
plt.savefig(OUT_FILE, dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
plt.close()
print(f"\n✓ Saved → {OUT_FILE}")
