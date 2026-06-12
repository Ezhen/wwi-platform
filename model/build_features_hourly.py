"""
WWI Hourly Feature Matrix Builder
Builds feature matrix at hourly resolution from historical_liege.db
Target: H at SAUHEID at t+6h, t+12h, t+24h

Key physics:
  - Eupen → Sauheid travel time: ~18h
  - Stavelot → Sauheid travel time: ~12h
  - Comblain → Sauheid travel time: ~6h
  - Rainfall → runoff lag: ~3-6h (Ardennes)

Output: export/csvs/features_sauheid_hourly.csv
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

ROOT     = Path(__file__).resolve().parent
DB_HIST  = str(ROOT / "export/databases/historical_liege.db")
DB_ERA5  = str(ROOT / "export/databases/era5_liege.db")
CSV_OUT  = str(ROOT / "export/csvs/features_sauheid_hourly.csv")

print("=" * 60)
print("WWI Hourly Feature Matrix — SAUHEID (Ourthe)")
print("=" * 60)

# ── 1. Load all hourly observations ──────────────────────────────────────────
print("\n[1/5] Loading hourly observations...")
con = sqlite3.connect(DB_HIST)

obs = pd.read_sql("""
    SELECT station_no, parameter, timestamp, value
    FROM observations
    WHERE value IS NOT NULL
      AND value < 10     -- exclude NGF absolute values
    ORDER BY timestamp
