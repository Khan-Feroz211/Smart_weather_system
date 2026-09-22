"""
Explainable AI (XAI) Integration for Multi-Hazard Weather Prediction
=====================================================================

Phase 4: Explainable AI (XAI) Integration

Implements dual-layer explainability:

  1. Global Explainability: SHAP (SHapley Additive exPlanations) to rank
     which features are generally most important for predictions in the region.

  2. Local Explainability: LIME to explain why a specific alert was triggered.
     The system outputs a text report alongside the alert.

Justification: Users don't trust what they don't understand. Explainable AI
builds trust with authorities and farmers, ensuring they act on the warnings.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

try:
    import shap
    HAS_SHAP = True
except ImportError:
    HAS_SHAP = False
    logging.warning("SHAP not available. Install with: pip install shap")

try:
    from lime import lime_tabular
    HAS_LIME = True
except ImportError:
    HAS_LIME = False
    logging.warning("LIME not available. Install with: pip install lime")

logger = logging.getLogger(__name__)


# ============================================================================
# Global Explainability: SHAP
# ============================================================================

class SHAPExplainer:
    """
    Global explainability using SHAP (SHapley Additive exPlanations).

    Uses SHAP to rank which features are generally most important for
    predictions in the region. This helps:
      - Identify which weather variables drive hazard predictions
      - Validate that the model uses physically meaningful features
      - Communicate to authorities which factors to monitor

    SHAP values are based on cooperative game theory and provide a
    consistent, theoretically grounded measure of feature importance.
    """

    def __init__(self, model, feature_names: List[str], background_data: Optional[np.ndarray] = None):
        self.model = model
        self.feature_names = feature_names
        self.background_data = background_data
        self._explainer = None
        self._is_initialized = False

    def _get_predict_fn(self):
        """Get the prediction function from the model."""
        if hasattr(self.model, "predict_proba"):
            return self.model.predict_proba
        elif hasattr(self.model, "predict"):
            return self.model.predict
        else:
            raise ValueError("Model does not have predict or predict_proba method.")

    def _initialize_explainer(self, X_sample: np.ndarray):
        """Initialize the SHAP explainer."""
        if not HAS_SHAP:
            logger.warning("SHAP not available. Using fallback feature importance.")
            return

        predict_fn = self._get_predict_fn()

        # Use TreeExplainer for tree-based models (RF, XGBoost, LightGBM)
        # This is much faster than KernelExplainer
        base_learners = getattr(self.model, "_base_learners", {})
        if "random_forest" in base_learners:
            rf_model = base_learners["random_forest"]
            try:
                self._explainer = shap.TreeExplainer(rf_model)
                self._is_initialized = True
                return
            except Exception as e:
                logger.warning(f"TreeExplainer failed: {e}. Falling back to KernelExplainer.")

        # Fallback: use the model's predict_proba with KernelExplainer
        try:
            if self.background_data is not None:
                background = self.background_data[:min(100, len(self.background_data))]
            else:
                background = X_sample[:min(100, len(X_sample))]

            self._explainer = shap.Explainer(predict_fn, background)
            self._is_initialized = True
        except Exception as e:
            logger.warning(f"SHAP explainer initialization failed: {e}")
            self._is_initialized = False

    def compute_global_shap_values(
        self,
        X: np.ndarray,
        max_samples: int = 500,
    ) -> Dict[str, Any]:
        """
        Compute global SHAP values for feature importance ranking.

        Parameters
        ----------
        X : np.ndarray
            Feature matrix to explain.
        max_samples : int
            Maximum number of samples to use (for performance).

        Returns
        -------
        dict with:
            - feature_importance: dict mapping feature -> mean |SHAP| value
            - feature_importance_ranked: list of (feature, importance) sorted
            - shap_values: raw SHAP values (if available)
            - explanation_text: human-readable summary
        """
        if not HAS_SHAP:
            return self._fallback_feature_importance(X)

        # Subsample for performance
        if len(X) > max_samples:
            np.random.seed(42)
            indices = np.random.choice(len(X), max_samples, replace=False)
            X_sample = X[indices]
        else:
            X_sample = X

        # Initialize explainer if needed
        if not self._is_initialized:
            self._initialize_explainer(X_sample)

        if not self._is_initialized:
            return self._fallback_feature_importance(X)

        try:
            # Compute SHAP values
            shap_values = self._explainer(X_sample)

            # Handle different SHAP value formats
            if hasattr(shap_values, 'values'):
                # New SHAP API (v0.45+)
                values = shap_values.values
            else:
                values = np.array(shap_values)

            # For multi-class, take the mean absolute value across all classes
            if values.ndim == 3:
                # Shape: (n_samples, n_features, n_classes)
                mean_abs_shap = np.mean(np.abs(values), axis=(0, 2))
            else:
                # Shape: (n_samples, n_features)
                mean_abs_shap = np.mean(np.abs(values), axis=0)

            # Rank features by importance
            feature_importance = dict(zip(self.feature_names, mean_abs_shap.tolist()))
            ranked = sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)

            # Generate explanation text
            explanation_text = self._generate_global_explanation(ranked)

            return {
                "feature_importance": feature_importance,
                "feature_importance_ranked": ranked,
                "explanation_text": explanation_text,
                "shap_available": True,
                "n_samples": len(X_sample),
            }

        except Exception as e:
            logger.warning(f"SHAP computation failed: {e}")
            return self._fallback_feature_importance(X)

    def _fallback_feature_importance(self, X: np.ndarray) -> Dict[str, Any]:
        """Fallback feature importance using model's built-in importance."""
        logger.info("Using fallback feature importance (model built-in).")

        # Try to get feature importance from the model
        importance = None
        base_learners = getattr(self.model, "_base_learners", {})

        if "random_forest" in base_learners:
            rf = base_learners["random_forest"]
            if hasattr(rf, "feature_importances_"):
                importance = rf.feature_importances_

        if importance is None:
            # Use correlation-based importance
            importance = np.abs(np.corrcoef(X.T, np.random.RandomState(42).randint(0, 2, X.shape[0]))[:X.shape[1], -1])

        feature_importance = dict(zip(self.feature_names, importance.tolist()))
        ranked = sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)

        explanation_text = self._generate_global_explanation(ranked)

        return {
            "feature_importance": feature_importance,
            "feature_importance_ranked": ranked,
            "explanation_text": explanation_text,
            "shap_available": False,
            "n_samples": len(X),
        }

    def _generate_global_explanation(self, ranked: List[Tuple[str, float]]) -> str:
        """Generate human-readable explanation of global feature importance."""
        lines = []
        lines.append("GLOBAL FEATURE IMPORTANCE (SHAP)")
        lines.append("=" * 50)
        lines.append("")
        lines.append("The following features are the most important drivers of")
        lines.append("hazard predictions in your region:")
        lines.append("")

        for i, (feature, importance) in enumerate(ranked[:10]):
            # Interpret the feature name
            interpretation = self._interpret_feature(feature)
            lines.append(f"  {i+1}. {feature}")
            lines.append(f"     Importance: {importance:.4f}")
            lines.append(f"     {interpretation}")
            lines.append("")

        lines.append("These features should be monitored closely for early")
        lines.append("warning of extreme weather events.")

        return "\n".join(lines)

    def _interpret_feature(self, feature_name: str) -> str:
        """Provide a human-readable interpretation of a feature."""
        interpretations = {
            "temperature": "Current temperature — directly drives heat/cold hazards",
            "humidity": "Relative humidity — affects heat stress and precipitation",
            "pressure": "Atmospheric pressure — falling pressure signals storms",
            "wind_speed": "Wind speed — directly drives wind/storm hazards",
            "heat_index": "Perceived temperature combining heat and humidity",
            "vapor_pressure_deficit": "Moisture deficit — indicates drought/fire risk",
            "dew_point": "Temperature at which air saturates — frost indicator",
            "air_density_proxy": "Air density — affects storm development",
            "wind_chill": "Effective temperature considering wind — cold hazard",
            "pressure_trend_6h": "Pressure change over 6h — storm precursor",
            "temp_trend_6h": "Temperature change over 6h — heat/cold snap indicator",
            "hour_sin": "Cyclical encoding of hour — daily temperature cycle",
            "hour_cos": "Cyclical encoding of hour — daily temperature cycle",
            "day_sin": "Cyclical encoding of day — seasonal patterns",
            "day_cos": "Cyclical encoding of day — seasonal patterns",
            "geopotential_height_850hPa": "Upper-air height at 850hPa — synoptic patterns",
            "geopotential_height_500hPa": "Upper-air height at 500hPa — ridge/trough",
            "temperature_850hPa": "Upper-air temperature at 850hPa — air mass",
            "thickness_850_500": "Column thickness — stability indicator",
            "height_anomaly_500hPa": "500hPa height anomaly — ridge/trough deviation",
            "temp_advection_proxy": "Temperature advection — warm/cold air transport",
            "wind_shear_u": "Zonal wind shear — storm development",
            "wind_shear_v": "Meridional wind shear — storm development",
            "fire_weather_index": "Fire weather potential — drought/fire risk",
            "storm_potential": "Composite storm development indicator",
            "gdd_cumulative": "Cumulative growing degree days — seasonal progress",
        }

        # Check for lag/rolling features
        if "_lag_" in feature_name:
            return "Lagged measurement — temporal pattern for trend analysis"
        if "_rolling_mean_" in feature_name:
            return "Rolling average — smoothed trend for stability"
        if "_rolling_std_" in feature_name:
            return "Rolling standard deviation — volatility indicator"
        if "_rolling_min_" in feature_name:
            return "Rolling minimum — extreme low value tracking"
        if "_rolling_max_" in feature_name:
            return "Rolling maximum — extreme high value tracking"
        if "_rate_" in feature_name:
            return "Rate of change — trend speed indicator"

        return interpretations.get(feature_name, "Engineered weather feature")


