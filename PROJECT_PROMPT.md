# Smart Weather System — Project Definition & Context Prompt

> **Project:** Smart Weather System — Multi-Hazard Early Warning & Precision Agriculture Platform
> **Entry point:** `start_app.py` (Flask dev server on `http://localhost:8000`)
> **Runtime:** `.venv\Scripts\python` (Python 3.11.9, Windows)

---

## 1. Mission

A unified multi-hazard early-warning and precision-agriculture platform that:
1. Fetches real-time weather & satellite imagery from the **Open-Meteo API** (free, no API key needed).
2. Renders **3D terrain + wind-particle visualizations** and **satellite-derived agronomic indices** (NDVI, SMI, canopy-stress) on interactive Leaflet maps.
3. Delivers **hyper-local crop-risk forecasts** (drought, flood, heat, pest) and **plant-disease diagnosis** from uploaded leaf images (PyTorch CNN).
4. Provides **Urdu/English i18n**, **TOTP 2FA authentication**, **Twilio SMS/WhatsApp** alerts, and a **role-based admin panel**.

---

## 2. Architecture

```
┌─────────────────┐      ┌──────────────────┐      ┌────────────────────┐
│   React/Vue SPA │      │   Leaflet Maps   │      │  Wind Particles    │
│   (admin panel) │◄────►│  + 3D Terrain    │◄────►│  Canvas Animation  │
└─────────────────┘      └──────────────────┘      └────────────────────┘
        │                         │                          │
        ▼                         ▼                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Flask App  (app_clean.py)  ◄──  initialize_app()                      │
│  ├─ Routes                ├─ API endpoints                              │
│  ├─ Auth (auth.py / totp) ├─ WebSocket (flask-socketio / eventlet)      │
│  ├─ Scheduler (APScheduler)├─ SQLite (smart_weather.db)  OR Supabase   │
│  └─ Templates (Jinja2)    └─ MQTT (paho-mqtt, mosquitto)                │
└────────────────────────────────────────────────────────────────────────┘
        │                         │                          │
        ▼                         ▼                          ▼
┌──────────────┐   ┌──────────────────────────┐  ┌──────────────────────┐
│ Open-Meteo   │   │ GeoVis Satellite         │  │ Plant-Disease CNN   │
│ (weather +   │   │ Analyzer (geovis_satellite│  │ (torch/torchvision)  │
│  satellite)  │   │  .py)  — NDVI/SMI/stress  │  │  classify_leaf()    │
└──────────────┘   └──────────────────────────┘  └──────────────────────┘
```

### Key Design Principles
- **Offline-first with graceful degradation:** If live API calls fail, the app falls back to cached/GeoVis synthetic data (`connectivity_state="CACHE"`).
- **Server-side geocoding:** Farm coordinates are resolved server-side via Open-Meteo Geocoding API before serving GeoJSON to the client (avoids exposing API keys in browser).
- **Defense-in-depth security:** HttpOnly + SameSite=Lax cookies, session timeouts, password hashing, TOTP 2FA, Supabase Service Role on backend only.
- **i18n:** All UI strings externalized to `static/i18n/{en,ur}.json`; dot-notation key resolution via `window.SWSI18n.t()`.

---

## 3. Directory Structure

