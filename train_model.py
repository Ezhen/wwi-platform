"""
WWI River Level Forecast Model
Target: H at SAUHEID 1/2/3 days ahead
Train: 2023-01-01 → 2024-12-31
Test:  2025-01-01 → 2025-06-04
Validation: July 2021 flood (out-of-sample)
"""

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

ROOT    = Path(__file__).parent.parent
CSV_IN  = str(ROOT / "wwi/export/csvs/features_sauheid.csv")
OUT_DIR = ROOT / "wwi/export/csvs"

print("=" * 60)
print("WWI River Level Forecast Model — SAUHEID (Ourthe)")
print("=" * 60)

# ── 1. Load features ──────────────────────────────────────────────────────────
print("\n[1/5] Loading feature matrix...")
df = pd.read_csv(CSV_IN, index_col=0, parse_dates=True)
print(f"  Shape: {df.shape}")
print(f"  Range: {df.index.min()} → {df.index.max()}")

TARGET_COLS  = ["H_t1", "H_t2", "H_t3"]
FEATURE_COLS = [c for c in df.columns if c not in TARGET_COLS]

# Drop rows with any NaN in features
df_clean = df[FEATURE_COLS + TARGET_COLS].dropna()
print(f"  After dropna: {len(df_clean)} rows")

# ── 2. Train/test split (temporal) ────────────────────────────────────────────
print("\n[2/5] Splitting data...")

# Make index timezone-naive for comparison
df_clean.index = df_clean.index.tz_localize(None)

train = df_clean["2023-01-01":"2024-12-31"]
test  = df_clean["2025-01-01":]
flood = df_clean["2021-06-01":"2021-09-30"]

print(f"  Train: {len(train)} days  ({train.index.min().date()} → {train.index.max().date()})")
print(f"  Test:  {len(test)} days   ({test.index.min().date()} → {test.index.max().date()})")
print(f"  Flood: {len(flood)} days  ({flood.index.min().date()} → {flood.index.max().date()})")

X_train = train[FEATURE_COLS]
X_test  = test[FEATURE_COLS]
X_flood = flood[FEATURE_COLS]

# ── 3. Train models ───────────────────────────────────────────────────────────
print("\n[3/5] Training models...")

from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

def nse(obs, sim):
    """Nash-Sutcliffe Efficiency — standard hydrological skill score."""
    obs, sim = np.array(obs), np.array(sim)
    mask = ~(np.isnan(obs) | np.isnan(sim))
    obs, sim = obs[mask], sim[mask]
    return 1 - np.sum((obs - sim)**2) / np.sum((obs - np.mean(obs))**2)

def rmse(obs, sim):
    obs, sim = np.array(obs), np.array(sim)
    mask = ~(np.isnan(obs) | np.isnan(sim))
    return np.sqrt(mean_squared_error(obs[mask], sim[mask]))

results = {}

