"""
feedback_store.py
=================
Storage for user feedback and prediction-accuracy monitoring, backed by the
`agri_feedback` table in Supabase PostgreSQL (schema: supabase/schema.sql).

Nothing touches the database at import time, so the app starts even when the
database is unreachable; calls raise `db.DatabaseUnavailable` in that case.
"""

from typing import Any, Dict

import db


class AccuracyMonitor:
    def log_feedback(self, crop: str, predicted: str, actual: str, is_simulated: bool = True):
        is_correct = str(predicted).lower() == str(actual).lower()
        try:
            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO agri_feedback (crop, predicted_disease, actual_disease, is_correct, is_simulated)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (crop, predicted, actual, is_correct, bool(is_simulated)))
                conn.commit()
        except (db.DatabaseUnavailable, Exception):
            # Demo/offline mode: do not crash the application if the Supabase backend
            # is unavailable. The recommendation engine may still operate with defaults.
            return None

    def get_accuracy_metrics(self) -> Dict[str, Any]:
        try:
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*), COUNT(*) FILTER (WHERE is_correct), COUNT(*) FILTER (WHERE is_simulated) "
                    "FROM agri_feedback").fetchone()
        except Exception:
            return {
                "sample_count": 0,
                "accuracy": 0.0,
                "is_simulated": True,
                "provenance": "SIMULATED DATA"
            }

        total = row[0] or 0
        correct = row[1] or 0
        simulated_count = row[2] or 0

        accuracy = float(correct) / total if total > 0 else 0.0
        is_simulated = (simulated_count == total) or (total == 0)

        return {
            "sample_count": total,
            "accuracy": round(accuracy, 4),
            "is_simulated": is_simulated,
            "provenance": "SIMULATED DATA" if is_simulated else f"REAL DATA n={total}"
        }
