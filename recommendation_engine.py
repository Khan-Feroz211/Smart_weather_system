"""
recommendation_engine.py
========================
AgriAdvisor Recommendation Engine (v6 + Mobile/Offline + Geovis Satellite).
Orchestrates disease detection, yield estimation, crop suitability, satellite data analysis,
and feedback store logging.
"""

from typing import Dict, Any, Optional
from datetime import datetime
from disease_detection import DiseaseDetector
from yield_estimator import YieldEstimator
from crop_suitability import rank_crops
from geovis_satellite import GeoVisSatelliteAnalyzer
from feedback_store import AccuracyMonitor
from integration_bridge import SystemBridge

ENGINE_VERSION = "v6.1-geovis"

class RecommendationEngine:
    def __init__(self):
        self.accuracy_monitor = AccuracyMonitor()
        try:
            sample_metrics = self.accuracy_monitor.get_accuracy_metrics()
        except Exception:
            sample_metrics = {"sample_count": 0}
        self.detector = DiseaseDetector(sample_count=sample_metrics.get("sample_count", 0))
        self.yield_estimator = YieldEstimator()
        self.satellite_analyzer = GeoVisSatelliteAnalyzer()
        self.bridge = SystemBridge()

    def analyze(
        self,
        crop: str = "wheat",
        symptoms: Optional[Dict[str, Any]] = None,
        site: Optional[Dict[str, Any]] = None,
        stage: str = "heading",
        connectivity_state: str = "ONLINE",
        data_age_hours: float = 0.0
    ) -> Dict[str, Any]:
        if symptoms is None:
            symptoms = {"yellow_pustules": True, "leaf_lesions": True}
        if site is None:
            site = {"temp": 22.0, "rainfall": 500.0, "month": 3}

        # 1. Resolve connectivity & penalties via SystemBridge
        eff_state, penalty, penalty_reason = self.bridge.resolve_connectivity_and_penalties(
            override_state=connectivity_state,
            data_age_hours=data_age_hours
        )

        # 2. Get data provenance and accuracy
        metrics = self.accuracy_monitor.get_accuracy_metrics()
        provenance_str = metrics["provenance"]

        # 3. Format Status Line Header
        if eff_state == "OFFLINE":
            status_line = f"[{provenance_str} | OFFLINE — weather-dependent sections withheld]"
        elif eff_state == "CACHE":
            status_line = f"[{provenance_str} | CACHE, {data_age_hours:.1f}h stale]"
        else:
            status_line = f"[{provenance_str} | ONLINE]"

        # 4. Disease Diagnosis (Pure Local / Offline-Safe)
        diagnoses = self.detector.diagnose_with_uncertainty(crop, symptoms)

        # Apply confidence penalty to diagnoses if in CACHE mode
        if penalty > 0 and eff_state == "CACHE":
            for diag in diagnoses:
                orig_conf = diag["confidence_calibrated"]
                adj_conf = max(0.1, round(orig_conf * (1.0 - penalty), 4))
                diag["confidence_calibrated"] = adj_conf
                diag["confidence_basis"] += f" {penalty_reason}"

        # 5. Geovis Satellite Analysis
        satellite_res = self.satellite_analyzer.analyze_site(site, connectivity_state=eff_state)

        # 6. Yield Estimate
        yield_res = self.yield_estimator.estimate(
            crop=crop,
            weather=site,
            stage=stage,
            diagnoses=diagnoses,
            connectivity_state=eff_state
        )

        # 7. Crop Suitability
        suitability_res = rank_crops(site, connectivity_state=eff_state)

        # Assemble Master Output Payload
        output = {
            "status_header": status_line,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "engine_version": ENGINE_VERSION,
            "diagnoses": diagnoses,
            "geovis_satellite": satellite_res,
            "yield_estimate": yield_res,
            "crop_suitability": suitability_res
        }

        return output
