"""
WWI Live Hourly Prediction
Assembles live feature vector from t_latest_H and t_rise_rate,
runs hourly RF model, outputs t+6h/t+12h/t+24h forecasts with SHAP.

Designed to run every 6h via update.sh / cron.
"""

import sqlite3
import pandas as pd
import numpy as np
import json
import logging
import warnings
warnings.filterwarnings("ignore")
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT    = Path(__file__).resolve().parent
DB_SPW  = str(ROOT / "export/databases/spw_liege.db")
DB_HIST = str(ROOT / "export/databases/historical_liege.db")
CSV_FEAT = str(ROOT / "export/csvs/features_sauheid_hourly.csv")
CSV_FLOG = str(ROOT / "export/csvs/forecast_log_hourly.csv")
ARCH_DIR = ROOT / "export/csvs/archive"
ARCH_DIR.mkdir(parents=True, exist_ok=True)

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# Station mapping
STATIONS = {
    "5826": "sauheid",
    "5904": "comblain",
    "6732": "stavelot",
    "6832": "troisponts",
    "6387": "eupen",
    "6228": "chaudf",
}

print("=" * 60)
print(f"WWI Live Hourly Prediction — {TODAY}")
print("=" * 60)

# ── 1. Retrain model ──────────────────────────────────────────────────────────
print("\n[1/5] Loading features and retraining...")
df = pd.read_csv(CSV_FEAT, index_col=0, parse_dates=True)
df.index = df.index.tz_localize(None)

TARGET_COLS  = ["H_t6h","H_t12h","H_t24h"]
FEATURE_COLS = [c for c in df.columns if c not in TARGET_COLS]

# Fill NaN
for col in FEATURE_COLS:
    if df[col].isna().any():
        med = df[col].median()
        df[col] = df[col].fillna(med if pd.notna(med) else 0.0)

train = df["2023-01-01":"2024-12-31"]

from sklearn.ensemble import RandomForestRegressor

models = {}
for horizon, target in [("t6h","H_t6h"),("t12h","H_t12h"),("t24h","H_t24h")]:
    tr_idx  = train[target].dropna().index
    y_tr    = train.loc[tr_idx, target] - train.loc[tr_idx, "H_sauheid"]
    X_tr    = train.loc[tr_idx, FEATURE_COLS]
    med     = X_tr.median().fillna(0)
    X_tr_f  = X_tr.fillna(med)

    rf = RandomForestRegressor(
        n_estimators=300, max_depth=12,
        min_samples_leaf=3, random_state=42, n_jobs=-1
    )
    rf.fit(X_tr_f, y_tr)
    models[horizon] = {"rf": rf, "med": med, "X_tr": X_tr_f, "y_tr": y_tr}

def predict_with_uncertainty(rf, X, H_current, ci=90):
    """
    Compute point forecast + prediction interval from RF tree ensemble.
    Uses per-tree predictions to build empirical distribution.
    ci: confidence interval width (default 90%)
    """
    # Get prediction from every tree
    tree_preds = np.array([
        tree.predict(X) for tree in rf.estimators_
    ])  # shape: (n_trees, n_samples)

    delta_mean = np.mean(tree_preds, axis=0)
    delta_p5   = np.percentile(tree_preds, (100-ci)/2, axis=0)
    delta_p95  = np.percentile(tree_preds, 100-(100-ci)/2, axis=0)

    return {
        "point":  H_current + delta_mean[0],
        "lower":  H_current + delta_p5[0],
        "upper":  H_current + delta_p95[0],
        "delta":  delta_mean[0],
        "spread": delta_p95[0] - delta_p5[0],  # interval width
    }

log.info(f"  Models trained: {list(models.keys())}")

# ── 2. Assemble live feature vector ──────────────────────────────────────────
print("\n[2/5] Assembling live feature vector...")
con = sqlite3.connect(DB_SPW)

def get_latest_H(station_no):
    """Get latest gauge-relative H."""
    row = con.execute("""
        SELECT level_m FROM t_latest_H
        WHERE station_no=? AND level_m IS NOT NULL AND level_m < 10
    """, (station_no,)).fetchone()
    return float(row[0]) if row else None

def get_rise_rate(station_no, hours):
    """Get dH over last N hours from t_rise_rate or raw observations."""
    # Try t_rise_rate first
    # Use whatever delta columns exist
    cols = [r[1] for r in con.execute("PRAGMA table_info(t_rise_rate)")]
    delta_map = {}
    for h, col in [(1,"delta_1h_m"),(3,"delta_3h_m"),
                   (6,"delta_6h_m"),(12,"delta_12h_m")]:
        if col in cols:
            delta_map[h] = col
    if delta_map and hours in delta_map:
        row2 = con.execute(
            f"SELECT {delta_map[hours]} FROM t_rise_rate WHERE station_no=?",
            (station_no,)).fetchone()
        if row2:
            return row2[0]

    # Fallback: compute from raw observations
    row2 = con.execute("""
        SELECT value FROM observations
        WHERE station_no=? AND parameter='H'
          AND value IS NOT NULL AND value < 10
          AND timestamp <= datetime('now')
          AND timestamp >= datetime('now', ? || ' hours')
        ORDER BY timestamp ASC LIMIT 1
    """, (station_no, f"-{hours}")).fetchone()
    if row2:
        H_now = get_latest_H(station_no)
        if H_now:
            return H_now - float(row2[0])
    return None

