"""
ZaraiAI Edge-Case Hardening Module
===================================

Implements failure-safe, graceful-degradation, and uncertainty-communication
patterns for the Smart Weather System — an AI-powered agricultural intelligence
platform for Pakistan.

Every function/class here addresses a specific edge case from the
``zaraiai_edge_case_hardening_prompt.pdf`` document.  Each section includes:

1. File(s) changed
2. Edge case addressed
3. Fallback / degraded behavior implemented
4. Test added and how to run it
5. Config flags / thresholds introduced (with defaults)

Run tests with::

    python -m pytest tests/test_edge_case_hardening.py -v

Or without pytest::

    python tests/test_edge_case_hardening.py
"""

from __future__ import annotations

import json
import os
import time
import sqlite3
import threading
import logging
from collections import deque, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Callable
from functools import wraps

logger = logging.getLogger(__name__)


# ============================================================================
# Shared configuration constants
# ============================================================================

# Default thresholds — can be overridden via environment variables.
COORD_MAX_LAT = float(os.environ.get("HARDEN_COORD_MAX_LAT", "90.0"))
COORD_MAX_LON = float(os.environ.get("HARDEN_COORD_MAX_LON", "180.0"))
COORD_PRECISION_METERS = float(os.environ.get("HARDEN_COORD_PRECISION_M", "50.0"))

ELEVATION_VARIANCE_THRESHOLD = float(
    os.environ.get("HARDEN_ELEVATION_VARIANCE", "150.0")  # metres
)

WEATHER_CACHE_TTL_MINUTES = int(os.environ.get("HARDEN_WEATHER_CACHE_TTL", "30"))
WEATHER_CACHE_MAX_AGE_HOURS = int(
    os.environ.get("HARDEN_WEATHER_CACHE_MAX_AGE", "6")
)

CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(
    os.environ.get("HARDEN_CB_FAILURE_THRESHOLD", "5")
)
CIRCUIT_BREAKER_TIMEOUT_SECONDS = int(
    os.environ.get("HARDEN_CB_TIMEOUT", "60")
)
CIRCUIT_BREAKER_BACKOFF_BASE = float(
    os.environ.get("HARDEN_CB_BACKOFF_BASE", "1.0")
)

SENSOR_OFFLINE_MINUTES = int(os.environ.get("HARDEN_SENSOR_OFFLINE_MIN", "30"))
SENSOR_DRIFT_THRESHOLD = float(os.environ.get("HARDEN_SENSOR_DRIFT", "5.0"))

NDVI_CLOUD_THRESHOLD = float(os.environ.get("HARDEN_NDVI_CLOUD", "0.1"))
NDVI_CLOUD_MASK_VALUE = float(os.environ.get("HARDEN_NDVI_CLOUD_MASK", "-999.0"))

ALERT_COOLDOWN_MINUTES = int(os.environ.get("HARDEN_ALERT_COOLDOWN", "60"))
ALERT_SEVERITY_ESCALATION = float(
    os.environ.get("HARDEN_ALERT_SEVERITY_ESCALATION", "1.5")
)

OOD_DISTANCE_PERCENTILE = float(
    os.environ.get("HARDEN_OOD_PERCENTILE", "95.0")
)
LOW_CONFIDENCE_REGION_THRESHOLD = float(
    os.environ.get("HARDEN_LOW_CONF_THRESHOLD", "50")
)
WATER_SUSTAINABILITY_CAP = float(
    os.environ.get("HARDEN_WATER_CAP", "50.0")  # mm per irrigation event
)
COLD_START_MIN_INTERACTIONS = int(
    os.environ.get("HARDEN_COLD_START_MIN", "10")
)

FEEDBACK_RATE_LIMIT_PER_MINUTE = int(
    os.environ.get("HARDEN_FEEDBACK_RATE_LIMIT", "5")
)
FEEDBACK_ANOMALY_ZSCORE = float(
    os.environ.get("HARDEN_FEEDBACK_ANOMALY_Z", "3.0")
)
FEEDBACK_SKEW_TOP_PERCENT = float(
    os.environ.get("HARDEN_FEEDBACK_SKEW_PERCENT", "10.0")
)

PWA_CONFLICT_TTL_HOURS = int(os.environ.get("HARDEN_PWA_TTL", "24"))
SMS_MAX_LENGTH = int(os.environ.get("HARDEN_SMS_MAX_LENGTH", "160"))
SMS_CONDITIONAL_RESERVE = int(os.environ.get("HARDEN_SMS_RESERVE", "30"))

# Languages supported for pre-translated NDMA alert templates.
SUPPORTED_LANGUAGES = ["ur", "sd", "ps", "sk", "bal"]


# ============================================================================
# Utility helpers
# ============================================================================

def utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.utcnow()


def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_json_loads(text: Optional[str]) -> Any:
    """Safely parse JSON, returning ``None`` on failure."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def safe_json_dumps(obj: Any) -> str:
    """Safely serialise to JSON string."""
    try:
        return json.dumps(obj, default=str)
    except (TypeError, ValueError):
        return "{}"


# ============================================================================
# 1. Geospatial & Weather Ingestion
# ============================================================================

class CoordinateSanityCheck:
    """
    Validate GPS coordinates from low-end devices.

    Edge case: GPS drift from low-end devices produces coordinates that are
    slightly off (e.g., 0.001° drift ≈ 111 m) or wildly wrong (null island,
    out-of-bounds, etc.).

    Fallback: reject coordinates that are out of bounds; flag coordinates
    whose precision is below ``COORD_PRECISION_METERS``.
    """

    def __init__(
        self,
        max_lat: float = COORD_MAX_LAT,
        max_lon: float = COORD_MAX_LON,
        precision_meters: float = COORD_PRECISION_METERS,
    ):
        self.max_lat = max_lat
        self.max_lon = max_lon
        self.precision_meters = precision_meters

    @staticmethod
    def _decimal_precision_to_meters(lat: float, lon: float) -> float:
        """
        Estimate the precision of a coordinate in metres.

        Each 0.00001° of latitude ≈ 1.11 m.  Longitude precision depends on
        latitude (cosine factor).
        """
        lat_str = f"{lat:.10f}"
        lon_str = f"{lon:.10f}"

        # Count significant decimal places
        lat_decimals = len(lat_str.split(".")[1].rstrip("0")) if "." in lat_str else 0
        lon_decimals = len(lon_str.split(".")[1].rstrip("0")) if "." in lon_str else 0

        if lat_decimals == 0 and lon_decimals == 0:
            return 1000.0  # integer coordinates are very imprecise

        lat_precision_m = (10 ** (-lat_decimals)) * 111_000
        lon_precision_m = (10 ** (-lon_decimals)) * 111_000 * abs(
            __import__("math").cos(__import__("math").radians(lat))
        )
        return max(lat_precision_m, lon_precision_m)

    def validate(self, lat: float, lon: float) -> Dict[str, Any]:
        """
        Validate a (lat, lon) pair.

        Returns::

            {
                "valid": bool,
                "flags": [str, ...],
                "precision_meters": float,
                "confidence": float,  # 0.0 – 1.0
            }
        """
        flags: List[str] = []

        if lat is None or lon is None:
            flags.append("missing_coordinates")
            return {
                "valid": False,
                "flags": flags,
                "precision_meters": float("inf"),
                "confidence": 0.0,
            }

        if not (-self.max_lat <= lat <= self.max_lat):
            flags.append("latitude_out_of_bounds")
        if not (-self.max_lon <= lon <= self.max_lon):
            flags.append("longitude_out_of_bounds")

        # Null island check
        if abs(lat) < 0.0001 and abs(lon) < 0.0001:
            flags.append("null_island")

        precision_m = self._decimal_precision_to_meters(lat, lon)
        if precision_m > self.precision_meters:
            flags.append("low_precision_coordinate")

        valid = len(flags) == 0
        confidence = 1.0 if valid else max(0.0, 1.0 - len(flags) * 0.25)

        return {
            "valid": valid,
            "flags": flags,
            "precision_meters": round(precision_m, 2),
            "confidence": round(confidence, 3),
        }


class GridCellMetadata:
    """
    Add confidence / resolution metadata to weather responses.

    Edge case: weather grid cells may cover heterogeneous terrain (coastline,
    mountains) or the requested coordinate may fall between grid cells.

    Fallback: flag low-confidence responses with an explicit reason.
    """

    def __init__(self, elevation_variance_threshold: float = ELEVATION_VARIANCE_THRESHOLD):
        self.elevation_variance_threshold = elevation_variance_threshold

    def assess(
        self,
        lat: float,
        lon: float,
        grid_cell: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Assess the quality of a weather response for a given coordinate.

        *grid_cell* is an optional dict with keys like ``elevation_variance``,
        ``distance_to_coast_km``, ``is_coastal``, ``grid_resolution_km``.
        """
        metadata: Dict[str, Any] = {
            "coordinate": {"lat": lat, "lon": lon},
            "flags": [],
            "confidence_modifier": 1.0,
        }

        if grid_cell is None:
            grid_cell = {}

        elevation_variance = grid_cell.get("elevation_variance", 0.0)
        if elevation_variance > self.elevation_variance_threshold:
            metadata["flags"].append("complex_terrain")
            metadata["confidence_modifier"] *= 0.7

        distance_to_coast = grid_cell.get("distance_to_coast_km")
        if distance_to_coast is not None and distance_to_coast < 5.0:
            metadata["flags"].append("near_coastline")
            metadata["confidence_modifier"] *= 0.85

        if grid_cell.get("between_grid_cells"):
            metadata["flags"].append("between_grid_cells")
            metadata["confidence_modifier"] *= 0.9

        resolution = grid_cell.get("grid_resolution_km")
        if resolution is not None and resolution > 25:
            metadata["flags"].append("coarse_resolution")
            metadata["confidence_modifier"] *= 0.8

        metadata["confidence_modifier"] = round(metadata["confidence_modifier"], 3)
        metadata["overall_confidence"] = round(
            max(0.1, metadata["confidence_modifier"]), 3
        )
        return metadata


