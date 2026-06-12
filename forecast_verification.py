"""
WWI Forecast Verification Log
Records daily forecast vs observed and computes rolling skill scores.
Run daily after live_explain.py to log yesterday's forecast accuracy.
Saves to export/csvs/forecast_verification.csv
"""

import sqlite3
import pandas as pd
import numpy as np
import json
import logging
from pathlib import Path
from datetime import date, timedelta, datetime

ROOT = Path(__file__).resolve().parent
DB_SPW   = str(ROOT / "export/databases/spw_liege.db")
CSV_FEAT = str(ROOT / "export/csvs/features_sauheid.csv")
CSV_VER  = str(ROOT / "export/csvs/forecast_verification.csv")
ARCH_DIR = ROOT / "export" / "csvs" / "archive"
ARCH_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

TODAY     = date.today()
YESTERDAY = TODAY - timedelta(days=1)

log.info("=" * 55)
log.info(f"WWI Forecast Verification — {TODAY}")
log.info("=" * 55)

# ── 1. Get today's observed daily mean H ──────────────────────────────────────
log.info("\n[1/4] Loading observed H...")
con = sqlite3.connect(DB_SPW)
obs_rows = con.execute("""
    SELECT DATE(timestamp) AS day,
           ROUND(AVG(value), 4) AS H_mean,
           ROUND(MIN(value), 4) AS H_min,
           ROUND(MAX(value), 4) AS H_max,
           COUNT(*)             AS n_obs
    FROM observations
    WHERE station_no = '5826'
      AND parameter  = 'H'
      AND DATE(timestamp) >= DATE('now', '-30 days')
      AND value IS NOT NULL
      AND value < 10
      AND value < 10
    GROUP BY DATE(timestamp)
    ORDER BY day
""").fetchall()
con.close()

obs_df = pd.DataFrame(obs_rows,
    columns=["date","H_mean","H_min","H_max","n_obs"])
obs_df["date"] = pd.to_datetime(obs_df["date"])
obs_df = obs_df.set_index("date")
log.info(f"  {len(obs_df)} days of observed H loaded")
log.info(f"  Latest: {obs_df.index[-1].date()}  H={obs_df['H_mean'].iloc[-1]:.3f}m")

# ── 2. Retrain model and generate retrospective forecasts ─────────────────────
log.info("\n[2/4] Retraining model and generating retrospective forecasts...")

import warnings
warnings.filterwarnings("ignore")
from sklearn.ensemble import RandomForestRegressor




df = pd.read_csv(CSV_FEAT, index_col=0, parse_dates=True)
df.index = df.index.tz_localize(None)

TARGET_COLS  = ["H_t1","H_t2","H_t3"]
FEATURE_COLS = [c for c in df.columns if c not in TARGET_COLS]

train = df["2023-01-01":"2024-12-31"].dropna(
    subset=["H_t1","H","H_lag1","Q"])

tr_idx = train["H_t1"].dropna().index
y_tr   = train.loc[tr_idx,"H_t1"] - train.loc[tr_idx,"H"]
X_tr   = train.loc[tr_idx, FEATURE_COLS].fillna(
             train.loc[tr_idx, FEATURE_COLS].median())

rf = RandomForestRegressor(n_estimators=200, max_depth=10,
                           min_samples_leaf=5, random_state=42, n_jobs=-1)
rf.fit(X_tr, y_tr)
log.info(f"  Model trained on {len(X_tr)} days")

# Load forecast log written by live_explain.py
forecast_log = ROOT / "export/csvs/forecast_log.csv"
forecast_df  = pd.DataFrame()
if forecast_log.exists():
    fc = pd.read_csv(str(forecast_log), parse_dates=["target_date_t1"])
    fc["target_date_t1"] = pd.to_datetime(fc["target_date_t1"])
    fc = fc.set_index("target_date_t1")
    forecast_df = fc[["H_pred_t1"]].rename(
        columns={"H_pred_t1": "H_forecast_t1"})
    log.info(f"  Loaded {len(forecast_df)} logged forecasts")
    log.info(f"  Range: {fc.index.min().date()} → {fc.index.max().date()}")
else:
    log.info("  No forecast_log.csv yet — run live_explain.py first")

# ── 3. Load or create verification CSV ───────────────────────────────────────
log.info("\n[3/4] Updating verification log...")

if Path(CSV_VER).exists():
    ver_df = pd.read_csv(CSV_VER, index_col=0, parse_dates=True)
    log.info(f"  Loaded {len(ver_df)} existing verification rows")
else:
    ver_df = pd.DataFrame(columns=[
        "date","H_obs","H_forecast_t1","H_forecast_t2","H_forecast_t3",
        "error_t1","abs_error_t1","direction_correct",
        "H_persistence","error_persistence","n_obs_records"
    ])
    ver_df = ver_df.set_index("date")
    log.info("  Created new verification log")

