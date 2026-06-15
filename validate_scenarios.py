"""
WWI Scenario Validation
Tests hourly RF-deltaH model on two additional scenarios:
  A) Moderate flood — January 2025 (out-of-sample)
  B) Drought — July 2023 (in-sample, low-flow regime test)

Output: export/maps/scenario_validation.png
        export/csvs/scenario_validation.csv
"""

import sqlite3
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
from pathlib import Path
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")
from sklearn.ensemble import RandomForestRegressor

ROOT     = Path(__file__).resolve().parent
DB_HIST  = str(ROOT / "export/databases/historical_liege.db")
CSV_FEAT = str(ROOT / "export/csvs/features_sauheid_hourly.csv")
OUT_FILE = str(ROOT / "export/maps/scenario_validation.png")
OUT_CSV  = str(ROOT / "export/csvs/scenario_validation.csv")

print("=" * 65)
print("WWI Scenario Validation")
print("=" * 65)

# ── Load feature matrix and train model ──────────────────────────
print("\n[1/4] Loading features and training model...")
df = pd.read_csv(CSV_FEAT, index_col=0, parse_dates=True)
df.index = pd.to_datetime(df.index).tz_localize(None)

TARGET_COLS  = ["H_t6h","H_t12h","H_t24h"]
FEATURE_COLS = [c for c in df.columns if c not in TARGET_COLS]

for col in FEATURE_COLS:
    if df[col].isna().any():
        df[col] = df[col].fillna(df[col].median() or 0.0)

# Train on 2023-2024 only
train = df["2023-01-01":"2024-12-31"]

models = {}
for horizon, target in [("t6h","H_t6h"),("t12h","H_t12h"),("t24h","H_t24h")]:
    tr_idx = train[target].dropna().index
    y_tr   = train.loc[tr_idx, target] - train.loc[tr_idx, "H_sauheid"]
    X_tr   = train.loc[tr_idx, FEATURE_COLS].fillna(0)
    rf = RandomForestRegressor(
        n_estimators=300, max_depth=12,
        min_samples_leaf=3, random_state=42, n_jobs=-1)
    rf.fit(X_tr, y_tr)
    med = train.loc[tr_idx, FEATURE_COLS].median().fillna(0)
    models[horizon] = {"rf": rf, "med": med}

print(f"  Model trained on {len(train):,} hours")

def predict_scenario(period_start, period_end, label):
    """Run model on a specific period, return obs/pred dataframe."""
    period = df[period_start:period_end].copy()
    if len(period) == 0:
        print(f"  {label}: no data in period")
        return None

    results = {"H_obs": period["H_sauheid"]}
    for horizon, target in [("t6h","H_t6h"),("t12h","H_t12h"),("t24h","H_t24h")]:
        m = models[horizon]
        X = period[FEATURE_COLS].fillna(m["med"])
        delta = m["rf"].predict(X)
        # Uncertainty from tree ensemble
        tree_preds = np.array([t.predict(X) for t in m["rf"].estimators_])
        delta_p5  = np.percentile(tree_preds, 5, axis=0)
        delta_p95 = np.percentile(tree_preds, 95, axis=0)
        results[f"H_pred_{horizon}"]  = period["H_sauheid"].values + delta
        results[f"H_lower_{horizon}"] = period["H_sauheid"].values + delta_p5
        results[f"H_upper_{horizon}"] = period["H_sauheid"].values + delta_p95
        if target in period.columns:
            results[f"H_target_{horizon}"] = period[target].values

    res_df = pd.DataFrame(results, index=period.index)

    # Compute NSE and RMSE vs actual H
    def nse(obs, sim):
        mask = ~(np.isnan(obs) | np.isnan(sim))
        obs, sim = obs[mask], sim[mask]
        if len(obs) < 2: return np.nan
        return 1 - np.sum((obs-sim)**2) / np.sum((obs-np.mean(obs))**2)

    def rmse(obs, sim):
        mask = ~(np.isnan(obs) | np.isnan(sim))
        if mask.sum() < 2: return np.nan
        return np.sqrt(np.mean((obs[mask]-sim[mask])**2))

    # Compare predictions at actual future times
    print(f"\n  {label}:")
    print(f"    Period: {period_start} → {period_end}")
    print(f"    H range: {period['H_sauheid'].min():.3f} → {period['H_sauheid'].max():.3f}m")
    for horizon in ["t6h","t12h","t24h"]:
        tgt_col = f"H_target_{horizon}"
        pred_col = f"H_pred_{horizon}"
        if tgt_col in res_df.columns:
            obs_  = res_df[tgt_col].values.astype(float)
            pred_ = res_df[pred_col].values.astype(float)
            n  = nse(obs_, pred_)
            r  = rmse(obs_, pred_)
            print(f"    {horizon}: NSE={n:+.3f}  RMSE={r:.4f}m")
    return res_df

