"""
crop_database.py
================
Static, offline-safe crop database shipping with AgriAdvisor app.
No network calls required.

Diseases listed here mirror the 13 class labels of the trained ResNet-18 CNN
in models/plant_disease_model.pth so that the rule-based tier and the
deep-learning tier share a consistent disease vocabulary.
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
            },
            "wheat_healthy": {
                "name": "Wheat — Healthy",
                "symptoms": [],
                "urgency": "low",
                "disclaimer": "No disease detected. Continue routine monitoring.",
                "explanation": "Plants show no visible disease symptoms. Maintain standard agronomic practices."
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
            },
            "rice_brown_spot": {
                "name": "Rice Brown Spot",
                "symptoms": ["brown_spots", "leaf_lesions", "withered_tips"],
                "urgency": "high",
                "disclaimer": "Ensure adequate silicon and nitrogen management; avoid water stress.",
                "explanation": "Brown, oval-shaped spots on leaves indicate Helminthosporium oryzae infection."
            },
            "rice_healthy": {
                "name": "Rice — Healthy",
                "symptoms": [],
                "urgency": "low",
                "disclaimer": "No disease detected. Continue routine monitoring.",
                "explanation": "Plants show no visible disease symptoms. Maintain standard agronomic practices."
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
            },
            "fusarium_wilt": {
                "name": "Cotton Fusarium Wilt",
                "symptoms": ["yellow_waving_margins", "withered_tips", "stunted_growth"],
                "urgency": "high",
                "disclaimer": "Use Fusarium-resistant varieties and ensure proper soil drainage.",
                "explanation": "Yellowing and wilting of lower leaves indicate Fusarium oxysporum f. sp. vasinfectum infection."
            },
            "cotton_healthy": {
                "name": "Cotton — Healthy",
                "symptoms": [],
                "urgency": "low",
                "disclaimer": "No disease detected. Continue routine monitoring.",
                "explanation": "Plants show no visible disease symptoms. Maintain standard agronomic practices."
            }
        },
        "optimal_conditions": {
            "temp_min": 20.0,
            "temp_max": 38.0,
            "rainfall_min": 500,
            "rainfall_max": 1000
        }
    },
    "maize": {
        "name": "Maize",
        "stages": ["germination", "vegetative", "tasseling", "silking", "grain_fill", "maturation"],
        "diseases": {
            "maize_common_rust": {
                "name": "Maize Common Rust",
                "symptoms": ["yellow_pustules", "leaf_lesions", "powdery_spots"],
                "urgency": "high",
                "disclaimer": "Use resistant hybrids and timely fungicide application if disease pressure is high.",
                "explanation": "Orange/yellow pustules on leaves, primarily on lower leaves, indicate Puccinia sorghi infection."
            },
            "maize_healthy": {
                "name": "Maize — Healthy",
                "symptoms": [],
                "urgency": "low",
                "disclaimer": "No disease detected. Continue routine monitoring.",
                "explanation": "Plants show no visible disease symptoms. Maintain standard agronomic practices."
            }
        },
        "optimal_conditions": {
            "temp_min": 18.0,
            "temp_max": 30.0,
            "rainfall_min": 500,
            "rainfall_max": 1000
        }
    }
}

def get_crop_data(crop_name: str) -> Dict[str, Any]:
    return CROP_DATABASE.get(crop_name.lower(), CROP_DATABASE["wheat"])
