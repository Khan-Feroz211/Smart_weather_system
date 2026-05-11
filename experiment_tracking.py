"""
Experiment Tracking Module - Research-Grade ML Experiment Management
Tracks model training runs, hyperparameters, metrics, and artifacts
for reproducibility and research documentation
"""
import sqlite3
import json
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional
import structlog

logger = structlog.get_logger()


class ExperimentTracker:
    """Track ML experiments for research reproducibility"""
    
    def __init__(self, db_path='smart_weather.db'):
        self.db_path = db_path
        self.init_tables()
    
    def init_tables(self):
        """Initialize experiment tracking tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Experiments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                experiment_name TEXT NOT NULL,
                model_type TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                status TEXT DEFAULT 'running',
                description TEXT,
                tags TEXT,
                parameters TEXT,
                metrics TEXT,
                artifacts TEXT,
                git_commit TEXT,
                dataset_version TEXT
            )
        ''')
        
        # Model versions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_versions (
                version_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                version_number TEXT NOT NULL,
                model_path TEXT NOT NULL,
                metrics TEXT,
                created_at TEXT NOT NULL,
                is_production BOOLEAN DEFAULT 0,
                FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
            )
        ''')
        
        # Dataset versions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dataset_versions (
                version_id TEXT PRIMARY KEY,
                dataset_name TEXT NOT NULL,
                version_number TEXT NOT NULL,
                data_path TEXT NOT NULL,
                row_count INTEGER,
                column_count INTEGER,
                created_at TEXT NOT NULL,
                checksum TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("experiment_tracking_tables_init")
    
    def start_experiment(self, experiment_name: str, model_type: str, 
                        description: str = None, tags: List[str] = None,
                        parameters: Dict[str, Any] = None) -> str:
        """Start a new experiment and return experiment ID"""
        experiment_id = str(uuid.uuid4())
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO experiments
            (experiment_id, experiment_name, model_type, start_time, status, description, tags, parameters)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            experiment_id,
            experiment_name,
            model_type,
            datetime.now().isoformat(),
            'running',
            description,
            json.dumps(tags or []),
            json.dumps(parameters or {})
        ))
        
        conn.commit()
        conn.close()
        
        logger.info("experiment_started", experiment_id=experiment_id, name=experiment_name)
        return experiment_id
    
    def log_metric(self, experiment_id: str, metric_name: str, value: float, step: int = None):
        """Log a metric for an experiment"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get current metrics
        cursor.execute('SELECT metrics FROM experiments WHERE experiment_id = ?', (experiment_id,))
        result = cursor.fetchone()
        
        if result and result[0]:
            metrics = json.loads(result[0])
        else:
            metrics = {}
        
        # Add new metric
        metric_key = f"{metric_name}_{step}" if step is not None else metric_name
        metrics[metric_key] = {
            'value': value,
            'timestamp': datetime.now().isoformat(),
            'step': step
        }
        
        # Update
        cursor.execute('''
            UPDATE experiments SET metrics = ? WHERE experiment_id = ?
        ''', (json.dumps(metrics), experiment_id))
        
        conn.commit()
        conn.close()
    
    def log_metrics(self, experiment_id: str, metrics: Dict[str, float]):
        """Log multiple metrics at once"""
        for metric_name, value in metrics.items():
            self.log_metric(experiment_id, metric_name, value)
    
    def log_parameter(self, experiment_id: str, param_name: str, value: Any):
        """Log a parameter for an experiment"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT parameters FROM experiments WHERE experiment_id = ?', (experiment_id,))
        result = cursor.fetchone()
        
        if result and result[0]:
            parameters = json.loads(result[0])
        else:
            parameters = {}
        
        parameters[param_name] = value
        
        cursor.execute('''
            UPDATE experiments SET parameters = ? WHERE experiment_id = ?
        ''', (json.dumps(parameters), experiment_id))
        
        conn.commit()
        conn.close()
    
    def log_artifact(self, experiment_id: str, artifact_name: str, artifact_path: str):
        """Log an artifact (model file, dataset, etc.)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT artifacts FROM experiments WHERE experiment_id = ?', (experiment_id,))
        result = cursor.fetchone()
        
        if result and result[0]:
            artifacts = json.loads(result[0])
        else:
            artifacts = {}
        
        artifacts[artifact_name] = {
            'path': artifact_path,
            'logged_at': datetime.now().isoformat()
        }
        
        cursor.execute('''
            UPDATE experiments SET artifacts = ? WHERE experiment_id = ?
        ''', (json.dumps(artifacts), experiment_id))
        
        conn.commit()
        conn.close()
    
    def end_experiment(self, experiment_id: str, status: str = 'completed'):
        """End an experiment"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE experiments SET end_time = ?, status = ? WHERE experiment_id = ?
        ''', (datetime.now().isoformat(), status, experiment_id))
        
        conn.commit()
        conn.close()
        
        logger.info("experiment_ended", experiment_id=experiment_id, status=status)
    
    def register_model_version(self, experiment_id: str, model_name: str, 
                              version_number: str, model_path: str,
                              metrics: Dict[str, float] = None,
                              is_production: bool = False) -> str:
        """Register a model version"""
        version_id = str(uuid.uuid4())
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO model_versions
            (version_id, experiment_id, model_name, version_number, model_path, metrics, created_at, is_production)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            version_id,
            experiment_id,
            model_name,
            version_number,
            model_path,
            json.dumps(metrics or {}),
            datetime.now().isoformat(),
            is_production
        ))
        
        conn.commit()
        conn.close()
        
        return version_id
    
    def register_dataset_version(self, dataset_name: str, version_number: str,
                                data_path: str, row_count: int = None,
                                column_count: int = None, checksum: str = None) -> str:
        """Register a dataset version"""
        version_id = str(uuid.uuid4())
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO dataset_versions
            (version_id, dataset_name, version_number, data_path, row_count, column_count, created_at, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            version_id,
            dataset_name,
            version_number,
            data_path,
            row_count,
            column_count,
            datetime.now().isoformat(),
            checksum
        ))
        
        conn.commit()
        conn.close()
        
        return version_id
    
    def get_experiment(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        """Get experiment details"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM experiments WHERE experiment_id = ?', (experiment_id,))
        result = cursor.fetchone()
        
        conn.close()
        
        if result:
            return dict(result)
        return None
    
    def list_experiments(self, model_type: str = None, status: str = None, 
                       limit: int = 50) -> List[Dict[str, Any]]:
        """List experiments with optional filters"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        query = 'SELECT * FROM experiments WHERE 1=1'
        params = []
        
        if model_type:
            query += ' AND model_type = ?'
            params.append(model_type)
        
        if status:
            query += ' AND status = ?'
            params.append(status)
        
        query += ' ORDER BY start_time DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        conn.close()
        
        return [dict(r) for r in results]
    
    def get_best_model(self, model_name: str, metric: str = 'accuracy') -> Optional[Dict[str, Any]]:
        """Get the best performing model for a given model name"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT mv.*, e.metrics as experiment_metrics
            FROM model_versions mv
            JOIN experiments e ON mv.experiment_id = e.experiment_id
            WHERE mv.model_name = ?
            ORDER BY json_extract(e.metrics, '$.' || ?) DESC
            LIMIT 1
        ''', (model_name, metric))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return dict(result)
        return None


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
