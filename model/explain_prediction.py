"""
WWI — Prediction Explainer
Given the current state of the basin, explains WHY the model
predicts what it predicts using SHAP values.
Produces a human-readable risk briefing.
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path

ROOT   = Path(__file__).parent.parent
CSV_IN = str(ROOT / "export/csvs/features_sauheid.csv")

print("=" * 60)
print("WWI Prediction Explainer — SAUHEID (Ourthe)")
print("=" * 60)

# ── 1. Load and retrain model ─────────────────────────────────────────────────
print("\n[1/4] Loading data and retraining RF-deltaH...")

df = pd.read_csv(CSV_IN, index_col=0, parse_dates=True)
df.index = df.index.tz_localize(None)

TARGET_COLS  = ["H_t1", "H_t2", "H_t3"]
FEATURE_COLS = [c for c in df.columns if c not in TARGET_COLS]

df_clean = df[FEATURE_COLS + TARGET_COLS].dropna()
train    = df_clean["2023-01-01":"2024-12-31"]

from sklearn.ensemble import RandomForestRegressor

# Train RF-deltaH for t+1d (best model)
target   = "H_t1"
tr_idx   = train[target].dropna().index
y_tr     = train.loc[tr_idx, target] - train.loc[tr_idx, "H"]
X_tr     = train.loc[tr_idx, FEATURE_COLS]

rf = RandomForestRegressor(
    n_estimators=300, max_depth=10,
    min_samples_leaf=5, random_state=42, n_jobs=-1
)
rf.fit(X_tr, y_tr)
print(f"  Model trained on {len(X_tr)} days")

# ── 2. Current state — use most recent available row ──────────────────────────
print("\n[2/4] Extracting current basin state...")

# Use the last row with complete features
current = df[FEATURE_COLS].dropna().iloc[[-1]]
current_date = current.index[0]
current_H    = float(current["H"].values[0])

# Predict
delta_pred = rf.predict(current)[0]
H_pred_t1  = current_H + delta_pred

# Also predict t+2 and t+3 using simpler lag approach
print(f"\n  Date:          {current_date.date()}")
print(f"  Current H:     {current_H:.3f} m")
print(f"  Predicted H+1: {H_pred_t1:.3f} m  (Δ={delta_pred:+.3f}m)")

# ── 3. SHAP explanation ───────────────────────────────────────────────────────
print("\n[3/4] Computing SHAP values...")

try:
    import shap

    explainer  = shap.TreeExplainer(rf)
    shap_vals  = explainer.shap_values(current)
    shap_series = pd.Series(shap_vals[0], index=FEATURE_COLS)

    # Top drivers — positive = pushing H up, negative = pushing H down
    top_pos = shap_series.nlargest(8)
    top_neg = shap_series.nsmallest(5)

    base = float(np.array(explainer.expected_value).flat[0])
    print(f"\n  SHAP base value (expected ΔH): {base:+.3f}m")
    print(f"  SHAP prediction:               {shap_vals[0].sum() + base:+.3f}m")

    print("\n  Factors RAISING river level:")
    for feat, val in top_pos.items():
        feat_val = float(current[feat].values[0])
        bar = "▲" * max(1, int(abs(val) * 100))
        print(f"    {feat:<28} {feat_val:>8.3f}  SHAP={val:+.4f}  {bar}")

    print("\n  Factors LOWERING river level:")
    for feat, val in top_neg.items():
        feat_val = float(current[feat].values[0])
        bar = "▼" * max(1, int(abs(val) * 100))
        print(f"    {feat:<28} {feat_val:>8.3f}  SHAP={val:+.4f}  {bar}")

except ImportError:
    print("  SHAP not installed — using RF feature importance as proxy")
    print("  Install with: pip install shap --break-system-packages")

    importances = pd.Series(rf.feature_importances_, index=FEATURE_COLS)
    contributions = importances * (current.values[0] - X_tr.mean().values)
    contrib_series = pd.Series(contributions, index=FEATURE_COLS)

    print("\n  Top factors (importance × deviation from mean):")
    for feat, val in contrib_series.abs().nlargest(10).items():
        feat_val = float(current[feat].values[0])
        direction = "▲" if contrib_series[feat] > 0 else "▼"
        print(f"    {direction} {feat:<28} val={feat_val:>8.3f}  contrib={contrib_series[feat]:+.4f}")

# ── 4. Natural language briefing ──────────────────────────────────────────────
print("\n[4/4] Generating operational briefing...")

# Risk classification
if H_pred_t1 > 3.0:
    risk = "HIGH"
    risk_desc = "Major flood risk. Immediate action may be required."
elif H_pred_t1 > 1.5:
    risk = "MODERATE"
    risk_desc = "Elevated water levels. Monitor closely."
elif H_pred_t1 > 0.8:
    risk = "WATCH"
    risk_desc = "Above normal levels. Situation developing."
else:
    risk = "NORMAL"
    risk_desc = "No significant flood risk."

# Tendency
if delta_pred > 0.1:
    tendency = "rising rapidly"
elif delta_pred > 0.02:
    tendency = "rising"
elif delta_pred < -0.1:
    tendency = "falling rapidly"
elif delta_pred < -0.02:
    tendency = "falling"
else:
    tendency = "stable"

# Antecedent rain context
p7d = float(current["P_basin_7d"].values[0]) if "P_basin_7d" in current.columns else None
p3d = float(current["P_ourthe_3d"].values[0]) if "P_ourthe_3d" in current.columns else None

print(f"""
╔══════════════════════════════════════════════════════════╗
║  WWI OPERATIONAL BRIEFING — {current_date.date()}              
║  Station: SAUHEID — Ourthe inférieure                    
╠══════════════════════════════════════════════════════════╣
║                                                          
║  Current level:    {current_H:.3f} m                           
║  24h forecast:     {H_pred_t1:.3f} m  ({delta_pred:+.3f} m)                 
║  Tendency:         {tendency:<20}                   
║  Risk level:       {risk:<10}                             
║                                                          
║  {risk_desc:<54}
║                                                          
║  Context:                                                
║  • 7-day basin rainfall: {p7d if p7d else 'N/A':.1f} mm              
║  • 3-day Ourthe rainfall: {p3d if p3d else 'N/A':.1f} mm             
║  • Upstream Stavelot:  {float(current['H_stavelot'].values[0]):.3f} m                    
║  • Upstream Comblain:  {float(current['H_comblain'].values[0]):.3f} m                    
║                                                          
║  Model: RF-deltaH · trained 2023-2024 · NSE=0.953 (1d)  
╚══════════════════════════════════════════════════════════╝
""")

# ── Save SHAP summary for export ──────────────────────────────────────────────
try:
    shap_export = pd.DataFrame({
        "feature":     FEATURE_COLS,
        "shap_value":  shap_vals[0],
        "feature_val": current.values[0],
    }).sort_values("shap_value", ascending=False)
    out = str(ROOT / "export/csvs/shap_current.csv")
    shap_export.to_csv(out, index=False)
    print(f"SHAP values saved → {out}")
except:
    pass

print("\nNext: integrate this briefing into the LLM bulletin (Claude API)")
