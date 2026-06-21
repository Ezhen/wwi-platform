"""
WWI — LLM Bulletin Generator
Reads live state from current_alerts.json and forecast_log.csv,
calls Claude API to generate a natural language daily briefing.
"""

import json
import sqlite3
import pandas as pd
import anthropic
from pathlib import Path
from datetime import date, datetime

ROOT         = Path(__file__).resolve().parent
SHAP_CSV     = str(ROOT / "export/csvs/shap_current.csv")
DB_FORECAST  = str(ROOT / "export/databases/forecast_liege.db")
ALERTS_JSON  = str(ROOT / "export/csvs/current_alerts.json")
FORECAST_LOG = str(ROOT / "export/csvs/forecast_log.csv")
OUT_BULLETIN = str(ROOT / "export/csvs/daily_bulletin.txt")
ARCH_DIR     = ROOT / "export/csvs/archive"
ARCH_DIR.mkdir(parents=True, exist_ok=True)

TODAY = date.today().isoformat()

print("=" * 60)
print(f"WWI LLM Bulletin Generator — {TODAY}")
print("=" * 60)

# ── 1. Load SHAP explanation ──────────────────────────────────────────────────
print("\n[1/4] Loading SHAP values...")
shap_df      = pd.read_csv(SHAP_CSV)
top_raising  = shap_df[shap_df["shap_value"] > 0].nlargest(5, "shap_value")
top_lowering = shap_df[shap_df["shap_value"] < 0].nsmallest(5, "shap_value")

FEATURE_LABELS = {
    "P_fagnes":        "Hautes Fagnes precipitation (today)",
    "P_fagnes_lag0":   "Hautes Fagnes precipitation (today)",
    "P_fagnes_lag1":   "Hautes Fagnes precipitation (yesterday)",
    "P_vesdre":        "Vesdre catchment precipitation (today)",
    "P_vesdre_lag0":   "Vesdre catchment precipitation (today)",
    "P_vesdre_lag1":   "Vesdre catchment precipitation (yesterday)",
    "P_ourthe":        "Ourthe headwater precipitation (today)",
    "P_ourthe_lag0":   "Ourthe headwater precipitation (today)",
    "H_stavelot":      "Water level at Stavelot (Amblève)",
    "H_comblain":      "Water level at Comblain (Ourthe)",
    "H_eupen":         "Water level at Eupen (Vesdre headwater)",
    "H_chaudf":        "Water level at Chaudfontaine (Vesdre)",
    "H_chaudf_lag1":   "Water level at Chaudfontaine yesterday",
    "H":               "Current water level at Sauheid",
    "Q":               "Current river discharge at Sauheid",
    "Q_lag1":          "River discharge yesterday",
    "H_delta1d":       "Change in level over past 24 hours",
    "H_delta3d":       "Change in level over past 3 days",
    "P_fagnes_7d":     "7-day Hautes Fagnes rainfall total",
    "P_ourthe_lag2":   "Ourthe rainfall 2 days ago",
    "swvl1_sauheid":   "Soil moisture in Ourthe catchment",
}

def label(feat):
    return FEATURE_LABELS.get(feat, feat.replace("_", " "))

raising_text  = "\n".join([
    f"  - {label(r['feature'])}: {r['shap_value']:+.4f}"
    for _, r in top_raising.iterrows()
])
lowering_text = "\n".join([
    f"  - {label(r['feature'])}: {r['shap_value']:+.4f}"
    for _, r in top_lowering.iterrows()
])

# ── 2. Load live state ────────────────────────────────────────────────────────
print("\n[2/4] Loading forecast data...")

# Defaults
H_current  = 0.0
H_t24      = 0.0
H_t48      = 0.0
H_t72      = 0.0
risk_level = "NORMAL"
P_7d       = 0.0
P_3d       = 0.0
H_stavelot = 0.0
H_comblain = 0.0
sta_tend   = "STABLE"
com_tend   = "STABLE"

# Load from current_alerts.json
if Path(ALERTS_JSON).exists():
    try:
        ad = json.loads(Path(ALERTS_JSON).read_text())
        st = ad.get("state", {})
        H_current  = float(st.get("H") or 0.0)
        P_7d       = float(st.get("basin_rain_7d") or
                           st.get("P_7d_mean") or 0.0)
        P_3d       = float(st.get("P_3d_mean") or 0.0)
        upstream   = st.get("upstream", {})
        _st6       = upstream.get("6732") or {}
        _st5       = upstream.get("5904") or {}
        H_stavelot = float(_st6.get("H") or 0.0)
        H_comblain = float(_st5.get("H") or 0.0)
        sta_tend   = _st6.get("tendency", "STABLE")
        com_tend   = _st5.get("tendency", "STABLE")
        alerts     = ad.get("alerts", [])
        risk_level = alerts[0].get("code", "NORMAL") if alerts else "NORMAL"
    except Exception as e:
        print(f"  Warning: could not load alerts — {e}")

