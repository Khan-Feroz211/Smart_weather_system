"""
Sensor Ingestion Service - MQTT Consumer for Real-Time Sensor Data
Handles real-time sensor data from IoT devices via MQTT broker
"""
import json
import sqlite3
import structlog
from datetime import datetime
from typing import Dict, Any
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import os

load_dotenv()

logger = structlog.get_logger()

# Sensor type definitions with validation rules
SENSOR_TYPES = {
    'soil_moisture': {
        'min': 0,
        'max': 100,
        'unit': '%',
        'description': 'Soil moisture percentage'
    },
    'soil_temperature': {
        'min': -20,
        'max': 60,
        'unit': '°C',
        'description': 'Soil temperature in Celsius'
    },
    'soil_ph': {
        'min': 0,
        'max': 14,
        'unit': 'pH',
        'description': 'Soil pH level'
    },
    'soil_ec': {
        'min': 0,
        'max': 5000,
        'unit': 'µS/cm',
        'description': 'Electrical conductivity'
    },
    'air_temperature': {
        'min': -40,
        'max': 60,
        'unit': '°C',
        'description': 'Air temperature'
    },
    'air_humidity': {
        'min': 0,
        'max': 100,
        'unit': '%',
        'description': 'Relative humidity'
    },
    'rainfall': {
        'min': 0,
        'max': 500,
        'unit': 'mm',
        'description': 'Rainfall amount'
    },
    'wind_speed': {
        'min': 0,
        'max': 150,
        'unit': 'km/h',
        'description': 'Wind speed'
    },
    'solar_radiation': {
        'min': 0,
        'max': 1400,
        'unit': 'W/m²',
        'description': 'Solar radiation'
    },
    'ndvi': {
        'min': -1,
        'max': 1,
        'unit': 'index',
        'description': 'Normalized Difference Vegetation Index'
    }
}


class SensorValidator:
    """Validates sensor readings against expected ranges and quality rules"""
    
    def __init__(self):
        self.sensor_history = {}  # Track recent readings for anomaly detection
    
    def validate(self, sensor_type: str, value: float, sensor_id: str) -> Dict[str, Any]:
        """
        Validate a sensor reading
        Returns: {'valid': bool, 'reason': str, 'quality_score': float}
        """
        if sensor_type not in SENSOR_TYPES:
            return {
                'valid': False,
                'reason': f'Unknown sensor type: {sensor_type}',
                'quality_score': 0.0
            }
        
        rules = SENSOR_TYPES[sensor_type]
        
        # Range check
        if value < rules['min'] or value > rules['max']:
            return {
                'valid': False,
                'reason': f'Value {value} outside valid range [{rules["min"]}, {rules["max"]}]',
                'quality_score': 0.0
            }
        
        # Anomaly detection based on recent history
        quality_score = 1.0
        anomaly_reason = None
        
        key = f'{sensor_id}_{sensor_type}'
        if key in self.sensor_history:
            recent_values = self.sensor_history[key]
            mean = sum(recent_values) / len(recent_values)
            std = (sum((x - mean) ** 2 for x in recent_values) / len(recent_values)) ** 0.5
            
            # Flag as anomaly if >3 standard deviations from mean
            if std > 0 and abs(value - mean) > 3 * std:
                quality_score = 0.5
                anomaly_reason = f'Anomaly: {value} deviates {abs(value - mean)/std:.1f}σ from mean {mean:.2f}'
        
        # Update history (keep last 10 readings)
        if key not in self.sensor_history:
            self.sensor_history[key] = []
        self.sensor_history[key].append(value)
        if len(self.sensor_history[key]) > 10:
            self.sensor_history[key].pop(0)
        
        return {
            'valid': True,
            'reason': anomaly_reason or 'OK',
            'quality_score': quality_score
        }