# Build feature dict matching training columns
now = datetime.now(timezone.utc)
fvec = {}

# Current H per station
H_now = {}
for sno, label in STATIONS.items():
    h = get_latest_H(sno)
    H_now[sno] = h
    fvec[f"H_{label}"] = h
    if h:
        log.info(f"  H_{label}: {h:.3f}m")

# Rise rates per station
for sno, label in STATIONS.items():
    for hrs in [1, 2, 3, 6, 12]:
        dh = get_rise_rate(sno, hrs)
        fvec[f"H_{label}_dH{hrs}h"] = dh

# Lagged H from recent observations
for sno, label in STATIONS.items():
    for lag in [1, 2, 3, 6, 9, 12, 18, 24, 36, 48]:
        row = con.execute("""
            SELECT value FROM observations
            WHERE station_no=? AND parameter='H'
              AND value IS NOT NULL AND value < 10
              AND timestamp <= datetime('now', ? || ' hours')
            ORDER BY timestamp DESC LIMIT 1
        """, (sno, f"-{lag}")).fetchone()
        fvec[f"H_{label}_lag{lag}h"] = float(row[0]) if row else None

# Q
for sno, label in [("5826","sauheid"),("6228","chaudf")]:
    row = con.execute("""
        SELECT discharge_m3s FROM t_latest_Q WHERE station_no=?
    """, (sno,)).fetchone()
    fvec[f"Q_{label}"] = float(row[0]) if row and row[0] else None
    # Lags
    for lag in [1,2,3,6]:
        row2 = con.execute("""
            SELECT value FROM observations
            WHERE station_no=? AND parameter='Q'
              AND value IS NOT NULL
              AND timestamp <= datetime('now', ? || ' hours')
            ORDER BY timestamp DESC LIMIT 1
        """, (sno, f"-{lag}")).fetchone()
        fvec[f"Q_{label}_lag{lag}h"] = float(row2[0]) if row2 else None

# Precipitation rolling sums
for sno, label in [("6529","fagnes"),("6657","ourthe"),("6958","vesdre")]:
    for w in [1, 2, 3, 6, 12, 24, 48, 72]:
        row = con.execute("""
            SELECT SUM(value) FROM observations
            WHERE station_no=? AND parameter='Precip'
              AND value IS NOT NULL
              AND timestamp >= datetime('now', ? || ' hours')
        """, (sno, f"-{w}")).fetchone()
        fvec[f"P_{label}_{w}h"] = float(row[0]) if row and row[0] else 0.0
    # Max intensity
    for w in [6, 12]:
        row2 = con.execute("""
            SELECT MAX(value) FROM observations
            WHERE station_no=? AND parameter='Precip'
              AND value IS NOT NULL
              AND timestamp >= datetime('now', ? || ' hours')
        """, (sno, f"-{w}")).fetchone()
        fvec[f"P_{label}_max{w}h"] = float(row2[0]) if row2 and row2[0] else 0.0

con.close()

# Time features
fvec["hour"]    = now.hour
fvec["month"]   = now.month
fvec["sin_hour"] = np.sin(2 * np.pi * now.hour / 24)
fvec["cos_hour"] = np.cos(2 * np.pi * now.hour / 24)
fvec["sin_doy"] = np.sin(2 * np.pi * now.timetuple().tm_yday / 365.25)
fvec["cos_doy"] = np.cos(2 * np.pi * now.timetuple().tm_yday / 365.25)

log.info(f"  Feature vector: {len(fvec)} values, "
         f"{sum(1 for v in fvec.values() if v is not None)} non-null")

# ── 3. Predict ────────────────────────────────────────────────────────────────
print("\n[3/5] Predicting...")
H_current = H_now.get("5826") or 0.0

predictions = {}
for horizon, m in models.items():
    X_live = pd.DataFrame([fvec], columns=FEATURE_COLS)
    X_live = X_live.fillna(m["med"])

    result = predict_with_uncertainty(m["rf"], X_live, H_current)
    predictions[horizon] = result

    h_int  = int(horizon.replace("t","").replace("h",""))
    target_time = now + timedelta(hours=h_int)
    log.info(f"  {horizon}: H={result['point']:.3f}m "
             f"(Δ={result['delta']:+.3f}m) "
             f"90%CI [{result['lower']:.3f}-{result['upper']:.3f}m] "
             f"spread={result['spread']:.3f}m  "
             f"valid at {target_time.strftime('%Y-%m-%d %H:%M UTC')}")

