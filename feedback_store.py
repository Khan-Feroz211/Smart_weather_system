"""
feedback_store.py
=================
Local SQLite storage for diagnostic outputs, accuracy monitoring, and user feedback.
Offline-Safe.
"""

import sqlite3
import os
from typing import Dict, Any, Optional

DB_PATH = os.environ.get("AGRI_DB_PATH", "smart_weather.db")

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

        return {
            "sample_count": total,
            "accuracy": round(accuracy, 4),
            "is_simulated": is_simulated,
            "provenance": "SIMULATED DATA" if is_simulated else f"REAL DATA n={total}"
        }
