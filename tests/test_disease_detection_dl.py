"""
test_disease_detection_dl.py
=============================
Integration tests for the deep-learning disease detection pipeline.

Tests:
1. DiseaseDetector.diagnose_from_image returns DL predictions.
2. DiseaseDetector.diagnose_hybrid fuses rule + DL diagnostics.
3. DiseaseDetector gracefully degrades when DL is not available.
4. RecommendationEngine.analyze accepts image_input and returns fused results.
5. RecommendationEngine.diagnose_from_image convenience method.
6. API endpoint /api/agri/disease-diagnose accepts multipart image uploads.
7. API endpoint /api/agri/analyze accepts multipart image uploads.
8. /api/agri/status reports deep_learning backend info.
"""

import io
import json
import unittest
from pathlib import Path

import numpy as np

from disease_detection import DiseaseDetector, ENGINE_VERSION
from recommendation_engine import RecommendationEngine

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

try:
    from plant_disease_model import PlantDiseaseClassifier, TORCH_AVAILABLE
except Exception:
    TORCH_AVAILABLE = False
    PlantDiseaseClassifier = None


def _make_test_image(size=224, seed=42):
    rng = np.random.RandomState(seed)
    arr = rng.randint(30, 70, (size, size, 3), dtype=np.uint8)
    if PIL_AVAILABLE:
        return Image.fromarray(arr)
    return arr


def _make_test_image_bytes(size=224, seed=42):
    """Return raw PNG image bytes."""
    img = _make_test_image(size=size, seed=seed)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@unittest.skipUnless(TORCH_AVAILABLE and PlantDiseaseClassifier and PlantDiseaseClassifier.is_available(),
                     "DL model not trained — run: python train_disease_model.py --synthetic")
class TestDiseaseDetectionDL(unittest.TestCase):

    def setUp(self):
        self.detector = DiseaseDetector(sample_count=5)
        self.engine = RecommendationEngine()

    def test_diagnose_from_image_returns_predictions(self):
        """diagnose_from_image returns ranked DL predictions."""
        img = _make_test_image(seed=1)
        results = self.detector.diagnose_from_image(img, crop="wheat", top_k=3)

        self.assertGreater(len(results), 0)
        top = results[0]
        self.assertIn("disease", top)
        self.assertIn("confidence_calibrated", top)
        self.assertIn("data_source", top)
        self.assertEqual(top["data_source"], "deep_learning_cnn_resnet18")
        self.assertIn("image_analysis", top)
        self.assertTrue(top["image_analysis"]["available"])
        self.assertIn("treatment", top)

    def test_diagnose_from_image_with_bytes(self):
        """diagnose_from_image accepts raw bytes."""
        img_bytes = _make_test_image_bytes(seed=5)
        results = self.detector.diagnose_from_image(img_bytes, crop="rice", top_k=2)
        self.assertGreater(len(results), 0)

    def test_diagnose_hybrid_fuses_sources(self):
        """diagnose_hybrid returns results with multiple data sources."""
        img = _make_test_image(seed=3)
        results = self.detector.diagnose_hybrid(
            "wheat",
            {"yellow_pustules": True, "leaf_lesions": True},
            img,
            top_k=3,
        )
        self.assertGreater(len(results), 0)
        # At least one result should have image_analysis
        has_image = any(r.get("image_analysis", {}).get("available") for r in results)
        self.assertTrue(has_image)

    def test_engine_analyze_with_image(self):
        """RecommendationEngine.analyze accepts image_input."""
        img = _make_test_image(seed=7)
        result = self.engine.analyze(
            crop="wheat",
            symptoms={"yellow_pustules": True, "leaf_lesions": True},
            connectivity_state="ONLINE",
            image_input=img,
            top_k=3,
        )

        self.assertIn("diagnoses", result)
        self.assertIn("image_analysis", result)
        self.assertTrue(result["image_analysis"]["available"])
        self.assertEqual(result["engine_version"], ENGINE_VERSION)
        self.assertGreater(len(result["diagnoses"]), 0)

    def test_engine_analyze_without_image_still_works(self):
        """Without image_input, engine falls back to rule-based only."""
        result = self.engine.analyze(
            crop="wheat",
            connectivity_state="ONLINE",
        )
        self.assertFalse(result["image_analysis"]["available"])
        self.assertGreater(len(result["diagnoses"]), 0)

    def test_engine_offline_with_image_still_works(self):
        """DL image analysis works even in OFFLINE mode (local CNN)."""
        img = _make_test_image(seed=9)
        result = self.engine.analyze(
            crop="wheat",
            connectivity_state="OFFLINE",
            image_input=img,
        )
        self.assertTrue(result["image_analysis"]["available"])
        self.assertIn("OFFLINE", result["status_header"])

    def test_engine_diagnose_from_image(self):
        """Engine's convenience method returns diagnosis for an image."""
        img = _make_test_image(seed=11)
        result = self.engine.diagnose_from_image(img, crop="cotton", top_k=3)
        self.assertIn("diagnoses", result)
        self.assertIn("image_analysis", result)

    def test_engine_version_bumped(self):
        """Engine version reflects the hybrid DL integration."""
        result = self.engine.analyze(crop="wheat")
        self.assertIn("v6.2", result["engine_version"])


@unittest.skipUnless(TORCH_AVAILABLE and PlantDiseaseClassifier and PlantDiseaseClassifier.is_available(),
                     "DL model not trained")
class TestDiseaseDetectionAPI(unittest.TestCase):

    def setUp(self):
        from app_clean import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_disease_diagnose_image_upload(self):
        """POST /api/agri/disease-diagnose with multipart image."""
        img_bytes = _make_test_image_bytes(seed=21)
        resp = self.client.post(
            "/api/agri/disease-diagnose",
            data={"image": (io.BytesIO(img_bytes), "test.png"), "crop": "wheat", "top_k": 3},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success") or data.get("image_analysis", {}).get("available"))

    def test_disease_diagnose_json_base64(self):
        """POST /api/agri/disease-diagnose with base64 image in JSON."""
        import base64
        img_bytes = _make_test_image_bytes(seed=22)
        b64 = base64.b64encode(img_bytes).decode("ascii")
        resp = self.client.post(
            "/api/agri/disease-diagnose",
            data=json.dumps({"image": b64, "crop": "rice", "top_k": 3}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success") or data.get("image_analysis", {}).get("available"))

    def test_disease_diagnose_missing_image(self):
        """POST /api/agri/disease-diagnose without image returns 400."""
        resp = self.client.post(
            "/api/agri/disease-diagnose",
            data={"crop": "wheat"},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)

    def test_analyze_with_multipart_image(self):
        """POST /api/agri/analyze with multipart image upload."""
        img_bytes = _make_test_image_bytes(seed=31)
        resp = self.client.post(
            "/api/agri/analyze",
            data={
                "image": (io.BytesIO(img_bytes), "leaf.png"),
                "crop": "wheat",
                "top_k": 3,
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("diagnoses", data)
        self.assertTrue(data.get("image_analysis", {}).get("available"))

    def test_status_reports_dl(self):
        """GET /api/agri/status includes deep_learning section."""
        resp = self.client.get("/api/agri/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("deep_learning", data)
        self.assertIn("available", data["deep_learning"])


if __name__ == "__main__":
    unittest.main()
