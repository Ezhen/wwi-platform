"""
WWI River Network Intelligence — Q-H Diagrams Colored by Propagation Speed
=============================================================================

For each of NAMUR, HUY, LIEGE (the Meuse chain), plots discharge Q
(x-axis) against water level H (y-axis) for that single station, then
overlays color on points falling inside a detected rise event, colored
by how fast that event propagated to the NEXT station downstream in
the chain (Namur->Huy, Huy->Liege). LIEGE has no further downstream
station in the current network, so it is colored by the INCOMING lag
from Huy instead (how fast water arrived INTO Liege), clearly labelled
as such rather than silently reusing the upstream-color convention.

Why this matters: H alone is a proxy for "how much water is moving."
Q (discharge) is the more direct physical driver of kinematic wave
celerity. If propagation speed tracks the station's own Q-H curve
(e.g. consistently faster at high Q, regardless of which physical
state currently sits there), that supports a genuine discharge-driven
celerity mechanism. If speed scatters independently of the Q-H curve
shape, that points more towards other explanations (e.g. event
selection/survivorship — only the most robust events make it the full
distance downstream to be detected and timed at all).

Method
------
1. Reuses check_wave_propagation.py's loading, rise-rate, and
   event-detection logic, generalised to also load Q (NOTE: per
   historical_ingest.py, Q is only fetched at Day.Mean resolution —
   there is no hourly Q timeseries in historical_liege.db. Q is
   therefore loaded at daily resolution and upsampled to match H's
   grid via interpolation; this is a real resolution mismatch, not a
   script limitation, and is labelled on every plot).
2. For each station's own detected rise events, fit the lag to the
   relevant neighbouring station (downstream for Namur/Huy, the
   incoming lag from upstream for Liege) using the same windowed,
   next-event-capped cross-correlation as wave_propagation_confidence.py.
3. Plot full (Q, H) state-space coverage in grey, overlay colored
   points only where an event's fitted lag is defined.

Output
------
  export/maps/qh_diagram_<station>.png — one per station

Run
---
  cd ~/wwi
  python3 plot_qh_diagram.py
"""

import sqlite3
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from check_wave_propagation import (
    load_stations,
    load_h_series,
    rise_rate,
    best_lag_xcorr,
    detect_events,
    resample_to_grid,
    MAX_LAG_HOURS,
    LAG_STEP_HOURS,
    EVENT_LOCAL_WINDOW_HOURS,
    EVENT_MIN_OVERLAP_HOURS,
    DB_HIST,
)

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "export" / "maps"

EVENT_SPAN_DECAY_HOURS = 24

# The Meuse chain we're examining, in upstream-to-downstream order.
# Each entry: (label, role) where role is "source" (only has a
# downstream neighbour), "relay" (has both an upstream and downstream
# neighbour — not used here but kept for clarity/future extension), or
# "sink" (only has an upstream neighbour, colored by INCOMING lag).
CHAIN = ["NAMUR", "HUY", "LIEGE"]


# ── Q loading (generalised from load_h_series; Q is Day.Mean only) ──
def load_q_series(con, station_no):
    """
    Load Q series for a station. Per historical_ingest.py, Q is fetched
    ONLY at Day.Mean resolution — there is no hourly Q timeseries in
    historical_liege.db. Returns (series, resolution) on a daily-derived
    grid upsampled to hourly via interpolation (same approach
    load_h_series uses for its own daily fallback), or (None, None) if
    no Q data exists for this station.
    """
    q = """
        SELECT timestamp, value, ts_name
        FROM observations
        WHERE station_no = ? AND parameter = 'Q'
        ORDER BY timestamp
    """
    df = pd.read_sql_query(q, con, params=(station_no,))
    if df.empty:
        return None, None

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)
    daily_mask = df["ts_name"].str.match(r"^Day\.", case=False, na=False)
    sub = df.loc[daily_mask, ["timestamp", "value"]].dropna() if daily_mask.any() \
        else df[["timestamp", "value"]].dropna()

    if sub.empty:
        return None, None

    sub = sub.drop_duplicates(subset="timestamp").set_index("timestamp").sort_index()
    series = sub["value"]
    full_idx = pd.date_range(series.index.min(), series.index.max(), freq="h")
    series = series.reindex(full_idx).interpolate(limit=48)
    return series, "daily->interpolated"


# ── Per-event lag with next-event capping (same fix validated in
#    wave_propagation_confidence.py) ───────────────────────────────
def event_lags_and_spans(up_rate, down_rate, events,
                          max_lag=MAX_LAG_HOURS,
                          local_window=EVENT_LOCAL_WINDOW_HOURS,
                          span_decay=EVENT_SPAN_DECAY_HOURS,
                          step_hours=LAG_STEP_HOURS):
    sorted_events = events.sort_values("timestamp").reset_index(drop=True)
    timestamps = sorted_events["timestamp"].tolist()

    out = []
    for i, ts in enumerate(timestamps):
        fit_start = ts - pd.Timedelta(hours=local_window)
        natural_end = ts + pd.Timedelta(hours=local_window + max_lag)
        if i + 1 < len(timestamps):
            capped_end = timestamps[i + 1] - pd.Timedelta(hours=1)
            fit_end = min(natural_end, capped_end)
        else:
            fit_end = natural_end
        if fit_end <= ts:
            continue

        up_local = up_rate.loc[fit_start:fit_end]
        down_local = down_rate.loc[fit_start:fit_end]
        lag, corr = best_lag_xcorr(
            up_local, down_local, max_lag,
            min_overlap=EVENT_MIN_OVERLAP_HOURS, step_hours=step_hours
        )
        if lag is not None:
            span_end = ts + pd.Timedelta(hours=span_decay)
            out.append((ts, span_end, lag))
    return out


