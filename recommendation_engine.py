"""
recommendation_engine.py
========================
AgriAdvisor Recommendation Engine (v6 + Mobile/Offline + Geovis Satellite +
Deep-Learning Disease Image Recognition).

Orchestrates disease detection (rule-based + CNN image), yield estimation,
crop suitability, satellite data analysis, and feedback store logging.
Supports compact mode for low-bandwidth mobile environments.
"""

from typing import Dict, Any, Optional, Union
from datetime import datetime
import os
import io
import requests
from disease_detection import DiseaseDetector
from yield_estimator import YieldEstimator
from crop_suitability import rank_crops
from geovis_satellite import GeoVisSatelliteAnalyzer
from feedback_store import AccuracyMonitor
from integration_bridge import SystemBridge

ENGINE_VERSION = "v6.2-hybrid"

# --- Weather data provider configuration ---
# Supported providers: "openweathermap" (requires API key) or "openmeteo" (free, no key)
# Open-Meteo is a free, open-source weather API that requires NO API key.
# https://open-meteo.com/  (CC BY 4.0)
WEATHER_PROVIDER = os.environ.get('WEATHER_PROVIDER', 'openweathermap').lower().strip()
OPENWEATHER_API_KEY = os.environ.get('OPENWEATHER_API_KEY', 'demo_key')
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPENMETEO_FALLBACK_LOCATIONS = {
    "Lahore": (31.5497, 74.3437),
    "London": (51.5074, -0.1278),
    "New York": (40.7128, -74.0060),
    "Tokyo": (35.6762, 139.6503),
    "Paris": (48.8566, 2.3522),
    "Berlin": (52.5200, 13.4050),
    "Mumbai": (19.0760, 72.8777),
    "Sydney": (-33.8688, 151.2093),
    "Cairo": (30.0444, 31.2357),
    "Delhi": (28.6139, 77.1031),
}

# Deep-learning disease classifier (lazy-loaded singleton)
_dl_classifier = None
_dl_available = False


def _get_dl_classifier():
    """Lazy-initialise the deep-learning classifier singleton."""
    global _dl_classifier, _dl_available
    if _dl_classifier is not None:
        return _dl_classifier, _dl_available
    try:
        from plant_disease_model import PlantDiseaseClassifier
        _dl_classifier = PlantDiseaseClassifier()
        _dl_available = _dl_classifier.is_available()
    except Exception:
        _dl_classifier = None
        _dl_available = False
    return _dl_classifier, _dl_available


