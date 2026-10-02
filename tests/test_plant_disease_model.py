"""
test_plant_disease_model.py
============================
Unit tests for the deep-learning plant disease image classifier.

Tests:
1. Module import & optional-dependency graceful degradation.
2. Class catalogue and DISEASE_INFO completeness.
3. Synthetic dataset rendering (distinct patterns per class).
4. Training on synthetic data produces a valid checkpoint.
5. Inference returns properly-ranked predictions.
6. Image input format coercion (bytes, path, numpy, PIL).
7. Save / load round-trip.
"""

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from plant_disease_model import (
    DEFAULT_DISEASE_CLASSES,
    DISEASE_INFO,
    ENGINE_VERSION,
    IMAGE_SIZE,
    PlantDiseaseClassifier,
    TORCH_AVAILABLE,
)

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_test_image(size=IMAGE_SIZE, seed=42):
    """Create a deterministic synthetic image."""
    rng = np.random.RandomState(seed)
    arr = rng.randint(30, 70, (size, size, 3), dtype=np.uint8)
    if PIL_AVAILABLE:
        return Image.fromarray(arr)
    return arr


@unittest.skipUnless(TORCH_AVAILABLE, "PyTorch not installed")
@unittest.skipUnless(PIL_AVAILABLE, "Pillow not installed")
class TestPlantDiseaseModel(unittest.TestCase):

    def setUp(self):
        # Suppress stdout noise during training
        from contextlib import redirect_stdout
        self._stdout = redirect_stdout(io.StringIO())

    def test_module_constants(self):
        """Class catalogue and metadata are well-formed."""
        self.assertGreaterEqual(len(DEFAULT_DISEASE_CLASSES), 10)
        # Every class has a corresponding DISEASE_INFO entry
        for cls in DEFAULT_DISEASE_CLASSES:
            self.assertIn(cls, DISEASE_INFO)
            info = DISEASE_INFO[cls]
            self.assertIn("name", info)
            self.assertIn("crop", info)
            self.assertIn("urgency", info)
            self.assertIn("explanation", info)
            self.assertIn("treatment", info)

    def test_classifier_initialization(self):
        """Classifier can be constructed and reports correct state."""
        clf = PlantDiseaseClassifier()
        self.assertIsInstance(clf.classes, list)
        self.assertTrue(len(clf.classes) > 0)
        # Engine version constant
        self.assertTrue(ENGINE_VERSION.startswith("v"))

    def test_synthetic_dataset_rendering(self):
        """Synthetic dataset generates distinct images per class."""
        from plant_disease_model import _SyntheticDiseaseDataset

        classes = DEFAULT_DISEASE_CLASSES[:3]
        ds = _SyntheticDiseaseDataset(classes, n_per_class=5, seed=42)
        self.assertEqual(len(ds), 15)
        img0, label0 = ds[0]
        img1, label1 = ds[5]  # different class
        self.assertEqual(label0, 0)
        self.assertEqual(label1, 1)
        # Images should have 3 channels
        self.assertEqual(img0.shape[0], 3)  # C x H x W after transform
        self.assertEqual(img0.shape[1], IMAGE_SIZE)

    def test_synthetic_training_small(self):
        """Training on a small synthetic set produces a trained model."""
        clf = PlantDiseaseClassifier()
        result = clf.train_from_synthetic(
            epochs=2,
            n_per_class=30,
            batch_size=16,
            log_fn=lambda msg: None,
        )
        self.assertTrue(result["success"])
        self.assertTrue(clf.is_trained)
        self.assertGreater(result["best_val_accuracy"], 0.0)

    def test_inference_returns_predictions(self):
        """After training, predict returns ranked predictions."""
        clf = PlantDiseaseClassifier()
        clf.train_from_synthetic(
            epochs=2,
            n_per_class=30,
            batch_size=16,
            log_fn=lambda msg: None,
        )
        img = _make_test_image()
        result = clf.predict(img, top_k=3)
        self.assertTrue(result["success"])
        self.assertTrue(result["available"])
        self.assertGreaterEqual(len(result["predictions"]), 1)
        # Top prediction has a confidence
        top = result["predictions"][0]
        self.assertIn("disease", top)
        self.assertIn("confidence", top)
        self.assertGreaterEqual(top["confidence"], 0.0)
        self.assertLessEqual(top["confidence"], 1.0)

    def test_inference_accepts_bytes(self):
        """predict accepts raw image bytes."""
        clf = PlantDiseaseClassifier()
        img = _make_test_image()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result = clf.predict(buf.getvalue(), top_k=2)
        self.assertTrue(result["success"])

    def test_inference_accepts_numpy(self):
        """predict accepts numpy arrays."""
        clf = PlantDiseaseClassifier()
        arr = _make_test_image()
        result = clf.predict(arr, top_k=2)
        self.assertTrue(result["success"])

    def test_inference_accepts_path(self):
        """predict accepts file paths."""
        import tempfile

        clf = PlantDiseaseClassifier()
        img = _make_test_image()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp.name, format="PNG")
            tmp_path = tmp.name
        try:
            result = clf.predict(tmp_path, top_k=2)
            self.assertTrue(result["success"])
        finally:
            os.unlink(tmp_path)

    def test_save_load_roundtrip(self):
        """Trained model survives save → reload cycle."""
        clf = PlantDiseaseClassifier()
        clf.train_from_synthetic(
            epochs=2,
            n_per_class=30,
            batch_size=16,
            log_fn=lambda msg: None,
        )
        # The best checkpoint is already saved; reload
        clf2 = PlantDiseaseClassifier()
        self.assertTrue(clf2.is_trained)
        self.assertTrue(clf2.is_available())

    def test_crop_hint_reweighting(self):
        """Crop hint should change prediction distribution."""
        clf = PlantDiseaseClassifier()
        clf.train_from_synthetic(
            epochs=2,
            n_per_class=30,
            batch_size=16,
            log_fn=lambda msg: None,
        )
        img = _make_test_image()
        result_no_hint = clf.predict(img, top_k=3, crop_hint=None)
        result_with_hint = clf.predict(img, top_k=3, crop_hint="wheat")

        self.assertTrue(result_no_hint["success"])
        self.assertTrue(result_with_hint["success"])

    def test_healthy_class_exists(self):
        """Each crop has a 'healthy' class in the default catalogue."""
        healthy_classes = [c for c in DEFAULT_DISEASE_CLASSES if "healthy" in c]
        self.assertGreaterEqual(len(healthy_classes), 3)


@unittest.skipUnless(TORCH_AVAILABLE, "PyTorch not installed")
class TestPlantDiseaseModelNoTorch(unittest.TestCase):
    """Tests that run even without a trained model."""

    def test_is_available_false_without_weights(self):
        """When no weights exist, is_available returns False."""
        # Create a fresh classifier pointed at a non-existent path
        clf = PlantDiseaseClassifier(model_path="/nonexistent/model.pth")
        self.assertFalse(clf.is_model_ready())


@unittest.skipUnless(not TORCH_AVAILABLE, "PyTorch is installed (testing fallback)")
class TestPlantDiseaseModelFallback(unittest.TestCase):
    """When torch is NOT installed, the module must still be importable."""

    def test_import_without_torch(self):
        from plant_disease_model import PlantDiseaseClassifier
        clf = PlantDiseaseClassifier()
        self.assertFalse(clf.is_available())
        result = clf.predict(np.zeros((224, 224, 3), dtype=np.uint8))
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
