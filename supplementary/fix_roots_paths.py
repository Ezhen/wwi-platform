"""
Fix ROOT path in all scripts to always resolve to ~/wwi/
regardless of which subdirectory the script lives in.
"""
from pathlib import Path
import re

# The correct ROOT resolution — walk up until we find the wwi directory
CORRECT_ROOT = '''def find_root():
    """Find project root (wwi/) regardless of script location."""
    p = Path(__file__).resolve()
    while p.name != "wwi" and p.parent != p:
        p = p.parent
    return p if p.name == "wwi" else Path(__file__).resolve().parent

ROOT = find_root()'''

SCRIPTS = [
    "live_explain.py",
    "build_alerts.py",
    "llm_bulletin.py",
    "forecast_verification.py",
    "build_features_v2.py",
    "build_features.py",
    "train_model.py",
    "processing/rebuild_all.py",
    "visualisation/build_map.py",
    "processing/ndvi_from_corine.py",
    "processing/plot_spatial.py",
]

for script in SCRIPTS:
    p = Path(script)
    if not p.exists():
        print(f"  skip (not found): {script}")
        continue

    content = p.read_text()

    # Replace any ROOT = Path(__file__)... pattern
    new_content = re.sub(
        r"ROOT\s*=\s*Path\(__file__\)[^\n]*\n",
        "",
        content
    )
    # Remove old find_root if present
    new_content = re.sub(
        r"def find_root\(\):.*?ROOT = find_root\(\)\n",
        "",
        new_content,
        flags=re.DOTALL
    )

    # Add correct ROOT after imports block (after last import line)
    lines = new_content.split("\n")
    last_import = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import = i

    insert_at = last_import + 1
    root_lines = ["", CORRECT_ROOT, ""]
    lines = lines[:insert_at] + root_lines + lines[insert_at:]
    new_content = "\n".join(lines)

    p.write_text(new_content)
    print(f"  ✓ {script}")

print("\nDone. ROOT now always resolves to ~/wwi/")