class RecommendationEngine:
    def __init__(self):
        self.accuracy_monitor = AccuracyMonitor()
        sample_metrics = self.accuracy_monitor.get_accuracy_metrics()
        self.detector = DiseaseDetector(sample_count=sample_metrics["sample_count"])
        self.yield_estimator = YieldEstimator()
        self.satellite_analyzer = GeoVisSatelliteAnalyzer()
        self.bridge = SystemBridge()

    def _geocode_openmeteo(self, location: str) -> Optional[Dict[str, Any]]:
        """Geocode a city name via the Open-Meteo Geocoding API (no API key needed)."""
        try:
            resp = requests.get(
                OPENMETEO_GEOCODING_URL,
                params={'name': location, 'count': 1, 'format': 'json', 'language': 'en'},
                timeout=3
            )
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                if results:
                    return results[0]
        except Exception:
            pass
        return None

    def fetch_live_telemetry(self, location: str = "Lahore") -> Dict[str, Any]:
        """Fetch live weather telemetry.

        Provider priority:
        1. Open-Meteo (``WEATHER_PROVIDER=openmeteo``) — free, no API key required.
        2. OpenWeatherMap (``WEATHER_PROVIDER=openweathermap``) — requires a valid API key.
        3. Fallback to simulated telemetry.
        """
        if WEATHER_PROVIDER == 'openmeteo':
            # --- Open-Meteo path (no API key) ---
            loc_info = self._geocode_openmeteo(location)
            if loc_info:
                lat, lon = loc_info['latitude'], loc_info['longitude']
            elif location in OPENMETEO_FALLBACK_LOCATIONS:
                lat, lon = OPENMETEO_FALLBACK_LOCATIONS[location]
            else:
                # Try default fallback location (Lahore coordinates)
                lat, lon = 31.5497, 74.3437

            try:
                resp = requests.get(
                    OPENMETEO_FORECAST_URL,
                    params={
                        'latitude': lat,
                        'longitude': lon,
                        'current': 'temperature_2m,relative_humidity_2m,weather_code,pressure_msl,wind_speed_10m,precipitation',
                        'timezone': 'auto',
                    },
                    timeout=5
                )
                if resp.status_code == 200:
                    d = resp.json()
                    current = d.get('current', {})
                    # WMO weather codes → approximate rainfall (mm/h)
                    # Code 61-67, 80-82, 95-96 = precipitation active
                    weather_code = current.get('weather_code', 0)
                    rainfall = current.get('precipitation', 0.0)
                    if rainfall is None:
                        rainfall = 0.0
                    # Convert mm/h to approximate mm over the period
                    rainfall_mm = float(rainfall) * 10  # scale like openweather
                    if weather_code in range(51, 100) and rainfall_mm == 0.0:
                        rainfall_mm = 5.0  # light precipitation inferred from code

                    return {
                        'temp': float(current.get('temperature', 22.0)),
                        'humidity': float(current.get('relative_humidity', 60.0)),
                        'rainfall': rainfall_mm,
                        'wind_speed': float(current.get('wind_speed', 5.0)),
                        'pressure': float(current.get('pressure', 1013.0)),
                        'source': 'live_openmeteo_api',
                        'provider': 'openmeteo',
                        'location': loc_info.get('name', location) if loc_info else location,
                        'latitude': lat,
                        'longitude': lon,
                    }
            except Exception:
                pass

        # --- OpenWeatherMap path (requires API key) ---
        if OPENWEATHER_API_KEY and OPENWEATHER_API_KEY != 'demo_key':
            try:
                resp = requests.get(
                    OPENWEATHER_URL,
                    params={'q': location, 'appid': OPENWEATHER_API_KEY, 'units': 'metric'},
                    timeout=3
                )
                if resp.status_code == 200:
                    d = resp.json()
                    return {
                        'temp': d['main']['temp'],
                        'humidity': d['main']['humidity'],
                        'rainfall': d.get('rain', {}).get('1h', 0.0) * 10,  # approximate
                        'wind_speed': d.get('wind', {}).get('speed', 5.0),
                        'pressure': d.get('main', {}).get('pressure', 1013.0),
                        'source': 'live_openweather_api',
                        'provider': 'openweathermap',
                        'location': d.get('name', location),
                    }
            except Exception:
                pass
        # Fallback default site conditions
        return {'temp': 22.0, 'humidity': 60.0, 'rainfall': 500.0, 'source': 'simulated_telemetry'}

    def analyze(
        self,
        crop: str = "wheat",
        symptoms: Optional[Dict[str, Any]] = None,
        site: Optional[Dict[str, Any]] = None,
        location: str = "Lahore",
        stage: str = "heading",
        connectivity_state: str = "ONLINE",
        data_age_hours: float = 0.0,
        compact: bool = False,
        image_input: Optional[Union[bytes, str, Any]] = None,
        top_k: int = 3,
    ) -> Dict[str, Any]:
        """
        Run the full AgriAdvisor recommendation pipeline.

        Parameters
        ----------
        crop : str
            Crop name (wheat, rice, cotton, maize).
        symptoms : dict | None
            Symptom flags reported by the user (rule-based tier input).
        site : dict | None
            Weather telemetry. If None, live or simulated telemetry is used.
        location : str
            City name for weather data fetch (used when ``site`` is None).
            Open-Meteo geocoding will resolve this to coordinates.
        stage : str
            Crop growth stage.
        connectivity_state : str
            "ONLINE", "CACHE", or "OFFLINE".
        data_age_hours : float
            Staleness of cached data (hours).
        compact : bool
            If True, trims verbose fields for low-bandwidth mobile clients.
        image_input : bytes | str | PIL.Image | np.ndarray | None
            If provided, runs the deep-learning CNN disease classifier on
            the uploaded plant image and fuses results with the rule-based
            diagnosis.
        top_k : int
            Number of top DL predictions to fuse.
        """
        if symptoms is None:
            symptoms = {"yellow_pustules": True, "leaf_lesions": True}

        # Wire live telemetry if ONLINE and site is not custom
        if site is None or not site:
            live_telemetry = self.fetch_live_telemetry(location=location)
            site = live_telemetry
        else:
            if "temp" not in site:
                site["temp"] = 22.0
            if "rainfall" not in site:
                site["rainfall"] = 500.0

        # 1. Resolve connectivity & penalties via SystemBridge
        eff_state, penalty, penalty_reason = self.bridge.resolve_connectivity_and_penalties(
            override_state=connectivity_state,
            data_age_hours=data_age_hours
        )

        # 2. Get data provenance and accuracy
        metrics = self.accuracy_monitor.get_accuracy_metrics()
        # Use the actual weather data source from the site dict for the status
        # header (falls back to accuracy-monitor provenance if no site source).
        site_source = site.get("source", "")
        site_provider = site.get("provider", "") if isinstance(site, dict) else ""
        if site_source.startswith("live_") or site_provider:
            provenance_str = f"LIVE DATA ({site_provider or site_source})"
        else:
            provenance_str = metrics["provenance"]

        # 3. Format Status Line Header
        if eff_state == "OFFLINE":
            status_line = f"[{provenance_str} | OFFLINE — weather-dependent sections withheld]"
        elif eff_state == "CACHE":
            status_line = f"[{provenance_str} | CACHE, {data_age_hours:.1f}h stale]"
        else:
            status_line = f"[{provenance_str} | ONLINE]"

        # 4. Disease Diagnosis — hybrid (DL image + rule-based symptoms)
        dl_classifier, dl_available = _get_dl_classifier()

        if image_input is not None and dl_available:
            # Run hybrid diagnosis: fuse image (DL) + symptom (rule) evidence
            diagnoses = self.detector.diagnose_hybrid(
                crop, symptoms, image_input, top_k=top_k
            )
            image_analysis_summary = {
                "available": True,
                "source": "deep_learning_cnn_resnet18",
                "crop_hint": crop,
            }
        elif image_input is not None and not dl_available:
            # Image was provided but DL backend is unavailable — fall back
            diagnoses = self.detector.diagnose_with_uncertainty(crop, symptoms)
            for d in diagnoses:
                d["image_analysis"] = {
                    "available": False,
                    "message": "Image provided but deep-learning backend is "
                               "not available. Rule-based diagnosis only.",
                    "source": "fallback_rule_based",
                }
            image_analysis_summary = {
                "available": False,
                "source": "unavailable",
                "message": "Deep-learning backend not available. "
                           "Install torch + train model (python train_disease_model.py).",
            }
        else:
            # No image — pure rule-based (original behaviour)
            diagnoses = self.detector.diagnose_with_uncertainty(crop, symptoms)
            image_analysis_summary = {"available": False, "source": "none"}

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

        # Compact mode trimming for low-bandwidth mobile environments (Gap 2c)
        if compact:
            for diag in diagnoses:
                diag.pop("explanation", None)
                diag.pop("disclaimer", None)
                diag.pop("confidence_basis", None)
            if satellite_res.get("disclaimer"):
                satellite_res.pop("disclaimer", None)
            if yield_res.get("disclaimer"):
                yield_res.pop("disclaimer", None)
            if suitability_res.get("disclaimer"):
                suitability_res.pop("disclaimer", None)

        output = {
            "status_header": status_line,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "engine_version": ENGINE_VERSION,
            "crop": crop,
            "growth_stage": stage,
            "connectivity_state": eff_state,
            "diagnoses": diagnoses,
            "image_analysis": image_analysis_summary,
            "geovis_satellite": satellite_res,
            "yield_estimate": yield_res,
            "crop_suitability": suitability_res
        }

        return output

    def diagnose_from_image(
        self,
        image_input: Union[bytes, str, Any],
        crop: str = "wheat",
        top_k: int = 3,
        connectivity_state: str = "ONLINE",
    ) -> Dict[str, Any]:
        """
        Convenience method: run disease diagnosis on an image and return
        a rich result dict (includes weather + yield context).
        """
        return self.analyze(
            crop=crop,
            image_input=image_input,
            connectivity_state=connectivity_state,
            top_k=top_k,
        )
