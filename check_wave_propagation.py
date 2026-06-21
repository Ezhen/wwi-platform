"""
WWI River Network Intelligence — Wave Propagation Analysis
============================================================

For every river in the network, estimates how long a water-level rise
at an upstream station takes to reach each downstream station, and
whether that travel time depends on the size of the rise (kinematic
wave theory predicts faster propagation for larger flood waves).

Method
------
1. Load H (water level) series per station from `observations`.
   Prefers hourly resolution (ts_name LIKE '%Hour%'); falls back to
   daily (ts_name LIKE '%Day%') per-station if no hourly rows exist.
2. Within each river, station order is NOT assumed from metadata
   (no distance/elevation columns exist in `stations`) — it is
   discovered empirically: for every pair of stations on the same
   river, cross-correlate rise-rate series and keep the pair only if
   a stable positive lag exists (A leads B). This avoids hardcoding
   upstream/downstream order.
3. Baseline lag = lag (hours) that maximises cross-correlation of
   rise-rate (dH/dt), not raw H — rise-rate isolates propagating
   events from slow seasonal drift.
4. Magnitude-dependent lag: detect discrete rise events at the
   upstream station (ΔH over a rolling window crossing a threshold),
   bin events by event amplitude (small / moderate / large), and
   re-estimate the lag that maximises correlation within each bin
   by locally cross-correlating event-windowed rise-rate segments.

Output
------
  export/csvs/wave_propagation.csv   — one row per station pair:
      river, upstream, downstream, n_hours_used, resolution,
      baseline_lag_h, baseline_corr,
      lag_small_h, lag_moderate_h, lag_large_h,
      n_events_small, n_events_moderate, n_events_large

  Console summary in the "Eupen --7h--> Chaudfontaine --4h--> Liège"
  style for the strongest chain per river.

Run
---
  cd ~/wwi
  python3 check_wave_propagation.py
"""

import sqlite3
import warnings
from pathlib import Path
from itertools import combinations

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
DB_HIST = str(ROOT / "export" / "databases" / "historical_liege.db")
OUT_CSV = ROOT / "export" / "csvs" / "wave_propagation.csv"

MAX_LAG_HOURS = 72          # search window for cross-correlation. Raised from
                              # 48 after confirming on real data that 2-8% of
                              # per-event lag fits were landing exactly at the
                              # old 48h ceiling (Comblain->Angleur, Chaudfontaine
                              # ->Verviers) — a search-limit artifact, not a true
                              # optimum. 72h gives headroom above any genuine
                              # slow-propagation events seen so far (max real
                              # fit observed: 38h on Comblain->Sauheid).
MIN_OVERLAP_HOURS = 24 * 30  # need at least ~1 month of overlapping data
MIN_CORR = 0.3               # below this, pair is not considered "connected"

# Event detection: a "rise event" at the upstream station is any
# window where H increases by more than this much within 6 hours.
EVENT_WINDOW_HOURS = 6
EVENT_MIN_RISE_M = 0.05

# Magnitude bins for the upstream rise amplitude (in metres over the
# event window). Edit these once you've seen the real distribution.
MAG_BINS = {
    "small":    (0.05, 0.15),
    "moderate": (0.15, 0.40),
    "large":    (0.40, np.inf),
}

EVENT_LOCAL_WINDOW_HOURS = 24  # +/- window around each event for local lag fit
EVENT_MIN_OVERLAP_HOURS = 20    # min overlapping hours required within a single
                                # event window (must be well below the full-series
                                # MIN_OVERLAP_HOURS threshold, which a single event
                                # window can never reach)


# ── Data loading ──────────────────────────────────────────────────
def load_stations(con):
    return pd.read_sql_query("SELECT * FROM stations", con)


