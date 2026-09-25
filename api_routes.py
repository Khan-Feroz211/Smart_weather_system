"""
api_routes.py
============
Flask API Blueprint for AgriAdvisor Recommendation Engine (v6 + Deep Learning).

Provides:
    * ``/api/agri/analyze``         – full recommendation pipeline (JSON)
    * ``/api/agri/analyze`` (POST)  – same endpoint, also accepts multipart
                                     image upload via ``image`` file field
    * ``/api/agri/disease-diagnose`` – image-only disease diagnosis (multipart)
    * ``/api/agri/feedback``        – log ground-truth feedback
    * ``/api/agri/sync-feedback``   – sync offline feedback queue
    * ``/api/agri/status``          – engine status incl. DL backend
    * ``/api/agri/train-disease-model`` – trigger synthetic model training
"""

from flask import Blueprint, request, jsonify
import os
import io
import json

from recommendation_engine import RecommendationEngine, _get_dl_classifier
from feedback_store import AccuracyMonitor
from plant_disease_model import PlantDiseaseClassifier, TORCH_AVAILABLE

agri_bp = Blueprint("agri_bp", __name__)
engine = RecommendationEngine()
monitor = AccuracyMonitor()

API_KEY_REQUIRED = os.environ.get("AGRI_REQUIRE_API_KEY", "false").lower() == "true"
VALID_API_KEYS = {os.environ.get("AGRI_API_KEY", "agri_dev_key_2024")}

# Upload size limit (2 MB)
MAX_CONTENT_LENGTH = 2 * 1024 * 1024
app_config_app = None  # set lazily by register callback


def check_auth():
    if not API_KEY_REQUIRED:
        return True
    api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
    return api_key in VALID_API_KEYS


def validate_analyze_payload(data: dict) -> tuple:
    if not isinstance(data, dict):
        return False, "Payload must be a JSON object"

    crop = data.get("crop")
    if crop and not isinstance(crop, str):
        return False, "'crop' must be a string"

    symptoms = data.get("symptoms")
    if symptoms is not None and not isinstance(symptoms, dict):
        return False, "'symptoms' must be a JSON dictionary of booleans"

    site = data.get("site")
    if site is not None and not isinstance(site, dict):
        return False, "'site' must be a JSON dictionary"

    connectivity_state = data.get("connectivity_state", "ONLINE")
    if connectivity_state not in ["ONLINE", "CACHE", "OFFLINE"]:
        return False, "'connectivity_state' must be one of: ONLINE, CACHE, OFFLINE"

    return True, "Valid"


def _extract_image():
    """
    Extract an image from the incoming request.

    Supports three input modes:
      1. Multipart form upload:  <input name="image" type="file">
      2. Base64 JSON:           {"image": "<base64 data>"}
      3. Raw bytes body:        POST raw image bytes directly

    Returns (image_bytes, error_message).
    """
    # 1. Multipart file
    if "image" in request.files:
        f = request.files["image"]
        if f.filename == "":
            return None, "No file selected"
        return f.read(), None

    # 2. JSON with base64 image
    data = request.get_json(silent=True)
    if data and isinstance(data, dict) and "image" in data:
        from plant_disease_model import _load_image  # noqa: F401 — ensures PIL
        img_b64 = data["image"]
        try:
            import base64
            raw = base64.b64decode(img_b64)
            return raw, None
        except Exception as exc:
            return None, f"Invalid base64 image: {exc}"

    # 3. Raw body image
    raw_body = request.get_data()
    if raw_body:
        try:
            # Quick check: is it a valid image?
            from PIL import Image
            img = Image.open(io.BytesIO(raw_body))
            img.verify()  # verify raises if invalid
            return raw_body, None
        except Exception:
            pass  # not an image

    return None, "No image provided. Send multipart 'image' or base64 'image' field."


@agri_bp.route("/api/agri/analyze", methods=["POST", "GET"])
def analyze_crop():
    if not check_auth():
        return jsonify({"error": "Unauthorized", "message": "Invalid or missing X-API-Key header"}), 401

    # Limit content length for image uploads
    if request.content_length and request.content_length > MAX_CONTENT_LENGTH:
        return jsonify({"error": "Payload Too Large",
                         "message": f"Image must be under {MAX_CONTENT_LENGTH // (1024*1024)} MB"}), 413

    # ---- Determine data source & image -----------------------------------
    image_input = None
    data: dict = {}

    if request.files and "image" in request.files:
        # Multipart form: image file + form fields
        img_bytes, err = _extract_image()
        if err:
            return jsonify({"error": "Bad Request", "message": err}), 400
        image_input = img_bytes
        data = request.form.to_dict()
    elif request.is_json:
        # Pure JSON (optionally with base64 image)
        data = request.get_json(silent=True) or {}
        if data.get("image"):
            img_bytes, err = _extract_image()
            if err:
                return jsonify({"error": "Bad Request", "message": err}), 400
            image_input = img_bytes
    else:
        # Query-string / GET params
        data = request.args.to_dict() or {}

    is_valid, err_msg = validate_analyze_payload(data)
    if not is_valid:
        return jsonify({"error": "Bad Request", "message": err_msg}), 400

    crop = data.get("crop", "wheat")
    # Symptoms may come as JSON string in multipart form
    symptoms_raw = data.get("symptoms", '{"yellow_pustules": true, "leaf_lesions": true}')
    if isinstance(symptoms_raw, str):
        try:
            symptoms = json.loads(symptoms_raw)
        except Exception:
            symptoms = {"leaf_lesions": True}
    else:
        symptoms = symptoms_raw if symptoms_raw else {"yellow_pustules": True, "leaf_lesions": True}
    site_raw = data.get("site")
    if isinstance(site_raw, str):
        try:
            site = json.loads(site_raw)
        except Exception:
            site = None
    else:
        site = site_raw
    stage = data.get("stage", "heading")
    connectivity_state = data.get("connectivity_state", "ONLINE")
    data_age_hours = float(data.get("data_age_hours", 0.0))
    compact = str(data.get("compact", "false")).lower() in ["true", "1"]
    top_k = int(data.get("top_k", 3))

    result = engine.analyze(
        crop=crop,
        symptoms=symptoms,
        site=site,
        stage=stage,
        connectivity_state=connectivity_state,
        data_age_hours=data_age_hours,
        compact=compact,
        image_input=image_input,
        top_k=top_k,
    )
    return jsonify(result)


