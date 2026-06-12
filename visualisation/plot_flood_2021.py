"""
WWI — July 2021 Flood Retrospective
Plots observed vs predicted H at Sauheid (Ourthe)
for the July 2021 catastrophic flood event.

Uses predictions_hourly_flood_2021.csv already generated
by train_model_hourly.py.

Output: export/maps/flood_2021_retrospective.png
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from pathlib import Path
from datetime import datetime
import sqlite3

ROOT     = Path(__file__).resolve().parent.parent
CSV_PRED = str(ROOT / "export/csvs/predictions_hourly_flood_2021.csv")
DB_HIST  = str(ROOT / "export/databases/historical_liege.db")
OUT_DIR  = ROOT / "export/maps"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = str(OUT_DIR / "flood_2021_retrospective.png")

print("=" * 60)
print("WWI — July 2021 Flood Retrospective")
print("=" * 60)

# ── Load predictions ──────────────────────────────────────────────────────────
print("\nLoading predictions...")
pred = pd.read_csv(CSV_PRED, index_col=0, parse_dates=True)
pred.index = pd.to_datetime(pred.index).tz_localize(None)
print(f"  Shape: {pred.shape}")
print(f"  Range: {pred.index.min().date()} → {pred.index.max().date()}")
print(f"  Columns: {list(pred.columns)}")

# ── Load upstream H from historical DB ───────────────────────────────────────
print("\nLoading upstream H...")
con = sqlite3.connect(DB_HIST)
upstream = pd.read_sql("""
    SELECT station_no, timestamp, value AS H
    FROM observations
    WHERE parameter = 'H'
      AND value IS NOT NULL
      AND value < 10
      AND timestamp >= '2021-06-14'
      AND timestamp <= '2021-09-30'
    ORDER BY timestamp