```
Smart_weather_system/
├── app_clean.py                 # MAIN Flask app — all routes, scheduler, satellite wiring
├── api_routes.py                # Agri/weather/alert API blueprint (/api/agri/*, /api/weather/*)
├── auth.py                      # Login, signup, session management, password hashing
├── totp_auth.py                 # TOTP 2FA setup & verification (pyotp, qrcode)
├── admin_routes.py              # Admin panel routes (role-based, RBAC)
├── admin_data.py                # Admin data layer
├── notifications.py             # Twilio SMS/WhatsApp + local SMS gateway fallback
├── multi_hazard.py              # Risk fusion (drought/flood/heat/pest) + Urdu SMS alerts
├── geovis_satellite.py          # GeoVisSatelliteAnalyzer — satellite NDVI/SMI/canopy-stress
├── openmeteo_integration.py     # fetch_satellite_data(), geocode_location(), REQUEST_TIMEOUT=10
├── disease_detection.py         # Plant disease CNN inference (torch CNN)
├── plant_disease_model.py       # Model architecture + loader
├── crop_database.py             # Crop profiles, thresholds
├── crop_suitability.py          # Crop suitability scoring
├── yield_estimator.py           # Yield prediction
├── crop_failure_predictor.py    # Crop failure probability
├── recommendation_engine.py     # Agronomic recommendations
├── sensor_ingestion.py          # MQTT sensor data ingestion
├── sensor_simulation.py         # Simulated sensor data for development
├── agri_sockets.py              # WebSocket (flask-socketio) for live telemetry
├── alerts.py                    # Alert state management
├── db.py                        # Database abstraction (SQLite/Supabase)
├── supabase_client.py           # Supabase client wrapper
├── check_db.py                  # DB health / schema checks
├── check_routes.py              # Route diagnostics
├── edge_case_hardening.py       # Edge-case & input-sanitization hardening
├── final_check.py               # Startup validation checklist
├── integration_bridge.py        # API integration bridge
│
├── templates/                   # Jinja2 templates (30+ views)
│   ├── base.html                # Master layout (navbar, i18n, script loading order)
│   ├── satellite_map.html       # Interactive satellite map (Leaflet + wind particles)
│   ├── agri_dashboard.html       # Agriculture dashboard (radiation + satellite cards)
│   ├── dashboard.html            # Main weather dashboard
│   ├── disease_detection.html    # Leaf-image disease scanner
│   ├── agri_alerts.html          # Agri alert list
│   ├── full_weather_map.html     # Full weather map view
│   ├── login.html / signup.html / access.html
│   ├── 2fa_setup.html / 2fa_verify.html   # TOTP 2FA flows
│   ├── admin/                    # Admin panel templates (role-gated)
│   │   ├── _layout.html, _macros.html
│   │   ├── login.html, dashboard.html
│   │   ├── users.html, user_detail.html, list.html
│   │   ├── alerts.html, analytics.html, settings.html
│   │   ├── active_users.html, add_user.html, error.html
│   ├── farms.html, farm_detail.html, field_detail.html, add_farm.html, add_field.html
│   ├── recommendations.html, profile.html, alerts.html, weather_display.html
│   └── user_management.html
│
├── static/
│   ├── css/
│   │   ├── style.css             # Core + base styles
│   │   ├── agri-theme.css        # Agriculture-specific theming
│   │   └── admin.css             # Admin panel styles
│   ├── js/
│   │   ├── script.js             # Core client JS (UI interactions, API helpers)
│   │   ├── map.js                # Leaflet map + wind-particle canvas animation (NEW, rewritten)
│   │   ├── i18n.js               # window.SWSI18n — nested-key translation resolver (NEW)
│   │   ├── admin.js              # Admin panel JS
│   │   ├── heartbeat.js          # Sensor heartbeat monitor
│   │   └── src/main.js           # (vite build entry, if applicable)
│   └── i18n/
│       ├── en.json               # English translations (nested map.*/alerts.* keys)
│       └── ur.json               # Urdu translations (RTL)
│
├── models/
│   ├── __init__.py
│   ├── plant_disease_classes.json       # Class label mapping
│   ├── plant_disease_class_weights.json # Training class weights
│   ├── plant_disease_metadata.json      # Model metadata
│   └── plant_disease_model.pth          # Binary — PyTorch weights (GITIGNORED)
│
├── scripts/                        # Utility scripts
│   ├── create_admin.py             # Seed first admin user
│   └── migrate_sqlite_to_supabase.py
│   ├── check_supabase.py
│   ├── clean.js, run-python.js
│
├── data/ & data_processing.py      # Dataset loading & processing pipeline
├── preprocessing/                  # CDS (Climate Data Store) download scripts
├── feature_engineering.py          # ML feature engineering
├── train_disease_model.py          # CNN training entry point
├── stacking_ensemble.py            # Ensemble model
├── xai_explainability.py           # Explainable AI (SHAP/LIME)
├── confidence_calibration.py       # Model confidence calibration
├── entropy_utils.py                # Uncertainty quantification
├── feedback_store.py               # User feedback loop
├── evaluation.py                   # Model evaluation
├── experiment_tracking.py          # ML experiment tracking
├── pipeline_config.py              # Pipeline configuration
│
├── tests/                          # Pytest test suite (14 test files)
│   ├── __init__.py / fake_db.py
│   ├── test_wind_i18n_sms.py      # Satellite map i18n + wind particles + SMS gating (3 tests)
│   ├── test_admin_security.py     # Admin auth security
│   ├── test_auth_2fa.py           # TOTP 2FA flows
│   ├── test_multi_hazard_system.py
│   ├── test_disease_detection_dl.py
│   ├── test_edge_case_hardening.py
│   ├── test_agri_advisor_v6.py
│   ├── test_backend_gaps_and_sockets.py
│   ├── test_plant_disease_model.py
│   └── test_admin_*.py / test_login_*.py
│
├── supabase/                       # Supabase schema + seed data
│   ├── schema.sql
│   └── seed.sql
├── vite.config.js                  # Frontend build config
├── package.json / package-lock.json
├── requirements.txt                # Python dependencies
├── .env.example                     # Environment template (DO NOT COMMIT .env)
├── .gitignore
├── README.md
├── start_app.py                    # ← RUN: .venv\Scripts\python start_app.py
├── start_all.py
└── run-production.py
```

