"""
plant_disease_model.py
======================
Deep-learning plant-disease image classifier built on PyTorch with a
ResNet-18 transfer-learning backbone (ImageNet pre-trained).

Design goals
------------
1. **Offline-first**: The module degrades gracefully when PyTorch is not
   installed or when no trained weights exist yet.  ``DiseaseImageClassifier``
   is importable in *every* environment — even on the production server that
   only runs the rule-based tier.  Callers can use ``is_available()`` to
   branch.

2. **Training**: ``train_from_directory`` and ``train_from_synthetic`` accept
   either a standard PlantVillage-style directory layout
   (``data/train/<crop>_<disease>/...``) or programmatically generated
   synthetic images so the model can always be bootstrapped.

3. **Inference**: ``predict`` accepts a PIL Image or a numpy array, runs
   preprocessing, and returns ranked disease predictions with confidence
   scores and uncertainty entropy.

4. **Integration**: Exposes a simple ``PlantDiseaseClassifier`` wrapper whose
   output contract matches the existing ``DiseaseDetector.diagnose_with_uncertainty``
   so disease results can be merged seamlessly in the recommendation engine.

Trained artifacts are stored under ``models/`` alongside the existing
joblib models:
    * ``models/plant_disease_model.pth``        – state dict
    * ``models/plant_disease_classes.json``     – class-index mapping
"""

from __future__ import annotations

import io
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Ensure UTF-8 output for emoji/Unicode in logs (important on Windows cp1252)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# --------------------------------------------------------------------------- #
# Optional torch dependency — the whole module must remain importable even if
# torch is not installed so that the rule-based tier and the rest of the app
# keep working in minimal/offline environments.
# --------------------------------------------------------------------------- #
TORCH_AVAILABLE = False
try:
    import torch  # type: ignore
    from torch import nn, optim  # type: ignore
    from torch.utils.data import Dataset, DataLoader  # type: ignore
    import torchvision  # type: ignore
    from torchvision import transforms  # type: ignore
    from torchvision.models import resnet18, ResNet18_Weights  # type: ignore

    TORCH_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when torch is absent
    torch = None  # type: ignore[assignment]
    torchvision = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# Optional Pillow dependency (same graceful-degradation philosophy)
# --------------------------------------------------------------------------- #
PIL_AVAILABLE = False
try:
    from PIL import Image  # type: ignore
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
MODEL_DIR: Path = Path(__file__).resolve().parent / "models"
MODEL_PATH: Path = MODEL_DIR / "plant_disease_model.pth"
CLASSES_PATH: Path = MODEL_DIR / "plant_disease_classes.json"
CLASS_WEIGHTS_PATH: Path = MODEL_DIR / "plant_disease_class_weights.json"

ENGINE_VERSION = "v1.0-dl"
IMAGE_SIZE = 224  # standard ResNet input size

# --------------------------------------------------------------------------- #
# Default disease class catalogue.
#
# These map directly onto the rule-based crop_database.py disease entries so
# that deep-learning predictions and rule-based predictions can be harmonised.
# If a trained model produces a different class set (loaded from JSON), the
# loaded set takes precedence.
# --------------------------------------------------------------------------- #
DEFAULT_DISEASE_CLASSES: List[str] = [
    # Wheat
    "wheat_rust",
    "wheat_leaf_blight",
    "wheat_powdery_mildew",
    "wheat_healthy",
    # Rice
    "rice_blast",
    "rice_bacterial_blight",
    "rice_brown_spot",
    "rice_healthy",
    # Cotton
    "cotton_leaf_curl_virus",
    "cotton_fusarium_wilt",
    "cotton_healthy",
    # Maize
    "maize_common_rust",
    "maize_healthy",
]