class SensorDatabase:
    """Handles storage of raw and validated sensor data"""
    
    def __init__(self, db_path='smart_weather.db'):
        self.db_path = db_path
        self.init_tables()
    
    def init_tables(self):
        """Create sensor data tables if they don't exist"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Raw sensor readings (never modified - archival)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS raw_sensor_readings (
                reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id TEXT NOT NULL,
                sensor_type TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                timestamp TEXT NOT NULL,
                field_id INTEGER,
                received_at TEXT NOT NULL,
                raw_payload TEXT
            )
        ''')
        
        # Validated sensor readings (after quality checks)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS validated_sensor_readings (
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
            )
        ''')
        
        # Sensor health tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sensor_health (
                sensor_id TEXT PRIMARY KEY,
                last_reading_at TEXT,
                uptime_percentage REAL DEFAULT 100.0,
                quality_score_avg REAL DEFAULT 1.0,
                calibration_date TEXT,
                status TEXT DEFAULT 'active'
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("sensor_database_init", message="Sensor tables initialized")
    
    def store_raw_reading(self, reading: Dict[str, Any]) -> int:
        """Store a raw sensor reading"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO raw_sensor_readings
            (sensor_id, sensor_type, value, unit, timestamp, field_id, received_at, raw_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            reading['sensor_id'],
            reading['sensor_type'],
            reading['value'],
            reading.get('unit'),
            reading['timestamp'],
            reading.get('field_id'),
            datetime.now().isoformat(),
            json.dumps(reading)
        ))
        
        reading_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return reading_id
    
    def store_validated_reading(self, reading: Dict[str, Any], validation: Dict[str, Any]) -> int:
        """Store a validated sensor reading"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO validated_sensor_readings
            (sensor_id, sensor_type, value, unit, quality_score, validation_reason, timestamp, field_id, stored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            reading['sensor_id'],
            reading['sensor_type'],
            reading['value'],
            reading.get('unit'),
            validation['quality_score'],
            validation['reason'],
            reading['timestamp'],
            reading.get('field_id'),
            datetime.now().isoformat()
        ))
        
        reading_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return reading_id
    
    def update_sensor_health(self, sensor_id: str, quality_score: float):
        """Update sensor health metrics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO sensor_health
            (sensor_id, last_reading_at, quality_score_avg, status)
            VALUES (?, ?, ?, 'active')
        ''', (
            sensor_id,
            datetime.now().isoformat(),
            quality_score
        ))
        
        conn.commit()
        conn.close()


class SensorIngestionService:
    """Main MQTT consumer for sensor data ingestion"""
    
    def __init__(self):
        self.validator = SensorValidator()
        self.db = SensorDatabase()
        self.mqtt_client = mqtt.Client(client_id="agri_sensor_ingestion", protocol=mqtt.MQTTv311)
        self.setup_mqtt()
    
    def setup_mqtt(self):
        """Configure MQTT client"""
        mqtt_broker = os.environ.get('MQTT_BROKER_URL', 'localhost')
        mqtt_port = int(os.environ.get('MQTT_BROKER_PORT', 1883))
        
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.mqtt_client.on_disconnect = self.on_disconnect
        
        logger.info("mqtt_setup", broker=mqtt_broker, port=mqtt_port)
    
    def on_connect(self, client, userdata, flags, rc):
        """Callback when MQTT broker connects"""
        if rc == 0:
            logger.info("mqtt_connected")
            # Subscribe to all sensor topics
            client.subscribe("sensors/+/+", qos=1)
            client.subscribe("sensors/+/+/+", qos=1)
        else:
            logger.error("mqtt_connection_failed", code=rc)
    
    def on_disconnect(self, client, userdata, rc):
        """Callback when MQTT broker disconnects"""
        logger.warning("mqtt_disconnected", code=rc)
    
    def on_message(self, client, userdata, msg):
        """Callback when MQTT message received"""
        try:
            payload = json.loads(msg.payload.decode())
            self.process_sensor_reading(payload)
        except Exception as e:
            logger.error("sensor_processing_error", error=str(e), topic=msg.topic)
    
    def process_sensor_reading(self, payload: Dict[str, Any]):
        """Process an incoming sensor reading"""
        sensor_id = payload.get('sensor_id')
        sensor_type = payload.get('sensor_type')
        value = payload.get('value')
        timestamp = payload.get('timestamp', datetime.now().isoformat())
        field_id = payload.get('field_id')
        
        if not all([sensor_id, sensor_type, value is not None]):
            logger.warning("invalid_sensor_payload", payload=payload)
            return
        
        # Validate the reading
        validation = self.validator.validate(sensor_type, value, sensor_id)
        
        # Store raw reading (archival - never modified)
        self.db.store_raw_reading({
            'sensor_id': sensor_id,
            'sensor_type': sensor_type,
            'value': value,
            'unit': SENSOR_TYPES.get(sensor_type, {}).get('unit'),
            'timestamp': timestamp,
            'field_id': field_id
        })
        
        # Store validated reading if quality score > 0
        if validation['quality_score'] > 0:
            self.db.store_validated_reading({
                'sensor_id': sensor_id,
                'sensor_type': sensor_type,
                'value': value,
                'unit': SENSOR_TYPES.get(sensor_type, {}).get('unit'),
                'timestamp': timestamp,
                'field_id': field_id
            }, validation)
            
            # Update sensor health
            self.db.update_sensor_health(sensor_id, validation['quality_score'])
        
        logger.info("sensor_reading_processed",
                   sensor_id=sensor_id,
                   sensor_type=sensor_type,
                   value=value,
                   valid=validation['valid'],
                   quality=validation['quality_score'])
    
    def start(self):
        """Start the MQTT consumer"""
        mqtt_broker = os.environ.get('MQTT_BROKER_URL', 'localhost')
        mqtt_port = int(os.environ.get('MQTT_BROKER_PORT', 1883))
        
        self.mqtt_client.connect(mqtt_broker, mqtt_port, 60)
        self.mqtt_client.loop_forever()


if __name__ == '__main__':
    logger.info("sensor_ingestion_start")
    service = SensorIngestionService()
    service.start()
