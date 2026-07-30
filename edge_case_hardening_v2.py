"""
Edge-Case Hardening: Graceful Degradation for Low-Connectivity Environments
=============================================================================

Phase 6: Edge-Case Hardening (Offline & Low-Connectivity)

Current: Fails if the API is down.

Required: Implement "Graceful Degradation."

  1. Fallback Cache System: Store the last 7 days of reliable model weights
     and weather observations locally.

  2. Cache Mode: If the OpenWeather API or internet fails, the system
     automatically switches to "Cache Mode," serving predictions based on
     the last known state with a clear "Stale Data" warning banner.

  3. Confidence Penalty: If recent data is missing, automatically lower the
     AI's confidence score to prevent false alarms.

Justification: Internet outages are common in rural Pakistan. The system
must remain operational and honest, rather than crashing.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

# Cache settings
CACHE_RETENTION_DAYS = int(os.environ.get("EDGE_CACHE_DAYS", "7"))
CACHE_CHECK_INTERVAL_SECONDS = int(os.environ.get("EDGE_CACHE_CHECK_INTERVAL", "300"))

# Confidence penalty settings
CONFIDENCE_PENALTY_PER_HOUR_STALE = float(os.environ.get("EDGE_CONFIDENCE_PENALTY_PER_HOUR", "0.05"))
CONFIDENCE_PENALTY_PER_MISSING_FIELD = float(os.environ.get("EDGE_CONFIDENCE_PENALTY_PER_FIELD", "0.1"))
CONFIDENCE_MINIMUM = float(os.environ.get("EDGE_CONFIDENCE_MINIMUM", "0.1"))

# API timeout settings
API_TIMEOUT_SECONDS = int(os.environ.get("EDGE_API_TIMEOUT", "10"))
API_MAX_RETRIES = int(os.environ.get("EDGE_API_MAX_RETRIES", "3"))
API_RETRY_BACKOFF_BASE = float(os.environ.get("EDGE_API_RETRY_BACKOFF", "1.0"))

# Circuit breaker settings
CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(os.environ.get("EDGE_CB_FAILURE_THRESHOLD", "5"))
CIRCUIT_BREAKER_TIMEOUT_SECONDS = int(os.environ.get("EDGE_CB_TIMEOUT", "60"))


# ============================================================================
# Cache Mode Enum
# ============================================================================

class CacheMode(Enum):
    """Operational modes for the system."""
    ONLINE = "online"          # Normal operation, API available
    DEGRADED = "degraded"      # API failing, using cache
    CACHE_ONLY = "cache_only"  # No API, serving from cache only
    OFFLINE = "offline"        # No data available at all


# ============================================================================
# Fallback Cache System
# ============================================================================

@dataclass
class CachedWeatherData:
    """Cached weather observation."""
    location: str
    data: Dict[str, Any]
    timestamp: datetime
    source: str  # "live" or "cached"
    confidence: float
    is_stale: bool = False


@dataclass
class CachedModelState:
    """Cached model state."""
    model_path: str
    model_version: str
    saved_at: datetime
    training_data_points: int
    training_metrics: Dict[str, Any]
    is_valid: bool = True


class FallbackCacheSystem:
    """
    Fallback cache system for graceful degradation.

    Stores the last 7 days of:
      - Weather observations (per location)
      - Model weights and metadata
      - Feature engineering results

    If the API or internet fails, the system automatically switches to
    "Cache Mode," serving predictions based on the last known state
    with a clear "Stale Data" warning banner.
    """

    def __init__(
        self,
        cache_dir: str = "cache",
        retention_days: int = CACHE_RETENTION_DAYS,
    ):
        self.cache_dir = cache_dir
        self.retention_days = retention_days
        self._lock = threading.RLock()
        self._weather_cache: Dict[str, deque] = defaultdict(lambda: deque(maxlen=24 * retention_days))
        self._model_cache: Optional[CachedModelState] = None
        self._cache_hits = 0
        self._cache_misses = 0
        self._mode = CacheMode.ONLINE

        # Create cache directory
        os.makedirs(cache_dir, exist_ok=True)
        os.makedirs(os.path.join(cache_dir, "weather"), exist_ok=True)
        os.makedirs(os.path.join(cache_dir, "models"), exist_ok=True)

        # Load existing cache
        self._load_cache()

    def _load_cache(self):
        """Load cached data from disk."""
        try:
            # Load weather cache
            weather_cache_path = os.path.join(self.cache_dir, "weather_cache.json")
            if os.path.exists(weather_cache_path):
                with open(weather_cache_path, "r") as f:
                    data = json.load(f)
                    for location, entries in data.items():
                        for entry in entries:
                            cached = CachedWeatherData(
                                location=location,
                                data=entry["data"],
                                timestamp=datetime.fromisoformat(entry["timestamp"]),
                                source=entry["source"],
                                confidence=entry["confidence"],
                                is_stale=entry.get("is_stale", False),
                            )
                            self._weather_cache[location].append(cached)

            # Load model cache metadata
            model_cache_path = os.path.join(self.cache_dir, "models", "model_metadata.json")
            if os.path.exists(model_cache_path):
                with open(model_cache_path, "r") as f:
                    meta = json.load(f)
                    self._model_cache = CachedModelState(
                        model_path=meta["model_path"],
                        model_version=meta["model_version"],
                        saved_at=datetime.fromisoformat(meta["saved_at"]),
                        training_data_points=meta["training_data_points"],
                        training_metrics=meta.get("training_metrics", {}),
                        is_valid=meta.get("is_valid", True),
                    )

            logger.info(f"Cache loaded: {sum(len(v) for v in self._weather_cache.values())} weather entries, "
                       f"model cache: {'available' if self._model_cache else 'none'}")
        except Exception as e:
            logger.warning(f"Cache load error: {e}")

    def _save_cache(self):
        """Save cache to disk."""
        try:
            # Save weather cache
            weather_cache_path = os.path.join(self.cache_dir, "weather_cache.json")
            data = {}
            for location, entries in self._weather_cache.items():
                data[location] = [
                    {
                        "data": entry.data,
                        "timestamp": entry.timestamp.isoformat(),
                        "source": entry.source,
                        "confidence": entry.confidence,
                        "is_stale": entry.is_stale,
                    }
                    for entry in entries
                ]
            with open(weather_cache_path, "w") as f:
                json.dump(data, f, indent=2, default=str)

            # Save model cache metadata
            if self._model_cache:
                model_cache_path = os.path.join(self.cache_dir, "models", "model_metadata.json")
                meta = {
                    "model_path": self._model_cache.model_path,
                    "model_version": self._model_cache.model_version,
                    "saved_at": self._model_cache.saved_at.isoformat(),
                    "training_data_points": self._model_cache.training_data_points,
                    "training_metrics": self._model_cache.training_metrics,
                    "is_valid": self._model_cache.is_valid,
                }
                with open(model_cache_path, "w") as f:
                    json.dump(meta, f, indent=2, default=str)

        except Exception as e:
            logger.warning(f"Cache save error: {e}")

    def store_weather(self, location: str, data: Dict[str, Any], confidence: float = 1.0) -> None:
        """Store weather observation in cache."""
        with self._lock:
            cached = CachedWeatherData(
                location=location,
                data=dict(data),
                timestamp=datetime.now(),
                source="live",
                confidence=confidence,
                is_stale=False,
            )
            self._weather_cache[location].append(cached)
            self._save_cache()

    def get_cached_weather(
        self,
        location: str,
        max_age_hours: float = 168.0,  # 7 days
    ) -> Optional[CachedWeatherData]:
        """
        Get the most recent cached weather for a location.

        Returns None if no cache exists or data is too old.
        """
        with self._lock:
            entries = self._weather_cache.get(location, deque())
            if not entries:
                self._cache_misses += 1
                return None

            # Get most recent entry
            latest = entries[-1]
            age_hours = (datetime.now() - latest.timestamp).total_seconds() / 3600

            if age_hours > max_age_hours:
                self._cache_misses += 1
                return None

            # Mark as stale if older than 1 hour
            if age_hours > 1.0:
                latest.is_stale = True
                latest.confidence *= (1.0 - min(0.5, age_hours * CONFIDENCE_PENALTY_PER_HOUR_STALE))

            self._cache_hits += 1
            return latest

    def get_recent_weather_history(
        self,
        location: str,
        hours: int = 24,
    ) -> List[Dict[str, Any]]:
        """Get recent weather history for a location."""
        with self._lock:
            entries = self._weather_cache.get(location, deque())
            cutoff = datetime.now() - timedelta(hours=hours)
            return [
                entry.data for entry in entries
                if entry.timestamp >= cutoff
            ]

    def store_model_state(
        self,
        model_path: str,
        model_version: str,
        training_data_points: int,
        training_metrics: Dict[str, Any],
    ) -> None:
        """Store model state in cache."""
        with self._lock:
            self._model_cache = CachedModelState(
                model_path=model_path,
                model_version=model_version,
                saved_at=datetime.now(),
                training_data_points=training_data_points,
                training_metrics=training_metrics,
                is_valid=True,
            )
            self._save_cache()

    def get_cached_model_state(self) -> Optional[CachedModelState]:
        """Get the cached model state."""
        with self._lock:
            if self._model_cache and self._model_cache.is_valid:
                # Check if model file still exists
                if os.path.exists(self._model_cache.model_path):
                    return self._model_cache
            return None

    def set_mode(self, mode: CacheMode) -> None:
        """Set the current operational mode."""
        with self._lock:
            old_mode = self._mode
            self._mode = mode
            if old_mode != mode:
                logger.info(f"Cache mode changed: {old_mode.value} -> {mode.value}")

    def get_mode(self) -> CacheMode:
        """Get the current operational mode."""
        with self._lock:
            return self._mode

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total_entries = sum(len(v) for v in self._weather_cache.values())
            return {
                "mode": self._mode.value,
                "total_weather_entries": total_entries,
                "locations_cached": len(self._weather_cache),
                "model_cache_available": self._model_cache is not None,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "cache_hit_rate": self._cache_hits / max(1, self._cache_hits + self._cache_misses),
                "retention_days": self.retention_days,
            }

    def cleanup_old_entries(self) -> int:
        """Remove entries older than retention period. Returns count removed."""
        with self._lock:
            cutoff = datetime.now() - timedelta(days=self.retention_days)
            removed = 0
            for location in list(self._weather_cache.keys()):
                entries = self._weather_cache[location]
                while entries and entries[0].timestamp < cutoff:
                    entries.popleft()
                    removed += 1
                if not entries:
                    del self._weather_cache[location]
            if removed > 0:
                self._save_cache()
            return removed


# ============================================================================
# Circuit Breaker
# ============================================================================

class CircuitBreaker:
    """
    Circuit breaker for API calls with exponential backoff.

    States:
      - CLOSED: requests flow normally
      - OPEN: requests rejected, fallback used
      - HALF_OPEN: one test request allowed
    """

    def __init__(
        self,
        name: str = "weather_api",
        failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        timeout_seconds: int = CIRCUIT_BREAKER_TIMEOUT_SECONDS,
        backoff_base: float = API_RETRY_BACKOFF_BASE,
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

    def allow_request(self) -> bool:
        """Return True if a request is allowed."""
        with self._lock:
            if self._state == "CLOSED":
                return True
            if self._state == "OPEN":
                elapsed = time.time() - (self._last_failure_time or 0)
                backoff = self.backoff_base * (2 ** (self._failure_count - 1))
                backoff = min(backoff, 300)  # Cap at 5 minutes
                if elapsed >= backoff:
                    self._state = "HALF_OPEN"
                    return True
                return False
            return True  # HALF_OPEN

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
                f"Circuit breaker failure: {self.name} "
                f"failures={self._failure_count} state={self._state}"
            )


# ============================================================================
# Confidence Penalty System
# ============================================================================

class ConfidencePenaltySystem:
    """
    Confidence penalty system for stale or missing data.

    Automatically lowers the AI's confidence score when:
      - Data is stale (older than expected)
      - Fields are missing
      - API is in degraded mode
      - Cache mode is active

    This prevents false alarms by being honest about uncertainty.
    """

    @staticmethod
    def apply_penalty(
        base_confidence: float,
        data_age_hours: float = 0.0,
        missing_fields: int = 0,
        is_cache_mode: bool = False,
        is_degraded: bool = False,
    ) -> Tuple[float, List[str]]:
        """
        Apply confidence penalties based on data quality.

        Parameters
        ----------
        base_confidence : float
            Original confidence from the model.
        data_age_hours : float
            Age of the data in hours.
        missing_fields : int
            Number of missing weather fields.
        is_cache_mode : bool
            Whether the system is in cache-only mode.
        is_degraded : bool
            Whether the system is in degraded mode.

        Returns
        -------
        (penalized_confidence, penalty_reasons) : Tuple[float, List[str]]
        """
        confidence = base_confidence
        reasons = []

        # Penalty for stale data
        if data_age_hours > 0:
            stale_penalty = min(0.5, data_age_hours * CONFIDENCE_PENALTY_PER_HOUR_STALE)
            if stale_penalty > 0:
                confidence -= stale_penalty
                reasons.append(f"data_stale_{data_age_hours:.1f}h")

        # Penalty for missing fields
        if missing_fields > 0:
            field_penalty = min(0.3, missing_fields * CONFIDENCE_PENALTY_PER_MISSING_FIELD)
            if field_penalty > 0:
                confidence -= field_penalty
                reasons.append(f"missing_fields_{missing_fields}")

        # Penalty for cache mode
        if is_cache_mode:
            confidence -= 0.3
            reasons.append("cache_mode_active")

        # Penalty for degraded mode
        if is_degraded:
            confidence -= 0.2
            reasons.append("degraded_mode")

        # Ensure confidence stays within bounds
        confidence = max(CONFIDENCE_MINIMUM, min(1.0, confidence))

        return confidence, reasons

    @staticmethod
    def get_confidence_label(confidence: float) -> str:
        """Map confidence to a human-readable label."""
        if confidence >= 0.9:
            return "high"
        elif confidence >= 0.7:
            return "medium"
        elif confidence >= 0.5:
            return "low"
        else:
            return "very_low"


# ============================================================================
# Graceful Degradation Manager
# ============================================================================

class GracefulDegradationManager:
    """
    Main manager for graceful degradation.

    Coordinates:
      - Cache system
      - Circuit breaker
      - Confidence penalties
      - Mode switching

    This is the main entry point for Phase 6 functionality.
    """

    def __init__(
        self,
        cache_dir: str = "cache",
        api_url: str = "https://api.openweathermap.org/data/2.5/weather",
        api_key: Optional[str] = None,
    ):
        self.cache = FallbackCacheSystem(cache_dir=cache_dir)
        self.circuit_breaker = CircuitBreaker(name="weather_api")
        self.confidence_system = ConfidencePenaltySystem()
        self.api_url = api_url
        self.api_key = api_key
        self._lock = threading.RLock()

    def fetch_weather_with_degradation(
        self,
        location: str,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Fetch weather data with full graceful degradation.

        1. Try live API (respecting circuit breaker)
        2. If API fails, use cached data
        3. If no cache, return insufficient data response
        4. Apply confidence penalties based on data quality

        Returns
        -------
        dict with weather data, confidence, mode, and warnings.
        """
        api_error = None
        live_data = None
        is_cache_mode = False
        is_degraded = False

        # Step 1: Try live API
        if self.circuit_breaker.allow_request():
            try:
                import requests
                params = {"q": location, "appid": self.api_key, "units": "metric"}
                if lat and lon:
                    params = {"lat": lat, "lon": lon, "appid": self.api_key, "units": "metric"}

                response = requests.get(self.api_url, params=params, timeout=API_TIMEOUT_SECONDS)

                if response.status_code == 200:
                    data = response.json()
                    live_data = {
                        "temperature": data["main"]["temp"],
                        "humidity": data["main"]["humidity"],
                        "pressure": data["main"]["pressure"],
                        "wind_speed": data["wind"]["speed"],
                        "condition": data["weather"][0]["main"],
                        "location": location,
                        "timestamp": datetime.now().isoformat(),
                    }
                    self.circuit_breaker.record_success()
                    self.cache.set_mode(CacheMode.ONLINE)

                    # Cache the fresh data
                    self.cache.store_weather(location, live_data, confidence=1.0)
                else:
                    api_error = f"HTTP {response.status_code}"
                    self.circuit_breaker.record_failure()
            except Exception as e:
                api_error = str(e)
                self.circuit_breaker.record_failure()
        else:
            api_error = "circuit_breaker_open"
            is_degraded = True

        # Step 2: If API failed, try cache
        if live_data is None:
            cached = self.cache.get_cached_weather(location)
            if cached is not None:
                live_data = dict(cached.data)
                live_data["stale"] = cached.is_stale
                live_data["data_source"] = "cached"
                live_data["last_updated"] = cached.timestamp.isoformat()
                is_cache_mode = True
                self.cache.set_mode(CacheMode.CACHE_ONLY)
                logger.info(f"Using cached data for {location} (age: {(datetime.now() - cached.timestamp).total_seconds()/3600:.1f}h)")
            else:
                # Step 3: No data at all
                self.cache.set_mode(CacheMode.OFFLINE)
                live_data = {
                    "temperature": None,
                    "humidity": None,
                    "pressure": None,
                    "wind_speed": None,
                    "condition": "Unknown",
                    "location": location,
                    "timestamp": datetime.now().isoformat(),
                    "data_source": "no_data",
                }
                api_error = api_error or "no_cache"

        # Step 4: Apply confidence penalties
        data_age_hours = 0.0
        if "timestamp" in live_data:
            try:
                data_time = datetime.fromisoformat(live_data["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
                data_age_hours = (datetime.now() - data_time).total_seconds() / 3600
            except Exception:
                data_age_hours = 0.0

        missing_fields = sum(1 for f in ["temperature", "humidity", "pressure", "wind_speed"]
                           if live_data.get(f) is None)

        base_confidence = 1.0 if not is_cache_mode else 0.5
        confidence, penalty_reasons = self.confidence_system.apply_penalty(
            base_confidence=base_confidence,
            data_age_hours=data_age_hours,
            missing_fields=missing_fields,
            is_cache_mode=is_cache_mode,
            is_degraded=is_degraded,
        )

        # Build response
        response = dict(live_data)
        response.update({
            "location": location,
            "mode": self.cache.get_mode().value,
            "confidence": round(confidence, 4),
            "confidence_label": self.confidence_system.get_confidence_label(confidence),
            "confidence_penalty_reasons": penalty_reasons,
            "data_age_hours": round(data_age_hours, 2),
            "missing_fields": missing_fields,
            "api_error": api_error,
            "circuit_breaker_state": self.circuit_breaker.state,
            "cache_stats": self.cache.get_cache_stats(),
        })

        # Add stale data warning banner
        if is_cache_mode or data_age_hours > 1.0:
            response["stale_data_warning"] = (
                f"⚠️ STALE DATA WARNING: Weather data is {data_age_hours:.1f} hours old. "
                f"System is in {self.cache.get_mode().value} mode. "
                f"Confidence reduced to {confidence:.1%}."
            )

        if is_cache_mode:
            response["cache_mode_banner"] = (
                "📡 CACHE MODE ACTIVE: Internet/API unavailable. "
                "Serving predictions from last known state. "
                "Connect to the internet for fresh data."
            )

        return response

    def get_system_status(self) -> Dict[str, Any]:
        """Get the current system status."""
        return {
            "mode": self.cache.get_mode().value,
            "circuit_breaker_state": self.circuit_breaker.state,
            "cache_stats": self.cache.get_cache_stats(),
        }


# ============================================================================
# Integration: Hardened Weather Pipeline
# ============================================================================

def build_hardened_weather_response(
    location: str,
    degradation_manager: GracefulDegradationManager,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Build a complete hardened weather response.

    This is the recommended replacement for bare fetch_live_weather calls.
    """
    return degradation_manager.fetch_weather_with_degradation(location, lat, lon)
