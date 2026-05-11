# Configuration Guide - AI-Powered Precision Agriculture System

## Quick Start

The system is now running at: **http://localhost:8000**

### Access Points
- Dashboard: http://localhost:8000/dashboard
- Agriculture Module: http://localhost:8000/agri

---

## Configuration Files

### 1. Environment Variables (`.env`)

Located at: `d:\SWS\Smart_weather_system\.env`

```env
SECRET_KEY=smart_weather_system_secret_2024
OPENWEATHER_API_KEY=demo_key_12345
DEBUG=True
SENSOR_MODE=simulation  # 'simulation' or 'real'
```

**How to configure:**
- `SECRET_KEY`: Flask session encryption key (generate a random string)
- `OPENWEATHER_API_KEY`: Get from https://openweathermap.org/api
- `DEBUG`: Set to `False` for production
- `SENSOR_MODE`: Set to `simulation` (default) or `real` for actual sensors

### Sensor Modes

**Simulation Mode (Default)**
- No hardware required
- System generates realistic synthetic sensor data
- Perfect for demonstrations and testing
- Use when you don't have physical sensors yet

**Real Sensor Mode**
- Connect Arduino/ESP32 sensors via MQTT or HTTP
- Requires physical hardware setup
- See `ARDUINO_INTEGRATION_GUIDE.md` for details
- Use when you have actual sensors installed

**Switching Modes:**

Via API:
```bash
# Get current mode
curl http://localhost:8000/api/sensors/mode

# Set to simulation
curl -X POST http://localhost:8000/api/sensors/mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "simulation"}'

# Set to real sensors
curl -X POST http://localhost:8000/api/sensors/mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "real"}'
```

Or edit `.env` file and restart the application.

### 2. Docker Compose (`docker-compose.yml`)

Located at: `d:\SWS\Smart_weather_system\docker-compose.yml`

**Services:**
- `mosquitto`: MQTT broker for IoT sensor data
  - Port: 1883 (MQTT)
  - Port: 9001 (WebSocket)
  - Config: `mosquitto/config/mosquitto.conf`

**How to use:**
```bash
# Start MQTT broker
docker-compose up -d mosquitto

# Stop MQTT broker
docker-compose down

# View logs
docker-compose logs -f mosquitto
```

### 3. MQTT Broker Configuration

Located at: `d:\SWS\Smart_weather_system\mosquitto\config\mosquitto.conf`

```
listener 1883
allow_anonymous true
persistence true
persistence_location /mosquitto/data
log_dest file /mosquitto/log/mosquitto.log
```

**How to configure:**
- `listener 1883`: MQTT port
- `allow_anonymous`: Set to `false` and add authentication for production
- `persistence`: Enables data persistence
- `log_dest`: Log file location

---

## Database Configuration

### SQLite Database

Located at: `d:\SWS\Smart_weather_system\smart_weather.db`

**Tables:**
- `users` - User accounts
- `weather_data` - Weather readings
- `weather_predictions` - AI predictions
- `alerts` - Weather alerts
- `farms` - Agriculture farms
- `fields` - Field details
- `crop_census` - Crop information
- `crop_health` - Health scores
- `pest_risks` - Pest/disease risks
- `irrigation_recommendations` - Irrigation advice
- `yield_forecasts` - Yield predictions
- `agri_alerts` - Agriculture alerts
- `raw_sensor_readings` - Raw sensor data (archival)
- `validated_sensor_readings` - Validated sensor data
- `sensor_health` - Sensor health metrics
- `experiments` - ML experiment tracking
- `model_versions` - Model versioning
- `dataset_versions` - Dataset versioning

**How to reset database:**
```bash
# Delete database file
del smart_weather.db

# Restart app (will recreate with sample data)
python app_clean.py
```

---

## Sensor Configuration

### Sensor Ingestion Service

Located at: `d:\SWS\Smart_weather_system\sensor_ingestion.py`

**MQTT Topics:**
- `sensors/<sensor_type>/<sensor_id>` - Sensor readings
- `sensors/<sensor_type>/<sensor_id>/<field_id>` - Field-specific readings

**Sensor Types:**
- `soil_moisture` - Soil moisture (%)
- `soil_temperature` - Soil temperature (°C)
- `soil_ph` - Soil pH
- `soil_ec` - Electrical conductivity (µS/cm)
- `air_temperature` - Air temperature (°C)
- `air_humidity` - Relative humidity (%)
- `rainfall` - Rainfall (mm)
- `wind_speed` - Wind speed (km/h)
- `solar_radiation` - Solar radiation (W/m²)
- `ndvi` - NDVI (0-1)

**How to start sensor ingestion:**
```bash
python sensor_ingestion.py
```

**Sample sensor data format:**
```json
{
  "sensor_id": "soil-moisture-1",
  "sensor_type": "soil_moisture",
  "value": 45.5,
  "timestamp": "2025-01-15T10:00:00Z",
  "field_id": 1
}
```

### HTTP Sensor Ingestion (Fallback)

**Endpoint:** `POST /api/sensors/ingest`

```bash
curl -X POST http://localhost:8000/api/sensors/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "sensor_id": "soil-moisture-1",
    "sensor_type": "soil_moisture",
    "value": 45.5,
    "timestamp": "2025-01-15T10:00:00Z",
    "field_id": 1
  }'
```