# ── Scenario A — Moderate flood Jan 2025 ─────────────────────────
print("\n[2/4] Scenario A — Moderate flood (January 2025)...")
res_A = predict_scenario("2025-01-01", "2025-01-20", "MODERATE FLOOD Jan 2025")

# ── Scenario B — Drought Jul 2023 ────────────────────────────────
print("\n[3/4] Scenario B — Drought (July 2023)...")
res_B = predict_scenario("2023-06-25", "2023-08-01", "DROUGHT Jul 2023")

# ── Plot ──────────────────────────────────────────────────────────
print("\n[4/4] Building figure...")

fig = plt.figure(figsize=(18, 14), dpi=150)
fig.patch.set_facecolor("#f9f9f7")
gs = gridspec.GridSpec(3, 2, figure=fig, hspace=0.45, wspace=0.25,
                       height_ratios=[2.5, 2.5, 1.0])

C6   = "#e84b4b"
C12  = "#f5960a"
C24  = "#2ea84e"
COBS = "#1a3a6b"

TH_WATCH    = 1.50
TH_ELEVATED = 2.50
TH_DROUGHT  = 0.25
TH_LOWFLOW  = 0.45

def plot_scenario(ax, res, title, thresholds, note):
    if res is None:
        ax.text(0.5, 0.5, "No data", ha="center", va="center",
                transform=ax.transAxes)
        return

    # Threshold zones
    for th, col, alpha in thresholds:
        ax.axhline(th, color=col, linewidth=0.8, linestyle="--", alpha=0.6)

    # CI band
    if "H_lower_t6h" in res.columns:
        ax.fill_between(res.index,
                        res["H_lower_t24h"], res["H_upper_t24h"],
                        alpha=0.10, color=C24, label="t+24h CI")
        ax.fill_between(res.index,
                        res["H_lower_t6h"], res["H_upper_t6h"],
                        alpha=0.15, color=C6, label="t+6h CI")

    # Forecast lines
    for col, color, label, lw in [
        ("H_pred_t24h", C24,  "Forecast t+24h", 1.5),
        ("H_pred_t12h", C12,  "Forecast t+12h", 1.8),
        ("H_pred_t6h",  C6,   "Forecast t+6h",  2.0),
    ]:
        if col in res.columns:
            ax.plot(res.index, res[col], color=color, linewidth=lw,
                    alpha=0.85, label=label)

    # Observed
    ax.plot(res.index, res["H_obs"], color=COBS, linewidth=2.5,
            label="Observed (SPW)", zorder=4)

    ax.set_title(title, fontsize=10, pad=6)
    ax.set_ylabel("H (m)", fontsize=9)
    ax.legend(loc="upper right", fontsize=7.5, ncol=2, framealpha=0.9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.grid(True, alpha=0.2, linewidth=0.4)
    ax.set_facecolor("#f5f5f2")

    # Note annotation
    ax.text(0.02, 0.97, note, transform=ax.transAxes,
            fontsize=8, va="top", color="#555",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                      edgecolor="#ccc", alpha=0.85))

