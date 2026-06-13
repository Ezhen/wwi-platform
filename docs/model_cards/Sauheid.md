# Model Card — SAUHEID (Ourthe inférieure)

**Station:** 5826 · SPW DGH network  
**River:** Ourthe inférieure, km 48  
**Location:** Sauheid, commune de Seraing, Province de Liège  
**Coordinates:** 50.597°N, 5.591°E  
**Last updated:** 2026-06-13

---

## Station Profile

### Hydrological position

SAUHEID is the primary forecast target of the WWI platform. It sits at km 48 on the Ourthe inférieure, 7 km upstream of the Meuse confluence at Liège, and integrates the drainage of virtually the entire Belgian Ardennes:

```
Salm (237 km²) ──┐
                  ├→ Amblève at TROIS-PONTS
Amblève (519 km²)─┘
                        ↓
         Amblève + upper Ourthe → COMBLAIN (1,113 km²)
                        ↓
                     SAUHEID (3,600 km²)
                        ↓
              Vesdre confluence → ANGLEUR
                        ↓
                      MEUSE at Liège
```

**Catchment area:** ~3,600 km²  
**Dominant land cover:** Mixed forest (40%) and semi-natural grassland (30%) in the Ardennes; urban (12%) in the Ourthe valley  
**Mean annual rainfall:** 900–1,400 mm (gradient from Hesbaye to Hautes Fagnes)

### Physical controls

- **No upstream reservoir** on the Ourthe main stem — unregulated flashy response
- **Two major confluences upstream:** Amblève at Comblain (~6h travel time), Salm at Trois-Ponts (~12h)
- **Vesdre joins downstream** at Angleur — not captured in H at Sauheid but visible in Meuse response
- **Wave travel times to Sauheid:** Stavelot ~12h, Comblain ~6h, Eupen ~18h

### Hydrological regime

Ardennes-type: fast response (6–18h rainfall-to-peak), high seasonal variability (0.3–4.5m range), episodic flood events driven by sustained frontal rainfall over the Hautes Fagnes plateau.

---

## Model

### Daily RF-deltaH (operational baseline)

| Parameter | Value |
|-----------|-------|
| Type | Random Forest (300 trees, max_depth=12) |
| Target | ΔH at t+24h, t+48h, t+72h |
| Training period | 2023-01-01 → 2024-12-31 (677 days) |
| Features | 59 (upstream H network, discharge, precipitation lags) |
| Feature matrix | `model/build_features.py` → `features_sauheid.csv` |

### Hourly RF-deltaH (early warning)

| Parameter | Value |
|-----------|-------|
| Type | Random Forest (300 trees, max_depth=12) |
| Target | ΔH at t+6h, t+12h, t+24h |
| Training period | 2023-01-01 → 2024-12-31 (16,269 hours) |
| Features | 109 (upstream H lags, rise rates, precipitation rolling sums) |
| Feature matrix | `model/build_features_hourly.py` → `features_sauheid_hourly.csv` |
| Update frequency | Every 6 hours |

### Dominant predictors (SHAP — hourly t+6h)

| Rank | Feature | Importance | Physical meaning |
|------|---------|------------|-----------------|
| 1 | `H_stavelot_dH12h` | 53% | Amblève 12h rise rate — wave propagation signal |
| 2 | `H_comblain_dH3h` | 19% | Ourthe at Comblain 3h rise rate — imminent arrival |
| 3 | `H_comblain_dH1h` | 9% | Short-term Comblain tendency |
| 4 | `H_stavelot_dH6h` | 2% | Amblève 6h rise rate |
| 5 | `H_eupen_dH6h` | 1% | Vesdre headwater signal |

**Key finding:** upstream network H accounts for 97% of predictive importance at daily resolution. ERA5 soil moisture, NDVI, CORINE land cover fractions, and terrain slope add no measurable skill. Temporal resolution (not feature engineering) was the binding constraint.

---

## Performance

### Daily model

| Metric | t+24h | t+48h | t+72h |
|--------|-------|-------|-------|
| Test NSE (2025) | **0.975** | 0.876 | 0.820 |
| Test RMSE | 0.043m | 0.095m | 0.115m |
| Flood NSE (Jul 2021) | **0.670** | 0.372 | 0.071 |
| Flood RMSE | 0.203m | 0.347m | 0.438m |

### Hourly model

| Metric | t+6h | t+12h | t+24h |
|--------|------|-------|-------|
| Test NSE (2025) | **0.998** | 0.988 | 0.935 |
| Test RMSE | 0.012m | 0.030m | 0.070m |
| Flood NSE (Jul 2021) | **0.981** | 0.878 | 0.603 |
| Flood RMSE | 0.025m | 0.084m | 0.227m |

*Validation: strict temporal split. Flood period (Jun–Sep 2021) fully out-of-sample. Persistence baseline included at all horizons.*

### Error by flow regime (hourly t+6h, flood 2021)

