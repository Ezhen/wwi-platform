"""
WWI Sensor Reliability Index
Scores each station 0-100 based on:
  - Data freshness (is the latest reading recent?)
  - Completeness (% non-null in last 7 days)
  - Flatline detection (sensor stuck at same value)
  - Spike detection (unrealistic jumps)
  - Physical consistency (agrees with upstream/downstream logic)
  - Quality code flags (SPW codes 200/40/255)

Output:
  - export/csvs/sensor_reliability.csv  (machine-readable)
  - export/csvs/archive/reliability_YYYYMMDD.csv
  - Printed report
"""

import sqlite3
import pandas as pd
import numpy as np
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT     = Path(__file__).resolve().parent
DB_SPW   = str(ROOT / "export/databases/spw_liege.db")
OUT_CSV  = str(ROOT / "export/csvs/sensor_reliability.csv")
ARCH_DIR = ROOT / "export/csvs/archive"
ARCH_DIR.mkdir(parents=True, exist_ok=True)

NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

print("=" * 65)
print(f"WWI Sensor Reliability Index — {TODAY}")
print("=" * 65)

con = sqlite3.connect(DB_SPW)

# ── Load all stations ─────────────────────────────────────────────
stations = con.execute("""
    SELECT station_no, station_name, river_name, lat, lon
    FROM stations
    WHERE station_no IS NOT NULL
    ORDER BY river_name, station_no
""").fetchall()

log.info(f"Evaluating {len(stations)} stations...")

# ── Upstream/downstream pairs for consistency check ──────────────
# (upstream_sno, downstream_sno, max_expected_dH_diff)
CONSISTENCY_PAIRS = [
    ("6732", "5904",  0.8),   # Stavelot → Comblain (Amblève→Ourthe)
    ("5904", "5826",  0.5),   # Comblain → Sauheid
    ("5826", "5806",  0.3),   # Sauheid  → Angleur
    ("6387", "6228",  0.8),   # Eupen    → Chaudfontaine (Vesdre)
    ("6832", "6732",  0.5),   # Trois-Ponts → Stavelot
]

results = []