# Inverse mapping: class_id → full disease description used in responses.
DISEASE_INFO: Dict[str, Dict[str, Any]] = {
    "wheat_rust": {
        "name": "Wheat Rust",
        "crop": "wheat",
        "symptoms": ["yellow_pustules", "leaf_lesions", "powdery_spots"],
        "urgency": "critical",
        "explanation": "Yellow/orange pustules on leaves indicate stripe/leaf rust "
                       "(Puccinia striiformis / recondita).",
        "treatment": "Apply a broad-spectrum fungicide (e.g. propiconazole) immediately. "
                     "Remove infected stalks.",
    },
    "wheat_leaf_blight": {
        "name": "Wheat Leaf Blight",
        "crop": "wheat",
        "symptoms": ["leaf_lesions", "brown_spots"],
        "urgency": "medium",
        "explanation": "Brown, water-soaked lesions with yellow halos indicate "
                       "Septoria or Fusarium leaf blight.",
        "treatment": "Foliar fungicide spray; ensure good field drainage.",
    },
    "wheat_powdery_mildew": {
        "name": "Wheat Powdery Mildew",
        "crop": "wheat",
        "symptoms": ["powdery_spots", "white_coating"],
        "urgency": "medium",
        "explanation": "White powdery growth on leaves indicates Erysiphe graminis.",
        "treatment": "Sulphur-based fungicide; improve air circulation.",
    },
    "wheat_healthy": {
        "name": "Wheat – Healthy",
        "crop": "wheat",
        "symptoms": [],
        "urgency": "low",
        "explanation": "No disease symptoms detected. Plant appears healthy.",
        "treatment": "Continue routine monitoring.",
    },
    "rice_blast": {
        "name": "Rice Blast",
        "crop": "rice",
        "symptoms": ["diamond_spots", "leaf_lesions", "withered_tips"],
        "urgency": "critical",
        "explanation": "Diamond-shaped lesions with gray centres indicate "
                       "Magnaporthe oryzae infection.",
        "treatment": "Apply tricyclazole orazole fungicide; avoid excess nitrogen.",
    },
    "rice_bacterial_blight": {
        "name": "Rice Bacterial Blight",
        "crop": "rice",
        "symptoms": ["yellow_waving_margins", "withered_tips"],
        "urgency": "high",
        "explanation": "Water-soaked stripes on leaves turning yellow indicate "
                       "Xanthomonas oryzae.",
        "treatment": "Copper-based bactericide; remove infected plant debris.",
    },
    "rice_brown_spot": {
        "name": "Rice Brown Spot",
        "crop": "rice",
        "symptoms": ["brown_spots", "leaf_lesions"],
        "urgency": "medium",
        "explanation": "Brown necrotic spots with yellow halos indicate "
                       "Bipolaris oryzae.",
        "treatment": "Propiconazole spray; maintain adequate nitrogen and water.",
    },
    "rice_healthy": {
        "name": "Rice – Healthy",
        "crop": "rice",
        "symptoms": [],
        "urgency": "low",
        "explanation": "No disease symptoms detected. Plant appears healthy.",
        "treatment": "Continue routine monitoring.",
    },
    "cotton_leaf_curl_virus": {
        "name": "Cotton Leaf Curl Virus (CLCuV)",
        "crop": "cotton",
        "symptoms": ["upward_curling", "thickened_veins", "stunted_growth"],
        "urgency": "critical",
        "explanation": "Upward leaf curling and vein thickening indicate CLCuV "
                       "spread by whiteflies.",
        "treatment": "Vector control with neem oil or insecticide; resistant "
                     "varieties if available.",
    },
    "cotton_fusarium_wilt": {
        "name": "Cotton Fusarium Wilt",
        "crop": "cotton",
        "symptoms": ["yellow_waving_margins", "stunted_growth"],
        "urgency": "high",
        "explanation": "Yellowing of lower leaves and wilting indicate "
                       "Fusarium oxysporum f.sp. vasinfectum.",
        "treatment": "Soil drench with carbendazim; solarise soil before planting.",
    },
    "cotton_healthy": {
        "name": "Cotton – Healthy",
        "crop": "cotton",
        "symptoms": [],
        "urgency": "low",
        "explanation": "No disease symptoms detected. Plant appears healthy.",
        "treatment": "Continue routine monitoring.",
    },
    "maize_common_rust": {
        "name": "Maize Common Rust",
        "crop": "maize",
        "symptoms": ["yellow_pustules", "powdery_spots"],
        "urgency": "medium",
        "explanation": "Orange-yellow pustules on leaves indicate Puccinia sorghi.",
        "treatment": "Foliar fungicide; hybrid-resistant varieties.",
    },
    "maize_healthy": {
        "name": "Maize – Healthy",
        "crop": "maize",
        "symptoms": [],
        "urgency": "low",
        "explanation": "No disease symptoms detected. Plant appears healthy.",
        "treatment": "Continue routine monitoring.",
    },
}


# --------------------------------------------------------------------------- #
# Image helpers (work with or without Pillow at import time)
# --------------------------------------------------------------------------- #
def _load_image(image_input: Any) -> "Image.Image":
    """Convert various inputs into a PIL Image. Raises if Pillow is missing."""
    if not PIL_AVAILABLE:
        raise ImportError("Pillow (PIL) is required for image processing but is not installed.")
    if isinstance(image_input, Image.Image):  # type: ignore[name-defined]
        return image_input
    if isinstance(image_input, (bytes, bytearray)):
        return Image.open(io.BytesIO(bytes(image_input))).convert("RGB")
    if isinstance(image_input, (str, Path)):
        return Image.open(str(image_input)).convert("RGB")
    # Assume numpy array or torch tensor
    arr = np.array(image_input)
    if arr.dtype != np.uint8:
        arr = (arr * 255).astype(np.uint8) if arr.max() <= 1.0 else arr.astype(np.uint8)
    return Image.fromarray(arr).convert("RGB")


