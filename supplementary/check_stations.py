"""
WWI Station Catalogue
Lists all stations sorted by river and hydrological position
(upstream → downstream) using known river network topology.
Cross-references historical_liege.db and spw_liege.db.
"""
import sqlite3
from pathlib import Path
from collections import defaultdict

ROOT   = Path(__file__).resolve().parent
DB     = str(ROOT / "export/databases/historical_liege.db")
DB_SPW = str(ROOT / "export/databases/spw_liege.db")

# ── Hydrological order per river ──────────────────────────────────────────────
# Each tuple: (station_no, label, river_km_approx, notes)
# Ordered strictly upstream → downstream
# Sources: SPW station catalogue + IGN topographic maps

HYDRO_ORDER = {

    # ── Salm (→ Amblève at Trois-Ponts) ──────────────────────────────────────
    "Salm": [
        ("9922", "SANKT-VITH",      2,  "headwater"),
        ("9926", "SCHOENBERG",      8,  ""),
        ("6832", "TROIS-PONTS",    18,  "confluence with Amblève"),
    ],

    # ── Amblève (→ Ourthe at Comblain) ────────────────────────────────────────
    "Amblève": [
        ("6529", "MONT-RIGI",       5,  "Hautes Fagnes headwater"),
        ("6732", "STAVELOT",       20,  "below Salm confluence"),
        ("6671", "TARGNON",        30,  ""),
        ("6712", "COO INF.",       35,  "below Coo waterfall"),
        ("6657", "REMOUCHAMPS",    50,  ""),
        ("6621", "MARTINRIVE",     58,  ""),
        ("6753", "LASNENVILLE",    62,  "near Aywaille"),
        ("5904", "COMBLAIN",       68,  "confluence with Ourthe"),
    ],

    # ── Vesdre (→ Ourthe/Meuse at Liège) ─────────────────────────────────────
    "Vesdre": [
        ("6958", "ROBERTVILLE",     5,  "Gileppe/Vesdre headwater"),
        ("6967", "BUTGENBACH",      8,  "reservoir catchment"),
        ("6387", "EUPEN",          20,  "below Eupen reservoir"),
        ("6497", "TERNELL",        28,  ""),
        ("6517", "POLLEUR",        32,  ""),
        ("6526", "BELLEHEID",      36,  ""),
        ("6353", "DOLHAIN",        42,  "below Hoëgne confluence"),
        ("6550", "JALHAY",         45,  "Hoëgne tributary"),
        ("6538", "SPA AERODROME",  30,  "Hoëgne headwater"),
        ("6933", "MALMEDY",        15,  "Warchenne tributary"),
        ("6228", "CHAUDFONTAINE",  68,  "2021 flood epicentre"),
        ("6204", "VERVIERS",       55,  "industrial Vesdre"),
        ("5808", "ANGLEUR",        75,  "near Meuse confluence"),
    ],

    # ── Ourthe (→ Meuse at Liège) ─────────────────────────────────────────────
    "Ourthe": [
        ("6832", "TROIS-PONTS",     0,  "Salm confluence — reference"),
        ("5922", "HAMOIR",         15,  ""),
        ("5921", "TABREUX",        20,  ""),
        ("5904", "COMBLAIN",       25,  "Amblève confluence"),
        ("5896", "CHANXHE",        30,  ""),
        ("6657", "LOUVEIGNE",      38,  "precip gauge"),
        ("5857", "MÉRY",           42,  ""),
        ("5826", "SAUHEID",        48,  "main forecast target"),
        ("5811", "STREUPAS",       52,  ""),
        ("5806", "ANGLEUR",        55,  "near Meuse confluence"),
        ("5804", "ANGLEUR GR BAT.",56,  "navigation gauge"),
    ],

    # ── Meuse (trunk — Liège reach) ───────────────────────────────────────────
    "Meuse": [
        ("7228", "MODAVE",          0,  "Hoyoux tributary area"),
        ("7242", "MOHA",           10,  ""),
        ("7244", "HUCCORGNE",      15,  ""),
        ("7141", "HUY",            20,  "below Hoyoux confluence"),
        ("7132", "AMAY",           30,  ""),
        ("7117", "IVOZ-RAMET",     40,  "navigation lock"),
        ("7133", "NEUVILLE",       48,  "= LIÈGE gauge"),
        ("7102", "LIÈGE CANAL",    50,  "NGF absolute — excluded"),
        ("5451", "VISÉ",           65,  "near Dutch border"),
        ("5757", "LANAYE",         70,  "last Belgian gauge"),
    ],

    # ── Mehaigne (→ Meuse, Hesbaye) ───────────────────────────────────────────
    "Mehaigne": [
        ("5578", "WAREMME",         5,  "headwater Hesbaye"),
        ("5572", "BERGILERS",      15,  ""),
        ("5596", "AWANS",          20,  "near Meuse confluence"),
    ],

    # ── Geer/Jeker (→ Meuse, Hesbaye) ────────────────────────────────────────
    "Geer": [
        ("5649", "BATTICE",         5,  "Herve plateau"),
    ],

    # ── Border/Voer area ──────────────────────────────────────────────────────
    "Border": [
        ("5284", "GEMMENICH",       2,  "Göhl/Geul headwater"),
        ("5291", "KELMIS",          8,  "Voer/Vesdre border"),
    ],
}

