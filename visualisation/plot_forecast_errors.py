"""
WWI Forecast Error Analysis
Where and when is forecast error highest?
Analyses both test period (2025) and flood period (2021).

Output: export/maps/forecast_error_analysis.png
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
from pathlib import Path
from datetime import datetime
import sqlite3

ROOT       = Path(__file__).resolve().parent
CSV_FLOOD  = str(ROOT / "export/csvs/predictions_hourly_flood_2021.csv")
CSV_TEST   = str(ROOT / "export/csvs/predictions_hourly_test_2025.csv")
DB_HIST    = str(ROOT / "export/databases/historical_liege.db")
OUT_FILE   = str(ROOT / "export/maps/forecast_error_analysis.png")

print("=" * 60)
print("WWI Forecast Error Analysis")
print("=" * 60)

# ── Load predictions ──────────────────────────────────────────────────────────
flood = pd.read_csv(CSV_FLOOD, index_col=0, parse_dates=True)
flood.index = pd.to_datetime(flood.index).tz_localize(None)

test  = pd.read_csv(CSV_TEST,  index_col=0, parse_dates=True)
test.index  = pd.to_datetime(test.index).tz_localize(None)

print(f"Flood period: {flood.index.min().date()} → {flood.index.max().date()}")
print(f"Test period:  {test.index.min().date()}  → {test.index.max().date()}")

# Compute errors (predicted - observed) — target is H_obs
for df in [flood, test]:
    for col in ["H_pred_t6h","H_pred_t12h","H_pred_t24h"]:
        if col in df.columns and "H_obs" in df.columns:
            df[f"err_{col}"] = df[col] - df["H_obs"]
            df[f"abs_{col}"] = df[f"err_{col}"].abs()

# ── Load upstream H for context ───────────────────────────────────────────────
con = sqlite3.connect(DB_HIST)
upstream = pd.read_sql("""
    SELECT station_no, timestamp, value AS H
    FROM observations
    WHERE parameter='H' AND value IS NOT NULL AND value < 10
    ORDER BY timestamp
