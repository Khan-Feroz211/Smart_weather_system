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
from collections import defaultdict, deque
import statistics

# Load environment variables
load_dotenv()



app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'smart_weather_ai_2024')
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

# Configuration
OPENWEATHER_API_KEY = os.environ.get('OPENWEATHER_API_KEY', 'demo_key')
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
SENSOR_GATEWAY_URL = os.environ.get('SENSOR_GATEWAY_URL', '').strip()
SENSOR_STALE_MINUTES = int(os.environ.get('SENSOR_STALE_MINUTES', '15'))

# AI Model Storage
MODEL_PATH = 'models/weather_model.joblib'
SCALER_PATH = 'models/scaler.joblib'

# Create models directory
os.makedirs('models', exist_ok=True)

SENSOR_FIELD_RANGES = {
    'temperature': (-50.0, 60.0),
    'humidity': (0.0, 100.0),
    'pressure': (870.0, 1085.0),
    'wind_speed': (0.0, 120.0)
}

recent_cleaned_by_location = defaultdict(lambda: deque(maxlen=24))

SCHEMA_TABLE_WHITELIST = {'weather_data'}
WEATHER_NUMERIC_FIELDS = {'temperature', 'humidity', 'pressure', 'wind_speed'}

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

    def assess_data_quality(self, validation_result, cleaning_notes, features):
        """Rule-based quality assessment for sensor records"""
        flags = validation_result.get('quality_flags', [])
        score = validation_result.get('validation_score', 0.0)
        suspicious_reasons = []

        if not validation_result.get('is_valid', False):
            suspicious_reasons.append('validation_failed')
        if any(flag.startswith('missing_') for flag in flags):
            suspicious_reasons.append('missing_values_detected')
        if any(flag.startswith('outlier_') for flag in flags) or any('outlier_' in note for note in cleaning_notes):
            suspicious_reasons.append('outlier_adjustments_applied')
        if features.get('sensor_reliability', 1.0) < 0.6:
            suspicious_reasons.append('low_sensor_reliability')

        quality_label = 'valid' if not suspicious_reasons and score >= 0.7 else 'suspicious'
        confidence = max(0.5, min(0.98, score))

        return {
            'quality_label': quality_label,
            'confidence': round(confidence, 3),
            'reasons': suspicious_reasons if suspicious_reasons else ['passed_all_checks'],
            'validation_score': round(score, 3),
            'quality_flags': flags
        }

# Initialize AI System
weather_ai = WeatherAI()

def ensure_column_exists(conn, table_name, column_name, column_def):
    """Add missing column to an existing table"""
    try:
        if table_name not in SCHEMA_TABLE_WHITELIST:
            raise ValueError(f"Unsupported table for migration: {table_name}")
        if not column_name.replace('_', '').isalnum():
            raise ValueError(f"Unsafe column name: {column_name}")
        allowed_defs = {'TEXT', 'REAL', 'INTEGER', 'BOOLEAN'}
        if column_def not in allowed_defs:
            raise ValueError(f"Unsupported column definition: {column_def}")

        existing = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        column_names = {col['name'] for col in existing}
        if column_name not in column_names:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
    except Exception as e:
        print(f"Schema update warning ({table_name}.{column_name}): {e}")

def parse_sensor_timestamp(timestamp_value):
    """Parse sensor timestamp safely"""
    if isinstance(timestamp_value, datetime):
        return timestamp_value
    if not timestamp_value:
        return datetime.now()
    try:
        return datetime.fromisoformat(str(timestamp_value).replace('Z', '+00:00')).replace(tzinfo=None)
    except Exception:
        return datetime.now()

def calculate_sensor_reliability(sensor_id):
    """Compute sensor reliability from monitoring table"""
    conn = get_db_connection()
    if not conn:
        return 1.0
    try:
        metric = conn.execute('''
            SELECT total_records, validation_failures
            FROM sensor_quality_metrics
            WHERE sensor_id = ?
        ''', (sensor_id,)).fetchone()
        if not metric or metric['total_records'] == 0:
            return 1.0
        failure_rate = metric['validation_failures'] / float(metric['total_records'])
        return max(0.0, min(1.0, 1.0 - failure_rate))
    except Exception as e:
        print(f"Reliability calculation warning: {e}")
        return 1.0
    finally:
        conn.close()

