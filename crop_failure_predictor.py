"""
Crop Failure Prediction Model - Research-Grade ML Pipeline
Implements probabilistic crop failure prediction with confidence intervals
and model explainability using SHAP values for research insights
"""
import sqlite3
import numpy as np
import pandas as pd
import joblib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
import shap
import structlog

logger = structlog.get_logger()


class CropFailurePredictor:
    """Predict crop failure probability with confidence intervals and explainability"""
    
    def __init__(self, model_type='random_forest'):
        self.model_type = model_type
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = []
        self.shap_explainer = None
        self.is_trained = False
        self.training_metadata = {}
    
    def prepare_features(self, sensor_data: Dict[str, float], crop_info: Dict[str, Any]) -> np.ndarray:
        """
        Prepare features for crop failure prediction
        Features include: sensor readings, stress indices, crop characteristics
        """
        features = []
        
        # Sensor readings
        features.extend([
            sensor_data.get('soil_moisture', 50.0),
            sensor_data.get('soil_temperature', 20.0),
            sensor_data.get('soil_ph', 6.5),
            sensor_data.get('soil_ec', 500.0),
            sensor_data.get('air_temperature', 25.0),
            sensor_data.get('air_humidity', 60.0),
            sensor_data.get('rainfall', 0.0),
            sensor_data.get('wind_speed', 5.0),
            sensor_data.get('solar_radiation', 200.0),
            sensor_data.get('ndvi', 0.5)
        ])
        
        # Stress indices
        heat_stress = max(0.0, (sensor_data.get('air_temperature', 25.0) - 35) / 15.0)
        drought_stress = max(0.0, (30 - sensor_data.get('soil_moisture', 50.0)) / 30.0)
        frost_risk = max(0.0, (5 - sensor_data.get('air_temperature', 25.0)) / 10.0)
        excess_moisture = max(0.0, (sensor_data.get('soil_moisture', 50.0) - 80) / 20.0)
        
        features.extend([heat_stress, drought_stress, frost_risk, excess_moisture])
        
        # Crop characteristics
        crop_type_encoded = self._encode_crop_type(crop_info.get('crop_type', 'Wheat'))
        growth_stage_encoded = self._encode_growth_stage(crop_info.get('growth_stage', 'Germination'))
        
        features.extend([
            crop_type_encoded,
            growth_stage_encoded,
            crop_info.get('area_ha', 1.0),
            crop_info.get('target_yield_ton_ha', 3.0)
        ])
        
        # Derived features
        features.append(sensor_data.get('air_temperature', 25.0) - sensor_data.get('soil_temperature', 20.0))  # Temp gradient
        features.append(sensor_data.get('air_humidity', 60.0) / 100.0 * sensor_data.get('soil_moisture', 50.0) / 100.0)  # Moisture index
        
        return np.array(features).reshape(1, -1)
    
    def _encode_crop_type(self, crop_type: str) -> float:
        """Encode crop type as numeric value"""
        crop_map = {
            'Wheat': 1.0, 'Rice': 2.0, 'Cotton': 3.0, 'Maize': 4.0,
            'Sugarcane': 5.0, 'Soybean': 6.0, 'Barley': 7.0, 'Other': 0.0
        }
        return crop_map.get(crop_type, 0.0)
    
    def _encode_growth_stage(self, growth_stage: str) -> float:
        """Encode growth stage as numeric value"""
        stage_map = {
            'Germination': 1.0, 'Tillering': 2.0, 'Jointing': 3.0,
            'Heading': 4.0, 'Flowering': 5.0, 'Grain Filling': 6.0,
            'Maturity': 7.0, 'Harvest': 8.0, 'Other': 0.0
        }
        return stage_map.get(growth_stage, 0.0)
    
    def train(self, X: np.ndarray, y: np.ndarray, feature_names: List[str] = None):
        """
        Train the crop failure prediction model
        X: feature matrix
        y: binary labels (0 = success, 1 = failure)
        """
        if len(X) < 30:
            logger.warning("insufficient_data_for_training", samples=len(X))
            return False
        
        # Split data
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
        
        # Scale features
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)
        
        # Initialize model
        if self.model_type == 'random_forest':
            base_model = RandomForestClassifier(
                n_estimators=100,
                max_depth=10,
                min_samples_split=5,
                random_state=42,
                class_weight='balanced'
            )
        elif self.model_type == 'gradient_boosting':
            base_model = GradientBoostingClassifier(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                random_state=42
            )
        else:
            base_model = RandomForestClassifier(n_estimators=100, random_state=42)
        
        # Calibrate for probability estimates
        self.model = CalibratedClassifierCV(base_model, cv=5, method='isotonic')
        self.model.fit(X_train_scaled, y_train)
        
        # Evaluate
        cv_scores = cross_val_score(self.model, X_train_scaled, y_train, cv=5, scoring='roc_auc')
        test_score = self.model.score(X_test_scaled, y_test)
        
        # Store feature names
        self.feature_names = feature_names or [f'feature_{i}' for i in range(X.shape[1])]
        
        # Initialize SHAP explainer
        try:
            self.shap_explainer = shap.TreeExplainer(self.model.calibrated_classifiers_[0])
        except Exception as e:
            logger.warning("shap_explainer_init_failed", error=str(e))
            self.shap_explainer = None
        
        # Store training metadata
        self.training_metadata = {
            'training_samples': len(X_train),
            'test_samples': len(X_test),
            'cv_roc_auc_mean': float(cv_scores.mean()),
            'cv_roc_auc_std': float(cv_scores.std()),
            'test_accuracy': float(test_score),
            'feature_count': len(self.feature_names),
            'trained_at': datetime.now().isoformat()
        }
        
        self.is_trained = True
        
        logger.info("model_trained", metadata=self.training_metadata)
        return True
    
    def predict_with_confidence(self, features: np.ndarray) -> Dict[str, Any]:
        """
        Predict crop failure probability with confidence interval
        Returns: {'failure_probability': float, 'confidence_interval': [low, high], 'prediction': str}
        """
        if not self.is_trained:
            return {
                'failure_probability': 0.5,
                'confidence_interval': [0.0, 1.0],
                'prediction': 'unknown',
                'confidence': 0.0
            }
        
        # Scale features
        features_scaled = self.scaler.transform(features)
        
        # Get probability estimates
        prob_failure = self.model.predict_proba(features_scaled)[0, 1]
        
        # Calculate confidence interval using ensemble variance
        if hasattr(self.model, 'calibrated_classifiers_'):
            # Get predictions from each calibrated classifier
            probs = []
            for calibrated_clf in self.model.calibrated_classifiers_:
                prob = calibrated_clf.predict_proba(features_scaled)[0, 1]
                probs.append(prob)
            
            probs = np.array(probs)
            std_error = np.std(probs)
            ci_low = max(0.0, prob_failure - 1.96 * std_error)
            ci_high = min(1.0, prob_failure + 1.96 * std_error)
        else:
            ci_low = max(0.0, prob_failure - 0.1)
            ci_high = min(1.0, prob_failure + 0.1)
        
        # Determine prediction
        if prob_failure < 0.3:
            prediction = 'low_risk'
            confidence = 1.0 - prob_failure
        elif prob_failure < 0.7:
            prediction = 'moderate_risk'
            confidence = 0.5
        else:
            prediction = 'high_risk'
            confidence = prob_failure
        
        return {
            'failure_probability': round(prob_failure, 3),
            'confidence_interval': [round(ci_low, 3), round(ci_high, 3)],
            'prediction': prediction,
            'confidence': round(confidence, 3)
        }
    
    def explain_prediction(self, features: np.ndarray) -> Dict[str, Any]:
        """
        Explain prediction using SHAP values
        Returns: {'feature_importance': {feature_name: shap_value}, 'base_value': float}
        """
        if not self.is_trained or self.shap_explainer is None:
            return {'error': 'Model not trained or SHAP explainer not available'}
        
        try:
            # Scale features
            features_scaled = self.scaler.transform(features)
            
            # Calculate SHAP values
            shap_values = self.shap_explainer.shap_values(features_scaled)
            
            # If shap_values is a list (for binary classification), take the second element (failure class)
            if isinstance(shap_values, list):
                shap_values = shap_values[1]
            
            # Create feature importance dictionary
            feature_importance = {}
            for i, feature_name in enumerate(self.feature_names):
                feature_importance[feature_name] = float(shap_values[0, i])
            
            # Get base value (expected value)
            base_value = float(self.shap_explainer.expected_value)
            if isinstance(base_value, list):
                base_value = base_value[1]  # Failure class
            
            return {
                'feature_importance': feature_importance,
                'base_value': round(base_value, 3),
                'top_positive_features': sorted(
                    feature_importance.items(), key=lambda x: x[1], reverse=True
                )[:3],
                'top_negative_features': sorted(
                    feature_importance.items(), key=lambda x: x[1]
                )[:3]
            }
        except Exception as e:
            logger.error("shap_explanation_error", error=str(e))
            return {'error': str(e)}
    
    def save_model(self, path='models/crop_failure_model.joblib'):
        """Save trained model and metadata"""
        if not self.is_trained:
            logger.warning("model_not_trained")
            return False
        
        model_data = {
            'model': self.model,
            'scaler': self.scaler,
            'feature_names': self.feature_names,
            'training_metadata': self.training_metadata,
            'model_type': self.model_type
        }
        
        joblib.dump(model_data, path)
        logger.info("model_saved", path=path)
        return True
    
    def load_model(self, path='models/crop_failure_model.joblib'):
        """Load trained model and metadata"""
        try:
            model_data = joblib.load(path)
            self.model = model_data['model']
            self.scaler = model_data['scaler']
            self.feature_names = model_data['feature_names']
            self.training_metadata = model_data['training_metadata']
            self.model_type = model_data['model_type']
            self.is_trained = True
            
            # Reinitialize SHAP explainer
            try:
                self.shap_explainer = shap.TreeExplainer(self.model.calibrated_classifiers_[0])
            except Exception as e:
                logger.warning("shap_explainer_init_failed", error=str(e))
                self.shap_explainer = None
            
            logger.info("model_loaded", path=path, metadata=self.training_metadata)
            return True
        except Exception as e:
            logger.error("model_load_error", error=str(e))
            return False


