# Wallonia Water Intelligence Platform (WWI)

> Physics-grounded agentic environmental intelligence for the Liège/Meuse basin.

Real-time multi-source ingestion → materialised indicators → ML forecasting → SHAP explainability → autonomous alert engine → LLM-generated operational bulletins.

Built as a portfolio project demonstrating full-stack environmental data engineering, hydrological modelling, and operational AI decision support for water management in Wallonia, Belgium.

---

## What it does

One command runs the full pipeline:

```bash
bash update.sh          # daily/6-hourly
```

Produces every run:
- **Live river level forecast** — H at SAUHEID (Ourthe) at t+6h/12h/24h/48h/72h
- **Physics-informed upstream alert** — detects wave propagation before it reaches the target station
- **Composite multi-signal alerts** — LOW_FLOW, UPSTREAM_RAPID_RISE, FLOOD_EMERGENCY etc.
- **LLM bulletin** — natural language operational briefing via Claude API
- **Forecast verification** — yesterday's prediction vs today's observed, rolling skill score

---

## Architecture

```
Seven data streams
       │
       ▼
┌──────────────────────────────────────────────┐
│                 ingestion/                   │
│  SPW KiWIS API      → spw_liege.db          │
│  ERA5 / CDS API     → era5_liege.db         │
│  Open-Meteo API     → forecast_liege.db     │
│  SPW Piézométrie    → piez_liege.db         │
│  CORINE Land Cover  → corine_liege.db       │
│  SPW Watersheds     → catchments_liege.db   │
│  GLO-30 DEM         → slope_liege.tif       │
└─────────────────┬────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────┐
│               processing/                   │
│  rebuild_all.py → materialised tables       │
│    t_latest_H / t_latest_Q                 │
│    t_antecedent_rain / t_rise_rate         │
│    t_flood_context / t_operational_alerts  │
└──────────────┬───────────────────────────────┘
               │
       ┌───────┴──────────────────┐
       ▼                          ▼
┌─────────────────┐    ┌─────────────────────────────┐
│    model/       │    │   Daily pipeline            │
│  RF-deltaH      │    │   live_explain.py           │
│  daily:         │    │   → t+24h/48h/72h forecast  │
│  NSE=0.975 test │    │   → SHAP explainability     │
│  NSE=0.670 flood│    └──────────────┬──────────────┘
│                 │                   │
│  RF-deltaH      │    ┌─────────────────────────────┐
│  hourly:        │    │   Hourly pipeline           │
│  NSE=0.998 test │    │   live_explain_hourly.py    │
│  NSE=0.981 flood│    │   → t+6h/12h/24h forecast  │
│  (t+6h)         │    │   → wave propagation SHAP   │
└─────────────────┘    └──────────────┬──────────────┘
                                      │
                       ┌──────────────▼──────────────┐
                       │      build_alerts.py        │
                       │   UPSTREAM_RAPID_RISE       │
                       │   LOW_FLOW_SUSTAINED        │
                       │   RAPID_RISE_WET_ANTECEDENT │
                       │   FLOOD_EMERGENCY           │
                       │   ANOMALY_RAIN_NO_RESPONSE  │
                       └──────────────┬──────────────┘
                                      │
                       ┌──────────────▼──────────────┐
                       │      llm_bulletin.py        │
                       │   Claude API →              │
                       │   operational briefing      │
                       └─────────────────────────────┘
```

---

## Data sources

