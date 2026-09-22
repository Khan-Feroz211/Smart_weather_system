"""
Sensor Ingestion Service - MQTT Consumer for Real-Time Sensor Data
Handles real-time sensor data from IoT devices via MQTT broker
"""
import json
import structlog
from datetime import datetime, timezone
from typing import Dict, Any
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import os

load_dotenv()

import db

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
    """Stores raw and validated sensor data in Supabase PostgreSQL.

    Tables (raw_sensor_readings, validated_sensor_readings, sensor_health) are
    created by supabase/schema.sql.  `db_path` is accepted only for backwards
    compatibility with the old SQLite constructor and is ignored."""

    def __init__(self, db_path=None):
        self.db_path = db_path

    @staticmethod
    def _event_time(value):
        """Device timestamp -> aware UTC datetime (falls back to 'now' so a reading is never lost)."""
        return db.as_utc(value) or datetime.now(timezone.utc)

    @staticmethod
    def _int_or_none(value):
        try:
            return int(value) if value not in (None, '') else None
        except (TypeError, ValueError):
            return None

    def store_raw_reading(self, reading: Dict[str, Any]) -> int:
        """Store a raw sensor reading (archival, never modified)"""
        with db.connect() as conn:
            row = conn.execute('''
                INSERT INTO raw_sensor_readings
                (sensor_id, sensor_type, value, unit, timestamp, field_id, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING reading_id
            ''', (
                reading['sensor_id'],
                reading['sensor_type'],
                reading['value'],
                reading.get('unit'),
                self._event_time(reading['timestamp']),
                self._int_or_none(reading.get('field_id')),
                db.safe_json(reading)
            )).fetchone()
            conn.commit()
            return row[0]

    def store_validated_reading(self, reading: Dict[str, Any], validation: Dict[str, Any]) -> int:
        """Store a validated sensor reading"""
        with db.connect() as conn:
            row = conn.execute('''
                INSERT INTO validated_sensor_readings
                (sensor_id, sensor_type, value, unit, quality_score, validation_reason, timestamp, field_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING reading_id
            ''', (
                reading['sensor_id'],
                reading['sensor_type'],
                reading['value'],
                reading.get('unit'),
                validation['quality_score'],
                validation['reason'],
                self._event_time(reading['timestamp']),
                self._int_or_none(reading.get('field_id'))
            )).fetchone()
            conn.commit()
            return row[0]

    def update_sensor_health(self, sensor_id: str, quality_score: float):
        """Update sensor health metrics (upsert; keeps uptime / calibration date)"""
        with db.connect() as conn:
            conn.execute('''
                INSERT INTO sensor_health (sensor_id, last_reading_at, quality_score_avg, status)
                VALUES (%s, now(), %s, 'active')
                ON CONFLICT (sensor_id) DO UPDATE SET
                    last_reading_at = now(),
                    quality_score_avg = EXCLUDED.quality_score_avg,
                    status = 'active'
            ''', (sensor_id, quality_score))
            conn.commit()


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