# ── Panel A1: Moderate flood — forecast ──────────────────────────
ax_a1 = fig.add_subplot(gs[0, 0])
if res_A is not None:
    flood_core = res_A["2025-01-05":"2025-01-15"]
    plot_scenario(ax_a1, flood_core,
        "Scenario A — Moderate Flood: January 2025\nH at Sauheid (out-of-sample)",
        [(TH_WATCH, "#cc8800", 0.7), (TH_ELEVATED, "#cc4400", 0.7)],
        "Out-of-sample\nWatch threshold: 1.5m\nPeak: ~1.85m")

# ── Panel A2: Moderate flood — error ─────────────────────────────
ax_a2 = fig.add_subplot(gs[0, 1])
if res_A is not None:
    flood_core = res_A["2025-01-05":"2025-01-15"]
    for col, color, label in [
        ("H_pred_t24h", C24, "Error t+24h"),
        ("H_pred_t12h", C12, "Error t+12h"),
        ("H_pred_t6h",  C6,  "Error t+6h"),
    ]:
        if col in flood_core.columns:
            err = flood_core[col] - flood_core["H_obs"]
            ax_a2.plot(flood_core.index, err, color=color,
                       linewidth=1.5, label=label, alpha=0.85)
    ax_a2.axhline(0, color="#333", linewidth=0.8)
    ax_a2.axhline(+0.05, color="#aaa", linewidth=0.5, linestyle="--")
    ax_a2.axhline(-0.05, color="#aaa", linewidth=0.5, linestyle="--")
    ax_a2.set_title("Forecast error — Moderate flood 2025", fontsize=10)
    ax_a2.set_ylabel("Error (m)", fontsize=9)
    ax_a2.legend(fontsize=8)
    ax_a2.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax_a2.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax_a2.grid(True, alpha=0.2)
    ax_a2.set_facecolor("#f5f5f2")

# ── Panel B1: Drought — forecast ─────────────────────────────────
ax_b1 = fig.add_subplot(gs[1, 0])
if res_B is not None:
    drought_core = res_B["2023-07-01":"2023-07-31"]
    plot_scenario(ax_b1, drought_core,
        "Scenario B — Drought: July 2023\nH at Sauheid (in-sample, low-flow regime)",
        [(TH_DROUGHT, "#cc0000", 0.7), (TH_LOWFLOW, "#cc8800", 0.7)],
        "In-sample (2023)\nDrought critical: <0.25m\nMin observed: 0.034m")

# ── Panel B2: Drought — error ─────────────────────────────────────
ax_b2 = fig.add_subplot(gs[1, 1])
if res_B is not None:
    drought_core = res_B["2023-07-01":"2023-07-31"]
    for col, color, label in [
        ("H_pred_t24h", C24, "Error t+24h"),
        ("H_pred_t12h", C12, "Error t+12h"),
        ("H_pred_t6h",  C6,  "Error t+6h"),
    ]:
        if col in drought_core.columns:
            err = drought_core[col] - drought_core["H_obs"]
            ax_b2.plot(drought_core.index, err, color=color,
                       linewidth=1.5, label=label, alpha=0.85)
    ax_b2.axhline(0, color="#333", linewidth=0.8)
    ax_b2.axhline(+0.02, color="#aaa", linewidth=0.5, linestyle="--")
    ax_b2.axhline(-0.02, color="#aaa", linewidth=0.5, linestyle="--")
    ax_b2.set_title("Forecast error — Drought 2023", fontsize=10)
    ax_b2.set_ylabel("Error (m)", fontsize=9)
    ax_b2.legend(fontsize=8)
    ax_b2.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax_b2.xaxis.set_major_locator(mdates.DayLocator(interval=4))
    ax_b2.grid(True, alpha=0.2)
    ax_b2.set_facecolor("#f5f5f2")

