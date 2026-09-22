"""
api_routes.py
============
Flask API Blueprint for AgriAdvisor Recommendation Engine (v6).
Provides `/api/agri/analyze`, `/api/agri/feedback`, and `/api/agri/status`.
"""

from flask import Blueprint, request, jsonify
from recommendation_engine import RecommendationEngine
import db
from feedback_store import AccuracyMonitor

agri_bp = Blueprint("agri_bp", __name__)
engine = RecommendationEngine()
monitor = AccuracyMonitor()

@agri_bp.route("/api/agri/analyze", methods=["POST", "GET"])
def analyze_crop():
    data = request.get_json(silent=True) or request.args.to_dict() or {}

    crop = data.get("crop", "wheat")
    symptoms = data.get("symptoms", {"yellow_pustules": True, "leaf_lesions": True})
    site = data.get("site", {"temp": 22.0, "rainfall": 500.0, "month": 3})
    stage = data.get("stage", "heading")
    connectivity_state = data.get("connectivity_state", "ONLINE")
    data_age_hours = float(data.get("data_age_hours", 0.0))

    result = engine.analyze(
        crop=crop,
        symptoms=symptoms,
        site=site,
        stage=stage,
        connectivity_state=connectivity_state,
        data_age_hours=data_age_hours
    )
    return jsonify(result)

@agri_bp.route("/api/agri/feedback", methods=["POST"])
def submit_feedback():
    data = request.get_json(silent=True) or {}
    crop = data.get("crop", "wheat")
    predicted = data.get("predicted_disease", "unknown")
    actual = data.get("actual_disease", "unknown")
    is_simulated = data.get("is_simulated", True)

    try:
        monitor.log_feedback(crop, predicted, actual, is_simulated=is_simulated)
    except db.DatabaseUnavailable:
        return jsonify({"status": "error", "message": "Database unavailable."}), 503
    return jsonify({
        "status": "success",
        "message": "Feedback logged successfully.",
        "note": "Pre-production dev testing only. Feedback stored in Supabase PostgreSQL."
    })

@agri_bp.route("/api/agri/status", methods=["GET"])
def get_status():
    try:
        metrics = monitor.get_accuracy_metrics()
    except db.DatabaseUnavailable:
        return jsonify({"status": "error", "message": "Database unavailable."}), 503
    return jsonify({
        "engine_version": "v6.1-geovis",
        "accuracy_monitor": metrics,
        "dev_warning": "Localhost/dev only (Gap 1d: No authentication/input validation)."
    })
