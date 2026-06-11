"""
WWI Feature Matrix v2 — adds catchment-weighted ERA5 precipitation
Compares NSE against v1 to quantify improvement.

New features vs v1:
  - P_catchment_weighted_Xd  : ERA5 precip weighted by watershed ERA5 cells
  - P_eupen_weighted         : Vesdre headwater weighted precip
  - P_stavelot_weighted      : Amblève weighted precip
  - mean_slope               : from catchments_liege.db (if available)

Changelog entry added to export/csvs/model_versions.csv
"""

import sqlite3
import pandas as pd
import numpy as np
import json
from pathlib import Path

ROOT      = Path(__file__).parent
DB_HIST   = str(ROOT / "export/databases/historical_liege.db")
DB_ERA5   = str(ROOT / "export/databases/era5_liege.db")
DB_CATCH  = str(ROOT / "export/databases/catchments_liege.db")
CSV_NDVI  = str(ROOT / "export/csvs/ndvi_synthetic.csv")
CSV_V1    = str(ROOT / "export/csvs/features_sauheid.csv")
CSV_V2    = str(ROOT / "export/csvs/features_sauheid_v2.csv")
CHANGELOG = str(ROOT / "export/csvs/model_versions.csv")

print("=" * 60)
print("WWI Feature Matrix v2 — Catchment-weighted ERA5 precip")
print("=" * 60)

# ── 1. Load v1 features as base ───────────────────────────────────────────────
print("\n[1/5] Loading v1 feature matrix...")
df = pd.read_csv(CSV_V1, index_col=0, parse_dates=True)
df.index = df.index.tz_localize(None)
print(f"  v1 shape: {df.shape}")

# ── 2. Load ERA5 weights per station ─────────────────────────────────────────
print("\n[2/5] Loading ERA5 catchment weights...")
con_c = sqlite3.connect(DB_CATCH)

# Get weights for key stations
weight_rows = con_c.execute("""
    SELECT station_no, era5_lat, era5_lon, weight
    FROM era5_catchment_weights
    ORDER BY station_no, weight DESC
""").fetchall()

# Build weight dict: station_no → {(lat,lon): weight}
weights_by_station = {}
for sno, lat, lon, w in weight_rows:
    if sno not in weights_by_station:
        weights_by_station[sno] = {}
    weights_by_station[sno][(lat, lon)] = w

print(f"  Stations with ERA5 weights: {len(weights_by_station)}")
for sno, wts in sorted(weights_by_station.items()):
    print(f"    {sno}: {len(wts)} cells  weights={list(wts.values())[:3]}...")

con_c.close()

# ── 3. Load ERA5 swvl1 (soil moisture) and precipitation ──────────────────────
print("\n[3/5] Loading ERA5 soil moisture and precipitation...")
con_era5 = sqlite3.connect(DB_ERA5)

# Check available variables
era5_vars = [r[0] for r in con_era5.execute(
    "SELECT DISTINCT variable FROM era5_observations"
).fetchall()]
print(f"  Available ERA5 variables: {era5_vars}")

# ── Soil moisture (swvl1) — daily means already stored ────────────────────────
swvl1_var = next((v for v in era5_vars
                  if "soil" in v.lower() or "swvl" in v.lower()), None)
era5_swvl1 = pd.DataFrame()
if swvl1_var:
    swvl1_raw = pd.read_sql(f"""
        SELECT DATE(o.timestamp) AS date,
               g.lat, g.lon,
               AVG(o.value) AS swvl1
        FROM era5_observations o
        JOIN grid_points g ON o.grid_id = g.id
        WHERE o.variable = '{swvl1_var}'
        GROUP BY DATE(o.timestamp), g.id
        ORDER BY date
    """, con_era5, parse_dates=["date"])
    swvl1_raw["date"] = pd.to_datetime(swvl1_raw["date"])
    era5_swvl1 = swvl1_raw.pivot_table(
        index="date", columns=["lat","lon"], values="swvl1"
    )
    print(f"  swvl1 shape: {era5_swvl1.shape}  "
          f"{era5_swvl1.index.min().date()} → {era5_swvl1.index.max().date()}")
else:
    print("  swvl1 not found in ERA5 DB")

# ── Precipitation ─────────────────────────────────────────────────────────────
precip_var = next((v for v in era5_vars
                   if "precip" in v.lower() or v == "tp"), None)
