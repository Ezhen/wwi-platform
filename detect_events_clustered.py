"""
WWI Event Discovery & Clustering
=================================

Discovers hydrological "events" on each station's H series relative to
a seasonal baseline (not fixed absolute thresholds), then clusters the
discovered events by shape into natural types (flash flood, slow flood,
drought, etc.) using DBSCAN — letting the number and definition of
event types emerge from the data rather than being assumed up front.

Pipeline
--------
1. Seasonal baseline per station: day-of-year circular rolling mean/std
   of H (window = +/- BASELINE_WINDOW_DAYS, wraps across the year
   boundary), computed across all available years. Every observation
   gets a z-score: z = (H_obs - baseline_mean) / baseline_std.

2. Event detection per station, on the z-score (not raw H), two families:
     a) Sustained anomaly: |z| stays above SUSTAINED_Z_THRESH for at
        least SUSTAINED_MIN_HOURS consecutive hours. Sign of z
        distinguishes flood-like (+) from drought-like (-).
     b) Rapid change: |z(t) - z(t - RAPID_WINDOW_HOURS)| exceeds
        RAPID_DZ_THRESH, independent of absolute level — catches
        flash-type rises even from a normal seasonal baseline.
   Overlapping detections are merged into one event per station with
   start/end/peak times.

3. Feature extraction per event (all interpretable, for explaining
   cluster centroids later):
     - peak_abs_z       : maximum |z| reached during the event
     - duration_hours    : event length
     - max_rate_z_per_h  : maximum |dz/dt| during the event
     - sign              : +1 flood-like, -1 drought-like
     - recession_half_life_h : hours from peak to decay to half-peak |z|
                               (np.nan if the event doesn't fully recede
                               within the available follow-on window)

4. Clustering: features are standardised (z-scored across events, not
   to be confused with the per-observation H z-score from step 1) and
   passed to DBSCAN. Events pooled across ALL stations together — the
   same physical event at two stations becomes two events, deliberately
   (per-station independent, pooled for clustering), so you can later
   compare how the same event looks at different network points.
   DBSCAN's noise label (-1) is kept as a real category: singleton
   extremes (e.g. July 2021) are expected to fall there rather than be
   forced into a cluster.

Output
------
  export/csvs/events_catalog.csv  — one row per detected event, with
      features and assigned cluster label.

Run
---
  cd ~/wwi
  python3 detect_events_clustered.py
"""

import sqlite3
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
DB_HIST = str(ROOT / "export" / "databases" / "historical_liege.db")
OUT_CSV = ROOT / "export" / "csvs" / "events_catalog.csv"

BASELINE_WINDOW_DAYS = 7      # +/- days around each day-of-year for climatology
MIN_YEARS_FOR_BASELINE = 1    # relax if you only have partial history

SUSTAINED_Z_THRESH = 2.0
SUSTAINED_MIN_HOURS = 12

RAPID_WINDOW_HOURS = 6
RAPID_DZ_PERCENTILE = 99.0   # threshold = this percentile of the station's
                               # OWN |dz| distribution, NOT a fixed value or
                               # a fixed multiple of std/MAD shared across
                               # the network.
                               #
                               # Rationale (confirmed empirically on real WWI
                               # data, then re-confirmed on synthetic data):
                               # stations on large regulated rivers (Meuse:
                               # lock operations, navigation traffic) have
                               # noisier hour-to-hour signal than small
                               # headwater torrents (Vesdre at Eupen) — raw
                               # |dH/dt| at Huy/Liège runs ~15-20x that of
                               # Eupen. A single global z-threshold massively
                               # over-detects on noisy stations.
                               #
                               # A first fix (multiple-of-MAD) over-corrected
                               # the other way: at very quiet stations, real
                               # events sit almost entirely in the extreme
                               # tail (p99->p99.9 can jump 5-6x) while MAD is
                               # set by the calm baseline and assumes a
                               # std/MAD ratio (~1.4826) that only holds for
                               # genuinely Gaussian noise — it doesn't here.
                               # A direct percentile cut adapts to each
                               # station's own tail shape without relying on
                               # that Gaussian assumption.

