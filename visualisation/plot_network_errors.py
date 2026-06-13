"""
WWI Network Error Map
Cartopy map of Liège province showing river network
with stations coloured by recent forecast error.
"""

import sqlite3
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.cm as cm
from pathlib import Path
from datetime import datetime

ROOT     = Path(__file__).resolve().parent.parent
DB_SPW   = str(ROOT / "export/databases/spw_liege.db")
DB_HIST  = str(ROOT / "export/databases/historical_liege.db")
OUT_FILE = str(ROOT / "export/maps/network_error_map.png")

# ── Station network with coordinates ─────────────────────────────────────────
# (station_no, label, river, lon, lat, river_km)
NETWORK = [
    # Salm
    ("6832", "TROIS-PONTS",    "Salm",    5.863, 50.368,  0),
    # Amblève
    ("6529", "MONT-RIGI",      "Amblève", 6.097, 50.497,  5),
    ("6732", "STAVELOT",       "Amblève", 5.930, 50.390, 20),
    ("6671", "TARGNON",        "Amblève", 5.771, 50.390, 30),
    ("6657", "REMOUCHAMPS",    "Amblève", 5.686, 50.490, 50),
    # Vesdre
    ("6958", "ROBERTVILLE",    "Vesdre",  6.096, 50.445,  5),
    ("6387", "EUPEN",          "Vesdre",  6.048, 50.640, 20),
    ("6353", "DOLHAIN",        "Vesdre",  5.814, 50.597, 42),
    ("6228", "CHAUDFONTAINE",  "Vesdre",  5.638, 50.583, 68),
    # Ourthe
    ("5904", "COMBLAIN",       "Ourthe",  5.578, 50.472, 25),
    ("5826", "SAUHEID",        "Ourthe",  5.530, 50.590, 48),
    ("5806", "ANGLEUR",        "Ourthe",  5.573, 50.607, 55),
    # Meuse
    ("7141", "HUY",            "Meuse",   5.239, 50.519, 20),
    ("7133", "NEUVILLE",       "Meuse",   5.573, 50.640, 48),
    ("5757", "LANAYE",         "Meuse",   5.638, 50.730, 70),
    # Mehaigne
    ("5578", "WAREMME",        "Méhaigne",5.256, 50.697,  5),
]

# River polylines (lon, lat points) — approximate centrelines
RIVERS = {
    # Meuse — flows W→NW through Liège
    "Meuse": [
        (5.239,50.519),(5.280,50.530),(5.340,50.548),
        (5.410,50.565),(5.460,50.580),(5.510,50.598),
        (5.543,50.613),(5.573,50.638),(5.595,50.657),
        (5.620,50.693),(5.638,50.728),(5.670,50.758),
    ],
    # Ourthe — flows NW from Ardennes, joins Meuse at Liège
    "Ourthe": [
        (5.863,50.368),  # Trois-Ponts
        (5.830,50.385),(5.790,50.400),(5.740,50.425),
        (5.700,50.445),(5.660,50.455),
        (5.602,50.470),  # Comblain — confluence with Amblève
        (5.580,50.490),(5.568,50.520),(5.548,50.555),
        (5.530,50.588),  # Sauheid
        (5.538,50.598),(5.555,50.608),
        (5.568,50.620),  # Angleur — confluence with Vesdre
        (5.573,50.633),  # joins Meuse
    ],
    # Amblève — flows W from Hautes Fagnes, joins Ourthe at Comblain
    "Amblève": [
        (6.097,50.497),  # Mont-Rigi
        (6.040,50.490),(5.980,50.460),(5.930,50.390),  # Stavelot
        (5.890,50.383),(5.850,50.378),(5.810,50.380),
        (5.771,50.391),  # Targnon
        (5.740,50.405),(5.710,50.430),(5.686,50.490),  # Remouchamps
        (5.660,50.487),(5.635,50.480),(5.610,50.474),
        (5.602,50.470),  # confluence with Ourthe at Comblain
    ],
    # Vesdre — flows W→NW from Eupen, joins Ourthe at Angleur
    "Vesdre": [
        (6.096,50.445),  # Robertville
        (6.070,50.490),(6.048,50.637),  # Eupen
        (5.980,50.633),(5.930,50.628),(5.870,50.618),
        (5.814,50.597),  # Dolhain
        (5.770,50.592),(5.730,50.590),(5.700,50.587),
        (5.638,50.583),  # Chaudfontaine
        (5.610,50.593),(5.590,50.607),(5.570,50.618),
        (5.568,50.620),  # confluence with Ourthe at Angleur
    ],
    # Salm — flows N, joins Amblève at Trois-Ponts
    "Salm": [
        (5.868,50.245),(5.866,50.290),(5.865,50.330),(5.863,50.368),
    ],
    # Méhaigne — flows W through Hesbaye, joins Meuse near Huy
    "Méhaigne": [
        (5.090,50.633),(5.150,50.650),(5.210,50.668),
        (5.256,50.697),(5.320,50.688),(5.380,50.672),
        (5.420,50.658),(5.460,50.638),(5.490,50.618),
    ],
}

