"""
api_routes.py
============
Flask API Blueprint for AgriAdvisor Recommendation Engine (v6).
Provides `/api/agri/analyze`, `/api/agri/feedback`, `/api/agri/sync-feedback`, and `/api/agri/status`.
Includes input validation, API key auth checking, and offline queue synchronization.
"""

from flask import Blueprint, request, jsonify
from recommendation_engine import RecommendationEngine
from feedback_store import AccuracyMonitor
import os

agri_bp = Blueprint("agri_bp", __name__)
engine = RecommendationEngine()
monitor = AccuracyMonitor()

API_KEY_REQUIRED = os.environ.get("AGRI_REQUIRE_API_KEY", "false").lower() == "true"
VALID_API_KEYS = {os.environ.get("AGRI_API_KEY", "agri_dev_key_2024")}

def check_auth():
    if not API_KEY_REQUIRED:
        return True
    api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
    return api_key in VALID_API_KEYS

def validate_analyze_payload(data: dict) -> tuple[bool, str]:
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

@agri_bp.route("/api/agri/analyze", methods=["POST", "GET"])
def analyze_crop():
    if not check_auth():
        return jsonify({"error": "Unauthorized", "message": "Invalid or missing X-API-Key header"}), 401

    data = request.get_json(silent=True) or request.args.to_dict() or {}
    
    is_valid, err_msg = validate_analyze_payload(data)
    if not is_valid:
        return jsonify({"error": "Bad Request", "message": err_msg}), 400

    crop = data.get("crop", "wheat")
    symptoms = data.get("symptoms", {"yellow_pustules": True, "leaf_lesions": True})
    site = data.get("site")
    stage = data.get("stage", "heading")
    connectivity_state = data.get("connectivity_state", "ONLINE")
    data_age_hours = float(data.get("data_age_hours", 0.0))
    compact = str(data.get("compact", "false")).lower() in ["true", "1"]

    result = engine.analyze(
        crop=crop,
        symptoms=symptoms,
        site=site,
        stage=stage,
        connectivity_state=connectivity_state,
        data_age_hours=data_age_hours,
        compact=compact
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
    return jsonify({
        "engine_version": "v6.1-geovis",
        "accuracy_monitor": metrics,
        "auth_enabled": API_KEY_REQUIRED,
        "dev_warning": "Localhost/dev mode. Pass X-API-Key header when auth is enabled."
    })
