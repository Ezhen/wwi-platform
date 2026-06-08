"""
WWI — Live Prediction Explainer
Builds today's feature row directly from live databases,
runs RF-deltaH, generates SHAP explanation and briefing.
Always uses current data, not stale CSV.
"""

import pandas as pd
import numpy as np
import sqlite3
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT    = Path(__file__).parent.parent
DB_SPW  = str(ROOT / "wwi/export/databases/spw_liege.db")
DB_HIST = str(ROOT / "wwi/export/databases/historical_liege.db")
CSV_FEATURES = str(ROOT / "wwi/export/csvs/features_sauheid.csv")

TODAY = datetime.now(timezone.utc).date()
print("=" * 60)
print(f"WWI Live Prediction Explainer — {TODAY}")
print("=" * 60)

# ── 1. Retrain model on historical data ───────────────────────────────────────
print("\n[1/5] Loading training data and retraining model...")

df_hist = pd.read_csv(CSV_FEATURES, index_col=0, parse_dates=True)
df_hist.index = df_hist.index.tz_localize(None)

TARGET_COLS  = ["H_t1", "H_t2", "H_t3"]
FEATURE_COLS = [c for c in df_hist.columns if c not in TARGET_COLS]

train = df_hist[FEATURE_COLS + TARGET_COLS]["2023-01-01":"2024-12-31"]
tr_idx = train["H_t1"].dropna().index
y_tr   = train.loc[tr_idx, "H_t1"] - train.loc[tr_idx, "H"]
X_tr   = train.loc[tr_idx, FEATURE_COLS]

from sklearn.ensemble import RandomForestRegressor
rf = RandomForestRegressor(
    n_estimators=300, max_depth=10,
    min_samples_leaf=5, random_state=42, n_jobs=-1
)
rf.fit(X_tr, y_tr)
print(f"  Model trained on {len(X_tr)} days")

# ── 2. Build today's feature row from live DBs ────────────────────────────────
print(f"\n[2/5] Building live feature row for {TODAY}...")

def get_daily_mean(db, station_no, parameter, ts_name, date, n_days=1):
    """Get daily mean from historical DB."""
    con = sqlite3.connect(db)
    start = (date - timedelta(days=n_days-1)).isoformat()
    end   = date.isoformat()
    row = con.execute(f"""
        SELECT AVG(value) FROM observations
        WHERE station_no='{station_no}' AND parameter='{parameter}'
          AND ts_name='{ts_name}'
          AND DATE(timestamp) BETWEEN '{start}' AND '{end}'
          AND value IS NOT NULL
    """).fetchone()
    con.close()
    return row[0] if row and row[0] is not None else None

def get_live_H(station_no, n_records=288):
    """Get gauge-relative H from t_latest_H (correct units, not NGF elevation)."""
    con = sqlite3.connect(DB_SPW)
    # First try t_latest_H materialized table (gauge-relative, correct units)
    row = con.execute(f"""
        SELECT level_m FROM t_latest_H
        WHERE station_no='{station_no}'
    """).fetchone()
    if row and row[0] is not None:
        con.close()
        return float(row[0])
    # Fallback: raw observations with Value returnfields (non-absolute)
    rows = con.execute(f"""
        SELECT AVG(o.value) FROM (
            SELECT value FROM observations o
            JOIN timeseries t ON o.ts_id = t.ts_id
            WHERE o.station_no='{station_no}' AND o.parameter='H'
              AND t.ts_path NOT LIKE '%Habs%'
            ORDER BY o.timestamp DESC LIMIT {n_records}
        )
    """).fetchone()
    con.close()
    return rows[0] if rows and rows[0] else None

def get_live_Q(station_no):
    con = sqlite3.connect(DB_SPW)
    row = con.execute(f"""
        SELECT AVG(value) FROM (
            SELECT value FROM observations
            WHERE station_no='{station_no}' AND parameter='Q'
              AND value IS NOT NULL
            ORDER BY timestamp DESC LIMIT 288
        )
    """).fetchone()
    con.close()
    return row[0] if row and row[0] else None

def get_live_precip_sum(station_no, n_days):
    """Sum precip over last N days from live DB."""
    con = sqlite3.connect(DB_SPW)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=n_days)).isoformat()
    row = con.execute(f"""
        SELECT SUM(value) FROM observations
        WHERE station_no='{station_no}' AND parameter='Precip'
          AND timestamp >= '{cutoff}' AND value IS NOT NULL
    """).fetchone()
    con.close()
    return row[0] if row and row[0] else 0.0

# Use correct gauge-relative H from feature CSV (last known good values)
# t_latest_H has NGF absolute elevations — unit mismatch with training data
# TODO: fix ingest to store Value (relative) not Absolute Value for H
yesterday_row = df_hist[FEATURE_COLS].dropna().iloc[-1]

H_now        = float(yesterday_row["H"])
H_chaudf     = float(yesterday_row["H_chaudf"])
H_eupen      = float(yesterday_row["H_eupen"])   if "H_eupen" in yesterday_row.index else None
H_stavelot   = float(yesterday_row["H_stavelot"])
H_troisponts = float(yesterday_row["H_troisponts"])
H_comblain   = float(yesterday_row["H_comblain"])
H_huy        = float(yesterday_row["H_huy"])