def load_h_series(con, station_no):
    """
    Load H series for a station, hourly-preferred, daily-fallback.
    Returns (series, resolution_label) where series is a pandas
    Series indexed by tz-naive datetime, or (None, None) if no H data.
    """
    q = """
        SELECT timestamp, value, ts_name
        FROM observations
        WHERE station_no = ? AND parameter = 'H'
        ORDER BY timestamp
    """
    df = pd.read_sql_query(q, con, params=(station_no,))
    if df.empty:
        return None, None

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)

    # Real ts_name values observed in historical_liege.db are
    # 'h.Mean' (hourly) and 'Day.Mean' (daily) — note lowercase 'h',
    # which does NOT contain the substring 'Hour'. Match on the
    # leading token instead of guessing a human-readable substring.
    hourly_mask = df["ts_name"].str.match(r"^h\.", case=False, na=False)
    daily_mask = df["ts_name"].str.match(r"^Day\.", case=False, na=False)

    if hourly_mask.any():
        sub = df.loc[hourly_mask, ["timestamp", "value"]].dropna()
        resolution = "hourly"
    elif daily_mask.any():
        sub = df.loc[daily_mask, ["timestamp", "value"]].dropna()
        resolution = "daily"
    else:
        # Unknown ts_name labelling — fall back to whatever exists
        sub = df[["timestamp", "value"]].dropna()
        resolution = "unknown"

    if sub.empty:
        return None, None

    sub = sub.drop_duplicates(subset="timestamp").set_index("timestamp").sort_index()
    series = sub["value"]
    # Regularise to hourly grid (interpolate small gaps only) so that
    # cross-correlation lag is interpretable directly in hours.
    if resolution == "daily":
        # Upsample daily to hourly via linear interpolation so that lag
        # results stay on an hourly scale; flag this in output.
        full_idx = pd.date_range(series.index.min(), series.index.max(), freq="h")
        series = series.reindex(full_idx).interpolate(limit=48)
    else:
        full_idx = pd.date_range(series.index.min(), series.index.max(), freq="h")
        series = series.reindex(full_idx).interpolate(limit=6)

    return series, resolution


# ── Lag estimation ────────────────────────────────────────────────
LAG_STEP_HOURS = 0.25  # sub-hour grid resolution for lag search (15 min).
                         # The underlying data is hourly (h.Mean) — this
                         # does NOT recover information finer than what
                         # hourly samples contain. It interpolates the
                         # smoothed rise-rate curve onto a finer grid so
                         # that the cross-correlation PEAK can be located
                         # between whole hours, rather than being forced
                         # to round to the nearest hour by construction.
                         # This is why chained lags (e.g. Namur->Huy +
                         # Huy->Liege) can now sum closer to the direct
                         # Namur->Liege lag instead of each independently
                         # rounding to a different nearby integer.


def resample_to_grid(series, step_hours=LAG_STEP_HOURS):
    """
    Upsample an hourly series onto a finer regular grid via linear
    interpolation, for sub-hour-resolution lag search. Does not
    interpolate across gaps larger than a few hours (limit scales with
    how many sub-steps fit in ~6 original hours), to avoid inventing
    long stretches of fake data where the original series had real gaps.
    """
    freq = pd.Timedelta(hours=step_hours)
    full_idx = pd.date_range(series.index.min(), series.index.max(), freq=freq)
    limit = max(1, int(6 / step_hours))  # ~6 original hours' worth of sub-steps
    return series.reindex(full_idx).interpolate(limit=limit)


def rise_rate(series, window_hours=3, step_hours=1.0):
    """
    Smoothed dH/dt in m/h, used instead of raw H for correlation.

    `step_hours` is the time between consecutive samples in `series`
    (1.0 for the original hourly grid, LAG_STEP_HOURS for a resampled
    finer grid). The rolling window is sized in SAMPLES as
    window_hours / step_hours, so the smoothing window stays a
    constant real-world duration (window_hours) regardless of grid
    resolution — without this, calling rise_rate on a 15-min-resampled
    series with the same window_hours=3 would silently smooth over
    45 minutes instead of 3 hours.
    """
    window_samples = max(1, round(window_hours / step_hours))
    smoothed = series.rolling(window_samples, min_periods=1, center=True).mean()
    diff = smoothed.diff()
    # diff() gives change PER STEP; convert to per-hour rate so units
    # stay comparable across different grid resolutions.
    return diff / step_hours


def best_lag_xcorr(up_rate, down_rate, max_lag, min_overlap=None, step_hours=1.0):
    """
    Find the lag L (in HOURS, possibly fractional if step_hours < 1) in
    [0, max_lag] such that down_rate(t) best correlates with
    up_rate(t - L), i.e. downstream lags upstream by L.
    Returns (best_lag_hours, best_corr) or (None, None) if insufficient
    data.

    `min_overlap` is a duration in HOURS regardless of step_hours — it
    gets converted to a sample count internally, so existing threshold
    constants (MIN_OVERLAP_HOURS, EVENT_MIN_OVERLAP_HOURS) keep meaning
    "this many hours of real data" rather than "this many samples,"
    which would silently mean something different on a finer grid.

    `step_hours` is the time between samples in up_rate/down_rate (1.0
    for the original hourly grid, LAG_STEP_HOURS for a resampled finer
    grid). The lag search itself steps in SAMPLES (so it can resolve
    lags finer than 1 hour when step_hours < 1), then converts the
    winning step count back to hours for the return value.
    """
    if min_overlap is None:
        min_overlap = MIN_OVERLAP_HOURS
    min_overlap_samples = max(1, round(min_overlap / step_hours))
    max_lag_samples = max(1, round(max_lag / step_hours))

    df = pd.DataFrame({"up": up_rate, "down": down_rate}).dropna()
    if len(df) < min_overlap_samples:
        return None, None

    best_lag_samples, best_corr = None, -np.inf
    for lag_samples in range(0, max_lag_samples + 1):
        shifted_up = df["up"].shift(lag_samples)
        valid = pd.concat([shifted_up, df["down"]], axis=1).dropna()
        if len(valid) < min_overlap_samples:
            continue
        corr = valid.iloc[:, 0].corr(valid.iloc[:, 1])
        if corr is not None and corr > best_corr:
            best_corr, best_lag_samples = corr, lag_samples

    if best_lag_samples is None or best_corr < MIN_CORR:
        return None, None
    return best_lag_samples * step_hours, best_corr