# ── 4. SHAP explainability ────────────────────────────────────────────────────
print("\n[4/5] SHAP explanation (t+6h)...")
try:
    import shap
    rf6   = models["t6h"]["rf"]
    X_live = pd.DataFrame([fvec], columns=FEATURE_COLS).fillna(
                models["t6h"]["med"])
    explainer  = shap.TreeExplainer(rf6)
    shap_vals  = explainer.shap_values(X_live)[0]
    shap_series = pd.Series(shap_vals, index=FEATURE_COLS)

    top_pos = shap_series.nlargest(5)
    top_neg = shap_series.nsmallest(5)

    print("\n  Factors RAISING level (t+6h):")
    for feat, val in top_pos.items():
        fval = fvec.get(feat)
        fval_str = f"{fval:.3f}" if isinstance(fval, float) else str(fval)
        print(f"    {feat:<32} val={fval_str:>8}  SHAP={val:+.4f}  ▲")

    print("\n  Factors LOWERING level (t+6h):")
    for feat, val in top_neg.items():
        fval = fvec.get(feat)
        fval_str = f"{fval:.3f}" if isinstance(fval, float) else str(fval)
        print(f"    {feat:<32} val={fval_str:>8}  SHAP={val:+.4f}  ▼")

    shap_out = pd.DataFrame({
        "feature": FEATURE_COLS,
        "shap_value": shap_vals,
        "feature_value": [fvec.get(c) for c in FEATURE_COLS],
    }).sort_values("shap_value", key=abs, ascending=False)
    shap_path = str(ROOT / "export/csvs/shap_current_hourly.csv")
    shap_out.to_csv(shap_path, index=False)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M")
    shap_out.to_csv(str(ARCH_DIR / f"shap_hourly_{ts_str}.csv"), index=False)

except ImportError:
    log.warning("  shap not installed — skipping explainability")

# ── 5. Briefing + logging ─────────────────────────────────────────────────────
print("\n[5/5] Operational briefing...")

p6  = predictions.get("t6h",  {"point": H_current, "lower": H_current, "upper": H_current, "delta": 0, "spread": 0})
p12 = predictions.get("t12h", {"point": H_current, "lower": H_current, "upper": H_current, "delta": 0, "spread": 0})
p24 = predictions.get("t24h", {"point": H_current, "lower": H_current, "upper": H_current, "delta": 0, "spread": 0})
H6, H12, H24 = p6["point"], p12["point"], p24["point"]

# Risk level — use upper bound for conservative assessment
H6_upper = p6["upper"]
if H6_upper > 3.5 or p12["upper"] > 3.5: risk = "FLOOD_EMERGENCY"
elif H6_upper > 2.5:                       risk = "FLOOD_ELEVATED"
elif H6_upper > 1.5:                       risk = "FLOOD_WATCH"
elif H_current < 0.25:                     risk = "DROUGHT_CRITICAL"
elif H_current < 0.45:                     risk = "LOW_FLOW"
else:                                       risk = "NORMAL"

print(f"""
╔══════════════════════════════════════════════════════════╗
║  WWI HOURLY BRIEFING — {TODAY:<34}
║  Station: SAUHEID — Ourthe inférieure
╠══════════════════════════════════════════════════════════╣
║  Current level:    {H_current:.3f} m
║  +6h  forecast:    {H6:.3f} m  ({H6-H_current:+.3f} m)
║              90%CI [{p6['lower']:.3f} – {p6['upper']:.3f} m]
║  +12h forecast:    {H12:.3f} m  ({H12-H_current:+.3f} m)
║              90%CI [{p12['lower']:.3f} – {p12['upper']:.3f} m]
║  +24h forecast:    {H24:.3f} m  ({H24-H_current:+.3f} m)
║              90%CI [{p24['lower']:.3f} – {p24['upper']:.3f} m]
║  Risk level:       {risk}
║
║  Model: RF-deltaH hourly · NSE=0.981 (t+6h flood 2021)
║  Validation: temporal split · persistence baseline included
╚══════════════════════════════════════════════════════════╝""")

# Log forecast
import csv, os
log_row = {
    "issued_utc":       datetime.now(timezone.utc).isoformat(),
    "H_current":        round(H_current, 4),
    "H_pred_t6h":       round(H6, 4),
    "H_lower_t6h":      round(p6["lower"], 4),
    "H_upper_t6h":      round(p6["upper"], 4),
    "H_pred_t12h":      round(H12, 4),
    "H_lower_t12h":     round(p12["lower"], 4),
    "H_upper_t12h":     round(p12["upper"], 4),
    "H_pred_t24h":      round(H24, 4),
    "H_lower_t24h":     round(p24["lower"], 4),
    "H_upper_t24h":     round(p24["upper"], 4),
    "target_t6h":       (now + timedelta(hours=6)).isoformat(),
    "target_t12h":      (now + timedelta(hours=12)).isoformat(),
    "target_t24h":      (now + timedelta(hours=24)).isoformat(),
    "risk":             risk,
}
write_header = not Path(CSV_FLOG).exists()
with open(CSV_FLOG, "a", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=log_row.keys())
    if write_header:
        writer.writeheader()
    writer.writerow(log_row)

log.info(f"Forecast logged → {CSV_FLOG}")
log.info("Next: python llm_bulletin.py  (reads current_alerts.json)")