class CropFailureService:
    """Service for crop failure prediction using sensor data"""
    
    def __init__(self, db_path='smart_weather.db'):
        self.db_path = db_path
        self.predictor = CropFailurePredictor()
        
        # Try to load existing model
        self.predictor.load_model()
    
    def get_sensor_data_for_field(self, field_id: int) -> Dict[str, float]:
        """Get latest sensor readings for a field"""
        conn = sqlite3.connect(self.db_path)
        try:
            readings = conn.execute('''
                SELECT sensor_type, value FROM validated_sensor_readings
                WHERE field_id = ?
                AND timestamp >= datetime('now', '-1 hour')
                GROUP BY sensor_type
            ''', (field_id,)).fetchall()
            
            return {row['sensor_type']: row['value'] for row in readings}
        finally:
            conn.close()
    
    def get_crop_info_for_field(self, field_id: int) -> Dict[str, Any]:
        """Get crop information for a field"""
        conn = sqlite3.connect(self.db_path)
        try:
            crop = conn.execute('''
                SELECT cc.*, f.soil_type, f.irrigation_type
                FROM crop_census cc
                JOIN fields f ON cc.field_id = f.field_id
                WHERE cc.field_id = ?
                ORDER BY cc.created_at DESC LIMIT 1
            ''', (field_id,)).fetchone()
            
            if crop:
                return dict(crop)
            return {}
        finally:
            conn.close()
    
    def predict_crop_failure(self, field_id: int) -> Dict[str, Any]:
        """
        Predict crop failure probability for a field
        Returns prediction with confidence interval and SHAP explanation
        """
        # Get sensor data
        sensor_data = self.get_sensor_data_for_field(field_id)
        if not sensor_data:
            return {'success': False, 'error': 'No sensor data available'}
        
        # Get crop info
        crop_info = self.get_crop_info_for_field(field_id)
        if not crop_info:
            return {'success': False, 'error': 'No crop information available'}
        
        # Prepare features
        features = self.predictor.prepare_features(sensor_data, crop_info)
        
        # Predict
        prediction = self.predictor.predict_with_confidence(features)
        
        # Explain
        explanation = self.predictor.explain_prediction(features)
        
        return {
            'success': True,
            'field_id': field_id,
            'crop_type': crop_info.get('crop_type'),
            'growth_stage': crop_info.get('growth_stage'),
            'sensor_data': sensor_data,
            'prediction': prediction,
            'explanation': explanation,
            'model_metadata': self.predictor.training_metadata
        }
    
    def generate_synthetic_training_data(self, num_samples: int = 500) -> Tuple[np.ndarray, np.ndarray]:
        """
        Generate synthetic training data for model training
        This is a placeholder - in production, use real historical data
        """
        np.random.seed(42)
        
        # Generate synthetic sensor data
        sensor_features = np.random.rand(num_samples, 10) * 100  # 10 sensor features
        
        # Generate synthetic stress indices
        stress_features = np.random.rand(num_samples, 4)
        
        # Generate synthetic crop features
        crop_features = np.random.rand(num_samples, 4)
        
        # Generate derived features
        derived_features = np.random.rand(num_samples, 2)
        
        # Combine all features
        X = np.hstack([sensor_features, stress_features, crop_features, derived_features])
        
        # Generate synthetic labels (crop failure)
        # Higher stress + extreme conditions = higher failure probability
        stress_score = np.mean(stress_features, axis=1)
        failure_prob = stress_score * 0.5 + np.random.rand(num_samples) * 0.3
        y = (failure_prob > 0.5).astype(int)
        
        return X, y
    
    def train_model_with_synthetic_data(self):
        """Train model with synthetic data (for demonstration)"""
        X, y = self.generate_synthetic_training_data(1000)
        
        feature_names = [
            'soil_moisture', 'soil_temperature', 'soil_ph', 'soil_ec',
            'air_temperature', 'air_humidity', 'rainfall', 'wind_speed',
            'solar_radiation', 'ndvi',
            'heat_stress', 'drought_stress', 'frost_risk', 'excess_moisture',
            'crop_type', 'growth_stage', 'area_ha', 'target_yield',
            'temp_gradient', 'moisture_index'
        ]
        
        success = self.predictor.train(X, y, feature_names)
        
        if success:
            self.predictor.save_model()
        
        return success


if __name__ == '__main__':
    logger.info("crop_failure_predictor_start")
    
    service = CropFailureService()
    
    # Train with synthetic data if not trained
    if not service.predictor.is_trained:
        logger.info("training_with_synthetic_data")
        service.train_model_with_synthetic_data()
    
    # Example prediction for field 1
    result = service.predict_crop_failure(1)
    print("Prediction result:", result)