# Add new rows for dates we have both forecast and observed
new_rows = []
for dt in obs_df.index:
    if dt in ver_df.index:
        continue  # already logged

    H_obs = obs_df.loc[dt, "H_mean"]
    n_obs = obs_df.loc[dt, "n_obs"]

    # Get t-1 forecast (what we predicted yesterday for today)
    dt_minus1 = dt - timedelta(days=1)
    H_fc_t1 = None
    if len(forecast_df) > 0 and dt in forecast_df.index:
        val = forecast_df.loc[dt, "H_forecast_t1"]
        val_scalar = val.iloc[0] if hasattr(val, "iloc") else val
        H_fc_t1 = float(val_scalar) if pd.notna(val_scalar) else None

    # Persistence forecast (yesterday's H)
    H_pers = float(obs_df.loc[dt_minus1, "H_mean"]) \
             if dt_minus1 in obs_df.index else None

    # Compute errors
    err_t1 = round(H_fc_t1 - H_obs, 4) if H_fc_t1 else None
    err_pers = round(H_pers - H_obs, 4) if H_pers else None

    # Direction: did model correctly predict rise/fall?
    dir_correct = None
    if H_fc_t1 and H_pers:
        predicted_direction = H_fc_t1 > H_pers
        actual_direction    = H_obs > H_pers
        dir_correct         = predicted_direction == actual_direction

    new_rows.append({
        "date":             dt,
        "H_obs":            round(H_obs, 4),
        "H_forecast_t1":    round(H_fc_t1, 4) if H_fc_t1 else None,
        "H_persistence":    round(H_pers, 4) if H_pers else None,
        "error_t1":         err_t1,
        "abs_error_t1":     abs(err_t1) if err_t1 else None,
        "error_persistence": err_pers,
        "direction_correct": dir_correct,
        "n_obs_records":    int(n_obs),
    })

if new_rows:
    new_df = pd.DataFrame(new_rows).set_index("date")
    ver_df = pd.concat([ver_df, new_df])
    ver_df = ver_df.sort_index()
    ver_df.to_csv(CSV_VER)
    log.info(f"  Added {len(new_rows)} new rows")
else:
    log.info("  No new rows to add")

# ── 4. Print skill summary ─────────────────────────────────────────────────────
log.info("\n[4/4] Skill summary:")

valid = ver_df.dropna(subset=["error_t1","error_persistence"])

if len(valid) > 0:
    rmse_model = np.sqrt((valid["error_t1"]**2).mean())
    rmse_pers  = np.sqrt((valid["error_persistence"]**2).mean())
    mae_model  = valid["abs_error_t1"].mean()
    nse_num    = ((valid["H_obs"] - valid["H_forecast_t1"])**2).sum()
    nse_den    = ((valid["H_obs"] - valid["H_obs"].mean())**2).sum()
    nse        = 1 - nse_num/nse_den if nse_den > 0 else None
    dir_acc    = valid["direction_correct"].mean() * 100 \
                 if "direction_correct" in valid.columns else None

    log.info(f"\n  Period: {valid.index[0].date()} → {valid.index[-1].date()}")
    log.info(f"  Days verified: {len(valid)}")
    log.info(f"\n  {'Metric':<25} {'RF Model':>10} {'Persistence':>12}")
    log.info(f"  {'─'*50}")
    log.info(f"  {'RMSE (m)':<25} {rmse_model:>10.4f} {rmse_pers:>12.4f}")
    log.info(f"  {'MAE (m)':<25} {mae_model:>10.4f}")
    if nse: log.info(f"  {'NSE':<25} {nse:>+10.3f}")
    if dir_acc: log.info(f"  {'Direction accuracy':<25} {dir_acc:>9.1f}%")

    log.info(f"\n  Last 7 days:")
    log.info(f"  {'Date':<12} {'H_obs':>7} {'H_fc':>7} {'Error':>7} "
             f"{'Pers':>7} {'Direction':>10}")
    log.info(f"  {'─'*55}")
    for dt, row in valid.tail(7).iterrows():
        fc  = f"{row['H_forecast_t1']:.3f}" if pd.notna(row['H_forecast_t1']) else "  N/A"
        err = f"{row['error_t1']:+.3f}"     if pd.notna(row['error_t1'])       else "  N/A"
        per = f"{row['H_persistence']:.3f}" if pd.notna(row['H_persistence'])  else "  N/A"
        dir_s = "✓" if row.get("direction_correct") else "✗" \
                if row.get("direction_correct") == False else "—"
        log.info(f"  {dt.date()}  {row['H_obs']:>7.3f} {fc:>7} "
                 f"{err:>7} {per:>7} {dir_s:>10}")
else:
    log.info("  Not enough verified days yet")

# Archive
arch_path = ARCH_DIR / f"verification_{TODAY}.csv"
ver_df.to_csv(str(arch_path))
log.info(f"\n✓ Saved → {CSV_VER}")
log.info(f"✓ Archived → {arch_path}")