for sno, name, river, lat, lon in stations:

    score   = 100.0
    issues  = []
    flags   = []

    # ── 1. Data freshness ─────────────────────────────────────────
    row = con.execute("""
        SELECT MAX(timestamp), value
        FROM observations
        WHERE station_no=? AND parameter='H'
          AND value IS NOT NULL AND value < 10
    """, (sno,)).fetchone()

    if not row or not row[0]:
        # Check if station has other parameters (precip-only stations)
        other = con.execute("""
            SELECT DISTINCT parameter FROM observations
            WHERE station_no=? AND timestamp >= datetime('now','-7 days')
        """, (sno,)).fetchall()
        other_params = [r[0] for r in other]
        if other_params:
            # Station active but no H — precip or Q only
            issue_str = f"NO_H_DATA_has_{'+'.join(other_params)}"
            flag_str  = "NO_H_PARAMETER"
            score_val = 50  # degraded but not failed
            grade_val = "C"
        else:
            issue_str = "NO_DATA"
            flag_str  = "SENSOR_OFFLINE"
            score_val = 0
            grade_val = "F"
        results.append({
            "station_no": sno, "name": name, "river": river,
            "score": score_val, "grade": grade_val,
            "issues": issue_str, "flags": flag_str,
            "H_latest": None, "last_update": None,
            "completeness_7d": 0, "flatline_hrs": 0,
            "spike_count": 0, "age_hours": 999,
        })
        continue

    last_ts_str = row[0]
    H_latest    = row[1]

    try:
        last_ts = pd.to_datetime(last_ts_str, format="mixed", utc=True)
        age_hours = (NOW - last_ts).total_seconds() / 3600
    except:
        age_hours = 999

    if age_hours > 48:
        score  -= 40
        issues.append(f"STALE_{age_hours:.0f}h")
        flags.append("SENSOR_STALE")
    elif age_hours > 24:
        penalty = min(25, age_hours * 0.8)
        score  -= penalty
        issues.append(f"STALE_{age_hours:.0f}h")
        flags.append("SENSOR_STALE")
    elif age_hours > 12:
        score -= 10
        issues.append(f"DELAYED_{age_hours:.0f}h")
    elif age_hours > 6:
        # Normal SPW 6h reporting cycle — no penalty, just note
        issues.append(f"DELAYED_{age_hours:.0f}h_normal")

    # ── 2. Completeness last 7 days ───────────────────────────────
    obs_7d = con.execute("""
        SELECT timestamp, value, quality_code
        FROM observations
        WHERE station_no=? AND parameter='H'
          AND timestamp >= datetime('now','-7 days')
        ORDER BY timestamp
    """, (sno,)).fetchall()

    if obs_7d:
        s = pd.Series(
            [r[1] for r in obs_7d],
            index=pd.to_datetime(
                [r[0] for r in obs_7d], format="mixed", utc=True)
        )
        s.index = s.index.tz_localize(None)
        s = s[~s.index.duplicated()].resample("1h").mean()

        completeness = s.notna().mean()
        expected_hrs = 7 * 24
        n_valid = s.notna().sum()

        if completeness < 0.5:
            score -= 25
            issues.append(f"INCOMPLETE_{completeness*100:.0f}pct")
            flags.append("DATA_GAPS")
        elif completeness < 0.8:
            score -= 10
            issues.append(f"PARTIAL_{completeness*100:.0f}pct")

        # ── 3. Flatline detection ─────────────────────────────────
        s_valid = s.dropna()
        flatline_hrs = 0
        if len(s_valid) > 3:
            diffs = s_valid.diff().abs()
            # Consecutive hours with zero change
            flatline = (diffs < 0.001).astype(int)
            runs = flatline * (flatline.groupby(
                (flatline != flatline.shift()).cumsum()).cumcount() + 1)
            max_flat = runs.max()
            flatline_hrs = int(max_flat) if pd.notna(max_flat) else 0

            if flatline_hrs > 48:
                score -= 30
                issues.append(f"FLATLINE_{flatline_hrs}h")
                flags.append("SENSOR_FLATLINE")
            elif flatline_hrs > 12:
                score -= 10
                issues.append(f"FLAT_{flatline_hrs}h")

        # ── 4. Spike detection ────────────────────────────────────
        spike_count = 0
        if len(s_valid) > 6:
            rolling_med = s_valid.rolling(6, center=True).median()
            deviations  = (s_valid - rolling_med).abs()
            mad         = deviations.median()
            threshold   = max(0.30, mad * 10)  # 30cm min threshold
            spikes      = deviations > threshold
            spike_count = int(spikes.sum())

            if spike_count > 5:
                score -= 20
                issues.append(f"SPIKES_{spike_count}")
                flags.append("SENSOR_SPIKES")
            elif spike_count > 1:
                score -= 8
                issues.append(f"SPIKE_{spike_count}")

        # ── 5. Quality code check ─────────────────────────────────
        bad_codes = [r[2] for r in obs_7d
                     if r[2] and r[2] not in (200, 40)]
        if len(bad_codes) > 10:
            score -= 10
            issues.append(f"QC_FLAGS_{len(bad_codes)}")
            flags.append("QUALITY_FLAGS")

        # ── 6. Physical consistency ───────────────────────────────
        for up_sno, dn_sno, max_diff in CONSISTENCY_PAIRS:
            if sno == dn_sno:
                H_up = con.execute("""
                    SELECT value FROM observations
                    WHERE station_no=? AND parameter='H'
                      AND value IS NOT NULL AND value < 10
                    ORDER BY timestamp DESC LIMIT 1
                """, (up_sno,)).fetchone()
                if H_up and H_latest:
                    abs_diff = abs(H_latest - H_up[0])
                    # Flag only if difference is physically implausible
                    # (>3m difference between adjacent stations)
                    if abs_diff > max_diff * 4:
                        score -= 15
                        issues.append(
                            f"INCONSISTENT_vs_{up_sno}"
                            f"_H={H_latest:.2f}_up={H_up[0]:.2f}")
                        flags.append("PHYSICS_INCONSISTENT")
    else:
        completeness = 0
        flatline_hrs = 0
        spike_count  = 0
        score -= 30
        issues.append("NO_RECENT_DATA")
        flags.append("SENSOR_STALE")

    # ── Grade ─────────────────────────────────────────────────────
    score = max(0, min(100, score))
    if score >= 90:   grade = "A"
    elif score >= 75: grade = "B"
    elif score >= 60: grade = "C"
    elif score >= 40: grade = "D"
    else:             grade = "F"

    results.append({
        "station_no":      sno,
        "name":            (name or "")[:25],
        "river":           river or "",
        "score":           round(score, 1),
        "grade":           grade,
        "H_latest":        round(H_latest, 3) if H_latest else None,
        "last_update":     last_ts_str[:16] if row[0] else None,
        "age_hours":       round(age_hours, 1),
        "completeness_7d": round(completeness * 100, 1) if obs_7d else 0,
        "flatline_hrs":    flatline_hrs,
        "spike_count":     spike_count,
        "issues":          "|".join(issues) if issues else "OK",
        "flags":           "|".join(flags)  if flags  else "",
    })

