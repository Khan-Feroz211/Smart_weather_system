"""
test_agri_advisor_v6.py
========================
Comprehensive unit & integration tests for AgriAdvisor Recommendation Engine (v6).
Tests:
1. Pure offline disease detection (rule-based tier).
2. Geovis satellite analyzer degradation across ONLINE, CACHE, and OFFLINE.
3. Yield estimator & Crop suitability connectivity degradation.
4. RecommendationEngine output formatting and status header precision.
5. Flask API routes integration.
"""

import unittest
import json
from crop_database import get_crop_data
from entropy_utils import compute_entropy, suggest_next_question
from confidence_calibration import ConfidenceCalibrator
from disease_detection import DiseaseDetector
from geovis_satellite import GeoVisSatelliteAnalyzer
from yield_estimator import YieldEstimator
from crop_suitability import rank_crops
from recommendation_engine import RecommendationEngine
from app_clean import app

class TestAgriAdvisorV6(unittest.TestCase):

    def setUp(self):
        self.engine = RecommendationEngine()
        self.app_client = app.test_client()

    def test_crop_database_offline(self):
        wheat_data = get_crop_data("wheat")
        self.assertIsNotNone(wheat_data)
        self.assertIn("wheat_rust", wheat_data["diseases"])

    def test_entropy_computation(self):
        probs = [0.5, 0.5]
        entropy_res = compute_entropy(probs)
        self.assertGreater(entropy_res["raw_entropy_nats"], 0)
        self.assertEqual(entropy_res["n_candidates"], 2)

    def test_disease_detection_offline_safe(self):
        detector = DiseaseDetector(sample_count=5)
        symptoms = {"yellow_pustules": True, "leaf_lesions": True}
        results = detector.diagnose_with_uncertainty("wheat", symptoms)

        self.assertTrue(len(results) > 0)
        diag = results[0]
        self.assertEqual(diag["disease"], "Wheat Rust")
        self.assertEqual(diag["data_source"], "rule_based_tier_local")
        self.assertIn("calibration_method", diag)
        self.assertIn("uncertainty_entropy", diag)
        self.assertIn("suggested_next_question", diag)

    def test_geovis_satellite_degradation(self):
        analyzer = GeoVisSatelliteAnalyzer()

        # ONLINE
        online_res = analyzer.analyze_site({"temp": 25.0}, connectivity_state="ONLINE")
        self.assertTrue(online_res["available"])
        self.assertEqual(online_res["status"], "ONLINE")
        self.assertIn("ndvi", online_res["data"])

        # CACHE
        cache_res = analyzer.analyze_site({"temp": 25.0}, connectivity_state="CACHE")
        self.assertTrue(cache_res["available"])
        self.assertEqual(cache_res["status"], "CACHE")
        self.assertTrue(cache_res["is_stale"])

        # OFFLINE
        offline_res = analyzer.analyze_site({"temp": 25.0}, connectivity_state="OFFLINE")
        self.assertFalse(offline_res["available"])
        self.assertEqual(offline_res["status"], "OFFLINE")
        self.assertIn("satellite data not available offline", offline_res["message"])

    def test_yield_estimator_and_suitability_degradation(self):
        yield_est = YieldEstimator()

        # ONLINE
        y_online = yield_est.estimate("wheat", {}, "heading", [], connectivity_state="ONLINE")
        self.assertTrue(y_online["available"])

        # OFFLINE
        y_offline = yield_est.estimate("wheat", {}, "heading", [], connectivity_state="OFFLINE")
        self.assertFalse(y_offline["available"])
        self.assertIn("weather-independent estimate not available offline", y_offline["message"])

        # Suitability OFFLINE
        s_offline = rank_crops({}, connectivity_state="OFFLINE")
        self.assertFalse(s_offline["available"])

    def test_recommendation_engine_status_headers(self):
        # ONLINE
        res_online = self.engine.analyze(connectivity_state="ONLINE")
        self.assertTrue(res_online["status_header"].startswith("[SIMULATED DATA | ONLINE]"))
        self.assertTrue(res_online["geovis_satellite"]["available"])

        # CACHE
        res_cache = self.engine.analyze(connectivity_state="CACHE", data_age_hours=3.2)
        self.assertIn("CACHE, 3.2h stale", res_cache["status_header"])

        # OFFLINE
        res_offline = self.engine.analyze(connectivity_state="OFFLINE")
        self.assertIn("OFFLINE — weather-dependent sections withheld", res_offline["status_header"])
        self.assertFalse(res_offline["geovis_satellite"]["available"])
        self.assertFalse(res_offline["yield_estimate"]["available"])

    def test_api_routes(self):
        response = self.app_client.post(
            "/api/agri/analyze",
            data=json.dumps({"crop": "wheat", "connectivity_state": "ONLINE"}),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("status_header", data)
        self.assertIn("diagnoses", data)
        self.assertIn("geovis_satellite", data)

if __name__ == "__main__":
    unittest.main()
