"""
crop_database.py
================
Static, offline-safe crop database shipping with AgriAdvisor app.
No network calls required.
"""

from typing import Dict, Any

CROP_DATABASE: Dict[str, Dict[str, Any]] = {
    "wheat": {
        "name": "Wheat",
        "stages": ["germination", "tillering", "jointing", "heading", "ripening"],
        "diseases": {
            "wheat_rust": {
                "name": "Wheat Rust",
                "symptoms": ["yellow_pustules", "leaf_lesions", "powdery_spots"],
                "urgency": "critical",
                "disclaimer": "CRITICAL URGENCY: Immediate fungicide application recommended. Note: No human-escalation queue exists yet (Gap 4a).",
                "explanation": "Yellow pustules on leaves indicate stripe/leaf rust (Puccinia striiformis / recondita)."
            },
            "leaf_blight": {
                "name": "Wheat Leaf Blight",
                "symptoms": ["leaf_lesions", "brown_spots"],
                "urgency": "medium",
                "disclaimer": "Monitor field closely and prepare foliar spray if humidity remains high.",
                "explanation": "Brown leaf lesions indicate fungal leaf blight."
            },
            "powdery_mildew": {
                "name": "Powdery Mildew",
                "symptoms": ["powdery_spots", "white_coating"],
                "urgency": "medium",
                "disclaimer": "Ensure proper field spacing and airflow.",
                "explanation": "White powdery patches indicate Erysiphe graminis infestation."
            }
        },
        "optimal_conditions": {
            "temp_min": 10.0,
            "temp_max": 25.0,
            "rainfall_min": 300,
            "rainfall_max": 800
        }
    },
    "rice": {
        "name": "Rice",
        "stages": ["nursery", "tillering", "panicle_initiation", "flowering", "maturation"],
        "diseases": {
            "rice_blast": {
                "name": "Rice Blast",
                "symptoms": ["diamond_spots", "leaf_lesions", "withered_tips"],
                "urgency": "critical",
                "disclaimer": "CRITICAL URGENCY: High potential for yield loss. No human-escalation queue exists yet (Gap 4a).",
                "explanation": "Diamond-shaped leaf lesions indicate Magnaporthe oryzae infection."
            },
            "bacterial_blight": {
                "name": "Bacterial Blight",
                "symptoms": ["yellow_waving_margins", "withered_tips"],
                "urgency": "high",
                "disclaimer": "Avoid excess nitrogen fertilization.",
                "explanation": "Water-soaked lesions turning yellow on leaf margins indicate Xanthomonas oryzae."
            }
        },
        "optimal_conditions": {
            "temp_min": 20.0,
            "temp_max": 35.0,
            "rainfall_min": 1000,
            "rainfall_max": 2000
        }
    },
    "cotton": {
        "name": "Cotton",
        "stages": ["seedling", "vegetative", "squaring", "boll_formation", "maturation"],
        "diseases": {
            "leaf_curl": {
                "name": "Cotton Leaf Curl Virus (CLCuV)",
                "symptoms": ["upward_curling", "thickened_veins", "stunted_growth"],
                "urgency": "critical",
                "disclaimer": "CRITICAL URGENCY: Vector control required. No human-escalation queue exists yet (Gap 4a).",
                "explanation": "Upward leaf curling and enations indicate CLCuV spread by whiteflies."
            }
        },
        "optimal_conditions": {
            "temp_min": 20.0,
            "temp_max": 38.0,
            "rainfall_min": 500,
            "rainfall_max": 1000
        }
    }
}

def get_crop_data(crop_name: str) -> Dict[str, Any]:
    return CROP_DATABASE.get(crop_name.lower(), CROP_DATABASE["wheat"])
