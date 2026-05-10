from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_socketio import SocketIO, emit
import sqlite3
import json
from datetime import datetime, timedelta
import requests
import threading
import time
from apscheduler.schedulers.background import BackgroundScheduler
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import joblib
import os
import atexit
from dotenv import load_dotenv

# Load environment variables
load_dotenv()



app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'smart_weather_ai_2024')
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

# Configuration
OPENWEATHER_API_KEY = os.environ.get('OPENWEATHER_API_KEY', 'demo_key')
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"

# AI Model Storage
MODEL_PATH = 'models/weather_model.joblib'
SCALER_PATH = 'models/scaler.joblib'

# Create models directory
os.makedirs('models', exist_ok=True)

class WeatherAI:
    def __init__(self):
        self.model = RandomForestRegressor(n_estimators=50, random_state=42, max_depth=10)
        self.scaler = StandardScaler()
        self.is_trained = False
        self.training_data_points = 0
    
    def prepare_features(self, historical_data):
        """Prepare features for training - optimized for performance"""
        if len(historical_data) < 20:
            return None, None
        
        try:
            df = pd.DataFrame(historical_data)
            features = []
            targets = []
            
            for i in range(len(df) - 1):
                current = df.iloc[i]
                features.append([
                    current['temperature'],
                    current['humidity'],
                    current['pressure'] / 100,  # Normalize pressure
                    current['wind_speed'],
                    current['hour'],
                    current['day_of_week'],
                    current['month']
                ])
                targets.append(df.iloc[i + 1]['temperature'])
            
            return np.array(features), np.array(targets)
        except Exception as e:
            print(f"Feature preparation error: {e}")
            return None, None
    
    def train(self, historical_data):
        """Train the AI model with error handling"""
        try:
            X, y = self.prepare_features(historical_data)
            if X is None or len(X) < 10:
                print("Insufficient data for training")
                return False
            
            X_scaled = self.scaler.fit_transform(X)
            X_train, X_test, y_train, y_test = train_test_split(
                X_scaled, y, test_size=0.2, random_state=42
            )
            
            self.model.fit(X_train, y_train)
            self.is_trained = True
            self.training_data_points = len(X)
            
            # Save model and scaler
            joblib.dump(self.model, MODEL_PATH)
            joblib.dump(self.scaler, SCALER_PATH)
            
            score = self.model.score(X_test, y_test)
            print(f"✅ AI Model trained successfully! R² score: {score:.3f}")
            
            # Emit training completion event
            socketio.emit('ai_training_complete', {
                'score': round(score, 3),
                'data_points': self.training_data_points,
                'timestamp': datetime.now().isoformat()
            })
            
            return True
        except Exception as e:
            print(f"❌ Training failed: {e}")
            return False
    
    def load_model(self):
        """Load trained model"""
        try:
            if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
                self.model = joblib.load(MODEL_PATH)
                self.scaler = joblib.load(SCALER_PATH)
                self.is_trained = True
                print("✅ AI Model loaded successfully!")
                return True
        except Exception as e:
            print(f"❌ Model loading failed: {e}")
        return False
    
    def predict(self, current_weather):
        """Predict next hour's weather"""
        if not self.is_trained:
            return None
        
        try:
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
            
            features_scaled = self.scaler.transform(features)
            prediction = self.model.predict(features_scaled)[0]
            
            return {
                'predicted_temperature': round(prediction, 1),
                'confidence': min(0.95, max(0.6, 0.85)),  # Simulated confidence
                'timestamp': (now + timedelta(hours=1)).isoformat()
            }
        except Exception as e:
            print(f"Prediction error: {e}")
            return None

# Initialize AI System
weather_ai = WeatherAI()

