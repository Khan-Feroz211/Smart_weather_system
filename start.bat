@echo off
echo ============================================================
echo   AI-Powered Precision Agriculture System - Startup
echo ============================================================
echo.

echo [1/3] Starting MQTT Broker...
docker-compose up -d mosquitto
timeout /t 3 /nobreak

echo.
echo [2/3] Installing dependencies...
pip install paho-mqtt structlog plotly shap xgboost pandas -q

echo.
echo [3/3] Starting Application...
echo.
echo ============================================================
echo   System Ready!
echo ============================================================
echo.
echo Access the application at: http://localhost:8000
echo.
echo Press Ctrl+C to stop the application
echo.
python app_clean.py
