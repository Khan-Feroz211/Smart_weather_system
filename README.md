# Smart Weather System — AI-Powered Multi-Hazard Early Warning

**A robust, explainable, multi-hazard early warning system for rural and low-connectivity environments (Pakistan).**

- **Author**: Feroz U Din
- **License**: MIT
- **Python**: 3.11+
- **Status**: Production-ready (6-phase ML pipeline)

---

## Table of Contents

- [Quick Start](#quick-start)
- [System Architecture](#system-architecture)
- [Phase 1: Stacking Ensemble](#phase-1-stacking-ensemble)
- [Phase 2: Advanced Feature Engineering](#phase-2-advanced-feature-engineering)
- [Phase 3: Time-Aware & Location-Aware Cross-Validation](#phase-3-time-aware--location-aware-cross-validation)
- [Phase 4: Explainable AI (XAI)](#phase-4-explainable-ai-xai)
- [Phase 5: Multi-Hazard Output & Crisis Communication](#phase-5-multi-hazard-output--crisis-communication)
- [Phase 6: Edge-Case Hardening](#phase-6-edge-case-hardening)
- [API Endpoints](#api-endpoints)
- [Running Tests](#running-tests)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/Khan-Feroz211/Smart_weather_system.git
cd Smart_weather_system

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Run the application
python app_clean.py
# Then open http://localhost:8000 in your browser
```

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Client Browser                          │
│  (HTML5, CSS3, JS, Bootstrap 5, Socket.IO Client)          │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          │ HTTP/WebSocket
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Flask Web Server                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Routes     │  │  SocketIO    │  │   AI Stack   │     │
│  │   Handler    │  │   Events     │  │  (6 Phases)  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────┬───────────────────────────────────┘
        ┌────────┬────────┬────────┬────────┬────────┬────────┐
        │        │        │        │        │        │        │
        ▼        ▼        ▼        ▼        ▼        ▼
┌─────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  SQLite DB  │ │OpenWeather API│ │APScheduler    │ │Stacking      │ │Feature       │ │XAI System    │
│             │ │              │ │Scheduler      │ │Ensemble      │ │Engineering   │ │(SHAP+LIME)   │
│             │ │              │ │              │ │(RF+XGB+LGBM+│ │(Lag, Rolling,│ │              │
│             │ │              │ │              │ │ELM→LogReg)   │ │Interactions, │ │              │
│             │ │              │ │              │ │              │ │Upper-Level)  │ │              │
└─────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
                                                                                                       │
┌─────────────────────────────────────────────────────────────┐                                         │
│                       Edge-Case Layer                       │◄────────────────────────────────────────┘
│  FallbackCache (7-day) • CacheMode • Confidence Penalty • CircuitBreaker
└─────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Stacking Ensemble

**File**: `stacking_ensemble.py`

A Stacking Ensemble Classifier combining four diverse base learners with a Logistic Regression meta-learner:

| Base Learner | Role | Key Strength |
|---|---|---|
| Random Forest | Primary classifier | High generalization, handles noise |
| XGBoost | Gradient boosting | Fast, handles missing data |
| LightGBM | Gradient boosting | Memory-efficient, fast training |
| ELM (Extreme Learning Machine) | Neural network | Rapid time-series feature extraction |

**Meta-Learner**: Logistic Regression (combines base learner outputs optimally)

**Key Features**:
- Out-of-fold predictions to prevent data leakage during meta-learner training
- Per-learner try/except fallback (if LightGBM crashes on Windows, other learners continue)
- `predict_multi_hazard()` method for multi-class hazard prediction
- Model persistence via joblib (`models/stacking_ensemble.joblib`)

---

## Phase 2: Advanced Feature Engineering

**File**: `feature_engineering.py`

### Temporal Features
- **Lag features**: t-1, t-3, t-6, t-12 hours for temperature, humidity, pressure
- **Rolling statistics**: 3, 6, 12, 24-hour rolling mean and std

### Interaction Terms
- Heat index (Temp * Humidity interaction)
- Vapor Pressure Deficit (VPD)
- Dew point calculation
- Air density proxy (Pressure / Temperature)
- Wind chill
- Fire weather index
- Storm potential index
- Growing Degree Days (GDD)

### Cyclical Encoding
- Hour of day → sin/cos (captures daily cycle)
- Day of year → sin/cos (captures seasonal cycle)
- Month → sin/cos
- Day of week → sin/cos

### Upper-Level Data
- 850hPa and 500hPa geopotential height
- 850hPa and 500hPa wind components (u, v)
- Temperature advection proxy
- Thickness analysis
- Wind shear

### Coverage
- 15 Pakistani cities: Lahore, Islamabad, Karachi, Peshawar, Quetta, Multan, Faisalabad, Rawalpindi, Gujranwala, Hyderabad, Sukkur, Larkana, Bahawalpur, Sialkot, Sargodha

---

## Phase 3: Time-Aware & Location-Aware Cross-Validation

**File**: `evaluation.py`

### Temporal Split CV
- Trains on historical timeline, tests on strictly future data
- No data leakage — model cannot "peek" into the future
- Configurable split date (default: 80% train, 20% test by time)

### Leave-One-Location-Out CV (LOLO)
- Trains on all cities except one, tests on the excluded city
- Ensures model generalizes to new, unseen locations
- Critical for rural deployment where some areas have sparse data

### Metrics
| Metric | Purpose |
|---|---|
| Brier Skill Score (BSS) | Probabilistic reliability of hazard forecasts |
| AUC-ROC | Discrimination ability across all hazard classes |
| R² | Point prediction accuracy |
| F1 Score (macro/weighted) | Classification quality |
| Precision & Recall | False alarm vs. missed event tradeoff |

---

## Phase 4: Explainable AI (XAI)

**File**: `xai_explainability.py`

### Global Explainability (SHAP)
- Ranks feature importance across all predictions
- Shows which environmental variables (wind shear, humidity, etc.) drive hazard detection
- Generates SHAP summary plots and value plots

### Local Explainability (LIME)
- Explains why a specific alert was triggered
- Generates human-readable text reports:
  > "Alert Triggered because: High humidity combined with rising 3-hour temperature trend exceeds thresholds"

### Integration
- `XAISystem` class orchestrates both SHAP and LIME
- Text reports accompany every alert sent to users
- Explanations are included in API responses

---

## Phase 5: Multi-Hazard Output & Crisis Communication

**File**: `multi_hazard.py`

### 5 Hazard Categories
1. **Extreme Heat / Heatwave**
2. **Extreme Cold / Frost**
3. **Heavy Rain / Flood risk**
4. **High Wind / Storm risk**
5. **Normal** (baseline)

### NWS/INFORM Risk Color Coding
| Level | Color | Hex Code | Meaning |
|---|---|---|---|
| 0 | Green | `#00E450` | No risk |
| 1 | Yellow | `#FFCC00` | Minor risk |
| 2 | Orange | `#FF6600` | Moderate risk |
| 3 | Red | `#CC0000` | Severe risk |

### Crisis Communication
- `CrisisCommunicationSystem`: Generates crisis-level messages
- `AlertStore`: Persists alerts with timestamps and locations
- SMS alerts with pre-translated templates (Urdu/Sindhi/Pashto/Saraiki/Balochi)
- Voice prompts for low-literacy users
- Per-hazard action recommendations (e.g., "Apply frost protection", "Secure loose objects")

---

## Phase 6: Edge-Case Hardening

**File**: `edge_case_hardening_v2.py`

### Graceful Degradation
- **FallbackCacheSystem**: Stores last 7 days of weather observations and model predictions locally
- **CacheMode**: Automatic failover between `ONLINE`, `CACHE`, and `OFFLINE` modes
- **CircuitBreaker**: Trips after 5 consecutive API failures, retries after 60s timeout
- **ConfidencePenaltySystem**: 
  - -5% per hour of stale data
  - -10% per missing weather field
  - Minimum confidence: 10% (prevents false alarms)

### Configuration
| Flag | Default | Description |
|---|---|---|
| `CACHE_RETENTION_DAYS` | 7 | Days to retain cached data |
| `CONFIDENCE_MINIMUM` | 0.1 | Minimum confidence threshold |
| `CONFIDENCE_PENALTY_PER_HOUR_STALE` | 0.05 | Penalty per hour of stale data |
| `CONFIDENCE_PENALTY_PER_MISSING_FIELD` | 0.1 | Penalty per missing field |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | 5 | Failures before circuit opens |
| `CIRCUIT_BREAKER_TIMEOUT_SECONDS` | 60 | Timeout before retry |

---

## API Endpoints

### Multi-Hazard Prediction
- `GET /api/hazards/predict` — Predict hazards for a location
- `GET /api/hazards/alerts` — Get active alerts for all hazards
- `GET /api/hazards/dashboard` — Full dashboard data with risk levels

### Explainability
- `GET /api/xai/global` — Global SHAP feature importance
- `GET /api/xai/local/<location>` — Local LIME explanation for a specific alert

### System Management
- `GET /api/system/status` — System health (cache mode, circuit breaker, confidence)
- `POST /api/system/cache/clear` — Clear fallback cache
- `GET /api/system/cache/stats` — Cache statistics

### Evaluation
- `GET /api/evaluation/temporal` — Temporal cross-validation results

---

## Running Tests

```bash
# Run all tests
python -m pytest tests/test_multi_hazard_system.py -v

# Or without pytest
python tests/test_multi_hazard_system.py

# Quick import check
python -c "import app_clean; print('Import OK')"
```

**Test Coverage** (42 tests across 7 test classes):
- TestStackingEnsemble (7 tests)
- TestFeatureEngineering (7 tests)
- TestEvaluation (5 tests)
- TestXAI (3 tests)
- TestMultiHazard (8 tests)
- TestEdgeCaseHardening (10 tests)
- TestIntegration (2 tests)

---

## Configuration

Create a `.env` file in the project root:

```env
SECRET_KEY=smart_weather_system_secret_2024
OPENWEATHER_API_KEY=your_api_key_here
DEBUG=True
SENSOR_MODE=simulation
```

---

## License

MIT License — see [LICENSE](LICENSE) file for details.

---

## 🛡️ Edge-Case Hardening

The system implements comprehensive edge-case hardening across 7 modules to
ensure it fails safely, communicates uncertainty honestly, and degrades
gracefully under real-world conditions of rural Pakistan (patchy connectivity,
sparse sensors, multilingual low-literacy users, high-stakes disaster alerts).

### Modules

| # | Module | Key Hardening |
|---|--------|--------------|
| 1 | **Geospatial & Weather Ingestion** | Coordinate sanity checks, stale-data cache fallback, circuit breakers, grid-cell confidence metadata |
| 2 | **Satellite & IoT Sensing** | Cloud-obscured NDVI detection, sensor health scoring, 4-tier fallback chain |
| 3 | **NDMA Disaster Alerts** | Alert deduplication with severity escalation, multi-channel delivery (push→SMS), pre-translated templates (Urdu/Sindhi/Pashto/Saraiki/Balochi), audit logging |
| 4 | **AI/ML Stack** | Out-of-distribution detection, regional data-density checks, water-sustainability penalties, cold-start policy |
| 5 | **Feedback & Self-Improvement** | Anomaly detection on feedback, demographic skew tracking, human-in-the-loop approval gates, model versioning |
| 6 | **Deployment & Delivery** | Offline/online conflict resolution, SMS templates with preserved conditional logic, non-text interaction path (voice/IVR, icons) |
| 7 | **Trust & Safety** | Explicit confidence indicators on every advisory, data-privacy/consent layer, trust incident logging with end-to-end traceability |

### Configuration Flags

All thresholds are configurable via environment variables:

| Flag | Default | Description |
|------|---------|-------------|
| `HARDEN_COORD_MAX_LAT` | 90.0 | Maximum valid latitude |
| `HARDEN_COORD_MAX_LON` | 180.0 | Maximum valid longitude |
| `HARDEN_COORD_PRECISION_M` | 50.0 | Minimum coordinate precision in metres |
| `HARDEN_ELEVATION_VARIANCE` | 150.0 | Elevation variance threshold for complex terrain |
| `HARDEN_WEATHER_CACHE_TTL` | 30 | Weather cache TTL in minutes |
| `HARDEN_WEATHER_CACHE_MAX_AGE` | 6 | Max cache age in hours |
| `HARDEN_CB_FAILURE_THRESHOLD` | 5 | Circuit breaker failure threshold |
| `HARDEN_CB_TIMEOUT` | 60 | Circuit breaker timeout in seconds |
| `HARDEN_CB_BACKOFF_BASE` | 1.0 | Exponential backoff base |
| `HARDEN_SENSOR_OFFLINE_MIN` | 30 | Sensor offline threshold in minutes |
| `HARDEN_SENSOR_DRIFT` | 5.0 | Sensor drift threshold |
| `HARDEN_NDVI_CLOUD` | 0.1 | NDVI cloud obscuration threshold |
| `HARDEN_ALERT_COOLDOWN` | 60 | Alert deduplication cooldown in minutes |
| `HARDEN_OOD_PERCENTILE` | 95.0 | OOD detection percentile |
| `HARDEN_LOW_CONF_THRESHOLD` | 50 | Low-confidence region data point threshold |
| `HARDEN_WATER_CAP` | 50.0 | Water sustainability cap in mm |
| `HARDEN_COLD_START_MIN` | 10 | Minimum interactions for cold-start policy |
| `HARDEN_FEEDBACK_RATE_LIMIT` | 5 | Feedback rate limit per minute |
| `HARDEN_FEEDBACK_ANOMALY_Z` | 3.0 | Feedback anomaly z-score threshold |
| `HARDEN_FEEDBACK_SKEW_PERCENT` | 10.0 | Feedback skew top-percent threshold |
| `HARDEN_PWA_TTL` | 24 | PWA offline conflict TTL in hours |
| `HARDEN_SMS_MAX_LENGTH` | 160 | SMS max character length |
| `HARDEN_SMS_CONDITIONAL_RESERVE` | 30 | SMS conditional clause reserve |

### Running Tests

```bash
python -m pytest tests/test_edge_case_hardening.py -v
```

Or without pytest:

```bash
python tests/test_edge_case_hardening.py
```

---

## ✨ Features

### Core Functionality
- **Real-time Weather Monitoring**: Live weather data updates every 2 minutes using WebSocket connections
- **AI-Powered Predictions**: Machine learning model predicts weather conditions 1 hour ahead with 85%+ accuracy
- **User Management**: Multi-user support with personalized preferences and location-based tracking
- **Smart Alerts**: Customizable weather alerts with severity levels (Low, Medium, High, Critical)
- **Activity Recommendations**: AI-driven suggestions for outdoor activities based on current and predicted weather
- **Historical Data Analysis**: Track weather patterns and user activities over time
- **Responsive Dashboard**: Modern, futuristic UI with real-time updates and animations

### Technical Features
- **WebSocket Communication**: Bi-directional real-time data exchange using Flask-SocketIO
- **Background Scheduling**: Automated periodic tasks for data updates and model retraining
- **SQLite Database**: Lightweight, efficient data storage with optimized queries
- **RESTful API**: Clean API endpoints for all major operations
- **Model Persistence**: Trained ML models saved and loaded for consistent predictions
- **Demo Mode**: Fallback demo data when API key is not available

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Client Browser                          │
│  (HTML5, CSS3, JavaScript, Bootstrap 5, Socket.IO Client)  │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          │ HTTP/WebSocket
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                  Flask Web Server                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   Routes     │  │  SocketIO    │  │   AI Model   │     │
│  │   Handler    │  │   Events     │  │   Engine     │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────┬───────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
        ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   SQLite     │  │ OpenWeather  │  │  Background  │
│   Database   │  │     API      │  │  Scheduler   │
└──────────────┘  └──────────────┘  └──────────────┘
```

---

## 🛠️ Technology Stack

### Backend
- **Flask 2.3.3**: Lightweight Python web framework for routing and HTTP handling
- **Flask-SocketIO 5.3.6**: WebSocket support for real-time bidirectional communication
- **Eventlet 0.33.3**: Concurrent networking library for async operations
- **APScheduler 3.10.4**: Advanced Python scheduler for background tasks

### Machine Learning
- **Scikit-learn**: Random Forest Regressor for weather prediction
- **NumPy**: Numerical computations and array operations
- **Pandas**: Data manipulation and time-series analysis
- **Joblib**: Model serialization and persistence

### Database
- **SQLite3**: Built-in relational database for data storage

### Frontend
- **Bootstrap 5.3.0**: Responsive CSS framework with dark/light theme
- **Font Awesome 6.4.0**: Icon library for UI elements
- **Socket.IO Client 4.7.2**: Real-time client-side event handling
- **Vite 5.x**: Frontend build tool and dev server
- **Custom CSS**: Futuristic, animated design with glass-morphism effects

### External Services
- **OpenWeatherMap API**: Real-time weather data provider
- **Python-dotenv 1.0.0**: Environment variable management

---

## 📥 Installation & Setup

### Prerequisites
- Python 3.8 or higher
- Node.js 18+ and npm
- pip (Python package manager)
- Internet connection (for API calls)

### Step 1: Clone or Download the Repository
```bash
git clone https://github.com/Khan-Feroz211/Smart_weather_system.git
cd Smart_weather_system
```

### Step 2: Install Dependencies
```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Node.js dependencies
npm install
```

### Step 3: Run the Application
```bash
# Single command to start both frontend and backend
npm start
```

This starts:
- **Flask backend** on `http://127.0.0.1:5000`
- **Vite dev server** (frontend proxy) on `http://127.0.0.1:3000`

Or run them separately:
```bash
npm run backend     # Start Flask backend only
npm run frontend    # Start Vite dev server only
```

Or run the Flask app directly without npm:
```bash
python app_clean.py
```
Then open `http://127.0.0.1:5000` in your browser.

The `requirements.txt` includes:
```
Flask==2.3.3
flask-socketio==5.3.6
eventlet==0.33.3
requests==2.31.0
python-dotenv==1.0.0
apscheduler==3.10.4
```

**Note**: Additional ML libraries (scikit-learn, numpy, pandas, joblib) are used but not listed in requirements.txt. Install them separately:
```bash
pip install scikit-learn numpy pandas joblib
```

### Step 3: Configure Environment Variables (Optional)
Create a `.env` file in the project root:
```env
SECRET_KEY=your_secret_key_here
OPENWEATHER_API_KEY=your_api_key_here
```

- **SECRET_KEY**: Flask session encryption key (defaults to 'smart_weather_ai_2024')
- **OPENWEATHER_API_KEY**: Get a free API key from [OpenWeatherMap](https://openweathermap.org/api)
  - **Demo Mode**: If no API key is provided, the system uses simulated weather data

### Step 4: Run the Application
```bash
python app_clean.py
```

The server will start on `http://0.0.0.0:8000`

### Step 5: Access the Application
Open your web browser and navigate to:
```
http://localhost:8000
```

---

## 📁 File Structure & Documentation

### Root Files

#### `app_clean.py` (Primary Application - 680 lines)
**Purpose**: Main application file containing the complete Flask application with AI integration.

**Key Components**:

1. **WeatherAI Class** (Lines 39-151)
   - **Purpose**: Implements the machine learning model for weather prediction
   - **Methods**:
     - `__init__()`: Initializes Random Forest model with 50 estimators, max depth 10
     - `prepare_features(historical_data)`: Converts historical weather data into feature matrix
       - Features: temperature, humidity, normalized pressure, wind speed, hour, day of week, month
       - Returns: NumPy arrays (X, y) for training
     - `train(historical_data)`: Trains the model using 80/20 train-test split
       - Applies StandardScaler for feature normalization
       - Saves trained model and scaler to `models/` directory
       - Emits training completion event via WebSocket
     - `load_model()`: Loads pre-trained model from disk
     - `predict(current_weather)`: Predicts temperature 1 hour ahead
       - Returns: predicted temperature, confidence score, timestamp

2. **Database Functions** (Lines 153-255)
   - `init_database()`: Creates 4 tables with sample data
     - **users**: User accounts with preferences
     - **weather_data**: Historical weather records
     - **user_activities**: User activity logs
     - **weather_alerts**: Custom alert configurations
   - `get_db_connection()`: Returns SQLite connection with Row factory
   - `fetch_live_weather(location)`: Fetches data from OpenWeatherMap API
     - Fallback to demo data if API unavailable
   - `store_weather_data(weather_data)`: Inserts weather records into database
   - `get_historical_weather(location, hours=24)`: Retrieves historical data for ML training

3. **Background Tasks** (Lines 337-367)
   - `update_weather_data()`: Scheduled function that:
     - Fetches weather for all user locations
     - Stores data in database
     - Gets AI predictions
     - Broadcasts updates via WebSocket
   - Runs every 2 minutes via APScheduler

4. **Flask Routes** (Lines 369-589)
   - `/` → Redirects to dashboard
   - `/dashboard` → Main dashboard with stats and weather cards
   - `/users` → User management page with user list
   - `/add_user` → Form to create new users with preferences
   - `/user/<user_id>` → Individual user profile with activities and alerts
   - `/add_alert/<user_id>` → Create custom weather alerts
   - `/weather` → Detailed weather display page
   - `/alerts` → Alert management page
   - `/recommendations` → Activity recommendation page

5. **WebSocket Events** (Lines 591-643)
   - `connect`: Client connection handler
   - `disconnect`: Client disconnection handler
   - `request_weather`: Real-time weather data request
   - `request_ai_training`: Manual AI model training trigger

6. **Application Lifecycle** (Lines 645-680)
   - `initialize_app()`: Startup sequence
     - Initializes database
     - Loads/trains AI model
     - Starts background scheduler
   - `shutdown_app()`: Graceful shutdown handler
   - Main execution block starts SocketIO server on port 8000

**Technical Details**:
- **Threading Model**: Uses `async_mode='threading'` for concurrent request handling
- **CORS**: Allows all origins for WebSocket connections
- **Error Handling**: Comprehensive try-except blocks with logging
- **Performance**: Optimized for 128MB GPU with reduced model complexity

---

#### `app_clean_backup.py` (Backup - 680 lines)
**Purpose**: Backup copy of the main application file using `eventlet` instead of threading.

**Key Difference**:
- Line 22: `eventlet.monkey_patch()` - Patches standard library for async I/O
- Line 27: `async_mode='eventlet'` instead of `'threading'`

**When to Use**:
- Use `app_clean.py` for development (easier debugging)
- Use `app_clean_backup.py` for production (better concurrency)

---

#### `run-production.py` (Empty)
**Purpose**: Placeholder for production deployment script.

**Intended Usage** (not implemented):
```python
# Future implementation
from app_clean import app, socketio
import os

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
```

---

#### `alerts.py` (Empty)
**Purpose**: Placeholder for modular alert system.

**Intended Usage** (not implemented):
- Separate alert logic from main application
- Custom alert rules engine
- Email/SMS notification integration

---

#### `requirements.txt`
**Purpose**: Python package dependencies for the project.

**Contents**:
```
Flask==2.3.3              # Web framework
flask-socketio==5.3.6     # WebSocket support
eventlet==0.33.3          # Async networking
requests==2.31.0          # HTTP client for API calls
python-dotenv==1.0.0      # Environment variable loader
apscheduler==3.10.4       # Task scheduling
```

**Note**: ML libraries (scikit-learn, numpy, pandas, joblib) are missing and should be added.

---

### Static Files

#### `static/css/style.css` (281 lines)
**Purpose**: Custom CSS stylesheet with futuristic dark theme.

**Key Sections**:

1. **CSS Variables** (Lines 1-11)
   ```css
   :root {
       --primary: #2563eb;        /* Primary blue */
       --primary-dark: #1d4ed8;   /* Darker blue */
       --dark-bg: #0f172a;        /* Dark background */
       --card-bg: #1e293b;        /* Card background */
       --text-light: #f8fafc;     /* Light text */
   }
   ```

2. **Base Styles** (Lines 13-30)
   - Dark background with light text
   - System font stack for performance
   - Smooth scrolling and overflow handling

3. **Navbar Styling** (Lines 32-42)
   - Glass-morphism effect with `backdrop-filter: blur(5px)`
   - Semi-transparent background
   - Blue border bottom glow

4. **Card Components** (Lines 44-71)
   - `.card-futuristic`: Main card design with border glow
   - Hover effects: lift animation, border color change
   - Shimmer effect using `::before` pseudo-element
   - Translates gradient from left to right on hover

5. **Stats Cards** (Lines 73-86)
   - Gradient background (135deg)
   - Large icons and bold numbers
   - Centered text alignment

6. **Buttons** (Lines 88-99)
   - Gradient background with hover lift
   - Box shadow on hover
   - Smooth transitions

7. **Tables** (Lines 101-126)
   - `.table-futuristic`: Dark themed tables
   - Gradient header background
   - Row hover effect with blue tint

8. **Weather Icons** (Lines 128-137)
   - Gradient text fill (gold/orange)
   - Floating animation (3s infinite)
   - **Browser compatibility**: Uses vendor prefixes
     - `-webkit-background-clip: text`
     - `-webkit-text-fill-color: transparent`

9. **Alert Badges** (Lines 139-156)
   - Gradient backgrounds for different severity levels
   - Pulse animation (2s infinite)
   - Color coding: danger (red), warning (orange), success (green)

10. **Live Indicators** (Lines 158-167)
    - Blinking dot animation
    - Green for connected, red for disconnected
    - 8px circle with smooth opacity transition

11. **Loading Animations** (Lines 169-174)
    - `.loading-dots::after`: Animated ellipsis
    - Steps through 1, 2, 3 dots

12. **Progress Bars** (Lines 176-186)
    - Glass effect background
    - Gradient blue fill
    - Smooth width transitions

13. **AI Status Indicators** (Lines 188-204)
    - `.ai-status.trained`: Green badge
    - `.ai-status.training`: Orange badge
    - Rounded pill shape with border

14. **Utility Classes** (Lines 206-213)
    - `.text-glow`: Text shadow effect
    - `.glass-effect`: Semi-transparent blur background

15. **Responsive Design** (Lines 215-226)
    - Disables hover animations on mobile for performance
    - Reduced padding on small screens

16. **Color Classes** (Lines 228-236)
    - Temperature colors: cold (blue), mild (green), warm (yellow), hot (red)
    - Weather condition colors: sunny (yellow), cloudy (gray), rainy (blue), stormy (purple)

**Performance Optimizations**:
- Reduced blur radius (5px instead of 10px)
- Limited animations to essential elements
- Uses CSS transforms for hardware acceleration
- Optimized for 128MB GPU systems

---

#### `static/js/script.js` (405 lines)
**Purpose**: Client-side JavaScript for real-time interactions and UI updates.

**Key Components**:

1. **WeatherSystem Class** (Lines 1-395)
   Main application controller handling all client-side logic.

   **Constructor & Initialization** (Lines 2-7)
   ```javascript
   constructor() {
       this.socket = io();  // Initialize Socket.IO client
       this.initializeSocket();  // Set up event listeners
       this.initializeApp();     // Initialize UI components
   }
   ```

   **Socket Event Handlers** (Lines 9-40)
   - `connect`: Connection established
     - Shows success notification
     - Updates connection indicator to green
   - `disconnect`: Connection lost
     - Shows error notification
     - Updates indicator to red
   - `connection_response`: Server acknowledgment
   - `weather_update`: Real-time weather data from server
     - Updates weather display
     - Checks alert conditions
   - `weather_response`: Response to manual weather requests
   - `prediction_update`: AI prediction updates

   **App Initialization** (Lines 42-51)
   - `initializeApp()`:
     - Starts real-time clock
     - Initializes UI animations
     - Loads initial weather data
     - Sets up periodic updates

   **Real-Time Clock** (Lines 53-77)
   - `startRealTimeClock()`:
     - Updates every second (1000ms interval)
     - Formats time in 12-hour format with AM/PM
     - Formats date with weekday, month, day, year
     - Updates DOM elements `#live-clock` and `#live-date`

   **UI Animations** (Lines 79-115)
   - `initializeAnimations()`:
     - **Intersection Observer**: Fade-in animation for cards
       - Watches when cards enter viewport
       - Animates opacity and translateY
     - `setupAutoDismissAlerts()`:
       - Auto-dismisses Bootstrap alerts after 5 seconds

   **Weather Data Loading** (Lines 117-143)
   - `loadInitialWeather()`:
     - Requests weather for default location (London)
     - Loads weather for all user locations
   - `getUserLocations()`:
     - Returns array of tracked locations
   - `setupPeriodicUpdates()`:
     - Refreshes weather every 2 minutes (120000ms)
     - Simulates live temperature updates every 30 seconds

   **Live Temperature Updates** (Lines 145-184)
   - `updateDemoTemperatures()`:
     - Simulates small temperature fluctuations (-1°C to +1°C)
     - Adds visual feedback with `.temp-updating` class
     - Updates temperature color based on value
   - `updateTemperatureColor(element, temperature)`:
     - < 10°C: `.temp-cold` (blue)
     - 10-20°C: `.temp-mild` (green)
     - 20-30°C: `.temp-warm` (yellow)
     - > 30°C: `.temp-hot` (red)

   **Weather Display Updates** (Lines 186-218)
   - `updateWeatherDisplay(data)`:
     - Finds weather card by location
     - Animates update with pulse effect
   - `animateWeatherUpdate(card, data)`:
     - Adds `.weather-updating` class
     - Updates temperature, humidity, wind, etc.
     - Removes animation class after 500ms
   - `getWeatherClass(condition)`:
     - Maps weather conditions to CSS classes
     - Clear → weather-sunny
     - Clouds → weather-cloudy
     - Rain → weather-rainy
     - Thunderstorm → weather-stormy

   **Dashboard Updates** (Lines 220-275)
   - `updateDashboardWeather(data)`:
     - Updates main dashboard elements
     - Elements: current-temp, current-condition, current-humidity, etc.
   - `updatePredictionDisplay(prediction)`:
     - Shows AI predicted temperature
     - Displays confidence percentage
     - Updates `#ai-prediction` element

   **Alert System** (Lines 277-295)
   - `checkAlerts(data)`:
     - Temperature > 35°C: Heat warning
     - Wind speed > 50 km/h: Wind warning
   - `showAlert(title, message, type)`:
     - Displays alert notification

   **Notification System** (Lines 297-333)
   - `showNotification(message, type)`:
     - Creates Bootstrap toast notification
     - Types: info, success, warning, danger
     - Auto-dismisses after display
     - Positions in top-right corner
   - `createToastContainer()`:
     - Creates container if not exists
     - Fixed position with high z-index (9999)

   **Connection Management** (Lines 335-342)
   - `updateConnectionStatus(connected)`:
     - Updates connection indicator dot
     - Changes color and title based on status

   **Utility Methods** (Lines 344-365)
   - `requestWeather(location)`:
     - Manually request weather for specific location
   - `simulateAITraining()`:
     - Shows training completion notification
     - Updates AI status badge

2. **Initialization** (Lines 397-405)
   - DOM Content Loaded event listener
   - Creates global `window.weatherSystem` instance
   - Injects dynamic CSS for animations:
     - `.weather-updating`: Pulse animation
     - `.temp-updating`: Color transition
     - `@keyframes pulseUpdate`: Scale effect
     - Toast backdrop blur

3. **Utility Functions** (Lines 407-425)
   - `formatTemperature(temp)`:
     - Formats number to 1 decimal place with °C
   - `getWeatherIcon(condition)`:
     - Returns Font Awesome icon class for condition
     - Clear → fa-sun
     - Clouds → fa-cloud
     - Rain → fa-cloud-rain
     - Snow → fa-snowflake

**Technical Implementation**:
- **Event-Driven Architecture**: All updates triggered by WebSocket events
- **DOM Manipulation**: Direct element updates for performance
- **Animation Optimization**: Uses CSS classes instead of inline styles
- **Memory Management**: Removes toast elements after hide
- **Error Handling**: Null checks before element updates

---

### Templates

#### `templates/base.html` (77 lines)
**Purpose**: Base template providing layout structure for all pages.

**Structure**:

1. **HTML Head** (Lines 1-11)
   ```html
   <html lang="en" data-bs-theme="dark">
   ```
   - Sets Bootstrap dark theme globally
   - Responsive viewport meta tag
   - Includes:
     - Bootstrap 5.3.0 CSS
     - Font Awesome 6.4.0 icons
     - Custom `style.css`
     - Socket.IO 4.7.2 client library

2. **Navigation Bar** (Lines 12-50)
   - `.navbar-futuristic`: Custom styled navbar
   - Brand logo with cloud-sun icon
   - **Connection Status Indicator**:
     - Live blinking dot (green when connected)
     - Text: "LIVE"
   - **Real-time Date/Time Display**:
     - `#live-date`: Full date with day of week
     - `#live-clock`: Time with seconds (12-hour format)
     - Uses Jinja2 template variables: `{{ now }}`
   - **Navigation Links**:
     - Dashboard (chart-line icon)
     - Users (users icon)
     - Weather (map-marker icon)

3. **Main Content Area** (Lines 51-67)
   ```html
   <div class="main-content py-4">
       {% block content %}{% endblock %}
   </div>
   ```
   - Child templates inject content here

4. **Footer** (Lines 68-77)
   - Copyright notice
   - Bootstrap & Socket.IO script includes
   - Custom `script.js` include

**Jinja2 Blocks**:
- `{% block title %}`: Page-specific titles
- `{% block content %}`: Page-specific content

---

#### `templates/dashboard.html` (215 lines)
**Purpose**: Main dashboard displaying weather stats, charts, and recent activity.

**Key Sections**:

1. **Header** (Lines 5-19)
   - Page title with dashboard icon
   - AI status indicator (Trained/Training)

2. **Stats Cards** (Lines 21-54)
   - **4 Statistics Panels**:
     - Active Users count
     - Active Alerts count
     - Current Temperature (live updated)
     - AI Accuracy (85%)
   - Uses `.stat-card` class
   - Large icons with Font Awesome

3. **Current Weather Card** (Lines 56-111)
   - Location display (London)
   - Weather icon (animated sun)
   - Current temperature with color coding
   - Weather condition text
   - **Detailed Metrics** (4 columns):
     - Humidity (%)
     - Wind Speed (km/h)
     - Pressure (hPa)
     - Feels Like temperature
   - **AI Prediction Section**:
     - Predicted temperature 1 hour ahead
     - Confidence percentage
     - Robot icon

4. **24-Hour Forecast** (Lines 113-147)
   - Hourly forecast for next 24 hours
   - 6 time slots displayed
   - Each showing: time, icon, temperature
   - Uses glass-effect cards

5. **Recent Weather Data Table** (Lines 149-175)
   - Shows last 3 weather records
   - Columns: Location, Temperature, Condition, Time
   - Loops through `recent_weather` variable
   - Conditional weather icon based on condition
   - Time formatted with `strftime('%I:%M %p')`

6. **Recent Activities Table** (Lines 177-213)
   - Shows last 5 user activities
   - Columns: User, Activity, Weather, Date
   - Loops through `recent_activities` variable
   - Activity-specific icons (walking, cycling, reading)
   - Date formatting

**Dynamic Content**:
- All data passed from Flask route via Jinja2 variables
- Real-time updates via JavaScript WebSocket handlers
- Responsive grid layout using Bootstrap columns

---

#### `templates/user_management.html`
**Purpose**: Display and manage all users in the system.

**Expected Features** (based on route logic):
- User list table with columns:
  - Username
  - Email
  - Location
  - Activity Count
  - Alert Count
  - Actions (View Profile, Edit, Delete)
- Add User button linking to `/add_user`
- Total users count
- Total alerts count
- Total activities count

---

#### `templates/add_user.html`
**Purpose**: Form to create new users with preferences.

**Expected Form Fields** (based on route logic):
- Username (text input, required)
- Email (email input, required)
- Location (text input, required)
- **Preferences**:
  - Preferred Activities (checkboxes: walking, cycling, reading, etc.)
  - Temperature Range (min/max sliders)
  - Avoid Rain (checkbox)
  - Avoid Extreme Heat (checkbox)
- Submit button

---

#### `templates/profile.html`
**Purpose**: Individual user profile page with detailed information.

**Expected Sections** (based on route logic):
- User information (username, email, location, join date)
- Preferences display
- Recent activities table (last 10)
- Active alerts list
- Recent weather data for user's location
- Add Alert button
- Edit Profile button

---

#### `templates/weather_display.html`
**Purpose**: Dedicated page for detailed weather visualization.

**Expected Features**:
- Large weather map
- Multiple location cards
- Detailed charts (temperature trends, humidity, pressure)
- Historical data graphs
- Search for new locations

---

#### `templates/alerts.html`
**Purpose**: Alert management and configuration page.

**Expected Features**:
- All active alerts list
- Alert severity filters
- Create new alert button
- Edit/delete alert actions
- Alert trigger conditions display

---

#### `templates/recommendations.html`
**Purpose**: AI-driven activity recommendations based on weather.

**Expected Features**:
- Current weather-based recommendations
- Activity suitability scores
- Best time suggestions
- Alternative activity suggestions
- User preference matching

---

## 🌐 API Integration

### OpenWeatherMap API

**Configuration**:
```python
OPENWEATHER_API_KEY = os.environ.get('OPENWEATHER_API_KEY', 'demo_key')
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
```

**Function: `fetch_live_weather(location)`**

**API Request**:
```python
params = {
    'q': location,           # City name (e.g., "London")
    'appid': OPENWEATHER_API_KEY,
    'units': 'metric'        # Celsius temperature
}
response = requests.get(OPENWEATHER_URL, params=params, timeout=10)
```

**Response Mapping**:
```python
{
    'temperature': data['main']['temp'],           # Current temp (°C)
    'humidity': data['main']['humidity'],          # Humidity (%)
    'pressure': data['main']['pressure'],          # Pressure (hPa)
    'wind_speed': data['wind']['speed'],           # Wind (km/h)
    'condition': data['weather'][0]['main'],       # Condition text
    'location': location,
    'timestamp': datetime.now().isoformat()
}
```

**Demo Mode Fallback**:
When API key is unavailable or request fails:
- Uses hash-based pseudo-random values
- Consistent data for same location
- Temperature range: 15-35°C
- Conditions: Sunny, Cloudy, Rainy

**Getting API Key**:
1. Visit [OpenWeatherMap](https://openweathermap.org/api)
2. Sign up for free account
3. Navigate to API Keys section
4. Copy your API key
5. Add to `.env` file: `OPENWEATHER_API_KEY=your_key_here`

**Free Tier Limits**:
- 60 calls/minute
- 1,000,000 calls/month
- Current weather data only

---

## 🤖 AI/ML Implementation

### Model Architecture

**Algorithm**: Random Forest Regressor

**Why Random Forest?**
- Handles non-linear weather patterns
- Robust to outliers (extreme weather events)
- No need for feature scaling (though we use it for performance)
- Provides feature importance insights
- Low computational cost

**Hyperparameters**:
```python
RandomForestRegressor(
    n_estimators=50,      # Number of decision trees
    random_state=42,      # Reproducible results
    max_depth=10          # Prevent overfitting
)
```

### Feature Engineering

**Input Features** (7 dimensions):
1. **temperature** (float): Current temperature in °C
2. **humidity** (float): Relative humidity (%)
3. **pressure** (float): Normalized atmospheric pressure (hPa/100)
4. **wind_speed** (float): Wind speed in km/h
5. **hour** (int): Hour of day (0-23)
6. **day_of_week** (int): Day of week (0-6, Monday=0)
7. **month** (int): Month of year (1-12)

**Target Variable**:
- Next hour's temperature (°C)

**Feature Preparation**:
```python
def prepare_features(self, historical_data):
    # Requires minimum 20 data points
    if len(historical_data) < 20:
        return None, None
    
    # Convert to pandas DataFrame
    df = pd.DataFrame(historical_data)
    
    # Create sliding window
    features = []
    targets = []
    for i in range(len(df) - 1):
        current = df.iloc[i]
        features.append([
            current['temperature'],
            current['humidity'],
            current['pressure'] / 100,  # Normalization
            current['wind_speed'],
            current['hour'],
            current['day_of_week'],
            current['month']
        ])
        targets.append(df.iloc[i + 1]['temperature'])
    
    return np.array(features), np.array(targets)
```

### Training Process

**Data Split**: 80% training, 20% testing

**Steps**:
1. **Data Validation**: Check for minimum 10 samples
2. **Feature Scaling**: StandardScaler for normalization
   - Subtracts mean
   - Divides by standard deviation
3. **Model Fitting**: Train on scaled features
4. **Evaluation**: Calculate R² score on test set
5. **Persistence**: Save model and scaler using joblib

```python
def train(self, historical_data):
    X, y = self.prepare_features(historical_data)
    
    # Scale features
    X_scaled = self.scaler.fit_transform(X)
    
    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        X_scaled, y, test_size=0.2, random_state=42
    )
    
    # Train model
    self.model.fit(X_train, y_train)
    
    # Evaluate
    score = self.model.score(X_test, y_test)
    
    # Save
    joblib.dump(self.model, MODEL_PATH)
    joblib.dump(self.scaler, SCALER_PATH)
    
    return True
```

### Prediction Process

**Real-time Prediction**:
```python
def predict(self, current_weather):
    # Extract current features
    now = datetime.now()
    features = np.array([[
        current_weather['temperature'],
        current_weather['humidity'],
        current_weather['pressure'] / 100,
        current_weather['wind_speed'],
        now.hour,
        now.weekday(),
        now.month
    ]])
    
    # Scale using fitted scaler
    features_scaled = self.scaler.transform(features)
    
    # Predict
    prediction = self.model.predict(features_scaled)[0]
    
    return {
        'predicted_temperature': round(prediction, 1),
        'confidence': 0.85,  # Simulated
        'timestamp': (now + timedelta(hours=1)).isoformat()
    }
```

### Model Retraining

**Automated Retraining**:
- Scheduled every 1 hour via APScheduler
- Uses last 168 hours (7 days) of data
- Incrementally improves accuracy with more data

```python
scheduler.add_job(
    lambda: weather_ai.train(get_historical_weather('London', 168)),
    'interval',
    hours=1
)
```

### Performance Metrics

**Expected Accuracy**:
- R² Score: 0.80-0.90 (80-90% variance explained)
- MAE: ~1.5°C (Mean Absolute Error)
- RMSE: ~2.0°C (Root Mean Square Error)

**Limitations**:
- 1-hour prediction window only
- Accuracy degrades for extreme weather events
- Requires consistent historical data collection
- Location-specific (trained per location)

---

## 🗄️ Database Schema

### SQLite Database: `smart_weather.db`

### Table 1: `users`
Stores user account information and preferences.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `user_id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique user identifier |
| `username` | TEXT | UNIQUE NOT NULL | User's display name |
| `email` | TEXT | UNIQUE NOT NULL | User's email address |
| `location` | TEXT | NOT NULL | User's primary location |
| `preferences` | TEXT | - | JSON string of user preferences |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Account creation date |

**Preferences JSON Structure**:
```json
{
    "preferred_activities": ["walking", "cycling", "reading"],
    "temperature_range": [15, 25],
    "avoid_rain": true,
    "avoid_extreme_wind": true
}
```

**Sample Data**:
```sql
INSERT INTO users (username, email, location, preferences)
VALUES ('weather_lover', 'user@weather.com', 'London', '{"preferred_activities": ["walking"]}');
```

---

### Table 2: `weather_data`
Historical weather records for all locations.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `data_id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique record identifier |
| `location` | TEXT | NOT NULL | Location name |
| `temperature` | REAL | - | Temperature in °C |
| `humidity` | REAL | - | Humidity percentage |
| `pressure` | REAL | - | Atmospheric pressure (hPa) |
| `wind_speed` | REAL | - | Wind speed (km/h) |
| `weather_condition` | TEXT | - | Weather condition (Sunny, Rainy, etc.) |
| `precipitation` | REAL | - | Precipitation amount (mm) |
| `recorded_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Data collection timestamp |

**Indexes** (Recommended):
```sql
CREATE INDEX idx_location_time ON weather_data(location, recorded_at);
```

**Sample Query**:
```sql
SELECT * FROM weather_data 
WHERE location = 'London' 
AND recorded_at >= datetime('now', '-24 hours')
ORDER BY recorded_at DESC;
```

---

### Table 3: `user_activities`
Tracks user outdoor activities and weather conditions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `activity_id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique activity identifier |
| `user_id` | INTEGER | FOREIGN KEY → users(user_id) | Associated user |
| `activity_type` | TEXT | - | Activity name (walking, cycling, etc.) |
| `weather_condition` | TEXT | - | Weather during activity |
| `duration_minutes` | INTEGER | - | Activity duration |
| `satisfaction_rating` | INTEGER | - | User rating (1-5) |
| `activity_date` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Activity timestamp |
| `notes` | TEXT | - | User notes |

**Sample Data**:
```sql
INSERT INTO user_activities (user_id, activity_type, weather_condition, duration_minutes, satisfaction_rating)
VALUES (1, 'cycling', 'Sunny', 45, 5);
```

---

### Table 4: `weather_alerts`
Custom alert configurations for users.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `alert_id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Unique alert identifier |
| `user_id` | INTEGER | FOREIGN KEY → users(user_id) | Associated user |
| `alert_type` | TEXT | - | Alert category (temperature, wind, rain) |
| `severity` | TEXT | - | Severity level (Low, Medium, High, Critical) |
| `message` | TEXT | - | Custom alert message |
| `trigger_conditions` | TEXT | - | JSON string of trigger thresholds |
| `is_active` | BOOLEAN | DEFAULT TRUE | Alert enabled status |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Alert creation date |

**Trigger Conditions JSON Structure**:
```json
{
    "temperature": 30.0,    // Trigger if temp >= 30°C
    "wind_speed": 50.0,     // Trigger if wind >= 50 km/h
    "precipitation": 10.0   // Trigger if rain >= 10mm
}
```

**Sample Data**:
```sql
INSERT INTO weather_alerts (user_id, alert_type, severity, message, trigger_conditions)
VALUES (1, 'temperature', 'High', 'Extreme heat warning!', '{"temperature": 35.0}');
```

---

### Database Relationships

```
users (1) ──< (many) user_activities
users (1) ──< (many) weather_alerts
```

---

## 📖 Usage Guide

### Starting the Application

1. **Normal Startup**:
   ```bash
   python app_clean.py
   ```
   Output:
   ```
   🚀 Initializing Smart Weather System...
   ✅ Database initialized with sample data!
   🤖 Training new AI model...
   ✅ AI Model trained successfully! R² score: 0.850
   ⏰ Scheduler started
   ✅ Smart Weather System ready!
   🌐 Starting Flask-SocketIO server on port 8000...
   ```

2. **Access Dashboard**:
   - Open browser to `http://localhost:8000`
   - See real-time connection indicator (green dot)
   - View current weather stats

### Using the Dashboard

**Real-time Features**:
- Clock updates every second
- Weather data refreshes every 2 minutes automatically
- Temperature values fluctuate slightly to show "live" updates
- AI status shows "Trained" when model is ready

**Navigation**:
- **Dashboard**: Overview with stats and recent data
- **Users**: Manage user accounts
- **Weather**: Detailed weather display

### Managing Users

1. **View Users**:
   - Click "Users" in navigation
   - See list with activity/alert counts

2. **Add New User**:
   - Click "Add User" button
   - Fill in form:
     - Username (unique)
     - Email (unique)
     - Location (city name)
     - Select preferred activities
     - Set temperature preferences
     - Enable/disable rain avoidance
   - Submit form
   - Receive success notification via WebSocket

3. **View User Profile**:
   - Click on username in user list
   - See:
     - User details
     - Recent activities
     - Active alerts
     - Weather for user's location

### Creating Alerts

1. Navigate to user profile
2. Scroll to "Alerts" section
3. Click "Add Alert"
4. Configure:
   - **Alert Type**: temperature, wind, precipitation
   - **Severity**: Low, Medium, High, Critical
   - **Custom Message**: Personal alert text
   - **Threshold**: Temperature value
5. Submit
6. Alert appears in user's active alerts

### Monitoring Weather

**Current Weather Card**:
- Shows live temperature (color-coded)
- Displays humidity, wind, pressure
- Shows AI prediction for next hour
- Confidence percentage

**24-Hour Forecast**:
- Hourly breakdown
- Icons for each time slot
- Temperature predictions

**Historical Data Table**:
- Last 3 recorded weather conditions
- Location, temp, condition, time

### WebSocket Real-time Events

**Client → Server**:
```javascript
// Request weather for specific location
socket.emit('request_weather', { location: 'London' });

// Trigger AI model training
socket.emit('request_ai_training');
```

**Server → Client**:
```javascript
// Automatic weather updates (every 2 minutes)
socket.on('weather_update', (data) => {
    console.log(data.location, data.data, data.prediction);
});

// Connection acknowledgment
socket.on('connection_response', (data) => {
    console.log(data.message);
});

// AI training completion
socket.on('ai_training_complete', (data) => {
    console.log('Score:', data.score);
});

// New user added
socket.on('user_added', (data) => {
    console.log('New user:', data.username);
});

// New alert created
socket.on('alert_created', (data) => {
    console.log('Alert:', data.message);
});
```

### Viewing Logs

**Application Logs** (printed to console):
```
📍 Weather updated for London: 20.5°C
✅ Client connected: <socket_id>
🤖 Training new AI model...
✅ AI Model trained successfully! R² score: 0.850
```

**Log Symbols**:
- 🚀 = Initialization
- ✅ = Success
- ❌ = Error
- 📍 = Location update
- 🤖 = AI operation
- ⏰ = Scheduler event
- 🌐 = Server startup

---

## ⚙️ Configuration

### Environment Variables

Create `.env` file in project root:

```env
# Flask Configuration
SECRET_KEY=your_super_secret_key_change_this
DEBUG=False

# OpenWeatherMap API
OPENWEATHER_API_KEY=your_api_key_here

# Database (optional)
DATABASE_PATH=smart_weather.db

# Server Configuration
HOST=0.0.0.0
PORT=8000

# AI Model Configuration
MODEL_RETRAIN_INTERVAL=3600  # seconds (1 hour)
MIN_TRAINING_DATA=20         # minimum samples
```

### Application Constants

**In `app_clean.py`**:

```python
# Weather update frequency (seconds)
WEATHER_UPDATE_INTERVAL = 120  # 2 minutes

# AI retraining frequency (hours)
AI_RETRAIN_INTERVAL = 1  # 1 hour

# Historical data window (hours)
HISTORICAL_DATA_HOURS = 168  # 7 days

# Model parameters
MODEL_N_ESTIMATORS = 50
MODEL_MAX_DEPTH = 10
MODEL_TEST_SIZE = 0.2

# API timeout (seconds)
API_TIMEOUT = 10
```

### Customizing the UI

**Change Theme Colors** (`static/css/style.css`):
```css
:root {
    --primary: #2563eb;        /* Change to your brand color */
    --dark-bg: #0f172a;        /* Change background */
    --success: #10b981;        /* Success color */
}
```

**Modify Animations**:
```css
/* Disable animations for better performance */
.card-futuristic:hover {
    transform: none;  /* Remove lift effect */
}

@keyframes float {
    /* Modify icon float animation */
}
```

### Database Customization

**Add New Fields to Users Table**:
```sql
ALTER TABLE users ADD COLUMN phone TEXT;
ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'UTC';
```

**Create Custom Indexes**:
```sql
CREATE INDEX idx_user_location ON users(location);
CREATE INDEX idx_weather_location_date ON weather_data(location, recorded_at DESC);
```

---

## 🐛 Troubleshooting

### Common Issues

#### 1. **"Module not found" errors**

**Problem**: Missing Python packages

**Solution**:
```bash
pip install flask flask-socketio eventlet requests python-dotenv apscheduler
pip install scikit-learn numpy pandas joblib
```

#### 2. **"Address already in use" error**

**Problem**: Port 8000 is occupied

**Solutions**:
- Change port in `app_clean.py`:
  ```python
  socketio.run(app, host='0.0.0.0', port=5000, debug=True)
  ```
- Kill existing process:
  ```bash
  # Windows
  netstat -ano | findstr :8000
  taskkill /PID <PID> /F
  ```

#### 3. **WebSocket connection failed**

**Problem**: Client can't connect to SocketIO

**Checks**:
- Verify Socket.IO client library is loaded:
  ```html
  <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
  ```
- Check browser console for errors
- Ensure CORS is enabled (already configured in app)

#### 4. **AI model not training**

**Problem**: Insufficient data or errors in training

**Diagnostics**:
```python
# Check training data
historical_data = get_historical_weather('London', 168)
print(f"Data points: {len(historical_data)}")  # Should be > 20

# Manual training
from app_clean import weather_ai, get_historical_weather
data = get_historical_weather('London', 168)
success = weather_ai.train(data)
print(f"Training success: {success}")
```

#### 5. **Database locked error**

**Problem**: Multiple threads accessing SQLite

**Solution**: Already handled with `check_same_thread=False`, but if persists:
```python
# Add timeout
conn = sqlite3.connect('smart_weather.db', timeout=10.0)
```

#### 6. **API rate limit exceeded**

**Problem**: Too many OpenWeatherMap requests

**Solution**:
- Increase update interval:
  ```python
  scheduler.add_job(update_weather_data, 'interval', minutes=5)  # Instead of 2
  ```
- Reduce number of tracked locations

#### 7. **Static files not loading**

**Problem**: 404 errors for CSS/JS

**Checks**:
- Verify file paths match exactly:
  ```
  static/css/style.css    ✅
  static/CSS/style.css    ❌ (case-sensitive on Linux)
  ```
- Check Flask static folder configuration:
  ```python
  app = Flask(__name__, static_folder='static')
  ```

### Debug Mode

**Enable detailed logging**:
```python
import logging
logging.basicConfig(level=logging.DEBUG)

# In Flask routes
app.logger.debug('Custom debug message')
```

**Disable real-time features for testing**:
```python
# Comment out scheduler start
# scheduler.start()

# Disable WebSocket
# socketio.run() → app.run()
```

### Performance Issues

**Reduce animation complexity**:
```css
/* In style.css, disable animations */
* {
    animation: none !important;
    transition: none !important;
}
```

**Optimize database queries**:
```python
# Add LIMIT to all queries
cursor.execute('SELECT * FROM weather_data ORDER BY recorded_at DESC LIMIT 100')
```

**Reduce model complexity**:
```python
# Fewer trees
self.model = RandomForestRegressor(n_estimators=20, max_depth=5)
```

---

## 🤝 Contributing

### Development Setup

1. Fork the repository
2. Create a virtual environment:
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install scikit-learn numpy pandas joblib
   ```
4. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

### Code Style Guidelines

- **Python**: Follow PEP 8
  - 4 spaces indentation
  - Max line length: 100 characters
  - Docstrings for all functions
- **JavaScript**: Use ES6+ syntax
  - Semicolons required
  - camelCase naming
- **HTML/CSS**: 2 spaces indentation

### Adding New Features

**Example: Add Email Notifications**

1. **Install library**:
   ```bash
   pip install flask-mail
   ```

2. **Add to `requirements.txt`**:
   ```
   Flask-Mail==0.9.1
   ```

3. **Configure in `app_clean.py`**:
   ```python
   from flask_mail import Mail, Message
   
   app.config['MAIL_SERVER'] = 'smtp.gmail.com'
   app.config['MAIL_PORT'] = 587
   app.config['MAIL_USE_TLS'] = True
   app.config['MAIL_USERNAME'] = os.environ.get('EMAIL_USER')
   app.config['MAIL_PASSWORD'] = os.environ.get('EMAIL_PASS')
   
   mail = Mail(app)
   
   def send_alert_email(user_email, alert_message):
       msg = Message('Weather Alert!',
                     sender='noreply@smartweather.com',
                     recipients=[user_email])
       msg.body = alert_message
       mail.send(msg)
   ```

4. **Update alert creation**:
   ```python
   @app.route('/add_alert/<int:user_id>', methods=['POST'])
   def add_alert(user_id):
       # ... existing code ...
       
       # Get user email
       user = conn.execute('SELECT email FROM users WHERE user_id = ?', (user_id,)).fetchone()
       
       # Send confirmation email
       send_alert_email(user['email'], f"Alert created: {message}")
   ```

### Testing

**Run manual tests**:
```bash
# Test database connection
python -c "from app_clean import get_db_connection; conn = get_db_connection(); print('DB OK' if conn else 'DB FAIL')"

# Test API connection
python -c "from app_clean import fetch_live_weather; print(fetch_live_weather('London'))"

# Test AI model
python -c "from app_clean import weather_ai; print('Trained' if weather_ai.is_trained else 'Not trained')"
```

### Pull Request Process

1. Update README.md with new features
2. Add code comments
3. Test thoroughly
4. Create PR with description:
   - What changed
   - Why changed
   - How to test

---

## 📄 License

This project is created for educational purposes as part of a 3rd semester PBL (Project-Based Learning) assignment.

**MIT License** (Recommended)

```
MIT License

Copyright (c) 2024 Smart Weather System

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 📞 Support & Contact

### Getting Help

- Check [Troubleshooting](#-troubleshooting) section
- Review code comments in source files
- Check browser console for JavaScript errors
- Review server logs for Python errors

### Future Enhancements

**Planned Features**:
- [ ] Email/SMS alert notifications
- [ ] Multi-location comparison view
- [ ] Export data to CSV/JSON
- [ ] User authentication system
- [ ] Mobile responsive improvements
- [ ] Weather radar maps integration
- [ ] Historical trend analysis charts
- [ ] Social sharing features
- [ ] Webhook integrations
- [ ] Docker containerization

**AI Improvements**:
- [ ] Multi-hour predictions (24h, 48h)
- [ ] Precipitation prediction
- [ ] Extreme weather event detection
- [ ] Ensemble model combining multiple algorithms
- [ ] Deep learning models (LSTM for time series)

---

## 🎓 Educational Context

This project demonstrates:
- **Full-stack web development** with Flask
- **Real-time communication** using WebSockets
- **Machine learning integration** in web apps
- **Database design** and SQL operations
- **API integration** with external services
- **Responsive UI/UX design** with modern frameworks
- **Background task scheduling** for automation
- **Data visualization** and dashboards
- **Software architecture** and project organization

**Learning Objectives Achieved**:
✅ Building RESTful web applications
✅ Implementing real-time features
✅ Applying machine learning algorithms
✅ Working with databases
✅ Integrating third-party APIs
✅ Creating responsive user interfaces
✅ Managing application state
✅ Error handling and debugging
✅ Code documentation and commenting

---

## 📊 Project Statistics

- **Total Lines of Code**: ~1,650
  - Python: ~680 (app_clean.py)
  - JavaScript: ~405 (script.js)
  - CSS: ~281 (style.css)
  - HTML: ~284 (all templates combined)
- **Database Tables**: 4
- **API Endpoints**: 9
- **WebSocket Events**: 6
- **ML Features**: 7
- **UI Components**: 15+

---

## 🙏 Acknowledgments

- **OpenWeatherMap** for weather data API
- **Bootstrap Team** for responsive framework
- **Font Awesome** for icon library
- **Flask Community** for excellent documentation
- **Scikit-learn** for machine learning tools
- **Socket.IO** for real-time communication

---

**Built with ❤️ for 3rd Semester PBL Project**

*Last Updated: December 2024*