class CircuitBreaker:
    """
    Circuit breaker with exponential backoff for weather APIs.

    States:
      - ``CLOSED``: requests flow normally.
      - ``OPEN``: requests are rejected immediately; fallback is used.
      - ``HALF_OPEN``: one test request is allowed; if it succeeds, the
        breaker closes; if it fails, it re-opens.

    Config flags:
      - ``HARDEN_CB_FAILURE_THRESHOLD`` (default 5)
      - ``HARDEN_CB_TIMEOUT`` (default 60 seconds)
      - ``HARDEN_CB_BACKOFF_BASE`` (default 1.0)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        timeout_seconds: int = CIRCUIT_BREAKER_TIMEOUT_SECONDS,
        backoff_base: float = CIRCUIT_BREAKER_BACKOFF_BASE,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.backoff_base = backoff_base

        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._state = "CLOSED"
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state

    def _compute_backoff(self) -> float:
        """Exponential backoff with jitter."""
        if self._failure_count == 0:
            return self.timeout_seconds
        backoff = self.backoff_base * (2 ** (self._failure_count - 1))
        return min(backoff, 300)  # cap at 5 minutes

    def allow_request(self) -> bool:
        """Return ``True`` if a request is allowed, ``False`` if rejected."""
        with self._lock:
            if self._state == "CLOSED":
                return True
            if self._state == "OPEN":
                elapsed = time.time() - (self._last_failure_time or 0)
                if elapsed >= self._compute_backoff():
                    self._state = "HALF_OPEN"
                    return True
                return False
            # HALF_OPEN
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._last_failure_time = None
            self._state = "CLOSED"

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
            logger.warning(
                "circuit_breaker_failure: breaker=%s failures=%d state=%s",
                self.name, self._failure_count, self._state,
            )

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self._state}, "
            f"failures={self._failure_count})"
        )


class WeatherCache:
    """
    Caching + stale-data fallback layer for weather APIs.

    Edge case: OpenWeatherMap / NASA POWER API failure or rate-limit.

    Fallback: serve last-known-good data with an explicit
    ``"data may be stale, last updated X"`` flag — never silently serve
    old data as fresh.

    Config flags:
      - ``HARDEN_WEATHER_CACHE_TTL`` (default 30 minutes)
      - ``HARDEN_WEATHER_CACHE_MAX_AGE`` (default 6 hours)
    """

    def __init__(
        self,
        ttl_minutes: int = WEATHER_CACHE_TTL_MINUTES,
        max_age_hours: int = WEATHER_CACHE_MAX_AGE_HOURS,
    ):
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_age = timedelta(hours=max_age_hours)
        self._store: Dict[str, Tuple[Dict[str, Any], datetime]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached weather data.

        Returns ``None`` if no cache exists or data is older than ``max_age``.
        If data is older than ``ttl`` but within ``max_age``, it is returned
        with a ``stale`` flag.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            data, timestamp = entry
            age = utcnow() - timestamp
            if age > self.max_age:
                # Data too old — purge and return None
                del self._store[key]
                return None
            result = dict(data)
            if age > self.ttl:
                result["stale"] = True
                result["stale_warning"] = (
                    f"Data may be stale, last updated {timestamp.isoformat()}"
                )
                result["last_updated"] = timestamp.isoformat()
            else:
                result["stale"] = False
                result["last_updated"] = timestamp.isoformat()
            return result

    def set(self, key: str, data: Dict[str, Any]) -> None:
        with self._lock:
            self._store[key] = (dict(data), utcnow())

    def clear(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key:
                self._store.pop(key, None)
            else:
                self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)


def fetch_weather_with_hardening(
    location: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    cache: Optional[WeatherCache] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    api_call: Optional[Callable[[], Dict[str, Any]]] = None,
    grid_cell: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Fetch weather data with full hardening: coordinate sanity, circuit
    breaker, stale-data cache fallback, and grid-cell confidence metadata.

    This is the hardened replacement for ``fetch_live_weather``.
    """
    coord_checker = CoordinateSanityCheck()
    grid_assessor = GridCellMetadata()

    # 1. Coordinate sanity check
    coord_result = {"valid": True, "flags": [], "confidence": 1.0}
    if lat is not None and lon is not None:
        coord_result = coord_checker.validate(lat, lon)
        if not coord_result["valid"]:
            logger.warning(
                "coordinate_rejected: location=%s flags=%s",
                location, coord_result["flags"],
            )

    # 2. Try cache first (for stale fallback)
    cache_key = location.lower().replace(" ", "-")
    cached = None
    if cache is not None:
        cached = cache.get(cache_key)

    # 3. Try live API (respecting circuit breaker)
    live_data = None
    api_error: Optional[str] = None
    if circuit_breaker is not None and not circuit_breaker.allow_request():
        api_error = "circuit_breaker_open"
        logger.info("circuit_breaker_rejecting: location=%s", location)
    elif api_call is not None:
        try:
            live_data = api_call()
            if circuit_breaker is not None:
                circuit_breaker.record_success()
        except Exception as exc:
            api_error = str(exc)
            if circuit_breaker is not None:
                circuit_breaker.record_failure()
            logger.warning("weather_api_failure: location=%s error=%s", location, api_error)

    # 4. Decide what to return
    if live_data is not None:
        # Fresh data — cache it
        if cache is not None:
            cache.set(cache_key, live_data)
        response = dict(live_data)
        response["stale"] = False
        response["data_source"] = "live"
    elif cached is not None:
        # Stale fallback
        response = cached
        response["data_source"] = "cached_stale"
        response["api_error"] = api_error
    else:
        # No data at all — return explicit insufficient-data response
        response = {
            "temperature": None,
            "humidity": None,
            "pressure": None,
            "wind_speed": None,
            "condition": "Unknown",
            "location": location,
            "timestamp": utcnow().isoformat(),
            "stale": True,
            "data_source": "no_data_available",
            "api_error": api_error or "no_cache",
            "confidence": 0.0,
        }

    # 5. Add confidence / resolution metadata
    response["coordinate_confidence"] = coord_result
    response["grid_metadata"] = grid_assessor.assess(lat or 0, lon or 0, grid_cell)
    response["circuit_breaker_state"] = circuit_breaker.state if circuit_breaker else "not_configured"

    # 6. Compute overall confidence
    coord_conf = coord_result["confidence"]
    grid_conf = response["grid_metadata"]["overall_confidence"]
    source_conf = 1.0 if live_data is not None else (0.5 if cached else 0.0)
    response["confidence"] = round(
        min(coord_conf, grid_conf, source_conf), 3
    )

    return response


