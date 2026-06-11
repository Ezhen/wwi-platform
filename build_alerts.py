"""
WWI Operational Alert Engine
Evaluates multi-signal physical conditions and produces
composite alert states with reasoning.

Adds t_operational_alerts to spw_liege.db.
Designed to impress operationally — not just threshold flags
but physically coherent state diagnosis with duration tracking.
"""

import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime, timezone




ROOT = Path(__file__).resolve().parent
DB_SPW = str(ROOT / "export/databases/spw_liege.db")
DB_FC  = str(ROOT / "export/databases/forecast_liege.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Physical thresholds — Ourthe at SAUHEID ───────────────────────────────────
# Based on SPW reference values + hydrological literature
THRESHOLDS = {
    "H_drought":      0.25,   # m — critically low
    "H_low":          0.45,   # m — below normal summer
    "H_normal_max":   0.80,   # m — normal upper bound
    "H_watch":        1.50,   # m — elevated, SPW vigilance
    "H_elevated":     2.50,   # m — high risk
    "H_flood":        3.50,   # m — emergency (July 2021 peak was 4.05m)
    "dH_rising_fast": 0.05,   # m/h — rapid rise threshold
    "dH_falling_fast":-0.05,  # m/h — rapid fall
    "Q_drought":      5.0,    # m³/s — critically low
    "Q_low":          15.0,   # m³/s — below normal
    "Q_flood":        200.0,  # m³/s — high
    "P_dry_7d":       10.0,   # mm — dry antecedent week
    "P_wet_7d":       40.0,   # mm — wet antecedent week
    "P_very_wet_7d":  80.0,   # mm — very wet (July 2021: ~150mm)
}


def load_current_state(con_spw, con_fc):
    """Load all current indicators from materialized tables."""
    state = {}

    # River level and rise rate at SAUHEID
    row = con_spw.execute("""
        SELECT f.level_m, f.delta_1h_m, f.delta_3h_m, f.delta_6h_m,
               f.tendency, f.basin_rain_7d_mm, f.discharge_m3s,
               f.timestamp
        FROM t_flood_context f
        WHERE f.station_no = '5826'
          AND (f.level_m IS NULL OR f.level_m < 10)
    """).fetchone()
    # Fallback: get latest valid H directly
    if not row or row[0] is None:
        row2 = con_spw.execute("""
            SELECT value FROM observations
            WHERE station_no='5826' AND parameter='H'
              AND value IS NOT NULL AND value < 10
            ORDER BY timestamp DESC LIMIT 1
        """).fetchone()
        if row2:
            state["H"] = row2[0]

    if row:
        state["H"]              = row[0]
        state["dH_1h"]          = row[1]
        state["dH_3h"]          = row[2]
        state["dH_6h"]          = row[3]
        state["tendency"]       = row[4]
        state["basin_rain_7d"]  = row[5]
        state["Q"]              = row[6]
        state["timestamp"]      = row[7]

    # Antecedent rainfall from multiple stations
    rain_rows = con_spw.execute("""
        SELECT station_no, rain_3d_mm, rain_7d_mm, rain_14d_mm
        FROM t_antecedent_rain
        WHERE station_no IN ('6657','6958','6529')
    """).fetchall()
    if rain_rows:
        state["P_3d_mean"]  = sum(r[1] or 0 for r in rain_rows) / len(rain_rows)
        state["P_7d_mean"]  = sum(r[2] or 0 for r in rain_rows) / len(rain_rows)
        state["P_14d_mean"] = sum(r[3] or 0 for r in rain_rows) / len(rain_rows)

    # Upstream state
    upstream = con_spw.execute("""
        SELECT station_no, level_m, tendency
        FROM t_flood_context
        WHERE station_no IN ('6387','6228','6732','6832','5904')
        ORDER BY station_no
    """).fetchall()
    state["upstream"] = {r[0]: {"H": r[1], "tendency": r[2]}
                         for r in upstream}

    # Forecast
    if con_fc:
        fc = con_fc.execute("""
            SELECT precip_24h_mm, precip_72h_mm, precip_7d_mm,
                   alert_24h, alert_72h
            FROM v_forecast_alert
            WHERE point_id = 'ourthe_sauheid'
        """).fetchone()
        if fc:
            state["fc_precip_24h"] = fc[0]
            state["fc_precip_72h"] = fc[1]
            state["fc_precip_7d"]  = fc[2]
            state["fc_alert_24h"]  = fc[3]
            state["fc_alert_72h"]  = fc[4]

    return state


def evaluate_alerts(state):
    """
    Evaluate multi-signal composite alert conditions.
    Returns list of active alerts with physical reasoning.
    """
    alerts = []
    T = THRESHOLDS
    H   = state.get("H") or 0
    Q   = state.get("Q") or 0
    dH1 = state.get("dH_1h") or 0
    P7  = state.get("P_7d_mean") or state.get("basin_rain_7d") or 0
    P3  = state.get("P_3d_mean") or 0
    fc24 = state.get("fc_precip_24h") or 0
    fc72 = state.get("fc_precip_72h") or 0
    tend  = state.get("tendency") or "STABLE"
    upstream = state.get("upstream") or {}

    # ── DROUGHT / LOW FLOW ────────────────────────────────────────────────────
    if H < T["H_drought"] and Q < T["Q_drought"]:
        alerts.append({
            "code":     "DROUGHT_CRITICAL",
            "severity": "HIGH",
            "signals":  [
                f"H={H:.3f}m < drought threshold {T['H_drought']}m",
                f"Q={Q:.2f} m³/s < critical minimum {T['Q_drought']} m³/s",
                f"7-day rainfall={P7:.1f}mm (dry)",
            ],
            "interpretation": (
                "Critically low flow. Baseflow nearly exhausted. "
                "Aquifer recharge deficit likely."
            ),
            "operational": (
                "HALT sensitive low-flow operations. "
                "Review abstraction permits immediately. "
                "Flag to INASEP/SWDE emergency desk."
            ),
        })

    elif H < T["H_low"] and Q < T["Q_low"] and P7 < T["P_dry_7d"]:
        sustained_days = _estimate_sustained_days(H, dH1, T["H_low"])
        alerts.append({
            "code":     "LOW_FLOW_SUSTAINED",
            "severity": "MODERATE",
            "signals":  [
                f"H={H:.3f}m below normal threshold {T['H_low']}m",
                f"Q={Q:.2f} m³/s below normal {T['Q_low']} m³/s",
                f"7-day rainfall={P7:.1f}mm < {T['P_dry_7d']}mm (dry week)",
                f"Forecast 72h: {fc72:.1f}mm (no relief)",
            ],
            "interpretation": (
                f"Sustained low-flow condition. "
                f"Baseflow drainage without rainfall replenishment. "
                f"Estimated {sustained_days} more days below threshold "
                f"if no significant rainfall."
            ),
            "operational": (
                "Review minimum flow abstraction permits. "
                "Flag to water utility planning for summer outlook. "
                "No operational restrictions yet but monitor daily."
            ),
        })

    # ── RAPID RISE ALERT ──────────────────────────────────────────────────────
    if dH1 and dH1 > T["dH_rising_fast"] and P7 > T["P_wet_7d"]:
        upstream_rising = sum(
            1 for v in upstream.values()
            if v.get("tendency") in ("RISING", "RISING_FAST")
        )
        alerts.append({
            "code":     "RAPID_RISE_WET_ANTECEDENT",
            "severity": "HIGH",
            "signals":  [
                f"ΔH/h = +{dH1:.4f}m/h > rapid rise threshold",
                f"7-day basin rainfall = {P7:.1f}mm (saturated catchment)",
                f"{upstream_rising}/{len(upstream)} upstream stations rising",
                f"Forecast 24h: {fc24:.1f}mm additional",
            ],
            "interpretation": (
                "Rapid rise on saturated catchment. "
                "High flood risk — soil moisture at capacity, "
                "additional rainfall converts directly to runoff. "
                "July 2021 type precursor pattern if forecast rainfall verifies."
            ),
            "operational": (
                "ELEVATED flood risk. Activate monitoring protocol. "
                "Review flood contingency plans. "
                "Alert downstream operators (HUY, LIÈGE)."
            ),
        })

    elif dH1 and dH1 > T["dH_rising_fast"]:
        alerts.append({
            "code":     "RAPID_RISE",
            "severity": "WATCH",
            "signals":  [
                f"ΔH/h = +{dH1:.4f}m/h > rapid rise threshold",
                f"7-day basin rainfall = {P7:.1f}mm",
                f"Tendency: {tend}",
            ],
            "interpretation": (
                "River rising rapidly. Catchment responding to rainfall. "
                "Flood risk elevated but antecedent conditions not critical."
            ),
            "operational": "Monitor hourly. Prepare contingency review.",
        })

    # ── FLOOD THRESHOLDS ──────────────────────────────────────────────────────
    if H > T["H_flood"]:
        alerts.append({
            "code":     "FLOOD_EMERGENCY",
            "severity": "CRITICAL",
            "signals":  [
                f"H={H:.3f}m > flood emergency threshold {T['H_flood']}m",
                f"Q={Q:.2f} m³/s",
                f"Tendency: {tend}",
            ],
            "interpretation": (
                "Major flood event in progress. "
                f"Level approaching July 2021 catastrophe threshold (4.05m peak). "
                "All low-lying areas at risk."
            ),
            "operational": (
                "EMERGENCY PROTOCOL. Halt all operations in flood plain. "
                "Alert civil protection. Contact SPW crisis desk."
            ),
        })

    elif H > T["H_elevated"]:
        alerts.append({
            "code":     "FLOOD_ELEVATED",
            "severity": "HIGH",
            "signals":  [
                f"H={H:.3f}m > elevated threshold {T['H_elevated']}m",
                f"Tendency: {tend}",
            ],
            "interpretation": "High water level. Flood risk elevated.",
            "operational": (
                "Restrict sensitive operations in riparian zones. "
                "Monitor every 30 minutes."
            ),
        })

    elif H > T["H_watch"]:
        alerts.append({
            "code":     "FLOOD_WATCH",
            "severity": "WATCH",
            "signals":  [
                f"H={H:.3f}m > watch threshold {T['H_watch']}m",
            ],
            "interpretation": "Elevated water level. SPW vigilance threshold exceeded.",
            "operational": "Review operations in riparian zones.",
        })

    # ── PHYSICS CONSISTENCY CHECK ─────────────────────────────────────────────
    # Flag physically implausible states
    if P7 > T["P_very_wet_7d"] and H < T["H_normal_max"]:
        alerts.append({
            "code":     "ANOMALY_RAIN_NO_RESPONSE",
            "severity": "INFO",
            "signals":  [
                f"7-day rainfall={P7:.1f}mm (very wet) but H={H:.3f}m (low)",
                "Expected H > 0.8m given antecedent rainfall",
            ],
            "interpretation": (
                "Physical inconsistency: high rainfall but low river level. "
                "Possible causes: gauge malfunction, data lag, "
                "or exceptional infiltration into dry soil."
            ),
            "operational": (
                "Check SPW gauge status. "
                "Verify data transmission. Do not trust forecast model."
            ),
        })

    # ── NORMAL STATE ──────────────────────────────────────────────────────────
    if not alerts:
        alerts.append({
            "code":     "NORMAL",
            "severity": "INFO",
            "signals":  [
                f"H={H:.3f}m within normal range",
                f"Q={Q:.2f} m³/s",
                f"7-day rainfall={P7:.1f}mm",
                f"Tendency: {tend}",
            ],
            "interpretation": (
                "All indicators within normal operating range. "
                "No physical anomalies detected."
            ),
            "operational": "No operational restrictions. Routine monitoring.",
        })

    return alerts


def _estimate_sustained_days(H, dH1, threshold):
    """Estimate days until H recovers to threshold at current rate."""
    if not dH1 or dH1 >= 0:
        return "unknown"
    days_to_threshold = abs((threshold - H) / (dH1 * 24))
    return f"~{days_to_threshold:.0f}"


def save_alerts(con_spw, alerts, state):
    """Save alert state to t_operational_alerts table."""
    con_spw.execute("""
        CREATE TABLE IF NOT EXISTS t_operational_alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT NOT NULL,
            station_no   TEXT NOT NULL,
            alert_code   TEXT NOT NULL,
            severity     TEXT NOT NULL,
            signals_json TEXT,
            interpretation TEXT,
            operational  TEXT,
            state_json   TEXT
        )
    """)

    # Keep only last 48 rows (2 days of history)
    con_spw.execute("""
        DELETE FROM t_operational_alerts
        WHERE id NOT IN (
            SELECT id FROM t_operational_alerts
            ORDER BY id DESC LIMIT 48
        )
    """)

    ts = datetime.now(timezone.utc).isoformat()
    for alert in alerts:
        con_spw.execute("""
            INSERT INTO t_operational_alerts
                (timestamp, station_no, alert_code, severity,
                 signals_json, interpretation, operational, state_json)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            ts, "5826",
            alert["code"],
            alert["severity"],
            json.dumps(alert["signals"]),
            alert["interpretation"],
            alert["operational"],
            json.dumps({k: v for k, v in state.items()
                        if k != "upstream"}),
        ))
    con_spw.commit()


def print_alerts(alerts, state):
    """Print formatted alert report."""
    H   = state.get("H") or 0
    Q   = state.get("Q") or 0
    P7  = state.get("P_7d_mean") or state.get("basin_rain_7d") or 0
    ts  = state.get("timestamp", "unknown")[:16]

    SEVERITY_COLORS = {
        "CRITICAL": "🔴",
        "HIGH":     "🟠",
        "WATCH":    "🟡",
        "MODERATE": "🟡",
        "INFO":     "🟢",
    }

    print("\n" + "═" * 62)
    print(f"  WWI OPERATIONAL ALERT REPORT — SAUHEID (Ourthe)")
    print(f"  {ts}")
    print("═" * 62)
    print(f"  Current state:  H={H:.3f}m  Q={Q:.2f}m³/s  "
          f"P(7d)={P7:.1f}mm")
    print("─" * 62)

    for alert in alerts:
        icon = SEVERITY_COLORS.get(alert["severity"], "⚪")
        print(f"\n{icon} [{alert['severity']}] {alert['code']}")
        print(f"  Signals:")
        for s in alert["signals"]:
            print(f"    • {s}")
        print(f"  Physical interpretation:")
        print(f"    {alert['interpretation']}")
        print(f"  Operational recommendation:")
        print(f"    {alert['operational']}")

    print("\n" + "═" * 62)
    active = [a for a in alerts if a["severity"] != "INFO"]
    if active:
        worst = max(active,
                    key=lambda x: ["INFO","WATCH","MODERATE",
                                   "HIGH","CRITICAL"].index(x["severity"]))
        print(f"  OVERALL STATUS: {SEVERITY_COLORS[worst['severity']]} "
              f"{worst['severity']} — {worst['code']}")
    else:
        print("  OVERALL STATUS: 🟢 NORMAL — no active alerts")
    print("═" * 62)


if __name__ == "__main__":
    log.info("WWI Alert Engine")

    con_spw = sqlite3.connect(DB_SPW, timeout=30)
    con_fc  = sqlite3.connect(DB_FC,  timeout=30) \
              if Path(DB_FC).exists() else None

    state  = load_current_state(con_spw, con_fc)
    alerts = evaluate_alerts(state)

    print_alerts(alerts, state)
    save_alerts(con_spw, alerts, state)

    if con_fc: con_fc.close()
    con_spw.close()

    # Export for LLM bulletin
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "station":   "SAUHEID",
        "state":     {k: v for k, v in state.items()
                      if k != "upstream"},
        "alerts":    alerts,
    }

    # Always-current file (read by llm_bulletin.py)
    out_current = ROOT / "wwi" / "export" / "csvs" / "current_alerts.json"
    with open(out_current, "w") as f:
        json.dump(payload, f, indent=2)

    # Timestamped archive (historical record)
    archive_dir = ROOT / "wwi" / "export" / "csvs" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M")
    out_archive = archive_dir / f"alerts_{ts_str}.json"
    with open(out_archive, "w") as f:
        json.dump(payload, f, indent=2)

    log.info(f"Alerts saved → {out_current}")
    log.info(f"Archived    → {out_archive}")
