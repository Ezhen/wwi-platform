"""
WWI — LLM Bulletin Generator
Reads the current SHAP explanation and operational state,
calls Claude API to generate a natural language daily briefing.
Output: human-readable bulletin for water managers.
"""

import json
from datetime import datetime
import sqlite3
import pandas as pd
import requests
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent
SHAP_CSV     = str(ROOT / "export/csvs/shap_current.csv")
DB_FORECAST  = str(ROOT / "export/databases/forecast_liege.db")
OUT_BULLETIN = str(ROOT / "export/csvs/daily_bulletin.txt")

TODAY = date.today().isoformat()

print("=" * 60)
print(f"WWI LLM Bulletin Generator — {TODAY}")
print("=" * 60)

# ── 1. Load SHAP explanation ──────────────────────────────────────────────────
print("\n[1/4] Loading SHAP values...")
shap_df = pd.read_csv(SHAP_CSV)
top_raising = shap_df[shap_df["shap_value"] > 0].nlargest(5, "shap_value")
top_lowering = shap_df[shap_df["shap_value"] < 0].nsmallest(5, "shap_value")

# ── 2. Load forecast alert ────────────────────────────────────────────────────
print("[2/4] Loading forecast data...")
con = sqlite3.connect(DB_FORECAST)
forecast_rows = con.execute("""
    SELECT description, precip_24h_mm, precip_72h_mm, alert_24h, alert_72h
    FROM v_forecast_alert
    ORDER BY precip_24h_mm DESC
""").fetchall()
con.close()

forecast_summary = "\n".join([
    f"  {r[0]}: 24h={r[1]:.1f}mm 72h={r[2]:.1f}mm alert={r[3]}/{r[4]}"
    for r in forecast_rows
])

# ── 3. Build the SHAP-readable feature names ──────────────────────────────────
FEATURE_LABELS = {
    "P_fagnes":        "Hautes Fagnes precipitation (today)",
    "P_fagnes_lag0":   "Hautes Fagnes precipitation (today)",
    "P_fagnes_lag1":   "Hautes Fagnes precipitation (yesterday)",
    "P_fagnes_7d":     "7-day Hautes Fagnes rainfall accumulation",
    "P_fagnes_3d":     "3-day Hautes Fagnes rainfall",
    "P_ourthe":        "Ourthe basin precipitation (today)",
    "P_ourthe_lag1":   "Ourthe basin precipitation (yesterday)",
    "P_ourthe_7d":     "7-day Ourthe basin rainfall",
    "P_vesdre":        "Vesdre basin precipitation (today)",
    "P_vesdre_lag1":   "Vesdre basin precipitation (yesterday)",
    "P_vesdre_3d":     "3-day Vesdre rainfall",
    "H":               "current water level at Sauheid",
    "H_lag1":          "water level at Sauheid yesterday",
    "H_chaudf":        "water level at Chaudfontaine (Vesdre)",
    "H_chaudf_lag1":   "water level at Chaudfontaine yesterday",
    "H_stavelot":      "water level at Stavelot (Amblève)",
    "H_comblain":      "water level at Comblain (Ourthe)",
    "H_eupen":         "water level at Eupen (Vesdre headwater)",
    "H_huy":           "water level at Huy (Meuse downstream)",
    "H_delta1d":       "24h water level change at Sauheid",
    "H_delta3d":       "3-day water level change at Sauheid",
    "Q":               "current discharge at Sauheid",
    "Q_lag1":          "discharge at Sauheid yesterday",
    "Q_chaudf":        "discharge at Chaudfontaine",
    "doy":             "day of year (seasonality)",
    "cos_doy":         "seasonal cycle position",
    "P_basin_7d":      "7-day basin-wide rainfall total",
}

def label(feat):
    return FEATURE_LABELS.get(feat, feat.replace("_", " "))

raising_text = "\n".join([
    f"  + {label(r['feature'])} (val={r['feature_val']:.2f}, SHAP=+{r['shap_value']:.4f})"
    for _, r in top_raising.iterrows()
])
lowering_text = "\n".join([
    f"  - {label(r['feature'])} (val={r['feature_val']:.2f}, SHAP={r['shap_value']:.4f})"
    for _, r in top_lowering.iterrows()
])

