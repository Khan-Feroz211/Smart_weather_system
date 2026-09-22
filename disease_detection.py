"""
disease_detection.py
====================
DiseaseDetector module.
Rule-based layer runs 100% locally with zero network dependency (Offline-Safe).
ML-calibrated tier uses ConfidenceCalibrator and computes uncertainty entropy.
"""

from typing import Dict, Any, List
from datetime import datetime
from crop_database import get_crop_data
from entropy_utils import compute_entropy, suggest_next_question
from confidence_calibration import ConfidenceCalibrator

ENGINE_VERSION = "v6.1-geovis"

class DiseaseDetector:
    def __init__(self, sample_count: int = 0):
        self.calibrator = ConfidenceCalibrator(sample_count=sample_count)

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
                "disclaimer": info.get("disclaimer", "Pre-production accuracy based on simulated data."),
                "suggested_next_question": next_question,
                "source": "rule_based_local",
                "engine_version": ENGINE_VERSION,
                "generated_at": datetime.utcnow().isoformat() + "Z"
            }
            results.append(res)

        return results
