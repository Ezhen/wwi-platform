"""
Fix ROOT paths in scripts that were moved one level deeper.
Scripts in model/, processing/, ingestion/ need parent.parent not parent.
Run from ~/wwi/ after reorganise.sh
"""
from pathlib import Path
import re

ROOT_PARENT = "ROOT = Path(__file__).resolve().parent.parent\n"
ROOT_SINGLE = "ROOT = Path(__file__).resolve().parent\n"

# Scripts now one level deep that need parent.parent
SUBDIR_SCRIPTS = [
    "model/build_features.py",
    "model/build_features_v2.py",
    "model/train_model.py",
    "model/explain_prediction.py",
    "processing/aggregate_catchment_stats.py",
    "processing/extract_slopes.py",
    "processing/ndvi_from_corine.py",
    "processing/validate_catchments.py",
    "supplementary/fix_H_ingest.py",
    "supplementary/shrink_databases.py",
    "ingestion/era5_2021_ingest.py",
    "ingestion/era5_lean_ingest.py",
    "ingestion/era5_historical_ingest.py",
]

for script in SUBDIR_SCRIPTS:
    p = Path(script)
    if not p.exists():
        print(f"  skip (not found): {script}")
        continue

    content = p.read_text()

    # Replace any ROOT = Path(...).parent (single) with parent.parent
    new_content = re.sub(
        r"ROOT\s*=\s*Path\(__file__\)\.resolve\(\)\.parent\n",
        ROOT_PARENT,
        content
    )
    # Also handle parent.parent.parent (triple) → parent.parent
    new_content = re.sub(
        r"ROOT\s*=\s*Path\(__file__\)\.resolve\(\)\.parent\.parent\.parent\n",
        ROOT_PARENT,
        new_content
    )

    if new_content != content:
        p.write_text(new_content)
        print(f"  ✓ fixed ROOT in {script}")
    else:
        # Check if ROOT is already correct
        if "parent.parent" in new_content:
            print(f"  ✓ already correct: {script}")
        else:
            print(f"  ? no ROOT found: {script}")

print("\nDone")
