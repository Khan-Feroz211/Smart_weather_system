"""
crop_suitability.py
===================
Crop Suitability Module.
Ranks crops for a target site based on environmental conditions.
Gracefully withholds or marks estimates offline.
"""

from typing import Dict, Any, List
from crop_database import CROP_DATABASE

def rank_crops(site: Dict[str, Any], connectivity_state: str = "ONLINE") -> Dict[str, Any]:
    state = connectivity_state.upper()

    if state == "OFFLINE":
        return {
            "available": False,
            "status": "OFFLINE",
            "rankings": [],
            "source": "crop_suitability_offline",
            "message": "weather-independent estimate not available offline",
            "disclaimer": "Crop suitability ranking requires weather telemetry, which is unavailable offline."
        }

    # Evaluate simple suitability ranking based on temperature / rainfall
    temp = site.get("temp", 22.0)
    rainfall = site.get("rainfall", 500.0)

    rankings = []
    for crop_key, crop_info in CROP_DATABASE.items():
        bounds = crop_info["optimal_conditions"]
        score = 100.0
        if temp < bounds["temp_min"] or temp > bounds["temp_max"]:
            score -= 30.0
        if rainfall < bounds["rainfall_min"] or rainfall > bounds["rainfall_max"]:
            score -= 30.0

        rankings.append({
            "crop": crop_info["name"],
            "score": max(score, 10.0)
        })

    rankings.sort(key=lambda x: x["score"], reverse=True)

    return {
        "available": True,
        "status": state,
        "rankings": [r["crop"] for r in rankings],
        "details": rankings,
        "source": "crop_suitability_live" if state == "ONLINE" else "crop_suitability_cache",
        "disclaimer": "Suitability calculated using site telemetry. Simulated weather ingestion."
    }