RIVER_COLORS = {
    "Meuse":    "#1a3a8b",
    "Ourthe":   "#2980b9",
    "Amblève":  "#27ae60",
    "Vesdre":   "#8e44ad",
    "Salm":     "#16a085",
    "Méhaigne": "#d35400",
}

# ── Load ALL SPW stations with coordinates ───────────────────────────────────
con_all = sqlite3.connect(DB_SPW)
all_stations = {r[0]: (r[1], r[2], r[3])
    for r in con_all.execute("""
        SELECT station_no, lat, lon, station_name
        FROM stations WHERE lat IS NOT NULL AND lat != 0
    """).fetchall()}
con_all.close()

# Key station nos (will get labels)
KEY_SNOS = {s[0] for s in NETWORK}

# ── Load recent errors from forecast_verification.csv ─────────────────────────
print("Loading forecast errors...")
# Only Sauheid (5826) has a forecast model for now
# For other stations use RMSE from observations vs persistence
errors = {}

# Sauheid — from forecast log
fc_log = ROOT / "export/csvs/forecast_log.csv"
ver_log = ROOT / "export/csvs/forecast_verification.csv"

sauheid_error = None
if ver_log.exists():
    try:
        ver = pd.read_csv(str(ver_log), index_col=0, parse_dates=True)
        recent = ver.dropna(subset=["abs_error_t1"]).tail(3)
        if len(recent):
            sauheid_error = float(recent["abs_error_t1"].mean())
            print(f"  SAUHEID mean |error| (last 3d): {sauheid_error:.4f}m")
    except: pass

# For all other stations — compute persistence error (baseline) from recent obs
con = sqlite3.connect(DB_SPW)
for sno, label, river, lon, lat, km in NETWORK:
    rows = con.execute("""
        SELECT timestamp, value FROM observations
        WHERE station_no=? AND parameter='H'
          AND value IS NOT NULL AND value < 10
          AND timestamp >= datetime('now','-4 days')
        ORDER BY timestamp
    """, (sno,)).fetchall()

    if len(rows) > 24:
        s = pd.Series(
            [r[1] for r in rows],
            index=pd.to_datetime([r[0] for r in rows], format="mixed", utc=True)
        )
        s.index = s.index.tz_localize(None)
        s = s[~s.index.duplicated()].resample("1h").mean().dropna()

        # Persistence error = |H(t) - H(t-24h)|
        pers_err = (s - s.shift(24)).abs().dropna()
        if len(pers_err) > 0:
            errors[sno] = {
                "label":      label,
                "river":      river,
                "lon":        lon,
                "lat":        lat,
                "mean_err":   float(pers_err.mean()),
                "max_err":    float(pers_err.max()),
                "H_current":  float(s.iloc[-1]) if len(s) else None,
                "type":       "persistence",
            }