def tag_series_with_lag(index, event_spans):
    tags = pd.Series(np.nan, index=index)
    for start, end, lag in event_spans:
        tags.loc[start:end] = lag
    return tags


# ── Plotting ────────────────────────────────────────────────────────
def plot_station(label, q_series, h_series, lag_tags, color_label, out_path):
    df = pd.DataFrame({"q": q_series, "h": h_series, "lag": lag_tags}).dropna(
        subset=["q", "h"]
    )
    if df.empty:
        return False

    fig, ax = plt.subplots(figsize=(8, 7))
    ax.scatter(df["q"], df["h"], s=4, color="lightgrey", alpha=0.4,
               linewidths=0, label="no event (background)")

    colored = df.dropna(subset=["lag"])
    if not colored.empty:
        sc = ax.scatter(colored["q"], colored["h"], s=10, c=colored["lag"],
                         cmap="viridis_r", alpha=0.85, linewidths=0,
                         vmin=colored["lag"].min(), vmax=colored["lag"].max())
        cbar = fig.colorbar(sc, ax=ax)
        cbar.set_label(f"{color_label} (hours) \u2014 darker = faster")

    ax.set_xlabel(f"{label} discharge Q (m\u00b3/s) \u2014 daily resolution, interpolated")
    ax.set_ylabel(f"{label} water level H (m)")
    ax.set_title(f"{label}: Q-H diagram\n"
                 f"({len(colored)} event-hours colored / {len(df)} total)")
    ax.legend(loc="upper left", framealpha=0.8, fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return True


# ── Main ────────────────────────────────────────────────────────────
def main():
    if not Path(DB_HIST).exists():
        raise FileNotFoundError(f"Database not found at {DB_HIST}.")

    con = sqlite3.connect(DB_HIST)
    stations = load_stations(con)

    station_no_by_label = {}
    h_cache, q_cache = {}, {}
    for label in CHAIN:
        matches = stations.loc[stations["label"] == label, "station_no"]
        if matches.empty:
            print(f"  {label}: not found in stations table, skipping")
            continue
        sno = matches.iloc[0]
        station_no_by_label[label] = sno

        h, h_res = load_h_series(con, sno)
        q, q_res = load_q_series(con, sno)
        if h is None:
            print(f"  {label}: no H data, skipping")
            continue
        if q is None:
            print(f"  {label}: no Q data, skipping (this station may be H-only)")
            continue
        h_cache[label] = (h, h_res)
        q_cache[label] = (q, q_res)
        print(f"  {label}: H ({h_res}) and Q ({q_res}) loaded")
    con.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n_plots = 0

    for i, label in enumerate(CHAIN):
        if label not in h_cache or label not in q_cache:
            continue

        h_series, _ = h_cache[label]
        q_series, _ = q_cache[label]

        # Determine neighbour relationship and which lag to color by.
        if i + 1 < len(CHAIN) and CHAIN[i + 1] in h_cache:
            # This station has a downstream neighbour: color by THIS
            # station's events propagating TO that neighbour.
            down_label = CHAIN[i + 1]
            down_series, _ = h_cache[down_label]
            up_rate_fine = rise_rate(resample_to_grid(h_series), step_hours=LAG_STEP_HOURS)
            down_rate_fine = rise_rate(resample_to_grid(down_series), step_hours=LAG_STEP_HOURS)
            events = detect_events(h_series)
            spans = event_lags_and_spans(up_rate_fine, down_rate_fine, events)
            color_label = f"Lag to {down_label} (downstream)"
        elif i > 0 and CHAIN[i - 1] in h_cache:
            # No downstream neighbour (end of chain): color by the
            # INCOMING lag from the upstream neighbour instead, clearly
            # labelled so it isn't mistaken for the same convention.
            up_label = CHAIN[i - 1]
            up_series_neighbor, _ = h_cache[up_label]
            up_rate_fine = rise_rate(resample_to_grid(up_series_neighbor), step_hours=LAG_STEP_HOURS)
            down_rate_fine = rise_rate(resample_to_grid(h_series), step_hours=LAG_STEP_HOURS)
            events = detect_events(up_series_neighbor)
            spans = event_lags_and_spans(up_rate_fine, down_rate_fine, events)
            color_label = f"Incoming lag from {up_label} (upstream)"
        else:
            print(f"  {label}: no usable neighbour in chain, skipping")
            continue

        if not spans:
            print(f"  {label}: no fittable events, skipping plot")
            continue

        shared_index = h_series.index.intersection(q_series.index)
        lag_tags = tag_series_with_lag(shared_index, spans)

        safe_label = label.lower().replace(" ", "_")
        out_path = OUT_DIR / f"qh_diagram_{safe_label}.png"
        ok = plot_station(label, q_series.reindex(shared_index),
                           h_series.reindex(shared_index), lag_tags,
                           color_label, out_path)
        if ok:
            n_plots += 1
            print(f"  {label}: saved {out_path.name} ({len(spans)} events, "
                  f"colored by: {color_label})")

    print(f"\n✓ Saved {n_plots} plots -> {OUT_DIR}")


if __name__ == "__main__":
    main()
