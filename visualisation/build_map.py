from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
DB_SPW      = str(ROOT / "export/databases/spw_liege.db")
DB_PIEZ     = str(ROOT / "export/databases/piez_liege.db")
DB_ERA5     = str(ROOT / "export/databases/era5_liege.db")
DB_CORINE   = str(ROOT / "export/databases/corine_liege.db")
DB_FORECAST = str(ROOT / "export/databases/forecast_liege.db")

"""
Wallonia Water Intelligence Platform — Interactive Map
Builds a self-contained HTML map from all platform databases.
"""

import sqlite3
import folium
from folium.plugins import MarkerCluster, HeatMap
import json
import math
from datetime import datetime




# ── Helpers ───────────────────────────────────────────────────────────────────

def tendency_color(tendency):
    return {
        "RISING_FAST":  "#d32f2f",  # red
        "RISING":       "#f57c00",  # orange
        "STABLE":       "#1976d2",  # blue
        "FALLING":      "#388e3c",  # green
        "FALLING_FAST": "#1b5e20",  # dark green
    }.get(tendency, "#757575")

def gw_state_color(state):
    return {
        "VERY_LOW":  "#b71c1c",
        "LOW":       "#ef6c00",
        "NORMAL":    "#2e7d32",
        "HIGH":      "#0277bd",
        "VERY_HIGH": "#01579b",
    }.get(state, "#757575")

def alert_color(alert):
    return {
        "HIGH":     "#b71c1c",
        "MODERATE": "#e65100",
        "LOW":      "#f9a825",
        "NONE":     "#2e7d32",
    }.get(alert, "#757575")

def safe(v, fmt=".3f"):
    if v is None: return "N/A"
    try: return format(float(v), fmt)
    except: return str(v)


# ── Load data from databases ──────────────────────────────────────────────────

def load_spw():
    if not Path(DB_SPW).exists(): return [], []
    con = sqlite3.connect(DB_SPW)
    con.row_factory = sqlite3.Row

    # River stations with rise rate
    rivers = con.execute("""
        SELECT station_no, station_name, river_name, basin,
               level_m, timestamp,
               delta_1h_m, delta_3h_m, tendency,
               basin_rain_7d_mm, risk_signal,
               lat, lon
        FROM t_flood_context
        WHERE lat IS NOT NULL AND lat != 0
          AND level_m IS NOT NULL
    """).fetchall()

    # Discharge — join to t_flood_context for station metadata + coords
    discharge = con.execute("""
        SELECT q.station_no, f.station_name, f.river_name,
               q.discharge_m3s, q.timestamp,
               f.lat, f.lon
        FROM t_latest_Q q
        LEFT JOIN t_flood_context f ON q.station_no = f.station_no
        WHERE q.discharge_m3s IS NOT NULL
    """).fetchall()

    con.close()
    return [dict(r) for r in rivers], [dict(r) for r in discharge]


def load_precip_stations():
    if not Path(DB_SPW).exists(): return []
    con = sqlite3.connect(DB_SPW)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT station_no, station_name, river_name, basin,
               rain_3d_mm, rain_7d_mm, rain_14d_mm, lat, lon
        FROM t_antecedent_rain
        WHERE lat IS NOT NULL AND lat != 0
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]


def load_groundwater():
    if not Path(DB_PIEZ).exists(): return []
    con = sqlite3.connect(DB_PIEZ)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT g.station_no, g.station_name, g.aquifer, g.commune, g.province,
               g.current_depth_m, g.mean_depth_m, g.anomaly_m, g.gw_state,
               g.depth_percentile, g.timestamp,
               g.lat, g.lon
        FROM v_groundwater_anomaly g
        WHERE g.lat IS NOT NULL AND g.lat != 0
          AND g.lat BETWEEN 50.15 AND 50.90
          AND g.lon BETWEEN 5.35 AND 6.40
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]


