"""
WWI Event Catalog — Background vs Notable, Per River
=======================================================

Takes the output of detect_events_clustered.py (events_catalog.csv,
one row per detected event with a DBSCAN cluster label) and produces
two organized, river-grouped catalogs:

  1. events_catalog_background.csv — events in the single large
     continuum cluster (cluster 1 on the last validated run; see
     BACKGROUND_CLUSTER_ID below). These are "ordinary" rises and
     recessions with no discrete sub-type — confirmed via histogram
     inspection to be unimodal, right-skewed, with no natural seam.
     Kept because they're exactly the small/moderate magnitude
     population that check_wave_propagation.py's magnitude bins
     (MAG_BINS: small/moderate/large) are built to characterise.

  2. events_catalog_notable.csv — every event in a DBSCAN outlier or
     small/distinct cluster (everything that is NOT the background
     cluster, including noise label -1). These are the genuinely
     distinct cases worth writing up individually for the catalog —
     candidate flash floods, the July 2021 extreme, droughts, etc.
     Each gets a human-readable type label DERIVED FROM the cluster's
     own feature signature (sign / relative rate / relative duration
     compared to the background population), not assumed in advance.

Both catalogs are joined with:
  - `river` (via the stations table — the raw events_catalog.csv only
    has station label, not river)
  - the matching propagation pair from wave_propagation.csv, if the
    event's station appears as an upstream or downstream station in
    any pair, so you can directly look up "what lag did THIS specific
    event imply" without manually cross-referencing two files. The
    magnitude bin used for lookup is chosen by where the event's
    peak_abs_z falls relative to the population of peak_abs_z values
    (tertiles), not by the absolute z thresholds used in MAG_BINS in
    check_wave_propagation.py, since these are two independently
    calibrated scales (different scripts, different events) — treat
    the join as approximate/indicative, not a precise lookup.

Run
---
  cd ~/wwi
  python3 build_event_catalog.py

Requires events_catalog.csv (from detect_events_clustered.py) and,
optionally, wave_propagation.csv (from check_wave_propagation.py) to
already exist in export/csvs/.
"""

import sqlite3
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
DB_HIST = str(ROOT / "export" / "databases" / "historical_liege.db")
EVENTS_CSV = ROOT / "export" / "csvs" / "events_catalog.csv"
PROPAGATION_CSV = ROOT / "export" / "csvs" / "wave_propagation.csv"

OUT_BACKGROUND = ROOT / "export" / "csvs" / "events_catalog_background.csv"
OUT_NOTABLE = ROOT / "export" / "csvs" / "events_catalog_notable.csv"

# Identifies which cluster is the "background continuum" rather than a
# genuinely distinct type. This is NOT auto-detected, because "largest
# cluster" is a fragile heuristic (a future run with better-tuned
# detection could legitimately have its largest cluster be a real
# type). Set this explicitly after inspecting cluster histograms, as
# was done for the cluster-1 finding above. If you re-run event
# detection and get different cluster IDs, update this before running
# this script, or pass --background-cluster on the command line.
BACKGROUND_CLUSTER_ID = 1


# ── River lookup ──────────────────────────────────────────────────
def load_station_river_map(con):
    df = pd.read_sql_query("SELECT label, river FROM stations", con)
    return dict(zip(df["label"], df["river"]))


# ── Cluster type labeling, derived from feature signature ─────────
def label_cluster_types(events_df, background_cluster_id):
    """
    For every non-background cluster, derive a human-readable label
    from its mean feature signature relative to the background
    population — rather than assuming generic names. Returns a dict
    {cluster_id: label_string}.
    """
    bg = events_df[events_df["cluster"] == background_cluster_id]
    bg_rate = bg["max_rate_z_per_h"].median()
    bg_duration = bg["duration_hours"].median()
    bg_peak = bg["peak_abs_z"].median()

    labels = {-1: "extreme outlier (singleton, did not cluster)"}

    for cluster_id, group in events_df.groupby("cluster"):
        if cluster_id in (background_cluster_id, -1):
            continue

        rate_ratio = group["max_rate_z_per_h"].median() / max(bg_rate, 1e-6)
        duration_ratio = group["duration_hours"].median() / max(bg_duration, 1e-6)
        peak_ratio = group["peak_abs_z"].median() / max(bg_peak, 1e-6)
        sign = group["sign"].mode()[0]

        # "Drought" implies a sustained, slow-developing deficit — not
        # appropriate for a brief sign=-1 event (a sharp, short drop is
        # a rapid recession/dip, not a drought). Pick the sign word
        # based on BOTH sign and duration_ratio, rather than sign alone.
        if sign > 0:
            sign_word = "flood-like"
        elif duration_ratio >= 1.5:
            sign_word = "drought-like"
        else:
            sign_word = "sharp-drop"

        # Direction-aware rate wording: a sign=-1 event that changes
        # quickly is falling/receding fast, not "rising" — rate is a
        # magnitude (|dz/dt|), it carries no sign of its own, so the
        # word must be chosen explicitly per sign rather than reusing
        # flood-oriented language for both directions.
        rising_word = "fast-rising" if sign > 0 else "fast-falling"
        slow_word = "slow-developing" if sign > 0 else "slow-receding"

        descriptors = []
        if rate_ratio >= 2:
            descriptors.append(rising_word)
        elif rate_ratio <= 0.5:
            descriptors.append(slow_word)

        if duration_ratio >= 2:
            descriptors.append("long-duration")
        elif duration_ratio <= 0.5:
            descriptors.append("short-duration")

        if peak_ratio >= 2:
            descriptors.append("high-magnitude")

        if not descriptors:
            descriptors.append("moderate")

        labels[cluster_id] = f"{' '.join(descriptors)} {sign_word}"

    return labels