---

## API Endpoints

### Sensor Ingestion
- `POST /api/sensors/ingest` - Ingest sensor data
- `GET /api/sensors/health` - Get sensor health status
- `GET /api/sensors/readings/<sensor_type>` - Get validated readings
- `GET /api/sensors/export` - Export sensor data (CSV/JSON)

### Data Processing
- `GET /api/processing/clean/<sensor_type>` - Clean sensor data
- `GET /api/processing/field/<field_id>` - Process all sensors for a field
- `GET /api/processing/quality/<field_id>` - Get data quality scores
- `GET /api/processing/features/gdd` - Calculate Growing Degree Days
- `GET /api/processing/features/et` - Calculate Evapotranspiration
- `GET /api/processing/features/stress` - Calculate stress indices

### Prediction
- `GET /api/prediction/failure/<field_id>` - Predict crop failure
- `GET /api/prediction/train` - Train crop failure model
- `GET /api/prediction/explain/<field_id>` - Explain prediction (SHAP)

### Visualization
- `GET /api/visualization/trend/historical/<sensor_type>` - Historical trend
- `GET /api/visualization/trend/forecast/<sensor_type>` - Forecast with uncertainty
- `GET /api/visualization/trend/health/<field_id>` - Health trend
- `GET /api/visualization/trend/yield/<field_id>` - Yield forecast
- `GET /api/visualization/trend/comparative` - Comparative trends

### Experiment Tracking
- `POST /api/experiments/start` - Start ML experiment
- `POST /api/experiments/<id>/metrics` - Log metrics
- `POST /api/experiments/<id>/end` - End experiment
- `GET /api/experiments` - List experiments
- `GET /api/experiments/<id>` - Get experiment details

---

## File Structure

```
Smart_weather_system/
├── app_clean.py                 # Main Flask application
├── sensor_ingestion.py          # MQTT sensor ingestion service
├── data_processing.py           # Data cleaning and feature engineering
├── crop_failure_predictor.py    # Crop failure prediction model
├── trend_visualization.py       # Trend visualization module
├── experiment_tracking.py       # ML experiment tracking
├── smart_weather.db             # SQLite database
├── .env                         # Environment variables
├── docker-compose.yml           # Docker services
├── Dockerfile                   # Application container
├── requirements.txt             # Python dependencies
├── mosquitto/                   # MQTT broker config
│   └── config/
│       └── mosquitto.conf
├── models/                      # Saved ML models
│   ├── weather_model.joblib
│   └── scaler.joblib
├── templates/                   # HTML templates
│   ├── base.html
│   ├── dashboard.html
│   ├── agri_dashboard.html
│   └── field_detail.html
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── script.js
├── PPT_PROMPT.md                # Presentation prompt
├── RESEARCHER_DOCUMENTATION.md  # Researcher guide
└── CONFIGURATION_GUIDE.md       # This file
```

---

## Starting the System

### Option 1: Start All Services Manually

```bash
# 1. Start MQTT broker
docker-compose up -d mosquitto

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start sensor ingestion (optional)
python sensor_ingestion.py

# 4. Start main application
python app_clean.py
```

### Option 2: Using Batch Script (Windows)

```bash
.\start.bat
```

### Option 3: Using Python Script

```bash
python start_all.py
```

---

## Dependencies

### Required Python Packages

Located at: `d:\SWS\Smart_weather_system\requirements.txt`

```
flask
flask-socketio
requests
python-dotenv
apscheduler
numpy
pandas
scikit-learn
joblib
paho-mqtt
structlog
shap
plotly
xgboost
```

**How to install:**
```bash
pip install -r requirements.txt
```

---

## Troubleshooting

### App won't start
1. Check if port 8000 is available: `netstat -ano | findstr :8000`
2. Kill process using port: `taskkill /PID <PID> /F`
3. Check `.env` file exists and has valid values

### MQTT broker issues
1. Check Docker is running: `docker --version`
2. Check Mosquitto status: `docker-compose ps`
3. View Mosquitto logs: `docker-compose logs mosquitto`

### Database errors
1. Delete `smart_weather.db` and restart app
2. Check file permissions
3. Ensure SQLite is installed

### Sensor data not appearing
1. Check MQTT broker is running: `docker-compose ps`
2. Check sensor ingestion service is running
3. Verify MQTT topic format matches expectations

---

## Production Deployment

### Security Checklist
- [ ] Set `DEBUG=False` in `.env`
- [ ] Use strong `SECRET_KEY`
- [ ] Enable MQTT authentication
- [ ] Use HTTPS (SSL/TLS)
- [ ] Set up firewall rules
- [ ] Use production WSGI server (Gunicorn/uWSGI)
- [ ] Enable database backups
- [ ] Set up monitoring and logging

### Deployment Steps
1. Update environment variables
2. Build Docker image: `docker build -t agri-system .`
3. Push to container registry
4. Deploy to production server
5. Configure reverse proxy (Nginx)
6. Set up SSL certificates
7. Configure firewall
8. Set up monitoring

---

## Contact & Support

- **Documentation**: `RESEARCHER_DOCUMENTATION.md`
- **PPT Prompt**: `PPT_PROMPT.md`
- **Configuration**: This file

---

**Last Updated**: January 2025
**Version**: 2.0 (Research-Grade Edition)
