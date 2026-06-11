# 1. Update all live databases
bash update.sh

# 2. Live prediction + SHAP
python live_explain.py

# 3. Alert engine
python build_alerts.py

# 4. LLM bulletin
python llm_bulletin.py

# 5. Verification log
python forecast_verification.py
