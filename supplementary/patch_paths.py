"""
Remove config imports and replace with direct Path(__file__) resolution.
Run from project root.
"""
from pathlib import Path
import re

# DB name → variable name mapping
DB_MAP = {
    "spw_liege.db":      "DB_SPW",
    "piez_liege.db":     "DB_PIEZ",
    "era5_liege.db":     "DB_ERA5",
    "corine_liege.db":   "DB_CORINE",
    "forecast_liege.db": "DB_FORECAST",
}

# Path relative to project root for each DB
DB_PATHS = {
    "DB_SPW":      "export/databases/spw_liege.db",
    "DB_PIEZ":     "export/databases/piez_liege.db",
    "DB_ERA5":     "export/databases/era5_liege.db",
    "DB_CORINE":   "export/databases/corine_liege.db",
    "DB_FORECAST": "export/databases/forecast_liege.db",
}

# Scripts and their subdirectory depth from project root
SCRIPTS = {
    "ingestion/spw_ingest.py":       2,
    "ingestion/piez_ingest.py":      2,
    "ingestion/era5_ingest.py":      2,
    "ingestion/corine_ingest.py":    2,
    "ingestion/forecast_ingest.py":  2,
    "processing/rebuild_all.py":     2,
    "processing/add_coords.py":      2,
    "visualisation/build_map.py":    2,
}

for script, depth in SCRIPTS.items():
    p = Path(script)
    if not p.exists():
        print(f"  skip (not found): {script}")
        continue

    content = p.read_text()

    # Remove all config-related lines
    lines = content.split("\n")
    clean_lines = []
    for line in lines:
        if "from config import" in line: continue
        if "sys.path.insert" in line and "parent" in line: continue
        if "import sys" == line.strip(): continue
        clean_lines.append(line)
    content = "\n".join(clean_lines)

    # Build the path resolution header
    # depth=2 means script is one folder deep, ROOT = Path(__file__).parent.parent
    parents = ".parent" * depth
    root_line = f"ROOT = Path(__file__){parents}"

    # Build DB variable lines — only for DBs actually referenced in this script
    db_lines = []
    for db_name, var_name in DB_MAP.items():
        if db_name in content or var_name in content:
            rel_path = DB_PATHS[var_name]
            db_lines.append(f'{var_name} = str(ROOT / "{rel_path}")')

    if not db_lines:
        print(f"  skip (no DB refs): {script}")
        continue

    header = (
        "from pathlib import Path\n"
        f"{root_line}\n"
        + "\n".join(db_lines)
        + "\n"
    )

    # Replace any remaining string DB references
    for db_name, var_name in DB_MAP.items():
        content = content.replace(f'"{db_name}"', var_name)
        content = content.replace(f"'{db_name}'", var_name)
        # Also fix DB_PATH = "..." style
        content = re.sub(
            rf'DB_PATH\s*=\s*["\'].*{db_name}["\']',
            f"DB_PATH = {var_name}",
            content
        )

    # Remove duplicate Path imports
    content = re.sub(r"^from pathlib import Path\n", "", content, flags=re.MULTILINE)
    content = re.sub(r"^import pathlib\n", "", content, flags=re.MULTILINE)

    # Prepend header
    content = header + content

    p.write_text(content)
    print(f"  ✓ {script}")
    for line in db_lines:
        print(f"      {line}")

print("\nDone — no more config.py needed.")