MERGE_GAP_HOURS = 6           # detections within this gap get merged into one event

DBSCAN_EPS = 0.7              # Tuned via k-distance elbow analysis (k=3,
                                 # matching min_samples below) AND validated
                                 # against synthetic data with known
                                 # fast/slow/drought/extreme sub-types.
                                 # 0.8 (the original guess) merges fast and
                                 # slow flood shapes into a single cluster
                                 # despite them being genuinely distinct —
                                 # this is exactly what was observed on real
                                 # WWI data (453 events, 358 of them dumped
                                 # into one over-broad cluster spanning the
                                 # full duration/rate range). 0.7 separates
                                 # all four known sub-types with >93% purity
                                 # each. Revisit this value again once you
                                 # have a full multi-year run across all 8
                                 # stations — the right eps depends on the
                                 # actual feature-space density of YOUR
                                 # events, and this number is the best
                                 # estimate available without that.
DBSCAN_MIN_SAMPLES = 3


# ── Data loading (reuses the same schema/resolution logic as
#    check_wave_propagation.py: hourly 'h.Mean' preferred, daily
#    'Day.Mean' fallback) ────────────────────────────────────────
def load_stations(con):
    return pd.read_sql_query("SELECT * FROM stations", con)


def load_h_series(con, station_no):
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

    hourly_mask = df["ts_name"].str.match(r"^h\.", case=False, na=False)
    daily_mask = df["ts_name"].str.match(r"^Day\.", case=False, na=False)

    if hourly_mask.any():
        sub = df.loc[hourly_mask, ["timestamp", "value"]].dropna()
        resolution = "hourly"
    elif daily_mask.any():
        sub = df.loc[daily_mask, ["timestamp", "value"]].dropna()
        resolution = "daily"
    else:
        sub = df[["timestamp", "value"]].dropna()
        resolution = "unknown"

    if sub.empty:
        return None, None

    sub = sub.drop_duplicates(subset="timestamp").set_index("timestamp").sort_index()
    series = sub["value"]

    full_idx = pd.date_range(series.index.min(), series.index.max(), freq="h")
    limit = 48 if resolution == "daily" else 6
    series = series.reindex(full_idx).interpolate(limit=limit)

    return series, resolution


# ── Seasonal baseline ─────────────────────────────────────────────
def compute_seasonal_baseline(series, window_days=BASELINE_WINDOW_DAYS):
    """
    Day-of-year circular rolling mean/std. Returns a DataFrame indexed
    like `series` with columns baseline_mean, baseline_std, z.

    Circular handling: day-of-year distance wraps at 365/366, so e.g.
    Dec 28 and Jan 3 are ~6 days apart, not ~360.
    """
    doy = series.index.dayofyear.values
    values = series.values
    n = len(series)

    # Precompute, for each unique day-of-year present in the data, the
    # circular-window mean/std pooled across all years.
    unique_doys = np.unique(doy)
    doy_stats = {}
    for d in unique_doys:
        # circular distance from d to every other day-of-year value
        diff = np.abs(doy - d)
        circ_diff = np.minimum(diff, 366 - diff)
        mask = circ_diff <= window_days
        window_vals = values[mask]
        window_vals = window_vals[~np.isnan(window_vals)]
        if len(window_vals) >= 2:
            doy_stats[d] = (np.nanmean(window_vals), np.nanstd(window_vals))
        else:
            doy_stats[d] = (np.nan, np.nan)

    baseline_mean = np.array([doy_stats[d][0] for d in doy])
    baseline_std = np.array([doy_stats[d][1] for d in doy])

    # Guard against zero/near-zero std producing exploding z-scores
    # (flat series segments, e.g. frozen sensors or droughts).
    safe_std = np.where(baseline_std < 1e-4, np.nan, baseline_std)

    z = (values - baseline_mean) / safe_std

    return pd.DataFrame(
        {"H": values, "baseline_mean": baseline_mean,
         "baseline_std": baseline_std, "z": z},
        index=series.index,
    )