era5_precip = pd.DataFrame()
if precip_var:
    precip_raw = pd.read_sql(f"""
        SELECT DATE(o.timestamp) AS date,
               g.lat, g.lon,
               SUM(o.value) * 1000 AS precip_mm
        FROM era5_observations o
        JOIN grid_points g ON o.grid_id = g.id
        WHERE o.variable = '{precip_var}'
        GROUP BY DATE(o.timestamp), g.id
        ORDER BY date
    """, con_era5, parse_dates=["date"])
    precip_raw["date"] = pd.to_datetime(precip_raw["date"])
    era5_precip = precip_raw.pivot_table(
        index="date", columns=["lat","lon"], values="precip_mm"
    )
    print(f"  ERA5 precip shape: {era5_precip.shape}")

con_era5.close()

# ── 4. Compute weighted precipitation time series ────────────────────────────
print("\n[4/5] Computing catchment-weighted precipitation...")

def weighted_precip(station_no, weights_dict, era5_df):
    """Compute area-weighted daily precip for a station's catchment."""
    if not weights_dict:
        return None
    series = pd.Series(0.0, index=era5_df.index)
    total_w = 0
    for (lat, lon), w in weights_dict.items():
        if (lat, lon) in era5_df.columns:
            series += era5_df[(lat, lon)].fillna(0) * w
            total_w += w
    if total_w > 0:
        series = series / total_w
    return series

# Compute for key stations
precip_weighted = {}
station_labels = {
    "5826": "sauheid",        # Ourthe at Sauheid
    "6228": "chaudfontaine",  # Vesdre at Chaudfontaine
    "6387": "eupen",          # Vesdre headwater
    "6732": "stavelot",       # Amblève
    "6832": "troisponts",     # Salm
    "7141": "huy",            # Meuse
    "6958": "robertville",    # Vesdre precip
    "6529": "montrigi",       # Fagnes precip
    "6657": "louveigne",      # Ourthe precip
}

for sno, label in station_labels.items():
    if sno in weights_by_station:
        series = weighted_precip(sno, weights_by_station[sno], era5_precip)
        if series is not None:
            precip_weighted[f"P_era5_{label}"] = series
            print(f"  ✓ P_era5_{label}: {series.notna().sum()} non-null days")
    else:
        print(f"  ✗ {label} ({sno}): no ERA5 weights")

# ── 3b. Compute catchment-weighted swvl1 ─────────────────────────────────────
print("\n[3b] Computing catchment-weighted soil moisture...")
swvl1_weighted = {}
if len(era5_swvl1) > 0:
    for sno, label in station_labels.items():
        if sno in weights_by_station:
            series = weighted_precip(sno, weights_by_station[sno], era5_swvl1)
            if series is not None and series.notna().sum() > 10:
                swvl1_weighted[f"swvl1_{label}"] = series
                print(f"  ✓ swvl1_{label}: {series.notna().sum()} non-null days")
else:
    print("  swvl1 not available — skipping")

# ── 4b. Load synthetic NDVI ──────────────────────────────────────────────────
print("\n[4b] Loading synthetic NDVI from CORINE...")
if Path(CSV_NDVI).exists():
    ndvi_df = pd.read_csv(CSV_NDVI, index_col=0, parse_dates=True)
    ndvi_df.index = pd.to_datetime(ndvi_df.index).tz_localize(None)
    # Keep only the raw NDVI columns (not anomaly) for key stations
    ndvi_cols = {
        "NDVI_5826_SAUHEID":    "ndvi_sauheid",
        "NDVI_6387_EUPEN":      "ndvi_eupen",
        "NDVI_6732_STAVELOT":   "ndvi_stavelot",
        "NDVI_6958_ROBERTVILLE":"ndvi_robertville",
        "NDVI_6529_MONT-RIGI":  "ndvi_montrigi",
    }
    ndvi_features = {}
    for src_col, dst_col in ndvi_cols.items():
        if src_col in ndvi_df.columns:
            ndvi_features[dst_col] = ndvi_df[src_col]
            print(f"  ✓ {dst_col}: {ndvi_df[src_col].notna().sum()} days")
    print(f"  NDVI date range: {ndvi_df.index.min().date()} → {ndvi_df.index.max().date()}")
else:
    ndvi_features = {}
    print("  NDVI CSV not found — skipping")

# ── 5. Build v2 feature matrix ────────────────────────────────────────────────
print("\n[5/5] Building v2 feature matrix...")

df_v2 = df.copy()

