"""
WWI — Feature matrix builder for river level forecast model.
Target: predict H at SAUHEID (Ourthe) 1/2/3 days ahead.
Features: lagged H, Q, precipitation, soil moisture proxy, seasonality.
Output: features_sauheid.csv ready for model training.
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path




ROOT = Path(__file__).resolve().parent.parent
DB_HIST = str(ROOT / "export/databases/historical_liege.db")
DB_ERA5 = str(ROOT / "export/databases/era5_liege.db")
OUT     = str(ROOT / "export/csvs/features_sauheid.csv")

print("=" * 60)
print("WWI Feature Matrix Builder")
print("Target: H at SAUHEID, horizons 1/2/3 days ahead")
print("=" * 60)

# ── 1. Load historical H and Q ────────────────────────────────────────────────
print("\n[1/6] Loading SPW historical data...")
con = sqlite3.connect(DB_HIST)

def load_ts(station_no, parameter, ts_name):
    df = pd.read_sql(f"""
        SELECT timestamp, value
        FROM observations
        WHERE station_no='{station_no}'
          AND parameter='{parameter}'
          AND ts_name='{ts_name}'
        ORDER BY timestamp
    """, con, parse_dates=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.normalize()
    df = df.groupby("timestamp")["value"].mean().reset_index()
    return df

# River level at key stations
H_sauheid      = load_ts("5826", "H", "Day.Mean")
H_chaudf       = load_ts("6228", "H", "Day.Mean")
H_eupen        = load_ts("6387", "H", "Day.Mean")
H_stavelot     = load_ts("6732", "H", "Day.Mean")
H_troisponts   = load_ts("6832", "H", "Day.Mean")
H_comblain     = load_ts("5904", "H", "Day.Mean")
H_huy          = load_ts("7141", "H", "Day.Mean")

# Discharge
Q_sauheid      = load_ts("5826", "Q", "Day.Mean")
Q_chaudf       = load_ts("6228", "Q", "Day.Mean")

# Precipitation
P_louveigne    = load_ts("6657", "Precip", "Day.Total")
P_robertville  = load_ts("6958", "Precip", "Day.Total")
P_montrigi     = load_ts("6529", "Precip", "Day.Total")

for name, df in [
    ("H_SAUHEID", H_sauheid), ("H_CHAUDF", H_chaudf),
    ("H_EUPEN", H_eupen), ("Q_SAUHEID", Q_sauheid),
    ("P_LOUVEIGNE", P_louveigne), ("P_ROBERTVILLE", P_robertville),
]:
    print(f"  {name:<18} {len(df):>5} rows  "
          f"{str(df['timestamp'].min())[:10]} → {str(df['timestamp'].max())[:10]}")

con.close()

# ── 2. Build base dataframe on SAUHEID date index ─────────────────────────────
print("\n[2/6] Building base dataframe...")

df = H_sauheid.rename(columns={"value": "H"}).copy()
df = df.set_index("timestamp")

def join_col(df, src, col_name):
    src = src.set_index("timestamp")["value"].rename(col_name)
    return df.join(src, how="left")

df = join_col(df, H_chaudf,      "H_chaudf")
df = join_col(df, H_eupen,       "H_eupen")
df = join_col(df, H_stavelot,    "H_stavelot")
df = join_col(df, H_troisponts,  "H_troisponts")
df = join_col(df, H_comblain,    "H_comblain")
df = join_col(df, H_huy,         "H_huy")
df = join_col(df, Q_sauheid,     "Q")
df = join_col(df, Q_chaudf,      "Q_chaudf")
df = join_col(df, P_louveigne,   "P_ourthe")
df = join_col(df, P_robertville, "P_vesdre")
df = join_col(df, P_montrigi,    "P_fagnes")

print(f"  Base shape: {df.shape}  ({df.index.min()} → {df.index.max()})")
print(f"  Null counts:\n{df.isnull().sum().to_string()}")

# ── 3. Lagged features ────────────────────────────────────────────────────────
print("\n[3/6] Building lagged features...")

# Autoregressive H lags
for lag in [1, 2, 3, 5, 7]:
    df[f"H_lag{lag}"] = df["H"].shift(lag)

# Upstream H lags — Eupen/Chaudfontaine lead Sauheid by ~1 day
for lag in [0, 1, 2]:
    df[f"H_eupen_lag{lag}"]  = df["H_eupen"].shift(lag)
    df[f"H_chaudf_lag{lag}"] = df["H_chaudf"].shift(lag)

# Q lags
for lag in [1, 2, 3]:
    df[f"Q_lag{lag}"] = df["Q"].shift(lag)

# Precipitation lags and rolling windows
for lag in [0, 1, 2, 3]:
    df[f"P_ourthe_lag{lag}"]  = df["P_ourthe"].shift(lag)
    df[f"P_vesdre_lag{lag}"]  = df["P_vesdre"].shift(lag)
    df[f"P_fagnes_lag{lag}"]  = df["P_fagnes"].shift(lag)

# Antecedent rainfall accumulations
for window in [3, 7, 14]:
    df[f"P_ourthe_{window}d"]  = df["P_ourthe"].rolling(window).sum()
    df[f"P_vesdre_{window}d"]  = df["P_vesdre"].rolling(window).sum()
    df[f"P_fagnes_{window}d"]  = df["P_fagnes"].rolling(window).sum()
    df[f"P_basin_{window}d"]   = (
        df["P_ourthe"].fillna(0) +
        df["P_vesdre"].fillna(0) +
        df["P_fagnes"].fillna(0)
    ).rolling(window).sum()

# H rise rate
df["H_delta1d"] = df["H"] - df["H"].shift(1)
df["H_delta3d"] = df["H"] - df["H"].shift(3)

# ── 4. ERA5 soil moisture ─────────────────────────────────────────────────────
print("\n[4/6] Loading ERA5 soil moisture...")
try:
    con_era5 = sqlite3.connect(DB_ERA5)
    era5 = pd.read_sql("""
        SELECT DATE(timestamp) AS date,
               AVG(value)      AS swvl1_mean
        FROM era5_observations
        WHERE variable = 'soil_water_layer_1'
        GROUP BY DATE(timestamp)
        ORDER BY date
    """, con_era5, parse_dates=["date"])
    con_era5.close()
    era5["date"] = pd.to_datetime(era5["date"], utc=True)
    era5 = era5.set_index("date")["swvl1_mean"].rename("swvl1")
    df = df.join(era5, how="left")
    # Rolling mean for soil moisture state
    df["swvl1_7d"] = df["swvl1"].rolling(7).mean()
    print(f"  ERA5 soil moisture: {era5.notna().sum()} days loaded")
except Exception as e:
    print(f"  ERA5 not available: {e} — skipping soil moisture")
    df["swvl1"]    = np.nan
    df["swvl1_7d"] = np.nan

# ── 5. Seasonality features ───────────────────────────────────────────────────
print("\n[5/6] Adding seasonality features...")
df["doy"]      = df.index.day_of_year
df["month"]    = df.index.month
df["sin_doy"]  = np.sin(2 * np.pi * df["doy"] / 365.25)
df["cos_doy"]  = np.cos(2 * np.pi * df["doy"] / 365.25)

# ── 6. Target variables ───────────────────────────────────────────────────────
print("\n[6/6] Building target variables...")
df["H_t1"] = df["H"].shift(-1)   # H tomorrow
df["H_t2"] = df["H"].shift(-2)   # H in 2 days
df["H_t3"] = df["H"].shift(-3)   # H in 3 days

# ── Final cleanup ─────────────────────────────────────────────────────────────
print("\nFinal cleanup...")

# Drop soil moisture cols — ERA5 historical not yet downloaded
df = df.drop(columns=["swvl1", "swvl1_7d"], errors="ignore")

# Drop rows where target is missing (edges of time series)
df = df.dropna(subset=["H_t1", "H_t2", "H_t3"])

# Report missingness
missing_pct = (df.isnull().sum() / len(df) * 100).sort_values(ascending=False)
print(f"  Shape after target dropna: {df.shape}")
print(f"  Features with >20% missing:")
print(missing_pct[missing_pct > 20].to_string())

# Save
df.to_csv(OUT)
print(f"\n✓ Saved → {OUT}")
print(f"  Rows: {len(df)}  Columns: {len(df.columns)}")
print(f"  Date range: {df.index.min()} → {df.index.max()}")
print(f"\n  Feature groups:")
target_cols  = ["H_t1", "H_t2", "H_t3"]
feature_cols = [c for c in df.columns if c not in target_cols]

print(f"    Features : {len(feature_cols)}")
print(f"    Targets  : {target_cols}")
print(f"\n  Sample (last 5 rows):")
print(df[["H","H_lag1","Q","P_basin_7d","H_t1","H_t2","H_t3"]].tail(5).to_string())