def load_forecast():
    if not Path(DB_FORECAST).exists(): return []
    con = sqlite3.connect(DB_FORECAST)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT a.point_id, a.description, a.lat, a.lon,
               a.precip_24h_mm, a.precip_72h_mm, a.precip_7d_mm,
               acc.temp_max_c, acc.temp_min_c,
               a.alert_24h, a.alert_72h
        FROM v_forecast_alert a
        LEFT JOIN v_forecast_accumulation acc USING(point_id)
    """).fetchall()
    con.close()
    return [dict(r) for r in rows]


def load_era5_heatmap():
    if not Path(DB_ERA5).exists(): return []
    con = sqlite3.connect(DB_ERA5)
    rows = con.execute("""
        SELECT g.lat, g.lon,
               SUM(o.value) * 1000 AS rain_7d_mm
        FROM era5_observations o
        JOIN grid_points g ON o.grid_id = g.id
        WHERE o.variable = 'total_precipitation'
        GROUP BY g.id
    """).fetchall()
    con.close()
    return [[r[0], r[1], r[2]] for r in rows if r[2] and r[2] > 0]


# ── Build map ─────────────────────────────────────────────────────────────────

def build_map():
    # Centre on Liège
    m = folium.Map(
        location=[50.60, 5.70],
        zoom_start=9,
        tiles=None,
    )

    # Base layers
    folium.TileLayer(
        "CartoDB positron",
        name="Light (CartoDB)",
        control=True,
    ).add_to(m)
    folium.TileLayer(
        "OpenStreetMap",
        name="OpenStreetMap",
        control=True,
    ).add_to(m)
    folium.TileLayer(
        "CartoDB dark_matter",
        name="Dark (CartoDB)",
        control=True,
    ).add_to(m)

    # ── ERA5 Rainfall Heatmap ─────────────────────────────────────────────────
    era5_data = load_era5_heatmap()
    if era5_data:
        heatmap_layer = folium.FeatureGroup(name="ERA5 Rainfall (7d heatmap)", show=True)
        HeatMap(
            era5_data,
            radius=12,
            blur=8,
            min_opacity=0.3,
            max_zoom=10,
            gradient={0.2: "#ffffcc", 0.5: "#41b6c4", 0.8: "#225ea8"},
        ).add_to(heatmap_layer)
        heatmap_layer.add_to(m)

    # ── River Stations ────────────────────────────────────────────────────────
    rivers, discharge = load_spw()
    river_layer = folium.FeatureGroup(name="River Levels (H)", show=True)
    discharge_map = {d["station_no"]: d["discharge_m3s"] for d in discharge}

    for st in rivers:
        if not st["lat"]: continue
        color    = tendency_color(st.get("tendency", "STABLE"))
        risk     = st.get("risk_signal", "NORMAL")
        q_val    = discharge_map.get(st["station_no"])
        q_str    = f"{q_val:.2f} m³/s" if q_val else "N/A"

        # Larger circle for elevated risk
        radius = 10 if risk == "ELEVATED" else 8 if risk == "WATCH" else 6

        popup_html = f"""
        <div style='font-family:Arial;min-width:220px'>
          <b style='color:{color}'>{st['station_name']}</b><br>
          <small>{st['river_name']} — {st['basin']}</small><hr style='margin:4px'>
          <table style='font-size:12px;width:100%'>
            <tr><td>Level</td><td><b>{safe(st['level_m'])} m</b></td></tr>
            <tr><td>Discharge</td><td><b>{q_str}</b></td></tr>
            <tr><td>ΔH (1h)</td><td>{safe(st.get('delta_1h_m'), '+.4f')} m</td></tr>
            <tr><td>ΔH (3h)</td><td>{safe(st.get('delta_3h_m'), '+.4f')} m</td></tr>
            <tr><td>Tendency</td><td><b style='color:{color}'>{st.get('tendency','?')}</b></td></tr>
            <tr><td>Rain 7d (basin)</td><td>{safe(st.get('basin_rain_7d_mm'),'.1f')} mm</td></tr>
            <tr><td>Risk</td><td><b>{risk}</b></td></tr>
            <tr><td>Updated</td><td><small>{str(st.get('timestamp',''))[:16]}</small></td></tr>
          </table>
        </div>"""

        folium.CircleMarker(
            location=[st["lat"], st["lon"]],
            radius=radius,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            weight=2,
            popup=folium.Popup(popup_html, max_width=260),
            tooltip=f"H: {safe(st['level_m'])}m | {st.get('tendency','?')} | {st['station_name']}",
        ).add_to(river_layer)

    river_layer.add_to(m)

    # ── Precipitation Stations ────────────────────────────────────────────────
    precip_stations = load_precip_stations()
    precip_layer = folium.FeatureGroup(name="Precipitation Stations", show=False)

    for st in precip_stations:
        if not st["lat"]: continue
        rain7 = st.get("rain_7d_mm") or 0
        # Colour by 7d accumulation
        if rain7 > 50:   color = "#1565c0"
        elif rain7 > 25: color = "#1976d2"
        elif rain7 > 10: color = "#64b5f6"
        else:            color = "#b3e5fc"

        popup_html = f"""
        <div style='font-family:Arial;min-width:200px'>
          <b>🌧 {st['station_name']}</b><br>
          <small>{st['basin']}</small><hr style='margin:4px'>
          <table style='font-size:12px'>
            <tr><td>3-day total</td><td><b>{safe(st.get('rain_3d_mm'),'.1f')} mm</b></td></tr>
            <tr><td>7-day total</td><td><b>{safe(st.get('rain_7d_mm'),'.1f')} mm</b></td></tr>
            <tr><td>14-day total</td><td><b>{safe(st.get('rain_14d_mm'),'.1f')} mm</b></td></tr>
          </table>
        </div>"""

        folium.CircleMarker(
            location=[st["lat"], st["lon"]],
            radius=6,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            weight=1.5,
            popup=folium.Popup(popup_html, max_width=240),
            tooltip=f"Rain 7d: {safe(rain7,'.1f')}mm | {st['station_name']}",
        ).add_to(precip_layer)

    precip_layer.add_to(m)

    # ── Groundwater Stations ──────────────────────────────────────────────────
    gw_data = load_groundwater()
    gw_layer = folium.FeatureGroup(name="Groundwater (Liège)", show=True)

    for st in gw_data:
        if not st["lat"]: continue
        color = gw_state_color(st.get("gw_state", "NORMAL"))
        anom  = st.get("anomaly_m") or 0

        popup_html = f"""
        <div style='font-family:Arial;min-width:220px'>
          <b>💧 {st['station_name']}</b><br>
          <small>{st.get('commune','')} — {st.get('aquifer','')[:40]}</small>
          <hr style='margin:4px'>
          <table style='font-size:12px;width:100%'>
            <tr><td>Depth now</td><td><b>{safe(st.get('current_depth_m'))} m</b></td></tr>
            <tr><td>Mean depth</td><td>{safe(st.get('mean_depth_m'))} m</td></tr>
            <tr><td>Anomaly</td><td><b style='color:{color}'>{safe(anom, '+.3f')} m</b></td></tr>
            <tr><td>State</td><td><b style='color:{color}'>{st.get('gw_state','?')}</b></td></tr>
            <tr><td>Updated</td><td><small>{str(st.get('timestamp',''))[:16]}</small></td></tr>
          </table>
        </div>"""

        folium.RegularPolygonMarker(
            location=[st["lat"], st["lon"]],
            number_of_sides=4,
            radius=7,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.75,
            weight=2,
            rotation=45,
            popup=folium.Popup(popup_html, max_width=260),
            tooltip=f"GW: {safe(st.get('current_depth_m'))}m | {st.get('gw_state','?')} | {st['station_name']}",
        ).add_to(gw_layer)

    gw_layer.add_to(m)

    # ── Forecast Points ───────────────────────────────────────────────────────
    fc_data = load_forecast()
    fc_layer = folium.FeatureGroup(name="Forecast (7-day)", show=True)

    for pt in fc_data:
        color = alert_color(pt.get("alert_24h", "NONE"))
        popup_html = f"""
        <div style='font-family:Arial;min-width:220px'>
          <b>⛅ {pt['description']}</b>
          <hr style='margin:4px'>
          <table style='font-size:12px;width:100%'>
            <tr><td>24h precip</td><td><b>{safe(pt.get('precip_24h_mm'),'.1f')} mm</b></td></tr>
            <tr><td>72h precip</td><td><b>{safe(pt.get('precip_72h_mm'),'.1f')} mm</b></td></tr>
            <tr><td>7d precip</td><td>{safe(pt.get('precip_7d_mm'),'.1f')} mm</td></tr>
            <tr><td>Temp range</td><td>{safe(pt.get('temp_min_c'),'.1f')}–{safe(pt.get('temp_max_c'),'.1f')} °C</td></tr>
            <tr><td>Alert 24h</td><td><b style='color:{color}'>{pt.get('alert_24h','?')}</b></td></tr>
            <tr><td>Alert 72h</td><td><b>{pt.get('alert_72h','?')}</b></td></tr>
          </table>
        </div>"""

        folium.Marker(
            location=[pt["lat"], pt["lon"]],
            icon=folium.DivIcon(
                html=f"""<div style='
                    background:{color};
                    border:2px solid white;
                    border-radius:50%;
                    width:16px;height:16px;
                    display:flex;align-items:center;justify-content:center;
                    font-size:10px;color:white;font-weight:bold;
                    box-shadow:0 1px 3px rgba(0,0,0,0.4)'>⛅</div>""",
                icon_size=(16, 16),
                icon_anchor=(8, 8),
            ),
            popup=folium.Popup(popup_html, max_width=260),
            tooltip=f"24h: {safe(pt.get('precip_24h_mm'),'.1f')}mm | {pt.get('alert_24h')} | {pt['description']}",
        ).add_to(fc_layer)

    fc_layer.add_to(m)

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_html = """
    <div style='position:fixed;bottom:30px;left:30px;z-index:1000;
                background:white;padding:12px 16px;border-radius:8px;
                border:1px solid #ddd;box-shadow:0 2px 8px rgba(0,0,0,0.15);
                font-family:Arial;font-size:12px;min-width:180px'>
      <b style='font-size:13px'>Wallonia Water Platform</b>
      <div style='color:#666;font-size:10px;margin-bottom:8px'>Liège Basin — Live</div>

      <b>River tendency (●)</b><br>
      <span style='color:#d32f2f'>● RISING FAST</span> &nbsp;
      <span style='color:#f57c00'>● RISING</span><br>
      <span style='color:#1976d2'>● STABLE</span> &nbsp;
      <span style='color:#388e3c'>● FALLING</span><br><br>

      <b>Groundwater state (◆)</b><br>
      <span style='color:#b71c1c'>◆ VERY LOW</span> &nbsp;
      <span style='color:#ef6c00'>◆ LOW</span><br>
      <span style='color:#2e7d32'>◆ NORMAL</span> &nbsp;
      <span style='color:#0277bd'>◆ HIGH</span><br><br>

      <b>Forecast alert (⛅)</b><br>
      <span style='color:#b71c1c'>● HIGH</span> &nbsp;
      <span style='color:#e65100'>● MODERATE</span><br>
      <span style='color:#f9a825'>● LOW</span> &nbsp;
      <span style='color:#2e7d32'>● NONE</span><br><br>

      <div style='color:#999;font-size:10px'>
        Data: SPW · DESO · ERA5 · Open-Meteo<br>
        Generated: """ + datetime.now().strftime("%Y-%m-%d %H:%M") + """
      </div>
    </div>"""

    m.get_root().html.add_child(folium.Element(legend_html))

    # Layer control
    folium.LayerControl(collapsed=False).add_to(m)

    return m


if __name__ == "__main__":
    print("Building map...")
    m = build_map()
    out = "wwi_map.html"
    m.save(out)
    size = Path(out).stat().st_size / 1024
    print(f"✓ Saved → {out}  ({size:.0f} KB)")
    print(f"  Open in browser: file://{Path(out).resolve()}")
