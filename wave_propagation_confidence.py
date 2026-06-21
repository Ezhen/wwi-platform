"""
WWI River Network Intelligence — Bootstrap Confidence Intervals on Lag
=========================================================================

Extends check_wave_propagation.py with bootstrap confidence intervals
on the propagation lag estimates, instead of reporting a single point
estimate with no sense of how much it could move with a different
sample of history.

Method
------
Reuses check_wave_propagation.py's validated station-pairing, rise-rate,
and per-event local lag-fitting logic UNCHANGED (imported, not
reimplemented) — this script only adds the resampling layer on top.

1. For each connected station pair, detect upstream rise events exactly
   as check_wave_propagation.py does.
2. For every individual event (not just per magnitude bin), compute its
   own local best-fit lag via the same windowed cross-correlation used
   internally by lag_for_event_bin — but expose the per-event lag list
   directly, rather than only the bin mean.
3. Bootstrap: resample the per-event lag list with replacement
   (N_BOOTSTRAP times), take the mean of each resample, and use the
   2.5th/97.5th percentiles of that distribution as a 95% CI on the
   mean lag.
4. Repeat per magnitude bin (small/moderate/large) as well as overall
   (all events pooled, regardless of magnitude).

Honesty built in: magnitude bins with very few events (commonly the
case for "large" — genuine big events are rare by definition) will
produce wide, possibly not-very-useful CIs. This is reported as-is,
with the event count shown alongside every CI, rather than hidden.
A CI from 3 events is real information ("we don't know this well
yet"), not a reason to suppress the result.

Output
------
  export/csvs/wave_propagation_ci.csv — one row per station pair per
      bin (overall + small/moderate/large), with:
      river, upstream, downstream, bin, n_events,
      median_lag_h, ci_lower_h, ci_upper_h

Run
---
  cd ~/wwi
  python3 wave_propagation_confidence.py

Requires the same historical_liege.db as check_wave_propagation.py.
"""

import sqlite3
import warnings
from pathlib import Path
from itertools import combinations

import numpy as np
import pandas as pd

from check_wave_propagation import (
    load_stations,
    load_h_series,
    rise_rate,
    best_lag_xcorr,
    detect_events,
    resample_to_grid,
    MAX_LAG_HOURS,
    MAG_BINS,
    LAG_STEP_HOURS,
    EVENT_LOCAL_WINDOW_HOURS,
    EVENT_MIN_OVERLAP_HOURS,
    DB_HIST,
)

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent
OUT_CSV = ROOT / "export" / "csvs" / "wave_propagation_ci.csv"

N_BOOTSTRAP = 1000
CI_LOWER_PCT = 2.5
CI_UPPER_PCT = 97.5
RANDOM_SEED = 42  # fixed seed so re-running reproduces the same CI, rather
                    # than the CI itself jittering run to run from resampling
                    # noise on top of already-limited event counts


# ── Per-event lag extraction (refactor of lag_for_event_bin's internals
#    to expose the individual lags, not just their mean) ──────────────
def per_event_lags(up_rate, down_rate, events, max_lag=MAX_LAG_HOURS,
                    local_window=EVENT_LOCAL_WINDOW_HOURS,
                    min_overlap=EVENT_MIN_OVERLAP_HOURS, step_hours=1.0):
    """
    For every event in `events` (a DataFrame with a 'timestamp' column,
    as returned by detect_events), compute its own local best-fit lag.
    Returns a plain list of lags (one float per event where a lag could
    be fit; events with insufficient local data are silently skipped,
    same as lag_for_event_bin does internally).

    CRITICAL FIX (confirmed via real Namur->Huy data): the search window
    end was originally fixed at `ts + local_window + max_lag`, regardless
    of how close the NEXT detected event was. On real data, 82% of
    events have another event within 24h, so a ~48-72h fitting window
    frequently swallowed a neighboring rise, and the cross-correlation
    locked onto that later event's correlation peak instead of the
    current event's true (much shorter) lag — producing per-event lags
    clustered at 7-16h on a pair whose clean global baseline lag was
    2.75h, plus a smaller cluster pinned exactly at MAX_LAG_HOURS (a
    second, distinct boundary artifact).

    Fix: the search window's end is capped at whichever is SMALLER —
    the original ts + local_window + max_lag, or the timestamp of the
    next detected event (with a small safety margin) — so a fit can
    never search past where a different event begins. This means a
    fit might miss a true lag longer than the gap to the next event,
    but that's the right tradeoff: a fit that can lock onto the wrong
    event is worse than a fit that returns "insufficient data" when
    events are packed too closely to disentangle individually.
    """
    sorted_events = events.sort_values("timestamp").reset_index(drop=True)
    timestamps = sorted_events["timestamp"].tolist()

    lags = []
    for i, ts in enumerate(timestamps):
        start = ts - pd.Timedelta(hours=local_window)
        natural_end = ts + pd.Timedelta(hours=local_window + max_lag)

        if i + 1 < len(timestamps):
            next_event_ts = timestamps[i + 1]
            # Small safety margin so the window doesn't run right up to
            # the very first sample of the next event's own rise.
            capped_end = next_event_ts - pd.Timedelta(hours=1)
            end = min(natural_end, capped_end)
        else:
            end = natural_end

        if end <= ts:
            # Next event is too close even for a minimal window —
            # genuinely can't fit this event in isolation, skip it
            # rather than fit against contaminated data.
            continue

        up_local = up_rate.loc[start:end]
        down_local = down_rate.loc[start:end]
        lag, corr = best_lag_xcorr(
            up_local, down_local, max_lag,
            min_overlap=min_overlap, step_hours=step_hours
        )
        if lag is not None:
            lags.append(float(lag))
    return lags