# ── Event detection ────────────────────────────────────────────────
def detect_sustained(baseline_df, z_thresh=SUSTAINED_Z_THRESH,
                      min_hours=SUSTAINED_MIN_HOURS):
    """Boolean mask of timestamps belonging to a sustained anomaly run."""
    z = baseline_df["z"]
    above = (z.abs() >= z_thresh).fillna(False)

    # Identify runs of consecutive True values >= min_hours long
    run_id = (above != above.shift()).cumsum()
    run_lengths = above.groupby(run_id).transform("sum")
    qualifies = above & (run_lengths >= min_hours)
    return qualifies


def detect_rapid(baseline_df, window_hours=RAPID_WINDOW_HOURS,
                  percentile=RAPID_DZ_PERCENTILE):
    """
    Boolean mask of timestamps where |Δz over window_hours| exceeds the
    given percentile of THIS station's own |dz| distribution, rather
    than a fixed value or a fixed multiple of std/MAD shared across the
    network.

    Percentile-based (rather than MAD-based) because the std/MAD ratio
    is not stable across stations with very different noise regimes —
    at quiet headwater stations, genuine rapid-change events can live
    almost entirely in the extreme tail (p99->p99.9 jumping 5-6x) while
    MAD is set by calm baseline behaviour and assumes a Gaussian
    std/MAD relationship that doesn't hold there. A direct percentile
    cut adapts to each station's own tail shape without that
    assumption, and naturally yields comparable detection rates (by
    construction, the same percentile flags the same FRACTION of hours
    at every station) regardless of whether the station is a quiet
    torrent or a noisy regulated lowland river.
    """
    z = baseline_df["z"]
    dz = z.diff(window_hours)

    dz_abs_clean = dz.abs().dropna()
    if len(dz_abs_clean) < 100:
        return pd.Series(False, index=baseline_df.index)

    threshold = np.percentile(dz_abs_clean, percentile)
    return (dz.abs() >= threshold).fillna(False)


def merge_to_events(mask, baseline_df, merge_gap_hours=MERGE_GAP_HOURS):
    """
    Convert a boolean timestamp mask into discrete event windows,
    merging detections separated by <= merge_gap_hours.
    Returns list of (start, end) timestamps.
    """
    flagged = baseline_df.index[mask]
    if len(flagged) == 0:
        return []

    events = []
    start = flagged[0]
    prev = flagged[0]
    for ts in flagged[1:]:
        gap_hours = (ts - prev).total_seconds() / 3600
        if gap_hours > merge_gap_hours:
            events.append((start, prev))
            start = ts
        prev = ts
    events.append((start, prev))
    return events


def extract_event_features(baseline_df, start, end, station_label,
                            recession_search_hours=72):
    """
    Build the interpretable feature vector for one event window.
    `recession_search_hours` extends the window past `end` to measure
    how long it takes z to decay to half its peak value.
    """
    window = baseline_df.loc[start:end]
    z_window = window["z"].dropna()
    if z_window.empty:
        return None

    peak_idx = z_window.abs().idxmax()
    peak_z = z_window.loc[peak_idx]
    peak_abs_z = abs(peak_z)
    sign = 1 if peak_z > 0 else -1

    duration_hours = (end - start).total_seconds() / 3600

    dz = window["z"].diff().abs()
    max_rate = dz.max()

    # Recession half-life: search forward from peak until |z| <= peak/2
    search_end = peak_idx + pd.Timedelta(hours=recession_search_hours)
    post_peak = baseline_df.loc[peak_idx:search_end, "z"].dropna()
    half_target = peak_abs_z / 2
    below_half = post_peak[post_peak.abs() <= half_target]
    if not below_half.empty:
        recession_half_life_h = (below_half.index[0] - peak_idx).total_seconds() / 3600
    else:
        recession_half_life_h = np.nan

    return {
        "station": station_label,
        "start": start,
        "end": end,
        "peak_time": peak_idx,
        "peak_abs_z": peak_abs_z,
        "duration_hours": duration_hours,
        "max_rate_z_per_h": max_rate,
        "sign": sign,
        "recession_half_life_h": recession_half_life_h,
    }