# ── 4. Call Claude API ────────────────────────────────────────────────────────
print("[3/4] Calling Claude API...")

PROMPT = f"""You are a hydrological analyst for the Wallonia Water Intelligence Platform (WWI).
Generate a concise, professional daily water bulletin for water managers at SPW, SWDE, and INASEP.

The bulletin should be written in clear English, suitable for a non-technical manager who needs to act on it.
It should explain the current situation, why the river is doing what it is doing, and what to expect.

CURRENT STATE — {TODAY}:
- Station: SAUHEID on the Ourthe river (Liège province, Belgium)
- Current water level: 0.361 m (low summer level, well below flood threshold of ~1.5m)
- 24h forecast: 0.360 m (stable, -0.001m)
- 48h forecast: 0.359 m
- 72h forecast: 0.358 m
- Risk level: NORMAL
- 7-day basin rainfall: 9.9 mm (dry week)
- 3-day Ourthe rainfall: 0.4 mm

MODEL EXPLANATION (SHAP values — what is driving the forecast):
Factors currently RAISING river level:
{raising_text}

Factors currently LOWERING river level:
{lowering_text}

7-DAY RAINFALL FORECAST (Open-Meteo):
{forecast_summary}

CONTEXT:
- The Ourthe drains the Ardennes highlands including the Hautes Fagnes, 
  the highest rainfall zone in Belgium (~1400mm/year).
- Normal summer low flow at Sauheid is 0.3-0.6m. Flood alert threshold is ~1.5m.
- The July 2021 flood peaked at 3.5m daily mean (6.65m hourly at Chaudfontaine).
- Upstream stations Stavelot (Amblève, 0.837m) and Comblain (Ourthe, 0.288m) 
  are both at normal summer levels.

Write a bulletin of 3-4 short paragraphs covering:
1. Current situation summary
2. What is driving the current level (plain language SHAP explanation)
3. 72-hour outlook and any precautions
4. One sentence on data quality / confidence

Do not use technical jargon like SHAP, Random Forest, or NSE. 
Write as if briefing a regional water manager on Monday morning."""

import os
api_key = os.environ.get("ANTHROPIC_API_KEY", "")
if not api_key:
    print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
    print("Run: export ANTHROPIC_API_KEY='sk-ant-...'")
    exit(1)

response = requests.post(
    "https://api.anthropic.com/v1/messages",
    headers={
        "Content-Type":    "application/json",
        "x-api-key":       api_key,
        "anthropic-version": "2023-06-01",
    },
    json={
        "model":      "claude-sonnet-4-6",
        "max_tokens": 1000,
        "messages":   [{"role": "user", "content": PROMPT}],
    },
    timeout=30,
)
response.raise_for_status()
data    = response.json()
bulletin = data["content"][0]["text"]

# ── 5. Output ─────────────────────────────────────────────────────────────────
print("\n[4/4] Bulletin generated:\n")
print("─" * 60)
print(bulletin)
print("─" * 60)

# Save
full_output = f"""WWI DAILY WATER BULLETIN
{TODAY}
Station: SAUHEID — Ourthe inférieure
{'='*60}

{bulletin}

{'='*60}
Generated by Wallonia Water Intelligence Platform
Model: RF-deltaH · NSE=0.953 (24h) · Explained by SHAP
Data: SPW Hydrométrie · Open-Meteo · ERA5 (Copernicus)
"""

# Always-current file
with open(OUT_BULLETIN, "w") as f:
    f.write(full_output)

# Timestamped archive
from datetime import date as _date



archive_dir = ROOT / "export" / "csvs" / "archive"
archive_dir.mkdir(parents=True, exist_ok=True)
ts_str = datetime.now().strftime("%Y%m%d")
archive_path = str(archive_dir / f"bulletin_{ts_str}.txt")
with open(archive_path, "w") as f:
    f.write(full_output)

print(f"\n✓ Bulletin saved → {OUT_BULLETIN}")
print(f"✓ Archived      → {archive_path}")