# ── Bootstrap ───────────────────────────────────────────────────────
def bootstrap_ci(lags, n_bootstrap=N_BOOTSTRAP, rng=None):
    """
    Bootstrap a 95% CI on the MEDIAN of `lags` (a list/array of
    per-event lag estimates). Returns (median_lag, ci_lower, ci_upper,
    n_events).

    Switched from mean to median after confirming on real data
    (Eupen->Chaudfontaine: 112 events) that per-event lag distributions
    can be genuinely bimodal: a real, concentrated cluster of fast fits
    (0-14h, ~70% of events) plus a sparse tail running out to exactly
    MAX_LAG_HOURS, including several events pinned EXACTLY at that
    ceiling — a boundary-search artifact (the cross-correlation search
    hit its limit without finding a true peak), not a real long lag.
    The mean is highly sensitive to this tail (pulling a representative
    ~8h median up to an 18h mean); the median is barely moved by it.
    This does NOT remove the underlying boundary-artifact problem —
    events pinned at the ceiling are still being included — but it
    stops that contamination from dominating the headline number, and
    a persistently wide CI on a contaminated pair is itself useful
    information rather than something to silently average away.

    With 0 events: returns all-NaN. With 1-2 events: the CI is computed
    but will be extremely wide or degenerate (bootstrapping the median
    of 1-2 values reproduces them directly) — this is correct
    behaviour, not a bug: a CI from 1-2 events SHOULD say "we don't
    know," not produce a falsely tight interval.
    """
    n = len(lags)
    if n == 0:
        return np.nan, np.nan, np.nan, 0

    lags_arr = np.array(lags)
    if rng is None:
        rng = np.random.default_rng(RANDOM_SEED)

    boot_medians = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        resample = rng.choice(lags_arr, size=n, replace=True)
        boot_medians[i] = np.median(resample)

    median_lag = np.median(lags_arr)
    ci_lower = np.percentile(boot_medians, CI_LOWER_PCT)
    ci_upper = np.percentile(boot_medians, CI_UPPER_PCT)
    return median_lag, ci_lower, ci_upper, n


