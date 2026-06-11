# Wallonia Water Intelligence Platform (WWI)

> Physics-grounded agentic environmental intelligence for the Liège/Meuse basin.

Real-time multi-source ingestion → materialised indicators → ML forecasting → SHAP explainability → autonomous alert engine → LLM-generated operational bulletins.

Built as a portfolio project demonstrating full-stack environmental data engineering, hydrological modelling, and operational AI decision support.

---

## What it does

Every morning, one command:

```bash
bash update.sh
```

Produces:
- **Live river level forecast** — H at SAUHEID (Ourthe) for t+24h/48h/72h
- **Physics-informed alert** — composite multi-signal diagnosis (LOW_FLOW, RAPID_RISE, FLOOD_EMERGENCY etc.)
- **LLM bulletin** — natural language operational briefing via Claude API
- **Forecast verification** — yesterday's prediction vs today's observed, skill score updated

---

## Architecture

```
Five data streams
       │
       ▼
┌─────────────────────────────────────────┐
│           ingestion/                    │
│  SPW KiWIS API    → spw_liege.db       │
│  ERA5 / CDS API   → era5_liege.db      │
│  Open-Meteo API   → forecast_liege.db  │
│  SPW Piézométrie  → piez_liege.db      │
│  CORINE Land Cover→ corine_liege.db    │
│  SPW Watersheds   → catchments_liege.db│
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│           processing/                   │
│  rebuild_all.py → materialised tables  │
│    t_latest_H / t_latest_Q             │
│    t_antecedent_rain / t_rise_rate     │
│    t_flood_context                     │
└────────────────┬────────────────────────┘
                 │
       ┌─────────┴──────────┐
       ▼                    ▼
┌─────────────┐    ┌────────────────────┐
│   model/    │    │   live_explain.py  │
│ RF-deltaH   │    │ live prediction +  │
│ NSE=0.975   │───▶│ SHAP explainability│
│ (test 2025) │    └────────┬───────────┘
│ NSE=0.670   │             │
│ (flood 2021)│             ▼
└─────────────┘    ┌────────────────────┐
                   │  build_alerts.py   │
                   │ composite alerts:  │
                   │ LOW_FLOW_SUSTAINED │
                   │ RAPID_RISE_WET     │
                   │ FLOOD_EMERGENCY    │
                   │ ANOMALY_RAIN_NO_   │
                   │   RESPONSE         │
                   └────────┬───────────┘
                            │
                            ▼
                   ┌────────────────────┐
                   │  llm_bulletin.py   │
                   │ Claude API →       │
                   │ daily operational  │
                   │ briefing           │
                   └────────────────────┘
```

---

## Data sources