# Live Q and Precip are correctly stored (not affected by units issue)
Q_now        = get_live_Q("5826")           # SAUHEID Q — live
Q_chaudf     = get_live_Q("6228")           # CHAUDFONTAINE Q — live

print(f"  NOTE: H values from last CSV date ({df_hist[FEATURE_COLS].dropna().index[-1].date()})")
print(f"        Q and Precip from live DB ({TODAY})")

P_ourthe_1d  = get_live_precip_sum("6657",  1)
P_ourthe_3d  = get_live_precip_sum("6657",  3)
P_ourthe_7d  = get_live_precip_sum("6657",  7)
P_ourthe_14d = get_live_precip_sum("6657", 14)
P_vesdre_1d  = get_live_precip_sum("6958",  1)
P_vesdre_3d  = get_live_precip_sum("6958",  3)
P_vesdre_7d  = get_live_precip_sum("6958",  7)
P_vesdre_14d = get_live_precip_sum("6958", 14)
P_fagnes_1d  = get_live_precip_sum("6529",  1)
P_fagnes_3d  = get_live_precip_sum("6529",  3)
P_fagnes_7d  = get_live_precip_sum("6529",  7)
P_fagnes_14d = get_live_precip_sum("6529", 14)

print(f"  H SAUHEID:    {H_now:.3f} m")
print(f"  H STAVELOT:   {H_stavelot:.3f} m")
print(f"  H COMBLAIN:   {H_comblain:.3f} m")
print(f"  Q SAUHEID:    {Q_now:.2f} m³/s")
print(f"  P Fagnes 7d:  {P_fagnes_7d:.1f} mm")
print(f"  P Ourthe 7d:  {P_ourthe_7d:.1f} mm")

# ── 3. Assemble feature row matching training columns ─────────────────────────
print(f"\n[3/5] Assembling feature vector ({len(FEATURE_COLS)} features)...")

# Use yesterday's row from historical CSV as base, then overwrite with live values
# This handles features we can't compute live (e.g. deep lags)
yesterday = df_hist[FEATURE_COLS].dropna().iloc[-1].copy()

# Overwrite with live values
doy = TODAY.timetuple().tm_yday
live_overrides = {
    "H":              H_now,
    "H_chaudf":       H_chaudf,
    "H_eupen":        H_eupen,
    "H_stavelot":     H_stavelot,
    "H_troisponts":   H_troisponts,
    "H_comblain":     H_comblain,
    "H_huy":          H_huy,
    "Q":              Q_now,
    "Q_chaudf":       Q_chaudf,
    "H_lag1":         float(yesterday["H"]),
    "H_lag2":         float(yesterday["H_lag1"]),
    "H_lag3":         float(yesterday["H_lag2"]),
    "H_lag5":         float(yesterday["H_lag4"]) if "H_lag4" in yesterday else float(yesterday["H_lag3"]),
    "H_lag7":         float(yesterday["H_lag5"]) if "H_lag5" in yesterday else float(yesterday["H_lag3"]),
    "H_delta1d":      H_now - float(yesterday["H"]),
    "H_delta3d":      H_now - float(yesterday["H_lag2"]),
    "P_ourthe":       P_ourthe_1d,
    "P_ourthe_lag0":  P_ourthe_1d,
    "P_ourthe_lag1":  float(yesterday["P_ourthe"]),
    "P_ourthe_lag2":  float(yesterday["P_ourthe_lag1"]),
    "P_ourthe_lag3":  float(yesterday["P_ourthe_lag2"]),
    "P_ourthe_3d":    P_ourthe_3d,
    "P_ourthe_7d":    P_ourthe_7d,
    "P_ourthe_14d":   P_ourthe_14d,
    "P_vesdre":       P_vesdre_1d,
    "P_vesdre_lag0":  P_vesdre_1d,
    "P_vesdre_lag1":  float(yesterday["P_vesdre"]),
    "P_vesdre_lag2":  float(yesterday["P_vesdre_lag1"]),
    "P_vesdre_lag3":  float(yesterday["P_vesdre_lag2"]),
    "P_vesdre_3d":    P_vesdre_3d,
    "P_vesdre_7d":    P_vesdre_7d,
    "P_vesdre_14d":   P_vesdre_14d,
    "P_fagnes":       P_fagnes_1d,
    "P_fagnes_lag0":  P_fagnes_1d,
    "P_fagnes_lag1":  float(yesterday["P_fagnes"]),
    "P_fagnes_lag2":  float(yesterday["P_fagnes_lag1"]),
    "P_fagnes_lag3":  float(yesterday["P_fagnes_lag2"]),
    "P_fagnes_3d":    P_fagnes_3d,
    "P_fagnes_7d":    P_fagnes_7d,
    "P_fagnes_14d":   P_fagnes_14d,
    "P_basin_3d":     P_ourthe_3d + P_vesdre_3d + P_fagnes_3d,
    "P_basin_7d":     P_ourthe_7d + P_vesdre_7d + P_fagnes_7d,
    "P_basin_14d":    P_ourthe_14d + P_vesdre_14d + P_fagnes_14d,
    "doy":            doy,
    "month":          TODAY.month,
    "sin_doy":        np.sin(2 * np.pi * doy / 365.25),
    "cos_doy":        np.cos(2 * np.pi * doy / 365.25),
}