| Source | Variables | Resolution | Update |
|--------|-----------|------------|--------|
| [SPW Hydrométrie](https://hydrometrie.wallonie.be) | H, Q, Precip — 98 stations | 5 min | Live |
| [SPW Piézométrie](https://piezometrie.wallonie.be) | Groundwater — 263 stations | Daily | Live |
| [ERA5 / Copernicus CDS](https://cds.climate.copernicus.eu) | swvl1 soil moisture, tp | Daily means | 5-day lag |
| [Open-Meteo](https://open-meteo.com) | 7-day forecast precip + soil moisture | Hourly | Live |
| [CORINE Land Cover 2018](https://www.geo.be) | Forest/urban/agri fractions | 100m vector | Static |
| [GLO-30 DEM](https://copernicus-dem-30m.s3.amazonaws.com) | Terrain slope | 30m raster | Static |
| [SPW Géoportail](https://geoservices.wallonie.be) | Official watershed polygons | Vector | Static |

---

## Project structure

```
wwi/
├── update.sh                      ← pipeline entry point (daily/6-hourly)
│
├── live_explain.py                ← daily prediction t+24h/48h/72h + SHAP
├── live_explain_hourly.py         ← hourly prediction t+6h/12h/24h + SHAP
├── build_alerts.py                ← composite multi-signal alert engine
├── llm_bulletin.py                ← LLM bulletin via Claude API
├── forecast_verification.py       ← daily skill score log
│
├── ingestion/                     ← data collection
│   ├── spw_ingest.py              ← H/Q/Precip from SPW KiWIS API
│   ├── piez_ingest.py             ← groundwater
│   ├── era5_lean_ingest.py        ← ERA5 swvl1 + tp (daily means, ~50MB)
│   ├── era5_2021_ingest.py        ← ERA5 2021 flood period
│   ├── forecast_ingest.py         ← Open-Meteo 7-day forecast
│   └── corine_ingest.py           ← CORINE land cover (one-time)
│
├── processing/                    ← indicators and spatial processing
│   ├── rebuild_all.py             ← materialise indicator tables (~1.3s)
│   ├── download_watersheds.py     ← SPW official watersheds from géoportail
│   ├── assign_watersheds.py       ← station → watershed assignment
│   ├── aggregate_catchment_stats.py ← slope + CORINE per catchment
│   ├── extract_slopes.py          ← per-station slope from GLO-30
│   ├── ndvi_from_corine.py        ← synthetic NDVI time series
│   └── dem_processing.py          ← DEM download + slope raster
│
├── model/                         ← ML training and feature engineering
│   ├── build_features.py          ← v1 daily feature matrix (1007d × 59)
│   ├── build_features_v2.py       ← v2 + swvl1 + NDVI + CORINE fractions
│   ├── build_features_hourly.py   ← hourly feature matrix (22k × 109)
│   ├── train_model.py             ← daily RF-deltaH training + evaluation
│   ├── train_model_hourly.py      ← hourly RF-deltaH training + evaluation
│   └── explain_prediction.py      ← SHAP analysis
│
├── visualisation/
│   ├── build_map.py               ← Folium interactive map (5 layers)
│   ├── plot_spatial.py            ← Cartopy spatial maps (slope, NDVI, CORINE)
│   ├── plot_flood_2021.py         ← July 2021 flood retrospective
│   └── plot_model_evolution.py    ← NSE evolution across model versions
│
├── export/
│   ├── databases/                 ← SQLite (git-ignored, ~150MB total)
│   ├── csvs/                      ← feature matrices, predictions, alerts
│   │   └── archive/               ← timestamped daily outputs
│   └── maps/                      ← spatial maps + model evolution plots
│
└── supplementary/                 ← one-off scripts, debug, raw data
```

---

## Databases

| Database | Content | Size |
|----------|---------|------|
| `spw_liege.db` | H/Q/Precip — 98 stations, 30-day rolling 5-min | ~50 MB |
| `historical_liege.db` | H/Q/Precip hourly+daily 2021+2023-2025, 15 stations | ~10 MB |
| `era5_liege.db` | swvl1 daily means 2021-2025, 42 grid points | ~50 MB |
| `catchments_liege.db` | 95 watershed polygons + slope + CORINE fractions | ~5 MB |
| `corine_liege.db` | 3,225 land cover polygons | ~2 MB |
| `forecast_liege.db` | Open-Meteo 7-day, 7 basin points | ~2 MB |
| `piez_liege.db` | Groundwater — 263 stations | ~2 MB |

### Key materialised tables (rebuilt in ~1.3s)

```sql
t_latest_H            -- current gauge-relative water level per station
t_latest_Q            -- current discharge per station
t_antecedent_rain     -- 3/7/14-day rainfall accumulation per station
t_rise_rate           -- ΔH/Δt at 1h/3h/6h + tendency label per station
t_flood_context       -- unified operational view per station
t_operational_alerts  -- active composite alerts with physical reasoning
```

---

## Forecast models

### Daily RF-deltaH (operational baseline)

Predicts ΔH (change in water level) at t+24h/48h/72h from daily features.

| Metric | t+24h | t+48h | t+72h |
|--------|-------|-------|-------|
| Test NSE (2025) | **0.975** | 0.876 | 0.820 |
| Test RMSE | 0.043m | 0.095m | 0.115m |
| Flood NSE (Jul 2021) | **0.670** | 0.372 | 0.071 |

### Hourly RF-deltaH (early warning)

Predicts ΔH at t+6h/12h/24h from live 5-min data resampled to hourly.
Runs every 6 hours via cron.

| Metric | t+6h | t+12h | t+24h |
|--------|------|-------|-------|
| Test NSE (2025) | **0.998** | 0.988 | 0.935 |
| Test RMSE | 0.012m | 0.030m | 0.070m |
| Flood NSE (Jul 2021) | **0.981** | 0.878 | 0.603 |

**Key finding — feature importance:** `H_stavelot_dH12h` (Amblève 12h rise rate) accounts for **53%** of importance at t+6h, `H_comblain_dH3h` for **19%**. The model explicitly encodes the 12-18h wave propagation from the Ardennes headwaters to Sauheid — the physical mechanism the daily model was blind to.

**Model evolution finding:** Two structural decisions drove all improvement. Feature engineering (ERA5 soil moisture, NDVI, CORINE fractions, slope) added zero measurable skill at daily resolution — upstream network H implicitly encodes all basin state information. The ceiling was temporal resolution, not features.

| Version | Change | Flood NSE gain |
|---------|--------|---------------|
| v1.0 → v1.1 | delta-H formulation | +0.164 |
| v1.1 → v1.3 | swvl1 + NDVI + CORINE + slope | +0.000 |
| daily → hourly t+6h | temporal resolution | **+0.311** |

---

## Alert engine

Seven composite multi-signal conditions, evaluated every 6 hours:

| Code | Trigger | Physical meaning |
|------|---------|-----------------|
| `UPSTREAM_RAPID_RISE` | Upstream station RISING_FAST | Wave arriving in 6-18h — pre-emptive watch |
| `DROUGHT_CRITICAL` | H<0.25m + Q<5 m³/s | Baseflow exhausted |
| `LOW_FLOW_SUSTAINED` | H<0.45m + dry 7d + no relief | Summer abstraction risk |
| `RAPID_RISE_WET_ANTECEDENT` | Fast rise + saturated catchment | July 2021 precursor pattern |
| `RAPID_RISE` | Fast rise, dry antecedent | Standard flood watch |
| `FLOOD_EMERGENCY/ELEVATED/WATCH` | H > 3.5/2.5/1.5m | Operational halt triggers |
| `ANOMALY_RAIN_NO_RESPONSE` | High rain but low H | Physics consistency check — data quality flag |

`UPSTREAM_RAPID_RISE` is the key early warning alert — it detects rapid rise at Stavelot/Eupen/Comblain and warns that the wave will reach Sauheid in 6-18h, before any change is visible at the target station.

`ANOMALY_RAIN_NO_RESPONSE` flags physically implausible states — AI that knows when to distrust its own inputs.

---

## July 2021 flood retrospective

The platform was tested against the worst flooding in recorded Belgian history (14-16 July 2021, 42 deaths in Liège province). The hourly model:

- Tracked the rising limb to 4.05m peak with t+6h RMSE = 0.025m
- Correctly crossed Watch (1.5m), Elevated (2.5m), Emergency (3.5m) thresholds
- NSE = 0.981 at t+6h, 0.878 at t+12h

See `export/maps/flood_2021_retrospective.png`.

---

## Spatial datasets

- **Terrain slope** — GLO-30 DEM (30m), Liège basin mosaic, 10800×10800 pixels
- **NDVI** — synthetic daily 2021-2025 from CORINE land cover + seasonal sinusoidal model
- **Catchment statistics** — slope and CORINE fractions clipped to SPW official watershed polygons

See `export/maps/wwi_spatial_maps.png`.

---

## Quick start

### Requirements

```bash
pip install requests pandas pyproj folium cdsapi netCDF4 \
            geopandas shapely rasterio pysheds scikit-learn \
            shap anthropic
```

ERA5: register at [cds.climate.copernicus.eu](https://cds.climate.copernicus.eu) → create `~/.cdsapirc`

LLM bulletin: set `ANTHROPIC_API_KEY` environment variable.

### One-time setup

```bash
python ingestion/era5_lean_ingest.py      # ERA5 soil moisture 2021-2025
python ingestion/corine_ingest.py         # CORINE land cover
python processing/download_watersheds.py  # SPW official watersheds
python processing/rebuild_all.py          # materialise indicator tables
python model/build_features_hourly.py     # hourly feature matrix
python model/train_model_hourly.py        # train hourly RF model
python model/build_features_v2.py         # daily feature matrix
python model/train_model.py               # train daily RF model
```

### Daily / 6-hourly operation

```bash
bash update.sh
```

### Optional: automated scheduling

```bash
# Run every 6 hours via cron
crontab -e
# Add:
0 0,6,12,18 * * * cd ~/wwi && bash update.sh >> update.log 2>&1
```

---

## Forecast verification

The platform verifies itself every run — yesterday's forecast vs today's observed H, logged to `export/csvs/forecast_verification.csv` with persistence baseline for comparison. Hourly forecasts logged to `export/csvs/forecast_log_hourly.csv`.

---

## Data fusion challenges addressed

| Challenge | Approach |
|-----------|----------|
| Temporal misalignment (5min/hourly/daily/5d-lag) | Explicit resampling, separate daily + hourly feature matrices |
| Spatial misalignment (point/grid/catchment) | ERA5 catchment-weighted by watershed polygon |
| H units (NGF absolute vs gauge-relative) | `Value` returnfields in SPW KiWIS API |
| Missing data during floods | Median imputation per feature, documented |
| Catchment attribution | SPW official watershed polygons from géoportail.wallonie.be |
| Model ceiling at daily resolution | Hourly model with explicit wave propagation lags |

---

## Output archive

Every run generates timestamped outputs in `export/csvs/archive/`:

```
alerts_YYYYMMDD_HHMM.json     ← alert state with physical reasoning
bulletin_YYYYMMDD.txt          ← LLM operational bulletin
shap_YYYYMMDD.csv              ← SHAP feature contributions
shap_hourly_YYYYMMDD_HHMM.csv ← hourly SHAP contributions
verification_YYYY-MM-DD.csv   ← forecast skill scores
```

---

## Author

Eugène Ivanov — environmental scientist, University of Liège (MAST team)  
PhD in coupled hydrodynamic-biogeochemical modelling · Environmental data engineering · HPC (NIC5/Lucia)

*Built from scratch as a working operational intelligence system.  
Architecture scales to any monitored environmental system — offshore, coastal, or riverine.*