# Always read upstream H directly from t_flood_context (most reliable)
DB_SPW = str(ROOT / "export/databases/spw_liege.db")
if Path(DB_SPW).exists():
    try:
        con_spw = sqlite3.connect(DB_SPW)
        upstream_rows = con_spw.execute("""
            SELECT station_no, level_m, tendency
            FROM t_flood_context
            WHERE station_no IN ('6732','5904','6387','6228')
              AND level_m IS NOT NULL AND level_m < 10
        """).fetchall()
        con_spw.close()
        for sno, H_val, tend in upstream_rows:
            if sno == "6732":
                H_stavelot = round(float(H_val), 3)
                sta_tend   = tend or "STABLE"
            elif sno == "5904":
                H_comblain = round(float(H_val), 3)
                com_tend   = tend or "STABLE"
        # Also update H_current from live DB if alerts gave 0
        if H_current == 0.0:
            row = con_spw.execute("""
                SELECT level_m FROM t_latest_H
                WHERE station_no='5826' AND level_m IS NOT NULL AND level_m < 10
            """).fetchone()
            if row:
                H_current = round(float(row[0]), 3)
    except Exception as e:
        print(f"  Warning: could not load upstream from DB — {e}")

# Load forecasts from log
if Path(FORECAST_LOG).exists():
    try:
        fc_df = pd.read_csv(FORECAST_LOG)
        if len(fc_df):
            last  = fc_df.iloc[-1]
            H_t24 = float(last.get("H_pred_t1") or H_current)
            H_t48 = float(last.get("H_pred_t2") or H_current)
            H_t72 = float(last.get("H_pred_t3") or H_current)
    except Exception as e:
        print(f"  Warning: could not load forecast log — {e}")

delta_24 = H_t24 - H_current
tend_str = ("rising" if delta_24 > 0.02
            else "falling" if delta_24 < -0.02
            else "stable")

print(f"  H current:  {H_current:.3f}m")
print(f"  H +24h:     {H_t24:.3f}m  ({delta_24:+.3f}m, {tend_str})")
print(f"  Risk:       {risk_level}")
print(f"  Stavelot:   {H_stavelot:.3f}m  {sta_tend}")
print(f"  Comblain:   {H_comblain:.3f}m  {com_tend}")

# Load 7-day forecast
forecast_summary = ""
try:
    con = sqlite3.connect(DB_FORECAST)
    fc_rows = con.execute("""
        SELECT description, precip_24h_mm, precip_72h_mm, alert_24h, alert_72h
        FROM v_forecast_alert
        ORDER BY precip_24h_mm DESC
    """).fetchall()
    con.close()
    forecast_summary = "\n".join([
        f"  {r[0]}: 24h={r[1]:.1f}mm 72h={r[2]:.1f}mm alert={r[3]}/{r[4]}"
        for r in fc_rows
    ])
except Exception as e:
    forecast_summary = f"  (forecast unavailable: {e})"

# ── 3. Build prompt and call Claude API ──────────────────────────────────────
print("\n[3/4] Calling Claude API...")

prompt = f"""You are a professional hydrologist writing a daily operational water bulletin for river managers and water utility operators in Wallonia, Belgium.

The bulletin should be written in clear English, suitable for a non-technical manager who needs to act on it.
It should explain the current situation, why the river is doing what it is doing, and what to expect.

CURRENT STATE — {TODAY}:
- Station: SAUHEID on the Ourthe river (Liège province, Belgium)
- Current water level: {H_current:.3f} m
- 24h forecast: {H_t24:.3f} m ({tend_str}, {delta_24:+.3f}m)
- 48h forecast: {H_t48:.3f} m
- 72h forecast: {H_t72:.3f} m
- Risk level: {risk_level}
- 7-day basin rainfall: {P_7d:.1f} mm
- 3-day Ourthe rainfall: {P_3d:.1f} mm

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
- The July 2021 flood peaked at 4.05m (6.65m hourly at Chaudfontaine).
- Upstream stations: Stavelot (Amblève) {H_stavelot:.3f}m {sta_tend},
  Comblain (Ourthe) {H_comblain:.3f}m {com_tend}.

Write a bulletin of 3-4 short paragraphs covering:
1. Current situation summary
2. What is driving the current level (plain language SHAP explanation)
3. 72-hour outlook and any precautions
4. One sentence on data quality / confidence

Do not use technical jargon like SHAP, Random Forest, or NSE.
Write as if briefing a regional water manager on Monday morning."""

client = anthropic.Anthropic()
message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1000,
    messages=[{"role": "user", "content": prompt}]
)
bulletin_text = message.content[0].text

# ── 4. Save ───────────────────────────────────────────────────────────────────
print("\n[4/4] Bulletin generated:\n")
divider = "─" * 60
print(divider)
print(bulletin_text)
print(divider)

ts_str = datetime.now().strftime("%Y%m%d")
full_output = f"# Daily Water Bulletin — {TODAY}\n\n{bulletin_text}\n"

with open(OUT_BULLETIN, "w") as f:
    f.write(full_output)
arch_path = str(ARCH_DIR / f"bulletin_{ts_str}.txt")
with open(arch_path, "w") as f:
    f.write(full_output)

print(f"\n✓ Bulletin saved → {OUT_BULLETIN}")
print(f"✓ Archived      → {arch_path}")
