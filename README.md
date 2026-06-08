# Wallonia Water Intelligence Platform (WWI)

> From rainfall to rivers to groundwater — a unified operational view of the water cycle.

Five independent data streams → one environmental intelligence system → operational decisions.

Built as a portfolio project demonstrating environmental data engineering, hydrological analysis, and operational decision support for the Liège/Meuse basin in Wallonia, Belgium.

---

## What it does

The platform ingests, fuses, and analyses data from five official sources to answer one question:

**"What is the water situation in the Liège basin right now, and what should I expect in the next 72 hours?"**

This is the question water utilities (SWDE, INASEP), river managers (SPW), and environmental consultancies ask every morning. Currently they check five separate portals manually. This platform fuses them into one.

---

## Data sources

| # | Source | Variables | Resolution | Update |
|---|--------|-----------|------------|--------|
| 1 | [SPW Hydrométrie](https://hydrometrie.wallonie.be) | River levels (H), discharge (Q), precipitation | 5 minutes | Live |
| 2 | [SPW Piézométrie](https://piezometrie.wallonie.be) | Groundwater depth + piezometric level | Daily | Live |
| 3 | [ERA5 / Copernicus](https://cds.climate.copernicus.eu) | Rainfall, temperature, soil moisture | Hourly, 0.25° | 5-day lag |
| 4 | [Open-Meteo](https://open-meteo.com) | 7-day forecast: precip, temp, soil moisture | Hourly | Live |
| 5 | [CORINE Land Cover](https://www.geo.be) | Land use — forests, urban, agriculture, wetlands | 100m vector | Static 2018 |

---

## Project structure

```
wwi/
├── update.sh                    ← daily update — run this
│
├── ingestion/                   ← data collection
│   ├── spw_ingest.py            ← H, Q, Precip from SPW KiWIS API
│   ├── piez_ingest.py           ← groundwater from SPW piézométrie
│   ├── era5_ingest.py           ← ERA5 reanalysis from CDS API
│   ├── corine_ingest.py         ← CORINE land cover (one-time)
│   └── forecast_ingest.py       ← Open-Meteo 7-day forecast
│
├── processing/                  ← indicators and transformations
│   ├── rebuild_all.py           ← materialise indicator tables
│   └── add_coords.py            ← Lambert 72 → WGS84 conversion
│
├── visualisation/               ← outputs
│   └── build_map.py             ← Folium interactive map → wwi_map.html
│
├── discovery/                   ← API exploration scripts
│   ├── spw_discover.py
│   └── coords_check.py
│
├── export/
│   ├── databases/               ← SQLite databases (see below)
│   ├── csvs/                    ← sample CSV exports
│   ├── jsons/                   ← station catalogue JSONs
│   └── netcdfs/                 ← ERA5 raw download
│
└── docs/
    ├── WWI_concept.md           ← full project concept
    ├── WWI_ROADMAP.md           ← work packages and next steps
    └── poster.png               ← project poster
```

---

## Databases

All data lives in SQLite — queryable directly, no server needed.

| Database | Content | Size |
|----------|---------|------|
| `spw_liege.db` | H, Q, Precip — 98 stations, 5-min live | ~40 MB |
| `piez_liege.db` | Groundwater — 263 stations | ~2 MB |
| `era5_liege.db` | Reanalysis grid — 42 points | ~22 MB |
| `corine_liege.db` | Land cover — 3,225 polygons | ~2 MB |
| `forecast_liege.db` | 7-day forecast — 7 basin points | ~2 MB |

### Key tables (after running `rebuild_all.py`)

**spw_liege.db**
- `t_latest_H` — current water level per station
- `t_latest_Q` — current discharge per station
- `t_antecedent_rain` — 3/7/14-day rainfall accumulation per station
- `t_rise_rate` — ΔH/Δt at 1h/3h/6h + tendency label per station
- `t_flood_context` — operational intelligence: level + rise rate + antecedent rain + risk signal

**piez_liege.db**
- `t_latest_groundwater` — current depth per station
- `t_groundwater_anomaly` — current vs mean + anomaly + state label

**forecast_liege.db**
- `v_forecast_accumulation` — 24h/72h/7d totals per basin point
- `v_forecast_alert` — alert levels per point

---

## Quick start

### Requirements

```bash
pip install requests pandas pyproj folium cdsapi netCDF4 geopandas
```

For ERA5: register at [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu) and create `~/.cdsapirc`.

### Daily update

```bash
bash update.sh
```

This runs all ingestion scripts, rebuilds indicators, and regenerates the map. Output logged to `update.log`.

### Individual steps

```bash
python ingestion/spw_ingest.py       # fetch latest SPW data
python ingestion/piez_ingest.py      # fetch latest groundwater
python ingestion/forecast_ingest.py  # fetch latest 7-day forecast
python processing/rebuild_all.py     # rebuild indicator tables
python visualisation/build_map.py    # generate wwi_map.html
```

### One-time setup (first run only)

```bash
python ingestion/era5_ingest.py      # download ERA5 reanalysis
python ingestion/corine_ingest.py    # download CORINE land cover
```

---

## The map

`build_map.py` generates `wwi_map.html` — a self-contained interactive map with five layers:

- **River levels** — circles coloured by tendency (red=rising fast → blue=stable → green=falling)
- **Precipitation stations** — coloured by 7-day accumulation
- **Groundwater** — diamonds coloured by anomaly state
- **Forecast points** — alert level per basin point
- **ERA5 rainfall heatmap** — 7-day accumulated precipitation background

Click any station for a full popup with current state and derived indicators.

---

## The centrepiece: July 2021 flood retrospective

On 14–16 July 2021, the Vesdre and Meuse valleys experienced the worst flooding in recorded Belgian history. 42 people died in Liège province alone.

The platform will reconstruct that event using ERA5 reanalysis + SPW historical data, showing what the integrated picture would have looked like 48 hours before the peak.

**Status:** ERA5 historical download pending. SPW historical data request submitted.

---

## Data fusion challenges

This project explicitly addresses six real-world data fusion problems:

| Problem | Impact | Approach |
|---------|--------|----------|
| Temporal misalignment | 5-min / hourly / daily / 5-day-lag sources | Explicit resampling to common grid |
| Spatial misalignment | Point gauges vs 0.25° grid vs catchment | Catchment-weighted ERA5 (WP2) |
| Quality code heterogeneity | SPW codes 200/40/255 vs ERA5 (none) | Quality filter + uncertainty flag |
| Reference frame inconsistency | Relative datum vs NGF vs accumulation | Documented per source |
| Missing catchment attribution | Naive basin averaging | DEM + flow accumulation (planned) |
| Missing data during floods | Sensors offline when needed most | Missingness map in quality dashboard |

---

## Roadmap

- **WP1** — Temporal alignment study (rainfall→river cross-correlation, ~6h Ourthe lag)
- **WP2** — Catchment-aware rainfall aggregation
- **WP3** — July 2021 flood retrospective reconstruction
- **WP4** — Data quality dashboard
- **WP5** — Simple river level forecast model (persistence → regression → Random Forest)
- **WP6** — Collaborative interactive visualisation app

See [docs/WWI_ROADMAP.md](docs/WWI_ROADMAP.md) for details.

---

## Target audience

SWDE · INASEP · SPW · Aquawal · Antea Group · environmental consultancies · researchers

---

## Data sources & attribution

- SPW Wallonie — [hydrometrie.wallonie.be](https://hydrometrie.wallonie.be) / [piezometrie.wallonie.be](https://piezometrie.wallonie.be)
- Copernicus Climate Data Store — ERA5 reanalysis
- Open-Meteo — [open-meteo.com](https://open-meteo.com) (CC BY 4.0)
- CORINE Land Cover 2018 — NGI Belgium / [geo.be](https://www.geo.be)

---

## Author

Eugène Ivanov — postdoctoral researcher, University of Liège (MAST team)  
Coupled hydrodynamic-wave-sediment-biogeochemical modelling · Environmental data engineering

*This project is part of an active portfolio. Contributions and feedback welcome.*
