#!/bin/bash
# WWI Project Reorganisation
# Moves scripts to correct directories, cleans up root
# Run from ~/wwi/

set -e
echo "=== WWI Project Reorganisation ==="
echo "Working from: $(pwd)"
echo ""

# ── Create new directories ────────────────────────────────────────────────────
mkdir -p model alerts verification
echo "✓ Created: model/ alerts/ verification/"

# ── model/ — ML training and feature engineering ─────────────────────────────
for f in build_features.py build_features_v2.py train_model.py explain_prediction.py; do
    if [ -f "$f" ]; then
        mv "$f" model/
        echo "  → model/$f"
    fi
done

# ── alerts/ — alert engine and bulletin ──────────────────────────────────────
# (live_explain.py, build_alerts.py, llm_bulletin.py stay at root)
# Nothing to move here — operational scripts stay at root

# ── verification/ ─────────────────────────────────────────────────────────────
# forecast_verification.py stays at root

# ── ingestion/ — move stray ingestion scripts ─────────────────────────────────
for f in era5_2021_ingest.py era5_lean_ingest.py era5_historical_ingest.py; do
    if [ -f "$f" ]; then
        mv "$f" ingestion/
        echo "  → ingestion/$f"
    fi
done

# ── processing/ — move processing scripts from root ──────────────────────────
for f in aggregate_catchment_stats.py extract_slopes.py \
          ndvi_from_corine.py validate_catchments.py; do
    if [ -f "$f" ]; then
        mv "$f" processing/
        echo "  → processing/$f"
    fi
done

# ── supplementary/ — one-off fix scripts ─────────────────────────────────────
for f in fix_H_ingest.py fix_roots_paths.py shrink_databases.py \
          fix_root_paths.py dem_processing.py; do
    if [ -f "$f" ]; then
        mv "$f" supplementary/
        echo "  → supplementary/$f"
    fi
done

# ── Clean up ghost wwi/ directory ────────────────────────────────────────────
if [ -d "wwi" ]; then
    echo ""
    echo "Cleaning ghost wwi/ directory..."
    rm -rf wwi/
    echo "  ✓ Deleted wwi/"
fi

# ── Update update.sh paths ────────────────────────────────────────────────────
echo ""
echo "update.sh paths unchanged (operational scripts stay at root)"

# ── Final tree ────────────────────────────────────────────────────────────────
echo ""
echo "=== Root directory after reorganisation ==="
ls -1 *.py *.sh *.md 2>/dev/null || true

echo ""
echo "=== Directory structure ==="
for d in ingestion processing model alerts verification visualisation supplementary discovery; do
    if [ -d "$d" ]; then
        count=$(ls "$d"/*.py 2>/dev/null | wc -l)
        echo "  $d/  ($count scripts)"
    fi
done

echo ""
echo "✓ Reorganisation complete"
echo ""
echo "Next: update import paths in model/ scripts if needed"
echo "  model/build_features_v2.py — ROOT = Path(__file__).resolve().parent.parent"
echo "  model/train_model.py       — ROOT = Path(__file__).resolve().parent.parent"
