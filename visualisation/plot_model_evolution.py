"""
WWI Model Evolution Plot
Shows NSE improvement across model versions.
Output: export/maps/model_evolution.png
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from pathlib import Path
from datetime import datetime

ROOT     = Path(__file__).resolve().parent
OUT_FILE = str(ROOT / "export/maps/model_evolution.png")

# ── Data ──────────────────────────────────────────────────────────────────────
versions = [
    ("v1.0\nBaseline RF\n(absolute H)",   "daily",  0.953, 0.506),
    ("v1.1\nRF delta-H",                  "daily",  0.974, 0.670),
    ("v1.2\n+swvl1+NDVI",                 "daily",  0.975, 0.671),
    ("v1.3\n+CORINE+slope",               "daily",  0.975, 0.670),
    ("hourly_v1\nt+24h",                  "hourly", 0.935, 0.603),
    ("hourly_v1\nt+12h",                  "hourly", 0.988, 0.878),
    ("hourly_v1\nt+6h",                   "hourly", 0.998, 0.981),
]

labels    = [v[0] for v in versions]
res       = [v[1] for v in versions]
nse_test  = [v[2] for v in versions]
nse_flood = [v[3] for v in versions]

x = np.arange(len(labels))
w = 0.35

# Colours
C_DAILY  = "#4a90d9"
C_HOURLY = "#e84b4b"
C_FLOOD  = "#f5960a"
C_FLOOD_H= "#cc3300"

colors_test  = [C_DAILY  if r=="daily" else C_HOURLY for r in res]
colors_flood = [C_FLOOD  if r=="daily" else C_FLOOD_H for r in res]

fig, axes = plt.subplots(1, 2, figsize=(16, 7), dpi=150)
fig.patch.set_facecolor("#f9f9f7")

# ── Panel 1: Grouped bar chart ────────────────────────────────────────────────
ax = axes[0]
ax.set_facecolor("#f5f5f2")

bars1 = ax.bar(x - w/2, nse_test,  w, color=colors_test,
               alpha=0.75, edgecolor="#333", linewidth=0.6,
               label="Test NSE (2025)")
bars2 = ax.bar(x + w/2, nse_flood, w, color=colors_flood,
               alpha=0.95, edgecolor="#333", linewidth=0.6,
               label="Flood NSE (Jul 2021)")

# Value labels
for bar, val in zip(bars1, nse_test):
    ax.text(bar.get_x() + bar.get_width()/2,
            bar.get_height() + 0.005,
            f"{val:.3f}", ha="center", va="bottom",
            fontsize=7.5, color="#333")
for bar, val in zip(bars2, nse_flood):
    ax.text(bar.get_x() + bar.get_width()/2,
            bar.get_height() + 0.005,
            f"{val:.3f}", ha="center", va="bottom",
            fontsize=8.5, fontweight="bold", color="#111")

# Reference lines
ax.axhline(0.75, color="#2ea84e", linewidth=1.0,
           linestyle="--", alpha=0.6, label="NSE=0.75 (excellent)")
ax.axhline(0.50, color="#999",    linewidth=0.8,
           linestyle=":",  alpha=0.5, label="NSE=0.50 (good)")

# Shade hourly region
ax.axvspan(3.5, len(labels)-0.5, alpha=0.06, color=C_HOURLY,
           label="Hourly models")
ax.text(5.0, 0.52, "hourly\nresolution",
        color=C_HOURLY, fontsize=8, ha="center", alpha=0.8)

ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
ax.set_ylabel("NSE", fontsize=10)
ax.set_ylim(0.45, 1.05)
ax.set_title("Model skill evolution\nTest 2025 vs Flood 2021 out-of-sample",
             fontsize=11)
ax.legend(fontsize=8, loc="lower right")
ax.grid(True, axis="y", alpha=0.25, linewidth=0.5)

# ── Panel 2: Improvement trajectory ──────────────────────────────────────────
ax2 = axes[1]
ax2.set_facecolor("#f5f5f2")

# Daily model trajectory
daily_versions = [v for v in versions if v[1]=="daily"]
daily_labels   = [v[0].split("\n")[0] for v in daily_versions]
daily_test     = [v[2] for v in daily_versions]
daily_flood    = [v[3] for v in daily_versions]

ax2.plot(range(len(daily_versions)), daily_test,
         "o-", color=C_DAILY, linewidth=2, markersize=8,
         label="Daily — Test NSE")
ax2.plot(range(len(daily_versions)), daily_flood,
         "s-", color=C_FLOOD, linewidth=2, markersize=8,
         label="Daily — Flood NSE")

# Hourly model points (plotted separately on right)
hourly_versions = [v for v in versions if v[1]=="hourly"]
hourly_x = [len(daily_versions) + 0.5,
             len(daily_versions) + 1.5,
             len(daily_versions) + 2.5]
hourly_labels_short = ["t+24h","t+12h","t+6h"]
hourly_test  = [v[2] for v in hourly_versions]
hourly_flood = [v[3] for v in hourly_versions]

ax2.plot(hourly_x, hourly_test,
         "o--", color=C_HOURLY, linewidth=2, markersize=8,
         label="Hourly — Test NSE")
ax2.plot(hourly_x, hourly_flood,
         "s--", color=C_FLOOD_H, linewidth=2.5, markersize=9,
         label="Hourly — Flood NSE")

# Annotate key improvements
ax2.annotate("Structural fix:\ndelta-H formulation\n+0.164 flood NSE",
             xy=(1, 0.670), xytext=(1.5, 0.58),
             fontsize=7.5, color="#333",
             arrowprops=dict(arrowstyle="->", color="#555", lw=0.8),
             bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                       edgecolor="#aaa", alpha=0.8))

ax2.annotate("Resolution fix:\nhourly model\n+0.311 flood NSE (t+6h)",
             xy=(hourly_x[2], 0.981),
             xytext=(hourly_x[1]-0.8, 0.82),
             fontsize=7.5, color="#cc3300",
             arrowprops=dict(arrowstyle="->", color="#cc3300", lw=0.8),
             bbox=dict(boxstyle="round,pad=0.2", facecolor="white",
                       edgecolor="#cc3300", alpha=0.8))

# Ceiling annotation
ax2.axhline(0.975, color=C_DAILY, linewidth=0.8,
            linestyle=":", alpha=0.5)
ax2.text(2.5, 0.978, "daily model ceiling\n(feature engineering plateau)",
         fontsize=7, color=C_DAILY, alpha=0.8)

# Divider between daily and hourly
ax2.axvline(len(daily_versions)-0.3, color="#aaa",
            linewidth=1, linestyle="--", alpha=0.5)
ax2.text(len(daily_versions)+0.1, 0.52,
         "← daily models  |  hourly models →",
         fontsize=7.5, color="#666")

all_x      = list(range(len(daily_versions))) + hourly_x
all_labels = daily_labels + hourly_labels_short
ax2.set_xticks(all_x)
ax2.set_xticklabels(all_labels, fontsize=8)
ax2.set_ylabel("NSE", fontsize=10)
ax2.set_ylim(0.45, 1.05)
ax2.set_title("Improvement trajectory\nKey: structural decisions > feature engineering",
              fontsize=11)
ax2.legend(fontsize=8, loc="lower right")
ax2.grid(True, alpha=0.25, linewidth=0.5)

fig.suptitle(
    "Wallonia Water Intelligence Platform — Model Evolution\n"
    f"Generated {datetime.now().strftime('%Y-%m-%d')}",
    fontsize=13, y=1.01
)
plt.tight_layout()
plt.savefig(OUT_FILE, dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
plt.close()
print(f"✓ Saved → {OUT_FILE}")