""", con)
con.close()

obs["timestamp"] = pd.to_datetime(obs["timestamp"], utc=True)
obs["timestamp"] = obs["timestamp"].dt.tz_localize(None)
obs = obs.set_index("timestamp")

print(f"  Total rows: {len(obs):,}")
print(f"  Range: {obs.index.min()} → {obs.index.max()}")
print(f"  Stations: {obs['station_no'].nunique()}")
print(f"  Parameters: {obs['parameter'].unique()}")

# Pivot to wide format
def get_series(station_no, param):
    mask = (obs["station_no"] == station_no) & (obs["parameter"] == param)
    s = obs[mask]["value"]
    s = s[~s.index.duplicated(keep="last")]
    return s.resample("1h").mean()  # ensure hourly

# Key stations
STATIONS = {
    "H_sauheid":      ("5826", "H"),
    "H_comblain":     ("5904", "H"),
    "H_stavelot":     ("6732", "H"),
    "H_troisponts":   ("6832", "H"),
    "H_eupen":        ("6387", "H"),
    "H_chaudf":       ("6228", "H"),
    "H_robertville":  ("6958", "H"),
    "Q_sauheid":      ("5826", "Q"),
    "Q_chaudf":       ("6228", "Q"),
    "P_fagnes":       ("6529", "Precip"),
    "P_ourthe":       ("6657", "Precip"),
    "P_vesdre":       ("6958", "Precip"),
}

# ── 2. Build base dataframe ───────────────────────────────────────────────────
print("\n[2/5] Building base dataframe...")
base = {}
for col, (sno, param) in STATIONS.items():
    s = get_series(sno, param)
    base[col] = s
    print(f"  {col:<25} n={s.notna().sum():>6,}  "
          f"{s.index.min().date()} → {s.index.max().date()}")

df = pd.DataFrame(base)
print(f"\n  Base shape: {df.shape}")

# ── 3. Build lag features ─────────────────────────────────────────────────────
print("\n[3/5] Building lag features...")

# Upstream H lags — wave propagation
LAG_HOURS = {
    "H_eupen":      [6, 12, 18, 24, 36, 48],   # 18h travel time
    "H_stavelot":   [3, 6, 9, 12, 18, 24],     # 12h travel time
    "H_troisponts": [3, 6, 9, 12],              # 9h travel time
    "H_comblain":   [1, 2, 3, 6, 9],            # 3-6h travel time
    "H_chaudf":     [1, 2, 3, 6],               # 2-3h travel time
    "H_robertville":[6, 12, 18],                # Vesdre headwater
    "H_sauheid":    [1, 2, 3, 6, 12, 24],      # autoregressive
    "Q_sauheid":    [1, 2, 3, 6],
    "Q_chaudf":     [1, 2, 3, 6],
}

for col, lags in LAG_HOURS.items():
    if col not in df.columns:
        continue
    for lag in lags:
        df[f"{col}_lag{lag}h"] = df[col].shift(lag)

# Precipitation rolling sums — key for flood initiation
PRECIP_WINDOWS = [1, 2, 3, 6, 12, 24, 48, 72]
for pcol in ["P_fagnes", "P_ourthe", "P_vesdre"]:
    if pcol not in df.columns:
        continue
    for w in PRECIP_WINDOWS:
        df[f"{pcol}_{w}h"] = df[pcol].rolling(w).sum()
    # Intensity (max in window)
    df[f"{pcol}_max6h"] = df[pcol].rolling(6).max()
    df[f"{pcol}_max12h"] = df[pcol].rolling(12).max()

# Rise rates — critical for flood early warning
for Hcol in ["H_sauheid", "H_eupen", "H_stavelot", "H_comblain"]:
    if Hcol not in df.columns:
        continue
    df[f"{Hcol}_dH1h"]  = df[Hcol].diff(1)
    df[f"{Hcol}_dH3h"]  = df[Hcol].diff(3)
    df[f"{Hcol}_dH6h"]  = df[Hcol].diff(6)
    df[f"{Hcol}_dH12h"] = df[Hcol].diff(12)

# Time features
df["hour"]         = df.index.hour
df["month"]        = df.index.month
df["sin_hour"]     = np.sin(2 * np.pi * df.index.hour / 24)
df["cos_hour"]     = np.cos(2 * np.pi * df.index.hour / 24)
df["sin_doy"]      = np.sin(2 * np.pi * df.index.dayofyear / 365.25)
df["cos_doy"]      = np.cos(2 * np.pi * df.index.dayofyear / 365.25)

print(f"  Features after lags: {df.shape[1]}")

# ── 4. Build targets ──────────────────────────────────────────────────────────
print("\n[4/5] Building forecast targets...")

TARGET_HOURS = [6, 12, 24]
for h in TARGET_HOURS:
    df[f"H_t{h}h"] = df["H_sauheid"].shift(-h)
    n = df[f"H_t{h}h"].notna().sum()
    print(f"  H_t{h}h: {n:,} valid rows")

# ── 5. Save ───────────────────────────────────────────────────────────────────
print("\n[5/5] Saving feature matrix...")

TARGET_COLS  = [f"H_t{h}h" for h in TARGET_HOURS]
FEATURE_COLS = [c for c in df.columns if c not in TARGET_COLS]

# Drop rows missing core features
core = ["H_sauheid","H_comblain","H_stavelot","H_eupen"]
df_clean = df.dropna(subset=core + TARGET_COLS[:1])
print(f"  Shape before clean: {df.shape}")
print(f"  Shape after clean:  {df_clean.shape}")

# Split info
train = df_clean["2023-01-01":"2024-12-31"]
test  = df_clean["2025-01-01":]
flood = df_clean["2021-06-14":"2021-09-29"]
print(f"\n  Train: {len(train):,} hours  ({train.index.min().date()} → {train.index.max().date()})")
print(f"  Test:  {len(test):,} hours   ({test.index.min().date()} → {test.index.max().date()})")
print(f"  Flood: {len(flood):,} hours  ({flood.index.min().date()} → {flood.index.max().date()})")

df_clean.to_csv(CSV_OUT)
print(f"\n✓ Saved → {CSV_OUT}")
print(f"  {df_clean.shape[1]} features × {len(df_clean):,} hourly rows")
print(f"\nNext: python model/train_model_hourly.py")
