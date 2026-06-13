# Model Card — EUPEN (Vesdre)

**Station:** 6387 · SPW DGH network  
**River:** Vesdre, km 20  
**Location:** Eupen, Province de Liège  
**Coordinates:** 50.623°N, 6.038°E  
**Last updated:** 2026-06-13

---

## Station Profile

### Hydrological position

EUPEN is the reference station for the upper Vesdre and the most regulated gauging point in the network. Located immediately downstream of the Eupen reservoir (Lac de la Vesdre, 25M m³ capacity), it provides the clearest early signal for Vesdre basin conditions.

```
Gileppe reservoir (13M m³) ──┐
                              ├→ Vesdre headwater
Eupen reservoir (25M m³) ────┘
                    ↓
                  EUPEN
                    ↓
              DOLHAIN → VERVIERS → CHAUDFONTAINE
                    ↓
               ANGLEUR (Vesdre/Ourthe confluence)
```

**Catchment area:** ~380 km²  
**Dominant land cover:** Mixed forest (37%), urban (31% — Eupen city)  
**Mean annual rainfall:** 1,100–1,200 mm

### Physical controls

- **Eupen reservoir** — 25M m³ capacity, operated by SWDE. Actively damps flood peaks and maintains minimum flow during drought. Primary reason for low persistence error.
- **Gileppe reservoir** — additional 13M m³ upstream on the Gileppe tributary, joining just above Eupen gauge
- **Urban catchment** — Eupen city has significant impervious cover, but reservoir regulation dominates the signal

### Hydrological regime

Most predictable station in the network (mean error 1.6cm, P90 3.4cm) due to reservoir regulation. The reservoir operator (SWDE) adjusts releases based on downstream demand and flood risk — creating a partially managed flow regime that is inherently more forecastable than natural catchments.

---

## Model

**Type:** Persistence baseline (no dedicated forecast model)  
**Role:** Secondary upstream signal — `H_eupen_dH6h` appears in Sauheid SHAP (rank 5, ~1%)  
**Note:** Low predictive contribution at Sauheid because Vesdre joins the Ourthe downstream of Sauheid (at Angleur) — Eupen signal arrives at the Meuse, not at Sauheid directly.

---

## Performance (persistence baseline)

| Metric | Value |
|--------|-------|
| Mean absolute error (7d) | **1.6cm** |
| P90 error (7d) | **3.4cm** |
| Sensor reliability | **100/100 (Grade A)** |
| Completeness 7d | 100% |

Lowest error in the network — reservoir regulation makes this station near-deterministic over 24h horizons.

---

## Data

**Source:** SPW KiWIS API, parameter `H`, 5-min resolution  
**Historical coverage:** 2021-06-01 → present (22,452 hourly records — some gaps)  
**Sensor status:** Fully operational ✓

---

## Operational use

### Role in July 2021 flood

The Eupen reservoir partially mitigated the Vesdre flood peak but was overwhelmed by the extreme rainfall (~150mm/48h over the Hautes Fagnes). The reservoir overtopped on July 15 2021, releasing an uncontrolled wave that contributed to the catastrophic flooding of Verviers and Liège. Peak H at Eupen: **3.0m** (gauge-relative).

### Significance for DEME

Eupen demonstrates the limits of infrastructure-based flood control. The reservoir attenuates normal events (mean error 1.6cm) but fails under extremes. Any offshore or coastal monitoring system faces the same dichotomy — infrastructure works within design parameters, fails outside them.

---

*WWI Platform · github.com/Ezhen/wwi-platform*
