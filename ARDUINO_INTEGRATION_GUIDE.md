# Arduino Integration Guide

## Overview

This guide explains how to integrate Arduino-based sensors with the Smart AgriWeather system. The system supports two modes:

1. **Simulation Mode** (Default) - Generates synthetic sensor data for demo/testing
2. **Real Sensor Mode** - Connects actual Arduino sensors via MQTT/HTTP

## Sensor Modes

### Simulation Mode (Default)

**Use when:** You don't have physical sensors yet or for testing

**Configuration:**
```env
SENSOR_MODE=simulation
```

**How it works:**
- System automatically generates realistic synthetic sensor data
- No hardware required
- Perfect for demonstrations and testing

### Real Sensor Mode

**Use when:** You have physical Arduino sensors connected

**Configuration:**
```env
SENSOR_MODE=real
```

**How it works:**
- System expects real sensor data via MQTT or HTTP
- Connect your Arduino sensors to the system
- Data is validated and processed in real-time

## Switching Between Modes

### Via API

**Get current mode:**
```bash
curl http://localhost:8000/api/sensors/mode
```

**Set mode:**
```bash
curl -X POST http://localhost:8000/api/sensors/mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "simulation"}'
```

or

```bash
curl -X POST http://localhost:8000/api/sensors/mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "real"}'
```

### Via .env File

Edit `.env` file:
```env
SENSOR_MODE=simulation  # or SENSOR_MODE=real
```

Restart the application after changing.

## Arduino Sensor Setup

### Minimal Setup (Beginner)

**Required Components:**
- Arduino Uno or ESP32
- Soil Moisture Sensor (Capacitive)
- Temperature & Humidity Sensor (DHT11/DHT22)
- Jumper wires
- Breadboard

**Total Cost:** ~$15-20 USD

**Connection (Arduino Uno):**
```
Soil Moisture Sensor:
- VCC → 3.3V or 5V
- GND → GND
- A0  → Analog Pin A0

DHT22 Temperature Sensor:
- VCC → 3.3V or 5V
- GND → GND
- DATA → Digital Pin 2
```

### Advanced Setup (Full System)

**Additional Components:**
- ESP32 (WiFi-enabled)
- Soil pH Sensor
- Soil EC Sensor
- Rain Sensor
- Wind Speed Sensor
- Solar Radiation Sensor
- LoRa or WiFi Module for transmission

**Total Cost:** ~$50-100 USD

## Arduino Code Example

### Basic Sensor Reading (Serial Output)

```cpp
#include <DHT.h>

#define DHTPIN 2
#define DHTTYPE DHT22
#define SOIL_MOISTURE_PIN A0

DHT dht(DHTPIN, DHTTYPE);

void setup() {
  Serial.begin(9600);
  dht.begin();
}

void loop() {
  delay(2000);
  
  // Read temperature and humidity
  float h = dht.readHumidity();
  float t = dht.readTemperature();
  
  // Read soil moisture
  int soilMoisture = analogRead(SOIL_MOISTURE_PIN);
  float moisturePercent = map(soilMoisture, 0, 1023, 100, 0);
  
  // Send to Serial (for testing)
  Serial.print("{\"sensor_id\":\"arduino-1\",\"sensor_type\":\"soil_moisture\",\"value\":");
  Serial.print(moisturePercent);
  Serial.print(",\"timestamp\":\"");
  Serial.print(millis());
  Serial.print("\",\"field_id\":1}");
  
  Serial.print("{\"sensor_id\":\"arduino-1\",\"sensor_type\":\"air_temperature\",\"value\":");
  Serial.print(t);
  Serial.print(",\"timestamp\":\"");
  Serial.print(millis());
  Serial.print("\",\"field_id\":1}");
  
  Serial.print("{\"sensor_id\":\"arduino-1\",\"sensor_type\":\"air_humidity\",\"value\":");
  Serial.print(h);
  Serial.print(",\"timestamp\":\"");
  Serial.print(millis());
  Serial.print("\",\"field_id\":1}");
}
```

### ESP32 with MQTT (Real-time Transmission)

