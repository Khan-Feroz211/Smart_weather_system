"""
Comprehensive Test Suite for Smart Weather System (Multi-Hazard Early Warning)
=============================================================================

Tests all 6 phases of the system:

  Phase 1: Stacking Ensemble Classifier
  Phase 2: Advanced Feature Engineering
  Phase 3: Time-Aware & Location-Aware Cross-Validation
  Phase 4: XAI Integration (SHAP + LIME)
  Phase 5: Multi-Hazard Output & Crisis Communication
  Phase 6: Edge-Case Hardening (Graceful Degradation)

Run with::

    python tests/test_multi_hazard_system.py -v

Or without pytest::

    python tests/test_multi_hazard_system.py
"""

import os
import sys
import json
import tempfile
import shutil
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Try to import pytest, fall back to unittest
try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False
    import unittest


# ============================================================================
# Test Data Generation
# ============================================================================

def generate_synthetic_weather_data(n_samples=500, location="Lahore"):
    """Generate synthetic weather data for testing."""
    np.random.seed(42)
    base_time = datetime.now() - timedelta(hours=n_samples)

    data = []
    for i in range(n_samples):
        # Simulate diurnal and seasonal cycles
        hour = (base_time + timedelta(hours=i)).hour
        day_of_year = (base_time + timedelta(hours=i)).timetuple().tm_yday

        # Temperature with diurnal cycle and seasonal variation
        temp = 20 + 10 * np.sin(2 * np.pi * hour / 24) + 5 * np.sin(2 * np.pi * day_of_year / 365)
        temp += np.random.normal(0, 2)

        # Humidity (inverse of temperature)
        humidity = 70 - (temp - 20) * 1.5 + np.random.normal(0, 5)
        humidity = max(20, min(100, humidity))

        # Pressure with random variation
        pressure = 1013 + np.random.normal(0, 5)

        # Wind speed
        wind_speed = abs(np.random.normal(5, 3))

        data.append({
            'temperature': round(temp, 1),
            'humidity': round(humidity, 1),
            'pressure': round(pressure, 1),
            'wind_speed': round(wind_speed, 1),
            'timestamp': (base_time + timedelta(hours=i)).isoformat(),
            'location': location,
        })

    return data


# ============================================================================
# Phase 1: Stacking Ensemble Tests
# ============================================================================