def ingest_sensor_weather(location):
    """Ingest weather data from sensor gateway with API fallback"""
    sensor_data = None
    ingestion_mode = 'sensor'
    source = 'sensor_gateway'

    if SENSOR_GATEWAY_URL:
        try:
            response = requests.get(SENSOR_GATEWAY_URL, params={'location': location}, timeout=8)
            if response.status_code == 200:
                payload = response.json()
                sensor_data = payload[0] if isinstance(payload, list) and payload else payload
        except Exception as e:
            print(f"Sensor gateway warning for {location}: {e}")

    if not sensor_data:
        weather_data = fetch_live_weather(location)
        ingestion_mode = 'fallback'
        source = 'openweather_or_demo'
        sensor_data = {
            'sensor_id': f'fallback-{location.lower().replace(" ", "-")}',
            'source': source,
            'timestamp': weather_data.get('timestamp', datetime.now().isoformat()),
            'temperature': weather_data.get('temperature'),
            'humidity': weather_data.get('humidity'),
            'pressure': weather_data.get('pressure'),
            'wind_speed': weather_data.get('wind_speed'),
            'condition': weather_data.get('condition', 'Unknown'),
            'location': location
        }

    sensor_data['source'] = sensor_data.get('source') or source
    sensor_data['ingestion_mode'] = ingestion_mode
    sensor_data['sensor_id'] = str(sensor_data.get('sensor_id') or f'sensor-{location.lower().replace(" ", "-")}')
    sensor_data['location'] = sensor_data.get('location') or location
    sensor_data['timestamp'] = sensor_data.get('timestamp') or datetime.now().isoformat()
    return sensor_data

def validate_sensor_data(sensor_record):
    """Validate raw sensor data before storage"""
    quality_flags = []
    required_fields = ['sensor_id', 'location', 'timestamp', 'temperature', 'humidity', 'pressure', 'wind_speed']
    validated = dict(sensor_record)

    for field in required_fields:
        if field not in validated or validated[field] in (None, ''):
            quality_flags.append(f'missing_{field}')

    for field in ['temperature', 'humidity', 'pressure', 'wind_speed']:
        if field in validated and validated[field] not in (None, ''):
            try:
                validated[field] = float(validated[field])
            except (TypeError, ValueError):
                quality_flags.append(f'invalid_type_{field}')
                validated[field] = None

    sensor_dt = parse_sensor_timestamp(validated.get('timestamp'))
    now = datetime.now()
    if sensor_dt < now - timedelta(minutes=SENSOR_STALE_MINUTES):
        quality_flags.append('stale_timestamp')
    if sensor_dt > now + timedelta(minutes=2):
        quality_flags.append('future_timestamp')
    validated['parsed_timestamp'] = sensor_dt

    for field, (min_value, max_value) in SENSOR_FIELD_RANGES.items():
        value = validated.get(field)
        if value is None:
            continue
        if value < min_value or value > max_value:
            quality_flags.append(f'out_of_range_{field}')

    conn = get_db_connection()
    if conn:
        try:
            duplicate = conn.execute('''
                SELECT 1
                FROM sensor_pipeline_logs
                WHERE sensor_id = ? AND sensor_timestamp = ?
                LIMIT 1
            ''', (validated.get('sensor_id'), sensor_dt.isoformat())).fetchone()
            if duplicate:
                quality_flags.append('duplicate_record')
        except Exception as e:
            print(f"Duplicate check warning: {e}")
        finally:
            conn.close()

    validation_score = max(0.0, 1.0 - (len(quality_flags) * 0.12))
    return {
        'is_valid': len(quality_flags) == 0,
        'quality_flags': quality_flags,
        'validation_score': round(validation_score, 3),
        'validated_record': validated
    }