---

## 4. Core Features & How They Work

### 4.1 Satellite Data Pipeline (the fix)
```
User visits /agri  ──►  app_clean.py route handler
  │
  ├─ spawn _sat_thread  ──►  _safe_satellite_analyze(site)
  │                            ├─ analyze_site(site, "ONLINE")  [geovis_satellite.py]
  │                            │     └─ Live Open-Meteo satellite radiation → NDVI/SMI/stress
  │                            └─ fallback: CACHE mode
  │  ◄── thread.join(timeout=12.0)  ← MUST exceed REQUEST_TIMEOUT(10)
  │
  ├─ _safe_satellite_analyze()
  │   └─ returns dict with: .available, .data.ndvi, .data.soil_moisture_index,
  │      .data.canopy_stress, .data.status, .data.disclaimer, .openmeteo_satellite
  │
  ├─ sat_radiation = satellite_data.get("openmeteo_satellite")
  └─ render agri_dashboard.html  ──►  satellite card  +  radiation card
```

**Key fix:** Previously `_safe_satellite_analyze` bypassed `analyze_site()` and read `cached_imagery` directly — always showing stale cached data. Now it calls `analyze_site(site, "ONLINE")` to fetch live satellite data, with a 12-second thread timeout (vs. Open-Meteo's 10s request timeout).

### 4.2 Interactive Satellite Map (`/agri/map`)
- **Leaflet** map with **3D terrain** (ESRI/CartoDB tiles, `crossorigin=""` NO integrity hashes).
- **Wind-particle canvas animation**: `requestAnimationFrame` loop, `globalAlpha` + `globalCompositeOperation = "destination-in"` for fading trails; `MAX_PARTICLES = 3000`, `MAX_AGE = 90`.
- **Farm GeoJSON:** Fetched dynamically from `/api/agri/map-data` (server-side geocoding via `geocode_location`), rendered with `L.geoJSON`.
- **Wind grid:** `/api/weather/grid?latitude=X&longitude=Y` using `map.getCenter()`.
- **User geolocation:** `navigator.geolocation.watchPosition` live marker + `locateMeBtn`.
- **Satellite live-data panel:** `#satelliteLiveData` populated from `/api/agri/openmeteo`.

### 4.3 Plant Disease Detection (CNN)
- PyTorch CNN (`plant_disease_model.py`) classifies leaf images into 38+ disease classes.
- `/api/agri/disease-diagnose` — accepts image upload, returns diagnosis + confidence + treatment.
- Model weights in `models/plant_disease_model.pth` (gitignored, loaded at runtime).

### 4.4 Multi-Hazard Risk Engine
- `multi_hazard.py` fuses weather forecasts → drought / flood / heat / pest risk scores.
- Risk levels: green < yellow < orange < red.
- **Urdu SMS alerts** gated: only sends when `alert_required` AND `overall_risk_level ∈ {"orange", "red"}`, sliced to 160 chars.

### 4.5 Authentication & Admin
- `auth.py` — session-based auth with password hashing.
- `totp_auth.py` — TOTP 2FA setup/verification (pyotp + qrcode).
- `admin_routes.py` / `admin_data.py` — RBAC admin panel at `/admin/`.
- `notifications.py` — Twilio SMS/WhatsApp; falls back to local SMS gateway; degrades to console logging if unconfigured.

### 4.6 i18n (Internationalization)
- `static/js/i18n.js` → `window.SWSI18n` with `translate(key)` supporting **dot-notation** nested keys (e.g. `map.wind_particles`).
- `static/i18n/en.json` + `ur.json` — flat top-level keys + nested `map.*` and `alerts.*` objects.
- Language toggle button in `base.html` navbar (English ↔ اردو).

---

## 5. API Endpoints (key routes)

| Method | Path | Backend Location | Purpose |
|--------|------|-----------------|---------|
| GET | `/` | `app_clean.py` | Landing page |
| GET | `/agri` | `app_clean.py` | Agriculture dashboard (satellite + radiation cards) |
| GET | `/agri/map` | `app_clean.py` | Interactive satellite/wind map page |
| GET | `/api/agri/map-data` | `app_clean.py` | GeoJSON of farms (server-side geocoded) |
| GET | `/api/agri/openmeteo` | `api_routes.py` | Live satellite radiation data |
| GET | `/api/weather/grid?lat=&lon=` | `app_clean.py` | Open-Meteo wind u/v grid |
| GET | `/api/agri/analyze` | `api_routes.py` | Agri analysis endpoint |
| GET | `/api/agri/disease-diagnose` | `api_routes.py` | Leaf disease diagnosis (POST image) |
| GET | `/api/hazards/predict` | `app_clean.py` | Multi-hazard risk prediction |
| GET | `/api/agri/status` | `api_routes.py` | System status |
| POST | `/access` | `auth.py` | Login/signup |
| GET | `/admin/` | `admin_routes.py` | Admin panel (RBAC) |

---

## 6. Data Sources

| Source | Purpose | Config |
|--------|---------|--------|
| Open-Meteo API | Weather forecast, wind grids, satellite radiation | `WEATHER_PROVIDER=openmeteo`, no API key needed |
| Open-Meteo Geocoding API | Lat/lon from farm location names | Server-side via `geocode_location()` |
| Supabase / SQLite | Farm/field data, user accounts, sessions | `SUPABASE_URL`, `SUPABASE_ANON_KEY`, local `smart_weather.db` |
| MQTT (mosquitto) | Real-time sensor telemetry | Local broker, `paho-mqtt` |
| Twilio | SMS/WhatsApp alerts | `TWILIO_*`, optional |
| PyTorch model | Plant disease image classification | `models/plant_disease_model.pth` |

---

## 7. Environment Configuration (`.env`)

| Key | Description | Default |
|-----|-------------|---------|
| `SECRET_KEY` | Flask session secret (≥32 chars) | *(empty, app refuses if weak)* |
| `DEBUG` | Debug mode | `True` |
| `OPENWEATHER_API_KEY` | OpenWeatherMap fallback | `demo_key` |
| `WEATHER_PROVIDER` | `openmeteo` \| `openweathermap` | `openmeteo` |
| `SENSOR_MODE` | `simulation` \| `real` | `simulation` |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | Supabase (optional, can use SQLite) | *(see .env.example)* |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | SMS provider | *(empty — console fallback)* |
| `TWO_FACTOR_ISSUER` | TOTP label | `Smart AgriWeather` |
| `NOTIFICATION_ENABLED` | Toggle all outgoing alerts | `true` |
| `AGRI_API_KEY` | Agri API auth | `agri_dev_key_2024` |

---

## 8. Development Workflow

### Run the app
```powershell
.venv\Scripts\python start_app.py
# → http://localhost:8000
```

### Run tests
```powershell
.venv\Scripts\python -m pytest tests/ -v
# Key satellite/i18n test:
.venv\Scripts\python -m pytest tests/test_wind_i18n_sms.py -v   # 3/3 passing
```

### Git (note: `core.fsmonitor=true` in global gitconfig causes hangs — use `-c core.fsmonitor=false` or the local override is already set)
```powershell
git -c core.fsmonitor=false add .
git -c core.fsmonitor=false commit -m "..."
git -c core.fsmonitor=false push origin main
```

### Key files for satellite data work
- **`app_clean.py`** — `_safe_satellite_analyze()` (lines ~2083-2260), `/agri/map` route, `/api/agri/map-data`, `/api/weather/grid`
- **`openmeteo_integration.py`** — `fetch_satellite_data()` (line 116, REQUEST_TIMEOUT=10), `geocode_location()` (line 64)
- **`geovis_satellite.py`** — `GeoVisSatelliteAnalyzer.analyze_site()` (line 39)
- **`static/js/map.js`** — Full map + wind particle engine (MAX_PARTICLES=3000, MAX_AGE=90)
- **`static/i18n/{en,ur}.json`** — Nested translation keys
- **`static/js/i18n.js`** — `window.SWSI18n.t()` resolver
- **`templates/satellite_map.html`** — Map page with geolocation + live-data panel
- **`templates/agri_dashboard.html`** — Dashboard cards

---

## 9. Git State

- **Branch:** `main`  ✅ synced with `origin/main`
- **HEAD:** `2eec3d0` — "Merge remote main into main"
- **Recent commits:**
  - `2eec3d0` Merge remote main (2FA, admin panel, color scheme, full_weather_map) + satellite fix
  - `2c5adca` fix: satellite data not displaying on agri dashboard and map
  - `dbe7a15` Merge master into main (remote)
- **Known exclusion:** `.env`, `smart_weather.db`, `cache/`, `models/plant_disease_model.pth`, `__pycache__/`, `mosquitto/logs/`, `.poolside/`, `.codebase-memory/` are all gitignored.
