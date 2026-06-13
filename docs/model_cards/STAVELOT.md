# Model Card — STAVELOT (Amblève)

**Station:** 6732 · SPW DGH network  
**River:** Amblève, km 20  
**Location:** Stavelot, Province de Liège  
**Coordinates:** 50.378°N, 5.885°E  
**Last updated:** 2026-06-13

---

## Station Profile

### Hydrological position

STAVELOT is the primary upstream early-warning station for the WWI platform. Located on the Amblève just below the confluence with the Salm, it integrates the drainage of the high Ardennes including the Hautes Fagnes plateau — the highest-rainfall zone in Belgium (~1,400 mm/year).

```
Salm (237 km²) ──→ TROIS-PONTS
                         ↓
Amblève headwater ──→ STAVELOT (519 km²)
                         ↓
                     TARGNON → REMOUCHAMPS
                         ↓
                     COMBLAIN → SAUHEID (~12h travel)
```

**Catchment area:** ~519 km²  
**Dominant land cover:** Dense mixed forest (53%), semi-natural grassland (33%)  
**Mean annual rainfall:** 1,100–1,400 mm

### Physical controls

- **No upstream reservoir** — fully unregulated, direct rainfall-to-runoff response
- **Salm confluence just upstream** — integrates two independent sub-basins simultaneously
- **Steep Ardennes terrain** — slope ~7–9° watershed average, fast runoff generation (3–6h)
- **Hautes Fagnes plateau** — saturated peat soils act as natural sponge; when saturated, near-instantaneous runoff response

### Hydrological regime

Most flashy station in the network. Highest persistence error (mean 10.3cm, P90 24.7cm) — reflecting genuine physical unpredictability, not a data quality issue. A RISING_FAST event at Stavelot is the primary trigger for `UPSTREAM_RAPID_RISE` alerts at Sauheid.

---

## Model

**Type:** Persistence baseline (no dedicated forecast model)  
**Role:** Upstream early-warning station — H and dH fed as features into the Sauheid RF model  
**Key feature contribution:** `H_stavelot_dH12h` = **53% of Sauheid t+6h importance**

A dedicated RF model for Stavelot is planned as the second deployment target (see Roadmap).

---

## Performance (persistence baseline)

| Metric | Value |
|--------|-------|
| Mean absolute error (7d) | **10.3cm** |
| P90 error (7d) | **24.7cm** |
| Sensor reliability | **100/100 (Grade A)** |
| Completeness 7d | 100% |

High error reflects the physical reality of an unregulated flashy Ardennes catchment — not a model failure.

---

## Data

**Source:** SPW KiWIS API, parameter `H`, 5-min resolution  
**Historical coverage:** 2021-06-01 → present (24,242 hourly records)  
**Sensor status:** Fully operational ✓

---

## Operational use

### Role in WWI alert engine

- **`UPSTREAM_RAPID_RISE`** fires when `tendency = RISING_FAST` (dH/6h > 0.05m)
- Wave travel time to Sauheid: **~12h**
- Wave travel time to Liège (Meuse): **~16h**

### Why Stavelot is red on the reliability map

Mean error 10.3cm vs Eupen 1.6cm — three physical reasons:
1. No reservoir regulation (Eupen has a 25M m³ reservoir)
2. Salm confluence doubles the catchment response complexity
3. Steep forested terrain generates rapid, concentrated runoff

### Reference events

| Event | Peak H | Response time |
|-------|--------|--------------|
| July 2021 | **3.5m** | Peak ~6h before Sauheid peak |
| June 2026 | 1.21m | +0.214m/6h → UPSTREAM_RAPID_RISE alert fired |

---

*WWI Platform · github.com/Ezhen/wwi-platform*