@agri_bp.route("/api/agri/disease-diagnose", methods=["POST"])
def diagnose_from_image():
    """
    Diagnose plant disease from an uploaded image.

    Accepts:
      - Multipart form:  POST /api/agri/disease-diagnose with file field 'image'
      - JSON base64:     {"image": "<base64>", "crop": "wheat", "top_k": 3}

    Returns a JSON response with ranked predictions and metadata.
    """
    if not check_auth():
        return jsonify({"error": "Unauthorized", "message": "Invalid or missing X-API-Key header"}), 401

    # Enforce size limit
    if request.content_length and request.content_length > MAX_CONTENT_LENGTH:
        return jsonify({"error": "Payload Too Large",
                         "message": f"Image must be under {MAX_CONTENT_LENGTH // (1024*1024)} MB"}), 413

    # Determine crop and top_k from JSON body or form data
    data = request.get_json(silent=True) or {}
    crop = data.get("crop", "wheat") if data else request.form.get("crop", "wheat")
    top_k = int(data.get("top_k", 3)) if data else int(request.form.get("top_k", 3))

    img_bytes, err = _extract_image()
    if err:
        return jsonify({"error": "Bad Request", "message": err}), 400

    if not PlantDiseaseClassifier.is_available():
        return jsonify({
            "success": False,
            "error": "Deep learning model not available",
            "message": "Install torch + train model (python train_disease_model.py)",
            "image_analysis": {
                "available": False,
                "source": "unavailable",
            },
            "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        }), 200

    result = engine.diagnose_from_image(img_bytes, crop=crop, top_k=top_k)
    return jsonify(result)


@agri_bp.route("/api/agri/train-disease-model", methods=["POST"])
def train_disease_model():
    """
    Trigger deep-learning disease model training (synthetic data by default).

    Accepts JSON: {"epochs": 8, "n_per_class": 200, "data_dir": "/path/to/images"}
    """
    if not check_auth():
        return jsonify({"error": "Unauthorized", "message": "Invalid or missing X-API-Key header"}), 401

    data = request.get_json(silent=True) or {}
    epochs = int(data.get("epochs", 8))
    n_per_class = int(data.get("n_per_class", 200))
    data_dir = data.get("data_dir")

    clf = PlantDiseaseClassifier()

    if not TORCH_AVAILABLE:
        return jsonify({
            "success": False,
            "error": "PyTorch is not installed. Install with: "
                     "pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu",
        }), 200

    if data_dir:
        result = clf.train_from_directory(
            data_dir=data_dir,
            epochs=epochs,
            log_fn=print,
        )
    else:
        result = clf.train_from_synthetic(
            epochs=epochs,
            n_per_class=n_per_class,
            log_fn=print,
        )

    return jsonify(result)


@agri_bp.route("/api/agri/feedback", methods=["POST"])
def submit_feedback():
    if not check_auth():
        return jsonify({"error": "Unauthorized", "message": "Invalid or missing X-API-Key header"}), 401

    data = request.get_json(silent=True) or {}
    crop = data.get("crop", "wheat")
    predicted = data.get("predicted_disease", "unknown")
    actual = data.get("actual_disease", "unknown")
    is_simulated = data.get("is_simulated", True)
    is_offline_submission = data.get("is_offline", False)

    if is_offline_submission:
        monitor.queue_offline_feedback(crop, predicted, actual)
        return jsonify({
            "status": "queued",
            "message": "Feedback queued locally for offline sync."
        })

    monitor.log_feedback(crop, predicted, actual, is_simulated=is_simulated)
    return jsonify({
        "status": "success",
        "message": "Feedback logged successfully.",
        "note": "Pre-production dev testing. Stored in local SQLite."
    })


@agri_bp.route("/api/agri/sync-feedback", methods=["POST"])
def sync_offline_feedback():
    if not check_auth():
        return jsonify({"error": "Unauthorized", "message": "Invalid or missing X-API-Key header"}), 401

    count = monitor.sync_offline_queue()
    return jsonify({
        "status": "success",
        "synced_count": count,
        "message": f"Successfully synced {count} offline feedback items."
    })


@agri_bp.route("/api/agri/status", methods=["GET"])
def get_status():
    metrics = monitor.get_accuracy_metrics()
    _, dl_available = _get_dl_classifier()
    clf = PlantDiseaseClassifier()
    return jsonify({
        "engine_version": "v6.2-hybrid",
        "accuracy_monitor": metrics,
        "auth_enabled": API_KEY_REQUIRED,
        "deep_learning": {
            "available": dl_available,
            "torch_installed": TORCH_AVAILABLE,
            "model_trained": clf.is_trained,
            "classes": clf.classes,
            "num_classes": len(clf.classes),
        },
        "dev_warning": "Localhost/dev mode. Pass X-API-Key header when auth is enabled."
    })