class TestStackingEnsemble:
    """Tests for Phase 1: Stacking Ensemble Classifier."""

    def setup_method(self):
        """Set up test data."""
        from stacking_ensemble import StackingEnsemble, HAZARD_CATEGORIES
        self.HAZARD_CATEGORIES = HAZARD_CATEGORIES
        self.ensemble = StackingEnsemble(
            n_estimators_rf=10,
            n_estimators_xgb=10,
            n_estimators_lgb=10,
            n_hidden_elm=20,
            cv_folds=3,
            random_state=42,
        )
        self.X, self.y = self._generate_classification_data()

    def _generate_classification_data(self):
        """Generate synthetic classification data."""
        np.random.seed(42)
        n_samples = 300
        n_features = 20

        X = np.random.randn(n_samples, n_features)
        # Create labels based on simple rules
        y = []
        for i in range(n_samples):
            temp = X[i, 0] * 10 + 20
            humidity = X[i, 1] * 10 + 60
            wind = X[i, 2] * 5 + 5
            pressure = X[i, 3] * 10 + 1013

            if temp > 35:
                y.append("extreme_heat_heatwave")
            elif temp < 5:
                y.append("extreme_cold_frost")
            elif wind > 20:
                y.append("high_wind_storm")
            elif pressure < 1000:
                y.append("heavy_rain_flood")
            else:
                y.append("normal")

        return X, np.array(y)

    def test_ensemble_initialization(self):
        """Test that the ensemble initializes correctly."""
        assert self.ensemble is not None
        assert self.ensemble.is_fitted is False
        assert self.ensemble._classes_ == []

    def test_ensemble_fit(self):
        """Test that the ensemble can be trained."""
        feature_names = [f"feature_{i}" for i in range(self.X.shape[1])]
        self.ensemble.fit(self.X, self.y, feature_names=feature_names)

        assert self.ensemble.is_fitted is True
        assert len(self.ensemble._classes_) > 0
        assert len(self.ensemble.base_learner_names) >= 1  # At least RF
        assert "random_forest" in self.ensemble.base_learner_names

    def test_ensemble_predict_proba(self):
        """Test that the ensemble produces probability predictions."""
        feature_names = [f"feature_{i}" for i in range(self.X.shape[1])]
        self.ensemble.fit(self.X, self.y, feature_names=feature_names)

        probs = self.ensemble.predict_proba(self.X[:5])
        assert probs.shape[0] == 5
        assert probs.shape[1] == len(self.ensemble._classes_)
        # Probabilities should sum to ~1
        for row in probs:
            assert abs(sum(row) - 1.0) < 0.01

    def test_ensemble_predict(self):
        """Test that the ensemble produces class predictions."""
        feature_names = [f"feature_{i}" for i in range(self.X.shape[1])]
        self.ensemble.fit(self.X, self.y, feature_names=feature_names)

        preds = self.ensemble.predict(self.X[:5])
        assert len(preds) == 5
        for pred in preds:
            assert pred in self.ensemble._classes_

    def test_multi_hazard_prediction(self):
        """Test that the ensemble produces multi-hazard probabilities."""
        feature_names = [f"feature_{i}" for i in range(self.X.shape[1])]
        self.ensemble.fit(self.X, self.y, feature_names=feature_names)

        result = self.ensemble.predict_multi_hazard(self.X[:1])

        assert "hazard_probabilities" in result
        assert "risk_levels" in result
        assert "active_hazards" in result
        assert "confidence" in result

        # Check all hazard categories are present
        for hazard in self.HAZARD_CATEGORIES:
            assert hazard in result["hazard_probabilities"]
            assert hazard in result["risk_levels"]

    def test_elm_base_learner(self):
        """Test that the ELM base learner works correctly."""
        from stacking_ensemble import ExtremeLearningMachine

        elm = ExtremeLearningMachine(n_hidden=20, random_state=42)
        elm.fit(self.X, self.y)

        preds = elm.predict(self.X[:5])
        assert len(preds) == 5

        probs = elm.predict_proba(self.X[:5])
        assert probs.shape[0] == 5

    def test_model_save_load(self):
        """Test that the model can be saved and loaded."""
        from stacking_ensemble import StackingEnsemble
        import tempfile
        feature_names = [f"feature_{i}" for i in range(self.X.shape[1])]
        self.ensemble.fit(self.X, self.y, feature_names=feature_names)

        with tempfile.NamedTemporaryFile(suffix=".joblib", delete=False) as f:
            path = f.name

        try:
            self.ensemble.save(path)
            assert os.path.exists(path)

            new_ensemble = StackingEnsemble()
            new_ensemble.load(path)
            assert new_ensemble.is_fitted is True
            assert new_ensemble._classes_ == self.ensemble._classes_
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ============================================================================
# Phase 2: Feature Engineering Tests
# ============================================================================

