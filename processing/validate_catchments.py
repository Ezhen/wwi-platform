"""
WWI Catchment Validation
Reads catchments_liege.db and cross-checks against:
1. SPW CATCHMENT_SIZE reference values
2. Known literature values for Meuse sub-basins
3. Nested catchment logic (downstream > upstream)
No reprocessing needed.
"""

import sqlite3
import json
from pathlib import Path

ROOT     = Path(__file__).parent.parent
DB_CATCH = str(ROOT / "export/databases/catchments_liege.db")
DB_SPW   = str(ROOT / "export/databases/spw_liege.db")

# Reference catchment areas from literature (km²)
# Sources: SPW annual reports, EEA Meuse basin assessment
REFERENCE_AREAS = {
    "6387":  ("EUPEN",         360,    50),   # Vesdre at Eupen
    "6228":  ("CHAUDFONTAINE", 686,    80),   # Vesdre at Chaudfontaine
    "5904":  ("COMBLAIN",      3600,   400),  # Ourthe at Comblain
    "5826":  ("SAUHEID",       3600,   400),  # Ourthe at Sauheid (~same as Comblain)
    "6732":  ("STAVELOT",      570,    70),   # Amblève at Stavelot
    "6832":  ("TROIS-PONTS",   170,    30),   # Salm at Trois-Ponts
    "7141":  ("HUY",           12000,  2000), # Meuse at Huy
    "7133":  ("LIEGE",         21000,  3000), # Meuse at Liège
    "6657":  ("LOUVEIGNE",     None,   None), # Precip gauge — no reference
    "6958":  ("ROBERTVILLE",   180,    40),   # Vesdre headwater
    "6529":  ("MONT-RIGI",     None,   None), # Precip gauge — no reference
}

# Nested catchment logic — upstream station_no list per station
# downstream catchment MUST be larger than all upstream
NESTING = {
    "7133":  ["5826", "6228", "6732", "6832"],  # LIEGE > SAUHEID, CHAUDF, STAVELOT, TROIS-PONTS
    "7141":  ["5826", "6228", "6732", "6832"],  # HUY > same
    "5826":  ["5904"],                           # SAUHEID > COMBLAIN (same river)
    "6228":  ["6387"],                           # CHAUDFONTAINE > EUPEN
    "6732":  ["6832"],                           # STAVELOT > TROIS-PONTS (Salm joins Amblève)
}

print("=" * 65)
print("WWI Catchment Validation Report")
print("=" * 65)

# ── Load computed catchments ──────────────────────────────────────────────────
if not Path(DB_CATCH).exists():
    print(f"✗ {DB_CATCH} not found — run dem_processing.py first")
    exit(1)

con = sqlite3.connect(DB_CATCH)
rows = con.execute("""
    SELECT station_no, label, river, area_km2, mean_slope_deg,
           n_cells, era5_weights
    FROM catchments ORDER BY area_km2
""").fetchall()
con.close()

if not rows:
    print("✗ No catchments in DB — dem_processing.py may have failed")
    exit(1)

catchments = {r[0]: {
    "label": r[1], "river": r[2], "area_km2": r[3],
    "mean_slope_deg": r[4], "n_cells": r[5],
    "era5_weights": json.loads(r[6]) if r[6] else {}
} for r in rows}

print(f"\nComputed catchments: {len(catchments)}")

# ── 1. Area comparison ────────────────────────────────────────────────────────
print("\n── 1. Area validation ──────────────────────────────────────────")
print(f"{'Station':<8} {'Label':<20} {'River':<12} {'Computed':>10} "
      f"{'Reference':>10} {'Error%':>8} {'Status'}")
print("-" * 75)

area_issues = []
for sno, (label, ref_area, tolerance) in REFERENCE_AREAS.items():
    if sno not in catchments:
        print(f"  {sno:<8} {label:<20} {'':12} {'MISSING':>10}")
        continue

    c = catchments[sno]
    computed = c["area_km2"]

    if ref_area is None:
        print(f"  {sno:<8} {label:<20} {c['river']:<12} "
              f"{computed:>10.0f} {'N/A':>10} {'N/A':>8}  (precip gauge)")
        continue

    if computed is None:
        print(f"  {sno:<8} {label:<20} {c['river']:<12} "
              f"{'FAILED':>10} {ref_area:>10.0f} {'N/A':>8}  ✗ delineation failed")
        area_issues.append(sno)
        continue

    error_pct = abs(computed - ref_area) / ref_area * 100
    if error_pct <= 30:
        status = "✓ GOOD"
    elif error_pct <= 60:
        status = "~ FAIR"
    else:
        status = "✗ POOR"
        area_issues.append(sno)

    print(f"  {sno:<8} {label:<20} {c['river']:<12} "
          f"{computed:>10.0f} {ref_area:>10.0f} {error_pct:>7.1f}%  {status}")