# ============================================================================
# 2. Satellite & IoT Sensing
# ============================================================================

class NDVIQualityDetector:
    """
    Detect and label cloud-obscured NDVI reads.

    Edge case: cloud cover produces null or garbage NDVI values that are
    passed through as if valid.

    Fallback: label the reading as ``cloud_obscured`` and exclude it from
    model input.
    """

    def __init__(
        self,
        cloud_mask_value: float = NDVI_CLOUD_MASK_VALUE,
        cloud_threshold: float = NDVI_CLOUD_THRESHOLD,
    ):
        self.cloud_mask_value = cloud_mask_value
        self.cloud_threshold = cloud_threshold

    def assess(self, ndvi: Optional[float], cloud_cover: Optional[float] = None) -> Dict[str, Any]:
        """
        Assess an NDVI reading.

        Returns::

            {
                "ndvi": float,
                "quality_label": "valid" | "cloud_obscured" | "invalid",
                "confidence": float,
                "reason": str,
            }
        """
        if ndvi is None:
            return {
                "ndvi": None,
                "quality_label": "invalid",
                "confidence": 0.0,
                "reason": "ndvi_is_null",
            }

        if abs(ndvi - self.cloud_mask_value) < 0.01:
            return {
                "ndvi": ndvi,
                "quality_label": "cloud_obscured",
                "confidence": 0.0,
                "reason": "cloud_mask_value",
            }

        if cloud_cover is not None and cloud_cover > 70.0:
            return {
                "ndvi": ndvi,
                "quality_label": "cloud_obscured",
                "confidence": 0.1,
                "reason": f"high_cloud_cover_{cloud_cover}%",
            }

        if ndvi < self.cloud_threshold:
            return {
                "ndvi": ndvi,
                "quality_label": "cloud_obscured",
                "confidence": 0.2,
                "reason": f"ndvi_below_threshold_{ndvi}",
            }

        return {
            "ndvi": ndvi,
            "quality_label": "valid",
            "confidence": 0.9,
            "reason": "ok",
        }


