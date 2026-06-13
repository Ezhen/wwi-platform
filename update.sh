#!/bin/bash
# WWI Platform — Daily update. Run from anywhere: bash /path/to/wwi/update.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/update.log"

echo "=============================================" | tee -a "$LOG_FILE"
echo "WWI Update — $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
echo "=============================================" | tee -a "$LOG_FILE"

run() {
    echo "[${1}] ${2}..." | tee -a "$LOG_FILE"
    python "$SCRIPT_DIR/${3}" 2>&1 | tee -a "$LOG_FILE"
    if [ ${PIPESTATUS[0]} -eq 0 ]; then
        echo "      ✓ OK" | tee -a "$LOG_FILE"
    else
        echo "      ✗ FAILED" | tee -a "$LOG_FILE"
    fi
}

run "1/5" "SPW hydrology"       "ingestion/spw_ingest.py"
run "2/5" "Piezometry"          "ingestion/piez_ingest.py"
run "3/5" "Forecast"            "ingestion/forecast_ingest.py"
run "4/5" "Rebuild indicators"  "processing/rebuild_all.py"
run "5/8" "Build map"           "visualisation/build_map.py"
run "6/8" "Sensor reliability"  "sensor_reliability.py"
run "7/8" "Daily prediction"    "live_explain.py"
run "8/8" "Hourly prediction"   "live_explain_hourly.py"

# Alert engine + bulletin (read live state)
echo "[alerts] Running alert engine..." | tee -a "$LOG_FILE"
python "$SCRIPT_DIR/build_alerts.py" 2>&1 | tee -a "$LOG_FILE"
echo "[bulletin] Generating LLM bulletin..." | tee -a "$LOG_FILE"
python "$SCRIPT_DIR/llm_bulletin.py" 2>&1 | tee -a "$LOG_FILE"
echo "[verify] Forecast verification..." | tee -a "$LOG_FILE"
python "$SCRIPT_DIR/forecast_verification.py" 2>&1 | tee -a "$LOG_FILE"

echo "✓ Done — $(date '+%H:%M:%S')" | tee -a "$LOG_FILE"
