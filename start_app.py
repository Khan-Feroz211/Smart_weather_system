#!/usr/bin/env python
"""Quick startup script for Smart Weather System."""
import sys
import os

# Ensure UTF-8 encoding
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Change to project directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Add to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_clean import app, initialize_app

if __name__ == '__main__':
    initialize_app()
    print("\n" + "=" * 60)
    print("  Smart Weather System - Multi-Hazard Early Warning")
    print("=" * 60)
    print(f"  Server: http://localhost:8000")
    print(f"  Dashboard: http://localhost:8000/dashboard")
    print(f"  Agriculture: http://localhost:8000/agri")
    print(f"  Disease Scanner: http://localhost:8000/agri/disease-detection")
    print(f"  API: http://localhost:8000/api/hazards/predict")
    print(f"  Agri API: http://localhost:8000/api/agri/analyze")
    print(f"  Disease API: http://localhost:8000/api/agri/disease-diagnose")
    print(f"  Status API: http://localhost:8000/api/agri/status")
    print("=" * 60)
    print("\nPress Ctrl+C to stop.\n")

    app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)