| Source | Variables | Resolution | Update |
|--------|-----------|------------|--------|
| [SPW Hydrométrie](https://hydrometrie.wallonie.be) | H, Q, Precip — 98 stations | 5 min | Live |
| [SPW Piézométrie](https://piezometrie.wallonie.be) | Groundwater — 263 stations | Daily | Live |
| [ERA5 / Copernicus CDS](https://cds.climate.copernicus.eu) | swvl1 (soil moisture), tp | Daily means | 5-day lag |
| [Open-Meteo](https://open-meteo.com) | 7-day forecast precip + soil moisture | Hourly | Live |
| [CORINE Land Cover 2018](https://www.geo.be) | Forest/urban/agri fractions | 100m vector | Static |
| [GLO-30 DEM](https://copernicus-dem-30m.s3.amazonaws.com) | Terrain slope | 30m raster | Static |
| [SPW Géoportail](https://geoservices.wallonie.be) | Official watershed polygons | Vector | Static |

---

## Project structure

```
wwi/
├── update.sh                    ← daily pipeline entry point
│
├── live_explain.py              ← live prediction + SHAP explainability
├── build_alerts.py              ← composite multi-signal alert engine
├── llm_bulletin.py              ← LLM bulletin via Claude API
├── forecast_verification.py     ← daily skill score log
│
├── ingestion/                   ← data collection
│   ├── spw_ingest.py            ← H/Q/Precip from SPW KiWIS API
│   ├── piez_ingest.py           ← groundwater
│   ├── era5_lean_ingest.py      ← ERA5 swvl1 + tp (daily means, ~50MB)
│   ├── era5_2021_ingest.py      ← ERA5 2021 flood period specifically
│   ├── forecast_ingest.py       ← Open-Meteo 7-day forecast
│   └── corine_ingest.py         ← CORINE land cover (one-time)
│
├── processing/                  ← indicators and spatial processing
│   ├── rebuild_all.py           ← materialise indicator tables (~1.3s)
│   ├── download_watersheds.py   ← SPW official watersheds from géoportail
│   ├── assign_watersheds.py     ← station → watershed assignment
│   ├── aggregate_catchment_stats.py ← slope + CORINE per catchment
│   ├── extract_slopes.py        ← per-station slope from GLO-30
│   ├── ndvi_from_corine.py      ← synthetic NDVI time series
│   └── dem_processing.py        ← DEM download + slope raster
│
├── model/                       ← ML training and feature engineering
│   ├── build_features.py        ← v1 feature matrix (1007 days × 59 features)
│   ├── build_features_v2.py     ← v2 + swvl1 + NDVI + CORINE fractions
│   ├── train_model.py           ← RF-deltaH model training + evaluation
│   └── explain_prediction.py    ← SHAP analysis
│
├── visualisation/
│   ├── build_map.py             ← Folium interactive map (5 layers)
│   └── plot_spatial.py          ← Cartopy spatial maps (slope, NDVI, CORINE)
│
├── export/
│   ├── databases/               ← SQLite (git-ignored, ~150MB total)
│   ├── csvs/                    ← feature matrices, predictions, alerts
│   │   └── archive/             ← timestamped daily outputs
│   └── maps/                    ← wwi_spatial_maps.png
│
└── supplementary/               ← one-off scripts, debug, raw data
```

---

## Databases

| Database | Content | Size |
|----------|---------|------|
| `spw_liege.db` | H/Q/Precip — 98 stations, 30-day rolling 5-min | ~50 MB |
| `historical_liege.db` | H/Q/Precip daily+hourly 2021+2023-2025 | ~10 MB |
| `era5_liege.db` | swvl1 daily means 2021-2025, 42 grid points | ~50 MB |
| `catchments_liege.db` | 95 watershed polygons + slope + CORINE fractions | ~5 MB |
| `corine_liege.db` | 3,225 land cover polygons | ~2 MB |
| `forecast_liege.db` | Open-Meteo 7-day, 7 basin points | ~2 MB |
| `piez_liege.db` | Groundwater — 263 stations | ~2 MB |

### Key materialised tables (rebuilt every update in ~1.3s)

```sql
t_latest_H          -- current gauge-relative water level per station
t_latest_Q          -- current discharge per station
t_antecedent_rain   -- 3/7/14-day rainfall accumulation
t_rise_rate         -- ΔH/Δt at 1h/3h/6h + tendency label
t_flood_context     -- unified operational view per station
t_operational_alerts-- active composite alerts with physical reasoning
```

---

## Forecast model

**RF-deltaH** — Random Forest predicting ΔH (change in water level) rather than absolute H.

| Metric | t+1d | t+2d | t+3d |
|--------|------|------|------|
| Test NSE (2025) | **0.975** | 0.876 | 0.820 |
| Test RMSE | 0.043m | 0.095m | 0.115m |
| Flood NSE (Jul 2021) | **0.670** | 0.372 | 0.071 |

**Key finding from feature importance:** upstream network H (Stavelot, Trois-Ponts, Comblain) accounts for 97% of predictive importance — confirming that a well-gauged river network implicitly encodes basin wetness state, land cover response, and antecedent conditions. Adding ERA5 soil moisture, NDVI, and CORINE fractions adds no measurable skill at daily resolution. The model ceiling is the daily timestep; an hourly model is the next development step.

**SHAP explainability** runs live daily, logging which factors are raising/lowering the predicted level.

---

## Alert engine

Six composite multi-signal alert conditions evaluated daily:

| Code | Trigger | Physical meaning |
|------|---------|-----------------|
| `DROUGHT_CRITICAL` | H<0.25m + Q<5 m³/s | Baseflow exhausted |
| `LOW_FLOW_SUSTAINED` | H<0.45m + dry 7d + no forecast relief | Summer abstraction risk |
| `RAPID_RISE_WET_ANTECEDENT` | Fast rise + saturated catchment | July 2021 precursor pattern |
| `RAPID_RISE` | Fast rise, dry antecedent | Standard flood watch |
| `FLOOD_EMERGENCY/ELEVATED/WATCH` | H > 3.5/2.5/1.5m | Operational halt triggers |
| `ANOMALY_RAIN_NO_RESPONSE` | High rain but low H | Physics consistency check |

The `ANOMALY_RAIN_NO_RESPONSE` alert flags physically implausible states — the AI knows when to distrust its own inputs.

---

## Spatial datasets

- **Terrain slope** — GLO-30 DEM (30m), Liège basin mosaic, 10800×10800 pixels
- **NDVI** — synthetic daily time series 2021-2025 from CORINE land cover classes + seasonal sinusoidal model
- **Catchment stats** — slope and CORINE fractions clipped to SPW official watershed polygons

---

## Quick start

```bash
pip install requests pandas pyproj folium cdsapi netCDF4 \
            geopandas shapely rasterio pysheds scikit-learn \
            shap anthropic
```

ERA5: register at [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu) → `~/.cdsapirc`

LLM bulletin: set `ANTHROPIC_API_KEY` environment variable.

```bash
# One-time setup
python ingestion/era5_lean_ingest.py
python ingestion/corine_ingest.py
python processing/download_watersheds.py
python processing/rebuild_all.py
python model/build_features_v2.py
python model/train_model.py

# Daily
bash update.sh
```

---

## Forecast verification

The platform verifies itself daily — yesterday's forecast vs today's observed H, logged to `export/csvs/forecast_verification.csv`. Persistence baseline included for comparison.

---

## Data fusion challenges addressed

| Challenge | Approach |
|-----------|----------|
| Temporal misalignment (5min/hourly/daily/5d-lag) | Explicit resampling, daily feature matrix |
| Spatial misalignment (point/grid/catchment) | ERA5 catchment-weighted by watershed polygon |
| H units (NGF absolute vs gauge-relative) | `Value` returnfields in SPW KiWIS API |
| Missing data during floods | Median imputation per feature, documented |
| Catchment attribution | SPW official watershed polygons from géoportail |

---

## Author

Eugène Ivanov — postdoctoral researcher, University of Liège (MAST team)  
Coupled hydrodynamic-wave-sediment-biogeochemical modelling · Environmental data engineering

*Built from scratch as a working operational intelligence system.  
Architecture scales to any monitored environmental system.*
