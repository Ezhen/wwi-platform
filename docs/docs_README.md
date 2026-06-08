# docs/

Project documentation for the Wallonia Water Intelligence Platform (WWI).

## Contents

| File | Description |
|------|-------------|
| `WWI_concept.md` | Full project concept — suitable for sharing with collaborators |
| `WWI_ROADMAP.md` | Work packages, next steps, and data gap inventory |
| `poster.png` | Project poster (overview of architecture and July 2021 centrepiece) |

## For visualisation collaborators

If you are building a frontend or interactive app on top of the WWI data, start with `WWI_concept.md`. It describes:

- The five data sources and what each provides
- The derived operational indicators (what the platform computes, not just collects)
- The seven forecast points with coordinates
- The five suggested visualisation layers
- The GeoJSON export specification — what files you receive and at what frequency

### Quick data access

All data is in SQLite databases in `export/databases/`. No server needed — open directly with any SQLite client, DBeaver, or Python:

```python
import sqlite3
con = sqlite3.connect("export/databases/spw_liege.db")

# Current water levels with risk signal
df = pd.read_sql("SELECT * FROM t_flood_context", con)

# Latest groundwater state
df = pd.read_sql("SELECT * FROM t_groundwater_anomaly", con)

# 7-day forecast alert levels
df = pd.read_sql("SELECT * FROM v_forecast_alert", 
                 sqlite3.connect("export/databases/forecast_liege.db"))
```

### Coordinate system
All station coordinates in WGS84 (EPSG:4326) — `lat`/`lon` columns in station tables.  
ERA5 grid points: WGS84, 0.25° resolution, 6×7 grid over Liège basin.  
CORINE: WGS84 polygon centroids.