| Regime | Mean absolute error |
|--------|-------------------|
| Low flow (<0.5m) | **0.4cm** |
| Normal (0.5–1.0m) | **1.1cm** |
| Elevated (1.0–1.5m) | **15.3cm** — transition zone, highest error |
| Watch (1.5–2.5m) | **8.1cm** |
| Flood (>2.5m) | **29.6cm** |

98% of all hours during the 2021 flood event had t+6h error < 5cm.

### Known failure modes

- **Rising limb inflection** — error peaks at the steepest point of the hydrograph (Jul 14 2021 ~00:00 UTC). The model lags by 1–2h at the inflection point.
- **Afternoon convective events** — diurnal error pattern: errors peak 15:00–22:00 UTC, likely driven by afternoon convective rainfall over the Ardennes that generates sub-hourly runoff not captured in the hourly feature window.
- **Post-peak recession overestimation** — model slightly overshoots during the falling limb, particularly at t+24h.
- **Extreme extrapolation** — flood NSE degrades sharply at t+72h (daily: 0.071). Not designed for multi-day flood forecasting.

---

## Data

### Primary source

**SPW Hydrométrie** — KiWIS API, parameter `H` (gauge-relative water level), 5-minute resolution, 30-day rolling window in `spw_liege.db`.

### Historical data

**`historical_liege.db`** — hourly H/Q/Precip from SPW archive, coverage:
- 2021-06-01 → 2025-06-08 (includes July 2021 flood)
- 24,242 hourly H records at Sauheid

### Sensor reliability

| Metric | Value |
|--------|-------|
| Current score | **90/100 (Grade A)** |
| Completeness 7d | 100% |
| Last reading | < 1h ago (live) |
| Known issue | FLAT_19h — minor plateau during low-flow stable period, not a sensor fault |

### Reference floods

| Event | Peak H | Date | Model performance |
|-------|--------|------|------------------|
| July 2021 | **4.05m** | 15 Jul 2021 15:00 | t+6h NSE=0.981, RMSE=2.5cm |
| Nov 2023 | ~1.8m | — | within training set |

---

## Limitations

- **Single station target** — model forecasts H at Sauheid only. No simultaneous multi-station prediction.
- **No NWP integration** — current features are observation-based. Rainfall forecast (Open-Meteo) is available but adds no skill at daily resolution. Hourly model is reactive, not anticipatory.
- **Training distribution** — trained on 2023–2024. Extreme events (H > 3m) are extrapolation. NSE=0.670 on July 2021 flood reflects this regime shift.
- **Groundwater not modelled** — 263 piezometers available but not yet integrated. Baseflow contribution unquantified.
- **Single catchment** — does not model Vesdre, Amblève, or Meuse independently. Platform extension to multi-basin is planned.

---

## Operational use

### Alert thresholds (SPW)

| Level | H threshold | Model response |
|-------|------------|---------------|
| Watch | 1.5m | FLOOD_WATCH alert |
| Elevated | 2.5m | FLOOD_ELEVATED alert |
| Emergency | 3.5m | FLOOD_EMERGENCY alert |
| Low flow | < 0.45m | LOW_FLOW_SUSTAINED alert |
| Critical | < 0.25m | DROUGHT_CRITICAL alert |

### Upstream early warning stations

| Station | River | Travel time to Sauheid | Key feature |
|---------|-------|----------------------|-------------|
| STAVELOT (6732) | Amblève | ~12h | `H_stavelot_dH12h` — 53% importance |
| COMBLAIN (5904) | Ourthe | ~6h | `H_comblain_dH3h` — 19% importance |
| EUPEN (6387) | Vesdre | ~18h | secondary signal |
| TROIS-PONTS (6832) | Salm | ~15h | upstream of Stavelot |

### Downstream targets

| Station | River | Travel time from Sauheid |
|---------|-------|------------------------|
| ANGLEUR (5806) | Ourthe | ~2h |
| NEUVILLE (7133) | Meuse | ~4h |
| IVOZ-RAMET (7117) | Meuse | ~3h |

### Uncertainty

90% prediction intervals from RF tree ensemble. Interval width reflects forecast confidence:
- Stable low-flow: spread ~1–3cm (high confidence)
- Rising event: spread ~15–40cm (moderate confidence)
- Flood peak: spread ~30–80cm (low confidence — extrapolation)

---

## Version history

| Version | Date | Change | Flood NSE |
|---------|------|--------|-----------|
| v1.0 | 2026-06-08 | Baseline RF (absolute H) | 0.506 |
| v1.1 | 2026-06-08 | RF delta-H formulation | **0.670** |
| v1.2 | 2026-06-11 | +swvl1 +NDVI +ERA5 2021 | 0.671 |
| v1.3 | 2026-06-11 | +CORINE fractions +slope | 0.670 |
| hourly_v1 | 2026-06-11 | Hourly resolution t+6h | **0.981** |

*Key finding: structural decisions (delta-H formulation, hourly resolution) drove all improvement. Feature engineering at daily resolution added zero measurable skill.*

---

*WWI Platform — Wallonia Water Intelligence · github.com/Ezhen/wwi-platform*
