"""
Experiment Tracking Module - Research-Grade ML Experiment Management
Tracks model training runs, hyperparameters, metrics, and artifacts
for reproducibility and research documentation.

Storage: Supabase PostgreSQL (tables experiments, model_versions,
dataset_versions - created by supabase/schema.sql).
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import structlog

import db

logger = structlog.get_logger()


def _now():
    return datetime.now(timezone.utc)


class ExperimentTracker:
    """Track ML experiments for research reproducibility"""

    def __init__(self, db_path=None):
        # `db_path` kept only for backwards compatibility with the SQLite version.
        self.db_path = db_path

    def start_experiment(self, experiment_name: str, model_type: str,
                         description: str = None, tags: List[str] = None,
                         parameters: Dict[str, Any] = None) -> str:
        """Start a new experiment and return experiment ID"""
        experiment_id = str(uuid.uuid4())
        with db.connect() as conn:
            conn.execute('''
                INSERT INTO experiments
                (experiment_id, experiment_name, model_type, start_time, status, description, tags, parameters)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ''', (experiment_id, experiment_name, model_type, _now(), 'running', description,
                  db.safe_json(tags or []), db.safe_json(parameters or {})))
            conn.commit()
        logger.info("experiment_started", experiment_id=experiment_id, name=experiment_name)
        return experiment_id

    def _merge_json(self, column: str, experiment_id: str, patch: Dict[str, Any]):
        """Atomically merge `patch` into a jsonb column (no read-modify-write race)."""
        if column not in ('metrics', 'parameters', 'artifacts'):     # whitelist: column is interpolated
            raise ValueError('unsupported column')
        with db.connect() as conn:
            conn.execute(
                f"UPDATE experiments SET {column} = COALESCE({column}, '{{}}'::jsonb) || %s::jsonb "
                "WHERE experiment_id = %s", (db.safe_json(patch), experiment_id))
            conn.commit()

    def log_metric(self, experiment_id: str, metric_name: str, value: float, step: int = None):
        """Log a metric for an experiment"""
        metric_key = f"{metric_name}_{step}" if step is not None else metric_name
        self._merge_json('metrics', experiment_id, {
            metric_key: {'value': value, 'timestamp': _now().isoformat(), 'step': step}})

    def log_metrics(self, experiment_id: str, metrics: Dict[str, float]):
        """Log multiple metrics at once"""
        for metric_name, value in metrics.items():
            self.log_metric(experiment_id, metric_name, value)

    def log_parameter(self, experiment_id: str, param_name: str, value: Any):
        """Log a parameter for an experiment"""
        self._merge_json('parameters', experiment_id, {param_name: value})

    def log_artifact(self, experiment_id: str, artifact_name: str, artifact_path: str):
        """Log an artifact (model file, dataset, etc.)"""
        self._merge_json('artifacts', experiment_id, {
            artifact_name: {'path': artifact_path, 'logged_at': _now().isoformat()}})

    def end_experiment(self, experiment_id: str, status: str = 'completed'):
        """End an experiment"""
        with db.connect() as conn:
            conn.execute("UPDATE experiments SET end_time = %s, status = %s WHERE experiment_id = %s",
                         (_now(), status, experiment_id))
            conn.commit()
        logger.info("experiment_ended", experiment_id=experiment_id, status=status)

    def register_model_version(self, experiment_id: str, model_name: str,
                               version_number: str, model_path: str,
                               metrics: Dict[str, float] = None,
                               is_production: bool = False) -> str:
        """Register a model version"""
        version_id = str(uuid.uuid4())
        with db.connect() as conn:
            conn.execute('''
                INSERT INTO model_versions
                (version_id, experiment_id, model_name, version_number, model_path, metrics, is_production)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (version_id, experiment_id, model_name, version_number, model_path,
                  db.safe_json(metrics or {}), bool(is_production)))
            conn.commit()
        return version_id

    def register_dataset_version(self, dataset_name: str, version_number: str,
                                 data_path: str, row_count: int = None,
                                 column_count: int = None, checksum: str = None) -> str:
        """Register a dataset version"""
        version_id = str(uuid.uuid4())
        with db.connect() as conn:
            conn.execute('''
                INSERT INTO dataset_versions
                (version_id, dataset_name, version_number, data_path, row_count, column_count, checksum)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', (version_id, dataset_name, version_number, data_path, row_count, column_count, checksum))
            conn.commit()
        return version_id

    def get_experiment(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        """Get experiment details"""
        with db.connect() as conn:
            result = conn.execute('SELECT * FROM experiments WHERE experiment_id = %s',
                                  (experiment_id,)).fetchone()
        return dict(result) if result else None

    def list_experiments(self, model_type: str = None, status: str = None,
                         limit: int = 50) -> List[Dict[str, Any]]:
        """List experiments with optional filters"""
        query = 'SELECT * FROM experiments WHERE TRUE'
        params: List[Any] = []
        if model_type:
            query += ' AND model_type = %s'
            params.append(model_type)
        if status:
            query += ' AND status = %s'
            params.append(status)
        query += ' ORDER BY start_time DESC LIMIT %s'
        params.append(int(limit))
        with db.connect() as conn:
            results = conn.execute(query, params).fetchall()
        return [dict(r) for r in results]

    def get_best_model(self, model_name: str, metric: str = 'accuracy') -> Optional[Dict[str, Any]]:
        """Get the best performing model for a given model name"""
        with db.connect() as conn:
            result = conn.execute('''
                SELECT mv.*, e.metrics AS experiment_metrics
                FROM model_versions mv
                JOIN experiments e ON mv.experiment_id = e.experiment_id
                WHERE mv.model_name = %s
                ORDER BY (e.metrics -> %s ->> 'value')::float8 DESC NULLS LAST
                LIMIT 1
            ''', (model_name, metric)).fetchone()
        return dict(result) if result else None


if __name__ == '__main__':
    logger.info("experiment_tracker_start")
    
    tracker = ExperimentTracker()
    
    # Example: Start an experiment
    exp_id = tracker.start_experiment(
        experiment_name="crop_failure_prediction_v1",
        model_type="random_forest",
        description="Initial crop failure prediction model",
        tags=["agriculture", "crop_failure", "research"],
        parameters={"n_estimators": 100, "max_depth": 10}
    )
    
    # Log metrics
    tracker.log_metrics(exp_id, {"accuracy": 0.85, "precision": 0.82, "recall": 0.88, "f1": 0.85})
    
    # Log artifact
    tracker.log_artifact(exp_id, "model", "models/crop_failure_model.joblib")
    
    # End experiment
    tracker.end_experiment(exp_id, status="completed")
    
    print("Experiment tracking demo completed")
