"""
WWI Hourly Forecast Model
Trains RF-deltaH at hourly resolution.
Targets: H at t+6h, t+12h, t+24h

Expected improvement over daily model:
  - Captures 6-18h wave propagation explicitly
  - Rising limb resolved (critical for flood warning)
  - Expected flood NSE: 0.85+ vs 0.670 daily
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error

ROOT    = Path(__file__).resolve().parent
CSV_IN  = str(ROOT / "export/csvs/features_sauheid_hourly.csv")
OUT_DIR = ROOT / "export/csvs"

print("=" * 60)
print("WWI Hourly Forecast Model — SAUHEID (Ourthe)")
print("=" * 60)

# ── Load ──────────────────────────────────────────────────────────────────────
print("\n[1/5] Loading hourly feature matrix...")
df = pd.read_csv(CSV_IN, index_col=0, parse_dates=True)
df.index = df.index.tz_localize(None)
print(f"  Shape: {df.shape}")
print(f"  Range: {df.index.min()} → {df.index.max()}")

TARGET_COLS  = ["H_t6h","H_t12h","H_t24h"]
FEATURE_COLS = [c for c in df.columns if c not in TARGET_COLS]

# Fill NaN with median
for col in FEATURE_COLS:
    if df[col].isna().any():
        med = df[col].median()
        df[col] = df[col].fillna(med if pd.notna(med) else 0.0)

# ── Split ─────────────────────────────────────────────────────────────────────
print("\n[2/5] Splitting data...")
df.index = pd.to_datetime(df.index).tz_localize(None)

train = df["2023-01-01":"2024-12-31"]
test  = df["2025-01-01":]
flood = df["2021-06-14":"2021-09-29"]

print(f"  Train: {len(train):,} hours")
print(f"  Test:  {len(test):,} hours")
print(f"  Flood: {len(flood):,} hours")

def nse(obs, sim):
    obs, sim = np.array(obs), np.array(sim)
    mask = ~(np.isnan(obs) | np.isnan(sim))
    obs, sim = obs[mask], sim[mask]
    if len(obs) < 2: return np.nan
    return 1 - np.sum((obs-sim)**2) / np.sum((obs-np.mean(obs))**2)

def rmse(obs, sim):
    obs, sim = np.array(obs), np.array(sim)
    mask = ~(np.isnan(obs) | np.isnan(sim))
    if mask.sum() < 2: return np.nan
    return np.sqrt(mean_squared_error(obs[mask], sim[mask]))

results = {}
print("\n[3/5] Training models...")

for horizon, target in [("t+6h","H_t6h"),("t+12h","H_t12h"),("t+24h","H_t24h")]:

    tr_idx = train[target].dropna().index
    te_idx = test[target].dropna().index
    fl_idx = flood[target].dropna().index

    y_tr = train.loc[tr_idx, target]
    y_te = test.loc[te_idx, target]
    y_fl = flood.loc[fl_idx, target]

    X_tr = train.loc[tr_idx, FEATURE_COLS]
    X_te = test.loc[te_idx, FEATURE_COLS]
    X_fl = flood.loc[fl_idx, FEATURE_COLS]

    med  = X_tr.median()
    X_tr_f = X_tr.fillna(med.fillna(0))
    X_te_f = X_te.fillna(med.fillna(0))
    X_fl_f = X_fl.fillna(med.fillna(0))

    print(f"\n  Horizon {horizon}  "
          f"(train={len(y_tr):,}  test={len(y_te):,}  flood={len(y_fl):,}):")

    # Persistence baseline (last known H)
    pers_te = test.loc[te_idx, "H_sauheid"]
    pers_fl = flood.loc[fl_idx, "H_sauheid"]
    print(f"    Persistence    NSE={nse(y_te, pers_te):+.3f}  "
          f"RMSE={rmse(y_te, pers_te):.4f}m")

    # Delta-H RF
    delta_col = f"dH_{horizon}"
    train[delta_col] = train[target] - train["H_sauheid"]
    test[delta_col]  = test[target]  - test["H_sauheid"]
    flood[delta_col] = flood[target] - flood["H_sauheid"]

    y_tr_d = train.loc[tr_idx, delta_col]

    rf = RandomForestRegressor(
        n_estimators=300, max_depth=12,
        min_samples_leaf=3, random_state=42, n_jobs=-1
    )
    rf.fit(X_tr_f, y_tr_d)

    pred_te = test.loc[te_idx, "H_sauheid"].values  + rf.predict(X_te_f)
    pred_fl = flood.loc[fl_idx,"H_sauheid"].values  + rf.predict(X_fl_f)

    print(f"    RF-deltaH      NSE={nse(y_te, pred_te):+.3f}  "
          f"RMSE={rmse(y_te, pred_te):.4f}m")
    print(f"    RF-dH (2021)   NSE={nse(y_fl, pred_fl):+.3f}  "
          f"RMSE={rmse(y_fl, pred_fl):.4f}m  ← flood test")

    results[horizon] = {
        "rf":       rf,
        "y_te":     y_te,
        "y_fl":     y_fl,
        "pred_te":  pd.Series(pred_te, index=te_idx),
        "pred_fl":  pd.Series(pred_fl, index=fl_idx),
        "pers_te":  pers_te,
        "nse_test": nse(y_te, pred_te),
        "nse_flood":nse(y_fl, pred_fl),
    }

# ── Feature importance ────────────────────────────────────────────────────────
print("\n[4/5] Feature importance (t+6h):")
rf6 = results["t+6h"]["rf"]
imp = pd.Series(rf6.feature_importances_,
                index=FEATURE_COLS).sort_values(ascending=False)
for feat, val in imp.head(20).items():
    bar = "█" * int(val * 300)
    print(f"  {feat:<30} {val:.4f}  {bar}")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n[5/5] Saving predictions...")
pred_out = pd.DataFrame({
    "H_obs":       results["t+6h"]["y_te"],
    "H_pred_t6h":  results["t+6h"]["pred_te"],
    "H_pred_t12h": results["t+12h"]["pred_te"],
    "H_pred_t24h": results["t+24h"]["pred_te"],
})
pred_out.to_csv(str(OUT_DIR / "predictions_hourly_test_2025.csv"))

flood_out = pd.DataFrame({
    "H_obs":       results["t+6h"]["y_fl"],
    "H_pred_t6h":  results["t+6h"]["pred_fl"],
    "H_pred_t12h": results["t+12h"]["pred_fl"],
    "H_pred_t24h": results["t+24h"]["pred_fl"],
})
flood_out.to_csv(str(OUT_DIR / "predictions_hourly_flood_2021.csv"))

print("\n" + "=" * 60)
print("SUMMARY — Hourly RF-deltaH vs Daily RF-deltaH")
print("=" * 60)
print(f"{'Horizon':<10} {'Test NSE':>10} {'Flood NSE':>10}  vs daily")
print("-" * 45)
daily_ref = {"t+1d": (0.975, 0.670)}
for h, r in results.items():
    print(f"  {h:<10} {r['nse_test']:>+10.3f} {r['nse_flood']:>+10.3f}")
print(f"\n  Daily model (ref): test=+0.975  flood=+0.670  (t+24h)")

# Log to model_versions
from datetime import date
row = (f"hourly_v1,hourly_RF_deltaH,"
       f"{results['t+24h']['nse_test']:.3f},"
       f"{results['t+24h']['nse_flood']:.3f},"
       f"{results['t+24h']['nse_test']-0.975:+.3f},"
       f"{results['t+24h']['nse_flood']-0.670:+.3f},"
       f"{date.today()}\n")
with open(str(OUT_DIR / "model_versions.csv"), "a") as f:
    f.write(row)
print(f"\n✓ Logged to model_versions.csv")