# Override Sauheid with actual model error
if sauheid_error and "5826" in errors:
    errors["5826"]["mean_err"] = sauheid_error
    errors["5826"]["type"]     = "RF-deltaH model"

con.close()

# Compute persistence errors for ALL SPW stations
con_all2 = sqlite3.connect(DB_SPW)
all_errors = {}
all_snos = list(all_stations.keys())
for sno in all_snos:
    lat, lon, name = all_stations[sno]
    if sno in KEY_SNOS:
        continue  # already computed above
    rows = con_all2.execute("""
        SELECT timestamp, value FROM observations
        WHERE station_no=? AND parameter='H'
          AND value IS NOT NULL AND value < 10
          AND timestamp >= datetime('now','-4 days')
        ORDER BY timestamp
    """, (sno,)).fetchall()
    if len(rows) > 12:
        try:
            s = pd.Series(
                [r[1] for r in rows],
                index=pd.to_datetime([r[0] for r in rows],
                                     format="mixed", utc=True)
            )
            s.index = s.index.tz_localize(None)
            s = s[~s.index.duplicated()].resample("1h").mean().dropna()
            pers = (s - s.shift(24)).abs().dropna()
            if len(pers) > 0:
                all_errors[sno] = {
                    "lat": lat, "lon": lon,
                    "mean_err": float(pers.mean()),
                    "H_current": float(s.iloc[-1]),
                }
        except: pass
con_all2.close()
print(f"  Background stations computed: {len(all_errors)}")

print(f"  Errors computed for {len(errors)} stations")
for sno, e in sorted(errors.items(), key=lambda x: x[1]["mean_err"], reverse=True)[:5]:
    print(f"    {e['label']:<20} mean_err={e['mean_err']:.4f}m  ({e['type']})")

# ── Build figure ──────────────────────────────────────────────────────────────
print("\nBuilding map...")

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    HAS_CARTOPY = True
except ImportError:
    HAS_CARTOPY = False
    print("  cartopy not available — using plain matplotlib")

LON_MIN, LON_MAX = 4.90, 6.35
LAT_MIN, LAT_MAX = 50.20, 50.85

fig = plt.figure(figsize=(16, 11), dpi=150)
fig.patch.set_facecolor("#f0f4f8")

if HAS_CARTOPY:
    proj = ccrs.PlateCarree()
    ax   = fig.add_subplot(111, projection=proj)
    ax.set_extent([LON_MIN, LON_MAX, LAT_MIN, LAT_MAX], crs=proj)
    ax.add_feature(cfeature.BORDERS.with_scale("10m"),
                   linewidth=0.6, color="#999", zorder=2)
    ax.add_feature(cfeature.LAND.with_scale("10m"),
                   color="#f5f0e8", zorder=1)
    gl = ax.gridlines(draw_labels=True, linewidth=0.3,
                      color="gray", alpha=0.4, linestyle="--")
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"size": 7}
    gl.ylabel_style = {"size": 7}
    transform = proj
else:
    ax = fig.add_subplot(111)
    ax.set_xlim(LON_MIN, LON_MAX)
    ax.set_ylim(LAT_MIN, LAT_MAX)
    ax.set_facecolor("#f5f0e8")
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3, linewidth=0.3)
    transform = None

def plot(ax, lons, lats, **kwargs):
    if HAS_CARTOPY:
        ax.plot(lons, lats, transform=transform, **kwargs)
    else:
        ax.plot(lons, lats, **kwargs)

def scatter(ax, lons, lats, **kwargs):
    if HAS_CARTOPY:
        ax.scatter(lons, lats, transform=transform, **kwargs)
    else:
        ax.scatter(lons, lats, **kwargs)

