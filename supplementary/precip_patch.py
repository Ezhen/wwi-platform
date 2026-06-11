"""
Patch spw_ingest.py to handle precipitation correctly.
Removes Precip from PARAMETER_GROUPS and adds a dedicated precip section.
"""
from pathlib import Path

PRECIP_STATIONS = [
    ("5284",  "DGH/5284/Precip/5m.CmdTotal.P"),
    ("5578",  "DGH/5578/Precip/5m.CmdTotal.P"),
    ("5596",  "DGH/5596/Precip/5m.CmdTotal.P"),
    ("5649",  "DGH/5649/Precip/5m.CmdTotal.P"),
    ("5757",  "DGH/5757/Precip/5m.CmdTotal.P"),
    ("6497",  "DGH/6497/Precip/5m.CmdTotal.P"),
    ("6529",  "DGH/6529/Precip/5m.CmdTotal.P"),
    ("6538",  "DGH/6538/Precip/5m.CmdTotal.P"),
    ("6550",  "DGH/6550/Precip/5m.CmdTotal.P"),
    ("6657",  "DGH/6657/Precip/5m.CmdTotal.P"),
    ("6712",  "DGH/6712/Precip/5m.CmdTotal.P"),
    ("6718",  "DGH/6718/Precip/5m.CmdTotal.P"),
    ("6958",  "DGH/6958/Precip/5m.CmdTotal.P"),
    ("6967",  "DGH/6967/Precip/5m.CmdTotal.P"),
    ("7003",  "DGH/7003/Precip/5m.CmdTotal.P"),
    ("7016",  "DGH/7016/Precip/5m.CmdTotal.P"),
    ("7228",  "DGH/7228/Precip/5m.CmdTotal.P"),
    ("9915",  "DGH/9915/Precip/5m.CmdTotal.P"),
    ("9922",  "DGH/9922/Precip/5m.CmdTotal.P"),
]

p = Path("ingestion/spw_ingest.py")
content = p.read_text()

# Step 1: Remove Precip from PARAMETER_GROUPS
old_groups = '''"Q":      "1962340",   # Discharge     — confirmed
    "Precip": "1962475",   # Precipitation — confirmed'''
new_groups = '''"Q":      "1962340",   # Discharge     — confirmed
    # Precip removed — uses direct ts_path ingestion below (group ID was wrong)'''

if old_groups in content:
    content = content.replace(old_groups, new_groups)
    print("✓ Removed Precip from PARAMETER_GROUPS")
else:
    print("✗ PARAMETER_GROUPS pattern not found — check manually")

# Step 2: Add precip section before final summary
precip_section = '''
    # ── Precipitation — direct ts_path ingestion ─────────────────────────────
    # 19 confirmed stations with 5-min production totals in 0.1mm units
    PRECIP_STATIONS = ''' + repr(PRECIP_STATIONS) + '''

    log.info(f"\\n{'─'*55}")
    log.info(f"Parameter: Precip (direct ts_path, 19 stations)")
    log.info(f"{'─'*55}")

    for sno, ts_path in PRECIP_STATIONS:
        ts_id = f"{sno}_Precip_5m"

        # Ensure station and timeseries are registered
        upsert_stations(con, stations_df[stations_df.station_no.astype(str) == sno]
                        if len(stations_df[stations_df.station_no.astype(str) == sno]) > 0
                        else stations_df.head(0))
        upsert_timeseries(con, sno, "Precip", ts_path, "mm", ts_id)

        log.info(f"  [Precip] {sno:<10}  {ts_path}")
        try:
            df_obs, _ = fetch_observations(ts_path, days=FETCH_DAYS)
            if df_obs.empty:
                log.warning("    → no data"); total_skip += 1
            else:
                # Convert 0.1mm → mm
                df_obs["value"] = df_obs["value"].apply(
                    lambda x: round(x / 10.0, 3) if x is not None else None
                )
                n = insert_observations(con, ts_id, sno, "Precip", df_obs)
                mark_fetched(con, ts_id)
                log.info(f"    → {len(df_obs)} records, {n} inserted")
                total_ok += 1
        except requests.HTTPError as e:
            log.error(f"    → HTTP {e.response.status_code}"); total_fail += 1
        except Exception as e:
            log.error(f"    → {e}"); total_fail += 1
        time.sleep(PAUSE)

'''

# Insert before the summary block
old_summary = '''    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("=" * 55)'''

if old_summary in content:
    content = content.replace(old_summary, precip_section + old_summary)
    print("✓ Added precip section before summary")
else:
    print("✗ Summary block not found — appending precip section at end of main")
    # Fallback: append before con.close()
    content = content.replace(
        "    log.info(f\"Done. OK={total_ok}",
        precip_section + "    log.info(f\"Done. OK={total_ok}"
    )

p.write_text(content)
print(f"\n✓ ingestion/spw_ingest.py updated")
print("  Precip now uses direct ts_path with ÷10 unit correction")
print("  Run: python ingestion/spw_ingest.py")
