"""
feedback_store.py
=================
Local SQLite storage for diagnostic outputs, accuracy monitoring, user feedback,
and offline feedback queueing.
Offline-Safe.
"""

import sqlite3
import os
from typing import Dict, Any, List, Optional

DB_PATH = os.environ.get("AGRI_DB_PATH", "smart_weather.db")

# Determine whether live (non-simulated) weather data is available.
# Open-Meteo is a free, no-key weather API; OpenWeatherMap requires a real key.
_WEATHER_PROVIDER = os.environ.get('WEATHER_PROVIDER', 'openweathermap').lower().strip()
_OPENWEATHER_KEY = os.environ.get('OPENWEATHER_API_KEY', 'demo_key')
_IS_DEMO_KEY = (not _OPENWEATHER_KEY) or _OPENWEATHER_KEY.startswith('demo_key')
_LIVE_WEATHER_CONFIGURED = (
    (_WEATHER_PROVIDER == 'openmeteo')
    or (_OPENWEATHER_KEY and not _IS_DEMO_KEY)
)

class AccuracyMonitor:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agri_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crop TEXT,
                predicted_disease TEXT,
                actual_disease TEXT,
                is_correct INTEGER,
                is_simulated INTEGER DEFAULT 1,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS offline_feedback_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crop TEXT,
                predicted_disease TEXT,
                actual_disease TEXT,
                queued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                synced INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def log_feedback(self, crop: str, predicted: str, actual: str, is_simulated: bool = True):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        is_correct = 1 if predicted.lower() == actual.lower() else 0
        cursor.execute("""
            INSERT INTO agri_feedback (crop, predicted_disease, actual_disease, is_correct, is_simulated)
            VALUES (?, ?, ?, ?, ?)
        """, (crop, predicted, actual, is_correct, 1 if is_simulated else 0))
        conn.commit()
        conn.close()

    def queue_offline_feedback(self, crop: str, predicted: str, actual: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO offline_feedback_queue (crop, predicted_disease, actual_disease)
            VALUES (?, ?, ?)
        """, (crop, predicted, actual))
        conn.commit()
        conn.close()

    def flush_offline_queue(self) -> int:
        return self.sync_offline_queue()


    def sync_offline_queue(self) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, crop, predicted_disease, actual_disease FROM offline_feedback_queue WHERE synced = 0")
        rows = cursor.fetchall()
        
        count = 0
        for row in rows:
            q_id, crop, predicted, actual = row
            is_correct = 1 if predicted.lower() == actual.lower() else 0
            cursor.execute("""
                INSERT INTO agri_feedback (crop, predicted_disease, actual_disease, is_correct, is_simulated)
                VALUES (?, ?, ?, ?, 1)
            """, (crop, predicted, actual, is_correct))
            cursor.execute("UPDATE offline_feedback_queue SET synced = 1 WHERE id = ?", (q_id,))
            count += 1

        conn.commit()
        conn.close()
        return count

    def get_accuracy_metrics(self) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), SUM(is_correct), SUM(is_simulated) FROM agri_feedback")
        row = cursor.fetchone()
        conn.close()

        total = row[0] if row and row[0] else 0
        correct = row[1] if row and row[1] else 0
        simulated_count = row[2] if row and row[2] else 0

        accuracy = float(correct) / total if total > 0 else 0.0
        is_simulated = (simulated_count == total) or (total == 0)
        # If a live weather provider is configured and no feedback has been
        # collected yet, data is live (not simulated).
        if total == 0 and _LIVE_WEATHER_CONFIGURED:
            is_simulated = False

        return {
            "sample_count": total,
            "accuracy": round(accuracy, 4) if total > 0 else 1.0,
            "is_simulated": is_simulated,
            "provenance": ("SIMULATED DATA" if is_simulated
                           else (f"REAL DATA n={total}" if total > 0
                                 else f"LIVE DATA ({_WEATHER_PROVIDER})"))
        }