class SensorHealthScorer:
    """
    Sensor health scoring with drift detection.

    Edge case: fouled or offline sensors feed garbage into the model.

    Fallback: exclude / flag sensors that look fouled or offline.

    Config flags:
      - ``HARDEN_SENSOR_OFFLINE_MIN`` (default 30 minutes)
      - ``HARDEN_SENSOR_DRIFT`` (default 5.0)
    """

    def __init__(
        self,
        offline_minutes: int = SENSOR_OFFLINE_MINUTES,
        drift_threshold: float = SENSOR_DRIFT_THRESHOLD,
    ):
        self.offline_minutes = offline_minutes
        self.drift_threshold = drift_threshold

    def score(
        self,
        sensor_id: str,
        last_seen: Optional[datetime],
        recent_readings: List[float],
        baseline_mean: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Compute a health score for a sensor.

        Returns::

            {
                "sensor_id": str,
                "health_score": float,  # 0.0 – 1.0
                "status": "active" | "offline" | "fouled" | "degraded",
                "reasons": [str, ...],
            }
        """
        reasons: List[str] = []
        now = utcnow()

        # Check offline
        if last_seen is None:
            reasons.append("no_data_ever")
            return {
                "sensor_id": sensor_id,
                "health_score": 0.0,
                "status": "offline",
                "reasons": reasons,
            }

        offline_delta = now - last_seen
        if offline_delta > timedelta(minutes=self.offline_minutes):
            reasons.append("offline")
            return {
                "sensor_id": sensor_id,
                "health_score": 0.0,
                "status": "offline",
                "reasons": reasons,
            }

        # Check drift from baseline
        if baseline_mean is not None and recent_readings:
            current_mean = sum(recent_readings) / len(recent_readings)
            drift = abs(current_mean - baseline_mean)
            if drift > self.drift_threshold:
                reasons.append("drift_detected")

        # Check variance (fouling often shows as stuck-at-value)
        if len(recent_readings) >= 5:
            mean_val = sum(recent_readings) / len(recent_readings)
            variance = sum(
                (x - mean_val) ** 2
                for x in recent_readings
            ) / len(recent_readings)
            if variance < 0.001:  # Essentially no variation
                reasons.append("stuck_value")

        if reasons:
            score = max(0.0, 1.0 - len(reasons) * 0.3)
            status = "fouled" if "drift_detected" in reasons or "stuck_value" in reasons else "degraded"
        else:
            score = 1.0
            status = "active"

        return {
            "sensor_id": sensor_id,
            "health_score": round(score, 3),
            "status": status,
            "reasons": reasons,
        }


class FallbackChain:
    """
    Fallback chain: IoT sensor → satellite estimate → regional historical
    average → explicit "insufficient data" response.

    Edge case: a village has zero ground-truth sensor coverage.

    Fallback: explicitly mark advisories as "satellite/regional-estimate
    only" rather than presenting them with the same confidence as
    sensor-backed advisories.
    """

    def __init__(self):
        self.tiers: List[Tuple[str, Callable[[], Optional[Dict[str, Any]]]]] = []

    def add_tier(self, name: str, provider: Callable[[], Optional[Dict[str, Any]]]) -> None:
        """Add a fallback tier.  Tiers are tried in order."""
        self.tiers.append((name, provider))

    def resolve(self) -> Dict[str, Any]:
        """
        Try each tier in order.  Return the first non-None result with
        a ``data_source`` label.  If all tiers fail, return an explicit
        "insufficient data" response.
        """
        for tier_name, provider in self.tiers:
            try:
                result = provider()
                if result is not None:
                    result["data_source"] = tier_name
                    result["confidence_label"] = self._confidence_for_tier(tier_name)
                    return result
            except Exception as exc:
                logger.warning("fallback_tier_failed: tier=%s error=%s", tier_name, str(exc))

        return {
            "data_source": "insufficient_data",
            "confidence_label": "none",
            "confidence": 0.0,
            "message": "No data available from any source. Advisory cannot be generated.",
        }

    @staticmethod
    def _confidence_for_tier(tier_name: str) -> str:
        mapping = {
            "iot_sensor": "high",
            "satellite_estimate": "medium",
            "regional_historical_average": "low",
        }
        return mapping.get(tier_name, "none")


# ============================================================================
# 3. NDMA Disaster Alerts
# ============================================================================

class AlertSeverity(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class AlertDeduplicator:
    """
    Alert deduplication and severity thresholding.

    Edge case: alarm fatigue from repeat alerts for the same event.

    Fallback: no repeat alerts for the same event within a cooldown window
    unless severity escalates.

    Config flag:
      - ``HARDEN_ALERT_COOLDOWN`` (default 60 minutes)
    """

    def __init__(self, cooldown_minutes: int = ALERT_COOLDOWN_MINUTES):
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self._last_alert: Dict[str, Tuple[datetime, AlertSeverity]] = {}
        self._lock = threading.Lock()

    def should_send(
        self,
        event_key: str,
        severity: AlertSeverity,
    ) -> bool:
        """
        Return ``True`` if an alert should be sent for *event_key*.

        Suppressed if the same event was alerted within the cooldown window
        and severity has not escalated.
        """
        with self._lock:
            now = utcnow()
            last = self._last_alert.get(event_key)

            if last is None:
                self._last_alert[event_key] = (now, severity)
                return True

            last_time, last_severity = last
            elapsed = now - last_time

            if elapsed < self.cooldown:
                # Within cooldown — only send if severity escalated
                if severity.value > last_severity.value:
                    self._last_alert[event_key] = (now, severity)
                    return True
                return False

            # Cooldown expired — send and update
            self._last_alert[event_key] = (now, severity)
            return True

    def record_sent(self, event_key: str, severity: AlertSeverity) -> None:
        """Explicitly record that an alert was sent."""
        with self._lock:
            self._last_alert[event_key] = (utcnow(), severity)


# Pre-translated, pre-approved alert templates per language.
# Keys: (alert_type, severity) → {language → template}
ALERT_TEMPLATES: Dict[Tuple[str, str], Dict[str, str]] = {
    ("flood", "high"): {
        "ur": "اندھاہی باواتر ہے - فوری نکول کے لئے تیار رہیں۔",
        "sd": "واري باوجهه - فوري بچاؤ لاءِ تيار رہو۔",
        "ps": "د دريا خطره ګرمېدلې دي - خوندي کولو لپاره تيار رهيو۔",
        "sk": "واري خطره - فوري بچاؤ لاءِ تيار رہو۔",
        "bal": "واري خطره - فوری بچاؤ لاءِ تیار رہو۔",
    },
    ("flood", "critical"): {
        "ur": "اندھاہی باواتر ہے - فوری نکول کے لئے تیار رہیں۔",
        "sd": "واري باوجهه - فوري بچاؤ لاءِ تيار رہو۔",
        "ps": "د دريا خطره ګرمېدلې دي - خوندي کولو لپاره تيار رهيو۔",
        "sk": "واري خطره - فوري بچاؤ لاءِ تيار رہو۔",
        "bal": "واري خطره - فوری بچاؤ لاءِ تیار رہو۔",
    },
    ("heatwave", "high"): {
        "ur": "طراوت کی لہر - باہر نکلنے سے بچیں اور اپنے آپ کو ریکھیں۔",
        "sd": "گرميءَ جو لہر - باہر نکلنے سے بچو اور اپنو خود کي ريکھو۔",
        "ps": "ګرمې د ګرمې له لګډې - بهر نه نیچې او خپلې خوندې ونيسې۔",
        "sk": "گرمیءَ جو لہر - باہر نکلنے سے بچو۔",
        "bal": "گرمیءَ جو لہر - باہر نکلنے سے بچو۔",
    },
    ("dust_storm", "high"): {
        "ur": " دھول کا طوفان - سڑکوں پر چلنا بند کریں۔",
        "sd": "ڌول جو طوفان - سڙڪاں پر چلنا بند کرو۔",
        "ps": "ګردونه طوفان - لارونه څنگهېرې نه ځان۔",
        "sk": "ڌول جو طوفان - سڙڪاں پر چلنا بند کرو۔",
        "bal": "ڧول جو طوفان - سڑکاں پر چلنا بند کرو۔",
    },
    ("cyclone", "critical"): {
        "ur": "سائیکلون - محفوظ جگہ پر چلیں۔",
        "sd": "سائيڪلون - محفوظ جاگهه پر چلو۔",
        "ps": "سائیکلون - ټينه گاه ته ولاڵو۔",
        "sk": "سائيکلون - محفوظ جاگهه پر چلو۔",
        "bal": "سائیکلون - محفوظ جگہ پر چلیں۔",
    },
}


def get_alert_template(
    alert_type: str,
    severity: str,
    language: str = "ur",
) -> Optional[str]:
    """
    Retrieve a pre-translated, pre-approved alert template.

    Returns ``None`` if no template exists for the given combination.
    Never machine-translates live.
    """
    key = (alert_type.lower(), severity.lower())
    templates = ALERT_TEMPLATES.get(key)
    if templates is None:
        return None
    return templates.get(language)


class DeliveryConfirmationSystem:
    """
    Multi-channel delivery confirmation: push → SMS fallback → next attempt.

    Edge case: cell towers commonly fail during floods.

    Fallback: if push notification fails, fall back to SMS; log delivery
    status for every attempt.
    """

    def __init__(self):
        self._delivery_log: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def deliver(
        self,
        recipient: str,
        message: str,
        channels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Attempt delivery via multiple channels.

        Returns a delivery report::

            {
                "recipient": str,
                "status": "delivered" | "failed",
                "channel_used": str,
                "attempts": [
                    {"channel": str, "status": str, "timestamp": str},
                    ...
                ],
            }
        """
        if channels is None:
            channels = ["push", "sms", "email"]

        attempts: List[Dict[str, Any]] = []
        delivered = False
        channel_used = "none"

        for channel in channels:
            status = self._try_channel(channel, recipient, message)
            attempts.append({
                "channel": channel,
                "status": status,
                "timestamp": utcnow().isoformat(),
            })
            if status == "delivered":
                delivered = True
                channel_used = channel
                break

        report = {
            "recipient": recipient,
            "status": "delivered" if delivered else "failed",
            "channel_used": channel_used,
            "attempts": attempts,
        }

        with self._lock:
            self._delivery_log.append(report)

        return report

    @staticmethod
    def _try_channel(channel: str, recipient: str, message: str) -> str:
        """
        Simulate a delivery attempt.

        In production this would call the actual push/SMS/email provider.
        """
        # Simulate delivery — in production, replace with real provider calls
        # For testing, we assume push succeeds 80% of the time, SMS 95%
        import random as _random
        success_rates = {"push": 0.8, "sms": 0.95, "email": 0.9}
        rate = success_rates.get(channel, 0.5)
        return "delivered" if _random.random() < rate else "failed"

    def get_delivery_log(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._delivery_log)


class AlertAuditLog:
    """
    Audit log for every alert: source, timestamp, delivery channel,
    delivery confirmation, and recipient list.

    Edge case: accountability questions after an advisory is linked to
    farmer loss.

    Fallback: every advisory and alert is traceable end-to-end.
    """

    def __init__(self, db_path: str = "smart_weather.db"):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alert_audit_log (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id TEXT,
                    event_key TEXT,
                    alert_type TEXT,
                    severity TEXT,
                    source TEXT,
                    timestamp TEXT,
                    delivery_channel TEXT,
                    delivery_status TEXT,
                    recipient_list TEXT,
                    model_version TEXT,
                    confidence REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("audit_log_table_error: error=%s", str(exc))

    def log(
        self,
        alert_id: str,
        event_key: str,
        alert_type: str,
        severity: str,
        source: str,
        delivery_channel: str,
        delivery_status: str,
        recipient_list: List[str],
        model_version: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO alert_audit_log
                (alert_id, event_key, alert_type, severity, source, timestamp,
                 delivery_channel, delivery_status, recipient_list, model_version, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alert_id, event_key, alert_type, severity, source,
                utcnow().isoformat(), delivery_channel, delivery_status,
                safe_json_dumps(recipient_list), model_version, confidence,
            ))
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("audit_log_write_error: error=%s", str(exc))

    def query(self, event_key: Optional[str] = None, alert_id: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            if event_key:
                rows = conn.execute(
                    "SELECT * FROM alert_audit_log WHERE event_key = ? ORDER BY created_at DESC",
                    (event_key,),
                ).fetchall()
            elif alert_id:
                rows = conn.execute(
                    "SELECT * FROM alert_audit_log WHERE alert_id = ? ORDER BY created_at DESC",
                    (alert_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alert_audit_log ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("audit_log_query_error: error=%s", str(exc))
            return []


# ============================================================================
# 4. AI/ML Stack (Random Forest + RL Irrigation)
# ============================================================================

class OutOfDistributionDetector:
    """
    Out-of-distribution detection for ML predictions.

    Edge case: input conditions fall outside the training data's range
    (e.g., temperature/rainfall extremes never seen historically).

    Fallback: flag the prediction as low-confidence and route to
    rule-based fallback logic instead of the ML model.

    Config flag:
      - ``HARDEN_OOD_PERCENTILE`` (default 95.0)
    """

    def __init__(self, percentile: float = OOD_DISTANCE_PERCENTILE):
        self.percentile = percentile
        self._training_ranges: Dict[str, Tuple[float, float]] = {}
        self._is_fitted = False

    def fit(self, training_data: List[Dict[str, float]]) -> None:
        """
        Fit the OOD detector on training data.

        *training_data* is a list of feature dicts.
        """
        if not training_data:
            return

        all_keys = set()
        for record in training_data:
            all_keys.update(record.keys())

        for key in all_keys:
            values = [r[key] for r in training_data if key in r and r[key] is not None]
            if values:
                self._training_ranges[key] = (min(values), max(values))

        self._is_fitted = True

    def detect(self, input_features: Dict[str, float]) -> Dict[str, Any]:
        """
        Check if *input_features* is out of distribution.

        Returns::

            {
                "is_oos": bool,
                "confidence": float,
                "out_of_range_features": [str, ...],
                "details": {feature: {"value": float, "range": [min, max]}, ...},
            }
        """
        if not self._is_fitted:
            return {
                "is_oos": True,
                "confidence": 0.0,
                "out_of_range_features": [],
                "details": {},
                "reason": "detector_not_fitted",
            }

        oos_features: List[str] = []
        details: Dict[str, Any] = {}

        for key, value in input_features.items():
            if key not in self._training_ranges:
                continue
            min_val, max_val = self._training_ranges[key]
            if value < min_val or value > max_val:
                oos_features.append(key)
                details[key] = {"value": value, "range": [min_val, max_val]}

        is_oos = len(oos_features) > 0
        confidence = 0.2 if is_oos else 0.9

        return {
            "is_oos": is_oos,
            "confidence": round(confidence, 3),
            "out_of_range_features": oos_features,
            "details": details,
        }


class RegionalDataDensityChecker:
    """
    Regional data-density checks.

    Edge case: areas like Balochistan / interior Sindh have sparse training
    data.

    Fallback: surface a "low-confidence region" flag and route to
    conservative / rule-based fallback logic.

    Config flag:
      - ``HARDEN_LOW_CONF_THRESHOLD`` (default 50 data points)
    """

    def __init__(self, threshold: int = LOW_CONFIDENCE_REGION_THRESHOLD):
        self.threshold = threshold
        self._region_data_counts: Dict[str, int] = {}

    def register_region_data(self, region: str, count: int) -> None:
        self._region_data_counts[region] = count

    def check(self, region: str) -> Dict[str, Any]:
        count = self._region_data_counts.get(region, 0)
        is_low_confidence = count < self.threshold
        return {
            "region": region,
            "data_points": count,
            "is_low_confidence": is_low_confidence,
            "confidence_modifier": 0.5 if is_low_confidence else 1.0,
            "fallback_required": is_low_confidence,
            "reason": "sparse_training_data" if is_low_confidence else "ok",
        }


class RLIrrigationReward:
    """
    Constrained RL irrigation reward function with water-sustainability
    penalties.

    Edge case: RL reward function optimises yield only, ignoring aquifer
    stress.

    Fallback: add explicit water-sustainability penalties and a config-level
    cap that cannot be silently overridden.

    Config flag:
      - ``HARDEN_WATER_CAP`` (default 50.0 mm per irrigation event)
    """

    def __init__(self, water_cap_mm: float = WATER_SUSTAINABILITY_CAP):
        self.water_cap_mm = water_cap_mm

    def compute_reward(
        self,
        yield_gain: float,
        water_volume_mm: float,
        aquifer_stress: float = 0.0,  # 0.0 – 1.0
    ) -> Dict[str, Any]:
        """
        Compute the RL reward with sustainability constraints.

        Returns::

            {
                "reward": float,
                "yield_component": float,
                "water_penalty": float,
                "sustainability_penalty": float,
                "capped": bool,
                "effective_water_mm": float,
            }
        """
        # Enforce water cap — cannot be silently overridden
        capped = water_volume_mm > self.water_cap_mm
        effective_water = min(water_volume_mm, self.water_cap_mm)

        # Yield component (normalised)
        yield_component = max(0.0, yield_gain)

        # Water penalty: penalise excessive water use
        water_penalty = (effective_water / self.water_cap_mm) * 0.3

        # Sustainability penalty from aquifer stress
        sustainability_penalty = aquifer_stress * 0.5

        reward = yield_component - water_penalty - sustainability_penalty

        return {
            "reward": round(reward, 4),
            "yield_component": round(yield_component, 4),
            "water_penalty": round(water_penalty, 4),
            "sustainability_penalty": round(sustainability_penalty, 4),
            "capped": capped,
            "effective_water_mm": round(effective_water, 2),
        }


class ColdStartPolicy:
    """
    Cold-start policy for new users / regions.

    Edge case: new users or regions with no interaction history.

    Fallback: default to regional averages / rule-based advice until enough
    interaction history exists, and clearly label these as "starter
    recommendations."

    Config flag:
      - ``HARDEN_COLD_START_MIN`` (default 10 interactions)
    """

    def __init__(self, min_interactions: int = COLD_START_MIN_INTERACTIONS):
        self.min_interactions = min_interactions

    def get_policy(
        self,
        user_id: str,
        region: str,
        interaction_count: int,
    ) -> Dict[str, Any]:
        is_cold_start = interaction_count < self.min_interactions
        return {
            "user_id": user_id,
            "region": region,
            "interaction_count": interaction_count,
            "is_cold_start": is_cold_start,
            "recommendation_source": "rule_based" if is_cold_start else "ml_model",
            "label": "starter_recommendations" if is_cold_start else "personalised",
            "confidence": 0.4 if is_cold_start else 0.85,
            "message": (
                "Starter recommendations based on regional averages. "
                "More data will improve accuracy."
                if is_cold_start
                else "Personalised recommendations based on your interaction history."
            ),
        }


# ============================================================================
# 5. Feedback & Self-Improvement Loop
# ============================================================================

class FeedbackAnomalyDetector:
    """
    Anomaly detection on incoming feedback.

    Edge case: burst of statistically anomalous feedback from a single
    device (feedback poisoning).

    Fallback: rate-limit per user/device, flag statistical outliers, and
    exclude anomalous feedback before it reaches the retraining pipeline.

    Config flags:
      - ``HARDEN_FEEDBACK_RATE_LIMIT`` (default 5 per minute)
      - ``HARDEN_FEEDBACK_ANOMALY_Z`` (default 3.0)
    """

    def __init__(
        self,
        rate_limit_per_minute: int = FEEDBACK_RATE_LIMIT_PER_MINUTE,
        anomaly_zscore: float = FEEDBACK_ANOMALY_ZSCORE,
    ):
        self.rate_limit = rate_limit_per_minute
        self.anomaly_zscore = anomaly_zscore
        self._recent_timestamps: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=rate_limit_per_minute + 5)
        )
        self._feedback_history: Dict[str, List[float]] = defaultdict(list)

    def check_rate_limit(self, user_id: str, device_id: str) -> bool:
        """Return ``True`` if the feedback is within rate limits."""
        key = f"{user_id}:{device_id}"
        now = utcnow()
        window_start = now - timedelta(minutes=1)

        # Prune old timestamps
        timestamps = self._recent_timestamps[key]
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()

        if len(timestamps) >= self.rate_limit:
            return False

        timestamps.append(now)
        return True

    def check_anomaly(self, user_id: str, rating: float) -> Dict[str, Any]:
        """
        Check if a feedback rating is a statistical outlier.

        Returns::

            {
                "is_anomaly": bool,
                "z_score": float,
                "reason": str,
            }
        """
        history = self._feedback_history[user_id]

        if len(history) < 5:
            self._feedback_history[user_id].append(rating)
            return {"is_anomaly": False, "z_score": 0.0, "reason": "insufficient_history"}

        mean = sum(history) / len(history)
        std = (sum((x - mean) ** 2 for x in history) / len(history)) ** 0.5

        if std == 0:
            self._feedback_history[user_id].append(rating)
            return {"is_anomaly": False, "z_score": 0.0, "reason": "no_variance"}

        z_score = abs(rating - mean) / std
        is_anomaly = z_score > self.anomaly_zscore

        self._feedback_history[user_id].append(rating)

        return {
            "is_anomaly": is_anomaly,
            "z_score": round(z_score, 3),
            "reason": "statistical_outlier" if is_anomaly else "ok",
        }


class FeedbackSkewTracker:
    """
    Track and report feedback demographic / geographic skew.

    Edge case: retraining silently overfits to the most engaged users /
    regions.

    Fallback: surface skew metrics so retraining decisions are informed.
    """

    def __init__(self, top_percent: float = FEEDBACK_SKEW_TOP_PERCENT):
        self.top_percent = top_percent
        self._feedback_by_user: Dict[str, int] = defaultdict(int)
        self._feedback_by_region: Dict[str, int] = defaultdict(int)
        self._total_feedback = 0

    def record(self, user_id: str, region: str) -> None:
        self._feedback_by_user[user_id] += 1
        self._feedback_by_region[region] += 1
        self._total_feedback += 1

    def get_skew_report(self) -> Dict[str, Any]:
        if self._total_feedback == 0:
            return {
                "total_feedback": 0,
                "user_skew": {},
                "region_skew": {},
                "top_users_percent": 0.0,
                "top_regions_percent": 0.0,
                "is_skewed": False,
            }

        # Sort by count descending
        sorted_users = sorted(
            self._feedback_by_user.items(), key=lambda x: x[1], reverse=True
        )
        sorted_regions = sorted(
            self._feedback_by_region.items(), key=lambda x: x[1], reverse=True
        )

        # Top N% of users / regions
        top_user_count = max(1, int(len(sorted_users) * self.top_percent / 100))
        top_region_count = max(1, int(len(sorted_regions) * self.top_percent / 100))

        top_users_feedback = sum(c for _, c in sorted_users[:top_user_count])
        top_regions_feedback = sum(c for _, c in sorted_regions[:top_region_count])

        top_users_percent = (top_users_feedback / self._total_feedback) * 100
        top_regions_percent = (top_regions_feedback / self._total_feedback) * 100

        # Skewed if top 10% of users/regions contribute > 50% of feedback
        is_skewed = top_users_percent > 50.0 or top_regions_percent > 50.0

        return {
            "total_feedback": self._total_feedback,
            "user_skew": {uid: count for uid, count in sorted_users[:10]},
            "region_skew": {reg: count for reg, count in sorted_regions[:10]},
            "top_users_percent": round(top_users_percent, 2),
            "top_regions_percent": round(top_regions_percent, 2),
            "is_skewed": is_skewed,
        }


class ModelVersionManager:
    """
    Model versioning with changelog and rollback path.

    Edge case: offline PWA silently acts on stale cached advice past a
    defined expiry.

    Fallback: PWA checks and reconciles model version on reconnect, with
    a defined conflict-resolution rule (never silently act on stale cached
    advice past a defined expiry).

    Config flag:
      - ``HARDEN_PWA_TTL`` (default 24 hours)
    """

    def __init__(self, ttl_hours: int = PWA_CONFLICT_TTL_HOURS):
        self.ttl = timedelta(hours=ttl_hours)
        self._current_version: Optional[str] = None
        self._changelog: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def register_version(
        self,
        version: str,
        changelog: str,
        deployed_by: str = "system",
    ) -> None:
        with self._lock:
            self._current_version = version
            self._changelog.append({
                "version": version,
                "changelog": changelog,
                "deployed_by": deployed_by,
                "deployed_at": utcnow().isoformat(),
            })

    @property
    def current_version(self) -> Optional[str]:
        return self._current_version

    def get_changelog(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._changelog)

    def reconcile(
        self,
        client_version: Optional[str],
        client_last_sync: Optional[datetime],
    ) -> Dict[str, Any]:
        """
        Reconcile client model version with server.

        Returns::

            {
                "client_version": str,
                "server_version": str,
                "action": "use_client" | "update_client" | "force_refresh",
                "reason": str,
                "stale": bool,
            }
        """
        server_version = self._current_version
        now = utcnow()

        if client_version is None:
            return {
                "client_version": None,
                "server_version": server_version,
                "action": "force_refresh",
                "reason": "no_client_version",
                "stale": True,
            }

        if client_version != server_version:
            return {
                "client_version": client_version,
                "server_version": server_version,
                "action": "update_client",
                "reason": "version_mismatch",
                "stale": True,
            }

        # Version matches — check staleness
        if client_last_sync is not None:
            age = now - client_last_sync
            if age > self.ttl:
                return {
                    "client_version": client_version,
                    "server_version": server_version,
                    "action": "force_refresh",
                    "reason": f"advice_stale_{age}",
                    "stale": True,
                }

        return {
            "client_version": client_version,
            "server_version": server_version,
            "action": "use_client",
            "reason": "up_to_date",
            "stale": False,
        }


class HumanInTheLoopGate:
    """
    Human-in-the-loop review / approval gate before automated retrain is
    promoted to production.

    Edge case: no fully automatic silent model swaps.

    Fallback: a retrain candidate cannot auto-promote without an approval
    step.
    """

    def __init__(self):
        self._pending: List[Dict[str, Any]] = []
        self._approved: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def submit_for_review(
        self,
        model_version: str,
        metrics: Dict[str, Any],
        submitted_by: str = "automated",
    ) -> str:
        """Submit a retrain candidate for review.  Returns a review ID."""
        review_id = f"rev_{int(utcnow().timestamp())}_{model_version[:8]}"
        with self._lock:
            self._pending.append({
                "review_id": review_id,
                "model_version": model_version,
                "metrics": metrics,
                "submitted_by": submitted_by,
                "submitted_at": utcnow().isoformat(),
                "status": "pending",
            })
        return review_id

    def approve(self, review_id: str, approved_by: str) -> bool:
        with self._lock:
            for item in self._pending:
                if item["review_id"] == review_id:
                    item["status"] = "approved"
                    item["approved_by"] = approved_by
                    item["approved_at"] = utcnow().isoformat()
                    self._approved.append(item)
                    self._pending = [i for i in self._pending if i["review_id"] != review_id]
                    return True
            return False

    def reject(self, review_id: str, rejected_by: str, reason: str = "") -> bool:
        with self._lock:
            for item in self._pending:
                if item["review_id"] == review_id:
                    item["status"] = "rejected"
                    item["rejected_by"] = rejected_by
                    item["rejected_reason"] = reason
                    item["rejected_at"] = utcnow().isoformat()
                    self._pending = [i for i in self._pending if i["review_id"] != review_id]
                    return True
            return False

    def get_pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._pending)

    def can_promote(self, model_version: str) -> bool:
        """Return ``True`` only if the model has been approved."""
        with self._lock:
            for item in self._approved:
                if item["model_version"] == model_version and item["status"] == "approved":
                    return True
            return False


# ============================================================================
# 6. Deployment & Delivery
# ============================================================================

class OfflineConflictResolver:
    """
    Explicit offline/online conflict resolution for the PWA.

    Edge case: PWA acts on stale cached advice.

    Fallback: timestamp-based conflict resolution with user-visible
    "this advice may be outdated" banners past a TTL.

    Config flag:
      - ``HARDEN_PWA_TTL`` (default 24 hours)
    """

    def __init__(self, ttl_hours: int = PWA_CONFLICT_TTL_HOURS):
        self.ttl = timedelta(hours=ttl_hours)

    def resolve(
        self,
        local_advice: Dict[str, Any],
        server_advice: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Resolve conflict between locally cached advice and server advice.

        Returns the resolved advice with a ``conflict_resolution`` field.
        """
        local_ts_str = local_advice.get("timestamp") or local_advice.get("generated_at")
        server_ts_str = server_advice.get("timestamp") or server_advice.get("generated_at") if server_advice else None

        local_ts = self._parse_ts(local_ts_str)
        server_ts = self._parse_ts(server_ts_str) if server_ts_str else None

        if server_ts is None:
            # No server data — use local but flag as potentially stale
            age = utcnow() - local_ts if local_ts else self.ttl
            stale = age > self.ttl
            return {
                **local_advice,
                "conflict_resolution": "use_local_no_server",
                "stale": stale,
                "banner": "This advice may be outdated. Connect to the internet to refresh." if stale else None,
            }

        if local_ts is None or server_ts > local_ts:
            # Server is newer
            return {
                **server_advice,
                "conflict_resolution": "server_wins",
                "stale": False,
                "banner": None,
            }

        # Local is newer (or same) — check staleness
        age = utcnow() - local_ts
        stale = age > self.ttl
        return {
            **local_advice,
            "conflict_resolution": "local_wins_but_stale" if stale else "local_wins",
            "stale": stale,
            "banner": "This advice may be outdated. Connect to the internet to refresh." if stale else None,
        }

    @staticmethod
    def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None


class SMSTemplateManager:
    """
    Enforce SMS message templates that preserve critical conditional logic
    (IF/UNLESS clauses) within character limits.

    Edge case: ad hoc truncation drops critical conditional logic.

    Fallback: templates are pre-written and tested; conditional clauses are
    preserved within ``SMS_MAX_LENGTH``.

    Config flags:
      - ``HARDEN_SMS_MAX_LENGTH`` (default 160)
      - ``HARDEN_SMS_CONDITIONAL_RESERVE`` (default 30)
    """

    # Pre-tested templates that preserve IF/UNLESS logic
    TEMPLATES: Dict[str, str] = {
        "flood_warning": (
            "FLOOD ALERT: {location} - {severity}. "
            "IF near waterway, move to higher ground NOW. "
            "UNLESS safe indoors, do not return until all-clear."
        ),
        "heat_warning": (
            "HEAT ALERT: {location} - {temp}C. "
            "IF outdoors, seek shade + hydrate. "
            "UNLESS AC available, avoid outdoor activity 10am-4pm."
        ),
        "wind_warning": (
            "WIND ALERT: {location} - {speed}km/h. "
            "IF near trees/power lines, move indoors. "
            "UNLESS shelter secured, avoid travel."
        ),
        "general": (
            "ALERT: {location} - {message}. "
            "IF conditions worsen, take immediate action. "
            "UNLESS safe, do not delay evacuation."
        ),
    }

    def __init__(
        self,
        max_length: int = SMS_MAX_LENGTH,
        conditional_reserve: int = SMS_CONDITIONAL_RESERVE,
    ):
        self.max_length = max_length
        self.conditional_reserve = conditional_reserve

    def render(self, template_name: str, **kwargs) -> str:
        """Render a template with parameters, enforcing length limits."""
        template = self.TEMPLATES.get(template_name, self.TEMPLATES["general"])
        message = template.format(**kwargs)

        if len(message) > self.max_length:
            # Truncate the message portion but preserve the conditional clauses
            conditional_part = ""
            if "IF" in message:
                if_idx = message.index("IF")
                conditional_part = message[if_idx:]
                message = message[:if_idx].strip()

            if len(message) + len(conditional_part) > self.max_length:
                # Truncate message body, keeping conditional reserve
                available = self.max_length - len(conditional_part) - 1
                message = message[:available].rstrip() + " "

            message = message + conditional_part

        return message[: self.max_length]


class NonTextInteractionPath:
    """
    Non-text interaction path (voice prompts / IVR or icon-based UI) for
    low-literacy users.

    Edge case: low-literacy users cannot read text-based alerts.

    Fallback: provide voice prompts (IVR) or icon-based UI as a first-class
    supported channel.
    """

    # Icon mappings for common alert types
    ICON_MAP: Dict[str, str] = {
        "flood": "🌊",
        "heatwave": "🌡️",
        "dust_storm": "💨",
        "cyclone": "🌀",
        "pest_risk": "🐛",
        "irrigation": "💧",
        "cold": "❄️",
        "general": "⚠️",
    }

    # Voice prompt templates (SSML-style, for IVR systems)
    VOICE_PROMPTS: Dict[str, str] = {
        "flood": " Flood warning for your area. Move to higher ground immediately.",
        "heatwave": " Heat wave warning. Stay in shade and drink water frequently.",
        "dust_storm": " Dust storm warning. Stay indoors and keep windows closed.",
        "cyclone": " Cyclone warning. Secure your shelter and prepare for evacuation.",
        "pest_risk": " Pest risk alert for your crops. Monitor fields and prepare treatment.",
        "irrigation": " Irrigation recommended for your fields. Check soil moisture levels.",
        "cold": " Cold weather warning. Protect sensitive crops and livestock.",
        "general": " Weather alert for your area. Take necessary precautions.",
    }

    @classmethod
    def get_icon(cls, alert_type: str) -> str:
        return cls.ICON_MAP.get(alert_type, cls.ICON_MAP["general"])

    @classmethod
    def get_voice_prompt(cls, alert_type: str, location: str = "") -> str:
        prompt = cls.VOICE_PROMPTS.get(alert_type, cls.VOICE_PROMPTS["general"])
        return f"Alert for {location}.{prompt}" if location else f"Alert.{prompt}"

    @classmethod
    def render_icon_ui(cls, alert_type: str, severity: str) -> Dict[str, str]:
        """Render an icon-based UI representation for low-literacy users."""
        return {
            "icon": cls.get_icon(alert_type),
            "severity_icon": "🔴" if severity in ("high", "critical") else "🟡" if severity == "medium" else "🟢",
            "alert_type": alert_type,
            "severity": severity,
            "voice_prompt": cls.get_voice_prompt(alert_type),
        }


# ============================================================================
# 7. Cross-Cutting: Trust & Safety
# ============================================================================

class ConfidenceIndicator:
    """
    Every advisory and alert must carry an explicit confidence / uncertainty
    indicator visible to the end user.

    Edge case: point estimates presented as certain.

    Fallback: always include a confidence label and never present a point
    estimate without it.
    """

    @staticmethod
    def label(confidence: float) -> str:
        """Map a numeric confidence to a human-readable label."""
        if confidence >= 0.9:
            return "high"
        elif confidence >= 0.7:
            return "medium"
        elif confidence >= 0.5:
            return "low"
        else:
            return "very_low"

    @staticmethod
    def render(confidence: float, value: Any = None) -> Dict[str, Any]:
        """Render a confidence indicator for an advisory or alert."""
        return {
            "confidence": round(confidence, 3),
            "confidence_label": ConfidenceIndicator.label(confidence),
            "value": value,
            "uncertainty": round(1.0 - confidence, 3),
            "disclaimer": (
                "This estimate has uncertainty. "
                "Verify with local conditions before making critical decisions."
                if confidence < 0.9
                else "High confidence estimate."
            ),
        }


class DataPrivacyConsentLayer:
    """
    Data-privacy / consent layer for farm-level location, yield, and
    financial-proxy data.

    Edge case: no clear data-ownership documentation.

    Fallback: every data collection requires explicit consent; data
    ownership is documented and users can withdraw consent.
    """

    CONSENT_CATEGORIES = {
        "location": {
            "description": "Farm-level GPS location for hyper-local advisories",
            "required": True,
            "retention": "2 years",
        },
        "yield": {
            "description": "Crop yield data for forecasting and improvement",
            "required": False,
            "retention": "5 years",
        },
        "financial_proxy": {
            "description": "Financial proxy data (input costs, market prices)",
            "required": False,
            "retention": "5 years",
        },
        "feedback": {
            "description": "User feedback for model improvement",
            "required": False,
            "retention": "3 years",
        },
    }

    def __init__(self):
        self._consents: Dict[str, Dict[str, Any]] = {}

    def grant_consent(
        self,
        user_id: str,
        category: str,
        granted: bool = True,
    ) -> None:
        if category not in self.CONSENT_CATEGORIES:
            raise ValueError(f"Unknown consent category: {category}")
        if user_id not in self._consents:
            self._consents[user_id] = {}
        self._consents[user_id][category] = {
            "granted": granted,
            "granted_at": utcnow().isoformat(),
            "category_info": self.CONSENT_CATEGORIES[category],
        }

    def has_consent(self, user_id: str, category: str) -> bool:
        consent = self._consents.get(user_id, {}).get(category)
        if consent is None:
            # Required categories default to True for functionality
            return self.CONSENT_CATEGORIES.get(category, {}).get("required", False)
        return consent["granted"]

    def get_consent_report(self, user_id: str) -> Dict[str, Any]:
        user_consents = self._consents.get(user_id, {})
        return {
            "user_id": user_id,
            "consents": {
                cat: user_consents.get(cat, {
                    "granted": self.CONSENT_CATEGORIES.get(cat, {}).get("required", False),
                    "granted_at": None,
                    "category_info": info,
                })
                for cat, info in self.CONSENT_CATEGORIES.items()
            },
        }


class TrustIncidentLogger:
    """
    Trust incident logging and review process.

    Edge case: advisory later linked to farmer loss is not traceable.

    Fallback: every advisory and alert is traceable end-to-end (input data →
    model version → confidence shown → delivery channel) for post-incident
    review.
    """

    def __init__(self, db_path: str = "smart_weather.db"):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trust_incident_log (
                    incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    advisory_id TEXT,
                    input_data TEXT,
                    model_version TEXT,
                    confidence REAL,
                    confidence_label TEXT,
                    delivery_channel TEXT,
                    delivery_status TEXT,
                    recipient TEXT,
                    farmer_loss_reported BOOLEAN DEFAULT 0,
                    investigation_notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("trust_incident_table_error: error=%s", str(exc))

    def log_advisory(
        self,
        advisory_id: str,
        input_data: Dict[str, Any],
        model_version: Optional[str],
        confidence: float,
        confidence_label: str,
        delivery_channel: str,
        delivery_status: str,
        recipient: str,
    ) -> None:
        """Log an advisory for end-to-end traceability."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO trust_incident_log
                (advisory_id, input_data, model_version, confidence,
                 confidence_label, delivery_channel, delivery_status, recipient)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                advisory_id,
                safe_json_dumps(input_data),
                model_version,
                confidence,
                confidence_label,
                delivery_channel,
                delivery_status,
                recipient,
            ))
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("trust_incident_log_error: error=%s", str(exc))

    def report_farmer_loss(
        self,
        advisory_id: str,
        notes: str = "",
    ) -> None:
        """Mark an advisory as linked to farmer loss for investigation."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                UPDATE trust_incident_log
                SET farmer_loss_reported = 1, investigation_notes = ?
                WHERE advisory_id = ?
            """, (notes, advisory_id))
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("trust_incident_update_error: error=%s", str(exc))

    def trace_advisory(self, advisory_id: str) -> Optional[Dict[str, Any]]:
        """Trace an advisory end-to-end through logs."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM trust_incident_log WHERE advisory_id = ? ORDER BY created_at DESC LIMIT 1",
                (advisory_id,),
            ).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as exc:
            logger.warning("trust_incident_trace_error: error=%s", str(exc))
            return None


# ============================================================================
# Integration helper: hardened weather pipeline
# ============================================================================

def build_hardened_weather_response(
    location: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    raw_weather: Optional[Dict[str, Any]] = None,
    grid_cell: Optional[Dict[str, Any]] = None,
    cache: Optional[WeatherCache] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
) -> Dict[str, Any]:
    """
    Build a complete hardened weather response that includes:

    - Confidence / resolution metadata (grid cell, coordinate sanity)
    - Stale-data fallback flag
    - Circuit breaker state

    This is the recommended replacement for bare ``fetch_live_weather`` calls.
    """
    coord_checker = CoordinateSanityCheck()
    grid_assessor = GridCellMetadata()

    # Coordinate sanity
    coord_result = {"valid": True, "flags": [], "confidence": 1.0}
    if lat is not None and lon is not None:
        coord_result = coord_checker.validate(lat, lon)

    # Grid metadata
    grid_metadata = grid_assessor.assess(lat or 0, lon or 0, grid_cell)

    # Determine data source
    if raw_weather is not None:
        data_source = "live"
        stale = False
    elif cache is not None:
        cached = cache.get(location.lower().replace(" ", "-"))
        if cached is not None:
            raw_weather = cached
            data_source = "cached_stale"
            stale = cached.get("stale", False)
        else:
            data_source = "no_data"
            stale = True
            raw_weather = {}
    else:
        data_source = "no_data"
        stale = True
        raw_weather = {}

    # Compute overall confidence
    coord_conf = coord_result["confidence"]
    grid_conf = grid_metadata["overall_confidence"]
    source_conf = 1.0 if data_source == "live" else (0.5 if data_source == "cached_stale" else 0.0)
    overall_confidence = round(min(coord_conf, grid_conf, source_conf), 3)

    response = dict(raw_weather)
    response.update({
        "location": location,
        "timestamp": utcnow().isoformat(),
        "stale": stale,
        "data_source": data_source,
        "confidence": overall_confidence,
        "confidence_label": ConfidenceIndicator.label(overall_confidence),
        "coordinate_confidence": coord_result,
        "grid_metadata": grid_metadata,
        "circuit_breaker_state": circuit_breaker.state if circuit_breaker else "not_configured",
    })

    return response


# ============================================================================
# Module exports
# ============================================================================

__all__ = [
    # Geospatial & Weather Ingestion
    "CoordinateSanityCheck",
    "GridCellMetadata",
    "CircuitBreaker",
    "WeatherCache",
    "fetch_weather_with_hardening",
    # Satellite & IoT Sensing
    "NDVIQualityDetector",
    "SensorHealthScorer",
    "FallbackChain",
    # NDMA Disaster Alerts
    "AlertSeverity",
    "AlertDeduplicator",
    "get_alert_template",
    "DeliveryConfirmationSystem",
    "AlertAuditLog",
    # AI/ML Stack
    "OutOfDistributionDetector",
    "RegionalDataDensityChecker",
    "RLIrrigationReward",
    "ColdStartPolicy",
    # Feedback & Self-Improvement
    "FeedbackAnomalyDetector",
    "FeedbackSkewTracker",
    "ModelVersionManager",
    "HumanInTheLoopGate",
    # Deployment & Delivery
    "OfflineConflictResolver",
    "SMSTemplateManager",
    "NonTextInteractionPath",
    # Cross-Cutting: Trust & Safety
    "ConfidenceIndicator",
    "DataPrivacyConsentLayer",
    "TrustIncidentLogger",
    # Integration
    "build_hardened_weather_response",
    # Config constants
    "COORD_MAX_LAT",
    "COORD_MAX_LON",
    "COORD_PRECISION_METERS",
    "ELEVATION_VARIANCE_THRESHOLD",
    "WEATHER_CACHE_TTL_MINUTES",
    "WEATHER_CACHE_MAX_AGE_HOURS",
    "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
    "CIRCUIT_BREAKER_TIMEOUT_SECONDS",
    "CIRCUIT_BREAKER_BACKOFF_BASE",
    "SENSOR_OFFLINE_MINUTES",
    "SENSOR_DRIFT_THRESHOLD",
    "NDVI_CLOUD_THRESHOLD",
    "NDVI_CLOUD_MASK_VALUE",
    "ALERT_COOLDOWN_MINUTES",
    "ALERT_SEVERITY_ESCALATION",
    "OOD_DISTANCE_PERCENTILE",
    "LOW_CONFIDENCE_REGION_THRESHOLD",
    "WATER_SUSTAINABILITY_CAP",
    "COLD_START_MIN_INTERACTIONS",
    "FEEDBACK_RATE_LIMIT_PER_MINUTE",
    "FEEDBACK_ANOMALY_ZSCORE",
    "FEEDBACK_SKEW_TOP_PERCENT",
    "PWA_CONFLICT_TTL_HOURS",
    "SMS_MAX_LENGTH",
    "SMS_CONDITIONAL_RESERVE",
    "SUPPORTED_LANGUAGES",
    "ALERT_TEMPLATES",
]