for horizon, target in [("t+1d", "H_t1"), ("t+2d", "H_t2"), ("t+3d", "H_t3")]:

    # Per-horizon dropna (Gemini fix) — avoids leaking future target NaNs
    tr_idx = train[target].dropna().index
    te_idx = test[target].dropna().index
    fl_idx = flood[target].dropna().index

    y_train = train.loc[tr_idx, target]
    y_test  = test.loc[te_idx, target]
    y_flood = flood.loc[fl_idx, target]

    X_tr = X_train.loc[tr_idx]
    X_te = X_test.loc[te_idx]
    X_fl = X_flood.loc[fl_idx]

    print(f"\n  Horizon {horizon}  "
          f"(train={len(y_train)}  test={len(y_test)}  flood={len(y_flood)}):")

    # Baseline: persistence
    pers_test  = test.loc[te_idx, "H_lag1"]
    pers_flood = flood.loc[fl_idx, "H_lag1"]
    print(f"    Persistence    NSE={nse(y_test, pers_test):+.3f}  "
          f"RMSE={rmse(y_test, pers_test):.4f}m")

    # Linear regression
    scaler = StandardScaler()
    lr = LinearRegression()
    lr.fit(scaler.fit_transform(X_tr), y_train)
    lr_pred_test  = lr.predict(scaler.transform(X_te))
    lr_pred_flood = lr.predict(scaler.transform(X_fl))
    print(f"    LinearReg      NSE={nse(y_test, lr_pred_test):+.3f}  "
          f"RMSE={rmse(y_test, lr_pred_test):.4f}m")

    # Random Forest — absolute H
    rf = RandomForestRegressor(
        n_estimators=200, max_depth=10,
        min_samples_leaf=5, random_state=42, n_jobs=-1
    )
    rf.fit(X_tr, y_train)
    rf_pred_test  = rf.predict(X_te)
    rf_pred_flood = rf.predict(X_fl)
    print(f"    RandomForest   NSE={nse(y_test, rf_pred_test):+.3f}  "
          f"RMSE={rmse(y_test, rf_pred_test):.4f}m")
    print(f"    RF (Jul2021)   NSE={nse(y_flood, rf_pred_flood):+.3f}  "
          f"RMSE={rmse(y_flood, rf_pred_flood):.4f}m")

    # Random Forest — delta H (Gemini/extrapolation fix)
    # Predict change rather than absolute level
    delta_target = f"dH_{horizon}"
    train[delta_target] = train[target] - train["H"]
    test[delta_target]  = test[target]  - test["H"]
    flood[delta_target] = flood[target] - flood["H"]

    y_tr_d = train.loc[tr_idx, delta_target]
    y_te_d = test.loc[te_idx, delta_target]
    y_fl_d = flood.loc[fl_idx, delta_target]

    rf_d = RandomForestRegressor(
        n_estimators=200, max_depth=10,
        min_samples_leaf=5, random_state=42, n_jobs=-1
    )
    rf_d.fit(X_tr, y_tr_d)
    # Convert delta predictions back to absolute H
    rf_d_pred_test  = test.loc[te_idx, "H"].values  + rf_d.predict(X_te)
    rf_d_pred_flood = flood.loc[fl_idx, "H"].values + rf_d.predict(X_fl)
    print(f"    RF-deltaH      NSE={nse(y_test, rf_d_pred_test):+.3f}  "
          f"RMSE={rmse(y_test, rf_d_pred_test):.4f}m")
    print(f"    RF-dH (2021)   NSE={nse(y_flood, rf_d_pred_flood):+.3f}  "
          f"RMSE={rmse(y_flood, rf_d_pred_flood):.4f}m  ← extrapolation test")

    results[horizon] = {
        "y_test":        y_test,
        "y_flood":       y_flood,
        "pers_test":     pers_test,
        "lr_test":       pd.Series(lr_pred_test,   index=te_idx),
        "rf_test":       pd.Series(rf_pred_test,   index=te_idx),
        "rf_flood":      pd.Series(rf_pred_flood,  index=fl_idx),
        "rf_d_flood":    pd.Series(rf_d_pred_flood, index=fl_idx),
        "rf_model":      rf,
        "rf_d_model":    rf_d,
        "feature_names": FEATURE_COLS,
    }

# ── 4. Feature importance ─────────────────────────────────────────────────────
print("\n[4/5] Feature importance (t+1d Random Forest):")
rf1 = results["t+1d"]["rf_model"]
importances = pd.Series(
    rf1.feature_importances_, index=FEATURE_COLS
).sort_values(ascending=False)
for feat, imp in importances.head(15).items():
    bar = "█" * int(imp * 200)
    print(f"  {feat:<25} {imp:.4f}  {bar}")

# ── 5. Save predictions ───────────────────────────────────────────────────────
print("\n[5/5] Saving predictions...")

# Test period predictions
pred_test = pd.DataFrame({
    "H_obs":      results["t+1d"]["y_test"],
    "H_pers_t1":  results["t+1d"]["pers_test"],
    "H_lr_t1":    results["t+1d"]["lr_test"],
    "H_rf_t1":    results["t+1d"]["rf_test"],
    "H_rf_t2":    results["t+2d"]["rf_test"],
    "H_rf_t3":    results["t+3d"]["rf_test"],
})
pred_test.to_csv(str(OUT_DIR / "predictions_test_2025.csv"))
print(f"  Test predictions → wwi/export/csvs/predictions_test_2025.csv")

# Flood period predictions
pred_flood = pd.DataFrame({
    "H_obs":   results["t+1d"]["y_flood"],
    "H_rf_t1": results["t+1d"]["rf_flood"],
    "H_rf_t2": results["t+2d"]["rf_flood"],
    "H_rf_t3": results["t+3d"]["rf_flood"],
})
pred_flood.to_csv(str(OUT_DIR / "predictions_flood_2021.csv"))
print(f"  Flood predictions → wwi/export/csvs/predictions_flood_2021.csv")

# Summary table
print("\n" + "=" * 60)
print("SUMMARY — Random Forest performance")
print("=" * 60)
print(f"{'Horizon':<10} {'Test NSE':>10} {'Test RMSE':>10} {'Flood NSE':>10} {'Flood RMSE':>11}")
print("-" * 55)
for h in ["t+1d","t+2d","t+3d"]:
    r = results[h]
    t_nse  = nse(r["y_test"],  r["rf_test"])
    t_rmse = rmse(r["y_test"], r["rf_test"])
    f_nse  = nse(r["y_flood"], r["rf_flood"])
    f_rmse = rmse(r["y_flood"], r["rf_flood"])
    print(f"{h:<10} {t_nse:>+10.3f} {t_rmse:>10.4f} {f_nse:>+10.3f} {f_rmse:>11.4f}")

print("\nNSE interpretation: >0.75 excellent · >0.5 good · >0 better than mean")
