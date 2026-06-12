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
    models[horizon] = {"rf": rf, "med": med}

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
    # Align feature vector to training columns
    X_live = pd.DataFrame([fvec], columns=FEATURE_COLS)
    X_live = X_live.fillna(m["med"])

    delta  = float(m["rf"].predict(X_live)[0])
    H_pred = round(H_current + delta, 4)
    predictions[horizon] = H_pred
    h_int  = int(horizon.replace("t","").replace("h",""))
    target_time = now + timedelta(hours=h_int)
    log.info(f"  {horizon}: H={H_pred:.3f}m  (Δ={delta:+.3f}m)  "
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

H6  = predictions.get("t6h",  H_current)
H12 = predictions.get("t12h", H_current)
H24 = predictions.get("t24h", H_current)

# Risk level
if H6 > 3.5 or H12 > 3.5:   risk = "FLOOD_EMERGENCY"
elif H6 > 2.5:                risk = "FLOOD_ELEVATED"
elif H6 > 1.5:                risk = "FLOOD_WATCH"
elif H_current < 0.25:        risk = "DROUGHT_CRITICAL"
elif H_current < 0.45:        risk = "LOW_FLOW"
else:                          risk = "NORMAL"

print(f"""
╔══════════════════════════════════════════════════════════╗
║  WWI HOURLY BRIEFING — {TODAY:<34}
║  Station: SAUHEID — Ourthe inférieure
╠══════════════════════════════════════════════════════════╣
║  Current level:    {H_current:.3f} m
║  +6h  forecast:    {H6:.3f} m  ({H6-H_current:+.3f} m)
║  +12h forecast:    {H12:.3f} m  ({H12-H_current:+.3f} m)
║  +24h forecast:    {H24:.3f} m  ({H24-H_current:+.3f} m)
║  Risk level:       {risk}
║
║  Model: RF-deltaH hourly · NSE=0.981 (t+6h flood 2021)
╚══════════════════════════════════════════════════════════╝""")

# Log forecast
import csv, os
log_row = {
    "issued_utc":       datetime.now(timezone.utc).isoformat(),
    "H_current":        round(H_current, 4),
    "H_pred_t6h":       H6,
    "H_pred_t12h":      H12,
    "H_pred_t24h":      H24,
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