# ── Load historical DB ────────────────────────────────────────────────────────
con_h = sqlite3.connect(DB)
hist_stats = {}
for r in con_h.execute("""
    SELECT s.station_no, COUNT(o.id) AS n,
           MIN(o.timestamp) AS t_min, MAX(o.timestamp) AS t_max,
           GROUP_CONCAT(DISTINCT o.parameter) AS params
    FROM stations s JOIN observations o ON s.station_no = o.station_no
    GROUP BY s.station_no
""").fetchall():
    hist_stats[r[0]] = {"n": r[1], "tmin": r[2], "tmax": r[3], "params": r[4]}
con_h.close()

# ── Load SPW live DB ──────────────────────────────────────────────────────────
con_spw = sqlite3.connect(DB_SPW)
spw_stations = {r[0]: r[1] for r in con_spw.execute(
    "SELECT station_no, station_name FROM stations"
).fetchall()}
# Latest H from live DB
latest_H = {}
for r in con_spw.execute("""
    SELECT station_no, level_m FROM t_latest_H
    WHERE level_m IS NOT NULL AND level_m < 10
""").fetchall():
    latest_H[r[0]] = r[1]
con_spw.close()

# ── Print ──────────────────────────────────────────────────────────────────────
print("=" * 90)
print("WWI Station Catalogue — Liège Basin (upstream → downstream)")
print("=" * 90)

DEST = {
    "Salm":     "→ Amblève",
    "Amblève":  "→ Ourthe",
    "Vesdre":   "→ Meuse (via Ourthe)",
    "Ourthe":   "→ Meuse",
    "Meuse":    "trunk river",
    "Mehaigne": "→ Meuse (Hesbaye)",
    "Geer":     "→ Meuse",
    "Border":   "→ various",
}

total_hist = 0
for river, stations in HYDRO_ORDER.items():
    print(f"\n{'─'*90}")
    print(f"  {river.upper()}  {DEST.get(river,'')}")
    print(f"{'─'*90}")
    print(f"  {'Sno':<8} {'Label':<22} {'km':>4}  {'Live H':>7}  "
          f"{'In hist.DB':>10}  {'Params':<15}  {'Date range':<23}  Notes")
    print(f"  {'·'*85}")

    for sno, label, km, notes in stations:
        live_h  = f"{latest_H[sno]:.3f}m" if sno in latest_H else "—"
        in_hist = "✓" if sno in hist_stats else "—"
        if sno in hist_stats:
            hs = hist_stats[sno]
            params    = hs["params"] or ""
            date_range = f"{str(hs['tmin'])[:10]} → {str(hs['tmax'])[:10]}"
            n_str     = f"({hs['n']:,})"
            total_hist += 1
        else:
            params = date_range = n_str = ""

        spw_name = spw_stations.get(sno, "")
        name_str = label if label else spw_name[:22]

        print(f"  {sno:<8} {name_str:<22} {km:>4}  {live_h:>7}  "
              f"{in_hist+' '+n_str:<12}  {params:<15}  {date_range:<23}  {notes}")

print(f"\n{'='*90}")
print(f"Stations in historical_liege.db: {len(hist_stats)}")
print(f"Stations with live H today:      {len(latest_H)}")
print(f"\nStations suitable for Vesdre model (need H + historical data):")
for sno, label, km, notes in HYDRO_ORDER["Vesdre"]:
    if sno in hist_stats:
        hs = hist_stats[sno]
        print(f"  ✓ {sno} {label:<20} {hs['params']:<15} {hs['n']:>8,} records")