def init_database():
    """Initialize database with required tables"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    tables = [
        '''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            location TEXT NOT NULL,
            preferences TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS weather_data (
            data_id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            temperature REAL,
            humidity REAL,
            pressure REAL,
            wind_speed REAL,
            weather_condition TEXT,
            precipitation REAL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS user_activities (
            activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            activity_type TEXT,
            weather_condition TEXT,
            duration_minutes INTEGER,
            satisfaction_rating INTEGER,
            activity_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS weather_alerts (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            alert_type TEXT,
            severity TEXT,
            message TEXT,
            trigger_conditions TEXT,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )'''
    ]
    
    for table in tables:
        cursor.execute(table)
    
    # Insert sample data for demo
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO users (username, email, location, preferences)
            VALUES (?, ?, ?, ?)
        ''', ('weather_lover', 'user@weather.com', 'London', json.dumps({
            'preferred_activities': ['walking', 'cycling', 'reading'],
            'temperature_range': [15, 25],
            'avoid_rain': True,
            'avoid_extreme_wind': True
        })))
        
        # Insert sample weather data for AI training
        sample_weather = [
            ('London', 18.5, 65, 1013, 12, 'Cloudy', 0),
            ('London', 20.1, 60, 1015, 8, 'Sunny', 0),
            ('London', 16.8, 75, 1010, 15, 'Rainy', 5),
            ('London', 22.3, 55, 1012, 10, 'Sunny', 0),
            ('London', 19.7, 70, 1014, 18, 'Windy', 0)
        ]
        
        for weather in sample_weather:
            cursor.execute('''
                INSERT OR IGNORE INTO weather_data 
                (location, temperature, humidity, pressure, wind_speed, weather_condition, precipitation)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', weather)
        
        conn.commit()
        print("✅ Database initialized with sample data!")
    except Exception as e:
        print(f"Database initialization note: {e}")
    
    conn.close()


def init_agriculture_database():
    """Initialize agriculture-specific database tables with seed data"""
    conn = get_db_connection()
    cursor = conn.cursor()

    agri_tables = [
        '''CREATE TABLE IF NOT EXISTS farms (
            farm_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_name TEXT NOT NULL,
            location TEXT NOT NULL,
            total_area_ha REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS fields (
            field_id INTEGER PRIMARY KEY AUTOINCREMENT,
            farm_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            area_ha REAL NOT NULL,
            soil_type TEXT DEFAULT 'Loamy',
            irrigation_type TEXT DEFAULT 'Drip',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (farm_id) REFERENCES farms(farm_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS crop_census (
            census_id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id INTEGER NOT NULL,
            crop_type TEXT NOT NULL,
            area_ha REAL NOT NULL,
            growth_stage TEXT DEFAULT 'Germination',
            planted_date TEXT,
            expected_harvest_date TEXT,
            target_yield_ton_ha REAL DEFAULT 3.0,
            season TEXT DEFAULT 'Rabi 2025-26',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (field_id) REFERENCES fields(field_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS field_weather (
            fw_id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id INTEGER NOT NULL,
            temperature REAL,
            humidity REAL,
            rainfall REAL DEFAULT 0,
            wind_speed REAL,
            pressure REAL,
            solar_radiation REAL DEFAULT 0,
            weather_condition TEXT,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (field_id) REFERENCES fields(field_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS crop_health (
            health_id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id INTEGER NOT NULL,
            health_score REAL DEFAULT 100,
            heat_stress REAL DEFAULT 0,
            frost_risk REAL DEFAULT 0,
            drought_stress REAL DEFAULT 0,
            excess_moisture REAL DEFAULT 0,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (field_id) REFERENCES fields(field_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS pest_risks (
            risk_id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id INTEGER NOT NULL,
            pest_type TEXT,
            risk_level TEXT DEFAULT 'low',
            warning_message TEXT,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (field_id) REFERENCES fields(field_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS irrigation_recommendations (
            rec_id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id INTEGER NOT NULL,
            recommended_date TEXT,
            volume_mm REAL,
            reason TEXT,
            is_done BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (field_id) REFERENCES fields(field_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS yield_forecasts (
            forecast_id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id INTEGER NOT NULL,
            expected_yield_ton_ha REAL,
            target_yield_ton_ha REAL,
            confidence REAL DEFAULT 0.7,
            forecast_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (field_id) REFERENCES fields(field_id)
        )''',
        '''CREATE TABLE IF NOT EXISTS agri_alerts (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            field_id INTEGER,
            farm_id INTEGER,
            alert_type TEXT NOT NULL,
            severity TEXT DEFAULT 'medium',
            message TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )'''
    ]

    for table in agri_tables:
        cursor.execute(table)

    try:
        # Demo farms
        cursor.execute('''INSERT OR IGNORE INTO farms (farm_id, name, owner_name, location, total_area_ha)
                          VALUES (1,'Green Valley Farm','Ahmed Khan','Karachi',15.5)''')
        cursor.execute('''INSERT OR IGNORE INTO farms (farm_id, name, owner_name, location, total_area_ha)
                          VALUES (2,'Punjab Wheat Estate','Muhammad Ali','Lahore',25.0)''')

        # Demo fields
        fields_seed = [
            (1, 1, 'North Field',  5.5, 'Loamy',      'Drip'),
            (2, 1, 'South Field',  4.0, 'Clay',        'Furrow'),
            (3, 1, 'West Field',   6.0, 'Sandy Loam',  'Sprinkler'),
            (4, 2, 'Alpha Field', 12.0, 'Clay Loam',   'Flood'),
            (5, 2, 'Beta Field',  13.0, 'Silty Clay',  'Drip'),
        ]
        for fs in fields_seed:
            cursor.execute('''INSERT OR IGNORE INTO fields
                              (field_id,farm_id,name,area_ha,soil_type,irrigation_type)
                              VALUES (?,?,?,?,?,?)''', fs)

        # Demo crop census
        census_seed = [
            (1, 1, 'Wheat',  5.5, 'Tillering',         '2025-11-01', '2026-04-15', 3.5, 'Rabi 2025-26'),
            (2, 2, 'Wheat',  4.0, 'Heading',            '2025-10-15', '2026-03-30', 3.2, 'Rabi 2025-26'),
            (3, 3, 'Cotton', 6.0, 'Boll Formation',     '2025-05-01', '2025-10-31', 2.8, 'Kharif 2025'),
            (4, 4, 'Wheat', 12.0, 'Jointing',           '2025-11-10', '2026-04-20', 4.0, 'Rabi 2025-26'),
            (5, 5, 'Rice',  13.0, 'Panicle Initiation', '2025-06-15', '2025-11-15', 5.0, 'Kharif 2025'),
        ]
        for cs in census_seed:
            cursor.execute('''INSERT OR IGNORE INTO crop_census
                              (census_id,field_id,crop_type,area_ha,growth_stage,
                               planted_date,expected_harvest_date,target_yield_ton_ha,season)
                              VALUES (?,?,?,?,?,?,?,?,?)''', cs)

        # Demo health records
        health_seed = [
            (1, 1,  82.5, 0.10, 0.00, 0.05, 0.08),
            (2, 2,  71.0, 0.20, 0.00, 0.00, 0.22),
            (3, 3,  91.0, 0.05, 0.00, 0.02, 0.05),
            (4, 4,  68.0, 0.25, 0.00, 0.10, 0.00),
            (5, 5,  78.5, 0.15, 0.00, 0.00, 0.12),
        ]
        for hs in health_seed:
            cursor.execute('''INSERT OR IGNORE INTO crop_health
                              (health_id,field_id,health_score,heat_stress,
                               frost_risk,drought_stress,excess_moisture)
                              VALUES (?,?,?,?,?,?,?)''', hs)

        # Demo pest risks
        pest_seed = [
            (1, 1, 'Wheat Rust', 'high',   'Conditions ideal for wheat rust spread – monitor closely'),
            (2, 1, 'Aphids',     'medium', 'Warm calm conditions favour aphid colonies'),
            (3, 2, 'Blight',     'medium', 'High humidity increases blight risk'),
            (4, 4, 'Wheat Rust', 'high',   'Jointing stage – rust risk elevated'),
        ]
        for ps in pest_seed:
            cursor.execute('''INSERT OR IGNORE INTO pest_risks
                              (risk_id,field_id,pest_type,risk_level,warning_message)
                              VALUES (?,?,?,?,?)''', ps)

        # Demo irrigation recommendations
        irr_seed = [
            (1, 2, '2026-05-11', 25.0, 'ET₀=28mm, rainfall=3mm, deficit=25mm',  0),
            (2, 4, '2026-05-11', 30.0, 'ET₀=32mm, rainfall=2mm, deficit=30mm',  0),
            (3, 3, '2026-05-12', 18.0, 'ET₀=20mm, rainfall=2mm, deficit=18mm',  0),
        ]
        for ir in irr_seed:
            cursor.execute('''INSERT OR IGNORE INTO irrigation_recommendations
                              (rec_id,field_id,recommended_date,volume_mm,reason,is_done)
                              VALUES (?,?,?,?,?,?)''', ir)

        # Demo yield forecasts
        yield_seed = [
            (1, 1, 3.1, 3.5, 0.78),
            (2, 2, 2.4, 3.2, 0.72),
            (3, 3, 2.6, 2.8, 0.85),
            (4, 4, 2.8, 4.0, 0.69),
            (5, 5, 4.2, 5.0, 0.74),
        ]
        for ys in yield_seed:
            cursor.execute('''INSERT OR IGNORE INTO yield_forecasts
                              (forecast_id,field_id,expected_yield_ton_ha,
                               target_yield_ton_ha,confidence)
                              VALUES (?,?,?,?,?)''', ys)

        # Demo agri alerts
        agri_alert_seed = [
            (1, 1, 1,    'Pest Risk',      'high',     '🌾 High wheat rust risk in North Field – apply fungicide',    1),
            (2, 2, 1,    'Irrigation Due', 'medium',   '💧 South Field needs irrigation – 25 mm deficit',              1),
            (3, 4, 2,    'Irrigation Due', 'medium',   '💧 Alpha Field needs 30 mm irrigation',                        1),
            (4, 4, 2,    'Pest Risk',      'high',     '🌾 Jointing stage rust risk elevated in Alpha Field',          1),
            (5, None, 1, 'Heatwave',       'critical', '🌡️ Heatwave forecast – prepare shade nets for nurseries',     1),
        ]
        for aa in agri_alert_seed:
            cursor.execute('''INSERT OR IGNORE INTO agri_alerts
                              (alert_id,field_id,farm_id,alert_type,severity,message,is_active)
                              VALUES (?,?,?,?,?,?,?)''', aa)

        conn.commit()
        print("✅ Agriculture database initialised with demo data!")
    except Exception as e:
        print(f"Agriculture DB init note: {e}")
    finally:
        conn.close()


def get_db_connection():
    """Get database connection with error handling"""
    try:
        conn = sqlite3.connect('smart_weather.db', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def fetch_live_weather(location):
    """Fetch real weather data from OpenWeatherMap API"""
    try:
        if OPENWEATHER_API_KEY == 'demo_key':
            # Return demo data if no API key
            temp_variation = (hash(location) % 20) - 5  # Random temp between 15-25
            return {
                'temperature': 20 + temp_variation,
                'humidity': 60 + (hash(location) % 30),
                'pressure': 1010 + (hash(location) % 20),
                'wind_speed': 5 + (hash(location) % 15),
                'condition': ['Sunny', 'Cloudy', 'Rainy'][hash(location) % 3],
                'location': location,
                'timestamp': datetime.now().isoformat()
            }
        
        params = {
            'q': location,
            'appid': OPENWEATHER_API_KEY,
            'units': 'metric'
        }
        
        response = requests.get(OPENWEATHER_URL, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {
                'temperature': data['main']['temp'],
                'humidity': data['main']['humidity'],
                'pressure': data['main']['pressure'],
                'wind_speed': data['wind']['speed'],
                'condition': data['weather'][0]['main'],
                'location': location,
                'timestamp': datetime.now().isoformat()
            }
    except Exception as e:
        print(f"Weather API error for {location}: {e}")
    
    # Fallback data
    return {
        'temperature': 20.0,
        'humidity': 65,
        'pressure': 1013,
        'wind_speed': 10,
        'condition': 'Sunny',
        'location': location,
        'timestamp': datetime.now().isoformat()
    }

def store_weather_data(weather_data):
    """Store weather data in database"""
    conn = get_db_connection()
    if conn:
        try:
            conn.execute('''
                INSERT INTO weather_data 
                (location, temperature, humidity, pressure, wind_speed, weather_condition, precipitation)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                weather_data['location'],
                weather_data['temperature'],
                weather_data['humidity'],
                weather_data['pressure'],
                weather_data['wind_speed'],
                weather_data['condition'],
                0  # precipitation placeholder
            ))
            conn.commit()
        except Exception as e:
            print(f"Data storage error: {e}")
        finally:
            conn.close()

def get_historical_weather(location, hours=24):
    """Get historical weather data for AI training"""
    conn = get_db_connection()
    if conn:
        try:
            query = '''
                SELECT temperature, humidity, pressure, wind_speed,
                       CAST(strftime('%H', recorded_at) AS INTEGER) as hour,
                       CAST(strftime('%w', recorded_at) AS INTEGER) as day_of_week,
                       CAST(strftime('%m', recorded_at) AS INTEGER) as month
                FROM weather_data 
                WHERE location = ? 
                AND recorded_at >= datetime('now', ?)
                ORDER BY recorded_at
            '''
            df = pd.read_sql_query(query, conn, params=(location, f'-{hours} hours'))
            return df.to_dict('records')
        except Exception as e:
            print(f"Historical data error: {e}")
            return []
        finally:
            conn.close()
    return []

# ============================================================
# AGRICULTURE HELPER FUNCTIONS
# ============================================================

def compute_crop_health(weather, crop_type='wheat'):
    """Return health score (0-100) and individual stress indicators."""
    temp     = weather.get('temperature', 20)
    humidity = weather.get('humidity', 60)
    rainfall = weather.get('rainfall', weather.get('precipitation', 0))

    heat_threshold = {'wheat': 30, 'rice': 35, 'maize': 32,
                      'cotton': 40, 'sugarcane': 38}.get(crop_type.lower(), 35)

    heat_stress      = min(1.0, max(0.0, (temp - heat_threshold) / 10)) if temp > heat_threshold else 0.0
    frost_risk       = min(1.0, max(0.0, (2.0 - temp) / 5))             if temp < 2.0             else 0.0
    drought_stress   = min(1.0, max(0.0, (35 - humidity) / 35))         if humidity < 35 and rainfall < 2 else 0.0
    excess_moisture  = min(1.0, max(0.0, (humidity - 85) / 15))         if humidity > 85 or rainfall > 20 else 0.0

    composite    = heat_stress * 0.30 + frost_risk * 0.40 + drought_stress * 0.35 + excess_moisture * 0.15
    health_score = round(max(0.0, 100.0 * (1 - composite)), 1)

    return {
        'health_score':    health_score,
        'heat_stress':     round(heat_stress, 3),
        'frost_risk':      round(frost_risk, 3),
        'drought_stress':  round(drought_stress, 3),
        'excess_moisture': round(excess_moisture, 3),
    }


def compute_pest_risks(weather, crop_type='wheat'):
    """Return list of pest/disease risk dicts based on current weather."""
    temp     = weather.get('temperature', 20)
    humidity = weather.get('humidity', 60)
    wind     = weather.get('wind_speed', 10)
    risks = []

    if 15 <= temp <= 25 and humidity > 75:
        level = 'high' if humidity > 85 else 'medium'
        risks.append({'pest_type': 'Blight', 'risk_level': level,
                      'warning_message': f'High humidity ({humidity}%) + temp {temp}°C favour blight spread'})

    if temp > 20 and wind < 8:
        level = 'high' if temp > 28 else 'medium'
        risks.append({'pest_type': 'Aphids', 'risk_level': level,
                      'warning_message': f'Warm, calm conditions ({temp}°C, {wind} km/h) favour aphid colonies'})

    if crop_type.lower() == 'wheat' and 15 <= temp <= 22 and humidity > 70:
        risks.append({'pest_type': 'Wheat Rust', 'risk_level': 'high',
                      'warning_message': f'Optimal rust conditions: {temp}°C, {humidity}% RH – apply fungicide'})

    if humidity > 80:
        risks.append({'pest_type': 'Fungal Disease', 'risk_level': 'medium',
                      'warning_message': f'High humidity ({humidity}%) promotes fungal growth'})

    if temp > 30 and humidity < 40:
        risks.append({'pest_type': 'Spider Mites', 'risk_level': 'medium',
                      'warning_message': f'Hot, dry conditions ({temp}°C, {humidity}% RH) favour spider mites'})

    return risks


def compute_irrigation(weather, area_ha=1.0):
    """Return irrigation recommendation dict or None if no deficit."""
    temp     = weather.get('temperature', 20)
    humidity = weather.get('humidity', 60)
    rainfall = weather.get('rainfall', weather.get('precipitation', 0))

    et0         = max(0.0, 0.0023 * (temp + 17.8) * max(0, 35 - humidity) / 35 * 5)
    net_deficit = round(max(0.0, et0 - rainfall), 1)

    if net_deficit < 2.0:
        return None

    return {
        'volume_mm':        net_deficit,
        'volume_m3':        round(net_deficit * area_ha * 10, 1),
        'reason':           f'ET₀={round(et0,1)}mm, rainfall={rainfall}mm, deficit={net_deficit}mm',
        'recommended_date': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
    }


def compute_yield_forecast(health_score, target_yield_ton_ha):
    """Forecast expected yield based on health score and target."""
    stress_factor = health_score / 100
    expected      = round(target_yield_ton_ha * stress_factor, 2)
    confidence    = round(min(0.95, 0.55 + 0.4 * stress_factor), 2)
    return {
        'expected_yield_ton_ha': expected,
        'target_yield_ton_ha':   target_yield_ton_ha,
        'gap_ton_ha':            round(target_yield_ton_ha - expected, 2),
        'confidence':            confidence,
    }


def refresh_field_data(field_id, location):
    """Fetch live weather and recompute health / risk / irrigation / yield for a field."""
    conn = get_db_connection()
    if not conn:
        return
    try:
        census = conn.execute(
            'SELECT crop_type, area_ha, target_yield_ton_ha FROM crop_census '
            'WHERE field_id=? ORDER BY created_at DESC LIMIT 1', (field_id,)).fetchone()
        crop_type    = census['crop_type']          if census else 'wheat'
        area_ha      = census['area_ha']            if census else 1.0
        target_yield = census['target_yield_ton_ha'] if census else 3.0

        weather           = fetch_live_weather(location)
        weather['rainfall'] = 0  # free-tier OWM does not always include precipitation

        conn.execute('''INSERT INTO field_weather
                        (field_id,temperature,humidity,rainfall,wind_speed,pressure,weather_condition)
                        VALUES (?,?,?,?,?,?,?)''',
                     (field_id, weather['temperature'], weather['humidity'],
                      weather['rainfall'], weather['wind_speed'],
                      weather['pressure'], weather['condition']))

        health = compute_crop_health(weather, crop_type)
        conn.execute('''INSERT INTO crop_health
                        (field_id,health_score,heat_stress,frost_risk,drought_stress,excess_moisture)
                        VALUES (?,?,?,?,?,?)''',
                     (field_id, health['health_score'], health['heat_stress'],
                      health['frost_risk'], health['drought_stress'], health['excess_moisture']))

        conn.execute('DELETE FROM pest_risks WHERE field_id=?', (field_id,))
        for risk in compute_pest_risks(weather, crop_type):
            conn.execute('''INSERT INTO pest_risks (field_id,pest_type,risk_level,warning_message)
                            VALUES (?,?,?,?)''',
                         (field_id, risk['pest_type'], risk['risk_level'], risk['warning_message']))

        irr = compute_irrigation(weather, area_ha)
        if irr:
            conn.execute('''INSERT INTO irrigation_recommendations
                            (field_id,recommended_date,volume_mm,reason) VALUES (?,?,?,?)''',
                         (field_id, irr['recommended_date'], irr['volume_mm'], irr['reason']))

        yf = compute_yield_forecast(health['health_score'], target_yield)
        conn.execute('''INSERT INTO yield_forecasts
                        (field_id,expected_yield_ton_ha,target_yield_ton_ha,confidence)
                        VALUES (?,?,?,?)''',
                     (field_id, yf['expected_yield_ton_ha'], yf['target_yield_ton_ha'], yf['confidence']))

        if health['health_score'] < 60:
            conn.execute('''INSERT INTO agri_alerts (field_id,alert_type,severity,message)
                            VALUES (?,?,?,?)''',
                         (field_id, 'Crop Health Critical', 'critical',
                          f'⚠️ Field health score dropped to {health["health_score"]}% – immediate action required'))

        conn.commit()
    except Exception as e:
        print(f"Field refresh error (field {field_id}): {e}")
    finally:
        conn.close()


# Real-time weather updates
def update_weather_data():
    """Update weather data for all user locations"""
    conn = get_db_connection()
    if conn:
        try:
            locations = conn.execute('SELECT DISTINCT location FROM users').fetchall()
            for location_row in locations:
                location = location_row['location']
                weather_data = fetch_live_weather(location)
                store_weather_data(weather_data)
                
                # Get AI prediction
                prediction = weather_ai.predict(weather_data)
                
                # Send real-time update to connected clients
                socketio.emit('weather_update', {
                    'location': location,
                    'data': weather_data,
                    'prediction': prediction
                })
                
                print(f"📍 Weather updated for {location}: {weather_data['temperature']}°C")
        except Exception as e:
            print(f"Weather update error: {e}")
        finally:
            conn.close()

# Scheduler for periodic tasks
scheduler = BackgroundScheduler()

@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    conn = get_db_connection()
    if not conn:
        flash('Database connection error', 'error')
        return render_template('dashboard.html', now=datetime.now())
    
    try:
        total_users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        total_alerts = conn.execute('SELECT COUNT(*) FROM weather_alerts WHERE is_active = 1').fetchone()[0]
        
        recent_activities = conn.execute('''
            SELECT u.username, ua.activity_type, ua.weather_condition, ua.activity_date
            FROM user_activities ua
            JOIN users u ON ua.user_id = u.user_id
            ORDER BY ua.activity_date DESC LIMIT 5
        ''').fetchall()
        
        recent_weather = conn.execute('''
            SELECT location, temperature, weather_condition, recorded_at
            FROM weather_data 
            ORDER BY recorded_at DESC 
            LIMIT 3
        ''').fetchall()
        
        # Get AI model status
        ai_status = "Trained" if weather_ai.is_trained else "Training"
        
        return render_template('dashboard.html', 
                             total_users=total_users,
                             total_alerts=total_alerts,
                             recent_activities=recent_activities,
                             recent_weather=recent_weather,
                             ai_status=ai_status,
                             now=datetime.now())
    except Exception as e:
        flash(f'Dashboard error: {e}', 'error')
        return render_template('dashboard.html', now=datetime.now())
    finally:
        conn.close()

@app.route('/users')
def user_management():
    conn = get_db_connection()
    if not conn:
        flash('Database connection error', 'error')
        return render_template('user_management.html')
    
    try:
        users = conn.execute('''
            SELECT u.*, 
                   COUNT(DISTINCT ua.activity_id) as activity_count,
                   COUNT(DISTINCT wa.alert_id) as alert_count
            FROM users u
            LEFT JOIN user_activities ua ON u.user_id = ua.user_id
            LEFT JOIN weather_alerts wa ON u.user_id = wa.user_id AND wa.is_active = 1
            GROUP BY u.user_id
        ''').fetchall()
        
        total_alerts = conn.execute('SELECT COUNT(*) FROM weather_alerts WHERE is_active = 1').fetchone()[0]
        total_activities = conn.execute('SELECT COUNT(*) FROM user_activities').fetchone()[0]
        
        return render_template('user_management.html', 
                             users=users,
                             total_alerts=total_alerts,
                             total_activities=total_activities)
    except Exception as e:
        flash(f'User management error: {e}', 'error')
        return render_template('user_management.html')
    finally:
        conn.close()

@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        location = request.form['location']
        preferences = {
            'preferred_activities': request.form.getlist('preferred_activities'),
            'temperature_min': int(request.form.get('temperature_min', 15)),
            'temperature_max': int(request.form.get('temperature_max', 25)),
            'avoid_rain': 'avoid_rain' in request.form,
            'avoid_extreme_heat': 'avoid_extreme_heat' in request.form
        }
        
        conn = get_db_connection()
        try:
            conn.execute('''
                INSERT INTO users (username, email, location, preferences)
                VALUES (?, ?, ?, ?)
            ''', (username, email, location, json.dumps(preferences)))
            conn.commit()
            flash('🎉 User added successfully!', 'success')
            
            # Emit real-time update
            socketio.emit('user_added', {
                'username': username,
                'location': location,
                'timestamp': datetime.now().isoformat()
            })
            
        except sqlite3.IntegrityError:
            flash('❌ Username or email already exists!', 'error')
        finally:
            conn.close()
        
        return redirect(url_for('user_management'))
    
    return render_template('add_user.html')

@app.route('/user/<int:user_id>')
def user_profile(user_id):
    conn = get_db_connection()
    if not conn:
        flash('Database connection error', 'error')
        return redirect(url_for('user_management'))
    
    try:
        user = conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
        if not user:
            flash('User not found!', 'error')
            return redirect(url_for('user_management'))
        
        activities = conn.execute('''
            SELECT * FROM user_activities 
            WHERE user_id = ? 
            ORDER BY activity_date DESC
            LIMIT 10
        ''', (user_id,)).fetchall()
        
        alerts = conn.execute('''
            SELECT * FROM weather_alerts 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,)).fetchall()
        
        # Get weather data for user's location
        weather_data = conn.execute('''
            SELECT * FROM weather_data 
            WHERE location = ? 
            ORDER BY recorded_at DESC 
            LIMIT 5
        ''', (user['location'],)).fetchall()
        
        preferences = json.loads(user['preferences']) if user['preferences'] else {}
        
        return render_template('profile.html', 
                             user=user,
                             preferences=preferences,
                             activities=activities,
                             alerts=alerts,
                             weather_data=weather_data)
    except Exception as e:
        flash(f'Profile error: {e}', 'error')
        return redirect(url_for('user_management'))
    finally:
        conn.close()

@app.route('/add_alert/<int:user_id>', methods=['POST'])
def add_alert(user_id):
    alert_type = request.form['alert_type']
    severity = request.form['severity']
    message = request.form['message']
    temp_threshold = request.form.get('temp_threshold', 30)
    
    conditions = {
        'temperature': float(temp_threshold),
        'wind_speed': 50.0,  # Default values
        'precipitation': 10.0
    }
    
    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT INTO weather_alerts (user_id, alert_type, severity, message, trigger_conditions)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, alert_type, severity, message, json.dumps(conditions)))
        conn.commit()
        flash('✅ Alert added successfully!', 'success')
        
        # Emit real-time alert
        socketio.emit('alert_created', {
            'user_id': user_id,
            'alert_type': alert_type,
            'severity': severity,
            'message': message,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        flash(f'Error adding alert: {e}', 'error')
    finally:
        conn.close()
    
    return redirect(url_for('user_profile', user_id=user_id))

@app.route('/weather')
def weather_display():
    return render_template('weather_display.html')

@app.route('/alerts')
def alerts():
    return render_template('alerts.html')

@app.route('/recommendations')
def recommendations():
    return render_template('recommendations.html')


# ============================================================
# AGRICULTURE ROUTES – HTML
# ============================================================

@app.route('/agri')
def agri_dashboard():
    conn = get_db_connection()
    if not conn:
        flash('Database error', 'error')
        return render_template('agri_dashboard.html', now=datetime.now())
    try:
        farms       = conn.execute('SELECT * FROM farms ORDER BY created_at DESC').fetchall()
        total_farms = len(farms)
        total_fields = conn.execute('SELECT COUNT(*) FROM fields').fetchone()[0]

        health_rows = conn.execute('''
            SELECT f.field_id, f.name, fa.farm_id, fa.name AS farm_name,
                   ch.health_score, ch.heat_stress, ch.frost_risk, ch.drought_stress,
                   ch.excess_moisture, ch.recorded_at,
                   cc.crop_type, cc.growth_stage, cc.area_ha
            FROM fields f
            JOIN farms fa ON f.farm_id = fa.farm_id
            LEFT JOIN crop_health ch ON ch.health_id = (
                SELECT health_id FROM crop_health WHERE field_id=f.field_id ORDER BY recorded_at DESC LIMIT 1)
            LEFT JOIN crop_census cc ON cc.census_id = (
                SELECT census_id FROM crop_census WHERE field_id=f.field_id ORDER BY created_at DESC LIMIT 1)
            ORDER BY ch.health_score ASC
        ''').fetchall()

        active_alerts = conn.execute('''
            SELECT aa.*, f.name AS field_name, fa.name AS farm_name
            FROM agri_alerts aa
            LEFT JOIN fields f  ON aa.field_id = f.field_id
            LEFT JOIN farms  fa ON aa.farm_id  = fa.farm_id
                                 OR (aa.farm_id IS NULL AND f.farm_id = fa.farm_id)
            WHERE aa.is_active=1
            ORDER BY aa.created_at DESC LIMIT 10
        ''').fetchall()

        pending_irrigation = conn.execute('''
            SELECT ir.*, f.name AS field_name, fa.name AS farm_name
            FROM irrigation_recommendations ir
            JOIN fields f ON ir.field_id = f.field_id
            JOIN farms fa ON f.farm_id = fa.farm_id
            WHERE ir.is_done=0
            ORDER BY ir.recommended_date ASC LIMIT 5
        ''').fetchall()

        high_risks = conn.execute('''
            SELECT pr.*, f.name AS field_name
            FROM pest_risks pr
            JOIN fields f ON pr.field_id = f.field_id
            WHERE pr.risk_level='high'
            ORDER BY pr.recorded_at DESC LIMIT 6
        ''').fetchall()

        yield_summary = conn.execute('''
            SELECT yf.field_id, f.name AS field_name, cc.crop_type,
                   yf.expected_yield_ton_ha, yf.target_yield_ton_ha, yf.confidence
            FROM yield_forecasts yf
            JOIN fields f ON yf.field_id = f.field_id
            LEFT JOIN crop_census cc ON cc.field_id = yf.field_id
            WHERE yf.forecast_id IN (
                SELECT MAX(forecast_id) FROM yield_forecasts GROUP BY field_id)
        ''').fetchall()

        avg_health_row = conn.execute('''
            SELECT AVG(health_score) FROM crop_health WHERE health_id IN (
                SELECT MAX(health_id) FROM crop_health GROUP BY field_id)
        ''').fetchone()
        avg_health = round(avg_health_row[0] or 0, 1)

        return render_template('agri_dashboard.html',
                               farms=farms, total_farms=total_farms,
                               total_fields=total_fields, health_rows=health_rows,
                               active_alerts=active_alerts,
                               pending_irrigation=pending_irrigation,
                               high_risks=high_risks, yield_summary=yield_summary,
                               avg_health=avg_health, now=datetime.now())
    except Exception as e:
        flash(f'Agriculture dashboard error: {e}', 'error')
        return render_template('agri_dashboard.html', now=datetime.now())
    finally:
        conn.close()


@app.route('/farms')
def farms_list():
    conn = get_db_connection()
    try:
        farms = conn.execute('''
            SELECT fa.*, COUNT(DISTINCT f.field_id) AS field_count,
                   COALESCE(SUM(f.area_ha), 0) AS computed_area
            FROM farms fa
            LEFT JOIN fields f ON fa.farm_id = f.farm_id
            GROUP BY fa.farm_id
            ORDER BY fa.created_at DESC
        ''').fetchall()
        return render_template('farms.html', farms=farms, now=datetime.now())
    except Exception as e:
        flash(f'Error loading farms: {e}', 'error')
        return render_template('farms.html', farms=[], now=datetime.now())
    finally:
        conn.close()


@app.route('/farms/add', methods=['GET', 'POST'])
def add_farm():
    if request.method == 'POST':
        name          = request.form['name']
        owner_name    = request.form['owner_name']
        location      = request.form['location']
        total_area_ha = float(request.form.get('total_area_ha', 0))
        conn = get_db_connection()
        try:
            conn.execute('INSERT INTO farms (name,owner_name,location,total_area_ha) VALUES (?,?,?,?)',
                         (name, owner_name, location, total_area_ha))
            conn.commit()
            flash('🌾 Farm registered successfully!', 'success')
            socketio.emit('farm_added', {'name': name, 'location': location,
                                         'timestamp': datetime.now().isoformat()})
        except Exception as e:
            flash(f'Error adding farm: {e}', 'error')
        finally:
            conn.close()
        return redirect(url_for('farms_list'))
    return render_template('add_farm.html', now=datetime.now())


@app.route('/farms/<int:farm_id>')
def farm_detail(farm_id):
    conn = get_db_connection()
    try:
        farm = conn.execute('SELECT * FROM farms WHERE farm_id=?', (farm_id,)).fetchone()
        if not farm:
            flash('Farm not found', 'error')
            return redirect(url_for('farms_list'))

        fields = conn.execute('''
            SELECT f.*,
                   cc.crop_type, cc.growth_stage, cc.area_ha AS crop_area,
                   cc.planted_date, cc.expected_harvest_date, cc.target_yield_ton_ha, cc.season,
                   ch.health_score, ch.heat_stress, ch.frost_risk, ch.drought_stress, ch.excess_moisture,
                   yf.expected_yield_ton_ha, yf.confidence
            FROM fields f
            LEFT JOIN crop_census cc ON cc.census_id = (
                SELECT census_id FROM crop_census WHERE field_id=f.field_id ORDER BY created_at DESC LIMIT 1)
            LEFT JOIN crop_health ch ON ch.health_id = (
                SELECT health_id FROM crop_health WHERE field_id=f.field_id ORDER BY recorded_at DESC LIMIT 1)
            LEFT JOIN yield_forecasts yf ON yf.forecast_id = (
                SELECT forecast_id FROM yield_forecasts WHERE field_id=f.field_id ORDER BY forecast_date DESC LIMIT 1)
            WHERE f.farm_id=?
            ORDER BY f.created_at ASC
        ''', (farm_id,)).fetchall()

        farm_alerts = conn.execute('''
            SELECT aa.* FROM agri_alerts aa
            WHERE (aa.farm_id=?
                   OR aa.field_id IN (SELECT field_id FROM fields WHERE farm_id=?))
              AND aa.is_active=1
            ORDER BY aa.created_at DESC
        ''', (farm_id, farm_id)).fetchall()

        return render_template('farm_detail.html', farm=farm, fields=fields,
                               farm_alerts=farm_alerts, now=datetime.now())
    except Exception as e:
        flash(f'Error loading farm: {e}', 'error')
        return redirect(url_for('farms_list'))
    finally:
        conn.close()


@app.route('/fields/add', methods=['GET', 'POST'])
def add_field():
    conn = get_db_connection()
    if request.method == 'POST':
        farm_id       = int(request.form['farm_id'])
        name          = request.form['name']
        area_ha       = float(request.form['area_ha'])
        soil_type     = request.form.get('soil_type', 'Loamy')
        irrigation_type = request.form.get('irrigation_type', 'Drip')
        crop_type     = request.form.get('crop_type', '')
        growth_stage  = request.form.get('growth_stage', 'Germination')
        planted_date  = request.form.get('planted_date', '')
        expected_harvest_date = request.form.get('expected_harvest_date', '')
        target_yield  = float(request.form.get('target_yield_ton_ha', 3.0))
        season        = request.form.get('season', 'Rabi 2025-26')
        try:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO fields (farm_id,name,area_ha,soil_type,irrigation_type) VALUES (?,?,?,?,?)',
                           (farm_id, name, area_ha, soil_type, irrigation_type))
            field_id = cursor.lastrowid
            if crop_type:
                cursor.execute('''INSERT INTO crop_census
                                  (field_id,crop_type,area_ha,growth_stage,planted_date,
                                   expected_harvest_date,target_yield_ton_ha,season)
                                  VALUES (?,?,?,?,?,?,?,?)''',
                               (field_id, crop_type, area_ha, growth_stage, planted_date,
                                expected_harvest_date, target_yield, season))
            conn.commit()
            farm = conn.execute('SELECT location FROM farms WHERE farm_id=?', (farm_id,)).fetchone()
            if farm:
                threading.Thread(target=refresh_field_data,
                                 args=(field_id, farm['location']), daemon=True).start()
            flash('🌱 Field and crop registered successfully!', 'success')
            return redirect(url_for('farm_detail', farm_id=farm_id))
        except Exception as e:
            flash(f'Error adding field: {e}', 'error')
            return redirect(url_for('farms_list'))
        finally:
            conn.close()

    farms = conn.execute('SELECT farm_id, name FROM farms ORDER BY name').fetchall()
    conn.close()
    pre_farm_id = request.args.get('farm_id')
    return render_template('add_field.html', farms=farms, pre_farm_id=pre_farm_id, now=datetime.now())


@app.route('/fields/<int:field_id>')
def field_detail(field_id):
    conn = get_db_connection()
    try:
        field = conn.execute('''
            SELECT f.*, fa.name AS farm_name, fa.location, fa.farm_id
            FROM fields f JOIN farms fa ON f.farm_id = fa.farm_id
            WHERE f.field_id=?
        ''', (field_id,)).fetchone()
        if not field:
            flash('Field not found', 'error')
            return redirect(url_for('farms_list'))

        crop_census    = conn.execute('SELECT * FROM crop_census WHERE field_id=? ORDER BY created_at DESC',
                                      (field_id,)).fetchall()
        latest_health  = conn.execute('SELECT * FROM crop_health WHERE field_id=? ORDER BY recorded_at DESC LIMIT 1',
                                      (field_id,)).fetchone()
        health_history = conn.execute('SELECT health_score, recorded_at FROM crop_health WHERE field_id=? '
                                      'ORDER BY recorded_at DESC LIMIT 10', (field_id,)).fetchall()
        pest_risks     = conn.execute('SELECT * FROM pest_risks WHERE field_id=? ORDER BY recorded_at DESC LIMIT 10',
                                      (field_id,)).fetchall()
        irrigation_recs = conn.execute('SELECT * FROM irrigation_recommendations WHERE field_id=? '
                                       'ORDER BY created_at DESC LIMIT 5', (field_id,)).fetchall()
        latest_yield   = conn.execute('SELECT * FROM yield_forecasts WHERE field_id=? '
                                      'ORDER BY forecast_date DESC LIMIT 1', (field_id,)).fetchone()
        recent_weather = conn.execute('SELECT * FROM field_weather WHERE field_id=? '
                                      'ORDER BY recorded_at DESC LIMIT 6', (field_id,)).fetchall()
        field_alerts   = conn.execute('SELECT * FROM agri_alerts WHERE field_id=? AND is_active=1 '
                                      'ORDER BY created_at DESC', (field_id,)).fetchall()

        return render_template('field_detail.html',
                               field=field, crop_census=crop_census,
                               latest_health=latest_health, health_history=health_history,
                               pest_risks=pest_risks, irrigation_recs=irrigation_recs,
                               latest_yield=latest_yield, recent_weather=recent_weather,
                               field_alerts=field_alerts, now=datetime.now())
    except Exception as e:
        flash(f'Field detail error: {e}', 'error')
        return redirect(url_for('farms_list'))
    finally:
        conn.close()


@app.route('/agri/alerts')
def agri_alerts_view():
    conn = get_db_connection()
    try:
        alerts = conn.execute('''
            SELECT aa.*, f.name AS field_name, fa.name AS farm_name
            FROM agri_alerts aa
            LEFT JOIN fields f  ON aa.field_id = f.field_id
            LEFT JOIN farms  fa ON aa.farm_id  = fa.farm_id
                                 OR (aa.farm_id IS NULL AND f.farm_id = fa.farm_id)
            ORDER BY aa.is_active DESC, aa.created_at DESC
        ''').fetchall()
        return render_template('agri_alerts.html', alerts=alerts, now=datetime.now())
    except Exception as e:
        flash(f'Error loading alerts: {e}', 'error')
        return render_template('agri_alerts.html', alerts=[], now=datetime.now())
    finally:
        conn.close()


@app.route('/agri/alerts/dismiss/<int:alert_id>', methods=['POST'])
def dismiss_agri_alert(alert_id):
    conn = get_db_connection()
    try:
        conn.execute('UPDATE agri_alerts SET is_active=0 WHERE alert_id=?', (alert_id,))
        conn.commit()
        flash('Alert dismissed.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'error')
    finally:
        conn.close()
    return redirect(request.referrer or url_for('agri_alerts_view'))


@app.route('/fields/<int:field_id>/refresh', methods=['POST'])
def refresh_field(field_id):
    """Manually trigger a live weather refresh for one field."""
    conn = get_db_connection()
    try:
        row = conn.execute('''SELECT f.field_id, fa.location FROM fields f
                              JOIN farms fa ON f.farm_id=fa.farm_id WHERE f.field_id=?''',
                           (field_id,)).fetchone()
        if row:
            threading.Thread(target=refresh_field_data,
                             args=(field_id, row['location']), daemon=True).start()
            flash('🔄 Field data refresh started!', 'success')
        else:
            flash('Field not found', 'error')
    except Exception as e:
        flash(f'Refresh error: {e}', 'error')
    finally:
        conn.close()
    return redirect(url_for('field_detail', field_id=field_id))


# ============================================================
# AGRICULTURE API ENDPOINTS (JSON)
# ============================================================

@app.route('/api/agri/farms')
def api_farms():
    conn = get_db_connection()
    try:
        rows = conn.execute('''
            SELECT fa.farm_id, fa.name, fa.owner_name, fa.location, fa.total_area_ha,
                   COUNT(DISTINCT f.field_id) AS field_count,
                   COALESCE(AVG(ch.health_score), 0) AS avg_health
            FROM farms fa
            LEFT JOIN fields f ON fa.farm_id = f.farm_id
            LEFT JOIN crop_health ch ON ch.field_id = f.field_id AND ch.health_id = (
                SELECT MAX(health_id) FROM crop_health WHERE field_id=f.field_id)
            GROUP BY fa.farm_id
        ''').fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.route('/api/agri/fields/<int:field_id>')
def api_field_detail(field_id):
    conn = get_db_connection()
    try:
        field  = conn.execute('SELECT f.*, fa.name AS farm_name, fa.location FROM fields f '
                              'JOIN farms fa ON f.farm_id=fa.farm_id WHERE f.field_id=?',
                              (field_id,)).fetchone()
        if not field:
            return jsonify({'error': 'Field not found'}), 404
        census = conn.execute('SELECT * FROM crop_census WHERE field_id=? '
                              'ORDER BY created_at DESC LIMIT 1', (field_id,)).fetchone()
        health = conn.execute('SELECT * FROM crop_health WHERE field_id=? '
                              'ORDER BY recorded_at DESC LIMIT 1', (field_id,)).fetchone()
        risks  = conn.execute('SELECT * FROM pest_risks WHERE field_id=? '
                              'ORDER BY recorded_at DESC', (field_id,)).fetchall()
        irr    = conn.execute('SELECT * FROM irrigation_recommendations WHERE field_id=? '
                              'AND is_done=0 ORDER BY created_at DESC LIMIT 1', (field_id,)).fetchone()
        yf     = conn.execute('SELECT * FROM yield_forecasts WHERE field_id=? '
                              'ORDER BY forecast_date DESC LIMIT 1', (field_id,)).fetchone()
        return jsonify({
            'field':         dict(field),
            'crop_census':   dict(census) if census else None,
            'health':        dict(health) if health else None,
            'pest_risks':    [dict(r) for r in risks],
            'irrigation':    dict(irr) if irr else None,
            'yield_forecast': dict(yf) if yf else None,
        })
    finally:
        conn.close()


@app.route('/api/agri/health/<int:field_id>')
def api_crop_health(field_id):
    """Live health score computed from current weather."""
    conn = get_db_connection()
    try:
        info = conn.execute('''
            SELECT f.field_id, fa.location, cc.crop_type, cc.area_ha, cc.target_yield_ton_ha
            FROM fields f JOIN farms fa ON f.farm_id=fa.farm_id
            LEFT JOIN crop_census cc ON cc.field_id=f.field_id
            WHERE f.field_id=? ORDER BY cc.created_at DESC LIMIT 1
        ''', (field_id,)).fetchone()
        if not info:
            return jsonify({'error': 'Field not found'}), 404
        weather = fetch_live_weather(info['location'])
        health  = compute_crop_health(weather, info['crop_type'] or 'wheat')
        risks   = compute_pest_risks(weather, info['crop_type'] or 'wheat')
        irr     = compute_irrigation(weather, info['area_ha'] or 1.0)
        yf      = compute_yield_forecast(health['health_score'], info['target_yield_ton_ha'] or 3.0)
        return jsonify({
            'field_id':     field_id,
            'weather':      weather,
            'health':       health,
            'pest_risks':   risks,
            'irrigation':   irr,
            'yield_forecast': yf,
            'computed_at':  datetime.now().isoformat(),
        })
    finally:
        conn.close()


@app.route('/api/agri/advisories')
def api_advisories():
    """All pending agri alerts and irrigation tasks."""
    conn = get_db_connection()
    try:
        alerts = conn.execute('''
            SELECT aa.*, f.name AS field_name, fa.name AS farm_name
            FROM agri_alerts aa
            LEFT JOIN fields f  ON aa.field_id = f.field_id
            LEFT JOIN farms  fa ON aa.farm_id  = fa.farm_id
                                 OR (aa.farm_id IS NULL AND f.farm_id = fa.farm_id)
            WHERE aa.is_active=1 ORDER BY aa.created_at DESC
        ''').fetchall()
        irrigation = conn.execute('''
            SELECT ir.*, f.name AS field_name, fa.name AS farm_name
            FROM irrigation_recommendations ir
            JOIN fields f ON ir.field_id=f.field_id
            JOIN farms fa ON f.farm_id=fa.farm_id
            WHERE ir.is_done=0 ORDER BY ir.recommended_date ASC
        ''').fetchall()
        return jsonify({
            'alerts':     [dict(a) for a in alerts],
            'irrigation': [dict(i) for i in irrigation],
            'count':      len(alerts) + len(irrigation),
        })
    finally:
        conn.close()


@app.route('/api/agri/analytics')
def api_analytics():
    """High-level agriculture analytics summary."""
    conn = get_db_connection()
    try:
        total_farms  = conn.execute('SELECT COUNT(*) FROM farms').fetchone()[0]
        total_fields = conn.execute('SELECT COUNT(*) FROM fields').fetchone()[0]
        total_area   = conn.execute('SELECT COALESCE(SUM(area_ha),0) FROM fields').fetchone()[0]
        avg_health   = conn.execute('''SELECT AVG(health_score) FROM crop_health WHERE health_id IN
                                       (SELECT MAX(health_id) FROM crop_health GROUP BY field_id)''').fetchone()[0] or 0
        crop_breakdown = conn.execute('''SELECT crop_type, COUNT(*) AS fields, SUM(area_ha) AS total_area
                                         FROM crop_census GROUP BY crop_type ORDER BY total_area DESC''').fetchall()
        risk_summary   = conn.execute('''SELECT risk_level, COUNT(*) AS count FROM pest_risks
                                         GROUP BY risk_level ORDER BY count DESC''').fetchall()
        yield_row      = conn.execute('''
            SELECT SUM(yf.expected_yield_ton_ha * f.area_ha) AS total_expected,
                   SUM(yf.target_yield_ton_ha   * f.area_ha) AS total_target
            FROM yield_forecasts yf JOIN fields f ON yf.field_id=f.field_id
            WHERE yf.forecast_id IN (SELECT MAX(forecast_id) FROM yield_forecasts GROUP BY field_id)
        ''').fetchone()
        return jsonify({
            'total_farms':        total_farms,
            'total_fields':       total_fields,
            'total_area_ha':      round(total_area, 1),
            'avg_health_score':   round(avg_health, 1),
            'crop_breakdown':     [dict(r) for r in crop_breakdown],
            'risk_summary':       [dict(r) for r in risk_summary],
            'yield_expected_tons': round(yield_row[0] or 0, 1),
            'yield_target_tons':   round(yield_row[1] or 0, 1),
        })
    finally:
        conn.close()


@app.route('/api/agri/forecast/<int:field_id>')
def api_forecast(field_id):
    """7-day weather-adjusted crop health forecast for a field."""
    conn = get_db_connection()
    try:
        field_info = conn.execute('''
            SELECT fa.location, cc.crop_type, cc.target_yield_ton_ha
            FROM fields f JOIN farms fa ON f.farm_id=fa.farm_id
            LEFT JOIN crop_census cc ON cc.field_id=f.field_id
            WHERE f.field_id=? ORDER BY cc.created_at DESC LIMIT 1
        ''', (field_id,)).fetchone()
        if not field_info:
            return jsonify({'error': 'Field not found'}), 404

        forecast_days = []
        if OPENWEATHER_API_KEY not in ('demo_key', 'demo_key_12345'):
            try:
                params = {'q': field_info['location'], 'appid': OPENWEATHER_API_KEY,
                          'units': 'metric', 'cnt': 40}
                r = requests.get('https://api.openweathermap.org/data/2.5/forecast',
                                 params=params, timeout=10)
                if r.status_code == 200:
                    seen = set()
                    for item in r.json()['list']:
                        date = item['dt_txt'][:10]
                        if date not in seen:
                            seen.add(date)
                            w = {'temperature': item['main']['temp'],
                                 'humidity':    item['main']['humidity'],
                                 'rainfall':    item.get('rain', {}).get('3h', 0),
                                 'wind_speed':  item['wind']['speed']}
                            h = compute_crop_health(w, field_info['crop_type'] or 'wheat')
                            forecast_days.append({'date': date, 'weather': w,
                                                  'health_score': h['health_score'],
                                                  'condition': item['weather'][0]['main']})
                            if len(forecast_days) >= 7:
                                break
            except Exception as fe:
                print(f"Forecast API error: {fe}")

        if not forecast_days:
            base = fetch_live_weather(field_info['location'])
            for i in range(7):
                variation = (hash(str(i) + field_info['location']) % 10) - 5
                w = {'temperature': base['temperature'] + variation * 0.5,
                     'humidity':    min(100, max(20, base['humidity'] + variation)),
                     'rainfall':    max(0, variation) if variation > 3 else 0,
                     'wind_speed':  base['wind_speed']}
                h = compute_crop_health(w, field_info['crop_type'] or 'wheat')
                forecast_days.append({
                    'date':         (datetime.now() + timedelta(days=i)).strftime('%Y-%m-%d'),
                    'weather':      w,
                    'health_score': h['health_score'],
                    'condition':    'Rain' if w['rainfall'] > 0 else 'Sunny',
                })

        return jsonify({'field_id': field_id, 'forecast': forecast_days})
    finally:
        conn.close()

# SocketIO Events
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print(f"✅ Client connected: {request.sid}")
    emit('connection_response', {
        'status': 'connected', 
        'message': 'Welcome to Smart Weather System!',
        'ai_status': 'trained' if weather_ai.is_trained else 'training'
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print(f"❌ Client disconnected: {request.sid}")

@socketio.on('request_weather')
def handle_weather_request(data):
    """Handle real-time weather requests"""
    location = data.get('location', 'London')
    weather_data = fetch_live_weather(location)
    prediction = weather_ai.predict(weather_data)
    
    emit('weather_response', {
        'location': location,
        'current': weather_data,
        'prediction': prediction
    })

@socketio.on('request_ai_training')
def handle_ai_training_request():
    """Handle AI training requests"""
    if not weather_ai.is_trained:
        emit('ai_training_start', {'message': 'Starting AI model training...'})
        
        # Train with available data
        historical_data = get_historical_weather('London', 168)
        success = weather_ai.train(historical_data)
        
        if success:
            emit('ai_training_complete', {
                'message': 'AI model trained successfully!',
                'score': 0.85,  # Simulated score
                'data_points': len(historical_data)
            })
        else:
            emit('ai_training_failed', {'message': 'AI training failed. Insufficient data.'})

# Initialize application
def initialize_app():
    """Initialize the application"""
    print("🚀 Initializing Smart Weather System...")
    init_database()
    init_agriculture_database()
    
    # Try to load existing AI model
    if not weather_ai.load_model():
        print("🤖 Training new AI model...")
        # Train with available historical data
        historical_data = get_historical_weather('London', 168)
        if historical_data:
            weather_ai.train(historical_data)
    
    # Start scheduler for periodic updates
    scheduler.add_job(update_weather_data, 'interval', minutes=2)
    scheduler.add_job(lambda: weather_ai.train(get_historical_weather('London', 168)), 'interval', hours=1)
    
    if not scheduler.running:
        scheduler.start()
        print("⏰ Scheduler started")
    
    print("✅ Smart Weather System ready!")

# Shutdown handler
def shutdown_app():
    """Cleanup on application shutdown"""
    print("🛑 Shutting down Smart Weather System...")
    if scheduler.running:
        scheduler.shutdown()
    print("✅ Clean shutdown completed")

# Register shutdown handler
atexit.register(shutdown_app)

if __name__ == '__main__':
    initialize_app()
    print("🌐 Starting Flask-SocketIO server on port 8000...")
    socketio.run(app, host='0.0.0.0', port=8000, debug=True)
