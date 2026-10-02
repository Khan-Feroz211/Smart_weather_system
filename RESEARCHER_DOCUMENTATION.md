# Researcher Documentation - AI-Powered Precision Agriculture System

## Overview

This system is a research-grade precision agriculture platform designed for real-time sensor data ingestion, AI-powered crop failure prediction, and data-driven decision support. The architecture is built for academic research collaboration, with emphasis on reproducibility, explainability, and data export capabilities.

## System Architecture

### Data Flow

```
IoT Sensors → MQTT Broker → Sensor Ingestion Service → Data Validation → 
Raw Data Storage → Data Cleaning → Feature Engineering → 
AI Models → Predictions → Visualization Dashboard
```

### Key Components

1. **Sensor Ingestion Layer** (`sensor_ingestion.py`)
   - MQTT consumer for real-time sensor data
   - Data validation with range checks and anomaly detection
   - Raw data archival (never modified)
   - Validated data storage with quality scores

2. **Data Processing Pipeline** (`data_processing.py`)
   - Anomaly detection using Isolation Forest
   - Missing value imputation (KNN + interpolation)
   - Feature engineering:
     - Growing Degree Days (GDD)
     - Evapotranspiration (Penman-Monteith)
     - Stress indices (heat, drought, frost, excess moisture)
   - Data quality scoring (completeness, temporal coverage, consistency)

3. **AI/ML Models** (`crop_failure_predictor.py`)
   - Crop failure prediction with Random Forest / Gradient Boosting
   - Confidence intervals using calibrated classifiers
   - Model explainability using SHAP values
   - Feature importance rankings for research insights

4. **Trend Visualization** (`trend_visualization.py`)
   - Historical trend charts (30-day, 90-day, 1-year)
   - Forecast trends with uncertainty bands (7-day, 14-day, 30-day)
   - Comparative analysis across sensor types
   - Health trend visualization
   - Yield forecast visualization

5. **Experiment Tracking** (`experiment_tracking.py`)
   - MLflow-style experiment logging
   - Model versioning and artifact tracking
   - Dataset versioning with checksums
   - Hyperparameter and metric logging
   - Reproducibility features

## Database Schema

### Sensor Tables

- `raw_sensor_readings`: Archival storage of all sensor readings (never modified)
- `validated_sensor_readings`: Quality-checked sensor data with quality scores
- `sensor_health`: Sensor uptime, quality averages, calibration tracking

### Agriculture Tables

- `farms`: Farm profiles with location and area
- `fields`: Field-level data with soil type and irrigation
- `crop_census`: Crop type, growth stage, planting/harvest dates
- `field_weather`: Field-specific weather data
- `crop_health`: Health scores and stress indicators
- `pest_risks`: Pest/disease risk intelligence
- `irrigation_recommendations`: AI-generated irrigation advice
- `yield_forecasts`: Yield predictions with confidence
- `agri_alerts`: Field/farm-level alerts

### Research Tables

- `experiments`: ML experiment tracking
- `model_versions`: Model versioning with metrics
- `dataset_versions`: Dataset versioning for reproducibility

## API Endpoints

### Sensor Ingestion

- `POST /api/sensors/ingest` - Ingest sensor data via HTTP (MQTT fallback)
- `GET /api/sensors/health` - Get sensor health status
- `GET /api/sensors/readings/<sensor_type>` - Get validated readings
- `GET /api/sensors/export` - Export sensor data (CSV/JSON)

### Data Processing

- `GET /api/processing/clean/<sensor_type>` - Clean and validate sensor data
- `GET /api/processing/field/<field_id>` - Process all sensors for a field
- `GET /api/processing/quality/<field_id>` - Get data quality scores
- `GET /api/processing/features/gdd` - Calculate Growing Degree Days
- `GET /api/processing/features/et` - Calculate Evapotranspiration
- `GET /api/processing/features/stress` - Calculate stress indices

### Prediction

- `GET /api/prediction/failure/<field_id>` - Predict crop failure probability
- `GET /api/prediction/train` - Train crop failure model
- `GET /api/prediction/explain/<field_id>` - Explain prediction with SHAP

### Visualization

- `GET /api/visualization/trend/historical/<sensor_type>` - Historical trend chart
- `GET /api/visualization/trend/forecast/<sensor_type>` - Forecast with uncertainty
- `GET /api/visualization/trend/health/<field_id>` - Health trend chart
- `GET /api/visualization/trend/yield/<field_id>` - Yield forecast chart
- `GET /api/visualization/trend/comparative` - Comparative sensor trends

### Experiment Tracking

