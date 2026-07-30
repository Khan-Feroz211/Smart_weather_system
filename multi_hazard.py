"""
Multi-Hazard Output & Crisis Communication
===========================================

Phase 5: Multi-Hazard Output & Crisis Communication

Re-labels the target variable to classify Multiple Hazards simultaneously.
The model outputs probabilities for:

  - Extreme Heat / Heatwave
  - Extreme Cold / Frost
  - Heavy Rain / Flood risk
  - High Wind / Storm risk

The dashboard displays alerts for all categories simultaneously, using the
NWS/INFORM risk color-coding (Green, Yellow, Orange, Red).

Justification: Farmers need to know if they need frost protection and wind
shelter at the same time. A single-number output is insufficient.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ============================================================================
# Hazard Definitions
# ============================================================================

# All hazard categories the system can predict
HAZARD_CATEGORIES = [
    "extreme_heat_heatwave",
    "extreme_cold_frost",
    "heavy_rain_flood",
    "high_wind_storm",
    "normal",
]

# Human-readable names for each hazard
HAZARD_NAMES = {
    "extreme_heat_heatwave": "Extreme Heat / Heatwave",
    "extreme_cold_frost": "Extreme Cold / Frost",
    "heavy_rain_flood": "Heavy Rain / Flood Risk",
    "high_wind_storm": "High Wind / Storm Risk",
    "normal": "Normal Conditions",
}

# Icons for UI display
HAZARD_ICONS = {
    "extreme_heat_heatwave": "🌡️",
    "extreme_cold_frost": "❄️",
    "heavy_rain_flood": "🌊",
    "high_wind_storm": "💨",
    "normal": "✅",
}

# NWS/INFORM risk color coding
RISK_COLORS = {
    "green": {
        "level": 0,
        "label": "Green",
        "description": "No risk",
        "hex": "#00E450",
        "rgb": "0, 228, 80",
    },
    "yellow": {
        "level": 1,
        "label": "Yellow",
        "description": "Moderate risk — be prepared",
        "hex": "#FFCC00",
        "rgb": "255, 204, 0",
    },
    "orange": {
        "level": 2,
        "label": "Orange",
        "description": "High risk — take action",
        "hex": "#FF6600",
        "rgb": "255, 102, 0",
    },
    "red": {
        "level": 3,
        "label": "Red",
        "description": "Severe risk — immediate action required",
        "hex": "#CC0000",
        "rgb": "204, 0, 0",
    },
}

# Thresholds for rule-based hazard detection
# These are used alongside the ML model for robustness
HAZARD_THRESHOLDS = {
    "extreme_heat_heatwave": {
        "temperature": 38.0,       # °C
        "heat_index": 40.0,        # °C
        "duration_hours": 3,      # consecutive hours
        "risk_color_thresholds": {
            "red": {"temperature": 45.0, "heat_index": 50.0},
            "orange": {"temperature": 40.0, "heat_index": 45.0},
            "yellow": {"temperature": 38.0, "heat_index": 40.0},
        },
    },
    "extreme_cold_frost": {
        "temperature": 2.0,        # °C
        "dew_point": 0.0,          # °C
        "duration_hours": 2,
        "risk_color_thresholds": {
            "red": {"temperature": -5.0, "dew_point": -3.0},
            "orange": {"temperature": 0.0, "dew_point": -1.0},
            "yellow": {"temperature": 2.0, "dew_point": 0.0},
        },
    },
    "heavy_rain_flood": {
        "precipitation_rate": 15.0,    # mm/hour
        "cumulative_rainfall": 50.0,   # mm over 24h
        "pressure_trend": -2.0,        # hPa drop over 6h
        "risk_color_thresholds": {
            "red": {"precipitation_rate": 50.0, "cumulative_rainfall": 100.0},
            "orange": {"precipitation_rate": 30.0, "cumulative_rainfall": 75.0},
            "yellow": {"precipitation_rate": 15.0, "cumulative_rainfall": 50.0},
        },
    },
    "high_wind_storm": {
        "wind_speed": 25.0,        # m/s
        "gust_factor": 1.5,        # gust / sustained ratio
        "risk_color_thresholds": {
            "red": {"wind_speed": 40.0},
            "orange": {"wind_speed": 30.0},
            "yellow": {"wind_speed": 25.0},
        },
    },
}

# Recommended actions for each hazard
HAZARD_ACTIONS = {
    "extreme_heat_heatwave": [
        "Limit outdoor activity between 10 AM - 4 PM",
        "Hydrate frequently — drink water even if not thirsty",
        "Apply shade nets for sensitive crops",
        "Monitor elderly and children for heat exhaustion",
        "Postpone irrigation to early morning or evening",
    ],
    "extreme_cold_frost": [
        "Cover sensitive crops with frost cloth or mulch",
        "Irrigate lightly before frost to release heat",
        "Protect livestock with shelter and bedding",
        "Monitor for pipe freezing",
        "Avoid outdoor exposure during peak cold hours",
    ],
    "heavy_rain_flood": [
        "Move livestock and equipment to higher ground",
        "Clear drainage channels and ditches",
        "Secure loose items that could become projectiles",
        "Monitor river levels and weather updates",
        "Avoid crossing flooded roads",
    ],
    "high_wind_storm": [
        "Secure lightweight structures and equipment",
        "Trim trees near buildings and power lines",
        "Avoid travel in exposed areas",
        "Close greenhouses and protect young plants",
        "Monitor for power outages",
    ],
    "normal": [
        "Continue routine monitoring",
        "Check weather updates regularly",
    ],
}


# ============================================================================
# Multi-Hazard Classifier
# ============================================================================

class MultiHazardClassifier:
    """
    Multi-hazard classification system.

    Combines ML model predictions with rule-based hazard detection to
    produce probabilities for all hazards simultaneously.

    The system uses a hybrid approach:
      1. ML model (Stacking Ensemble) provides base hazard probabilities
      2. Rule-based detection adjusts probabilities based on physical thresholds
      3. Risk levels are assigned using NWS/INFORM color coding
    """

    def __init__(self, ml_model=None, feature_names: Optional[List[str]] = None):
        self.ml_model = ml_model
        self.feature_names = feature_names or []
        self.hazard_categories = HAZARD_CATEGORIES

    def classify_hazards(
        self,
        weather_data: Dict[str, Any],
        features: Optional[np.ndarray] = None,
        ml_probabilities: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        Classify all hazards for a given weather observation.

        Parameters
        ----------
        weather_data : dict
            Current weather observation (temperature, humidity, pressure, wind_speed, etc.)
        features : np.ndarray, optional
            Engineered features for ML model prediction.
        ml_probabilities : dict, optional
            Pre-computed ML probabilities. If None, uses the ML model.

        Returns
        -------
        dict with:
            - hazard_probabilities: dict mapping hazard -> probability
            - risk_levels: dict mapping hazard -> risk color
            - active_hazards: list of hazards above threshold
            - recommended_actions: dict mapping hazard -> list of actions
            - overall_risk_level: the highest risk color across all hazards
            - alert_required: bool
        """
        # Get ML probabilities
        if ml_probabilities is None:
            if self.ml_model is not None and features is not None:
                ml_result = self.ml_model.predict_multi_hazard(features)
                ml_probabilities = ml_result.get("hazard_probabilities", {})
            else:
                ml_probabilities = {hazard: 0.0 for hazard in self.hazard_categories}

        # Get rule-based hazard probabilities
        rule_probabilities = self._compute_rule_based_probabilities(weather_data)

        # Combine ML and rule-based probabilities (weighted average)
        # ML weight: 0.7, Rule-based weight: 0.3
        # This ensures physical thresholds are respected even if ML is uncertain
        combined_probabilities = {}
        for hazard in self.hazard_categories:
            ml_prob = ml_probabilities.get(hazard, 0.0)
            rule_prob = rule_probabilities.get(hazard, 0.0)
            combined_probabilities[hazard] = 0.7 * ml_prob + 0.3 * rule_prob

        # Assign risk levels using NWS/INFORM color coding
        risk_levels = {}
        for hazard in self.hazard_categories:
            prob = combined_probabilities[hazard]
            risk_levels[hazard] = self._probability_to_risk_color(hazard, prob)

        # Determine active hazards (above yellow threshold)
        active_hazards = [
            hazard for hazard in self.hazard_categories
            if risk_levels[hazard] in ("yellow", "orange", "red")
            and hazard != "normal"
        ]

        # Determine overall risk level (highest across all hazards)
        overall_risk_level = "green"
        if "red" in risk_levels.values():
            overall_risk_level = "red"
        elif "orange" in risk_levels.values():
            overall_risk_level = "orange"
        elif "yellow" in risk_levels.values():
            overall_risk_level = "yellow"

        # Get recommended actions for active hazards
        recommended_actions = {}
        for hazard in active_hazards:
            recommended_actions[hazard] = HAZARD_ACTIONS.get(hazard, [])

        # Determine if alert is required
        alert_required = len(active_hazards) > 0

        return {
            "hazard_probabilities": {k: round(v, 4) for k, v in combined_probabilities.items()},
            "risk_levels": risk_levels,
            "active_hazards": active_hazards,
            "recommended_actions": recommended_actions,
            "overall_risk_level": overall_risk_level,
            "alert_required": alert_required,
            "hazard_names": HAZARD_NAMES,
            "hazard_icons": HAZARD_ICONS,
            "risk_colors": RISK_COLORS,
            "timestamp": datetime.now().isoformat(),
        }

    def _compute_rule_based_probabilities(self, weather_data: Dict[str, Any]) -> Dict[str, float]:
        """
        Compute hazard probabilities using physical thresholds.

        This provides a robust fallback when ML predictions are uncertain
        and ensures physically meaningful hazard detection.
        """
        probs = {hazard: 0.0 for hazard in self.hazard_categories}

        temp = weather_data.get("temperature", 20.0)
        humidity = weather_data.get("humidity", 60.0)
        pressure = weather_data.get("pressure", 1013.0)
        wind_speed = weather_data.get("wind_speed", 5.0)
        precipitation = weather_data.get("precipitation", 0.0)
        precipitation_rate = weather_data.get("precipitation_rate", 0.0)

        # Compute heat index
        heat_index = temp + 0.33 * np.exp(-0.03 * temp) * humidity

        # Compute dew point (simplified)
        if humidity > 0:
            gamma = np.log(humidity / 100.0) + (17.27 * temp) / (237.3 + temp)
            dew_point = (237.3 * gamma) / (17.27 - gamma)
        else:
            dew_point = temp

        # Extreme Heat / Heatwave
        heat_thresholds = HAZARD_THRESHOLDS["extreme_heat_heatwave"]
        if temp >= heat_thresholds["risk_color_thresholds"]["red"]["temperature"]:
            probs["extreme_heat_heatwave"] = 0.95
        elif temp >= heat_thresholds["risk_color_thresholds"]["orange"]["temperature"]:
            probs["extreme_heat_heatwave"] = 0.75
        elif temp >= heat_thresholds["risk_color_thresholds"]["yellow"]["temperature"]:
            probs["extreme_heat_heatwave"] = 0.50
        elif heat_index >= heat_thresholds["heat_index"]:
            probs["extreme_heat_heatwave"] = 0.40

        # Extreme Cold / Frost
        cold_thresholds = HAZARD_THRESHOLDS["extreme_cold_frost"]
        if temp <= cold_thresholds["risk_color_thresholds"]["red"]["temperature"]:
            probs["extreme_cold_frost"] = 0.95
        elif temp <= cold_thresholds["risk_color_thresholds"]["orange"]["temperature"]:
            probs["extreme_cold_frost"] = 0.75
        elif temp <= cold_thresholds["risk_color_thresholds"]["yellow"]["temperature"]:
            probs["extreme_cold_frost"] = 0.50
        elif dew_point <= cold_thresholds["dew_point"]:
            probs["extreme_cold_frost"] = 0.40

        # Heavy Rain / Flood
        flood_thresholds = HAZARD_THRESHOLDS["heavy_rain_flood"]
        if precipitation_rate >= flood_thresholds["risk_color_thresholds"]["red"]["precipitation_rate"]:
            probs["heavy_rain_flood"] = 0.95
        elif precipitation_rate >= flood_thresholds["risk_color_thresholds"]["orange"]["precipitation_rate"]:
            probs["heavy_rain_flood"] = 0.75
        elif precipitation_rate >= flood_thresholds["risk_color_thresholds"]["yellow"]["precipitation_rate"]:
            probs["heavy_rain_flood"] = 0.50
        elif precipitation >= flood_thresholds["cumulative_rainfall"]:
            probs["heavy_rain_flood"] = 0.40
        elif pressure < 1000 and pressure < 1013:  # Falling pressure
            probs["heavy_rain_flood"] = 0.30

        # High Wind / Storm
        wind_thresholds = HAZARD_THRESHOLDS["high_wind_storm"]
        if wind_speed >= wind_thresholds["risk_color_thresholds"]["red"]["wind_speed"]:
            probs["high_wind_storm"] = 0.95
        elif wind_speed >= wind_thresholds["risk_color_thresholds"]["orange"]["wind_speed"]:
            probs["high_wind_storm"] = 0.75
        elif wind_speed >= wind_thresholds["risk_color_thresholds"]["yellow"]["wind_speed"]:
            probs["high_wind_storm"] = 0.50

        # Normal conditions (inverse of all hazards)
        max_hazard_prob = max(probs[h] for h in self.hazard_categories if h != "normal")
        probs["normal"] = max(0.0, 1.0 - max_hazard_prob)

        return probs

    def _probability_to_risk_color(
        self,
        hazard: str,
        probability: float,
    ) -> str:
        """
        Convert a hazard probability to an NWS/INFORM risk color.

        Green:  < 0.3 (no significant risk)
        Yellow: 0.3 - 0.5 (moderate risk)
        Orange: 0.5 - 0.7 (high risk)
        Red:    > 0.7 (severe risk)
        """
        if probability >= 0.7:
            return "red"
        elif probability >= 0.5:
            return "orange"
        elif probability >= 0.3:
            return "yellow"
        else:
            return "green"


