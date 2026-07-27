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
SENSOR_MODE = os.environ.get('SENSOR_MODE', 'simulation').lower()  # 'simulation' or 'real'

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
WEATHER_DATA_MIGRATION_COLUMNS = {
    'sensor_id': 'TEXT',
    'data_source': 'TEXT',
    'sensor_timestamp': 'TEXT',
    'quality_flags': 'TEXT',
    'validation_score': 'REAL',
    'quality_label': 'TEXT',
    'cleaning_notes': 'TEXT',
    'feature_blob': 'TEXT',
    'decision_blob': 'TEXT',
    'ingestion_mode': 'TEXT'
}
VALIDATION_PENALTY_PER_FLAG = 0.12
KMH_TO_MPS = 0.277778

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
        expected_def = WEATHER_DATA_MIGRATION_COLUMNS.get(column_name)
        if expected_def is None or expected_def != column_def:
            raise ValueError(f"Unsupported migration for column: {column_name}")

        existing = conn.execute("PRAGMA table_info(weather_data)").fetchall()
        column_names = {col['name'] for col in existing}
        if column_name not in column_names:
            conn.execute(f"ALTER TABLE weather_data ADD COLUMN {column_name} {column_def}")
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

    validation_score = max(0.0, 1.0 - (len(quality_flags) * VALIDATION_PENALTY_PER_FLAG))
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
    field_queries = {
        'temperature': "SELECT AVG(temperature) as avg_value FROM weather_data WHERE location = ? AND temperature IS NOT NULL",
        'humidity': "SELECT AVG(humidity) as avg_value FROM weather_data WHERE location = ? AND humidity IS NOT NULL",
        'pressure': "SELECT AVG(pressure) as avg_value FROM weather_data WHERE location = ? AND pressure IS NOT NULL",
        'wind_speed': "SELECT AVG(wind_speed) as avg_value FROM weather_data WHERE location = ? AND wind_speed IS NOT NULL"
    }
    conn = get_db_connection()
    if not conn:
        return None
    try:
        row = conn.execute(field_queries[field], (location,)).fetchone()
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

    recent_series = [
        item['temperature']
        for item in recent_cleaned_by_location[location]
        if 'temperature' in item and item['temperature'] is not None
    ]
    if len(recent_series) >= 5:
        median_temp = statistics.median(recent_series)
        if abs(record['temperature'] - median_temp) > 20:
            record['temperature'] = round((record['temperature'] + median_temp) / 2, 2)
            notes.append('outlier_temperature_smoothed')
            flags.append('outlier_temperature')

    if record['wind_speed'] > 70:
        record['wind_speed'] = round(record['wind_speed'] * KMH_TO_MPS, 2)
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

    temp_history = [item['temperature'] for item in recent_records if 'temperature' in item and item['temperature'] is not None]
    humidity_history = [item['humidity'] for item in recent_records if 'humidity' in item and item['humidity'] is not None]
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
    with app.app_context():
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

    for column_name, column_def in WEATHER_DATA_MIGRATION_COLUMNS.items():
        ensure_column_exists(conn, 'weather_data', column_name, column_def)
    
    # Insert sample data for demo
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO users (username, email, location, preferences)
            VALUES (?, ?, ?, ?)
        ''', ('farm_owner', 'owner@agri.pk', 'Lahore', json.dumps({
            'preferred_activities': ['farming', 'irrigation']
        })))
        
        # Insert sample weather data for Pakistan cities
        sample_weather = [
            ('Lahore', 32.5, 45, 1013, 12, 'Sunny', 0),
            ('Islamabad', 28.1, 60, 1015, 8, 'Cloudy', 0),
            ('Karachi', 35.8, 55, 1010, 15, 'Sunny', 5),
            ('Peshawar', 30.3, 40, 1012, 10, 'Clear', 0),
            ('Quetta', 25.7, 35, 1014, 18, 'Sunny', 0)
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
        )''',
        '''CREATE TABLE IF NOT EXISTS raw_sensor_readings (
            reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id TEXT NOT NULL,
            sensor_type TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT,
            timestamp TEXT NOT NULL,
            field_id INTEGER,
            received_at TEXT NOT NULL,
            raw_payload TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS validated_sensor_readings (
            reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id TEXT NOT NULL,
            sensor_type TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT,
            quality_score REAL DEFAULT 1.0,
            validation_reason TEXT,
            timestamp TEXT NOT NULL,
            field_id INTEGER,
            stored_at TEXT NOT NULL
        )''',
        '''CREATE TABLE IF NOT EXISTS sensor_health (
            sensor_id TEXT PRIMARY KEY,
            last_reading_at TEXT,
            uptime_percentage REAL DEFAULT 100.0,
            quality_score_avg REAL DEFAULT 1.0,
            calibration_date TEXT,
            status TEXT DEFAULT 'active'
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
            (1, 2, '2026-05-11', 25.0, 'Soil moisture below threshold - irrigation recommended',  0),
            (2, 4, '2026-05-11', 30.0, 'High evapotranspiration rate detected',  0),
            (3, 3, '2026-05-12', 18.0, 'Crop water stress indicator active',  0),
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
    with app.app_context():
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
    return redirect(url_for('agri_alerts_view'))


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
    location = data.get('location', 'Lahore')
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
    init_agriculture_database()
    
    # Try to load existing AI model
    if not weather_ai.load_model():
        print("🤖 Training new AI model...")
        # Train with available historical data
        historical_data = get_historical_weather('London', 168)
        if historical_data:
            weather_ai.train(historical_data)
    
    # Start scheduler for periodic updates
    # Temporarily disabled to fix application context issues
    # scheduler.add_job(lambda: update_weather_data(), 'interval', minutes=2)
    # scheduler.add_job(lambda: retrain_ai_with_recent_clean_data(168), 'interval', hours=1)
    # scheduler.add_job(lambda: evaluate_prediction_quality(), 'interval', minutes=10)
    
    # if not scheduler.running:
    #     scheduler.start()
    #     print("⏰ Scheduler started")
    
    print("✅ Smart Weather System ready!")

# ============================================================
# SENSOR INGESTION ROUTES
# ============================================================

@app.route('/api/sensors/mode', methods=['GET'])
def get_sensor_mode():
    """Get current sensor mode (simulation or real)"""
    return jsonify({
        'mode': SENSOR_MODE,
        'description': 'simulation' if SENSOR_MODE == 'simulation' else 'real sensors via MQTT/HTTP'
    })

@app.route('/api/sensors/mode', methods=['POST'])
def set_sensor_mode():
    """Set sensor mode (simulation or real)"""
    try:
        data = request.json
        mode = data.get('mode', 'simulation').lower()
        
        if mode not in ['simulation', 'real']:
            return jsonify({'error': 'Invalid mode. Use "simulation" or "real"'}), 400
        
        # Update environment variable in .env file
        import os as os_module
        from pathlib import Path
        
        env_path = Path('.env')
        if env_path.exists():
            env_content = env_path.read_text()
            # Update or add SENSOR_MODE line
            if 'SENSOR_MODE=' in env_content:
                lines = env_content.split('\n')
                for i, line in enumerate(lines):
                    if line.startswith('SENSOR_MODE='):
                        lines[i] = f'SENSOR_MODE={mode}'
                        break
                env_content = '\n'.join(lines)
            else:
                env_content += f'\nSENSOR_MODE={mode}'
            
            env_path.write_text(env_content)
        
        # Update global variable
        global SENSOR_MODE
        SENSOR_MODE = mode
        
        # Restart sensor ingestion if running
        if mode == 'simulation':
            return jsonify({
                'success': True,
                'mode': mode,
                'message': 'Switched to simulation mode. System will generate synthetic sensor data.'
            })
        else:
            return jsonify({
                'success': True,
                'mode': mode,
                'message': 'Switched to real sensor mode. Connect your sensors via MQTT/HTTP.'
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sensors/ingest', methods=['POST'])
def sensor_ingest():
    """Receive sensor data via HTTP (fallback for MQTT)"""
    try:
        # Check if in simulation mode
        if SENSOR_MODE == 'simulation':
            from sensor_simulation import SensorSimulator
            simulator = SensorSimulator()
            reading = simulator.generate_sensor_reading(
                request.json.get('sensor_type', 'soil_moisture'),
                request.json.get('field_id', 1)
            )
            # Use simulated data instead of real sensor data
            payload = reading
        else:
            payload = request.json
            if not payload:
                return jsonify({'error': 'No payload provided'}), 400
            
            # Validate required fields
            required = ['sensor_id', 'sensor_type', 'value', 'timestamp']
            for field in required:
                if field not in payload:
                    return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Import sensor ingestion components
        from sensor_ingestion import SensorValidator, SensorDatabase
        
        validator = SensorValidator()
        db = SensorDatabase()
        
        # Validate reading
        validation = validator.validate(
            payload['sensor_type'],
            float(payload['value']),
            payload['sensor_id']
        )
        
        # Store raw reading
        db.store_raw_reading({
            'sensor_id': payload['sensor_id'],
            'sensor_type': payload['sensor_type'],
            'value': float(payload['value']),
            'unit': payload.get('unit'),
            'timestamp': payload['timestamp'],
            'field_id': payload.get('field_id')
        })
        
        # Store validated reading if quality > 0
        if validation['quality_score'] > 0:
            db.store_validated_reading({
                'sensor_id': payload['sensor_id'],
                'sensor_type': payload['sensor_type'],
                'value': float(payload['value']),
                'unit': payload.get('unit'),
                'timestamp': payload['timestamp'],
                'field_id': payload.get('field_id')
            }, validation)
            db.update_sensor_health(payload['sensor_id'], validation['quality_score'])
        
        return jsonify({
            'success': True,
            'sensor_id': payload['sensor_id'],
            'validation': validation
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sensors/health')
def sensor_health_status():
    """Get health status of all sensors"""
    conn = get_db_connection()
    try:
        sensors = conn.execute('SELECT * FROM sensor_health ORDER BY last_reading_at DESC').fetchall()
        return jsonify([dict(s) for s in sensors])
    finally:
        conn.close()

@app.route('/api/sensors/readings/<sensor_type>')
def sensor_readings(sensor_type):
    """Get validated readings for a sensor type"""
    conn = get_db_connection()
    try:
        field_id = request.args.get('field_id', type=int)
        hours = request.args.get('hours', 24, type=int)
        
        query = '''
            SELECT * FROM validated_sensor_readings
            WHERE sensor_type = ?
            AND timestamp >= datetime('now', '-{} hours')
        '''.format(hours)
        
        params = [sensor_type]
        if field_id:
            query += ' AND field_id = ?'
            params.append(field_id)
        
        query += ' ORDER BY timestamp DESC LIMIT 1000'
        
        readings = conn.execute(query, params).fetchall()
        return jsonify([dict(r) for r in readings])
    finally:
        conn.close()

@app.route('/api/sensors/export')
def sensor_data_export():
    """Export sensor data for research (CSV/JSON/Parquet)"""
    conn = get_db_connection()
    try:
        format_type = request.args.get('format', 'csv')
        sensor_type = request.args.get('sensor_type')
        hours = request.args.get('hours', 168, type=int)  # Default 7 days
        
        query = '''
            SELECT * FROM validated_sensor_readings
            WHERE timestamp >= datetime('now', '-{} hours')
        '''.format(hours)
        
        params = []
        if sensor_type:
            query += ' AND sensor_type = ?'
            params.append(sensor_type)
        
        query += ' ORDER BY timestamp DESC'
        
        readings = conn.execute(query, params).fetchall()
        
        if format_type == 'csv':
            import csv
            from io import StringIO
            output = StringIO()
            writer = csv.DictWriter(output, fieldnames=[r[0] for r in conn.description])
            writer.writeheader()
            writer.writerows([dict(r) for r in readings])
            response = Response(output.getvalue(), mimetype='text/csv')
            response.headers['Content-Disposition'] = 'attachment; filename=sensor_data.csv'
            return response
        else:
            return jsonify([dict(r) for r in readings])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

# ============================================================
# DATA PROCESSING ROUTES
# ============================================================

@app.route('/api/processing/clean/<sensor_type>')
def clean_sensor_data(sensor_type):
    """Clean and validate sensor data with anomaly detection"""
    from data_processing import DataProcessingPipeline
    
    try:
        field_id = request.args.get('field_id', type=int)
        hours = request.args.get('hours', 168, type=int)
        
        pipeline = DataProcessingPipeline()
        result = pipeline.process_sensor_data(sensor_type, field_id, hours)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/processing/field/<int:field_id>')
def process_field_data(field_id):
    """Process all sensor data for a field"""
    from data_processing import DataProcessingPipeline
    
    try:
        hours = request.args.get('hours', 168, type=int)
        
        pipeline = DataProcessingPipeline()
        results = pipeline.process_all_field_sensors(field_id, hours)
        
        return jsonify({
            'success': True,
            'field_id': field_id,
            'sensor_results': results
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/processing/quality/<int:field_id>')
def field_data_quality(field_id):
    """Get data quality scores for all sensors in a field"""
    from data_processing import DataProcessingPipeline, DataQualityScorer
    
    try:
        pipeline = DataProcessingPipeline()
        quality_scores = {}
        
        sensor_types = ['soil_moisture', 'soil_temperature', 'air_temperature', 'air_humidity']
        
        for sensor_type in sensor_types:
            df = pipeline.get_validated_sensor_data(sensor_type, field_id, hours=168)
            if len(df) > 0:
                quality_scores[sensor_type] = DataQualityScorer.compute_overall_quality_score(df, sensor_type)
        
        return jsonify({
            'success': True,
            'field_id': field_id,
            'quality_scores': quality_scores
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/processing/features/gdd')
def calculate_gdd():
    """Calculate Growing Degree Days from temperature data"""
    from data_processing import FeatureEngineer
    
    try:
        field_id = request.args.get('field_id', type=int)
        hours = request.args.get('hours', 168, type=int)
        base_temp = request.args.get('base_temp', 10.0, type=float)
        
        from data_processing import DataProcessingPipeline
        pipeline = DataProcessingPipeline()
        
        # Fetch temperature data
        df = pipeline.get_validated_sensor_data('air_temperature', field_id, hours)
        
        if len(df) == 0:
            return jsonify({'success': False, 'message': 'No temperature data available'})
        
        temperatures = df['value'].tolist()
        gdd = FeatureEngineer.calculate_growing_degree_days(temperatures, base_temp)
        
        return jsonify({
            'success': True,
            'growing_degree_days': round(gdd, 2),
            'base_temperature': base_temp,
            'data_points': len(temperatures)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/processing/features/et')
def calculate_et():
    """Calculate reference evapotranspiration using Penman-Monteith"""
    from data_processing import FeatureEngineer
    
    try:
        field_id = request.args.get('field_id', type=int)
        
        conn = get_db_connection()
        try:
            # Fetch latest sensor readings
            readings = conn.execute('''
                SELECT sensor_type, value FROM validated_sensor_readings
                WHERE field_id = ?
                AND timestamp >= datetime('now', '-1 hour')
                GROUP BY sensor_type
            ''', (field_id,)).fetchall()
            
            sensor_dict = {row['sensor_type']: row['value'] for row in readings}
            
            et = FeatureEngineer.calculate_evapotranspiration_penman_monteith(
                temperature=sensor_dict.get('air_temperature', 20.0),
                humidity=sensor_dict.get('air_humidity', 50.0),
                wind_speed=sensor_dict.get('wind_speed', 5.0),
                solar_radiation=sensor_dict.get('solar_radiation', 200.0),
                pressure=1013.0
            )
            
            return jsonify({
                'success': True,
                'evapotranspiration_mm_day': round(et, 2),
                'sensor_data': sensor_dict
            })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/processing/features/stress')
def calculate_stress_indices():
    """Calculate stress indices from sensor data"""
    from data_processing import FeatureEngineer
    
    try:
        field_id = request.args.get('field_id', type=int)
        
        conn = get_db_connection()
        try:
            readings = conn.execute('''
                SELECT sensor_type, value FROM validated_sensor_readings
                WHERE field_id = ?
                AND timestamp >= datetime('now', '-1 hour')
                GROUP BY sensor_type
            ''', (field_id,)).fetchall()
            
            sensor_dict = {row['sensor_type']: row['value'] for row in readings}
            
            stress_indices = FeatureEngineer.calculate_stress_indices(sensor_dict)
            
            return jsonify({
                'success': True,
                'stress_indices': stress_indices,
                'sensor_data': sensor_dict
            })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# CROP FAILURE PREDICTION ROUTES
# ============================================================

@app.route('/api/prediction/failure/<int:field_id>')
def predict_crop_failure(field_id):
    """Predict crop failure probability with confidence intervals"""
    from crop_failure_predictor import CropFailureService
    
    try:
        service = CropFailureService()
        result = service.predict_crop_failure(field_id)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prediction/train')
def train_crop_failure_model():
    """Train crop failure prediction model (with synthetic data for demo)"""
    from crop_failure_predictor import CropFailureService
    
    try:
        service = CropFailureService()
        success = service.train_model_with_synthetic_data()
        
        return jsonify({
            'success': success,
            'model_metadata': service.predictor.training_metadata
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/prediction/explain/<int:field_id>')
def explain_crop_failure_prediction(field_id):
    """Explain crop failure prediction using SHAP values"""
    from crop_failure_predictor import CropFailureService
    
    try:
        service = CropFailureService()
        
        # Get sensor data
        sensor_data = service.get_sensor_data_for_field(field_id)
        crop_info = service.get_crop_info_for_field(field_id)
        
        if not sensor_data or not crop_info:
            return jsonify({'success': False, 'error': 'Insufficient data for explanation'})
        
        # Prepare features
        features = service.predictor.prepare_features(sensor_data, crop_info)
        
        # Explain
        explanation = service.predictor.explain_prediction(features)
        
        return jsonify({
            'success': True,
            'field_id': field_id,
            'explanation': explanation,
            'feature_names': service.predictor.feature_names
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# TREND VISUALIZATION ROUTES
# ============================================================

@app.route('/api/visualization/trend/historical/<sensor_type>')
def visualize_historical_trend(sensor_type):
    """Generate historical trend chart for sensor data"""
    from trend_visualization import TrendVisualizer
    
    try:
        field_id = request.args.get('field_id', type=int)
        hours = request.args.get('hours', 720, type=int)  # Default 30 days
        
        visualizer = TrendVisualizer()
        result = visualizer.generate_historical_trend(sensor_type, field_id, hours)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/visualization/trend/forecast/<sensor_type>')
def visualize_forecast_trend(sensor_type):
    """Generate forecast trend chart with uncertainty bands"""
    from trend_visualization import TrendVisualizer
    
    try:
        field_id = request.args.get('field_id', type=int)
        forecast_hours = request.args.get('forecast_hours', 168, type=int)  # Default 7 days
        
        visualizer = TrendVisualizer()
        result = visualizer.generate_forecast_trend(sensor_type, field_id, forecast_hours)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/visualization/trend/health/<int:field_id>')
def visualize_health_trend(field_id):
    """Generate crop health trend chart"""
    from trend_visualization import TrendVisualizer
    
    try:
        hours = request.args.get('hours', 720, type=int)
        
        visualizer = TrendVisualizer()
        result = visualizer.generate_health_trend(field_id, hours)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/visualization/trend/yield/<int:field_id>')
def visualize_yield_forecast(field_id):
    """Generate yield forecast trend chart"""
    from trend_visualization import TrendVisualizer
    
    try:
        visualizer = TrendVisualizer()
        result = visualizer.generate_yield_forecast_trend(field_id)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/visualization/trend/comparative')
def visualize_comparative_trend():
    """Generate comparative trend chart for multiple sensor types"""
    from trend_visualization import TrendVisualizer
    
    try:
        field_id = request.args.get('field_id', type=int)
        hours = request.args.get('hours', 168, type=int)
        sensor_types = request.args.getlist('sensor_types')
        
        if not sensor_types:
            sensor_types = ['soil_moisture', 'air_temperature', 'air_humidity']
        
        visualizer = TrendVisualizer()
        result = visualizer.generate_comparative_trend(sensor_types, field_id, hours)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# EXPERIMENT TRACKING ROUTES
# ============================================================

@app.route('/api/experiments/start', methods=['POST'])
def start_experiment():
    """Start a new ML experiment"""
    from experiment_tracking import ExperimentTracker
    
    try:
        data = request.json
        tracker = ExperimentTracker()
        
        exp_id = tracker.start_experiment(
            experiment_name=data.get('experiment_name'),
            model_type=data.get('model_type'),
            description=data.get('description'),
            tags=data.get('tags'),
            parameters=data.get('parameters')
        )
        
        return jsonify({'success': True, 'experiment_id': exp_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/experiments/<experiment_id>/metrics', methods=['POST'])
def log_experiment_metrics(experiment_id):
    """Log metrics for an experiment"""
    from experiment_tracking import ExperimentTracker
    
    try:
        data = request.json
        tracker = ExperimentTracker()
        
        tracker.log_metrics(experiment_id, data.get('metrics'))
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/experiments/<experiment_id>/end', methods=['POST'])
def end_experiment(experiment_id):
    """End an experiment"""
    from experiment_tracking import ExperimentTracker
    
    try:
        data = request.json
        tracker = ExperimentTracker()
        
        tracker.end_experiment(experiment_id, status=data.get('status', 'completed'))
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/experiments')
def list_experiments():
    """List all experiments"""
    from experiment_tracking import ExperimentTracker
    
    try:
        model_type = request.args.get('model_type')
        status = request.args.get('status')
        limit = request.args.get('limit', 50, type=int)
        
        tracker = ExperimentTracker()
        experiments = tracker.list_experiments(model_type, status, limit)
        
        return jsonify({'success': True, 'experiments': experiments})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/experiments/<experiment_id>')
def get_experiment(experiment_id):
    """Get experiment details"""
    from experiment_tracking import ExperimentTracker
    
    try:
        tracker = ExperimentTracker()
        experiment = tracker.get_experiment(experiment_id)
        
        return jsonify({'success': True, 'experiment': experiment})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
    socketio.run(app, host='0.0.0.0', port=8000, debug=True, allow_unsafe_werkzeug=True)