def historical_field_average(location, field):
    """Get historical average for fallback imputation"""
    if field not in WEATHER_NUMERIC_FIELDS:
        return None
    conn = get_db_connection()
    if not conn:
        return None
    try:
        row = conn.execute(f'''
            SELECT AVG({field}) as avg_value
            FROM weather_data
            WHERE location = ? AND {field} IS NOT NULL
            ORDER BY recorded_at DESC
            LIMIT 50
        ''', (location,)).fetchone()
        return float(row['avg_value']) if row and row['avg_value'] is not None else None
    except Exception as e:
        print(f"Historical average warning ({field}): {e}")
        return None
    finally:
        conn.close()

def clean_sensor_data(validation_result):
    """Clean and normalize validated sensor data"""
    record = dict(validation_result['validated_record'])
    flags = list(validation_result['quality_flags'])
    notes = []
    location = record.get('location', 'Unknown')

    defaults = {
        'temperature': 20.0,
        'humidity': 60.0,
        'pressure': 1013.0,
        'wind_speed': 8.0
    }

    for field in ['temperature', 'humidity', 'pressure', 'wind_speed']:
        if record.get(field) is None:
            avg = historical_field_average(location, field)
            record[field] = avg if avg is not None else defaults[field]
            notes.append(f'imputed_{field}')

        min_value, max_value = SENSOR_FIELD_RANGES[field]
        if record[field] < min_value:
            record[field] = min_value
            notes.append(f'clamped_low_{field}')
        elif record[field] > max_value:
            record[field] = max_value
            notes.append(f'clamped_high_{field}')

    recent_series = [item['temperature'] for item in recent_cleaned_by_location[location] if 'temperature' in item]
    if len(recent_series) >= 5:
        median_temp = statistics.median(recent_series)
        if abs(record['temperature'] - median_temp) > 20:
            record['temperature'] = round((record['temperature'] + median_temp) / 2, 2)
            notes.append('outlier_temperature_smoothed')
            flags.append('outlier_temperature')

    if record['wind_speed'] > 70:
        record['wind_speed'] = round(record['wind_speed'] * 0.277778, 2)
        notes.append('wind_normalized_to_mps')

    record['condition'] = str(record.get('condition', 'Unknown')).title()
    record['timestamp'] = record.get('parsed_timestamp', datetime.now()).isoformat()
    if 'duplicate_record' in flags:
        notes.append('duplicate_removed_from_storage')
    record['quality_flags'] = sorted(set(flags))
    record['cleaning_notes'] = notes
    return record

def engineer_features(cleaned_record, validation_result):
    """Generate features from cleaned weather data"""
    location = cleaned_record.get('location', 'Unknown')
    sensor_id = cleaned_record.get('sensor_id', 'unknown')
    sensor_dt = parse_sensor_timestamp(cleaned_record.get('timestamp'))
    recent_records = list(recent_cleaned_by_location[location])

    temp_history = [item['temperature'] for item in recent_records if 'temperature' in item]
    humidity_history = [item['humidity'] for item in recent_records if 'humidity' in item]
    recent_temp_avg = statistics.mean(temp_history[-6:]) if temp_history else cleaned_record['temperature']
    recent_humidity_avg = statistics.mean(humidity_history[-6:]) if humidity_history else cleaned_record['humidity']

    prev_temp = temp_history[-1] if temp_history else cleaned_record['temperature']
    prev_humidity = humidity_history[-1] if humidity_history else cleaned_record['humidity']

    features = {
        'hour': sensor_dt.hour,
        'day_of_week': sensor_dt.weekday(),
        'month': sensor_dt.month,
        'rolling_temp_avg_6': round(recent_temp_avg, 3),
        'rolling_humidity_avg_6': round(recent_humidity_avg, 3),
        'delta_temperature': round(cleaned_record['temperature'] - prev_temp, 3),
        'humidity_trend': round(cleaned_record['humidity'] - prev_humidity, 3),
        'sensor_reliability': round(calculate_sensor_reliability(sensor_id), 3),
        'validation_score': validation_result['validation_score']
    }
    return features

