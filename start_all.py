"""
Startup Script - Launch all services with one command
Starts MQTT broker, sensor ingestion service, and main Flask app
"""
import subprocess
import time
import sys
import os

def print_header(message):
    """Print formatted header"""
    print("\n" + "="*60)
    print(f"  {message}")
    print("="*60 + "\n")

def run_command(cmd, description):
    """Run a command and print status"""
    print(f"🚀 {description}...")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ {description} completed successfully")
            return True
        else:
            print(f"❌ {description} failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ {description} error: {e}")
        return False

def main():
    """Main startup function"""
    print_header("AI-Powered Precision Agriculture System - Startup")
    
    # Check if Docker is available
    docker_check = subprocess.run("docker --version", shell=True, capture_output=True)
    if docker_check.returncode == 0:
        print("✅ Docker is available")
    else:
        print("❌ Docker is not available. Please install Docker first.")
        sys.exit(1)
    
    # Step 1: Start MQTT broker
    print("\n📡 Starting MQTT Broker (Mosquitto)...")
    mqtt_result = run_command("docker-compose up -d mosquitto", "MQTT Broker")
    if not mqtt_result:
        print("⚠️  MQTT broker failed to start, continuing anyway...")
    
    time.sleep(3)
    
    # Step 2: Install dependencies if needed
    print("\n📦 Checking dependencies...")
    run_command("pip install paho-mqtt structlog plotly shap xgboost pandas -q", "Install dependencies")
    
    # Step 3: Start sensor ingestion service
    print("\n📡 Starting Sensor Ingestion Service...")
    # Start in background
    sensor_process = subprocess.Popen(
        ["python", "sensor_ingestion.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    print("✅ Sensor Ingestion Service started (PID: {})".format(sensor_process.pid))
    
    time.sleep(2)
    
    # Step 4: Start main Flask application
    print("\n🌐 Starting Main Flask Application...")
    app_process = subprocess.Popen(
        ["python", "app_clean.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    print("✅ Flask Application started (PID: {})".format(app_process.pid))
    
    time.sleep(3)
    
    # Step 5: Test the application
    print("\n🧪 Testing Application...")
    time.sleep(2)
    
    try:
        import requests
        response = requests.get("http://localhost:8000/dashboard", timeout=5)
        if response.status_code == 200:
            print("✅ Flask Application is responding")
        else:
            print(f"⚠️  Flask Application returned status code: {response.status_code}")
    except Exception as e:
        print(f"⚠️  Could not connect to Flask Application: {e}")
    
    print_header("System Startup Complete")
    
    print("\n📊 System Status:")
    print("  • MQTT Broker: Running (Docker)")
    print("  • Sensor Ingestion: Running (PID: {})".format(sensor_process.pid))
    print("  • Flask Application: Running (PID: {})".format(app_process.pid))
    print("\n🌐 Access the application at: http://localhost:8000")
    print("\n📚 API Endpoints:")
    print("  • Sensor Ingestion: POST /api/sensors/ingest")
    print("  • Data Processing: GET /api/processing/*")
    print("  • Predictions: GET /api/prediction/*")
    print("  • Visualization: GET /api/visualization/*")
    print("  • Experiment Tracking: GET /api/experiments/*")
    
    print("\n💡 To stop all services, press Ctrl+C or run: docker-compose down")
    
    try:
        # Keep script running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Shutting down services...")
        sensor_process.terminate()
        app_process.terminate()
        run_command("docker-compose down", "Stop MQTT Broker")
        print("✅ All services stopped")

if __name__ == "__main__":
    main()