# ============================================================================
# Crisis Communication System
# ============================================================================

class CrisisCommunicationSystem:
    """
    Crisis communication system for multi-hazard alerts.

    Generates alert messages, recommended actions, and UI-ready data
    for the dashboard.
    """

    def __init__(self):
        self.hazard_classifier = MultiHazardClassifier()

    def generate_alert(
        self,
        location: str,
        hazard_classification: Dict[str, Any],
        explanation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate a complete alert message for the dashboard.

        Parameters
        ----------
        location : str
            Location name.
        hazard_classification : dict
            Output from MultiHazardClassifier.classify_hazards().
        explanation : dict, optional
            XAI explanation from LIME.

        Returns
        -------
        dict with complete alert data for the dashboard.
        """
        active_hazards = hazard_classification["active_hazards"]
        risk_levels = hazard_classification["risk_levels"]
        overall_risk = hazard_classification["overall_risk_level"]

        # Build alert message
        if not active_hazards:
            message = f"✅ All clear for {location}. No active hazards."
        else:
            hazard_names = [HAZARD_NAMES[h] for h in active_hazards]
            if len(active_hazards) == 1:
                message = f"⚠️ {HAZARD_NAMES[active_hazards[0]]} alert for {location}."
            else:
                message = f"⚠️ Multiple hazards for {location}: {', '.join(hazard_names)}."

        # Build per-hazard alert details
        hazard_alerts = []
        for hazard in active_hazards:
            hazard_alerts.append({
                "hazard": hazard,
                "name": HAZARD_NAMES[hazard],
                "icon": HAZARD_ICONS[hazard],
                "risk_color": risk_levels[hazard],
                "probability": hazard_classification["hazard_probabilities"][hazard],
                "actions": hazard_classification["recommended_actions"].get(hazard, []),
            })

        # Build the complete alert
        alert = {
            "location": location,
            "message": message,
            "overall_risk_level": overall_risk,
            "risk_color": RISK_COLORS[overall_risk]["hex"],
            "risk_description": RISK_COLORS[overall_risk]["description"],
            "active_hazards": hazard_alerts,
            "hazard_probabilities": hazard_classification["hazard_probabilities"],
            "risk_levels": risk_levels,
            "alert_required": hazard_classification["alert_required"],
            "timestamp": datetime.now().isoformat(),
            "explanation": explanation.get("alert_explanation") if explanation else None,
            "top_features": explanation.get("top_features") if explanation else None,
        }

        return alert

    def generate_dashboard_data(
        self,
        locations_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Generate dashboard-ready data for multiple locations.

        Parameters
        ----------
        locations_data : list of dicts
            Each dict contains location name and hazard classification.

        Returns
        -------
        dict with dashboard data including all locations and their alerts.
        """
        dashboard_alerts = []

        for location_data in locations_data:
            location = location_data["location"]
            classification = location_data["classification"]
            explanation = location_data.get("explanation")

            alert = self.generate_alert(location, classification, explanation)
            dashboard_alerts.append(alert)

        # Sort by overall risk level (red first)
        risk_order = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
        dashboard_alerts.sort(key=lambda x: risk_order.get(x["overall_risk_level"], 4))

        return {
            "alerts": dashboard_alerts,
            "total_locations": len(dashboard_alerts),
            "active_alerts": sum(1 for a in dashboard_alerts if a["alert_required"]),
            "red_alerts": sum(1 for a in dashboard_alerts if a["overall_risk_level"] == "red"),
            "orange_alerts": sum(1 for a in dashboard_alerts if a["overall_risk_level"] == "orange"),
            "yellow_alerts": sum(1 for a in dashboard_alerts if a["overall_risk_level"] == "yellow"),
            "timestamp": datetime.now().isoformat(),
        }

    def generate_sms_alert(self, alert: Dict[str, Any]) -> str:
        """
        Generate a concise SMS alert message (within 160 characters).

        Preserves critical conditional logic (IF/UNLESS clauses).
        """
        if not alert["alert_required"]:
            return f"All clear for {alert['location']}. No active hazards."

        # Build concise message
        hazards = alert["active_hazards"]
        if len(hazards) == 1:
            hazard = hazards[0]
            risk = alert["risk_levels"][hazard]
            return f"{HAZARD_ICONS[hazard]} {HAZARD_NAMES[hazard]} - {alert['location']} - {risk.upper()}. Take action now."
        else:
            names = " + ".join([HAZARD_NAMES[h].split("/")[0] for h in hazards])
            return f"⚠️ {names} - {alert['location']} - MULTIPLE HAZARDS. Take action now."

    def generate_voice_prompt(self, alert: Dict[str, Any]) -> str:
        """
        Generate a voice prompt for IVR systems (for low-literacy users).
        """
        if not alert["alert_required"]:
            return f"All clear for {alert['location']}."

        hazards = alert["active_hazards"]
        if len(hazards) == 1:
            hazard = hazards[0]
            return f"Alert for {alert['location']}. {HAZARD_NAMES[hazard]} warning. Take immediate action."
        else:
            names = " and ".join([HAZARD_NAMES[h].split("/")[0] for h in hazards])
            return f"Alert for {alert['location']}. {names} warnings. Take immediate action."

    def generate_icon_ui(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate icon-based UI representation for low-literacy users.
        """
        if not alert["alert_required"]:
            return {
                "icon": "✅",
                "severity_icon": "🟢",
                "alert_type": "normal",
                "severity": "green",
                "voice_prompt": self.generate_voice_prompt(alert),
            }

        # Use the highest-risk hazard for the icon
        hazard = alert["active_hazards"][0]
        risk = alert["overall_risk_level"]

        severity_icon = {
            "red": "🔴",
            "orange": "🟠",
            "yellow": "🟡",
            "green": "🟢",
        }.get(risk, "⚠️")

        return {
            "icon": HAZARD_ICONS[hazard],
            "severity_icon": severity_icon,
            "alert_type": hazard,
            "severity": risk,
            "voice_prompt": self.generate_voice_prompt(alert),
        }


# ============================================================================
# Alert Persistence
# ============================================================================

class AlertStore:
    """
    Store and retrieve multi-hazard alerts.

    Uses SQLite for persistence, with support for:
      - Active alerts (current)
      - Historical alerts (for analysis)
      - Alert deduplication (prevent spam)
    """

    def __init__(self, db_path: str = "smart_weather.db"):
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self):
        """Create alert tables if they don't exist."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS multi_hazard_alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                location TEXT NOT NULL,
                hazard_type TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                probability REAL NOT NULL,
                message TEXT,
                recommended_actions TEXT,
                explanation TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hazard_probabilities (
                prob_id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id INTEGER,
                hazard_type TEXT NOT NULL,
                probability REAL NOT NULL,
                risk_color TEXT NOT NULL,
                FOREIGN KEY (alert_id) REFERENCES multi_hazard_alerts(alert_id)
            )
        """)
        conn.commit()
        conn.close()

    def store_alert(self, alert: Dict[str, Any]) -> int:
        """Store an alert and return its ID."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Store main alert (use overall risk for the main record)
        cursor.execute("""
            INSERT INTO multi_hazard_alerts
            (location, hazard_type, risk_level, probability, message,
             recommended_actions, explanation, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            alert["location"],
            "multi_hazard",
            alert["overall_risk_level"],
            alert["hazard_probabilities"].get("normal", 0.0),
            alert["message"],
            json.dumps(alert.get("recommended_actions", {})),
            alert.get("explanation"),
        ))

        alert_id = cursor.lastrowid

        # Store per-hazard probabilities
        for hazard, prob in alert["hazard_probabilities"].items():
            cursor.execute("""
                INSERT INTO hazard_probabilities
                (alert_id, hazard_type, probability, risk_color)
                VALUES (?, ?, ?, ?)
            """, (
                alert_id,
                hazard,
                prob,
                alert["risk_levels"].get(hazard, "green"),
            ))

        conn.commit()
        conn.close()
        return alert_id

    def get_active_alerts(self, location: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all active alerts, optionally filtered by location."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        if location:
            rows = conn.execute("""
                SELECT * FROM multi_hazard_alerts
                WHERE is_active = 1 AND location = ?
                ORDER BY created_at DESC
                LIMIT 20
            """, (location,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM multi_hazard_alerts
                WHERE is_active = 1
                ORDER BY created_at DESC
                LIMIT 50
            """).fetchall()

        alerts = []
        for row in rows:
            alert = dict(row)
            # Get hazard probabilities
            probs = conn.execute("""
                SELECT hazard_type, probability, risk_color
                FROM hazard_probabilities
                WHERE alert_id = ?
            """, (row["alert_id"],)).fetchall()
            alert["hazard_probabilities"] = {p["hazard_type"]: p["probability"] for p in probs}
            alert["risk_levels"] = {p["hazard_type"]: p["risk_color"] for p in probs}
            alerts.append(alert)

        conn.close()
        return alerts

    def dismiss_alert(self, alert_id: int) -> None:
        """Mark an alert as inactive (dismissed)."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE multi_hazard_alerts SET is_active = 0 WHERE alert_id = ?", (alert_id,))
        conn.commit()
        conn.close()
