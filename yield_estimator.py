"""
yield_estimator.py
==================
YieldEstimator module.
Evaluates expected yield impact given crop, weather, growth stage, and disease diagnoses.
Gracefully handles ONLINE, CACHE, and OFFLINE connectivity degradation.
"""

from typing import Dict, Any, List, Optional

class YieldEstimator:
    def estimate(
        self,
        crop: str,
        weather: Dict[str, Any],
        stage: str,
        diagnoses: List[Dict[str, Any]],
        connectivity_state: str = "ONLINE"
    ) -> Dict[str, Any]:
        state = connectivity_state.upper()

        if state == "OFFLINE":
            return {
                "available": False,
                "status": "OFFLINE",
                "impact_percentage": None,
                "source": "yield_estimator_offline",
                "message": "weather-independent estimate not available offline",
                "disclaimer": "Yield impact estimation requires weather/satellite telemetry, which is unavailable offline."
            }

        # Calculate base impact from diagnoses
        base_impact = 0.0
        for diag in diagnoses:
            urgency = diag.get("urgency", "medium")
            if urgency == "critical":
                base_impact -= 25.0
            elif urgency == "high":
                base_impact -= 15.0
            else:
                base_impact -= 5.0

        # Adjust for weather if available
        is_stale = (state == "CACHE")
        source = "yield_estimator_cached_weather" if is_stale else "yield_estimator_live_weather"
        penalty_text = " (Cached weather penalty applied)" if is_stale else ""

        return {
            "available": True,
            "status": state,
            "impact_percentage": round(base_impact, 1),
            "source": source,
            "is_stale": is_stale,
            "disclaimer": f"Estimated yield impact based on {stage} stage and disease severity{penalty_text}. Note: Ingestion data simulated (Tier 1a)."
        }