def annotate(ax, text, xy, xytext, **kwargs):
    if HAS_CARTOPY:
        ax.annotate(text, xy=xy, xytext=xytext, transform=transform, **kwargs)
    else:
        ax.annotate(text, xy=xy, xytext=xytext, **kwargs)

# Draw rivers — prefer OSM GeoJSON if available, fallback to hardcoded
GEOJSON_FILE = ROOT / "export/maps/rivers_liege.geojson"
if GEOJSON_FILE.exists():
    import json as _json
    gj = _json.loads(GEOJSON_FILE.read_text())
    for feat in gj["features"]:
        rname  = feat["properties"].get("key", feat["properties"].get("name",""))
        color  = RIVER_COLORS.get(rname, "#2980b9")
        lw     = 2.5 if rname=="Meuse" else 1.8 if rname in ["Ourthe","Vesdre"] else 1.2
        geom   = feat["geometry"]
        gtype  = geom["type"]
        # Handle both LineString and MultiLineString
        if gtype == "LineString":
            segs = [geom["coordinates"]]
        else:  # MultiLineString
            segs = geom["coordinates"]
        for seg in segs:
            lons = [c[0] for c in seg]
            lats = [c[1] for c in seg]
            plot(ax, lons, lats, color=color, linewidth=lw,
                 alpha=0.75, zorder=3, solid_capstyle="round")
    print(f"  Using OSM river data ({len(gj['features'])} rivers)")
else:
    # Fallback to hardcoded polylines
    for river_name, pts in RIVERS.items():
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        color = RIVER_COLORS.get(river_name, "#2980b9")
        lw = 2.5 if river_name=="Meuse" else 1.8 if river_name in ["Ourthe","Vesdre"] else 1.2
        plot(ax, lons, lats, color=color, linewidth=lw,
             alpha=0.75, zorder=3, solid_capstyle="round")
    print("  Using hardcoded river polylines (run download_rivers_osm.py for better accuracy)")

# River labels
river_label_pos = {
    "Meuse":    (5.35, 50.54, 0),
    "Ourthe":   (5.62, 50.50, -30),
    "Amblève":  (5.80, 50.42, 10),
    "Vesdre":   (5.82, 50.62, 20),
    "Salm":     (5.87, 50.31, 90),
    "Méhaigne": (5.20, 50.67, 15),
}
for rname, (rlon, rlat, angle) in river_label_pos.items():
    color = RIVER_COLORS.get(rname, "#2980b9")
    if HAS_CARTOPY:
        ax.text(rlon, rlat, rname,
                transform=transform,
                fontsize=8, color=color, fontstyle="italic",
                fontweight="bold", alpha=0.8,
                rotation=angle, zorder=4)
    else:
        ax.text(rlon, rlat, rname, fontsize=8, color=color,
                fontstyle="italic", fontweight="bold",
                alpha=0.8, rotation=angle, zorder=4)

# Colormap for errors
err_vals = [e["mean_err"] for e in errors.values() if e["mean_err"] is not None]
if err_vals:
    vmin = 0
    vmax = max(err_vals) * 1.1
    norm  = mcolors.Normalize(vmin=vmin, vmax=vmax)
    cmap  = cm.RdYlGn_r  # green=low error, red=high error

# Plot ALL background stations — small dots, colour only, no labels
if all_errors:
    all_lons = [v["lon"] for v in all_errors.values()]
    all_lats = [v["lat"] for v in all_errors.values()]
    all_errs = [v["mean_err"] for v in all_errors.values()]
    scatter(ax, all_lons, all_lats,
            s=36, c=all_errs,
            cmap=cmap, norm=norm,
            zorder=5, edgecolors="#555",
            linewidths=0.3, alpha=0.75)

