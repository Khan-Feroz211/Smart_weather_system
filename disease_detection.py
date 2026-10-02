"""
disease_detection.py
====================
DiseaseDetector module.
Rule-based layer runs 100% locally with zero network dependency (Offline-Safe).
ML-calibrated tier uses ConfidenceCalibrator and computes uncertainty entropy.

Deep-learning tier (v2 hybrid):
    When PyTorch + a trained CNN checkpoint are available, the detector can
    also accept a plant image and return CNN-based disease predictions.  If the
    DL backend is not available the detector transparently falls back to the
    rule-based tier so the system never breaks.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, Any, List, Optional, Union

from datetime import datetime
from crop_database import get_crop_data
from entropy_utils import compute_entropy, suggest_next_question
from confidence_calibration import ConfidenceCalibrator

# Lazy import of the DL backend — the rule-based tier must work even if
# torch / torchvision are not installed.
_PLANT_DL = None
_DL_IMPORT_ERROR = None
try:
    from plant_disease_model import PlantDiseaseClassifier
    _PLANT_DL = PlantDiseaseClassifier()  # lazy-loads weights if present
except Exception as exc:  # pragma: no cover - exercised when torch missing
    _DL_IMPORT_ERROR = exc
    _PLANT_DL = None

ENGINE_VERSION = "v6.2-hybrid"
"""Version bump to signal the integrated DL tier (hybrid v6.1 + deep learning)."""


class DiseaseDetector:
    def __init__(self, sample_count: int = 0):
        self.calibrator = ConfidenceCalibrator(sample_count=sample_count)

    # ------------------------------------------------------------------ #
    # Deep-learning availability
    # ------------------------------------------------------------------ #
    @property
    def dl_available(self) -> bool:
        """True when the PyTorch CNN backend and trained weights are present."""
        return _PLANT_DL is not None and _PLANT_DL.is_available()

    # ------------------------------------------------------------------ #
    # Rule-based diagnosis (existing entry point, unchanged behaviour)
    # ------------------------------------------------------------------ #
    def diagnose_with_uncertainty(self, crop: str, symptoms: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Diagnoses crop diseases based on input symptoms.
        Completely offline-safe for rule-based tier.
        """
        crop_data = get_crop_data(crop)
        diseases = crop_data.get("diseases", {})

        # Active symptoms reported as True
        active_symptoms = [k for k, v in symptoms.items() if v is True or v == "true"]

        candidates = []
        for dis_id, dis_info in diseases.items():
            matched_symptoms = [s for s in dis_info["symptoms"] if s in active_symptoms]
            match_score = len(matched_symptoms) / len(dis_info["symptoms"]) if dis_info["symptoms"] else 0.0

            if match_score > 0 or not active_symptoms:
                raw_confidence = match_score if active_symptoms else 0.5
                candidates.append({
                    "disease_id": dis_id,
                    "info": dis_info,
                    "raw_confidence": raw_confidence,
                    "matched_symptoms": matched_symptoms
                })

        if not candidates:
            # Fallback if no matching disease found
            first_dis = list(diseases.values())[0] if diseases else {
                "name": "Unknown Health Abnormality",
                "urgency": "medium",
                "disclaimer": "No exact symptom match found.",
                "explanation": "Symptoms provided do not match known disease signatures."
            }
            candidates.append({
                "disease_id": "unknown",
                "info": first_dis,
                "raw_confidence": 0.3,
                "matched_symptoms": []
            })

        # Calculate probabilities for candidate diseases
        raw_conf_sum = sum(c["raw_confidence"] for c in candidates)
        probs = [c["raw_confidence"] / raw_conf_sum for c in candidates] if raw_conf_sum > 0 else [1.0 / len(candidates)] * len(candidates)

        entropy_info = compute_entropy(probs)
        next_question = suggest_next_question(
            symptoms_present={k: (v is True or v == "true") for k, v in symptoms.items()},
            candidates=[c["info"] for c in candidates]
        )

        results = []
        for cand, prob in zip(candidates, probs):
            info = cand["info"]
            calibrated_conf, cal_method, sample_cnt = self.calibrator.calibrate(prob)

            res = {
                "disease": info.get("name", cand["disease_id"]),
                "urgency": info.get("urgency", "medium"),
                "is_ambiguous": entropy_info["is_ambiguous"],
                "ambiguity_threshold": entropy_info["ambiguity_threshold"],
                "uncertainty_entropy": entropy_info,
                "explanation": info.get("explanation", ""),
                "evidence": cand["matched_symptoms"] if cand["matched_symptoms"] else ["No specific symptom match"],
                "data_source": "rule_based_tier_local",
                "confidence_calibrated": calibrated_conf,
                "confidence_basis": f"Rule-based pattern matching (sample count: {sample_cnt}). Fully offline-safe path.",
                "calibration_method": cal_method,
                "calibration_sample_count": sample_cnt,
                "disclaimer": info.get("disclaimer", "Pre-production accuracy. Calibrate with ground-truth feedback via /api/agri/feedback."),
                "suggested_next_question": next_question,
                "source": "rule_based_local",
                "engine_version": ENGINE_VERSION,
                "generated_at": datetime.utcnow().isoformat() + "Z"
            }
            results.append(res)

        return results

    # ------------------------------------------------------------------ #
    # Deep-learning image diagnosis (new tier)
    # ------------------------------------------------------------------ #
    def diagnose_from_image(
        self,
        image_input: Union[bytes, str, "any"],
        crop: str = "wheat",
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Diagnose plant disease from an uploaded image using a CNN
        (ResNet-18 transfer learning).

        Parameters
        ----------
        image_input :
            A PIL.Image, raw bytes (file content), a file path string, or a
            numpy array.
        crop :
            Crop name to bias the prediction ("wheat", "rice", "cotton",
            "maize").  When the DL backend is unavailable the method
            gracefully falls back to the rule-based tier.
        top_k :
            Number of top predictions to return.

        Returns
        -------
        list[dict]
            Unified diagnosis list compatible with the rule-based format.
            Each entry has ``disease``, ``urgency``, ``confidence``,
            ``data_source``, ``explanation``, ``evidence``, and metadata.
        """
        # ------------------------------------------------------------------
        # Fallback path — DL backend not available
        # ------------------------------------------------------------------
        if _PLANT_DL is None or not _PLANT_DL.is_available():
            fallback_msg = (
                f"Deep-learning backend not available "
                f"({'torch missing: ' + str(_DL_IMPORT_ERROR) if _PLANT_DL is None else 'no trained weights'}). "
                f"Falling back to rule-based tier with default symptoms."
            )
            # Delegate to rule-based diagnosis with a generic symptom set
            default_symptoms = {"leaf_lesions": True}
            rule_results = self.diagnose_with_uncertainty(crop, default_symptoms)
            for r in rule_results:
                r["image_analysis"] = {
                    "available": False,
                    "message": fallback_msg,
                    "source": "fallback_rule_based",
                }
            return rule_results

        # ------------------------------------------------------------------
        # Deep-learning path
        # ------------------------------------------------------------------
        dl_result = _PLANT_DL.predict(image_input, top_k=top_k, crop_hint=crop)

        if not dl_result.get("success"):
            # Inference error — still return something useful
            fallback = self.diagnose_with_uncertainty(crop, {"leaf_lesions": True})
            for r in fallback:
                r["image_analysis"] = {
                    "available": False,
                    "message": dl_result.get("error", "Unknown DL inference error"),
                    "source": "fallback_rule_based",
                }
            return fallback

        predictions = dl_result.get("predictions", [])
        if not predictions:
            fallback = self.diagnose_with_uncertainty(crop, {"leaf_lesions": True})
            for r in fallback:
                r["image_analysis"] = {
                    "available": False,
                    "message": "No predictions returned by the model.",
                    "source": "fallback_rule_based",
                }
            return fallback

        # Normalise probabilities from raw model output so they sum to 1
        raw_confs = [p["confidence"] for p in predictions]
        total = sum(raw_confs) or 1.0
        normalised = [c / total for c in raw_confs]

        entropy_info = compute_entropy(normalised)

        # Load DL disease metadata for treatment / explanation enrichment
        try:
            from plant_disease_model import DISEASE_INFO as DL_DISEASE_INFO
        except Exception:
            DL_DISEASE_INFO = {}

        # Build unified diagnosis result list
        results: List[Dict[str, Any]] = []
        for pred, prob in zip(predictions, normalised):
            cal_conf, cal_method, sample_cnt = self.calibrator.calibrate(prob)

            disease_id = pred.get("disease_id", "unknown")
            detail = DL_DISEASE_INFO.get(disease_id, {}) if DL_DISEASE_INFO else {}

            res = {
                "disease": pred.get("disease", disease_id),
                "disease_id": disease_id,
                "crop": pred.get("crop", crop),
                "urgency": pred.get("urgency", detail.get("urgency", "medium")),
                "is_ambiguous": entropy_info["is_ambiguous"],
                "ambiguity_threshold": entropy_info["ambiguity_threshold"],
                "uncertainty_entropy": entropy_info,
                "explanation": pred.get("explanation") or detail.get("explanation", ""),
                "treatment": detail.get("treatment", ""),
                "evidence": ["leaf_image_analysis"],
                "data_source": "deep_learning_cnn_resnet18",
                "confidence_calibrated": cal_conf,
                "confidence_basis": (
                    f"Deep-learning CNN (ResNet-18) classification. "
                    f"Image entropy: {entropy_info['raw_entropy_nats']} nats. "
                    f"Calibration: {cal_method} (n={sample_cnt})."
                ),
                "calibration_method": cal_method,
                "calibration_sample_count": sample_cnt,
                "disclaimer": "Deep-learning diagnosis is pre-production. "
                              "Calibrate with ground-truth feedback via /api/agri/feedback.",
                "suggested_next_question": None,
                "source": "deep_learning_cnn",
                "engine_version": ENGINE_VERSION,
                "image_analysis": {
                    "available": True,
                    "confidence_raw": pred["confidence"],
                    "confidence_calibrated": cal_conf,
                    "top_k": top_k,
                    "model_version": dl_result.get("model_version", "unknown"),
                    "dl_engine_version": dl_result.get("engine_version", "unknown"),
                },
                "generated_at": datetime.utcnow().isoformat() + "Z",
            }
            results.append(res)

        return results

    # ------------------------------------------------------------------ #
    # Hybrid: image + symptoms
    # ------------------------------------------------------------------ #
    def diagnose_hybrid(
        self,
        crop: str,
        symptoms: Optional[Dict[str, Any]] = None,
        image_input: Optional[Union[bytes, str, "any"]] = None,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Combined diagnosis that fuses image-based (DL) and symptom-based
        (rule) evidence via a rank-normalised geometric mean of confidences.

        If no image is supplied, behaves identically to
        ``diagnose_with_uncertainty``.  If the DL backend is unavailable,
        only the rule-based results are returned.
        """
        # Always run rule-based tier
        rule_results = self.diagnose_with_uncertainty(crop, symptoms or {})

        # If no image or DL unavailable, return rule-based only
        if image_input is None:
            for r in rule_results:
                r["image_analysis"] = {
                    "available": False,
                    "message": "No image provided for analysis.",
                    "source": "none",
                }
            return rule_results

        if _PLANT_DL is None or not _PLANT_DL.is_available():
            for r in rule_results:
                r["image_analysis"] = {
                    "available": False,
                    "message": "DL backend unavailable.",
                    "source": "fallback_rule_based",
                }
            return rule_results

        # Run DL tier
        dl_results = self.diagnose_from_image(image_input, crop=crop, top_k=top_k)

        # Fuse: merge by disease_id, averaging confidences
        rule_map: Dict[str, Dict[str, Any]] = {
            r.get("disease_id", r.get("disease", "")): r for r in rule_results
        }
        dl_map: Dict[str, Dict[str, Any]] = {
            r.get("disease_id", r.get("disease", "")): r for r in dl_results
        }

        all_ids = set(rule_map.keys()) | set(dl_map.keys())
        fused: List[Dict[str, Any]] = []

        for did in all_ids:
            rule_r = rule_map.get(did)
            dl_r = dl_map.get(did)

            if rule_r and dl_r:
                # Geometric-mean fusion of calibrated confidences
                r_conf = rule_r.get("confidence_calibrated", 0.5)
                d_conf = dl_r.get("confidence_calibrated", 0.0)
                fused_conf = (r_conf * d_conf) ** 0.5
                merged = dict(rule_r)  # start from rule-based result
                merged["confidence_calibrated"] = round(fused_conf, 4)
                merged["confidence_basis"] += f" | DL confidence: {d_conf:.4f} (fused)"
                merged["image_analysis"] = dl_r.get("image_analysis", {})
                merged["data_source"] = "hybrid_rule_plus_dl"
                merged["source"] = "hybrid"
                fused.append(merged)
            elif dl_r:
                # DL-only (disease not in rule-set for this crop)
                r = dict(dl_r)
                fused.append(r)
            else:
                # Rule-only
                r = dict(rule_r)
                r["image_analysis"] = {
                    "available": True,
                    "message": "Disease not detected in image; rule-based result only.",
                    "source": "rule_based_unmatched",
                }
                fused.append(r)

        # Sort by fused confidence descending
        fused.sort(key=lambda x: x.get("confidence_calibrated", 0.0), reverse=True)
        return fused