def detect_events_for_station(series, station_label):
    baseline_df = compute_seasonal_baseline(series)

    sustained_mask = detect_sustained(baseline_df)
    rapid_mask = detect_rapid(baseline_df)
    combined_mask = sustained_mask | rapid_mask

    windows = merge_to_events(combined_mask, baseline_df)

    events = []
    for start, end in windows:
        feat = extract_event_features(baseline_df, start, end, station_label)
        if feat is not None:
            events.append(feat)

    return events, baseline_df


# ── Clustering ─────────────────────────────────────────────────────
FEATURE_COLS = ["peak_abs_z", "duration_hours", "max_rate_z_per_h",
                 "sign", "recession_half_life_h"]


def cluster_events(events_df, eps=DBSCAN_EPS, min_samples=DBSCAN_MIN_SAMPLES):
    """
    Standardise features and run DBSCAN.

    Two features can legitimately be NaN per event and both are
    imputed rather than dropped, since a missing value here is itself
    informative, not absent data:
      - recession_half_life_h: event never fully receded within the
        search window (e.g. a sustained drought) -> imputed to the
        observed max (such events are the slowest-receding by
        construction, so the max is the appropriate fill value).
      - max_rate_z_per_h: event window too short (e.g. a single-hour
        spike) for .diff() to produce a non-NaN value -> imputed to 0,
        i.e. "no measurable rate over multiple hours," which is the
        correct interpretation for a single-point event.
    """
    feat = events_df[FEATURE_COLS].copy()

    if feat["recession_half_life_h"].notna().any():
        recession_impute = feat["recession_half_life_h"].max()
    else:
        recession_impute = 72.0
    feat["recession_half_life_h"] = feat["recession_half_life_h"].fillna(recession_impute)

    feat["max_rate_z_per_h"] = feat["max_rate_z_per_h"].fillna(0.0)

    scaler = StandardScaler()
    X = scaler.fit_transform(feat)

    db = DBSCAN(eps=eps, min_samples=min_samples)
    labels = db.fit_predict(X)

    events_df = events_df.copy()
    events_df["cluster"] = labels
    return events_df


def summarise_clusters(events_df):
    print("\n=== Cluster summary (cluster -1 = noise / singleton outliers) ===")
    for cluster_id, group in events_df.groupby("cluster"):
        print(f"\nCluster {cluster_id} (n={len(group)}):")
        print(group[FEATURE_COLS].mean().round(2).to_string())
        examples = group.nlargest(3, "peak_abs_z")[["station", "start", "peak_abs_z"]]
        print("  example events:")
        print(examples.to_string(index=False))


# ── Main ────────────────────────────────────────────────────────────
def main():
    if not Path(DB_HIST).exists():
        raise FileNotFoundError(
            f"Database not found at {DB_HIST}. "
            "Run this script from the wwi/ root, or edit DB_HIST."
        )

    con = sqlite3.connect(DB_HIST)
    stations = load_stations(con)

    all_events = []
    for _, row in stations.iterrows():
        series, resolution = load_h_series(con, row["station_no"])
        if series is None:
            continue
        events, _ = detect_events_for_station(series, row["label"])
        print(f"{row['label']}: {len(events)} events detected "
              f"({resolution} resolution)")
        all_events.extend(events)
    con.close()

    if not all_events:
        print("\nNo events detected. Check threshold constants if this "
              "seems too strict for the available data.")
        return

    events_df = pd.DataFrame(all_events)
    print(f"\nTotal events across all stations: {len(events_df)}")

    events_df = cluster_events(events_df)
    summarise_clusters(events_df)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    events_df.to_csv(OUT_CSV, index=False)
    print(f"\n✓ Saved {len(events_df)} events -> {OUT_CSV}")


if __name__ == "__main__":
    main()