""", con, parse_dates=["timestamp"])
con.close()

upstream["timestamp"] = pd.to_datetime(
    upstream["timestamp"], utc=True).dt.tz_localize(None)

def get_station(sno):
    s = upstream[upstream["station_no"] == sno].set_index("timestamp")["H"]
    return s[~s.index.duplicated()].resample("1h").mean()

H_eupen      = get_station("6387")
H_stavelot   = get_station("6732")
H_comblain   = get_station("5904")
H_sauheid_obs = get_station("5826")

print(f"  EUPEN: {H_eupen.notna().sum()} hours")
print(f"  STAVELOT: {H_stavelot.notna().sum()} hours")
print(f"  COMBLAIN: {H_comblain.notna().sum()} hours")
print(f"  SAUHEID: {H_sauheid_obs.notna().sum()} hours")

# ── Focus window — the flood event ───────────────────────────────────────────
FLOOD_START = "2021-07-10"
FLOOD_END   = "2021-07-22"
FULL_START  = "2021-06-14"
FULL_END    = "2021-09-29"

# ── Build figure ──────────────────────────────────────────────────────────────
print("\nBuilding figure...")

fig = plt.figure(figsize=(18, 14), dpi=150)
fig.patch.set_facecolor("#f9f9f7")

gs = GridSpec(4, 2, figure=fig,
              height_ratios=[2.5, 1.2, 1.2, 1.2],
              hspace=0.45, wspace=0.12)

# Colour scheme
C_OBS   = "#1a3a6b"   # dark blue — observed
C_T6    = "#e84b4b"   # red — t+6h forecast
C_T12   = "#f5960a"   # orange — t+12h
C_T24   = "#2ea84e"   # green — t+24h
C_FLOOD = "#ffcccc"   # light red — flood zone
C_WATCH = "#fff3cc"   # yellow — watch zone

# Alert thresholds (SPW)
TH_WATCH    = 1.50
TH_ELEVATED = 2.50
TH_EMERGENCY= 3.50

# ── Panel 1 (top, full width): Main forecast vs observed ─────────────────────
ax_main = fig.add_subplot(gs[0, :])

# Focus on the flood event core
mask = (pred.index >= FLOOD_START) & (pred.index <= FLOOD_END)
pred_f = pred[mask]
obs_f  = H_sauheid_obs[FLOOD_START:FLOOD_END]

# Alert zones
ax_main.axhspan(TH_EMERGENCY, 5.5,
                color="#ff8888", alpha=0.15, zorder=0)
ax_main.axhspan(TH_ELEVATED, TH_EMERGENCY,
                color="#ffaa88", alpha=0.15, zorder=0)
ax_main.axhspan(TH_WATCH, TH_ELEVATED,
                color="#ffee88", alpha=0.15, zorder=0)

# Threshold lines
for th, label, color in [
    (TH_EMERGENCY, "Emergency (3.5m)", "#cc0000"),
    (TH_ELEVATED,  "Elevated (2.5m)",  "#cc6600"),
    (TH_WATCH,     "Watch (1.5m)",     "#ccaa00"),
]:
    ax_main.axhline(th, color=color, linewidth=0.8,
                    linestyle="--", alpha=0.7, zorder=1)
    ax_main.text(pred_f.index[-1], th + 0.05, label,
                 color=color, fontsize=7.5, va="bottom", ha="right")

# Forecast bands
if "H_pred_t24h" in pred_f.columns:
    ax_main.fill_between(pred_f.index,
                          pred_f["H_pred_t6h"],
                          pred_f["H_pred_t24h"],
                          alpha=0.12, color=C_T12,
                          label="t+6h → t+24h envelope")

# Forecast lines
for col, color, label, lw in [
    ("H_pred_t24h", C_T24, "Forecast t+24h", 1.5),
    ("H_pred_t12h", C_T12, "Forecast t+12h", 1.8),
    ("H_pred_t6h",  C_T6,  "Forecast t+6h",  2.0),
]:
    if col in pred_f.columns:
        ax_main.plot(pred_f.index, pred_f[col],
                     color=color, linewidth=lw,
                     label=label, alpha=0.85, zorder=3)

# Observed
ax_main.plot(obs_f.index, obs_f.values,
             color=C_OBS, linewidth=2.5,
             label="Observed (SPW)", zorder=4)

# Peak annotation
if obs_f.notna().any():
    peak_idx = obs_f.idxmax()
    peak_val = obs_f.max()
    ax_main.annotate(
        f"Peak: {peak_val:.2f}m\n{peak_idx.strftime('%d %b %H:%M')}",
        xy=(peak_idx, peak_val),
        xytext=(peak_idx - pd.Timedelta(hours=18), peak_val - 0.4),
        fontsize=9, color="#aa0000", fontweight="bold",
        arrowprops=dict(arrowstyle="->", color="#aa0000", lw=1.2),
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                  edgecolor="#aa0000", alpha=0.9),
    )

ax_main.set_ylabel("Water level H (m)", fontsize=10)
ax_main.set_title(
    "July 2021 Meuse Flood — Ourthe at Sauheid: Observed vs Hourly RF-deltaH Forecast\n"
    "NSE=0.981 (t+6h) · NSE=0.878 (t+12h) · NSE=0.603 (t+24h)",
    fontsize=12, pad=8
)
ax_main.legend(loc="upper left", fontsize=8.5, framealpha=0.9,
               ncol=2)
ax_main.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
ax_main.xaxis.set_major_locator(mdates.DayLocator())
ax_main.grid(True, alpha=0.25, linewidth=0.5)
ax_main.set_facecolor("#f5f5f2")

# ── Panel 2: Forecast error ───────────────────────────────────────────────────
ax_err = fig.add_subplot(gs[1, :])

if "H_pred_t6h" in pred_f.columns and obs_f.notna().any():
    err6  = pred_f["H_pred_t6h"]  - obs_f.reindex(pred_f.index)
    err12 = pred_f["H_pred_t12h"] - obs_f.reindex(pred_f.index)
    err24 = pred_f["H_pred_t24h"] - obs_f.reindex(pred_f.index)

    ax_err.fill_between(pred_f.index, err24, 0,
                         alpha=0.2, color=C_T24)
    ax_err.plot(pred_f.index, err24,
                color=C_T24, linewidth=1.2, alpha=0.6, label="Error t+24h")
    ax_err.plot(pred_f.index, err12,
                color=C_T12, linewidth=1.5, alpha=0.8, label="Error t+12h")
    ax_err.plot(pred_f.index, err6,
                color=C_T6,  linewidth=2.0, label="Error t+6h")
    ax_err.axhline(0, color="#333", linewidth=0.8)

ax_err.set_ylabel("Error (m)", fontsize=9)
ax_err.set_title("Forecast error (predicted − observed)", fontsize=9)
ax_err.legend(fontsize=8, loc="upper left")
ax_err.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
ax_err.xaxis.set_major_locator(mdates.DayLocator())
ax_err.grid(True, alpha=0.25, linewidth=0.5)
ax_err.set_facecolor("#f5f5f2")

# ── Panel 3 (bottom left): Upstream cascade ───────────────────────────────────
ax_up = fig.add_subplot(gs[2, 0])

for H_s, label, color in [
    (H_eupen,    "EUPEN (Vesdre)",    "#8b4513"),
    (H_stavelot, "STAVELOT (Amblève)","#556b2f"),
    (H_comblain, "COMBLAIN (Ourthe)", "#4682b4"),
]:
    s = H_s[FLOOD_START:FLOOD_END]
    if s.notna().any():
        # Normalise to 0-1 for comparison
        s_norm = (s - s.min()) / (s.max() - s.min() + 1e-6)
        ax_up.plot(s.index, s_norm,
                   label=f"{label} ({s.max():.1f}m peak)",
                   linewidth=1.8, alpha=0.85)

ax_up.set_title("Upstream cascade (normalised)", fontsize=9)
ax_up.set_ylabel("Normalised H", fontsize=8)
ax_up.legend(fontsize=7.5, loc="upper left")
ax_up.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
ax_up.xaxis.set_major_locator(mdates.DayLocator(interval=2))
ax_up.grid(True, alpha=0.25, linewidth=0.5)
ax_up.set_facecolor("#f5f5f2")

# ── Panel 4 (bottom right): Full season context ───────────────────────────────
ax_ctx = fig.add_subplot(gs[2, 1])

obs_full = H_sauheid_obs[FULL_START:FULL_END]
pred_full = pred["H_pred_t6h"] if "H_pred_t6h" in pred.columns else None

ax_ctx.fill_between(obs_full.index, obs_full.values, 0,
                     alpha=0.15, color=C_OBS)
ax_ctx.plot(obs_full.index, obs_full.values,
            color=C_OBS, linewidth=1.2, label="Observed", alpha=0.9)
if pred_full is not None:
    ax_ctx.plot(pred_full.index, pred_full.values,
                color=C_T6, linewidth=1.0,
                label="Forecast t+6h", alpha=0.7)

# Highlight flood window
ax_ctx.axvspan(pd.Timestamp(FLOOD_START), pd.Timestamp(FLOOD_END),
               alpha=0.12, color="red", label="Flood event")
ax_ctx.axhline(TH_WATCH, color="#ccaa00", linewidth=0.8,
               linestyle="--", alpha=0.7)

ax_ctx.set_title("Full period context (Jun–Sep 2021)", fontsize=9)
ax_ctx.set_ylabel("H (m)", fontsize=8)
ax_ctx.legend(fontsize=7.5, loc="upper right")
ax_ctx.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
ax_ctx.xaxis.set_major_locator(mdates.MonthLocator())
ax_ctx.grid(True, alpha=0.25, linewidth=0.5)
ax_ctx.set_facecolor("#f5f5f2")

# ── Panel 5+6 (bottom): NSE skill metrics bar ────────────────────────────────
ax_nse = fig.add_subplot(gs[3, :])

horizons   = ["t+6h",  "t+12h", "t+24h", "Daily t+24h\n(reference)"]
nse_flood  = [0.981,    0.878,   0.603,   0.670]
nse_test   = [0.998,    0.988,   0.935,   0.975]
colors_f   = [C_T6,     C_T12,   C_T24,   "#aaaaaa"]

x = np.arange(len(horizons))
w = 0.35
bars1 = ax_nse.bar(x - w/2, nse_test,  w,
                    label="Test 2025 NSE",
                    color=colors_f, alpha=0.6, edgecolor="#333",
                    linewidth=0.6)
bars2 = ax_nse.bar(x + w/2, nse_flood, w,
                    label="Flood 2021 NSE",
                    color=colors_f, alpha=0.95, edgecolor="#333",
                    linewidth=0.6)

for bar in bars1:
    ax_nse.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + 0.005,
                f"{bar.get_height():.3f}",
                ha="center", va="bottom", fontsize=8, color="#444")
for bar in bars2:
    ax_nse.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + 0.005,
                f"{bar.get_height():.3f}",
                ha="center", va="bottom", fontsize=8.5,
                fontweight="bold", color="#111")

ax_nse.axhline(0.75, color="#2ea84e", linewidth=0.8,
               linestyle="--", alpha=0.7, label="NSE=0.75 (excellent)")
ax_nse.axhline(0.50, color="#f5960a", linewidth=0.8,
               linestyle="--", alpha=0.7, label="NSE=0.50 (good)")
ax_nse.set_xticks(x)
ax_nse.set_xticklabels(horizons, fontsize=9)
ax_nse.set_ylabel("NSE", fontsize=9)
ax_nse.set_ylim(0, 1.08)
ax_nse.set_title("Model skill — hourly RF-deltaH vs daily baseline",
                  fontsize=9)
ax_nse.legend(fontsize=8, loc="lower right", ncol=2)
ax_nse.grid(True, alpha=0.25, linewidth=0.5, axis="y")
ax_nse.set_facecolor("#f5f5f2")

# ── Suptitle ──────────────────────────────────────────────────────────────────
fig.suptitle(
    "Wallonia Water Intelligence Platform — July 2021 Flood Retrospective\n"
    "Hourly RF-deltaH model · Ourthe at Sauheid · "
    f"Generated {datetime.now().strftime('%Y-%m-%d')}",
    fontsize=13, fontweight="normal", y=1.01
)

plt.savefig(OUT_FILE, dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
plt.close()
print(f"\n✓ Saved → {OUT_FILE}")
