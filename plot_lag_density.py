"""
WWI River Network Intelligence — State-Space Lag Density Plots
===================================================================

For each connected station pair (from check_wave_propagation.py), plots
downstream H (x-axis) against upstream H (y-axis) across the full
overlapping time series, then overlays color on exactly the timestamps
that fall inside a detected rise event, colored by that event's
fitted propagation lag (hours).

Why this view: the magnitude-binned lag analysis (small/moderate/large)
only looks at the upstream rise SIZE. This plot instead shows where in
the JOINT (H_down, H_up) state space propagation events actually occur,
and whether lag varies systematically across that state space — e.g.
do fast-propagating events cluster in a particular corner (both
stations already elevated — a "primed" basin) versus slow ones
occurring from a calm baseline?

Method
------
1. Reuses check_wave_propagation.py's station-pairing, rise-rate, and
   event-detection logic unchanged (imported, not reimplemented).
2. For each pair: load H_up(t), H_down(t) on the shared hourly index.
3. Detect upstream events (detect_events, as in the other scripts).
4. For each event, fit its local lag (same windowed cross-correlation
   used elsewhere) — but instead of averaging into a bin, assign that
   single lag value to every hourly timestamp within the EVENT SPAN
   itself (the rise window + a short decay tail), not the full ±24h
   fitting window used just for cross-correlation search. Coloring the
   entire fitting window would paint mostly "nothing happening" time
   as if it were part of the event.
5. Plot: all (H_down, H_up) points as a light grey background (full
   state-space coverage), then overlay the event-tagged points as a
   colored scatter (colormap = lag in hours), one PNG per station pair.

Event span definition: from the event's detected start (where the
rolling rise crossed EVENT_MIN_RISE_M over EVENT_WINDOW_HOURS) through
EVENT_SPAN_DECAY_HOURS afterward, capturing the rise and immediate
recession without dragging in the full quiet-period fitting buffer.

Output
------
  export/maps/lag_density_<upstream>_<downstream>.png — one per pair

Run
---
  cd ~/wwi
  python3 plot_lag_density.py
"""

import sqlite3
import warnings
from pathlib import Path
from itertools import combinations

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
    MAX_LAG_HOURS,
    EVENT_LOCAL_WINDOW_HOURS,
    EVENT_MIN_OVERLAP_HOURS,
    EVENT_WINDOW_HOURS,
    DB_HIST,
)

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "export" / "maps"

EVENT_SPAN_DECAY_HOURS = 24  # how far past an event's detected start to
                               # keep coloring points, capturing the rise
                               # and immediate recession only — not the
                               # full quiet-period fitting buffer


# ── Per-event lag + span ───────────────────────────────────────────
def event_lags_and_spans(up_rate, down_rate, events,
                          max_lag=MAX_LAG_HOURS,
                          local_window=EVENT_LOCAL_WINDOW_HOURS,
                          span_decay=EVENT_SPAN_DECAY_HOURS):
    """
    For every detected event, fit its local lag (same method as
    check_wave_propagation.py / wave_propagation_confidence.py) and
    return a list of (event_start, event_end, lag) tuples — event_end
    is event_start + span_decay, NOT the full fitting window, so the
    caller can color only the genuine event span.
    """
    out = []
    for ts in events["timestamp"]:
        fit_start = ts - pd.Timedelta(hours=local_window)
        fit_end = ts + pd.Timedelta(hours=local_window + max_lag)
        up_local = up_rate.loc[fit_start:fit_end]
        down_local = down_rate.loc[fit_start:fit_end]
        lag, corr = best_lag_xcorr(
            up_local, down_local, max_lag, min_overlap=EVENT_MIN_OVERLAP_HOURS
        )
        if lag is not None:
            span_end = ts + pd.Timedelta(hours=span_decay)
            out.append((ts, span_end, lag))
    return out


def tag_series_with_lag(index, event_spans):
    """
    Build a Series aligned to `index`, NaN everywhere except inside a
    detected event span, where it holds that event's lag value.
    Later events overwrite earlier ones if spans overlap (rare, but
    possible for closely-spaced events) — last-detected wins, which is
    an arbitrary but harmless tie-break for a visualisation.
    """
    tags = pd.Series(np.nan, index=index)
    for start, end, lag in event_spans:
        tags.loc[start:end] = lag
    return tags