def detect_events(series, window_hours=EVENT_WINDOW_HOURS, min_rise=EVENT_MIN_RISE_M):
    """
    Detect discrete rise events: timestamps where H rose by >= min_rise
    over the preceding `window_hours`. Returns DataFrame with columns
    [timestamp, rise_amplitude_m], deduplicated so events don't overlap
    within the same window.
    """
    rise = series.diff(window_hours)
    candidates = rise[rise >= min_rise].copy()
    if candidates.empty:
        return pd.DataFrame(columns=["timestamp", "rise_amplitude_m"])

    events = []
    last_ts = None
    for ts, amp in candidates.items():
        if last_ts is None or (ts - last_ts).total_seconds() / 3600 > window_hours:
            events.append((ts, amp))
            last_ts = ts
        elif amp > events[-1][1]:
            events[-1] = (ts, amp)
            last_ts = ts

    return pd.DataFrame(events, columns=["timestamp", "rise_amplitude_m"])


def lag_for_event_bin(up_rate, down_rate, events, bin_range, max_lag,
                       local_window=EVENT_LOCAL_WINDOW_HOURS, step_hours=1.0):
    """
    For events whose amplitude falls in bin_range, locally cross-correlate
    a window around each event and average the resulting best-lag.
    Returns (mean_lag, n_events_used).
    """
    lo, hi = bin_range
    sel = events[(events["rise_amplitude_m"] >= lo) & (events["rise_amplitude_m"] < hi)]
    if sel.empty:
        return None, 0

    lags = []
    for ts in sel["timestamp"]:
        start = ts - pd.Timedelta(hours=local_window)
        end = ts + pd.Timedelta(hours=local_window + max_lag)
        up_local = up_rate.loc[start:end]
        down_local = down_rate.loc[start:end]
        lag, corr = best_lag_xcorr(
            up_local, down_local, max_lag,
            min_overlap=EVENT_MIN_OVERLAP_HOURS, step_hours=step_hours
        )
        if lag is not None:
            lags.append(lag)

    if not lags:
        return None, 0
    return float(np.mean(lags)), len(lags)