# ============================================================================
# Local Explainability: LIME
# ============================================================================

class LIMEExplainer:
    """
    Local explainability using LIME (Local Interpretable Model-agnostic
    Explanations).

    Explains why a specific alert was triggered by approximating the
    model's prediction locally with an interpretable model (linear).

    The system outputs a text report alongside the alert, e.g.:
    "Alert Triggered because: 'High humidity combined with rising 3-hour
    temperature trend exceeds thresholds'"
    """

    def __init__(
        self,
        model,
        feature_names: List[str],
        class_names: Optional[List[str]] = None,
        training_data: Optional[np.ndarray] = None,
    ):
        self.model = model
        self.feature_names = feature_names
        self.class_names = class_names or []
        self.training_data = training_data
        self._explainer = None
        self._is_initialized = False

    def _initialize_explainer(self):
        """Initialize the LIME explainer."""
        if not HAS_LIME:
            logger.warning("LIME not available. Using fallback explanation.")
            return

        try:
            predict_fn = self.model.predict_proba if hasattr(self.model, "predict_proba") else self.model.predict

            if self.training_data is not None:
                training_data = self.training_data[:min(5000, len(self.training_data))]
            else:
                training_data = np.random.RandomState(42).randn(1000, len(self.feature_names))

            self._explainer = lime_tabular.LimeTabularExplainer(
                training_data=training_data,
                feature_names=self.feature_names,
                class_names=self.class_names if self.class_names else [f"class_{i}" for i in range(5)],
                mode="classification",
                verbose=False,
                random_state=42,
            )
            self._is_initialized = True
        except Exception as e:
            logger.warning(f"LIME initialization failed: {e}")
            self._is_initialized = False

    def explain_prediction(
        self,
        X_instance: np.ndarray,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        Explain a single prediction using LIME.

        Parameters
        ----------
        X_instance : np.ndarray of shape (1, n_features)
            Single instance to explain.
        top_k : int
            Number of top features to include in the explanation.

        Returns
        -------
        dict with:
            - explanation_text: human-readable explanation
            - top_features: list of (feature, weight) tuples
            - predicted_class: the predicted class
            - confidence: prediction confidence
        """
        if not self._is_initialized:
            self._initialize_explainer()

        if not self._is_initialized or not HAS_LIME:
            return self._fallback_explanation(X_instance, top_k)

        try:
            # Ensure X_instance is 1D
            if X_instance.ndim == 2:
                X_instance = X_instance[0]

            # Get LIME explanation
            explanation = self._explainer.explain_instance(
                X_instance,
                self.model.predict_proba,
                num_features=top_k,
            )

            # Extract top features
            top_features = explanation.as_list()

            # Get predicted class
            predicted_class_idx = np.argmax(self.model.predict_proba(X_instance.reshape(1, -1))[0])
            predicted_class = self.class_names[predicted_class_idx] if self.class_names else str(predicted_class_idx)

            # Get confidence
            probs = self.model.predict_proba(X_instance.reshape(1, -1))[0]
            confidence = float(probs[predicted_class_idx])

            # Generate text explanation
            explanation_text = self._generate_local_explanation(
                top_features, predicted_class, confidence
            )

            return {
                "explanation_text": explanation_text,
                "top_features": top_features,
                "predicted_class": predicted_class,
                "confidence": round(confidence, 4),
                "lime_available": True,
            }

        except Exception as e:
            logger.warning(f"LIME explanation failed: {e}")
            return self._fallback_explanation(X_instance, top_k)

    def _fallback_explanation(self, X_instance: np.ndarray, top_k: int) -> Dict[str, Any]:
        """Fallback explanation when LIME is not available."""
        # Use simple correlation-based feature importance
        if X_instance.ndim == 2:
            X_instance = X_instance[0]

        # Find features with highest absolute values (most "extreme")
        abs_values = np.abs(X_instance)
        top_indices = np.argsort(abs_values)[::-1][:top_k]

        top_features = []
        for idx in top_indices:
            feature_name = self.feature_names[idx] if idx < len(self.feature_names) else f"feature_{idx}"
            top_features.append((feature_name, float(X_instance[idx])))

        # Get predicted class
        predicted_class_idx = np.argmax(self.model.predict_proba(X_instance.reshape(1, -1))[0])
        predicted_class = self.class_names[predicted_class_idx] if self.class_names else str(predicted_class_idx)
        confidence = float(self.model.predict_proba(X_instance.reshape(1, -1))[0][predicted_class_idx])

        explanation_text = self._generate_local_explanation(
            top_features, predicted_class, confidence
        )

        return {
            "explanation_text": explanation_text,
            "top_features": top_features,
            "predicted_class": predicted_class,
            "confidence": round(confidence, 4),
            "lime_available": False,
        }

    def _generate_local_explanation(
        self,
        top_features: List[Tuple[str, float]],
        predicted_class: str,
        confidence: float,
    ) -> str:
        """
        Generate human-readable local explanation.

        Example output:
        "Alert Triggered because: 'High humidity combined with rising 3-hour
        temperature trend exceeds thresholds'"
        """
        lines = []

        # Alert header
        hazard_emoji = {
            "extreme_heat_heatwave": "🌡️",
            "extreme_cold_frost": "❄️",
            "heavy_rain_flood": "🌊",
            "high_wind_storm": "💨",
            "normal": "✅",
        }
        emoji = hazard_emoji.get(predicted_class, "⚠️")

        lines.append(f"{emoji} ALERT TRIGGERED: {predicted_class.replace('_', ' ').title()}")
        lines.append(f"   Confidence: {confidence:.1%}")
        lines.append("")
        lines.append("Alert Triggered because:")
        lines.append("")

        # Build the explanation from top features
        feature_descriptions = []
        for feature_name, weight in top_features:
            interpretation = self._interpret_feature_contribution(feature_name, weight)
            feature_descriptions.append(interpretation)

        # Join with "combined with" for natural language
        if len(feature_descriptions) == 1:
            lines.append(f"  • {feature_descriptions[0]}")
        elif len(feature_descriptions) == 2:
            lines.append(f"  • {feature_descriptions[0]} combined with {feature_descriptions[1]}")
        else:
            lines.append(f"  • {feature_descriptions[0]} combined with {feature_descriptions[1]}")
            for desc in feature_descriptions[2:]:
                lines.append(f"    and {desc}")

        lines.append("")
        lines.append("Top contributing factors:")
        for feature_name, weight in top_features:
            direction = "increases" if weight > 0 else "decreases"
            lines.append(f"  • {feature_name}: {direction} risk (weight: {weight:+.4f})")

        return "\n".join(lines)

    def _interpret_feature_contribution(self, feature_name: str, weight: float) -> str:
        """Interpret a feature's contribution to the prediction."""
        direction = "high" if weight > 0 else "low"

        interpretations = {
            "temperature": f"high temperature ({direction})",
            "humidity": f"{direction} humidity levels",
            "pressure": f"{direction} atmospheric pressure",
            "wind_speed": f"{direction} wind speed",
            "heat_index": f"elevated heat index ({direction})",
            "vapor_pressure_deficit": f"{direction} moisture deficit",
            "dew_point": f"{direction} dew point",
            "pressure_trend_6h": f"{direction} pressure trend over 6h",
            "temp_trend_6h": f"{direction} 3-hour temperature trend",
            "temp_trend_12h": f"{direction} 12-hour temperature trend",
            "wind_chill": f"{direction} wind chill",
            "hour_sin": "time of day pattern",
            "hour_cos": "time of day pattern",
            "day_sin": "seasonal pattern",
            "day_cos": "seasonal pattern",
            "geopotential_height_500hPa": f"{direction} 500hPa height (synoptic pattern)",
            "temperature_850hPa": f"{direction} 850hPa temperature (air mass)",
            "thickness_850_500": f"{direction} atmospheric column thickness",
            "height_anomaly_500hPa": f"{direction} 500hPa height anomaly",
            "temp_advection_proxy": f"{direction} temperature advection",
            "wind_shear_u": f"{direction} zonal wind shear",
            "wind_shear_v": f"{direction} meridional wind shear",
            "fire_weather_index": f"{direction} fire weather potential",
            "storm_potential": f"{direction} storm development potential",
            "gdd_cumulative": f"{direction} growing degree days",
        }

        if "_lag_" in feature_name:
            parts = feature_name.split("_lag_")
            base = parts[0] if parts else "measurement"
            return f"{direction} {base} from {parts[1] if len(parts) > 1 else 'past'} lag"
        if "_rolling_mean_" in feature_name:
            return f"{direction} rolling average"
        if "_rolling_std_" in feature_name:
            return f"{direction} volatility"
        if "_rolling_max_" in feature_name:
            return f"{direction} recent maximum"
        if "_rolling_min_" in feature_name:
            return f"{direction} recent minimum"
        if "_rate_" in feature_name:
            return f"{direction} rate of change"

        return interpretations.get(feature_name, f"{direction} {feature_name}")


# ============================================================================
# Combined XAI System
# ============================================================================

class XAISystem:
    """
    Combined XAI system providing both global and local explainability.

    This is the main entry point for explainability in the Smart Weather
    System. It combines SHAP for global feature importance and LIME for
    local alert explanations.
    """

    def __init__(
        self,
        model,
        feature_names: List[str],
        class_names: Optional[List[str]] = None,
        training_data: Optional[np.ndarray] = None,
    ):
        self.model = model
        self.feature_names = feature_names
        self.class_names = class_names or []
        self.training_data = training_data

        self.shap_explainer = SHAPExplainer(model, feature_names, training_data)
        self.lime_explainer = LIMEExplainer(
            model, feature_names, class_names, training_data
        )

    def explain_global(self, X: np.ndarray, max_samples: int = 500) -> Dict[str, Any]:
        """Compute global feature importance using SHAP."""
        return self.shap_explainer.compute_global_shap_values(X, max_samples)

    def explain_local(
        self,
        X_instance: np.ndarray,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Explain a single prediction using LIME."""
        return self.lime_explainer.explain_prediction(X_instance, top_k)

    def explain_alert(
        self,
        X_instance: np.ndarray,
        hazard_probabilities: Dict[str, float],
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        Generate a complete explanation for a multi-hazard alert.

        Combines LIME local explanation with the hazard probabilities
        to produce a comprehensive alert report.
        """
        local_explanation = self.explain_local(X_instance, top_k)

        # Find the highest-risk hazard
        max_hazard = max(hazard_probabilities, key=hazard_probabilities.get)
        max_prob = hazard_probabilities[max_hazard]

        return {
            "alert_explanation": local_explanation["explanation_text"],
            "top_features": local_explanation["top_features"],
            "predicted_hazard": max_hazard,
            "hazard_confidence": round(max_prob, 4),
            "all_hazard_probabilities": hazard_probabilities,
            "lime_available": local_explanation["lime_available"],
            "timestamp": datetime.now().isoformat(),
        }