# ── Plotting ────────────────────────────────────────────────────────
def plot_pair(up_label, down_label, h_up, h_down, lag_tags, river, out_path):
    df = pd.DataFrame({"h_up": h_up, "h_down": h_down, "lag": lag_tags}).dropna(
        subset=["h_up", "h_down"]
    )
    if df.empty:
        return False

    fig, ax = plt.subplots(figsize=(8, 7))

    # Full state-space coverage, light grey background
    ax.scatter(df["h_down"], df["h_up"], s=4, color="lightgrey",
               alpha=0.4, linewidths=0, label="no event (background)")

    # Colored overlay: only points tagged with a lag value
    colored = df.dropna(subset=["lag"])
    if not colored.empty:
        sc = ax.scatter(colored["h_down"], colored["h_up"], s=10,
                         c=colored["lag"], cmap="viridis_r",
                         alpha=0.85, linewidths=0,
                         vmin=colored["lag"].min(), vmax=colored["lag"].max())
        cbar = fig.colorbar(sc, ax=ax)
        cbar.set_label("Propagation lag (hours) — darker = faster")

    ax.set_xlabel(f"{down_label} water level H (m)")
    ax.set_ylabel(f"{up_label} water level H (m)")
    ax.set_title(f"{river}: {up_label} \u2192 {down_label}\n"
                 f"State-space lag density "
                 f"({len(colored)} event-hours colored / {len(df)} total)")
    ax.legend(loc="upper left", framealpha=0.8, fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return True


# ── Main ────────────────────────────────────────────────────────────
def main():
    if not Path(DB_HIST).exists():
        raise FileNotFoundError(
            f"Database not found at {DB_HIST}. Run from the wwi/ root."
        )

    con = sqlite3.connect(DB_HIST)
    stations = load_stations(con)

    series_cache = {}
    for _, row in stations.iterrows():
        s, res = load_h_series(con, row["station_no"])
        if s is not None:
            series_cache[row["station_no"]] = (s, res)
    con.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n_plots = 0

    for river, group in stations.groupby("river"):
        st_nos = [s for s in group["station_no"] if s in series_cache]
        if len(st_nos) < 2:
            continue

        for a, b in combinations(st_nos, 2):
            series_a, _ = series_cache[a]
            series_b, _ = series_cache[b]
            label_a = group.loc[group["station_no"] == a, "label"].iloc[0]
            label_b = group.loc[group["station_no"] == b, "label"].iloc[0]

            rate_a = rise_rate(series_a)
            rate_b = rise_rate(series_b)

            lag_ab, corr_ab = best_lag_xcorr(rate_a, rate_b, MAX_LAG_HOURS)
            lag_ba, corr_ba = best_lag_xcorr(rate_b, rate_a, MAX_LAG_HOURS)

            if lag_ab is None and lag_ba is None:
                continue

            if (corr_ab or -np.inf) >= (corr_ba or -np.inf):
                up_label, down_label = label_a, label_b
                up_series, up_rate_s, down_series_s = series_a, rate_a, series_b
                down_rate_s = rate_b
            else:
                up_label, down_label = label_b, label_a
                up_series, up_rate_s, down_series_s = series_b, rate_b, series_a
                down_rate_s = rate_a

            events = detect_events(up_series)
            if events.empty:
                print(f"  {up_label} -> {down_label}: no events detected, skipping plot")
                continue

            event_spans = event_lags_and_spans(up_rate_s, down_rate_s, events)
            if not event_spans:
                print(f"  {up_label} -> {down_label}: no fittable events, skipping plot")
                continue

            shared_index = up_series.index.intersection(down_series_s.index)
            lag_tags = tag_series_with_lag(shared_index, event_spans)

            safe_up = up_label.lower().replace(" ", "_")
            safe_down = down_label.lower().replace(" ", "_")
            out_path = OUT_DIR / f"lag_density_{safe_up}_{safe_down}.png"

            ok = plot_pair(up_label, down_label,
                            up_series.reindex(shared_index),
                            down_series_s.reindex(shared_index),
                            lag_tags, river, out_path)

            if ok:
                n_plots += 1
                print(f"  {up_label} -> {down_label}: saved {out_path.name} "
                      f"({len(event_spans)} events plotted)")

    print(f"\n✓ Saved {n_plots} plots -> {OUT_DIR}")


if __name__ == "__main__":
    main()