""", con, parse_dates=["timestamp"])
con.close()
upstream["timestamp"] = pd.to_datetime(
    upstream["timestamp"], utc=True).dt.tz_localize(None)

def get_H(sno):
    s = upstream[upstream["station_no"]==sno].set_index("timestamp")["H"]
    return s[~s.index.duplicated()].resample("1h").mean()

H_sauheid  = get_H("5826")
H_stavelot = get_H("6732")
H_comblain = get_H("5904")

# ── Build figure ──────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(20, 16), dpi=150)
fig.patch.set_facecolor("#f9f9f7")
gs = gridspec.GridSpec(4, 3, figure=fig,
                       hspace=0.45, wspace=0.30)

C6  = "#e84b4b"
C12 = "#f5960a"
C24 = "#2ea84e"

# ── Panel 1: Error time series — flood period ─────────────────────────────────
ax1 = fig.add_subplot(gs[0, :])
ax1.set_facecolor("#f5f5f2")

obs_flood = H_sauheid["2021-06-14":"2021-09-29"]
ax1_twin  = ax1.twinx()
ax1_twin.fill_between(obs_flood.index, obs_flood.values, 0,
                      alpha=0.08, color="#1a3a6b")
ax1_twin.plot(obs_flood.index, obs_flood.values,
              color="#1a3a6b", linewidth=1.0, alpha=0.4,
              label="Observed H (right)")
ax1_twin.set_ylabel("H observed (m)", fontsize=8, color="#1a3a6b")
ax1_twin.tick_params(colors="#1a3a6b", labelsize=7)

for col, color, label in [
    ("err_H_pred_t6h",  C6,  "Error t+6h"),
    ("err_H_pred_t12h", C12, "Error t+12h"),
    ("err_H_pred_t24h", C24, "Error t+24h"),
]:
    if col in flood.columns:
        ax1.plot(flood.index, flood[col],
                 color=color, linewidth=0.8,
                 alpha=0.7, label=label)

ax1.axhline(0, color="#333", linewidth=0.8, alpha=0.5)
ax1.axhline(+0.1, color="#aaa", linewidth=0.5, linestyle="--", alpha=0.5)
ax1.axhline(-0.1, color="#aaa", linewidth=0.5, linestyle="--", alpha=0.5)
ax1.set_ylabel("Forecast error (m)", fontsize=9)
ax1.set_title("Forecast error during July 2021 flood — error peaks at the rising limb inflection",
              fontsize=10)
ax1.legend(loc="upper left", fontsize=8, ncol=3)
ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
ax1.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
ax1.grid(True, alpha=0.2, linewidth=0.4)
ax1.set_ylim(-1.5, 1.5)

# ── Panel 2: Error vs H level (flood) ────────────────────────────────────────
ax2 = fig.add_subplot(gs[1, 0])
ax2.set_facecolor("#f5f5f2")

H_obs_flood = flood["H_obs"] if "H_obs" in flood.columns     else H_sauheid.reindex(flood.index)

err6 = flood.get("err_H_pred_t6h", pd.Series(dtype=float))
valid = err6.notna() & H_obs_flood.notna()
if valid.sum() > 0:
    sc = ax2.scatter(H_obs_flood[valid], err6[valid],
                     c=err6[valid].abs(), cmap="YlOrRd",
                     s=3, alpha=0.5, vmin=0, vmax=0.5)
    plt.colorbar(sc, ax=ax2, label="|error| (m)", shrink=0.8)
ax2.axhline(0, color="#333", linewidth=0.8)
ax2.set_xlabel("Observed H (m)", fontsize=8)
ax2.set_ylabel("Error t+6h (m)", fontsize=8)
ax2.set_title("Error vs water level\n(flood period)", fontsize=9)
ax2.grid(True, alpha=0.2)

# ── Panel 3: Error vs dH/dt (rising rate) ────────────────────────────────────
ax3 = fig.add_subplot(gs[1, 1])
ax3.set_facecolor("#f5f5f2")

dH_flood = flood["H_obs"].diff(3) if "H_obs" in flood.columns     else H_sauheid["2021-06-14":"2021-09-29"].diff(3).reindex(flood.index)
valid2 = err6.notna() & dH_flood.notna()
if valid2.sum() > 0:
    sc3 = ax3.scatter(dH_flood[valid2], err6[valid2],
                      c=err6[valid2].abs(), cmap="YlOrRd",
                      s=3, alpha=0.5, vmin=0, vmax=0.5)
    plt.colorbar(sc3, ax=ax3, label="|error| (m)", shrink=0.8)
ax3.axhline(0, color="#333", linewidth=0.8)
ax3.axvline(0, color="#333", linewidth=0.5, linestyle="--", alpha=0.4)
ax3.set_xlabel("ΔH/3h (m) — rise rate", fontsize=8)
ax3.set_ylabel("Error t+6h (m)", fontsize=8)
ax3.set_title("Error vs rise rate\n(steeper rise = larger error)", fontsize=9)
ax3.grid(True, alpha=0.2)

# ── Panel 4: Error by month/hour (flood) ─────────────────────────────────────
ax4 = fig.add_subplot(gs[1, 2])
ax4.set_facecolor("#f5f5f2")

abs6 = flood.get("abs_H_pred_t6h", pd.Series(dtype=float))
if abs6.notna().sum() > 0:
    hourly_err = abs6.groupby(flood.index.hour).mean()
    ax4.bar(hourly_err.index, hourly_err.values,
            color=C6, alpha=0.8, edgecolor="#333", linewidth=0.3)
ax4.set_xlabel("Hour of day (UTC)", fontsize=8)
ax4.set_ylabel("Mean |error| t+6h (m)", fontsize=8)
ax4.set_title("Error by hour of day\n(any diurnal pattern?)", fontsize=9)
ax4.grid(True, alpha=0.2, axis="y")

# ── Panel 5: Error percentiles — flood ───────────────────────────────────────
ax5 = fig.add_subplot(gs[2, 0])
ax5.set_facecolor("#f5f5f2")

for col, color, label in [
    ("abs_H_pred_t6h",  C6,  "t+6h"),
    ("abs_H_pred_t12h", C12, "t+12h"),
    ("abs_H_pred_t24h", C24, "t+24h"),
]:
    if col in flood.columns:
        vals = flood[col].dropna().sort_values()
        pcts = np.linspace(0, 100, len(vals))
        ax5.plot(vals, pcts, color=color, linewidth=2, label=label)

ax5.axvline(0.05, color="#aaa", linewidth=0.8, linestyle="--", alpha=0.6)
ax5.axvline(0.10, color="#888", linewidth=0.8, linestyle="--", alpha=0.6)
ax5.axvline(0.20, color="#555", linewidth=0.8, linestyle="--", alpha=0.6)
ax5.text(0.05, 5, "5cm", fontsize=7, color="#888")
ax5.text(0.10, 5, "10cm", fontsize=7, color="#888")
ax5.text(0.20, 5, "20cm", fontsize=7, color="#555")
ax5.set_xlabel("|Error| (m)", fontsize=8)
ax5.set_ylabel("Percentile", fontsize=8)
ax5.set_title("Error CDF — flood 2021\n(% of hours within X cm)", fontsize=9)
ax5.legend(fontsize=8)
ax5.grid(True, alpha=0.2)
ax5.set_xlim(0, 0.6)

# ── Panel 6: Error percentiles — test ────────────────────────────────────────
ax6 = fig.add_subplot(gs[2, 1])
ax6.set_facecolor("#f5f5f2")

for col, color, label in [
    ("abs_H_pred_t6h",  C6,  "t+6h"),
    ("abs_H_pred_t12h", C12, "t+12h"),
    ("abs_H_pred_t24h", C24, "t+24h"),
]:
    if col in test.columns:
        vals = test[col].dropna().sort_values()
        pcts = np.linspace(0, 100, len(vals))
        ax6.plot(vals, pcts, color=color, linewidth=2, label=label)

ax6.axvline(0.05, color="#aaa", linewidth=0.8, linestyle="--", alpha=0.6)
ax6.axvline(0.10, color="#888", linewidth=0.8, linestyle="--", alpha=0.6)
ax6.set_xlabel("|Error| (m)", fontsize=8)
ax6.set_ylabel("Percentile", fontsize=8)
ax6.set_title("Error CDF — test 2025\n(routine low-flow conditions)", fontsize=9)
ax6.legend(fontsize=8)
ax6.grid(True, alpha=0.2)
ax6.set_xlim(0, 0.3)

# ── Panel 7: RMSE by flow regime ──────────────────────────────────────────────
ax7 = fig.add_subplot(gs[2, 2])
ax7.set_facecolor("#f5f5f2")

# Bin by H level
H_bins  = [0, 0.5, 1.0, 1.5, 2.5, 5.0]
bin_labels = ["<0.5m\n(low flow)", "0.5-1m\n(normal)", "1-1.5m\n(elevated)",
              "1.5-2.5m\n(watch)", ">2.5m\n(flood)"]
H_obs_f = flood["H_obs"] if "H_obs" in flood.columns     else H_sauheid.reindex(flood.index)
rmse_by_bin = []
for i in range(len(H_bins)-1):
    mask = (H_obs_f >= H_bins[i]) & (H_obs_f < H_bins[i+1])
    e = flood.get("abs_H_pred_t6h", pd.Series(dtype=float))
    vals = e[mask & e.notna()]
    rmse_by_bin.append(vals.mean() if len(vals) > 0 else 0)

colors_bin = [C24, C24, C12, C12, C6]
bars = ax7.bar(range(len(bin_labels)), rmse_by_bin,
               color=colors_bin, alpha=0.85,
               edgecolor="#333", linewidth=0.5)
for bar, val in zip(bars, rmse_by_bin):
    if val > 0:
        ax7.text(bar.get_x() + bar.get_width()/2,
                 bar.get_height() + 0.003,
                 f"{val:.3f}m", ha="center", va="bottom", fontsize=8)

ax7.set_xticks(range(len(bin_labels)))
ax7.set_xticklabels(bin_labels, fontsize=7.5)
ax7.set_ylabel("Mean |error| t+6h (m)", fontsize=8)
ax7.set_title("RMSE by flow regime\n(flood period 2021)", fontsize=9)
ax7.grid(True, alpha=0.2, axis="y")

# ── Panel 8: Summary stats table ─────────────────────────────────────────────
ax8 = fig.add_subplot(gs[3, :])
ax8.set_facecolor("#f5f5f2")
ax8.axis("off")

def stats(df, col):
    # col is like "abs_H_pred_t6h" — map to err_ for signed stats
    err_col = col.replace("abs_", "err_")
    e_abs = df.get(col,     pd.Series(dtype=float)).dropna()
    e_sig = df.get(err_col, pd.Series(dtype=float)).dropna()
    e = e_abs  # use absolute for RMSE/MAE/P90
    e_bias = e_sig
    if len(e) == 0:
        return ["N/A"] * 6
    rmse  = np.sqrt((e**2).mean())
    mae   = e.mean()
    p90   = e.quantile(0.90)
    bias  = e_bias.mean() if len(e_bias) > 0 else 0
    pct5  = (e < 0.05).mean() * 100
    pct10 = (e < 0.10).mean() * 100
    return [f"{rmse:.4f}", f"{mae:.4f}", f"{p90:.4f}",
            f"{bias:+.4f}", f"{pct5:.0f}%", f"{pct10:.0f}%"]

rows = [
    ["Flood 2021 — t+6h"]  + stats(flood, "abs_H_pred_t6h"),
    ["Flood 2021 — t+12h"] + stats(flood, "abs_H_pred_t12h"),
    ["Flood 2021 — t+24h"] + stats(flood, "abs_H_pred_t24h"),
    ["Test 2025 — t+6h"]   + stats(test,  "abs_H_pred_t6h"),
    ["Test 2025 — t+12h"]  + stats(test,  "abs_H_pred_t12h"),
    ["Test 2025 — t+24h"]  + stats(test,  "abs_H_pred_t24h"),
]

cols = ["Period/Horizon", "RMSE (m)", "MAE (m)", "P90 error",
        "Bias (m)", "% within 5cm", "% within 10cm"]

table = ax8.table(
    cellText=rows,
    colLabels=cols,
    cellLoc="center",
    loc="center",
    bbox=[0, 0, 1, 1]
)
table.auto_set_font_size(False)
table.set_fontsize(9)
for (row, col), cell in table.get_celld().items():
    if row == 0:
        cell.set_facecolor("#2c3e50")
        cell.set_text_props(color="white", fontweight="bold")
    elif "Flood" in str(rows[row-1][0]) if row > 0 else False:
        cell.set_facecolor("#fff0ee")
    else:
        cell.set_facecolor("#f0fff0")
    cell.set_edgecolor("#ddd")

ax8.set_title("Error statistics summary", fontsize=10, pad=8)

fig.suptitle(
    "Wallonia Water Intelligence Platform — Hourly Forecast Error Analysis\n"
    f"Generated {datetime.now().strftime('%Y-%m-%d')}",
    fontsize=13, y=1.01
)
plt.savefig(OUT_FILE, dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
plt.close()
print(f"\n✓ Saved → {OUT_FILE}")