# --------------------------------------------------------------------------- #
# Synthetic dataset generator
# --------------------------------------------------------------------------- #
class _SyntheticDiseaseDataset(Dataset):
    """
    Generates synthetic plant-leaf images on-the-fly with realistic variation
    per image — including lighting noise, leaf texture, and mild inter-class
    overlap — so the model cannot trivially reach 100% accuracy.

    Each class is rendered as a coloured base with a characteristic texture:
      * rust  → orange-brown circular pustules on green
      * blight → brown necrotic lesions
      * mildew → white powdery patches
      * healthy → uniform green leaf
      * blast → diamond-shaped gray lesions
      * curlvirus → curled/leaf-marginal yellowing
      * fusarium → vascular striping

    The generator is deterministic given a seed, making it reproducible for
    tests and small-scale training.
    """

    def __init__(self, classes: List[str], n_per_class: int = 200, seed: int = 42):
        self.classes = classes
        self.n_per_class = n_per_class
        self.seed = seed
        self._total = n_per_class * len(classes)
        # Data augmentation that is cheap and runs on PIL images
        self.transform = transforms.Compose([  # noqa: SIM117
            transforms.RandomHorizontalFlip(p=0.3),
            transforms.RandomRotation(degrees=10),
            transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ])

    def __len__(self) -> int:
        return self._total

    def _render(self, idx: int) -> np.ndarray:
        """Render a single synthetic leaf image for the given global index.

        Uses ``idx`` (not just class) as the random seed so every image is
        unique — this prevents trivial memorisation and produces realistic
        accuracy figures.
        """
        class_idx = idx // self.n_per_class
        rng = np.random.RandomState(self.seed + idx * 7919)  # large prime stride

        # --- Base leaf texture with veins ---
        img = rng.randint(40, 80, (IMAGE_SIZE, IMAGE_SIZE, 3), dtype=np.uint8)
        # Base green
        img[:, :, 0] = rng.randint(20, 60, (IMAGE_SIZE, IMAGE_SIZE))   # R low
        img[:, :, 1] = rng.randint(70, 160, (IMAGE_SIZE, IMAGE_SIZE))   # G high
        img[:, :, 2] = rng.randint(15, 55, (IMAGE_SIZE, IMAGE_SIZE))    # B low

        # --- Leaf venation (subtle midrib + secondary veins) ---
        midrib_x = IMAGE_SIZE // 2 + rng.randint(-10, 10)
        thickness = rng.randint(3, 8)
        img[:, max(0, midrib_x - thickness):midrib_x + thickness] = [
            rng.randint(10, 30),
            rng.randint(60, 100),
            rng.randint(10, 30),
        ]
        # Secondary veins (angled)
        for _ in range(rng.randint(3, 8)):
            start_y = rng.randint(0, IMAGE_SIZE)
            angle = rng.uniform(-0.5, 0.5)
            length = rng.randint(30, 80)
            for step in range(length):
                y = int(start_y + step * angle)
                x = int(midrib_x + (step - length / 2) * 0.15)
                if 0 <= y < IMAGE_SIZE and 0 <= x < IMAGE_SIZE:
                    r = max(1, 3 - step // 20)
                    img[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] = [
                        rng.randint(10, 30), rng.randint(60, 100), rng.randint(10, 30)
                    ]

        cls_name = self.classes[class_idx].lower()

        # --- Class-specific disease patterns ---
        # NOTE: some patterns intentionally overlap between classes to
        # introduce label noise and avoid trivial separation.
        if "rust" in cls_name:
            n_pustules = rng.randint(15, 50)
            for _ in range(n_pustules):
                y, x = rng.randint(0, IMAGE_SIZE, 2)
                r = rng.randint(3, 18)
                color = [rng.randint(100, 200), rng.randint(50, 140), rng.randint(10, 90)]
                _safe_circle(img, y, x, r, color, rng)
            # Occasionally add mildew-like white spots (overlap)
            if rng.random() < 0.15:
                _safe_circle(img, rng.randint(0, IMAGE_SIZE), rng.randint(0, IMAGE_SIZE),
                             rng.randint(5, 20), [200, 200, 200], rng)
        elif "blight" in cls_name or "leaf_blight" in cls_name:
            n_lesions = rng.randint(10, 35)
            for _ in range(n_lesions):
                y, x = rng.randint(0, IMAGE_SIZE, 2)
                r = rng.randint(8, 35)
                color = [rng.randint(70, 160), rng.randint(30, 110), rng.randint(10, 70)]
                _safe_circle(img, y, x, r, color, rng)
        elif "mildew" in cls_name:
            n_patches = rng.randint(25, 80)
            for _ in range(n_patches):
                y, x = rng.randint(0, IMAGE_SIZE, 2)
                r = rng.randint(4, 28)
                color = [190 + rng.randint(0, 65)] * 3
                _safe_circle(img, y, x, r, color, rng)
        elif "blast" in cls_name:
            n_lesions = rng.randint(8, 25)
            for _ in range(n_lesions):
                y, x = rng.randint(0, IMAGE_SIZE, 2)
                r = rng.randint(8, 28)
                color = [rng.randint(100, 190)] * 3
                _safe_diamond(img, y, x, r, color)
        elif "curl" in cls_name or "curlvirus" in cls_name:
            # Yellow wavy margins
            yy, xx = np.ogrid[:IMAGE_SIZE, :IMAGE_SIZE]
            dist = np.minimum(np.minimum(yy, IMAGE_SIZE - yy),
                              np.minimum(xx, IMAGE_SIZE - xx))
            margin_width = rng.randint(15, 45)
            mask = dist < margin_width
            img[mask] = [180 + rng.randint(0, 40), 180 + rng.randint(0, 40), 60 + rng.randint(0, 40)]
            # Vein thickening / yellow streaks
            for _ in range(rng.randint(5, 18)):
                y, x = rng.randint(0, IMAGE_SIZE, 2)
                _safe_circle(img, y, x, rng.randint(6, 18), [150, 150, 70], rng)
        elif "fusarium" in cls_name:
            # Vascular striping
            n_stripes = rng.randint(3, 12)
            for _ in range(n_stripes):
                x = rng.randint(0, IMAGE_SIZE)
                thickness = rng.randint(2, 8)
                yellowish = [rng.randint(160, 210), rng.randint(150, 200), rng.randint(60, 120)]
                img[:, max(0, x - thickness):x + thickness] = yellowish
        # healthy → nothing added, stays green (but with texture/noise)

        # --- Add realistic noise, shadows, and lighting ---
        noise = rng.randint(-15, 15, img.shape, dtype=np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        # Random lighting gradient (darker towards one corner)
        if rng.random() < 0.5:
            yy, xx = np.ogrid[:IMAGE_SIZE, :IMAGE_SIZE]
            shade = rng.uniform(0.7, 1.0)
            gradient = shade + (1.0 - shade) * (yy / IMAGE_SIZE) * (xx / IMAGE_SIZE)
            img = np.clip((img.astype(np.float32) * gradient[:, :, None]), 0, 255).astype(np.uint8)

        # Random blur (simulate camera shake / distance)
        if rng.random() < 0.3:
            img = _gaussian_blur(img, max(1, int(rng.uniform(1, 3))))

        return img

    def __getitem__(self, idx: int) -> Tuple[Any, int]:
        class_idx = idx // self.n_per_class
        image = self._render(idx)  # pass global idx, not class_idx
        pil_img = Image.fromarray(image)
        return self.transform(pil_img), class_idx


def _safe_circle(
    img: np.ndarray, cy: int, cx: int, radius: int,
    color: List[int], rng: np.random.RandomState,
) -> None:
    """Draw an approximate filled circle using numpy (no cv2 dependency needed)."""
    h, w = img.shape[:2]
    y, x = np.ogrid[:h, :w]
    dist = (y - cy) ** 2 + (x - cx) ** 2
    mask = dist <= radius ** 2
    for c in range(3):
        img[:, :, c][mask] = np.clip(
            img[:, :, c][mask].astype(np.int16) * 0.3 + color[c] * 0.7, 0, 255
        ).astype(np.uint8)


def _safe_diamond(
    img: np.ndarray, cy: int, cx: int, radius: int, color: List[int],
) -> None:
    """Draw a diamond (rotated square) lesion."""
    h, w = img.shape[:2]
    y, x = np.ogrid[:h, :w]
    dist = np.abs(y - cy) + np.abs(x - cx)
    mask = dist <= radius
    for c in range(3):
        img[:, :, c][mask] = np.clip(
            img[:, :, c][mask].astype(np.int16) * 0.3 + color[c] * 0.7, 0, 255
        ).astype(np.uint8)


def _gaussian_blur(img: np.ndarray, radius: int = 1) -> np.ndarray:
    """Apply a simple box-blur approximation (no scipy/cv2 dependency)."""
    if radius <= 0:
        return img
    h, w, c = img.shape
    result = img.copy()
    r = max(1, radius)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy == 0 and dx == 0:
                continue
            shifted = np.roll(img, (dy, dx), axis=(0, 1))
            result = result.astype(np.int16) + shifted.astype(np.int16)
    result = np.clip(result // ((2 * r + 1) ** 2), 0, 255).astype(np.uint8)
    return result


# --------------------------------------------------------------------------- #
# Model definition
# --------------------------------------------------------------------------- #
def _build_model(num_classes: int, pretrained: bool = True) -> "nn.Module":
    """Build a ResNet-18 classifier with a custom head.

    The final FC layer is replaced; all but the last ResNet block are frozen
    during fine-tuning by default (see ``train_from_synthetic`` /
    ``train_from_directory``).
    """
    weights = ResNet18_Weights.IMAGENET1K_V1 if pretrained else None
    model = resnet18(weights=weights)

    # Freeze early layers for faster convergence on small datasets
    for param in list(model.parameters())[:-10]:
        param.requires_grad = False

    # Replace classifier
    num_ftrs = model.fc.in_features
    model.fc = nn.Sequential(
        nn.Dropout(0.3),
        nn.Linear(num_ftrs, 512),
        nn.ReLU(inplace=True),
        nn.Dropout(0.2),
        nn.Linear(512, num_classes),
    )
    return model


# --------------------------------------------------------------------------- #
# Main classifier class
# --------------------------------------------------------------------------- #
class PlantDiseaseClassifier:
    """
    Deep-learning plant disease image classifier.

    Wraps a ResNet-18 CNN.  When PyTorch is available and a trained checkpoint
    exists, ``is_available()`` returns True and ``predict`` returns real
    predictions.  Otherwise the classifier reports itself as unavailable and
    callers fall back to the rule-based tier.
    """

    def __init__(
        self,
        model_path: str | Path | None = None,
        classes: List[str] | None = None,
    ):
        self.model_path = Path(model_path) if model_path else MODEL_PATH
        self.classes_path = self.model_path.parent / "plant_disease_classes.json"
        self.classes_path = Path(self.classes_path)

        self.classes: List[str] = classes if classes is not None else self._load_or_default_classes()
        self.model: Optional["nn.Module"] = None
        self.device: str = "cpu"
        self.transform = None
        self.is_trained = False

        if TORCH_AVAILABLE:
            self.transform = transforms.Compose([
                transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225],
                ),
            ])

        # Attempt lazy load
        self._try_load()

    # ------------------------------------------------------------------ #
    # Public status
    # ------------------------------------------------------------------ #
    @staticmethod
    def is_available() -> bool:
        """Return True if PyTorch + a trained model are ready for inference."""
        return TORCH_AVAILABLE and PlantDiseaseClassifier._weights_exist()

    @classmethod
    def _weights_exist(cls) -> bool:
        return MODEL_PATH.is_file() and CLASSES_PATH.is_file()

    def _instance_weights_exist(self) -> bool:
        """Check if *this* instance's model path has weights."""
        return self.model_path.is_file() and self.classes_path.is_file()

    def is_model_ready(self) -> bool:
        """Instance-level check: torch available + *this* instance's weights exist."""
        return TORCH_AVAILABLE and self._instance_weights_exist()

    def _load_or_default_classes(self) -> List[str]:
        if CLASSES_PATH.is_file():
            try:
                with open(CLASSES_PATH, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return list(DEFAULT_DISEASE_CLASSES)

    # ------------------------------------------------------------------ #
    # Model loading
    # ------------------------------------------------------------------ #
    def _try_load(self) -> bool:
        """Attempt to load saved weights. Safe no-op if unavailable."""
        if not TORCH_AVAILABLE:
            return False
        if not self._weights_exist():
            return False
        try:
            self.model = _build_model(len(self.classes), pretrained=False)
            state_dict = torch.load(self.model_path, map_location=self.device, weights_only=True)
            self.model.load_state_dict(state_dict)
            self.model.to(self.device)
            self.model.eval()
            self.is_trained = True
            return True
        except Exception as exc:
            print(f"[PlantDiseaseClassifier] Warning: could not load model: {exc}")
            self.model = None
            self.is_trained = False
            return False

    def _ensure_loaded(self) -> bool:
        if self.model is not None:
            return True
        return self._try_load()

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #
    def predict(
        self,
        image_input: Any,
        top_k: int = 3,
        crop_hint: str | None = None,
    ) -> Dict[str, Any]:
        """
        Classify a plant image.

        Parameters
        ----------
        image_input : PIL.Image | bytes | str | np.ndarray | torch.Tensor
            The image to classify.
        top_k : int
            Number of top predictions to return.
        crop_hint : str | None
            Optional crop name ("wheat", "rice", "cotton", "maize") to boost
            confidence for disease classes of that crop.

        Returns
        -------
        dict with keys:
            - success: bool
            - available: bool
            - crop: str | None
            - predictions: list of {disease_id, disease, crop, confidence, urgency, explanation}
            - entropy_bits: float
            - is_ambiguous: bool
            - model_version: str
            - engine_version: str
            - timestamp: str
            - error: str (only on failure)
        """
        if not self.is_available():
            return self._unavailable_response()

        if not self._ensure_loaded():
            return self._unavailable_response()

        try:
            img = _load_image(image_input)

            # Convert crop_hint to a crop string for filtering
            crop_key = (crop_hint or "").lower().strip()

            tensor = self.transform(img).unsqueeze(0).to(self.device)

            with torch.no_grad():  # type: ignore[union-attr]
                logits = self.model(tensor)  # type: ignore[union-attr]
                probs = torch.softmax(logits[0], dim=0)  # type: ignore[union-attr]

            # Apply crop hint reweighting
            raw_probs = probs.cpu().numpy()  # type: ignore[union-attr]
            adjusted = self._apply_crop_hint(raw_probs, crop_key)

            # Normalise after adjustment
            if adjusted.sum() > 0:
                adjusted = adjusted / adjusted.sum()
            else:
                adjusted = raw_probs / raw_probs.sum()

            top_indices = np.argsort(adjusted)[::-1][:top_k]

            predictions = []
            probs_list = []
            for idx in top_indices:
                cls_name = self.classes[idx]
                info = DISEASE_INFO.get(cls_name, {})
                conf = float(adjusted[idx])
                probs_list.append(conf)
                predictions.append({
                    "disease_id": cls_name,
                    "disease": info.get("name", cls_name),
                    "crop": info.get("crop", crop_key or "unknown"),
                    "confidence": round(conf, 4),
                    "urgency": info.get("urgency", "medium"),
                    "explanation": info.get("explanation", ""),
                    "data_source": "deep_learning_cnn_resnet18",
                })

            # Entropy of the (full) probability distribution
            from entropy_utils import compute_entropy

            # Use top_k normalised for entropy context
            entropy_info = compute_entropy([
                float(p) for p in np.sort(adjusted)[::-1][:len(adjusted)]
            ])

            return {
                "success": True,
                "available": True,
                "crop": crop_key or None,
                "predictions": predictions,
                "entropy_bits": entropy_info["raw_entropy_nats"],
                "is_ambiguous": entropy_info["is_ambiguous"],
                "model_version": self.model_path.stem if self.model_path.exists() else "untrained",
                "engine_version": ENGINE_VERSION,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "image_size": [IMAGE_SIZE, IMAGE_SIZE],
            }

        except Exception as exc:
            return {
                "success": False,
                "available": self.is_available(),
                "error": str(exc),
                "predictions": [],
                "engine_version": ENGINE_VERSION,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }

    def _apply_crop_hint(self, probs: np.ndarray, crop_key: str) -> np.ndarray:
        """Optionally reweight probabilities to favour classes of the hinted crop."""
        if not crop_key:
            return probs
        adjusted = probs.copy()
        for i, cls_name in enumerate(self.classes):
            info = DISEASE_INFO.get(cls_name, {})
            info_crop = info.get("crop", "").lower()
            if info_crop and crop_key and crop_key in info_crop:
                adjusted[i] *= 1.3  # 30% boost
            elif info_crop and crop_key and crop_key not in info_crop:
                adjusted[i] *= 0.7  # 30% penalty
        return adjusted

    def _unavailable_response(self) -> Dict[str, Any]:
        return {
            "success": False,
            "available": False,
            "predictions": [],
            "error": (
                "Deep learning model not available. "
                "Train a model first (python train_disease_model.py) "
                "or install torch + torchvision."
            ),
            "engine_version": ENGINE_VERSION,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    # ------------------------------------------------------------------ #
    # Training
    # ------------------------------------------------------------------ #
    def train_from_synthetic(
        self,
        classes: List[str] | None = None,
        n_per_class: int = 200,
        epochs: int = 10,
        batch_size: int = 32,
        learning_rate: float = 1e-3,
        seed: int = 42,
        log_fn=print,
    ) -> Dict[str, Any]:
        """Train the model from synthetically generated leaf images."""
        if not TORCH_AVAILABLE:
            return {"success": False, "error": "torch not installed"}

        classes = classes or self.classes
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)

        dataset = _SyntheticDiseaseDataset(classes, n_per_class=n_per_class, seed=seed)

        # 80/20 split
        n_total = len(dataset)
        n_train = int(0.8 * n_total)
        n_val = n_total - n_train
        train_ds, val_ds = torch.utils.data.random_split(
            dataset, [n_train, n_val],
            generator=torch.Generator().manual_seed(seed)
        )

        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

        return self._train_loop(
            train_loader, val_loader, classes,
            epochs=epochs, lr=learning_rate, log_fn=log_fn,
        )

    def train_from_directory(
        self,
        data_dir: str | Path,
        val_split: float = 0.2,
        epochs: int = 20,
        batch_size: int = 32,
        learning_rate: float = 3e-4,
        seed: int = 42,
        log_fn=print,
    ) -> Dict[str, Any]:
        """
        Train from a PlantVillage-style directory structure::

            data_dir/
              wheat_rust/
                img1.jpg
                img2.jpg
              wheat_leaf_blight/
                ...
              wheat_healthy/
                ...

        Sub-directory names become the class labels.
        """
        if not TORCH_AVAILABLE:
            return {"success": False, "error": "torch not installed"}

        data_dir = Path(data_dir)
        if not data_dir.is_dir():
            return {"success": False, "error": f"Directory not found: {data_dir}"}

        # Discover classes from subdirectories
        classes = sorted([d.name for d in data_dir.iterdir()
                         if d.is_dir() and not d.name.startswith(".")])
        if not classes:
            return {"success": False, "error": "No class subdirectories found"}

        # Use torchvision ImageFolder
        from torchvision.datasets import ImageFolder

        dataset = ImageFolder(
            root=str(data_dir),
            transform=transforms.Compose([
                transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
                transforms.RandomHorizontalFlip(p=0.3),
                transforms.RandomRotation(degrees=15),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225],
                ),
            ]),
        )

        n_total = len(dataset)
        n_val = int(val_split * n_total)
        n_train = n_total - n_val
        train_ds, val_ds = torch.utils.data.random_split(
            dataset, [n_train, n_val],
            generator=torch.Generator().manual_seed(seed)
        )

        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

        # Update self.classes to match directory labels
        self.classes = list(dataset.classes)
        self._save_classes()

        return self._train_loop(
            train_loader, val_loader, self.classes,
            epochs=epochs, lr=learning_rate, log_fn=log_fn,
        )

    def _train_loop(
        self,
        train_loader: "DataLoader",
        val_loader: "DataLoader",
        classes: List[str],
        epochs: int,
        lr: float,
        log_fn=print,
    ) -> Dict[str, Any]:
        """Shared training loop for synthetic and directory training."""
        self.classes = classes
        self.device = "cuda" if torch.cuda.is_available() else "cpu"  # type: ignore[union-attr]

        self.model = _build_model(len(classes), pretrained=True)
        self.model.to(self.device)

        # Class-weighted loss to handle imbalance
        class_weights = self._compute_or_load_class_weights(classes, train_loader)
        class_weights = class_weights.to(self.device)  # type: ignore[union-attr]

        criterion = nn.CrossEntropyLoss(weight=class_weights)
        optimizer = optim.Adam(
            filter(lambda p: p.requires_grad, self.model.parameters()),  # type: ignore[arg-type]
            lr=lr,
        )
        scheduler = optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="max", factor=0.5, patience=2
        )

        best_val_acc = 0.0
        for epoch in range(epochs):
            # --- Train ---
            self.model.train()
            running_loss = 0.0
            correct = 0
            total = 0
            for images, labels in train_loader:
                images, labels = images.to(self.device), labels.to(self.device)
                optimizer.zero_grad()
                outputs = self.model(images)
                loss = criterion(outputs, labels)
                loss.backward()
                optimizer.step()

                running_loss += loss.item() * images.size(0)
                _, predicted = outputs.max(1)
                total += labels.size(0)
                correct += predicted.eq(labels).sum().item()

            train_loss = running_loss / total
            train_acc = correct / total

            # --- Validate ---
            val_loss, val_acc = self._validate(val_loader, criterion)

            scheduler.step(val_acc)

            log_fn(
                f"  Epoch {epoch+1:>3}/{epochs} | "
                f"train_loss={train_loss:.4f} train_acc={train_acc:.4f} | "
                f"val_loss={val_loss:.4f} val_acc={val_acc:.4f}"
            )

            if val_acc > best_val_acc:
                best_val_acc = val_acc
                self._save_checkpoint()

        # Reload best checkpoint
        self._try_load()
        self.is_trained = True

        log_fn(f"  ✅ Training complete. Best val accuracy: {best_val_acc:.4f}")

        # Save metadata
        metadata = {
            "classes": self.classes,
            "best_val_accuracy": round(float(best_val_acc), 4),
            "epochs": epochs,
            "image_size": IMAGE_SIZE,
            "engine_version": ENGINE_VERSION,
            "trained_at": datetime.utcnow().isoformat() + "Z",
            "torch_version": torch.__version__ if TORCH_AVAILABLE else "n/a",  # type: ignore[union-attr]
        }
        meta_path = MODEL_PATH.parent / "plant_disease_metadata.json"
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

        return {
            "success": True,
            "best_val_accuracy": round(float(best_val_acc), 4),
            "epochs_completed": epochs,
            "classes": self.classes,
            "device": self.device,
        }

    def _validate(self, val_loader: "DataLoader", criterion: "nn.Module") -> Tuple[float, float]:
        """Run validation and return (loss, accuracy)."""
        self.model.eval()
        loss_sum = 0.0
        correct = 0
        total = 0
        with torch.no_grad():  # type: ignore[union-attr]
            for images, labels in val_loader:
                images, labels = images.to(self.device), labels.to(self.device)
                outputs = self.model(images)
                loss = criterion(outputs, labels)
                loss_sum += loss.item() * images.size(0)
                _, predicted = outputs.max(1)
                total += labels.size(0)
                correct += predicted.eq(labels).sum().item()
        avg_loss = loss_sum / max(total, 1)
        acc = correct / max(total, 1)
        return avg_loss, acc

    def _compute_or_load_class_weights(
        self, classes: List[str], train_loader: "DataLoader",
    ) -> "torch.Tensor":
        """Compute inverse-frequency class weights for unbalanced datasets."""
        n_classes = len(classes)
        counts = np.ones(n_classes, dtype=np.float64)

        # Try to load pre-computed weights
        if CLASS_WEIGHTS_PATH.is_file():
            try:
                with open(CLASS_WEIGHTS_PATH, "r") as f:
                    saved = json.load(f)
                if saved.get("classes") == classes:
                    weights_arr = np.array(saved["weights"], dtype=np.float64)
                    if len(weights_arr) == n_classes:
                        return torch.tensor(weights_arr, dtype=torch.float32)  # type: ignore[union-attr]
            except Exception:
                pass

        # Compute from data
        label_counts = np.zeros(n_classes, dtype=np.int64)
        for _, labels in train_loader:
            for lbl in labels:
                label_counts[lbl.item()] += 1

        total = label_counts.sum()
        for i in range(n_classes):
            if label_counts[i] > 0:
                counts[i] = total / (n_classes * label_counts[i])
            else:
                counts[i] = 1.0

        counts = counts / counts.sum() * n_classes  # normalise

        # Save for future use
        try:
            with open(CLASS_WEIGHTS_PATH, "w") as f:
                json.dump({"classes": classes, "weights": counts.tolist()}, f, indent=2)
        except Exception:
            pass

        return torch.tensor(counts, dtype=torch.float32)  # type: ignore[union-attr]

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _save_checkpoint(self) -> None:
        """Save model state dict and class mapping."""
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        if self.model is not None and TORCH_AVAILABLE:
            torch.save(self.model.state_dict(), str(self.model_path))
        self._save_classes()

    def _save_classes(self) -> None:
        with open(self.classes_path, "w") as f:
            json.dump(self.classes, f, indent=2)

    def save(self) -> None:
        """Public save entry point — persists model + classes."""
        self._save_checkpoint()

    def load(self) -> bool:
        """Public load entry point — reloads weights from disk."""
        return self._try_load()


# --------------------------------------------------------------------------- #
# Convenience: train with synthetic data (used by the bootstrap script and CLI)
# --------------------------------------------------------------------------- #
def train_synthetic_cli(epochs: int = 10, n_per_class: int = 200) -> Dict[str, Any]:
    """CLI helper to train from synthetic data."""
    clf = PlantDiseaseClassifier()
    if not TORCH_AVAILABLE:
        return {"success": False, "error": "PyTorch is not installed. Run: pip install torch torchvision"}
    return clf.train_from_synthetic(epochs=epochs, n_per_class=n_per_class)


if __name__ == "__main__":
    train_synthetic_cli(epochs=10, n_per_class=200)