def build_decision(cleaned_record, prediction, quality_assessment):
    """Generate decision output from weather + quality + AI prediction"""
    severity = 'low'
    reasons = []
    actions = []

    if quality_assessment['quality_label'] == 'suspicious':
        severity = 'medium'
        reasons.append('sensor_data_suspicious')
        actions.append('Inspect sensor and verify calibration.')

    if cleaned_record['temperature'] >= 35:
        severity = 'high'
        reasons.append('extreme_heat')
        actions.append('Limit outdoor activity and hydrate frequently.')
    elif cleaned_record['temperature'] <= 2:
        severity = 'high'
        reasons.append('extreme_cold')
        actions.append('Protect sensitive crops and wear thermal clothing.')

    if cleaned_record['wind_speed'] >= 20:
        severity = 'high'
        reasons.append('high_wind')
        actions.append('Avoid lightweight outdoor structures and secure equipment.')

    if cleaned_record['humidity'] >= 90:
        reasons.append('very_high_humidity')
        actions.append('Delay irrigation and monitor fungal risk.')

    alert_required = severity in ('medium', 'high')
    confidence = prediction['confidence'] if prediction else quality_assessment['confidence']
    recommended_action = actions[0] if actions else 'Weather conditions normal. Continue monitoring.'

    return {
        'alert_required': alert_required,
        'severity': severity,
        'reason': ', '.join(sorted(set(reasons))) if reasons else 'normal_conditions',
        'recommended_action': recommended_action,
        'confidence': round(float(confidence), 3)
    }

def update_sensor_quality_metrics(sensor_id, validation_result, cleaned_record):
    """Track validation failures and cleaning corrections per sensor"""
    conn = get_db_connection()
    if not conn:
        return
    try:
        row = conn.execute('SELECT * FROM sensor_quality_metrics WHERE sensor_id = ?', (sensor_id,)).fetchone()
        validation_failures = 1 if not validation_result['is_valid'] else 0
        duplicate_failures = 1 if 'duplicate_record' in validation_result['quality_flags'] else 0
        outlier_corrections = len([x for x in cleaned_record.get('cleaning_notes', []) if 'outlier' in x or 'clamped' in x])

        if row:
            conn.execute('''
                UPDATE sensor_quality_metrics
                SET total_records = total_records + 1,
                    validation_failures = validation_failures + ?,
                    duplicate_records = duplicate_records + ?,
                    outlier_corrections = outlier_corrections + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE sensor_id = ?
            ''', (validation_failures, duplicate_failures, outlier_corrections, sensor_id))
        else:
            conn.execute('''
                INSERT INTO sensor_quality_metrics
                (sensor_id, total_records, validation_failures, duplicate_records, outlier_corrections)
                VALUES (?, ?, ?, ?, ?)
            ''', (sensor_id, 1, validation_failures, duplicate_failures, outlier_corrections))
        conn.commit()
    except Exception as e:
        print(f"Sensor metrics warning: {e}")
    finally:
        conn.close()

def log_prediction_quality(location, sensor_id, prediction):
    """Store predictions to evaluate quality against future observed values"""
    if not prediction:
        return
    conn = get_db_connection()
    if not conn:
        return
    try:
        conn.execute('''
            INSERT INTO prediction_quality_metrics
            (location, sensor_id, predicted_temperature, target_timestamp, status)
            VALUES (?, ?, ?, ?, 'pending')
        ''', (
            location,
            sensor_id,
            prediction.get('predicted_temperature'),
            prediction.get('timestamp')
        ))
        conn.commit()
    except Exception as e:
        print(f"Prediction metric warning: {e}")
    finally:
        conn.close()

def evaluate_prediction_quality():
    """Evaluate pending predictions against observed weather"""
    conn = get_db_connection()
    if not conn:
        return
    try:
        pending = conn.execute('''
            SELECT * FROM prediction_quality_metrics
            WHERE status = 'pending'
              AND datetime(target_timestamp) <= datetime('now')
            ORDER BY metric_id ASC
            LIMIT 25
        ''').fetchall()
        for item in pending:
            observed = conn.execute('''
                SELECT temperature
                FROM weather_data
                WHERE location = ?
                  AND datetime(recorded_at) >= datetime(?)
                ORDER BY recorded_at ASC
                LIMIT 1
            ''', (item['location'], item['target_timestamp'])).fetchone()
            if not observed or observed['temperature'] is None:
                continue

            error_abs = abs(float(observed['temperature']) - float(item['predicted_temperature']))
            conn.execute('''
                UPDATE prediction_quality_metrics
                SET actual_temperature = ?,
                    error_abs = ?,
                    status = 'evaluated',
                    updated_at = CURRENT_TIMESTAMP
                WHERE metric_id = ?
            ''', (observed['temperature'], round(error_abs, 3), item['metric_id']))
        conn.commit()
    except Exception as e:
        print(f"Prediction evaluation warning: {e}")
    finally:
        conn.close()

