"""
WWI Wave Propagation Check
Compares actual H evolution across Ourthe network stations
over the last 48h to validate the upstream alert.
"""
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

ROOT   = Path(__file__).resolve().parent
DB_SPW = str(ROOT / "export/databases/spw_liege.db")

# Ourthe network — upstream to downstream
OURTHE_NETWORK = [
    ("6832", "TROIS-PONTS",  "Salm/Ourthe",   0),
    ("6732", "STAVELOT",     "Amblève",       10),
    ("5904", "COMBLAIN",     "Ourthe",        25),
    ("6657", "LOUVEIGNE",    "Ourthe",        38),
    ("5826", "SAUHEID",      "Ourthe",        48),
    ("5806", "ANGLEUR",      "Ourthe",        55),
]

con = sqlite3.connect(DB_SPW)

print("=" * 70)
print("Ourthe network — H evolution last 48h (wave propagation check)")
print("=" * 70)

dfs = {}
for sno, label, river, km in OURTHE_NETWORK:
    rows = con.execute("""
        SELECT timestamp, value
        FROM observations
        WHERE station_no = ?
          AND parameter  = 'H'
          AND value IS NOT NULL
          AND value < 10
          AND timestamp >= datetime('now', '-48 hours')
        ORDER BY timestamp
    """, (sno,)).fetchall()

    if rows:
        s = pd.Series(
            [r[1] for r in rows],
            index=pd.to_datetime([r[0] for r in rows], utc=True, format="mixed")
        )
        s.index = s.index.tz_localize(None)
        s = s[~s.index.duplicated()].resample("1h").mean()
        dfs[sno] = s

        H_min  = s.min()
        H_max  = s.max()
        H_now  = s.iloc[-1]
        H_peak_t = s.idxmax().strftime("%d %b %H:%M")
        dH     = s.iloc[-1] - s.iloc[0]

        print(f"\n  {label:<20} ({river}, km {km})")
        print(f"    H now:  {H_now:.3f}m  |  "
              f"H min: {H_min:.3f}m  |  "
              f"H max: {H_max:.3f}m (peak {H_peak_t})")
        print(f"    ΔH over 48h: {dH:+.3f}m")

        # Hourly trend
        if len(s) >= 6:
            trend = "  ".join([
                f"{s.index[i].strftime('%H:%M')}={s.iloc[i]:.2f}"
                for i in range(0, len(s), max(1, len(s)//8))
            ])
            print(f"    Trend: {trend}")
    else:
        print(f"\n  {label:<20} — no data in last 48h")

con.close()

# Wave timing analysis
print("\n" + "=" * 70)
print("Wave propagation timing (peak H times)")
print("=" * 70)
peak_times = {}
for sno, label, river, km in OURTHE_NETWORK:
    if sno in dfs and dfs[sno].notna().any():
        peak_t = dfs[sno].idxmax()
        peak_times[label] = (peak_t, km, dfs[sno].max())

if len(peak_times) > 1:
    sorted_peaks = sorted(peak_times.items(), key=lambda x: x[1][0])
    print(f"\n  {'Station':<20} {'km':>4}  {'Peak time':<18}  "
          f"{'Peak H':>7}  {'Lag from STAVELOT'}")
    print(f"  {'─'*70}")
    ref_time = peak_times.get("STAVELOT", (None,))[0]
    for label, (pt, km, H_peak) in sorted_peaks:
        lag = ""
        if ref_time and pt:
            hours = (pt - ref_time).total_seconds() / 3600
            lag = f"+{hours:.0f}h" if hours >= 0 else f"{hours:.0f}h"
        print(f"  {label:<20} {km:>4}  "
              f"{pt.strftime('%d %b %H:%M') if pt else '?':<18}  "
              f"{H_peak:>7.3f}m  {lag}")