# Add weighted ERA5 precip and lagged versions
for col_name, series in precip_weighted.items():
    # Align index (ERA5 only covers May 2026, historical in features_v1 is 2021-2025)
    series.index = pd.to_datetime(series.index).tz_localize(None)
    df_v2 = df_v2.join(series.rename(col_name), how="left")

    # Lags and rolling windows
    for lag in [0, 1, 2, 3]:
        df_v2[f"{col_name}_lag{lag}"] = df_v2[col_name].shift(lag)
    for window in [3, 7, 14]:
        df_v2[f"{col_name}_{window}d"] = df_v2[col_name].rolling(window).sum()

# Join swvl1 soil moisture features
for col_name, series in swvl1_weighted.items():
    series.index = pd.to_datetime(series.index).tz_localize(None)
    df_v2 = df_v2.join(series.rename(col_name), how="left")
    # Rolling mean (soil moisture responds slowly)
    df_v2[f"{col_name}_7d"]  = df_v2[col_name].rolling(7).mean()
    df_v2[f"{col_name}_30d"] = df_v2[col_name].rolling(30).mean()
    # Anomaly vs 30-day mean
    df_v2[f"{col_name}_anom"] = (
        df_v2[col_name] - df_v2[f"{col_name}_30d"]
    )

# Join NDVI features
for col_name, series in ndvi_features.items():
    series.index = pd.to_datetime(series.index).tz_localize(None)
    df_v2 = df_v2.join(series.rename(col_name), how="left")
    # Lag 1 month (NDVI responds slowly)
    df_v2[f"{col_name}_lag30"] = df_v2[col_name].shift(30)
    # Interception proxy: NDVI × antecedent rainfall
    if "P_basin_7d" in df_v2.columns:
        df_v2[f"{col_name}_intercept"] = df_v2[col_name] * df_v2["P_basin_7d"].fillna(0)


# ── 4c. Load CORINE catchment fractions per station ─────────────────────────
print('\n[4c] Loading CORINE catchment fractions...')
corine_features = {}
db_catch_path = str(ROOT / 'export/databases/catchments_liege.db')
if Path(db_catch_path).exists():
    import sqlite3 as _sq
    _con = _sq.connect(db_catch_path)
    rows = _con.execute('''
        SELECT station_no, corine_forest_frac,
               corine_urban_frac, corine_agri_frac,
               slope_watershed_deg
        FROM catchments WHERE corine_forest_frac IS NOT NULL
    ''').fetchall()
    _con.close()
    STATION_LABELS = {
        '5826':'sauheid','6228':'chaudfontaine','6387':'eupen',
        '6732':'stavelot','6832':'troisponts','5904':'comblain',
        '7141':'huy','6958':'robertville','6529':'montrigi','6657':'louveigne',
    }
    for sno, forest, urban, agri, slope in rows:
        label = STATION_LABELS.get(sno)
        if not label: continue
        corine_features[f'forest_{label}'] = forest or 0.0
        corine_features[f'urban_{label}']  = urban  or 0.0
        corine_features[f'agri_{label}']   = agri   or 0.0
        if slope: corine_features[f'slope_{label}'] = slope
        print(f'  {label}: forest={forest:.2f} urban={urban:.2f}')
else:
    print('  catchments DB not found')

# Add static CORINE features to dataframe
for feat_name, feat_val in corine_features.items():
    df_v2[feat_name] = feat_val

# Basin-weighted composite (Ourthe basin = sauheid + louveigne ERA5)
if "P_era5_sauheid" in df_v2.columns and "P_era5_louveigne" in df_v2.columns:
    df_v2["P_era5_basin_7d"] = (
        df_v2.get("P_era5_sauheid_7d", 0).fillna(0) +
        df_v2.get("P_era5_louveigne_7d", 0).fillna(0) +
        df_v2.get("P_era5_montrigi_7d", 0).fillna(0)
    ) / 3
    print("  ✓ P_era5_basin_7d composite created")

TARGET_COLS = ["H_t1", "H_t2", "H_t3"]
df_v2 = df_v2.dropna(subset=TARGET_COLS)

print(f"\n  v1 shape: {df.shape}")
print(f"  v2 shape: {df_v2.shape}")
print(f"  New features added: {df_v2.shape[1] - df.shape[1]}")

# How many v2 features have actual data (non-null)?
new_cols = [c for c in df_v2.columns if c not in df.columns]
coverage = {c: df_v2[c].notna().sum() for c in new_cols}
print(f"\n  New feature coverage:")
for c, n in sorted(coverage.items(), key=lambda x: -x[1])[:15]:
    print(f"    {c:<35} {n:>5} non-null rows")