# ── Main ────────────────────────────────────────────────────────────
def main():
    if not Path(DB_HIST).exists():
        raise FileNotFoundError(
            f"Database not found at {DB_HIST}. "
            "Run this script from the wwi/ root, or edit DB_HIST."
        )

    con = sqlite3.connect(DB_HIST)
    stations = load_stations(con)
    print(f"Loaded {len(stations)} stations across "
          f"{stations['river'].nunique()} rivers.\n")

    # Cache series per station so we don't re-query per pair
    series_cache = {}
    for _, row in stations.iterrows():
        s, res = load_h_series(con, row["station_no"])
        if s is not None:
            series_cache[row["station_no"]] = (s, res)
    con.close()

    print(f"Loaded H series for {len(series_cache)}/{len(stations)} stations "
          f"(missing stations have no H data).\n")

    results = []

    for river, group in stations.groupby("river"):
        st_nos = [s for s in group["station_no"] if s in series_cache]
        if len(st_nos) < 2:
            continue

        print(f"=== River: {river} ({len(st_nos)} stations with H data) ===")

        for a, b in combinations(st_nos, 2):
            series_a, res_a = series_cache[a]
            series_b, res_b = series_cache[b]
            label_a = group.loc[group["station_no"] == a, "label"].iloc[0]
            label_b = group.loc[group["station_no"] == b, "label"].iloc[0]

            # Resample onto the finer sub-hour grid for lag search.
            # Event DETECTION still uses the original hourly series
            # (detect_events below) since event timing doesn't need
            # sub-hour precision and running it on 4x more samples
            # would just slow things down for no benefit — only the
            # cross-correlation step benefits from the finer grid.
            series_a_fine = resample_to_grid(series_a)
            series_b_fine = resample_to_grid(series_b)

            rate_a = rise_rate(series_a_fine, step_hours=LAG_STEP_HOURS)
            rate_b = rise_rate(series_b_fine, step_hours=LAG_STEP_HOURS)

            # Try both directions; keep whichever gives a stable positive lag
            lag_ab, corr_ab = best_lag_xcorr(rate_a, rate_b, MAX_LAG_HOURS,
                                               step_hours=LAG_STEP_HOURS)
            lag_ba, corr_ba = best_lag_xcorr(rate_b, rate_a, MAX_LAG_HOURS,
                                               step_hours=LAG_STEP_HOURS)

            if lag_ab is None and lag_ba is None:
                continue  # no detectable connection between these two

            # Choose the direction with higher correlation as upstream->downstream
            if (corr_ab or -np.inf) >= (corr_ba or -np.inf):
                upstream_no, downstream_no = a, b
                upstream_lbl, downstream_lbl = label_a, label_b
                up_series, up_rate_s = series_a, rate_a
                down_rate_s = rate_b
                baseline_lag, baseline_corr = lag_ab, corr_ab
                resolution = res_a if res_a == res_b else f"{res_a}/{res_b}"
            else:
                upstream_no, downstream_no = b, a
                upstream_lbl, downstream_lbl = label_b, label_a
                up_series, up_rate_s = series_b, rate_b
                down_rate_s = rate_a
                baseline_lag, baseline_corr = lag_ba, corr_ba
                resolution = res_a if res_a == res_b else f"{res_a}/{res_b}"

            if baseline_lag is None:
                continue

            n_hours = pd.concat([up_rate_s, down_rate_s], axis=1).dropna().shape[0] * LAG_STEP_HOURS

            # Event detection on the ORIGINAL hourly up_series (timing
            # precision here doesn't matter — only the lag search needs
            # the fine grid).
            events = detect_events(up_series)
            bin_results = {}
            for bin_name, bin_range in MAG_BINS.items():
                lag_b_, n_ev = lag_for_event_bin(
                    up_rate_s, down_rate_s, events, bin_range, MAX_LAG_HOURS,
                    step_hours=LAG_STEP_HOURS
                )
                bin_results[bin_name] = (lag_b_, n_ev)

            row = {
                "river": river,
                "upstream": upstream_lbl,
                "downstream": downstream_lbl,
                "upstream_station_no": upstream_no,
                "downstream_station_no": downstream_no,
                "n_hours_used": n_hours,
                "resolution": resolution,
                "baseline_lag_h": round(baseline_lag, 2),
                "baseline_corr": round(baseline_corr, 3),
                "lag_small_h": round(bin_results["small"][0], 2) if bin_results["small"][0] is not None else None,
                "n_events_small": bin_results["small"][1],
                "lag_moderate_h": round(bin_results["moderate"][0], 2) if bin_results["moderate"][0] is not None else None,
                "n_events_moderate": bin_results["moderate"][1],
                "lag_large_h": round(bin_results["large"][0], 2) if bin_results["large"][0] is not None else None,
                "n_events_large": bin_results["large"][1],
            }
            results.append(row)

            print(f"  {upstream_lbl} -> {downstream_lbl}: "
                  f"lag={baseline_lag:.2f}h  corr={baseline_corr:.2f}  "
                  f"({n_hours:.0f} overlapping hours, {resolution})")

        print()

    if not results:
        print("No connected station pairs found. Check MIN_CORR / data coverage.")
        return

    df_out = pd.DataFrame(results)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(OUT_CSV, index=False)
    print(f"\n✓ Saved {len(df_out)} station-pair results -> {OUT_CSV}")

    # ── Magnitude-dependence summary ───────────────────────────────
    print("\n=== Magnitude dependence (kinematic wave check) ===")
    valid = df_out.dropna(subset=["lag_small_h", "lag_large_h"])
    if not valid.empty:
        faster_when_larger = (valid["lag_large_h"] < valid["lag_small_h"]).mean()
        print(f"  {faster_when_larger*100:.0f}% of pairs show FASTER propagation "
              f"for large rises than small rises (expected from kinematic wave theory).")
        print(valid[["upstream", "downstream", "lag_small_h",
                      "lag_moderate_h", "lag_large_h"]].to_string(index=False))
    else:
        print("  Not enough events in both small and large bins to test magnitude "
              "dependence yet — likely need more historical data or wider bins.")

    # ── Strongest chain per river (Eupen -> Chaudfontaine -> ... style) ──
    print("\n=== Strongest propagation chain per river ===")
    for river, group in df_out.groupby("river"):
        chain = group.sort_values("baseline_corr", ascending=False)
        top = chain.iloc[0]
        print(f"  {river}: {top['upstream']} --{top['baseline_lag_h']:.0f}h--> "
              f"{top['downstream']}  (corr={top['baseline_corr']:.2f})")


if __name__ == "__main__":
    main()