- `POST /api/experiments/start` - Start ML experiment
- `POST /api/experiments/<id>/metrics` - Log experiment metrics
- `POST /api/experiments/<id>/end` - End experiment
- `GET /api/experiments` - List experiments
- `GET /api/experiments/<id>` - Get experiment details

## Research Features

### Data Export

All sensor data can be exported in multiple formats for external analysis:
- CSV for Excel/R analysis
- JSON for programmatic access
- API endpoints for integration with research tools

### Model Explainability

SHAP values provide:
- Feature importance rankings
- Individual prediction explanations
- Global model interpretability
- Research publication insights

### Reproducibility

- Versioned datasets with checksums
- Model artifact versioning
- Experiment tracking with hyperparameters
- Git commit integration

### Data Quality Metrics

- Completeness score (percentage of non-null values)
- Temporal coverage (time interval adherence)
- Consistency score (coefficient of variation)
- Sensor reliability (uptime, calibration drift)

## Sensor Types Supported

1. **Soil Sensors**
   - Soil moisture (%)
   - Soil temperature (°C)
   - Soil pH
   - Electrical conductivity (µS/cm)

2. **Weather Sensors**
   - Air temperature (°C)
   - Relative humidity (%)
   - Rainfall (mm)
   - Wind speed (km/h)
   - Atmospheric pressure (hPa)
   - Solar radiation (W/m²)

3. **Vegetation Sensors**
   - NDVI (Normalized Difference Vegetation Index)
   - Multispectral indices

## Model Performance Metrics

### Crop Failure Prediction

- ROC-AUC: Area under ROC curve
- Precision: Positive predictive value
- Recall: True positive rate
- F1-Score: Harmonic mean of precision and recall
- Confidence intervals: 95% prediction intervals

### Yield Forecast

- MAE: Mean Absolute Error (t/ha)
- RMSE: Root Mean Squared Error (t/ha)
- MAPE: Mean Absolute Percentage Error
- Forecast horizon: 7-30 days

## Integration with Satellite Data

The system is designed to integrate with:
- NASA MODIS NDVI data
- SMAP soil moisture data
- GPM precipitation data
- Sentinel-2 multispectral imagery

Integration points:
- API endpoints for satellite data ingestion
- Data fusion with ground sensor data
- Comparative analysis (satellite vs ground truth)

## Collaboration Opportunities

### For NASA/SUPARCO Researchers

1. **Data Access**
   - Real-time agricultural sensor data
   - Historical datasets for model validation
   - Export capabilities for external analysis

2. **Model Validation**
   - Test ML models on existing research datasets
   - Cross-validation with satellite data
   - Benchmark comparison

3. **Joint Publications**
   - Crop failure prediction methodology
   - Sensor data fusion techniques
   - AI explainability in agriculture

4. **Field Trials**
   - Model validation on research farms
   - Sensor deployment coordination
   - Data collection protocols

5. **Technology Transfer**
   - Integration with existing research platforms
   - API development for custom workflows
   - Cloud deployment options

## Getting Started

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Start MQTT broker (Docker)
docker-compose up -d mosquitto

# Start sensor ingestion service
python sensor_ingestion.py

# Start main application
python app_clean.py
```

### Docker Deployment

```bash
# Build and start all services
docker-compose up -d

# Check service status
docker-compose ps

# View logs
docker-compose logs -f
```

### API Testing

```bash
# Ingest sensor data
curl -X POST http://localhost:8000/api/sensors/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "sensor_id": "soil-moisture-1",
    "sensor_type": "soil_moisture",
    "value": 45.5,
    "timestamp": "2025-01-15T10:00:00Z",
    "field_id": 1
  }'

# Get crop failure prediction
curl http://localhost:8000/api/prediction/failure/1

# Get trend visualization
curl "http://localhost:8000/api/visualization/trend/historical/soil_moisture?field_id=1&hours=168"
```

## Citation

If you use this system in your research, please cite:

```
Feroz Khan (2025). AI-Powered Precision Agriculture System with Real-Time 
Sensor Analytics and Crop Failure Prediction. NUTECH, Pakistan.
```

## Contact

- **Developer**: Feroz Khan
- **Affiliation**: NUTECH (4th Semester, AI Engineering)
- **GitHub**: [Project Repository]
- **Demo**: [Deployment URL]

## License

This project is open-source and available for academic research collaboration.

## Acknowledgments

- Sensor validation framework inspired by agricultural IoT best practices
- ML models based on scikit-learn and XGBoost
- Visualization powered by Plotly
- Experiment tracking inspired by MLflow

---

**Last Updated**: January 2025
**Version**: 2.0 (Research-Grade Edition)