df_v2.to_csv(CSV_V2)
print(f"\n✓ Saved → {CSV_V2}")

# ── Quick NSE comparison ──────────────────────────────────────────────────────
print("\n── Quick model comparison ──────────────────────────────────────")
from sklearn.ensemble import RandomForestRegressor
import warnings
warnings.filterwarnings("ignore")

def nse(obs, sim):
    obs, sim = np.array(obs), np.array(sim)
    mask = ~(np.isnan(obs) | np.isnan(sim))
    obs, sim = obs[mask], sim[mask]
    if len(obs) == 0: return np.nan
    return 1 - np.sum((obs-sim)**2) / np.sum((obs-np.mean(obs))**2)

results = {}
for version, csv_path in [("v1", CSV_V1), ("v2", CSV_V2)]:
    df_m = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    df_m.index = df_m.index.tz_localize(None)
    target_cols = ["H_t1", "H_t2", "H_t3"]
    feature_cols = [c for c in df_m.columns if c not in target_cols]

    # Drop columns with >80% NaN before dropna on rows
    good_cols = [c for c in feature_cols
                 if df_m[c].isna().sum() / len(df_m) < 0.80]
    df_clean = df_m[good_cols + target_cols].dropna()
    feature_cols = good_cols
    train = df_clean["2023-01-01":"2024-12-31"]
    test  = df_clean["2025-01-01":]
    flood = df_clean["2021-06-14":"2021-09-29"]

    if len(train) < 100:
        print(f"  {version}: insufficient training data")
        continue

    target = "H_t1"
    tr_idx = train[target].dropna().index
    y_tr   = train.loc[tr_idx, target] - train.loc[tr_idx, "H"]
    X_tr   = train.loc[tr_idx, feature_cols]

    # Drop columns with >50% NaN in training
    good_cols = [c for c in feature_cols
                 if X_tr[c].isna().sum() / len(X_tr) < 0.5]
    X_tr = X_tr[good_cols].fillna(X_tr[good_cols].median())

    rf = RandomForestRegressor(n_estimators=200, max_depth=10,
                               min_samples_leaf=5, random_state=42, n_jobs=-1)
    rf.fit(X_tr, y_tr)

    # Test
    te_idx = test[target].dropna().index
    X_te   = test.loc[te_idx, good_cols].fillna(X_tr[good_cols].median())
    pred_te = test.loc[te_idx, "H"].values + rf.predict(X_te)
    nse_test = nse(test.loc[te_idx, target].values, pred_te)

    # Flood
    fl_idx = flood[target].dropna().index
    nse_flood = np.nan
    if len(fl_idx) > 0:
        X_fl = flood.loc[fl_idx, good_cols].fillna(X_tr[good_cols].median())
        pred_fl = flood.loc[fl_idx, "H"].values + rf.predict(X_fl)
        nse_flood = nse(flood.loc[fl_idx, target].values, pred_fl)

    results[version] = {"nse_test": nse_test, "nse_flood": nse_flood,
                        "n_features": len(good_cols)}
    print(f"  {version}: features={len(good_cols):>3}  "
          f"test NSE={nse_test:+.3f}  flood NSE={nse_flood:+.3f}")

# Changelog
if len(results) == 2:
    v1, v2 = results.get("v1"), results.get("v2")
    delta_test  = v2["nse_test"]  - v1["nse_test"]
    delta_flood = v2["nse_flood"] - v1["nse_flood"]
    print(f"\n  Δ test NSE:  {delta_test:+.3f}")
    print(f"  Δ flood NSE: {delta_flood:+.3f}")

    # Save to changelog
    import os
    from datetime import date
    log_row = (
        f"v1.2,catchment_weighted_ERA5,"
        f"{v2['nse_test']:.3f},{v2['nse_flood']:.3f},"
        f"{delta_test:+.3f},{delta_flood:+.3f},"
        f"{date.today()}\n"
    )
    header = "version,features_added,test_NSE_t1,flood_NSE_t1,delta_test,delta_flood,date\n"
    if not Path(CHANGELOG).exists():
        with open(CHANGELOG, "w") as f:
            f.write(header)
            f.write("v1.0,baseline,0.953,0.506,+0.000,+0.000,2026-06-08\n")
            f.write("v1.1,delta_H,0.974,0.670,+0.021,+0.164,2026-06-08\n")
    with open(CHANGELOG, "a") as f:
        f.write(log_row)
    print(f"\n✓ Changelog updated → {CHANGELOG}")

print("\nNext: python train_model.py  (using features_sauheid_v2.csv)")
