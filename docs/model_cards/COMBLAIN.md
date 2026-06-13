# Model Card — COMBLAIN-AU-PONT (Ourthe/Amblève confluence)

**Station:** 5904 · SPW DGH network  
**River:** Ourthe moyenne, km 25  
**Location:** Comblain-au-Pont, Province de Liège  
**Coordinates:** 50.486°N, 5.583°E  
**Last updated:** 2026-06-13

---

## Station Profile

### Hydrological position

COMBLAIN sits at one of the most hydraulically complex points in the Liège basin — the confluence of the Amblève and the Ourthe. It integrates the drainage of both major Ardennes tributaries and is the closest upstream station to Sauheid (~6h travel time).

```
Amblève (519 km²) ──┐
                     ├→ COMBLAIN (1,113 km²)
Upper Ourthe (594 km²)┘
                     ↓
              CHANXHE → SAUHEID (~6h travel)
```

**Catchment area:** ~1,113 km²  
**Dominant land cover:** Mixed forest (40%), semi-natural grassland (20%), urban (11%)

### Physical controls

- **Confluence dynamics** — Amblève and Ourthe waves may arrive simultaneously or with offset depending on rainfall distribution. When both peak together, Comblain shows the largest amplification.
- **Canyon topography** — the Ourthe gorge at Comblain concentrates flow rapidly, creating fast-rising but short-duration flood pulses
- **No regulation** — fully natural flow regime on both tributaries above the confluence

### Hydrological regime

Moderate predictability (mean error 6.6cm). More complex than Stavelot alone because it integrates two independent catchments. The confluence effect means that identical rainfall totals can produce very different H responses depending on spatial distribution.

---

## Model

**Type:** Persistence baseline (no dedicated forecast model)  
**Role:** Second most important upstream predictor — `H_comblain_dH3h` = **19% of Sauheid t+6h importance**, `H_comblain_dH1h` = 9%  
**Wave travel time to Sauheid:** ~6h

---

## Performance (persistence baseline)

| Metric | Value |
|--------|-------|
| Mean absolute error (7d) | **6.6cm** |
| P90 error (7d) | ~15cm (estimated) |
| Sensor reliability | **100/100 (Grade A)** |
| Completeness 7d | 100% |

---

## Data

**Source:** SPW KiWIS API, parameter `H`, 5-min resolution  
**Historical coverage:** 2021-06-01 → present (23,602 hourly records)  
**Sensor status:** Fully operational ✓

---

## Operational use

### Role in WWI model

Comblain is the **second most important feature** in the Sauheid hourly model. When `H_comblain_dH3h` is positive and `H_stavelot_dH12h` is also positive, the model generates its strongest flood predictions — both upstream sources converging on Sauheid.

### Reference events

| Event | Peak H | Notes |
|-------|--------|-------|
| July 2021 | **4.0m** | Both Amblève and Ourthe peaked simultaneously — classic compound event |

---

*WWI Platform · github.com/Ezhen/wwi-platform*