# Plot KEY stations — larger with labels
for sno, e in errors.items():
    lon, lat = e["lon"], e["lat"]
    err  = e["mean_err"]
    H    = e["H_current"]
    color = cmap(norm(err)) if err_vals else "#888"

    # Station dot — size proportional to current H
    size = 80 + (H or 0.5) * 120
    scatter(ax, [lon], [lat], s=size, c=[color],
            cmap=None, norm=None,
            zorder=6, edgecolors="#333", linewidths=0.8)

    # Label
    h_str = f"{H:.2f}m" if H else ""
    err_str = f"{err*100:.1f}cm"
    label_text = f"{e['label']}\n{h_str} | err~{err_str}"

    # Offset labels to avoid overlap
    offsets = {
        "5826": (6, -12),   # SAUHEID
        "7133": (6,   6),   # NEUVILLE
        "6228": (-8, -12),  # CHAUDFONTAINE
        "6387": (6,   6),   # EUPEN
        "6732": (6,  -12),  # STAVELOT
        "5904": (-8,  6),   # COMBLAIN
        "7141": (-8,  6),   # HUY
        "6832": (-8, -12),  # TROIS-PONTS
        "6958": (6,   6),   # ROBERTVILLE
    }
    dx, dy = offsets.get(sno, (6, 6))
    if HAS_CARTOPY:
        ax.annotate(label_text,
                    xy=(lon, lat),
                    xytext=(dx, dy),
                    textcoords="offset points",
                    fontsize=6.5, color="#111",
                    transform=transform,
                    zorder=7,
                    bbox=dict(boxstyle="round,pad=0.2",
                              facecolor="white",
                              edgecolor=color,
                              alpha=0.85,
                              linewidth=0.8))
    else:
        ax.annotate(label_text,
                    xy=(lon, lat),
                    xytext=(dx, dy),
                    textcoords="offset points",
                    fontsize=6.5, color="#111",
                    zorder=7,
                    bbox=dict(boxstyle="round,pad=0.2",
                              facecolor="white",
                              edgecolor=color,
                              alpha=0.85, linewidth=0.8))

# Colorbar
if err_vals:
    sm = cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax,
                        orientation="vertical",
                        fraction=0.025, pad=0.02,
                        shrink=0.6)
    cbar.set_label("Mean |error| / persistence (m)\nlast 3 days",
                   fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    cbar.set_ticks([0, 0.02, 0.05, 0.10, 0.20])
    cbar.set_ticklabels(["0cm", "2cm", "5cm", "10cm", "20cm"])

# River legend
from matplotlib.lines import Line2D
legend_elements = [
    Line2D([0],[0], color=RIVER_COLORS[r], linewidth=2, label=r)
    for r in ["Meuse","Ourthe","Amblève","Vesdre","Salm","Méhaigne"]
]
ax.legend(handles=legend_elements,
          loc="lower left", fontsize=7.5,
          framealpha=0.9, title="Rivers", title_fontsize=8)

# Model note
note = ("● Station size = current H level\n"
        "● Colour = mean forecast error (3-day)\n"
        "● Green = low error  |  Red = high error\n"
        "● SAUHEID: RF-deltaH model error\n"
        "● Others: persistence baseline")
if HAS_CARTOPY:
    ax.text(0.01, 0.98, note,
            transform=ax.transAxes,
            fontsize=6.5, va="top", ha="left",
            bbox=dict(boxstyle="round,pad=0.4",
                      facecolor="white", alpha=0.85,
                      edgecolor="#ccc"))
else:
    ax.text(LON_MIN+0.02, LAT_MAX-0.02, note,
            fontsize=6.5, va="top",
            bbox=dict(boxstyle="round,pad=0.4",
                      facecolor="white", alpha=0.85,
                      edgecolor="#ccc"))

ax.set_title(
    f"Liège Basin — River Network & Forecast Error Map\n"
    f"Mean |error| last 3 days · "
    f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    fontsize=11, pad=8
)

plt.tight_layout()
plt.savefig(OUT_FILE, dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
plt.close()
print(f"\n✓ Saved → {OUT_FILE}")