con.close()

# ── Save ──────────────────────────────────────────────────────────
df = pd.DataFrame(results).sort_values("score")
df.to_csv(OUT_CSV, index=False)

ts_str = datetime.now().strftime("%Y%m%d")
df.to_csv(str(ARCH_DIR / f"reliability_{ts_str}.csv"), index=False)

# Save summary JSON for alert engine and bulletin
summary = {
    "generated": TODAY,
    "n_stations": len(df),
    "grade_A": int((df["grade"]=="A").sum()),
    "grade_B": int((df["grade"]=="B").sum()),
    "grade_C": int((df["grade"]=="C").sum()),
    "grade_D": int((df["grade"]=="D").sum()),
    "grade_F": int((df["grade"]=="F").sum()),
    "offline":  df[df["issues"].str.contains("NO_DATA",na=False)]["station_no"].tolist(),
    "flatline": df[df["flags"].str.contains("FLATLINE",na=False)]["station_no"].tolist(),
    "stale":    df[df["flags"].str.contains("STALE",na=False)]["station_no"].tolist(),
    "mean_score": round(float(df["score"].mean()), 1),
    "key_stations": {
        r["station_no"]: {
            "score": r["score"],
            "grade": r["grade"],
            "issues": r["issues"],
        }
        for _, r in df[df["station_no"].isin(
            ["5826","6732","5904","6387","6228","7133","7141"]
        )].iterrows()
    }
}
json_path = str(ROOT / "export/csvs/sensor_reliability.json")
import json
with open(json_path, "w") as f:
    json.dump(summary, f, indent=2)
log.info(f"Summary JSON → {json_path}")

# ── Print report ──────────────────────────────────────────────────
print(f"\n{'─'*65}")
print(f"  {'Sno':<8} {'Station':<25} {'River':<18} "
      f"{'Score':>6} {'Grade':>5}  Issues")
print(f"{'─'*65}")

grade_counts = {"A":0,"B":0,"C":0,"D":0,"F":0}
for _, r in df.iterrows():
    grade = r["grade"]
    grade_counts[grade] += 1
    icon  = {"A":"🟢","B":"🟡","C":"🟠","D":"🔴","F":"⛔"}.get(grade,"?")
    print(f"  {r['station_no']:<8} {r['name']:<25} {r['river']:<18} "
          f"{r['score']:>6.1f} {icon+grade:>5}  {r['issues'][:40]}")

print(f"\n{'─'*65}")
print(f"  Grade summary: "
      f"A={grade_counts['A']} "
      f"B={grade_counts['B']} "
      f"C={grade_counts['C']} "
      f"D={grade_counts['D']} "
      f"F={grade_counts['F']}")

flagged = df[df["flags"] != ""]
if len(flagged):
    print(f"\n  ⚠ Stations with active reliability flags:")
    for _, r in flagged.iterrows():
        print(f"    {r['station_no']} {r['name']:<25} → {r['flags']}")

print(f"\n✓ Saved → {OUT_CSV}")
print(f"✓ Archived → reliability_{ts_str}.csv")