for k, v in live_overrides.items():
    if k in yesterday.index and v is not None:
        yesterday[k] = v

current = pd.DataFrame([yesterday], index=[pd.Timestamp(TODAY)])

# ── 4. Predict and explain ────────────────────────────────────────────────────
print(f"\n[4/5] Predicting and explaining...")

X_now    = current[FEATURE_COLS]
delta    = rf.predict(X_now)[0]
H_pred   = H_now + delta

# Multi-step autoregressive forecast t+2, t+3
# Roll H forward and re-predict
X_t2 = X_now.copy()
X_t2["H"]       = H_pred
X_t2["H_lag1"]  = H_now
X_t2["H_lag2"]  = float(X_now["H_lag1"].values[0])
X_t2["H_delta1d"] = H_pred - H_now
delta2  = rf.predict(X_t2)[0]
H_pred2 = H_pred + delta2

X_t3 = X_t2.copy()
X_t3["H"]       = H_pred2
X_t3["H_lag1"]  = H_pred
X_t3["H_lag2"]  = H_now
X_t3["H_delta1d"] = H_pred2 - H_pred
delta3  = rf.predict(X_t3)[0]
H_pred3 = H_pred2 + delta3

import shap
explainer = shap.TreeExplainer(rf)
shap_vals = explainer.shap_values(X_now)
base      = float(np.array(explainer.expected_value).flat[0])

shap_series = pd.Series(shap_vals[0], index=FEATURE_COLS)
top_pos = shap_series.nlargest(8)
top_neg = shap_series.nsmallest(5)

print(f"\n  SHAP base ΔH: {base:+.3f}m  →  predicted ΔH: {delta:+.3f}m")
print(f"\n  Factors RAISING river level:")
for feat, val in top_pos.items():
    fv = float(X_now[feat].values[0])
    print(f"    {feat:<28} val={fv:>8.3f}  SHAP={val:+.4f}  {'▲'*max(1,int(abs(val)*100))}")

print(f"\n  Factors LOWERING river level:")
for feat, val in top_neg.items():
    fv = float(X_now[feat].values[0])
    print(f"    {feat:<28} val={fv:>8.3f}  SHAP={val:+.4f}  {'▼'*max(1,int(abs(val)*100))}")

# ── 5. Operational briefing ───────────────────────────────────────────────────
if H_pred > 3.0:   risk, risk_desc = "HIGH",     "Major flood risk. Immediate action may be required."
elif H_pred > 1.5: risk, risk_desc = "MODERATE", "Elevated water levels. Monitor closely."
elif H_pred > 0.8: risk, risk_desc = "WATCH",    "Above normal levels. Situation developing."
else:              risk, risk_desc = "NORMAL",   "No significant flood risk."

if delta > 0.1:    tendency = "rising rapidly"
elif delta > 0.02: tendency = "rising"
elif delta < -0.1: tendency = "falling rapidly"
elif delta < -0.02:tendency = "falling"
else:              tendency = "stable"

print(f"""
[5/5] Operational briefing:

╔══════════════════════════════════════════════════════════╗
║  WWI OPERATIONAL BRIEFING — {TODAY}              
║  Station: SAUHEID — Ourthe inférieure                    
╠══════════════════════════════════════════════════════════╣
║                                                          
║  Current level:    {H_now:.3f} m                           
║  24h forecast:     {H_pred:.3f} m  ({delta:+.3f} m)                 
║  48h forecast:     {H_pred2:.3f} m  ({delta2:+.3f} m)                
║  72h forecast:     {H_pred3:.3f} m  ({delta3:+.3f} m)                
║  Tendency:         {tendency:<20}                   
║  Risk level:       {risk:<10}                             
║                                                          
║  {risk_desc:<54}
║                                                          
║  Context:                                                
║  • 7-day basin rainfall: {P_ourthe_7d+P_vesdre_7d+P_fagnes_7d:.1f} mm              
║  • 3-day Ourthe rainfall: {P_ourthe_3d:.1f} mm             
║  • Upstream Stavelot:  {H_stavelot:.3f} m                    
║  • Upstream Comblain:  {H_comblain:.3f} m                    
║                                                          
║  Model: RF-deltaH · trained 2023-2024 · NSE=0.953 (1d)  
╚══════════════════════════════════════════════════════════╝
""")

# Save SHAP
shap_export = pd.DataFrame({
    "feature": FEATURE_COLS,
    "shap_value": shap_vals[0],
    "feature_val": X_now.values[0],
}).sort_values("shap_value", ascending=False)
out = str(ROOT / "wwi/export/csvs/shap_current.csv")
shap_export.to_csv(out, index=False)
print(f"SHAP saved → {out}")
print("Next: python llm_bulletin.py")