class TestFeatureEngineering:
    """Tests for Phase 2: Advanced Feature Engineering."""

    def setup_method(self):
        from feature_engineering import AdvancedFeatureEngineer
        self.engineer = AdvancedFeatureEngineer()
        self.weather_data = generate_synthetic_weather_data(100, "Lahore")

    def test_lag_features(self):
        """Test that lag features are created."""
        df = pd.DataFrame(self.weather_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        df = self.engineer._add_lag_features(df)

        # Check lag features exist
        assert 'temperature_lag_1h' in df.columns
        assert 'temperature_lag_3h' in df.columns
        assert 'temperature_lag_6h' in df.columns
        assert 'temperature_lag_12h' in df.columns
        assert 'humidity_lag_1h' in df.columns
        assert 'pressure_lag_1h' in df.columns

    def test_rolling_statistics(self):
        """Test that rolling statistics are created."""
        df = pd.DataFrame(self.weather_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        df = self.engineer._add_rolling_statistics(df)

        # Check rolling features exist
        assert 'temperature_rolling_mean_3h' in df.columns
        assert 'temperature_rolling_std_3h' in df.columns
        assert 'temperature_rolling_min_6h' in df.columns
        assert 'temperature_rolling_max_12h' in df.columns

    def test_interaction_terms(self):
        """Test that interaction terms are created."""
        df = pd.DataFrame(self.weather_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        df = self.engineer._add_interaction_terms(df)

        # Check interaction features exist
        assert 'heat_index' in df.columns
        assert 'vapor_pressure_deficit' in df.columns
        assert 'air_density_proxy' in df.columns
        assert 'dew_point' in df.columns
        assert 'wind_chill' in df.columns

    def test_cyclical_encoding(self):
        """Test that cyclical encoding is created."""
        df = pd.DataFrame(self.weather_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        df = self.engineer._add_cyclical_encoding(df)

        # Check cyclical features exist
        assert 'hour_sin' in df.columns
        assert 'hour_cos' in df.columns
        assert 'day_sin' in df.columns
        assert 'day_cos' in df.columns
        assert 'month_sin' in df.columns
        assert 'month_cos' in df.columns

        # Check that sine/cosine values are in [-1, 1]
        assert df['hour_sin'].min() >= -1.0
        assert df['hour_sin'].max() <= 1.0

    def test_upper_level_features(self):
        """Test that upper-level features are created."""
        df = pd.DataFrame(self.weather_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        df = self.engineer._add_upper_level_features(df, "Lahore")

        # Check upper-level features exist
        assert 'geopotential_height_850hPa' in df.columns
        assert 'geopotential_height_500hPa' in df.columns
        assert 'u_wind_850hPa' in df.columns
        assert 'u_wind_500hPa' in df.columns
        assert 'temperature_850hPa' in df.columns

    def test_full_feature_engineering(self):
        """Test the full feature engineering pipeline."""
        df = pd.DataFrame(self.weather_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        df_engineered, feature_names = self.engineer.engineer_features(df, "Lahore")

        # Check that features were created
        assert len(feature_names) > 10
        assert len(df_engineered) > 0

        # Check no NaN values in feature columns
        for col in feature_names:
            assert not df_engineered[col].isna().all(), f"Column {col} is all NaN"

    def test_single_prediction_features(self):
        """Test feature preparation for single prediction."""
        recent_history = self.weather_data[-24:]
        current = self.weather_data[-1]

        features = self.engineer.prepare_single_prediction(
            current, recent_history, "Lahore"
        )

        assert features.shape[0] == 1
        assert features.shape[1] > 0
        assert not np.any(np.isnan(features))


# ============================================================================
# Phase 3: Evaluation Tests
# ============================================================================

class TestEvaluation:
    """Tests for Phase 3: Time-Aware & Location-Aware Cross-Validation."""

    def setup_method(self):
        from evaluation import ComprehensiveEvaluator, TemporalSplitCV, LeaveOneLocationOutCV
        self.evaluator = ComprehensiveEvaluator(temporal_splits=3, train_fraction=0.8)
        self.temporal_cv = TemporalSplitCV(n_splits=3, train_size=0.8)
        self.lolo_cv = LeaveOneLocationOutCV()

        # Generate test data
        np.random.seed(42)
        self.X = np.random.randn(200, 10)
        self.y = np.random.choice(["normal", "extreme_heat_heatwave", "high_wind_storm"], 200)
        self.timestamps = pd.date_range("2020-01-01", periods=200, freq="h").values
        self.locations = np.array(["Lahore"] * 100 + ["Karachi"] * 100)

    def test_temporal_split(self):
        """Test that temporal split produces valid train/test indices."""
        splits = list(self.temporal_cv.split(self.X, self.y, timestamps=self.timestamps))

        assert len(splits) > 0
        for train_idx, test_idx in splits:
            assert len(train_idx) > 0
            assert len(test_idx) > 0
            # Training should come before testing (temporal order)
            assert max(train_idx) < min(test_idx) or max(train_idx) >= len(self.X) - 1

    def test_lolo_split(self):
        """Test that LOLO split produces valid train/test indices."""
        splits = list(self.lolo_cv.split(self.X, self.y, groups=self.locations))

        assert len(splits) == 2  # Two locations
        for train_idx, test_idx in splits:
            assert len(train_idx) > 0
            assert len(test_idx) > 0
            # Test location should not be in training
            test_locations = set(self.locations[test_idx])
            train_locations = set(self.locations[train_idx])
            assert len(test_locations & train_locations) == 0

    def test_brier_skill_score(self):
        """Test Brier Skill Score computation."""
        from evaluation import EvaluationMetrics

        y_true = np.array([0, 1, 0, 1, 0])
        y_prob = np.array([[0.8, 0.2], [0.3, 0.7], [0.9, 0.1], [0.2, 0.8], [0.7, 0.3]])

        bss = EvaluationMetrics.brier_skill_score(y_true, y_prob)
        assert isinstance(bss, float)
        assert -1.0 <= bss <= 1.0

    def test_auc_roc(self):
        """Test AUC-ROC computation."""
        from evaluation import EvaluationMetrics

        y_true = np.array([0, 1, 0, 1, 0, 1])
        y_prob = np.array([[0.8, 0.2], [0.3, 0.7], [0.9, 0.1], [0.2, 0.8], [0.7, 0.3], [0.1, 0.9]])

        auc = EvaluationMetrics.auc_roc(y_true, y_prob)
        assert 0.0 <= auc <= 1.0

    def test_evaluate_metrics(self):
        """Test comprehensive evaluation metrics."""
        from evaluation import EvaluationMetrics

        y_true = np.array([0, 1, 0, 1, 0])
        y_pred = np.array([0, 1, 0, 0, 0])
        y_prob = np.array([[0.8, 0.2], [0.3, 0.7], [0.9, 0.1], [0.6, 0.4], [0.7, 0.3]])

        results = EvaluationMetrics.evaluate(y_true, y_pred, y_prob)

        assert "accuracy" in results
        assert "brier_score" in results
        assert "brier_skill_score" in results
        assert "auc_roc" in results
        assert "per_class" in results


# ============================================================================
# Phase 4: XAI Tests
# ============================================================================

class TestXAI:
    """Tests for Phase 4: Explainable AI Integration."""

    def setup_method(self):
        from stacking_ensemble import StackingEnsemble
        from xai_explainability import XAISystem

        # Train a small ensemble
        np.random.seed(42)
        self.X = np.random.randn(100, 10)
        self.y = np.random.choice(["normal", "extreme_heat_heatwave"], 100)
        self.feature_names = [f"feature_{i}" for i in range(10)]

        self.ensemble = StackingEnsemble(n_estimators_rf=5, n_hidden_elm=10, cv_folds=3, random_state=42)
        self.ensemble.fit(self.X, self.y, feature_names=self.feature_names)

        self.xai = XAISystem(
            model=self.ensemble,
            feature_names=self.feature_names,
            class_names=["normal", "extreme_heat_heatwave"],
            training_data=self.X,
        )

    def test_shap_global_explanation(self):
        """Test SHAP global feature importance."""
        result = self.xai.explain_global(self.X[:50], max_samples=50)

        assert "feature_importance" in result
        assert "feature_importance_ranked" in result
        assert "explanation_text" in result
        assert len(result["feature_importance_ranked"]) > 0

    def test_lime_local_explanation(self):
        """Test LIME local explanation."""
        result = self.xai.explain_local(self.X[0], top_k=3)

        assert "explanation_text" in result
        assert "top_features" in result
        assert "predicted_class" in result
        assert "confidence" in result
        assert len(result["top_features"]) <= 3

    def test_alert_explanation(self):
        """Test multi-hazard alert explanation."""
        hazard_probs = {
            "extreme_heat_heatwave": 0.85,
            "extreme_cold_frost": 0.1,
            "heavy_rain_flood": 0.05,
            "high_wind_storm": 0.2,
            "normal": 0.15,
        }

        result = self.xai.explain_alert(self.X[0], hazard_probs, top_k=3)

        assert "alert_explanation" in result
        assert "predicted_hazard" in result
        assert "hazard_confidence" in result
        assert "all_hazard_probabilities" in result


# ============================================================================
# Phase 5: Multi-Hazard Output Tests
# ============================================================================

class TestMultiHazard:
    """Tests for Phase 5: Multi-Hazard Output & Crisis Communication."""

    def setup_method(self):
        from multi_hazard import MultiHazardClassifier, CrisisCommunicationSystem, AlertStore
        self.classifier = MultiHazardClassifier()
        self.comm = CrisisCommunicationSystem()

        self.weather_data = {
            "temperature": 42.0,
            "humidity": 20.0,
            "pressure": 995.0,
            "wind_speed": 30.0,
            "condition": "Clear",
            "location": "Lahore",
        }

    def test_rule_based_probabilities(self):
        """Test rule-based hazard probability computation."""
        probs = self.classifier._compute_rule_based_probabilities(self.weather_data)

        assert "extreme_heat_heatwave" in probs
        assert "extreme_cold_frost" in probs
        assert "heavy_rain_flood" in probs
        assert "high_wind_storm" in probs
        assert "normal" in probs

        # High temperature should trigger heat hazard
        assert probs["extreme_heat_heatwave"] > 0.5

    def test_classify_hazards(self):
        """Test multi-hazard classification."""
        result = self.classifier.classify_hazards(self.weather_data)

        assert "hazard_probabilities" in result
        assert "risk_levels" in result
        assert "active_hazards" in result
        assert "recommended_actions" in result
        assert "overall_risk_level" in result
        assert "alert_required" in result

    def test_risk_color_coding(self):
        """Test NWS/INFORM risk color coding."""
        from multi_hazard import RISK_COLORS

        assert "green" in RISK_COLORS
        assert "yellow" in RISK_COLORS
        assert "orange" in RISK_COLORS
        assert "red" in RISK_COLORS

        # Check that colors have required fields
        for color_name, color_info in RISK_COLORS.items():
            assert "label" in color_info
            assert "description" in color_info
            assert "hex" in color_info

    def test_generate_alert(self):
        """Test alert generation."""
        classification = self.classifier.classify_hazards(self.weather_data)
        alert = self.comm.generate_alert("Lahore", classification)

        assert "location" in alert
        assert "message" in alert
        assert "overall_risk_level" in alert
        assert "active_hazards" in alert
        assert "hazard_probabilities" in alert

    def test_sms_alert(self):
        """Test SMS alert generation."""
        classification = self.classifier.classify_hazards(self.weather_data)
        alert = self.comm.generate_alert("Lahore", classification)
        sms = self.comm.generate_sms_alert(alert)

        assert len(sms) <= 160  # SMS character limit
        assert "Lahore" in sms

    def test_voice_prompt(self):
        """Test voice prompt generation."""
        classification = self.classifier.classify_hazards(self.weather_data)
        alert = self.comm.generate_alert("Lahore", classification)
        voice = self.comm.generate_voice_prompt(alert)

        assert "Lahore" in voice
        assert len(voice) > 0

    def test_icon_ui(self):
        """Test icon-based UI for low-literacy users."""
        classification = self.classifier.classify_hazards(self.weather_data)
        alert = self.comm.generate_alert("Lahore", classification)
        icon_ui = self.comm.generate_icon_ui(alert)

        assert "icon" in icon_ui
        assert "severity_icon" in icon_ui
        assert "alert_type" in icon_ui
        assert "severity" in icon_ui
        assert "voice_prompt" in icon_ui

    def test_alert_store(self):
        """Test alert persistence."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            from multi_hazard import AlertStore
            store = AlertStore(db_path=db_path)

            classification = self.classifier.classify_hazards(self.weather_data)
            alert = self.comm.generate_alert("Lahore", classification)

            alert_id = store.store_alert(alert)
            assert alert_id > 0

            alerts = store.get_active_alerts()
            assert len(alerts) > 0
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


# ============================================================================
# Phase 6: Edge-Case Hardening Tests
# ============================================================================

class TestEdgeCaseHardening:
    """Tests for Phase 6: Edge-Case Hardening."""

    def setup_method(self):
        from edge_case_hardening_v2 import (
            GracefulDegradationManager, FallbackCacheSystem,
            ConfidencePenaltySystem, CircuitBreaker, CacheMode
        )
        self.FallbackCacheSystem = FallbackCacheSystem
        self.ConfidencePenaltySystem = ConfidencePenaltySystem
        self.CircuitBreaker = CircuitBreaker
        self.CacheMode = CacheMode

        # Use temp directory for cache
        self.temp_dir = tempfile.mkdtemp()
        self.cache = FallbackCacheSystem(cache_dir=self.temp_dir)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cache_store_and_retrieve(self):
        """Test that weather data can be stored and retrieved from cache."""
        weather_data = {
            "temperature": 25.0,
            "humidity": 60.0,
            "pressure": 1013.0,
            "wind_speed": 5.0,
            "condition": "Sunny",
            "location": "Lahore",
        }

        self.cache.store_weather("Lahore", weather_data, confidence=1.0)
        cached = self.cache.get_cached_weather("Lahore")

        assert cached is not None
        assert cached.data["temperature"] == 25.0
        assert cached.is_stale is False

    def test_cache_stale_data(self):
        """Test that stale data is properly flagged."""
        weather_data = {
            "temperature": 25.0,
            "humidity": 60.0,
            "pressure": 1013.0,
            "wind_speed": 5.0,
            "condition": "Sunny",
            "location": "Lahore",
        }

        # Store with old timestamp
        from datetime import datetime, timedelta
        cached = type("Cached", (), {
            "location": "Lahore",
            "data": weather_data,
            "timestamp": datetime.now() - timedelta(hours=3),
            "source": "live",
            "confidence": 1.0,
            "is_stale": False,
        })
        self.cache._weather_cache["Lahore"].append(cached)

        result = self.cache.get_cached_weather("Lahore")
        assert result is not None
        assert result.is_stale is True

    def test_cache_empty(self):
        """Test that empty cache returns None."""
        result = self.cache.get_cached_weather("NonExistentLocation")
        assert result is None

    def test_confidence_penalty_stale_data(self):
        """Test that confidence is penalized for stale data."""
        confidence, reasons = self.ConfidencePenaltySystem.apply_penalty(
            base_confidence=0.9,
            data_age_hours=5.0,
        )

        assert confidence < 0.9
        assert "data_stale_5.0h" in reasons

    def test_confidence_penalty_missing_fields(self):
        """Test that confidence is penalized for missing fields."""
        confidence, reasons = self.ConfidencePenaltySystem.apply_penalty(
            base_confidence=0.9,
            missing_fields=2,
        )

        assert confidence < 0.9
        assert "missing_fields_2" in reasons

    def test_confidence_penalty_cache_mode(self):
        """Test that confidence is penalized for cache mode."""
        confidence, reasons = self.ConfidencePenaltySystem.apply_penalty(
            base_confidence=0.9,
            is_cache_mode=True,
        )

        assert confidence < 0.9
        assert "cache_mode_active" in reasons

    def test_confidence_minimum(self):
        """Test that confidence never goes below minimum."""
        confidence, _ = self.ConfidencePenaltySystem.apply_penalty(
            base_confidence=0.5,
            data_age_hours=50.0,
            missing_fields=10,
            is_cache_mode=True,
        )

        assert confidence >= 0.1  # CONFIDENCE_MINIMUM

    def test_circuit_breaker(self):
        """Test circuit breaker behavior."""
        cb = self.CircuitBreaker(name="test", failure_threshold=3, timeout_seconds=1)

        # Initially closed
        assert cb.state == "CLOSED"
        assert cb.allow_request() is True

        # Record failures
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "CLOSED"  # Not yet at threshold

        cb.record_failure()
        assert cb.state == "OPEN"  # Now open
        assert cb.allow_request() is False  # Should reject

        # Record success (from half-open)
        cb.record_success()
        assert cb.state == "CLOSED"

    def test_cache_mode_enum(self):
        """Test cache mode enum values."""
        assert self.CacheMode.ONLINE.value == "online"
        assert self.CacheMode.DEGRADED.value == "degraded"
        assert self.CacheMode.CACHE_ONLY.value == "cache_only"
        assert self.CacheMode.OFFLINE.value == "offline"

    def test_cache_stats(self):
        """Test cache statistics."""
        self.cache.store_weather("Lahore", {"temperature": 25.0}, confidence=1.0)
        self.cache.get_cached_weather("Lahore")
        self.cache.get_cached_weather("NonExistent")

        stats = self.cache.get_cache_stats()
        assert "mode" in stats
        assert "total_weather_entries" in stats
        assert "cache_hits" in stats
        assert "cache_misses" in stats
        assert stats["total_weather_entries"] > 0


# ============================================================================
# Integration Test
# ============================================================================

class TestIntegration:
    """Integration tests for the full system."""

    def test_full_pipeline(self):
        """Test the full weather prediction pipeline."""
        from stacking_ensemble import StackingEnsemble
        from feature_engineering import AdvancedFeatureEngineer
        from multi_hazard import MultiHazardClassifier, CrisisCommunicationSystem

        # Generate data
        weather_data = generate_synthetic_weather_data(200, "Lahore")
        df = pd.DataFrame(weather_data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        # Feature engineering
        engineer = AdvancedFeatureEngineer()
        df_engineered, feature_names = engineer.engineer_features(df, "Lahore")

        # Create hazard labels based on the engineered data (matching rows)
        # Use quantile-based thresholds to ensure multiple classes
        temps = df_engineered['temperature'].dropna()
        heat_threshold = temps.quantile(0.85)  # Top 15% = heat
        cold_threshold = temps.quantile(0.15)  # Bottom 15% = cold
        
        labels = []
        for _, row in df_engineered.iterrows():
            temp = row.get('temperature', 20.0)
            if pd.isna(temp):
                temp = 20.0
            if temp >= heat_threshold:
                labels.append("extreme_heat_heatwave")
            elif temp <= cold_threshold:
                labels.append("extreme_cold_frost")
            else:
                labels.append("normal")

        X = df_engineered[feature_names].values.astype(float)
        y = np.array(labels)

        # Ensure X and y have the same length
        assert len(X) == len(y), f"X length {len(X)} != y length {len(y)}"
        # Ensure no NaN in X
        assert not np.any(np.isnan(X)), "X contains NaN values"

        # Train ensemble
        ensemble = StackingEnsemble(
            n_estimators_rf=10, n_estimators_xgb=10,
            n_estimators_lgb=10, n_hidden_elm=20,
            cv_folds=3, random_state=42,
        )
        ensemble.fit(X, y, feature_names=feature_names)

        # Predict
        probs = ensemble.predict_proba(X[:5])
        assert probs.shape[0] == 5

        # Multi-hazard prediction
        result = ensemble.predict_multi_hazard(X[:1])
        assert "hazard_probabilities" in result
        assert "risk_levels" in result

        # Hazard classification
        classifier = MultiHazardClassifier(ml_model=ensemble, feature_names=feature_names)
        hazard_result = classifier.classify_hazards(
            weather_data={"temperature": 40.0, "humidity": 30.0, "pressure": 1000.0, "wind_speed": 10.0},
            features=X[:1],
        )
        assert "hazard_probabilities" in hazard_result
        assert "active_hazards" in hazard_result

    def test_degradation_integration(self):
        """Test graceful degradation integration."""
        from edge_case_hardening_v2 import GracefulDegradationManager, CacheMode

        temp_dir = tempfile.mkdtemp()
        try:
            manager = GracefulDegradationManager(
                cache_dir=temp_dir,
                api_url="https://api.openweathermap.org/data/2.5/weather",
                api_key="test_key",
            )

            # Store some cached data
            manager.cache.store_weather("Lahore", {
                "temperature": 25.0,
                "humidity": 60.0,
                "pressure": 1013.0,
                "wind_speed": 5.0,
                "condition": "Sunny",
                "location": "Lahore",
            }, confidence=1.0)

            # Fetch with degradation (will use cache since API key is fake)
            result = manager.fetch_weather_with_degradation("Lahore")

            assert "mode" in result
            assert "confidence" in result
            assert "confidence_label" in result
            assert result["confidence"] >= 0.1

            # Check system status
            status = manager.get_system_status()
            assert "mode" in status
            assert "circuit_breaker_state" in status
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    if HAS_PYTEST:
        pytest.main([__file__, "-v", "--tb=short"])
    else:
        # Fall back to unittest
        import unittest
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()

        # Add all test classes
        for test_class in [
            TestStackingEnsemble,
            TestFeatureEngineering,
            TestEvaluation,
            TestXAI,
            TestMultiHazard,
            TestEdgeCaseHardening,
            TestIntegration,
        ]:
            tests = loader.loadTestsFromTestCase(test_class)
            suite.addTests(tests)

        runner = unittest.TextTestRunner(verbosity=2)
        runner.run(suite)