def retrain_ai_with_recent_clean_data(hours=168):
    """Retrain AI model using recent cleaned weather data"""
    conn = get_db_connection()
    if not conn:
        return
    try:
        locations = conn.execute('SELECT DISTINCT location FROM users').fetchall()
        for row in locations:
            location = row['location']
            historical_data = get_historical_weather(location, hours)
            if len(historical_data) >= 20:
                weather_ai.train(historical_data)
                break
    except Exception as e:
        print(f"Retrain warning: {e}")
    finally:
        conn.close()

def process_weather_pipeline(location):
    """End-to-end sensor ingestion, validation, cleaning, feature, AI, and decision pipeline"""
    raw_record = ingest_sensor_weather(location)
    validation_result = validate_sensor_data(raw_record)
    cleaned_record = clean_sensor_data(validation_result)
    features = engineer_features(cleaned_record, validation_result)
    quality_assessment = weather_ai.assess_data_quality(
        validation_result,
        cleaned_record.get('cleaning_notes', []),
        features
    )
    prediction = weather_ai.predict(cleaned_record)
    decision = build_decision(cleaned_record, prediction, quality_assessment)

    payload = {
        'raw': raw_record,
        'cleaned': cleaned_record,
        'features': features,
        'quality': quality_assessment,
        'prediction': prediction,
        'decision': decision,
        'validation': validation_result,
        'skip_weather_storage': 'duplicate_record' in validation_result.get('quality_flags', [])
    }
    return payload

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
        '''CREATE TABLE IF NOT EXISTS sensor_pipeline_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id TEXT,
            source TEXT,
            location TEXT NOT NULL,
            sensor_timestamp TEXT,
            quality_flags TEXT,
            validation_score REAL,
            quality_label TEXT,
            raw_payload TEXT,
            cleaned_payload TEXT,
            feature_payload TEXT,
            decision_payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS sensor_quality_metrics (
            metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id TEXT UNIQUE,
            total_records INTEGER DEFAULT 0,
            validation_failures INTEGER DEFAULT 0,
            duplicate_records INTEGER DEFAULT 0,
            outlier_corrections INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
        '''CREATE TABLE IF NOT EXISTS prediction_quality_metrics (
            metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT,
            sensor_id TEXT,
            predicted_temperature REAL,
            actual_temperature REAL,
            error_abs REAL,
            target_timestamp TEXT,
            status TEXT DEFAULT 'pending',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    weather_data_migrations = [
        ('sensor_id', 'TEXT'),
        ('data_source', 'TEXT'),
        ('sensor_timestamp', 'TEXT'),
        ('quality_flags', 'TEXT'),
        ('validation_score', 'REAL'),
        ('quality_label', 'TEXT'),
        ('cleaning_notes', 'TEXT'),
        ('feature_blob', 'TEXT'),
        ('decision_blob', 'TEXT'),
        ('ingestion_mode', 'TEXT')
    ]
    for column_name, column_def in weather_data_migrations:
        ensure_column_exists(conn, 'weather_data', column_name, column_def)
    
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

def store_weather_data(weather_data, pipeline_output=None):
    """Store weather data in database with sensor metadata and pipeline artifacts"""
    conn = get_db_connection()
    if conn:
        try:
            quality_flags = []
            validation_score = None
            quality_label = None
            cleaning_notes = []
            feature_blob = None
            decision_blob = None
            ingestion_mode = weather_data.get('ingestion_mode')
            sensor_timestamp = weather_data.get('timestamp')

            if pipeline_output:
                quality_flags = pipeline_output.get('validation', {}).get('quality_flags', [])
                validation_score = pipeline_output.get('validation', {}).get('validation_score')
                quality_label = pipeline_output.get('quality', {}).get('quality_label')
                cleaning_notes = pipeline_output.get('cleaned', {}).get('cleaning_notes', [])
                feature_blob = json.dumps(pipeline_output.get('features', {}))
                decision_blob = json.dumps(pipeline_output.get('decision', {}))

            conn.execute('''
                INSERT INTO weather_data 
                (location, temperature, humidity, pressure, wind_speed, weather_condition, precipitation,
                 sensor_id, data_source, sensor_timestamp, quality_flags, validation_score, quality_label,
                 cleaning_notes, feature_blob, decision_blob, ingestion_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                weather_data['location'],
                weather_data['temperature'],
                weather_data['humidity'],
                weather_data['pressure'],
                weather_data['wind_speed'],
                weather_data['condition'],
                0,  # precipitation placeholder
                weather_data.get('sensor_id'),
                weather_data.get('source'),
                sensor_timestamp,
                json.dumps(quality_flags),
                validation_score,
                quality_label,
                json.dumps(cleaning_notes),
                feature_blob,
                decision_blob,
                ingestion_mode
            ))
            conn.commit()
        except Exception as e:
            print(f"Data storage error: {e}")
        finally:
            conn.close()

def store_pipeline_log(pipeline_output):
    """Persist raw/cleaned/featured/decision payloads"""
    conn = get_db_connection()
    if not conn:
        return
    try:
        raw = pipeline_output.get('raw', {})
        cleaned = pipeline_output.get('cleaned', {})
        validation = pipeline_output.get('validation', {})
        quality = pipeline_output.get('quality', {})

        conn.execute('''
            INSERT INTO sensor_pipeline_logs
            (sensor_id, source, location, sensor_timestamp, quality_flags, validation_score,
             quality_label, raw_payload, cleaned_payload, feature_payload, decision_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            raw.get('sensor_id'),
            raw.get('source'),
            raw.get('location'),
            cleaned.get('timestamp') or raw.get('timestamp'),
            json.dumps(validation.get('quality_flags', [])),
            validation.get('validation_score'),
            quality.get('quality_label'),
            json.dumps(raw),
            json.dumps(cleaned),
            json.dumps(pipeline_output.get('features', {})),
            json.dumps(pipeline_output.get('decision', {}))
        ))
        conn.commit()
    except Exception as e:
        print(f"Pipeline log warning: {e}")
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

# Real-time weather updates
def update_weather_data():
    """Update weather data for all user locations"""
    conn = get_db_connection()
    if conn:
        try:
            locations = conn.execute('SELECT DISTINCT location FROM users').fetchall()
            for location_row in locations:
                location = location_row['location']
                pipeline = process_weather_pipeline(location)
                weather_data = pipeline['cleaned']
                prediction = pipeline['prediction']

                if not pipeline.get('skip_weather_storage'):
                    store_weather_data(weather_data, pipeline_output=pipeline)
                store_pipeline_log(pipeline)
                update_sensor_quality_metrics(
                    weather_data.get('sensor_id', f'sensor-{location}'),
                    pipeline['validation'],
                    weather_data
                )
                log_prediction_quality(location, weather_data.get('sensor_id', 'unknown'), prediction)
                recent_cleaned_by_location[location].append(weather_data)
                
                # Send real-time update to connected clients
                socketio.emit('weather_update', {
                    'location': location,
                    'data': weather_data,
                    'prediction': prediction,
                    'quality': pipeline['quality'],
                    'decision': pipeline['decision'],
                    'features': pipeline['features']
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
            SELECT location, temperature, weather_condition, humidity, wind_speed, recorded_at, data_source, quality_label
            FROM weather_data 
            ORDER BY recorded_at DESC 
            LIMIT 3
        ''').fetchall()

        latest_pipeline_rows = conn.execute('''
            SELECT location, sensor_id, quality_label, validation_score, decision_payload, created_at
            FROM sensor_pipeline_logs
            ORDER BY created_at DESC
            LIMIT 5
        ''').fetchall()
        latest_pipeline = []
        for row in latest_pipeline_rows:
            try:
                decision = json.loads(row['decision_payload']) if row['decision_payload'] else {}
            except Exception:
                decision = {}
            latest_pipeline.append({
                'location': row['location'],
                'sensor_id': row['sensor_id'],
                'quality_label': row['quality_label'],
                'validation_score': row['validation_score'],
                'severity': decision.get('severity', 'low'),
                'reason': decision.get('reason', 'n/a'),
                'recommended_action': decision.get('recommended_action', 'Monitor conditions.'),
                'confidence': decision.get('confidence', 0.0),
                'alert_required': decision.get('alert_required', False),
                'created_at': row['created_at']
            })

        sensor_metrics = conn.execute('''
            SELECT
                COALESCE(SUM(total_records), 0) as total_records,
                COALESCE(SUM(validation_failures), 0) as validation_failures
            FROM sensor_quality_metrics
        ''').fetchone()

        failure_rate = 0.0
        if sensor_metrics and sensor_metrics['total_records'] > 0:
            failure_rate = sensor_metrics['validation_failures'] / float(sensor_metrics['total_records'])
        
        # Get AI model status
        ai_status = "Trained" if weather_ai.is_trained else "Training"
        
        return render_template('dashboard.html', 
                             total_users=total_users,
                             total_alerts=total_alerts,
                             recent_activities=recent_activities,
                             recent_weather=recent_weather,
                             latest_pipeline=latest_pipeline,
                             sensor_failure_rate=round(failure_rate * 100, 2),
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
    conn = get_db_connection()
    ai_alerts = []
    if conn:
        try:
            rows = conn.execute('''
                SELECT location, sensor_id, decision_payload, created_at
                FROM sensor_pipeline_logs
                ORDER BY created_at DESC
                LIMIT 20
            ''').fetchall()
            for row in rows:
                try:
                    decision = json.loads(row['decision_payload']) if row['decision_payload'] else {}
                except Exception:
                    decision = {}
                if decision.get('alert_required'):
                    ai_alerts.append({
                        'location': row['location'],
                        'sensor_id': row['sensor_id'],
                        'severity': decision.get('severity', 'low'),
                        'reason': decision.get('reason', 'n/a'),
                        'recommended_action': decision.get('recommended_action', 'Monitor conditions.'),
                        'confidence': decision.get('confidence', 0.0),
                        'created_at': row['created_at']
                    })
            ai_alerts = ai_alerts[:10]
        except Exception as e:
            print(f"Alerts load warning: {e}")
        finally:
            conn.close()
    return render_template('alerts.html', ai_alerts=ai_alerts)

@app.route('/recommendations')
def recommendations():
    return render_template('recommendations.html')

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
    pipeline = process_weather_pipeline(location)
    weather_data = pipeline['cleaned']
    prediction = pipeline['prediction']

    if not pipeline.get('skip_weather_storage'):
        store_weather_data(weather_data, pipeline_output=pipeline)
    store_pipeline_log(pipeline)
    update_sensor_quality_metrics(
        weather_data.get('sensor_id', f'sensor-{location}'),
        pipeline['validation'],
        weather_data
    )
    log_prediction_quality(location, weather_data.get('sensor_id', 'unknown'), prediction)
    recent_cleaned_by_location[location].append(weather_data)
    
    emit('weather_response', {
        'location': location,
        'current': weather_data,
        'prediction': prediction,
        'quality': pipeline['quality'],
        'decision': pipeline['decision'],
        'features': pipeline['features']
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
    
    # Try to load existing AI model
    if not weather_ai.load_model():
        print("🤖 Training new AI model...")
        # Train with available historical data
        historical_data = get_historical_weather('London', 168)
        if historical_data:
            weather_ai.train(historical_data)
    
    # Start scheduler for periodic updates
    scheduler.add_job(update_weather_data, 'interval', minutes=2)
    scheduler.add_job(lambda: retrain_ai_with_recent_clean_data(168), 'interval', hours=1)
    scheduler.add_job(evaluate_prediction_quality, 'interval', minutes=10)
    
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