# ── 2. Nesting logic ──────────────────────────────────────────────────────────
print("\n── 2. Nested catchment logic ───────────────────────────────────")
nesting_issues = []
for downstream_sno, upstream_snos in NESTING.items():
    if downstream_sno not in catchments: continue
    d_area = catchments[downstream_sno]["area_km2"] or 0
    d_label = catchments[downstream_sno]["label"]

    for up_sno in upstream_snos:
        if up_sno not in catchments: continue
        u_area = catchments[up_sno]["area_km2"] or 0
        u_label = catchments[up_sno]["label"]

        if d_area > u_area:
            print(f"  ✓ {d_label:<20} ({d_area:>7.0f} km²) > "
                  f"{u_label:<20} ({u_area:>7.0f} km²)")
        else:
            print(f"  ✗ {d_label:<20} ({d_area:>7.0f} km²) < "
                  f"{u_label:<20} ({u_area:>7.0f} km²)  ← WRONG")
            nesting_issues.append(f"{d_label} < {u_label}")

# ── 3. Slope sanity check ─────────────────────────────────────────────────────
print("\n── 3. Slope sanity check ───────────────────────────────────────")
print(f"  {'Label':<20} {'River':<12} {'Slope':>8}  Assessment")
for sno, c in sorted(catchments.items(),
                      key=lambda x: x[1].get("mean_slope_deg") or 0,
                      reverse=True):
    slope = c.get("mean_slope_deg")
    if slope is None: continue
    if slope > 15:    assessment = "✓ Ardennes headwater (expected)"
    elif slope > 5:   assessment = "✓ Hillslope terrain (expected)"
    elif slope > 1:   assessment = "✓ Valley terrain (expected)"
    elif slope > 0:   assessment = "~ Very flat — check delineation"
    else:             assessment = "✗ Zero slope — likely error"
    print(f"  {c['label']:<20} {c['river']:<12} {slope:>7.1f}°  {assessment}")

# ── 4. ERA5 coverage check ────────────────────────────────────────────────────
print("\n── 4. ERA5 grid coverage ───────────────────────────────────────")
for sno, c in sorted(catchments.items(),
                      key=lambda x: x[1].get("area_km2") or 0):
    n_era5 = len(c.get("era5_weights", {}))
    area   = c.get("area_km2", 0) or 0
    # ERA5 cells are ~25x25km = 625 km² each
    expected_min = max(1, int(area / 1000))
    status = "✓" if n_era5 >= expected_min else "~"
    print(f"  {status} {c['label']:<20} area={area:>7.0f} km²  "
          f"ERA5 cells={n_era5:>3}  (expected ≥{expected_min})")

# ── 5. Cross-check with SPW CATCHMENT_SIZE ───────────────────────────────────
print("\n── 5. SPW CATCHMENT_SIZE cross-check ───────────────────────────")
if Path(DB_SPW).exists():
    con_spw = sqlite3.connect(DB_SPW)
    spw_sizes = {r[0]: r[1] for r in con_spw.execute(
        "SELECT station_no, catchment_km2 FROM stations "
        "WHERE catchment_km2 IS NOT NULL"
    ).fetchall()}
    con_spw.close()

    if spw_sizes:
        for sno, spw_area in sorted(spw_sizes.items(),
                                     key=lambda x: x[1] or 0):
            if sno not in catchments: continue
            computed = catchments[sno].get("area_km2")
            if computed is None: continue
            err = abs(computed - spw_area) / spw_area * 100 if spw_area else None
            status = "✓" if err and err < 30 else "~" if err and err < 60 else "✗"
            print(f"  {status} {catchments[sno]['label']:<20} "
                  f"DEM={computed:>7.0f} km²  SPW={spw_area:>7.0f} km²  "
                  f"err={err:.0f}%" if err else
                  f"  {catchments[sno]['label']:<20} DEM={computed:>7.0f} km²  "
                  f"SPW={spw_area:>7.0f} km²")
    else:
        print("  No CATCHMENT_SIZE values in SPW DB")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 65)
print("SUMMARY")
print(f"  Total catchments:    {len(catchments)}")
print(f"  Area issues:         {len(area_issues)} stations → {area_issues}")
print(f"  Nesting violations:  {len(nesting_issues)}")
if not area_issues and not nesting_issues:
    print("  ✓ All checks passed — catchments ready for feature engineering")
else:
    print("  Stations with issues may need manual pour point adjustment")
    print("  Suggestion: increase snap threshold or adjust coordinates")
