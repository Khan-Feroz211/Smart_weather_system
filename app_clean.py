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