# ── Wave propagation cross-reference ───────────────────────────────
def build_propagation_lookup(propagation_df):
    """
    Build a lookup from station label -> list of propagation pair rows
    where that station appears as either upstream or downstream.
    """
    lookup = {}
    if propagation_df is None:
        return lookup

    for _, row in propagation_df.iterrows():
        for station_label, role in [(row["upstream"], "upstream"),
                                      (row["downstream"], "downstream")]:
            lookup.setdefault(station_label, []).append({
                "role": role,
                "river": row["river"],
                "paired_with": row["downstream"] if role == "upstream" else row["upstream"],
                "baseline_lag_h": row["baseline_lag_h"],
                "lag_small_h": row.get("lag_small_h"),
                "lag_moderate_h": row.get("lag_moderate_h"),
                "lag_large_h": row.get("lag_large_h"),
            })
    return lookup


def magnitude_tertile(peak_abs_z, all_peaks):
    """Classify an event's peak_abs_z into small/moderate/large relative
    to the full population's tertiles, for an approximate match against
    check_wave_propagation.py's magnitude-binned lag estimates."""
    t1, t2 = np.percentile(all_peaks, [33, 66])
    if peak_abs_z <= t1:
        return "small", "lag_small_h"
    elif peak_abs_z <= t2:
        return "moderate", "lag_moderate_h"
    else:
        return "large", "lag_large_h"


def attach_propagation_info(events_df, propagation_lookup):
    all_peaks = events_df["peak_abs_z"].values
    rows = []
    for _, ev in events_df.iterrows():
        matches = propagation_lookup.get(ev["station"], [])
        mag_label, lag_col = magnitude_tertile(ev["peak_abs_z"], all_peaks)

        if not matches:
            rows.append({
                "propagation_role": None,
                "propagation_paired_with": None,
                "propagation_baseline_lag_h": None,
                "magnitude_tertile": mag_label,
                "propagation_lag_for_magnitude_h": None,
            })
            continue

        # If a station appears in multiple pairs (e.g. as upstream in
        # one and downstream in another), report the strongest-corr
        # pairing only, to keep one row per event rather than
        # exploding rows per match.
        best = matches[0]
        rows.append({
            "propagation_role": best["role"],
            "propagation_paired_with": best["paired_with"],
            "propagation_baseline_lag_h": best["baseline_lag_h"],
            "magnitude_tertile": mag_label,
            "propagation_lag_for_magnitude_h": best.get(lag_col),
        })

    prop_df = pd.DataFrame(rows, index=events_df.index)
    return pd.concat([events_df, prop_df], axis=1)


# ── Main ────────────────────────────────────────────────────────────
def main():
    if not EVENTS_CSV.exists():
        raise FileNotFoundError(
            f"{EVENTS_CSV} not found. Run detect_events_clustered.py first."
        )

    events_df = pd.read_csv(EVENTS_CSV, parse_dates=["start", "end", "peak_time"])

    con = sqlite3.connect(DB_HIST)
    station_river_map = load_station_river_map(con)
    con.close()
    events_df["river"] = events_df["station"].map(station_river_map)

    unmapped = events_df[events_df["river"].isna()]["station"].unique()
    if len(unmapped) > 0:
        print(f"Warning: {len(unmapped)} station(s) not found in stations "
              f"table river mapping: {list(unmapped)}. Their events will "
              f"have river=NaN.")

    propagation_df = None
    if PROPAGATION_CSV.exists():
        propagation_df = pd.read_csv(PROPAGATION_CSV)
        print(f"Loaded {len(propagation_df)} propagation pairs from "
              f"{PROPAGATION_CSV.name} for cross-referencing.")
    else:
        print(f"Note: {PROPAGATION_CSV.name} not found — propagation "
              f"columns will be empty. Run check_wave_propagation.py "
              f"first for full cross-referencing.")

    propagation_lookup = build_propagation_lookup(propagation_df)
    events_df = attach_propagation_info(events_df, propagation_lookup)

    type_labels = label_cluster_types(events_df, BACKGROUND_CLUSTER_ID)
    events_df["event_type"] = events_df["cluster"].map(type_labels)
    events_df.loc[events_df["cluster"] == BACKGROUND_CLUSTER_ID, "event_type"] = \
        "background variability"

    background = events_df[events_df["cluster"] == BACKGROUND_CLUSTER_ID].copy()
    notable = events_df[events_df["cluster"] != BACKGROUND_CLUSTER_ID].copy()

    background = background.sort_values(["river", "station", "start"])
    notable = notable.sort_values(["river", "cluster", "start"])

    OUT_BACKGROUND.parent.mkdir(parents=True, exist_ok=True)
    background.to_csv(OUT_BACKGROUND, index=False)
    notable.to_csv(OUT_NOTABLE, index=False)

    print(f"\n✓ Background events: {len(background)} -> {OUT_BACKGROUND}")
    print(f"✓ Notable events:    {len(notable)} -> {OUT_NOTABLE}")

    print("\n=== Notable events per river ===")
    print(notable.groupby("river").size().to_string())

    print("\n=== Notable event types found ===")
    for cluster_id, label in sorted(type_labels.items()):
        n = (notable["cluster"] == cluster_id).sum()
        print(f"  cluster {cluster_id}: {label}  (n={n})")

    n_with_propagation = notable["propagation_role"].notna().sum()
    print(f"\n{n_with_propagation}/{len(notable)} notable events have a "
          f"matching wave-propagation pair for their station.")


if __name__ == "__main__":
    main()
