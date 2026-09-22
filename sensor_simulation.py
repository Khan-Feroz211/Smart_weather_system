"""
Sensor Simulation Module - Generate synthetic sensor data for demo/testing
Creates realistic sensor readings for agriculture use cases
"""
import random
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any
import json

class SensorSimulator:
    """Generate synthetic sensor data for agriculture monitoring"""
    
    def __init__(self, simulation_mode=True):
        self.simulation_mode = simulation_mode
        self.sensor_types = [
            'soil_moisture', 'soil_temperature', 'soil_ph', 'soil_ec',
            'air_temperature', 'air_humidity', 'rainfall', 'wind_speed',
            'solar_radiation', 'ndvi'
        ]
        self.field_ids = [1, 2, 3, 4, 5]
    
    def generate_sensor_reading(self, sensor_type: str, field_id: int = None) -> Dict[str, Any]:
        """Generate a realistic sensor reading based on type"""
        if field_id is None:
            field_id = random.choice(self.field_ids)
        
        timestamp = datetime.now().isoformat()
        
        # Generate realistic values based on sensor type
        if sensor_type == 'soil_moisture':
            value = round(random.uniform(25, 75), 1)  # 25-75%
        elif sensor_type == 'soil_temperature':
            value = round(random.uniform(15, 35), 1)  # 15-35°C
        elif sensor_type == 'soil_ph':
            value = round(random.uniform(6.0, 8.0), 1)  # pH 6-8
        elif sensor_type == 'soil_ec':
            value = round(random.uniform(200, 800), 0)  # µS/cm
        elif sensor_type == 'air_temperature':
            value = round(random.uniform(25, 40), 1)  # 25-40°C (Pakistan climate)
        elif sensor_type == 'air_humidity':
            value = round(random.uniform(30, 70), 0)  # 30-70%
        elif sensor_type == 'rainfall':
            value = round(random.uniform(0, 5), 1) if random.random() < 0.3 else 0  # 30% chance of rain
        elif sensor_type == 'wind_speed':
            value = round(random.uniform(2, 20), 1)  # km/h
        elif sensor_type == 'solar_radiation':
            value = round(random.uniform(100, 500), 0)  # W/m²
        elif sensor_type == 'ndvi':
            value = round(random.uniform(0.3, 0.8), 2)  # 0.3-0.8 (healthy vegetation)
        else:
            value = 0.0
        
        return {
            'sensor_id': f'{sensor_type}_{field_id}',
            'sensor_type': sensor_type,
            'value': value,
            'timestamp': timestamp,
            'field_id': field_id,
            'unit': self._get_unit(sensor_type)
        }
    
    def _get_unit(self, sensor_type: str) -> str:
        """Get the unit for a sensor type"""
        units = {
            'soil_moisture': '%',
            'soil_temperature': '°C',
            'soil_ph': 'pH',
            'soil_ec': 'µS/cm',
            'air_temperature': '°C',
            'air_humidity': '%',
            'rainfall': 'mm',
            'wind_speed': 'km/h',
            'solar_radiation': 'W/m²',
            'ndvi': ''
        }
        return units.get(sensor_type, '')
    
    def generate_batch_readings(self, count: int = 10) -> List[Dict[str, Any]]:
        """Generate a batch of sensor readings"""
        readings = []
        for _ in range(count):
            sensor_type = random.choice(self.sensor_types)
            reading = self.generate_sensor_reading(sensor_type)
            readings.append(reading)
        return readings
    
    def simulate_field_data(self, field_id: int, hours: int = 24) -> Dict[str, List[Dict[str, Any]]]:
        """Generate time-series data for a field"""
        field_data = {}
        for sensor_type in self.sensor_types:
            readings = []
            for i in range(hours):
                reading = self.generate_sensor_reading(sensor_type, field_id)
                # Adjust timestamp for historical data
                reading['timestamp'] = (datetime.now() - timedelta(hours=i)).isoformat()
                readings.append(reading)
            field_data[sensor_type] = readings
        return field_data


def inject_simulated_data():
    """Inject simulated sensor data into the system"""
    simulator = SensorSimulator()
    
    # Generate readings for all fields
    for field_id in simulator.field_ids:
        for sensor_type in simulator.sensor_types:
            reading = simulator.generate_sensor_reading(sensor_type, field_id)
            print(f"Simulated: {sensor_type} for field {field_id} = {reading['value']} {reading['unit']}")
    
    return simulator.generate_batch_readings(20)


if __name__ == '__main__':
    print("🌾 Sensor Simulation Mode")
    simulator = SensorSimulator()
    
    # Generate sample readings
    print("\nSample Sensor Readings:")
    for sensor_type in simulator.sensor_types[:5]:
        reading = simulator.generate_sensor_reading(sensor_type, 1)
        print(f"{sensor_type}: {reading['value']} {reading['unit']}")
    
    # Generate batch
    print("\nBatch Generation:")
    batch = simulator.generate_batch_readings(5)
    for reading in batch:
        print(f"{reading['sensor_type']} (Field {reading['field_id']}): {reading['value']}")