```cpp
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// WiFi Configuration
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// MQTT Configuration
const char* mqtt_server = "YOUR_SERVER_IP"; // e.g., "192.168.1.100"
const int mqtt_port = 1883;

#define DHTPIN 4
#define DHTTYPE DHT22
#define SOIL_MOISTURE_PIN 34

DHT dht(DHTPIN, DHTTYPE);
WiFiClient espClient;
PubSubClient client(espClient);

void setup() {
  Serial.begin(115200);
  dht.begin();
  
  // Connect to WiFi
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  // Configure MQTT
  client.setServer(mqtt_server, mqtt_port);
}

void reconnect() {
  while (!client.connected()) {
    if (client.connect("ESP32Client")) {
      Serial.println("Connected to MQTT");
    } else {
      delay(5000);
    }
  }
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();
  
  delay(5000);
  
  // Read sensors
  float h = dht.readHumidity();
  float t = dht.readTemperature();
  int soilMoisture = analogRead(SOIL_MOISTURE_PIN);
  float moisturePercent = map(soilMoisture, 0, 4095, 100, 0);
  
  // Send to MQTT
  char payload[256];
  sprintf(payload, "{\"sensor_id\":\"esp32-1\",\"sensor_type\":\"soil_moisture\",\"value\":%.1f,\"timestamp\":\"%ld\",\"field_id\":1}", moisturePercent, millis());
  client.publish("sensors/soil_moisture/esp32-1", payload);
  
  sprintf(payload, "{\"sensor_id\":\"esp32-1\",\"sensor_type\":\"air_temperature\",\"value\":%.1f,\"timestamp\":\"%ld\",\"field_id\":1}", t, millis());
  client.publish("sensors/air_temperature/esp32-1", payload);
  
  sprintf(payload, "{\"sensor_id\":\"esp32-1\",\"sensor_type\":\"air_humidity\",\"value\":%.1f,\"timestamp\":\"%ld\",\"field_id\":1}", h, millis());
  client.publish("sensors/air_humidity/esp32-1", payload);
}
```

## Sending Data via HTTP (Alternative to MQTT)

If you don't want to use MQTT, you can send data via HTTP POST:

```cpp
#include <WiFi.h>
#include <HTTPClient.h>

void sendSensorData(String sensorType, float value) {
  HTTPClient http;
  http.begin("http://YOUR_SERVER_IP:8000/api/sensors/ingest");
  http.addHeader("Content-Type", "application/json");
  
  String payload = "{\"sensor_id\":\"esp32-1\",\"sensor_type\":\"" + sensorType + 
                   "\",\"value\":" + String(value) + ",\"timestamp\":\"" + 
                   String(millis()) + "\",\"field_id\":1}";
  
  int httpResponseCode = http.POST(payload);
  
  if (httpResponseCode > 0) {
    Serial.println("Data sent successfully");
  }
  
  http.end();
}

void loop() {
  // Read sensor and send
  float moisture = readSoilMoisture();
  sendSensorData("soil_moisture", moisture);
  
  delay(60000); // Send every minute
}
```

## Sensor Types Supported

| Sensor Type | Description | Unit | Arduino Pin |
|------------|-------------|------|-------------|
| soil_moisture | Soil moisture level | % | Analog |
| soil_temperature | Soil temperature | °C | Analog/Digital |
| soil_ph | Soil pH level | pH | Analog |
| soil_ec | Electrical conductivity | µS/cm | Analog |
| air_temperature | Air temperature | °C | Digital (DHT) |
| air_humidity | Relative humidity | % | Digital (DHT) |
| rainfall | Rainfall amount | mm | Analog/Digital |
| wind_speed | Wind speed | km/h | Digital |
| solar_radiation | Solar radiation | W/m² | Analog |
| ndvi | Vegetation index (camera) | 0-1 | Camera |

## MQTT Topics

**Format:** `sensors/<sensor_type>/<sensor_id>`

Examples:
- `sensors/soil_moisture/arduino-1`
- `sensors/air_temperature/esp32-1`
- `sensors/soil_ph/esp32-2`

**Data Format:**
```json
{
  "sensor_id": "arduino-1",
  "sensor_type": "soil_moisture",
  "value": 45.5,
  "timestamp": "2025-01-15T10:00:00Z",
  "field_id": 1
}
```

## Testing Without Hardware

Use simulation mode to test the system without any hardware:

```bash
# Ensure .env has:
SENSOR_MODE=simulation

# Restart the app
python app_clean.py

# The system will automatically generate realistic sensor data
```

## Troubleshooting

### Arduino Not Connecting

**Check:**
- USB cable is data-capable (not just charging)
- Correct COM port selected in Arduino IDE
- Board type selected correctly

### MQTT Not Working

**Check:**
- MQTT broker is running: `docker-compose ps`
- WiFi credentials are correct in Arduino code
- Server IP is correct
- Port 1883 is not blocked by firewall

### Data Not Appearing

**Check:**
- Sensor mode is set correctly
- API endpoint is accessible
- Check system logs for errors
- Verify sensor data format

## Next Steps

1. **Start with simulation mode** to test the system
2. **Get basic Arduino sensors** (soil moisture, temperature, humidity)
3. **Test with serial output** first
4. **Add WiFi module** for wireless transmission
5. **Connect to MQTT** for real-time data
6. **Add more sensors** as budget allows

## Support

For issues or questions:
- Check CONFIGURATION_GUIDE.md for general configuration
- Check RESEARCHER_DOCUMENTATION.md for research features
- Review sensor_ingestion.py for validation rules
