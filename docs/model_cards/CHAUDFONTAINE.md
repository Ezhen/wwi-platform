# Model Card — CHAUDFONTAINE (Vesdre)

**Station:** 6228 · SPW DGH network  
**River:** Vesdre, km 68  
**Location:** Chaudfontaine, Province de Liège  
**Coordinates:** 50.589°N, 5.654°E  
**Last updated:** 2026-06-13

---

## Station Profile

### Hydrological position

CHAUDFONTAINE is the last major gauging point on the Vesdre before its confluence with the Ourthe at Angleur. It integrates the full Vesdre catchment including the Hoëgne and Gileppe tributaries, and is the reference station for the 2021 Vesdre flood disaster.

```
Eupen reservoir ──→ EUPEN
Gileppe ──────────→ Vesdre
Hoëgne (Spa) ─────→ DOLHAIN → VERVIERS
                              ↓
                        CHAUDFONTAINE (1,100 km²)
                              ↓
                    ANGLEUR (Vesdre/Ourthe confluence)
```

**Catchment area:** ~1,100 km²  
**Dominant land cover:** Mixed forest (37%), urban (31%), semi-natural (20%)  
**Mean annual rainfall:** 900–1,200 mm

### Physical controls

- **Vesdre gorge** — narrow valley concentrates flow; flood waves propagate rapidly with steep rising limbs
- **Urban corridor** — Verviers, Dison, Chaudfontaine are extensively urbanised, increasing impervious runoff
- **Hoëgne confluence** — Spa/Theux area adds a fast-responding sub-catchment (no reservoir)
- **Partial reservoir regulation** — Eupen reservoir upstream, but Hoëgne is unregulated

### Hydrological regime

Mixed regime: partially regulated by Eupen reservoir but dominated by the unregulated Hoëgne contribution during extreme events. Moderate predictability (mean error 7.8cm). Historical significance: **Chaudfontaine peaked at 6.65m on July 15 2021** — the highest water level ever recorded in Wallonia, causing catastrophic destruction of the Vesdre valley.

---

## Model

**Type:** Persistence baseline (no dedicated forecast model)  
**Role:** Secondary signal in Sauheid model (`H_chaudf` and `H_chaudf_lag1` appear in daily SHAP)  
**Note:** Vesdre joins the Ourthe downstream of Sauheid — Chaudfontaine signal is most relevant for Meuse at Liège forecasting, not Sauheid directly.

---

## Performance (persistence baseline)

| Metric | Value |
|--------|-------|
| Mean absolute error (7d) | **7.8cm** |
| P90 error (7d) | ~18cm (estimated) |
| Sensor reliability | **100/100 (Grade A)** |
| Completeness 7d | 100% |

---

## Data

**Source:** SPW KiWIS API, parameter `H`, 5-min resolution  
**Historical coverage:** 2021-06-01 → present (24,242 hourly records)  
**Sensor status:** Fully operational ✓

---

## Operational significance

### July 2021 — the reference disaster

Chaudfontaine is the epicentre of the July 2021 catastrophe. Key timeline:

| Time | Event |
|------|-------|
| Jul 13 18:00 | Rainfall begins over Hautes Fagnes |
| Jul 14 06:00 | Eupen reservoir approaching capacity |
| Jul 14 18:00 | H rising fast at Chaudfontaine — 1.5m → 3.0m in 6h |
| Jul 15 06:00 | **Peak 6.65m** — bridges destroyed, valley inundated |
| Jul 15 12:00 | Peak propagates to Liège Meuse |

42 people died in Liège province. The Vesdre valley from Verviers to Liège was devastated. Infrastructure loss exceeded €2 billion.

### For DEME interview

Chaudfontaine is the answer to "why does this platform matter?" The 2021 event showed that existing monitoring systems gave insufficient warning time. The WWI hourly model, retroactively applied to 2021, predicted the 4.05m peak at Sauheid with NSE=0.981 at t+6h — demonstrating the operational value of the approach.

---

*WWI Platform · github.com/Ezhen/wwi-platform*
