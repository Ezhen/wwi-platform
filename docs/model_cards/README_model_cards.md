# WWI Model Cards

Documentation for each station in the WWI monitoring network.
Updated manually as models and data coverage evolve.

## Forecast model stations

| Station | River | Model | Flood NSE | Card |
|---------|-------|-------|-----------|------|
| SAUHEID (5826) | Ourthe, km 48 | RF-deltaH hourly | **0.981 (t+6h)** | [SAUHEID.md](SAUHEID.md) |

## Upstream early-warning stations (persistence baseline)

| Station | River | Mean err | Role | Card |
|---------|-------|----------|------|------|
| STAVELOT (6732) | Amblève, km 20 | 10.3cm | Primary UPSTREAM_RAPID_RISE trigger | [STAVELOT.md](STAVELOT.md) |
| COMBLAIN (5904) | Ourthe, km 25 | 6.6cm | Second most important Sauheid feature | [COMBLAIN.md](COMBLAIN.md) |
| EUPEN (6387) | Vesdre, km 20 | 1.6cm | Vesdre headwater, reservoir-regulated | [EUPEN.md](EUPEN.md) |
| CHAUDFONTAINE (6228) | Vesdre, km 68 | 7.8cm | 2021 disaster reference station | [CHAUDFONTAINE.md](CHAUDFONTAINE.md) |

## Trunk river network

| Station | River | Mean err | Card |
|---------|-------|----------|------|
| HUY (7141) | Meuse moyenne | 3.4cm | [MEUSE_NETWORK.md](MEUSE_NETWORK.md) |
| NEUVILLE (7133) | Meuse inférieure | 9.3cm | [MEUSE_NETWORK.md](MEUSE_NETWORK.md) |
| IVOZ-RAMET (7117) | Meuse (navigation) | — | [MEUSE_NETWORK.md](MEUSE_NETWORK.md) |

## Planned cards

- TROIS-PONTS (6832) — Salm/Amblève confluence
- ROBERTVILLE (6958) — Vesdre headwater, precip-only currently
- MONT-RIGI (6529) — Hautes Fagnes reference, precip-only currently
- ANGLEUR (5806) — Ourthe/Vesdre confluence, near Meuse

## Sensor reliability summary (2026-06-13)

- **69 stations Grade A** (100/100) — fully operational
- **4 stations Grade B** — minor quality flags
- **24 stations Grade C** — no H parameter (precip or Q only)
- **1 station Grade F** — ANGLEUR GR BAT. Av (5804) offline
- **Active alerts:** BELLEHEID (6526) FLATLINE_51h on Hoëgne
