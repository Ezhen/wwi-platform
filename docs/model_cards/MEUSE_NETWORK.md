# Model Card — MEUSE NETWORK (Liège reach)

**Stations:** HUY (7141) · IVOZ-RAMET (7117) · NEUVILLE/LIÈGE (7133)  
**River:** Meuse moyenne and inférieure  
**Last updated:** 2026-06-13

---

## Station Profiles

### Network overview

The Meuse stations form the trunk river monitoring backbone. They integrate all Ardennes and Hesbaye contributions and provide the final downstream validation layer for the WWI platform.

```
Ourthe (via SAUHEID) ───┐
Vesdre (via ANGLEUR) ───┤→ MEUSE at LIÈGE → NEUVILLE
Méhaigne (Hesbaye) ─────┤
Direct Meuse runoff ────┘
        ↓
    IVOZ-RAMET (navigation lock)
        ↓
    NEUVILLE (7133) — main Liège gauge
        ↓
    → Netherlands border (Visé, Lanaye)
```

---

## HUY (7141)

**Coordinates:** 50.516°N, 5.234°E · km 20 Meuse moyenne

| Metric | Value |
|--------|-------|
| Mean absolute error (7d) | **3.4cm** |
| P90 error (7d) | ~8cm |
| Sensor reliability | **100/100 (Grade A)** |
| H current (2026-06-13) | 1.23m |

**Physical context:** Below the Hoyoux confluence (Hesbaye groundwater-fed tributary). The Hesbaye plateau contribution gives the Meuse at Huy a significant groundwater baseflow component — stabilising the signal compared to pure Ardennes stations. Low error reflects this mixed regime.

---

## IVOZ-RAMET (7117)

**Coordinates:** 50.561°N, 5.463°E · navigation lock

| Metric | Value |
|--------|-------|
| Sensor reliability | **100/100 (Grade A)** |
| Role | Navigation lock gauge — heavily regulated |

**Physical context:** The Canal Albert and navigation lock system at Ivoz-Ramet introduces artificial regulation — lock operations create discrete H steps. This station is useful for flood monitoring (lock gates closed during high water) but less suitable for natural hydrological modelling.

---

## NEUVILLE / LIÈGE (7133)

**Coordinates:** 50.532°N, 5.309°E · km 48 Meuse inférieure

| Metric | Value |
|--------|-------|
| Mean absolute error (7d) | **9.3cm** |
| P90 error (7d) | ~22cm |
| Sensor reliability | **100/100 (Grade A)** |
| H current (2026-06-13) | 1.64m |

**Physical context:** The main Liège gauge, integrating the full Ardennes and Hesbaye contribution. Highest error of the Meuse stations because it receives all upstream variability simultaneously — Ourthe wave + Vesdre wave + direct Meuse runoff can arrive with different timing, creating complex superposition. Error of 9.3cm during the June 2026 Amblève wave event confirms this.

**Note:** Station 7102 (LIÈGE canal) is excluded from the model — it records NGF absolute elevation, not gauge-relative H, creating reference frame inconsistency. Use 7133 (NEUVILLE) as the Liège reference.

---

## Collective role in WWI platform

The Meuse network provides:
1. **Downstream validation** — verifies that upstream wave predictions (Sauheid t+2h → Liège t+4h) verify correctly
2. **Navigation alert** — H > 3.5m at NEUVILLE triggers operational closures on the Canal Albert
3. **Dutch border monitoring** — LANAYE (5757) is the last Belgian gauge before the Netherlands

### Wave propagation through Meuse network

| Source | Arrival at Liège | Delay from Sauheid |
|--------|-----------------|-------------------|
| Ourthe at Sauheid | ~2h | reference |
| Vesdre at Angleur | ~1h | simultaneous |
| Amblève via Comblain | ~4h | +2h after Sauheid |

---

## Roadmap

The Meuse at NEUVILLE is the next priority forecast target after Sauheid — it serves as the operational flood warning gauge for Liège city. A dedicated RF model using Sauheid H (t+2h lag) + Chaudfontaine H (t+1h lag) as primary features would provide 2–4h advance warning for the urban Liège reach.

---

*WWI Platform · github.com/Ezhen/wwi-platform*
