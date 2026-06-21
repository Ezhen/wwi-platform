cd ~/wwi

# 1. Update all databases
bash update.sh

# 2. Daily prediction (t+24h/48h/72h)
python live_explain.py

# 3. Hourly prediction (t+6h/12h/24h with uncertainty)
python live_explain_hourly.py

# 4. Alert engine (checks upstream + composite signals)
python build_alerts.py

# 5. LLM bulletin
#python llm_bulletin.py

# 6. Verification log
python forecast_verification.py