# ── Main ────────────────────────────────────────────────────────────
def main():
    if not Path(DB_HIST).exists():
        raise FileNotFoundError(
            f"Database not found at {DB_HIST}. "
            "Run this script from the wwi/ root, or edit DB_HIST in "
            "check_wave_propagation.py."
        )

    con = sqlite3.connect(DB_HIST)
    stations = load_stations(con)

    series_cache = {}
    for _, row in stations.iterrows():
        s, res = load_h_series(con, row["station_no"])
        if s is not None:
            series_cache[row["station_no"]] = (s, res)
    con.close()

    print(f"Loaded H series for {len(series_cache)}/{len(stations)} stations.\n")

    results = []
    rng = np.random.default_rng(RANDOM_SEED)

    for river, group in stations.groupby("river"):
        st_nos = [s for s in group["station_no"] if s in series_cache]
        if len(st_nos) < 2:
            continue

        print(f"=== River: {river} ===")

        for a, b in combinations(st_nos, 2):
            series_a, _ = series_cache[a]
            series_b, _ = series_cache[b]
            label_a = group.loc[group["station_no"] == a, "label"].iloc[0]
            label_b = group.loc[group["station_no"] == b, "label"].iloc[0]

            # Resample to the same fine grid check_wave_propagation.py
            # uses, for consistency between the two scripts' lag
            # estimates on the same data.
            series_a_fine = resample_to_grid(series_a)
            series_b_fine = resample_to_grid(series_b)

            rate_a = rise_rate(series_a_fine, step_hours=LAG_STEP_HOURS)
            rate_b = rise_rate(series_b_fine, step_hours=LAG_STEP_HOURS)

            lag_ab, corr_ab = best_lag_xcorr(rate_a, rate_b, MAX_LAG_HOURS,
                                               step_hours=LAG_STEP_HOURS)
            lag_ba, corr_ba = best_lag_xcorr(rate_b, rate_a, MAX_LAG_HOURS,
                                               step_hours=LAG_STEP_HOURS)

            if lag_ab is None and lag_ba is None:
                continue

            if (corr_ab or -np.inf) >= (corr_ba or -np.inf):
                upstream_lbl, downstream_lbl = label_a, label_b
                up_series, up_rate_s, down_rate_s = series_a, rate_a, rate_b
            else:
                upstream_lbl, downstream_lbl = label_b, label_a
                up_series, up_rate_s, down_rate_s = series_b, rate_b, rate_a

            # Event detection on the ORIGINAL hourly series (timing
            # precision doesn't need the fine grid here).
            events = detect_events(up_series)
            if events.empty:
                continue

            # Overall (all events pooled, regardless of magnitude)
            all_lags = per_event_lags(up_rate_s, down_rate_s, events,
                                       step_hours=LAG_STEP_HOURS)
            median_lag, ci_lo, ci_hi, n = bootstrap_ci(all_lags, rng=rng)
            results.append({
                "river": river, "upstream": upstream_lbl,
                "downstream": downstream_lbl, "bin": "overall",
                "n_events": n, "median_lag_h": median_lag,
                "ci_lower_h": ci_lo, "ci_upper_h": ci_hi,
            })

            # Per magnitude bin
            for bin_name, (lo, hi) in MAG_BINS.items():
                sel = events[(events["rise_amplitude_m"] >= lo) &
                             (events["rise_amplitude_m"] < hi)]
                bin_lags = per_event_lags(up_rate_s, down_rate_s, sel,
                                           step_hours=LAG_STEP_HOURS)
                median_lag, ci_lo, ci_hi, n = bootstrap_ci(bin_lags, rng=rng)
                results.append({
                    "river": river, "upstream": upstream_lbl,
                    "downstream": downstream_lbl, "bin": bin_name,
                    "n_events": n, "median_lag_h": median_lag,
                    "ci_lower_h": ci_lo, "ci_upper_h": ci_hi,
                })

            overall_row = results[-(len(MAG_BINS) + 1)]
            ci_str = (f"[{overall_row['ci_lower_h']:.1f}, {overall_row['ci_upper_h']:.1f}]"
                      if not np.isnan(overall_row["ci_lower_h"]) else "[n/a]")
            print(f"  {upstream_lbl} -> {downstream_lbl}: "
                  f"median_lag={overall_row['median_lag_h']:.1f}h  "
                  f"95% CI={ci_str}  (n={overall_row['n_events']} events)")

        print()

    if not results:
        print("No results — check that check_wave_propagation.py runs "
              "cleanly against the same database first.")
        return

    df_out = pd.DataFrame(results)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(OUT_CSV, index=False)
    print(f"\n✓ Saved {len(df_out)} rows -> {OUT_CSV}")

    print("\n=== Bins with fewer than 5 events (CI likely too wide to trust) ===")
    thin = df_out[(df_out["n_events"] > 0) & (df_out["n_events"] < 5)]
    if not thin.empty:
        print(thin[["river", "upstream", "downstream", "bin", "n_events",
                     "median_lag_h", "ci_lower_h", "ci_upper_h"]].to_string(index=False))
    else:
        print("  None — every bin had at least 5 events.")


if __name__ == "__main__":
    main()