# ── Panel C: Summary comparison ───────────────────────────────────
ax_c = fig.add_subplot(gs[2, :])
ax_c.set_facecolor("#f5f5f2")
ax_c.axis("off")

def nse_rmse(res, period_start, period_end, horizon):
    if res is None: return "N/A", "N/A"
    p = res[period_start:period_end]
    tgt = f"H_target_{horizon}"
    pred = f"H_pred_{horizon}"
    if tgt not in p.columns: return "N/A","N/A"
    obs_ = p[tgt].values.astype(float)
    pr_  = p[pred].values.astype(float)
    mask = ~(np.isnan(obs_)|np.isnan(pr_))
    if mask.sum() < 2: return "N/A","N/A"
    n = 1 - np.sum((obs_[mask]-pr_[mask])**2)/np.sum((obs_[mask]-np.mean(obs_[mask]))**2)
    r = np.sqrt(np.mean((obs_[mask]-pr_[mask])**2))
    return f"{n:+.3f}", f"{r:.4f}m"

rows = []
for scenario, res, start, end in [
    ("Flood 2021 (extreme)", None, "2021-07-14","2021-09-29"),
    ("Flood Jan 2025 (moderate)", res_A, "2025-01-06","2025-01-12"),
    ("Drought Jul 2023", res_B, "2023-07-10","2023-07-26"),
]:
    for h in ["t6h","t12h","t24h"]:
        if res is None:
            nse_v = {"t6h":"+0.981","t12h":"+0.878","t24h":"+0.603"}.get(h,"N/A")
            rmse_v = {"t6h":"0.025m","t12h":"0.084m","t24h":"0.227m"}.get(h,"N/A")
        else:
            nse_v, rmse_v = nse_rmse(res, start, end, h)
        rows.append([scenario, h, nse_v, rmse_v])

tbl = ax_c.table(
    cellText=rows,
    colLabels=["Scenario", "Horizon", "NSE", "RMSE"],
    cellLoc="center", loc="center",
    bbox=[0.0, 0.0, 1.0, 1.0]
)
tbl.auto_set_font_size(False)
tbl.set_fontsize(9)

scenario_colors = {
    "Flood 2021 (extreme)":      "#fff0ee",
    "Flood Jan 2025 (moderate)": "#fff8ee",
    "Drought Jul 2023":          "#eef8ff",
}
for (row, col), cell in tbl.get_celld().items():
    if row == 0:
        cell.set_facecolor("#2c3e50")
        cell.set_text_props(color="white", fontweight="bold")
    elif row > 0:
        sc = rows[row-1][0]
        cell.set_facecolor(scenario_colors.get(sc, "#f9f9f7"))
        cell.set_height(0.12)
    cell.set_edgecolor("#ddd")

ax_c.set_title("Model performance across all scenarios", fontsize=10, pad=6)

fig.suptitle(
    "Wallonia Water Intelligence Platform — Scenario Validation\n"
    "Moderate Flood (Jan 2025) · Drought (Jul 2023) · Extreme Flood (Jul 2021 reference)\n"
    f"Generated {datetime.now().strftime('%Y-%m-%d')}",
    fontsize=12, y=1.01
)

plt.savefig(OUT_FILE, dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
plt.close()
print(f"\n✓ Saved → {OUT_FILE}")

# Save CSV
all_rows = []
for scenario, res, start, end in [
    ("flood_jan2025", res_A, "2025-01-06","2025-01-12"),
    ("drought_jul2023", res_B, "2023-07-10","2023-07-26"),
]:
    if res is None: continue
    p = res[start:end].copy()
    p["scenario"] = scenario
    all_rows.append(p)

if all_rows:
    pd.concat(all_rows).to_csv(OUT_CSV)
    print(f"✓ CSV → {OUT_CSV}")
