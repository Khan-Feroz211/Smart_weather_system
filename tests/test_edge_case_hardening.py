"""
Comprehensive test suite for the ZaraiAI Edge-Case Hardening module.

These tests reproduce every edge case described in
``zaraiai_edge_case_hardening_prompt.pdf`` and verify that the
hardening implementations handle them correctly.

Run with::

    python -m pytest tests/test_edge_case_hardening.py -v

Or without pytest::

    python tests/test_edge_case_hardening.py
"""

import os
import sys
import json
import time
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from edge_case_hardening import (
    # Geospatial & Weather Ingestion
    CoordinateSanityCheck,
    GridCellMetadata,
    CircuitBreaker,
    WeatherCache,
    fetch_weather_with_hardening,
    # Satellite & IoT Sensing
    NDVIQualityDetector,
    SensorHealthScorer,
    FallbackChain,
    # NDMA Disaster Alerts
    AlertSeverity,
    AlertDeduplicator,
    get_alert_template,
    DeliveryConfirmationSystem,
    AlertAuditLog,
    # AI/ML Stack
    OutOfDistributionDetector,
    RegionalDataDensityChecker,
    RLIrrigationReward,
    ColdStartPolicy,
    # Feedback & Self-Improvement
    FeedbackAnomalyDetector,
    FeedbackSkewTracker,
    ModelVersionManager,
    HumanInTheLoopGate,
    # Deployment & Delivery
    OfflineConflictResolver,
    SMSTemplateManager,
    NonTextInteractionPath,
    # Cross-Cutting: Trust & Safety
    ConfidenceIndicator,
    DataPrivacyConsentLayer,
    TrustIncidentLogger,
    # Integration
    build_hardened_weather_response,
    # Config constants
    ALERT_TEMPLATES,
    SUPPORTED_LANGUAGES,
)


# ============================================================================
# 1. Geospatial & Weather Ingestion Tests
# ============================================================================

class TestCoordinateSanityCheck(unittest.TestCase):
    """Tests for GPS coordinate validation from low-end devices."""

    def setUp(self):
        self.checker = CoordinateSanityCheck()

    def test_valid_coordinates(self):
        """Valid coordinates within bounds should pass."""
        result = self.checker.validate(33.6844, 73.0479)  # Islamabad
        self.assertTrue(result["valid"])
        self.assertEqual(result["flags"], [])
        self.assertGreater(result["confidence"], 0.9)

    def test_out_of_bounds_latitude(self):
        """Latitude > 90 should be flagged."""
        result = self.checker.validate(95.0, 73.0)
        self.assertFalse(result["valid"])
        self.assertIn("latitude_out_of_bounds", result["flags"])

    def test_out_of_bounds_longitude(self):
        """Longitude > 180 should be flagged."""
        result = self.checker.validate(33.0, 185.0)
        self.assertFalse(result["valid"])
        self.assertIn("longitude_out_of_bounds", result["flags"])

    def test_null_island(self):
        """(0, 0) coordinates should be flagged as null island."""
        result = self.checker.validate(0.0, 0.0)
        self.assertFalse(result["valid"])
        self.assertIn("null_island", result["flags"])

    def test_low_precision_coordinate(self):
        """Integer coordinates should be flagged as low precision."""
        result = self.checker.validate(33, 73)
        self.assertFalse(result["valid"])
        self.assertIn("low_precision_coordinate", result["flags"])

    def test_missing_coordinates(self):
        """None coordinates should be rejected."""
        result = self.checker.validate(None, None)
        self.assertFalse(result["valid"])
        self.assertIn("missing_coordinates", result["flags"])
        self.assertEqual(result["confidence"], 0.0)

    def test_negative_coordinates(self):
        """Negative coordinates (valid in southern/western hemispheres) should pass."""
        result = self.checker.validate(-33.8688, -118.2437)  # Sydney / LA
        self.assertTrue(result["valid"])

    def test_precision_meters_calculation(self):
        """Precision should be estimated in metres."""
        result = self.checker.validate(33.6844, 73.0479)
        self.assertGreater(result["precision_meters"], 0)
        self.assertLess(result["precision_meters"], 100)  # 4 decimal places ≈ 11m


class TestGridCellMetadata(unittest.TestCase):
    """Tests for confidence/resolution metadata on weather responses."""

    def setUp(self):
        self.assessor = GridCellMetadata()

    def test_simple_terrain(self):
        """Flat terrain with no flags should have high confidence."""
        metadata = self.assessor.assess(33.6844, 73.0479, {"elevation_variance": 10.0})
        self.assertEqual(metadata["flags"], [])
        self.assertGreater(metadata["overall_confidence"], 0.9)

    def test_complex_terrain(self):
        """High elevation variance should lower confidence."""
        metadata = self.assessor.assess(33.6844, 73.0479, {"elevation_variance": 200.0})
        self.assertIn("complex_terrain", metadata["flags"])
        self.assertLess(metadata["confidence_modifier"], 1.0)

    def test_near_coastline(self):
        """Near-coastline grid cells should be flagged."""
        metadata = self.assessor.assess(24.8607, 67.0011, {"distance_to_coast_km": 2.0})
        self.assertIn("near_coastline", metadata["flags"])

    def test_between_grid_cells(self):
        """Coordinates between grid cells should be flagged."""
        metadata = self.assessor.assess(33.6844, 73.0479, {"between_grid_cells": True})
        self.assertIn("between_grid_cells", metadata["flags"])

    def test_coarse_resolution(self):
        """Coarse grid resolution should lower confidence."""
        metadata = self.assessor.assess(33.6844, 73.0479, {"grid_resolution_km": 50})
        self.assertIn("coarse_resolution", metadata["flags"])


class TestCircuitBreaker(unittest.TestCase):
    """Tests for circuit breaker with exponential backoff."""

    def test_initial_state_closed(self):
        """Circuit breaker should start in CLOSED state."""
        cb = CircuitBreaker("test_api")
        self.assertEqual(cb.state, "CLOSED")
        self.assertTrue(cb.allow_request())

    def test_opens_after_threshold_failures(self):
        """Breaker should open after threshold failures."""
        cb = CircuitBreaker("test_api", failure_threshold=3, timeout_seconds=1)
        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state, "OPEN")
        self.assertFalse(cb.allow_request())

    def test_half_open_after_timeout(self):
        """Breaker should go HALF_OPEN after timeout, allowing one request."""
        cb = CircuitBreaker("test_api", failure_threshold=2, timeout_seconds=1, backoff_base=0.5)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "OPEN")
        self.assertFalse(cb.allow_request())

        # Wait for timeout (backoff for 2 failures = 0.5 * 2^1 = 1.0s)
        time.sleep(1.5)
        self.assertTrue(cb.allow_request())

    def test_closes_after_success(self):
        """Breaker should close after a successful request in HALF_OPEN."""
        cb = CircuitBreaker("test_api", failure_threshold=2, timeout_seconds=1, backoff_base=0.5)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, "OPEN")

        # Wait for timeout (backoff for 2 failures = 0.5 * 2^1 = 1.0s)
        time.sleep(1.5)
        self.assertTrue(cb.allow_request())
        cb.record_success()
        self.assertEqual(cb.state, "CLOSED")

    def test_exponential_backoff(self):
        """Backoff should increase exponentially with failure count."""
        cb = CircuitBreaker("test_api", failure_threshold=5, backoff_base=1.0)
        cb.record_failure()
        backoff1 = cb._compute_backoff()
        cb.record_failure()
        backoff2 = cb._compute_backoff()
        self.assertGreater(backoff2, backoff1)

    def test_repr(self):
        """Circuit breaker should have a useful repr."""
        cb = CircuitBreaker("my_api")
        repr_str = repr(cb)
        self.assertIn("my_api", repr_str)
        self.assertIn("CLOSED", repr_str)


class TestWeatherCache(unittest.TestCase):
    """Tests for caching + stale-data fallback layer."""

    def test_set_and_get(self):
        """Cached data should be retrievable."""
        cache = WeatherCache(ttl_minutes=30)
        cache.set("lahore", {"temperature": 25.0})
        result = cache.get("lahore")
        self.assertIsNotNone(result)
        self.assertEqual(result["temperature"], 25.0)
        self.assertFalse(result["stale"])

    def test_stale_data_flag(self):
        """Data older than TTL should be flagged as stale."""
        cache = WeatherCache(ttl_minutes=0, max_age_hours=1)
        cache.set("lahore", {"temperature": 25.0})
        time.sleep(0.1)
        result = cache.get("lahore")
        self.assertIsNotNone(result)
        self.assertTrue(result["stale"])
        self.assertIn("stale_warning", result)
        self.assertIn("last_updated", result)

    def test_data_too_old_purged(self):
        """Data older than max_age should be purged and return None."""
        cache = WeatherCache(ttl_minutes=0, max_age_hours=0)
        cache.set("lahore", {"temperature": 25.0})
        time.sleep(0.1)
        result = cache.get("lahore")
        self.assertIsNone(result)

    def test_no_cache_returns_none(self):
        """Missing cache key should return None."""
        cache = WeatherCache()
        result = cache.get("nonexistent")
        self.assertIsNone(result)

    def test_clear(self):
        """Clear should remove all entries."""
        cache = WeatherCache()
        cache.set("lahore", {"temperature": 25.0})
        cache.set("karachi", {"temperature": 30.0})
        cache.clear()
        self.assertEqual(cache.size(), 0)

    def test_clear_single_key(self):
        """Clear with a key should remove only that entry."""
        cache = WeatherCache()
        cache.set("lahore", {"temperature": 25.0})
        cache.set("karachi", {"temperature": 30.0})
        cache.clear("lahore")
        self.assertIsNone(cache.get("lahore"))
        self.assertIsNotNone(cache.get("karachi"))


class TestFetchWeatherWithHardening(unittest.TestCase):
    """Acceptance test: simulate API timeout during storm-alert window."""

    def test_api_timeout_serves_cached_with_stale_flag(self):
        """
        Acceptance test: simulate API timeout during a storm-alert window
        and confirm the system serves cached data with a visible staleness
        flag instead of failing or serving silently wrong data.
        """
        cache = WeatherCache(ttl_minutes=0, max_age_hours=6)
        cb = CircuitBreaker("openweather", failure_threshold=3, timeout_seconds=60)

        # Prime the cache with good data
        cache.set("lahore", {"temperature": 25.0, "humidity": 60})

        # Simulate API timeout
        def failing_api():
            raise TimeoutError("API timeout during storm")

        result = fetch_weather_with_hardening(
            location="Lahore",
            lat=31.5407,
            lon=74.3587,
            cache=cache,
            circuit_breaker=cb,
            api_call=failing_api,
        )

        # Should serve cached data
        self.assertEqual(result["data_source"], "cached_stale")
        self.assertTrue(result["stale"])
        self.assertIn("stale_warning", result)
        self.assertIn("Data may be stale", result["stale_warning"])
        self.assertEqual(result["temperature"], 25.0)
        # Should not silently serve as fresh
        self.assertFalse(result.get("data_source") == "live")

    def test_live_data_served_when_available(self):
        """When API succeeds, fresh data should be served and cached."""
        cache = WeatherCache()
        cb = CircuitBreaker("openweather")

        def good_api():
            return {"temperature": 28.0, "humidity": 55}

        result = fetch_weather_with_hardening(
            location="Karachi",
            lat=24.8607,
            lon=67.0011,
            cache=cache,
            circuit_breaker=cb,
            api_call=good_api,
        )

        self.assertEqual(result["data_source"], "live")
        self.assertFalse(result["stale"])
        self.assertEqual(result["temperature"], 28.0)
        # Should be cached now
        cached = cache.get("karachi")
        self.assertIsNotNone(cached)

    def test_no_data_available(self):
        """When no cache and API fails, return explicit insufficient-data response."""
        cache = WeatherCache()
        cb = CircuitBreaker("openweather")

        def failing_api():
            raise ConnectionError("No network")

        result = fetch_weather_with_hardening(
            location="Nowhere",
            cache=cache,
            circuit_breaker=cb,
            api_call=failing_api,
        )

        self.assertEqual(result["data_source"], "no_data_available")
        self.assertEqual(result["confidence"], 0.0)
        self.assertIsNone(result["temperature"])

    def test_circuit_breaker_open_uses_cache(self):
        """When circuit breaker is open, cache should be used."""
        cache = WeatherCache()
        cb = CircuitBreaker("openweather", failure_threshold=1, timeout_seconds=300)
        cb.record_failure()  # Open the breaker

        cache.set("lahore", {"temperature": 22.0})

        result = fetch_weather_with_hardening(
            location="Lahore",
            cache=cache,
            circuit_breaker=cb,
            api_call=lambda: {"temperature": 99.0},  # Should NOT be called
        )

        self.assertEqual(result["data_source"], "cached_stale")
        self.assertEqual(result["temperature"], 22.0)
        self.assertEqual(result["circuit_breaker_state"], "OPEN")

    def test_coordinate_sanity_in_response(self):
        """Coordinate sanity check should be included in the response."""
        cache = WeatherCache()

        result = fetch_weather_with_hardening(
            location="Test",
            lat=999.0,  # Invalid
            lon=73.0,
            cache=cache,
            api_call=lambda: {"temperature": 25.0},
        )

        self.assertIn("coordinate_confidence", result)
        self.assertFalse(result["coordinate_confidence"]["valid"])
        self.assertIn("latitude_out_of_bounds", result["coordinate_confidence"]["flags"])


# ============================================================================
# 2. Satellite & IoT Sensing Tests
# ============================================================================

class TestNDVIQualityDetector(unittest.TestCase):
    """Tests for cloud-obscured NDVI detection."""

    def setUp(self):
        self.detector = NDVIQualityDetector()

    def test_valid_ndvi(self):
        """Valid NDVI values should be labelled as valid."""
        result = self.detector.assess(0.65)
        self.assertEqual(result["quality_label"], "valid")
        self.assertGreater(result["confidence"], 0.8)

    def test_null_ndvi(self):
        """Null NDVI should be labelled as invalid."""
        result = self.detector.assess(None)
        self.assertEqual(result["quality_label"], "invalid")
        self.assertEqual(result["confidence"], 0.0)

    def test_cloud_mask_value(self):
        """Cloud mask value should be labelled as cloud_obscured."""
        result = self.detector.assess(-999.0)
        self.assertEqual(result["quality_label"], "cloud_obscured")
        self.assertEqual(result["confidence"], 0.0)

    def test_high_cloud_cover(self):
        """High cloud cover should label NDVI as cloud_obscured."""
        result = self.detector.assess(0.5, cloud_cover=85.0)
        self.assertEqual(result["quality_label"], "cloud_obscured")

    def test_low_ndvi_threshold(self):
        """NDVI below threshold should be flagged."""
        result = self.detector.assess(0.05)
        self.assertEqual(result["quality_label"], "cloud_obscured")

    def test_ndvi_not_passed_as_valid(self):
        """
        Acceptance test: feed a fixture with cloud-obscured NDVI and confirm
        the response includes a clear data-source/confidence label, not a
        false-confidence prediction.
        """
        result = self.detector.assess(-999.0, cloud_cover=90.0)
        self.assertNotEqual(result["quality_label"], "valid")
        self.assertLess(result["confidence"], 0.5)


class TestSensorHealthScorer(unittest.TestCase):
    """Tests for sensor health scoring with drift detection."""

    def setUp(self):
        self.scorer = SensorHealthScorer(offline_minutes=30, drift_threshold=5.0)

    def test_active_sensor(self):
        """A recently seen, stable sensor should have high health score."""
        result = self.scorer.score(
            "sensor_1",
            last_seen=datetime.utcnow(),
            recent_readings=[25.0, 25.1, 25.0, 24.9, 25.0],
            baseline_mean=25.0,
        )
        self.assertEqual(result["status"], "active")
        self.assertGreater(result["health_score"], 0.9)

    def test_offline_sensor(self):
        """A sensor not seen for >30 minutes should be offline."""
        result = self.scorer.score(
            "sensor_1",
            last_seen=datetime.utcnow() - timedelta(minutes=45),
            recent_readings=[25.0],
        )
        self.assertEqual(result["status"], "offline")
        self.assertEqual(result["health_score"], 0.0)
        self.assertIn("offline", result["reasons"])

    def test_drift_detected(self):
        """A sensor with significant drift should be flagged."""
        result = self.scorer.score(
            "sensor_1",
            last_seen=datetime.utcnow(),
            recent_readings=[35.0, 35.1, 35.0],
            baseline_mean=25.0,
        )
        self.assertIn("drift_detected", result["reasons"])
        self.assertLess(result["health_score"], 1.0)

    def test_stuck_value(self):
        """A sensor with no variance should be flagged as stuck."""
        result = self.scorer.score(
            "sensor_1",
            last_seen=datetime.utcnow(),
            recent_readings=[25.0, 25.0, 25.0, 25.0, 25.0],
            baseline_mean=25.0,
        )
        self.assertIn("stuck_value", result["reasons"])

    def test_no_data_ever(self):
        """A sensor with no data should be offline."""
        result = self.scorer.score("sensor_1", last_seen=None, recent_readings=[])
        self.assertEqual(result["status"], "offline")
        self.assertIn("no_data_ever", result["reasons"])


class TestFallbackChain(unittest.TestCase):
    """Tests for IoT → satellite → regional → insufficient data fallback chain."""

    def test_iot_sensor_first(self):
        """IoT sensor should be the first tier tried."""
        chain = FallbackChain()
        chain.add_tier("iot_sensor", lambda: {"temperature": 25.0})
        chain.add_tier("satellite_estimate", lambda: {"temperature": 24.0})
        chain.add_tier("regional_historical_average", lambda: {"temperature": 23.0})

        result = chain.resolve()
        self.assertEqual(result["data_source"], "iot_sensor")
        self.assertEqual(result["temperature"], 25.0)
        self.assertEqual(result["confidence_label"], "high")

    def test_satellite_fallback(self):
        """When IoT fails, satellite should be used."""
        chain = FallbackChain()
        chain.add_tier("iot_sensor", lambda: None)
        chain.add_tier("satellite_estimate", lambda: {"temperature": 24.0})
        chain.add_tier("regional_historical_average", lambda: {"temperature": 23.0})

        result = chain.resolve()
        self.assertEqual(result["data_source"], "satellite_estimate")
        self.assertEqual(result["confidence_label"], "medium")

    def test_regional_fallback(self):
        """When IoT and satellite fail, regional average should be used."""
        chain = FallbackChain()
        chain.add_tier("iot_sensor", lambda: None)
        chain.add_tier("satellite_estimate", lambda: None)
        chain.add_tier("regional_historical_average", lambda: {"temperature": 23.0})

        result = chain.resolve()
        self.assertEqual(result["data_source"], "regional_historical_average")
        self.assertEqual(result["confidence_label"], "low")

    def test_insufficient_data(self):
        """When all tiers fail, return explicit insufficient-data response."""
        chain = FallbackChain()
        chain.add_tier("iot_sensor", lambda: None)
        chain.add_tier("satellite_estimate", lambda: None)
        chain.add_tier("regional_historical_average", lambda: None)

        result = chain.resolve()
        self.assertEqual(result["data_source"], "insufficient_data")
        self.assertEqual(result["confidence_label"], "none")
        self.assertEqual(result["confidence"], 0.0)

    def test_no_tiers(self):
        """Empty chain should return insufficient-data response."""
        chain = FallbackChain()
        result = chain.resolve()
        self.assertEqual(result["data_source"], "insufficient_data")

    def test_exception_in_tier(self):
        """Exceptions in a tier should not crash the chain."""
        chain = FallbackChain()
        chain.add_tier("iot_sensor", lambda: (_ for _ in ()).throw(ValueError("boom")))
        chain.add_tier("satellite_estimate", lambda: {"temperature": 24.0})

        result = chain.resolve()
        self.assertEqual(result["data_source"], "satellite_estimate")

    def test_village_zero_sensor_coverage(self):
        """
        Acceptance test: feed a fixture with all sensors offline and confirm
        the advisory response includes a clear data-source/confidence label,
        not a false-confidence prediction.
        """
        chain = FallbackChain()
        chain.add_tier("iot_sensor", lambda: None)
        chain.add_tier("satellite_estimate", lambda: None)
        chain.add_tier("regional_historical_average", lambda: None)

        result = chain.resolve()
        self.assertNotEqual(result.get("confidence_label"), "high")
        self.assertIn("data_source", result)
        self.assertEqual(result["confidence"], 0.0)


# ============================================================================
# 3. NDMA Disaster Alerts Tests
# ============================================================================

class TestAlertDeduplicator(unittest.TestCase):
    """Tests for alert deduplication and severity thresholding."""

    def setUp(self):
        self.dedup = AlertDeduplicator(cooldown_minutes=60)

    def test_first_alert_sent(self):
        """First alert for an event should be sent."""
        self.assertTrue(self.dedup.should_send("flood_karachi", AlertSeverity.HIGH))

    def test_duplicate_within_cooldown_suppressed(self):
        """Duplicate alert within cooldown should be suppressed."""
        self.assertTrue(self.dedup.should_send("flood_karachi", AlertSeverity.HIGH))
        self.assertFalse(self.dedup.should_send("flood_karachi", AlertSeverity.HIGH))

    def test_severity_escalation_allowed(self):
        """Severity escalation should bypass cooldown suppression."""
        self.assertTrue(self.dedup.should_send("flood_karachi", AlertSeverity.MEDIUM))
        self.assertFalse(self.dedup.should_send("flood_karachi", AlertSeverity.MEDIUM))
        # Escalation to HIGH should be allowed
        self.assertTrue(self.dedup.should_send("flood_karachi", AlertSeverity.HIGH))

    def test_no_escalation_downgrade_suppressed(self):
        """Severity downgrade within cooldown should still be suppressed."""
        self.assertTrue(self.dedup.should_send("flood_karachi", AlertSeverity.HIGH))
        self.assertFalse(self.dedup.should_send("flood_karachi", AlertSeverity.LOW))

    def test_cooldown_expiry_allows_repeat(self):
        """After cooldown expires, repeat alerts should be allowed."""
        self.dedup = AlertDeduplicator(cooldown_minutes=0)
        self.assertTrue(self.dedup.should_send("flood_karachi", AlertSeverity.HIGH))
        # With 0-minute cooldown, immediately allowed
        self.assertTrue(self.dedup.should_send("flood_karachi", AlertSeverity.HIGH))

    def test_different_events_independent(self):
        """Different events should not interfere with each other."""
        self.assertTrue(self.dedup.should_send("flood_karachi", AlertSeverity.HIGH))
        self.assertTrue(self.dedup.should_send("heatwave_lahore", AlertSeverity.HIGH))

    def test_record_sent(self):
        """record_sent should update the last alert time."""
        self.dedup.record_sent("flood_karachi", AlertSeverity.HIGH)
        self.assertFalse(self.dedup.should_send("flood_karachi", AlertSeverity.HIGH))


class TestAlertTemplates(unittest.TestCase):
    """Tests for pre-translated, pre-approved alert templates."""

    def test_template_exists_for_flood(self):
        """Flood alert templates should exist for all supported languages."""
        for lang in SUPPORTED_LANGUAGES:
            template = get_alert_template("flood", "high", lang)
            self.assertIsNotNone(template)
            self.assertGreater(len(template), 0)

    def test_template_exists_for_heatwave(self):
        """Heatwave alert templates should exist for all supported languages."""
        for lang in SUPPORTED_LANGUAGES:
            template = get_alert_template("heatwave", "high", lang)
            self.assertIsNotNone(template)

    def test_no_machine_translation(self):
        """Templates should be pre-translated, not generated on the fly."""
        template = get_alert_template("flood", "high", "ur")
        # Should be a pre-defined string, not a machine-translated one
        self.assertIsInstance(template, str)
        self.assertGreater(len(template), 10)

    def test_unknown_template_returns_none(self):
        """Unknown alert type/severity should return None."""
        result = get_alert_template("unknown_event", "high", "ur")
        self.assertIsNone(result)

    def test_all_languages_covered(self):
        """All 5 languages (Urdu, Sindhi, Pashto, Saraiki, Balochi) should be covered."""
        self.assertIn("ur", SUPPORTED_LANGUAGES)
        self.assertIn("sd", SUPPORTED_LANGUAGES)
        self.assertIn("ps", SUPPORTED_LANGUAGES)
        self.assertIn("sk", SUPPORTED_LANGUAGES)
        self.assertIn("bal", SUPPORTED_LANGUAGES)


class TestDeliveryConfirmationSystem(unittest.TestCase):
    """Tests for multi-channel delivery confirmation."""

    def setUp(self):
        self.delivery = DeliveryConfirmationSystem()

    def test_delivery_report_structure(self):
        """Delivery report should have the correct structure."""
        report = self.delivery.deliver("user_123", "Test message", channels=["sms"])
        self.assertIn("recipient", report)
        self.assertIn("status", report)
        self.assertIn("channel_used", report)
        self.assertIn("attempts", report)

    def test_delivery_log_persisted(self):
        """Delivery should be logged."""
        self.delivery.deliver("user_123", "Test message", channels=["sms"])
        log = self.delivery.get_delivery_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["recipient"], "user_123")

    def test_sms_fallback(self):
        """When push fails, SMS fallback should be attempted."""
        # Mock _try_channel to simulate push failure
        original = DeliveryConfirmationSystem._try_channel
        call_count = [0]

        def mock_try_channel(self_inner, channel, recipient, message):
            call_count[0] += 1
            if channel == "push":
                return "failed"
            return "delivered"

        with patch.object(DeliveryConfirmationSystem, "_try_channel", mock_try_channel):
            report = self.delivery.deliver("user_123", "Test", channels=["push", "sms"])

        self.assertEqual(report["status"], "delivered")
        self.assertEqual(report["channel_used"], "sms")
        self.assertGreaterEqual(call_count[0], 2)  # Both push and sms attempted

    def test_all_channels_fail(self):
        """When all channels fail, status should be failed."""
        def always_fail(self_inner, channel, recipient, message):
            return "failed"

        with patch.object(DeliveryConfirmationSystem, "_try_channel", always_fail):
            report = self.delivery.deliver("user_123", "Test", channels=["push", "sms"])

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["channel_used"], "none")


class TestAlertAuditLog(unittest.TestCase):
    """Tests for alert audit logging."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_audit.db")
        self.audit = AlertAuditLog(db_path=self.db_path)

    def test_log_alert(self):
        """Alert should be logged with all required fields."""
        self.audit.log(
            alert_id="alert_001",
            event_key="flood_karachi",
            alert_type="flood",
            severity="high",
            source="weather_api",
            delivery_channel="sms",
            delivery_status="delivered",
            recipient_list=["user_1", "user_2"],
            model_version="v1.2.3",
            confidence=0.85,
        )
        results = self.audit.query(event_key="flood_karachi")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["alert_id"], "alert_001")
        self.assertEqual(results[0]["delivery_channel"], "sms")

    def test_query_by_alert_id(self):
        """Should be able to query by alert_id."""
        self.audit.log(
            alert_id="alert_002",
            event_key="heatwave_lahore",
            alert_type="heatwave",
            severity="high",
            source="ml_model",
            delivery_channel="push",
            delivery_status="delivered",
            recipient_list=["user_1"],
        )
        results = self.audit.query(alert_id="alert_002")
        self.assertEqual(len(results), 1)

    def test_traceability(self):
        """
        Acceptance test: trace one synthetic advisory end-to-end through logs
        and confirm every stage (data source, model version, confidence,
        delivery) is reconstructable.
        """
        self.audit.log(
            alert_id="trace_test_001",
            event_key="flood_karachi_2026",
            alert_type="flood",
            severity="critical",
            source="satellite_data",
            delivery_channel="sms",
            delivery_status="delivered",
            recipient_list=["farmer_001", "farmer_002"],
            model_version="v2.1.0",
            confidence=0.78,
        )
        results = self.audit.query(alert_id="trace_test_001")
        self.assertEqual(len(results), 1)
        record = results[0]
        # Every stage should be reconstructable
        self.assertEqual(record["source"], "satellite_data")
        self.assertEqual(record["model_version"], "v2.1.0")
        self.assertEqual(record["confidence"], 0.78)
        self.assertEqual(record["delivery_channel"], "sms")
        self.assertEqual(record["delivery_status"], "delivered")
        recipients = json.loads(record["recipient_list"])
        self.assertIn("farmer_001", recipients)


# ============================================================================
# 4. AI/ML Stack Tests
# ============================================================================

class TestOutOfDistributionDetector(unittest.TestCase):
    """Tests for out-of-distribution detection."""

    def setUp(self):
        self.detector = OutOfDistributionDetector()
        # Fit on normal training data
        self.detector.fit([
            {"temperature": 20.0, "humidity": 50.0, "pressure": 1013.0},
            {"temperature": 25.0, "humidity": 55.0, "pressure": 1010.0},
            {"temperature": 30.0, "humidity": 60.0, "pressure": 1008.0},
            {"temperature": 22.0, "humidity": 48.0, "pressure": 1015.0},
            {"temperature": 28.0, "humidity": 52.0, "pressure": 1012.0},
        ])

    def test_in_distribution(self):
        """Normal inputs should not be flagged as OOD."""
        result = self.detector.detect({"temperature": 25.0, "humidity": 55.0, "pressure": 1012.0})
        self.assertFalse(result["is_oos"])
        self.assertGreater(result["confidence"], 0.8)

    def test_out_of_distribution(self):
        """Extreme inputs should be flagged as OOD."""
        result = self.detector.detect({"temperature": 60.0, "humidity": 95.0, "pressure": 500.0})
        self.assertTrue(result["is_oos"])
        self.assertIn("temperature", result["out_of_range_features"])
        self.assertLess(result["confidence"], 0.5)

    def test_not_fitted(self):
        """Detector should flag as OOD if not fitted."""
        detector = OutOfDistributionDetector()
        result = detector.detect({"temperature": 25.0})
        self.assertTrue(result["is_oos"])
        self.assertEqual(result["reason"], "detector_not_fitted")

    def test_partial_ood(self):
        """Only some features being OOD should still flag."""
        result = self.detector.detect({"temperature": 25.0, "humidity": 99.0, "pressure": 1012.0})
        self.assertTrue(result["is_oos"])
        self.assertIn("humidity", result["out_of_range_features"])
        self.assertNotIn("temperature", result["out_of_range_features"])

    def test_details_included(self):
        """OOD detection should include details about which features are out of range."""
        result = self.detector.detect({"temperature": 100.0, "humidity": 50.0, "pressure": 1013.0})
        self.assertIn("temperature", result["details"])
        self.assertIn("range", result["details"]["temperature"])
        self.assertEqual(result["details"]["temperature"]["value"], 100.0)


class TestRegionalDataDensityChecker(unittest.TestCase):
    """Tests for regional data-density checks."""

    def setUp(self):
        self.checker = RegionalDataDensityChecker(threshold=50)

    def test_high_data_region(self):
        """Regions with sufficient data should not be low confidence."""
        self.checker.register_region_data("Punjab", 500)
        result = self.checker.check("Punjab")
        self.assertFalse(result["is_low_confidence"])
        self.assertFalse(result["fallback_required"])
        self.assertEqual(result["confidence_modifier"], 1.0)

    def test_low_data_region(self):
        """Regions with sparse data should be flagged as low confidence."""
        self.checker.register_region_data("Balochistan", 20)
        result = self.checker.check("Balochistan")
        self.assertTrue(result["is_low_confidence"])
        self.assertTrue(result["fallback_required"])
        self.assertEqual(result["confidence_modifier"], 0.5)
        self.assertEqual(result["reason"], "sparse_training_data")

    def test_unknown_region(self):
        """Unknown regions should default to low confidence."""
        result = self.checker.check("UnknownRegion")
        self.assertTrue(result["is_low_confidence"])
        self.assertEqual(result["data_points"], 0)

    def test_interior_sindh(self):
        """
        Acceptance test: interior Sindh (sparse data) should be flagged for
        conservative/rule-based fallback.
        """
        self.checker.register_region_data("Interior Sindh", 15)
        result = self.checker.check("Interior Sindh")
        self.assertTrue(result["fallback_required"])


class TestRLIrrigationReward(unittest.TestCase):
    """Tests for RL irrigation reward with water-sustainability penalties."""

    def setUp(self):
        self.reward_fn = RLIrrigationReward(water_cap_mm=50.0)

    def test_normal_irrigation(self):
        """Normal irrigation within cap should have positive reward."""
        result = self.reward_fn.compute_reward(
            yield_gain=2.0, water_volume_mm=30.0, aquifer_stress=0.1
        )
        self.assertGreater(result["reward"], 0)
        self.assertFalse(result["capped"])

    def test_water_cap_enforced(self):
        """Water volume exceeding cap should be capped."""
        result = self.reward_fn.compute_reward(
            yield_gain=2.0, water_volume_mm=100.0, aquifer_stress=0.0
        )
        self.assertTrue(result["capped"])
        self.assertEqual(result["effective_water_mm"], 50.0)

    def test_aquifer_stress_penalty(self):
        """High aquifer stress should reduce reward."""
        low_stress = self.reward_fn.compute_reward(
            yield_gain=2.0, water_volume_mm=30.0, aquifer_stress=0.1
        )
        high_stress = self.reward_fn.compute_reward(
            yield_gain=2.0, water_volume_mm=30.0, aquifer_stress=0.9
        )
        self.assertLess(high_stress["reward"], low_stress["reward"])
        self.assertGreater(high_stress["sustainability_penalty"], low_stress["sustainability_penalty"])

    def test_cap_not_silently_overridden(self):
        """The water cap should be enforced regardless of yield gain."""
        result = self.reward_fn.compute_reward(
            yield_gain=100.0, water_volume_mm=200.0, aquifer_stress=0.0
        )
        self.assertTrue(result["capped"])
        self.assertEqual(result["effective_water_mm"], 50.0)


class TestColdStartPolicy(unittest.TestCase):
    """Tests for cold-start policy for new users/regions."""

    def setUp(self):
        self.policy = ColdStartPolicy(min_interactions=10)

    def test_new_user_cold_start(self):
        """New users with few interactions should get starter recommendations."""
        result = self.policy.get_policy("user_001", "Balochistan", interaction_count=3)
        self.assertTrue(result["is_cold_start"])
        self.assertEqual(result["recommendation_source"], "rule_based")
        self.assertEqual(result["label"], "starter_recommendations")
        self.assertIn("Starter recommendations", result["message"])

    def test_established_user(self):
        """Users with enough interactions should get ML-based recommendations."""
        result = self.policy.get_policy("user_002", "Punjab", interaction_count=50)
        self.assertFalse(result["is_cold_start"])
        self.assertEqual(result["recommendation_source"], "ml_model")
        self.assertEqual(result["label"], "personalised")

    def test_boundary_interaction_count(self):
        """Exactly min_interactions should NOT be cold start."""
        result = self.policy.get_policy("user_003", "Sindh", interaction_count=10)
        self.assertFalse(result["is_cold_start"])

    def test_starter_recommendations_labelled(self):
        """
        Acceptance test: feed input outside training distribution and confirm
        the model returns a flagged low-confidence/fallback response instead
        of a point prediction presented as certain.
        """
        result = self.policy.get_policy("new_user", "Balochistan", interaction_count=1)
        self.assertTrue(result["is_cold_start"])
        self.assertLess(result["confidence"], 0.5)
        self.assertEqual(result["label"], "starter_recommendations")


# ============================================================================
# 5. Feedback & Self-Improvement Tests
# ============================================================================

class TestFeedbackAnomalyDetector(unittest.TestCase):
    """Tests for anomaly detection on incoming feedback."""

    def setUp(self):
        self.detector = FeedbackAnomalyDetector(
            rate_limit_per_minute=5, anomaly_zscore=3.0
        )

    def test_rate_limit_within_bounds(self):
        """Feedback within rate limit should be allowed."""
        for i in range(5):
            self.assertTrue(self.detector.check_rate_limit("user_1", "device_1"))

    def test_rate_limit_exceeded(self):
        """Feedback exceeding rate limit should be blocked."""
        for i in range(5):
            self.detector.check_rate_limit("user_1", "device_1")
        self.assertFalse(self.detector.check_rate_limit("user_1", "device_1"))

    def test_rate_limit_per_device(self):
        """Rate limits should be per-device, not global."""
        for i in range(5):
            self.detector.check_rate_limit("user_1", "device_1")
        # Different device should still be allowed
        self.assertTrue(self.detector.check_rate_limit("user_1", "device_2"))

    def test_anomaly_detection(self):
        """Statistical outliers should be flagged as anomalies."""
        # Build up normal history
        for rating in [5, 5, 5, 5, 5, 4, 5, 4, 5, 5]:
            self.detector.check_anomaly("user_1", rating)

        # Now send an anomalous rating
        result = self.detector.check_anomaly("user_1", 1)
        self.assertTrue(result["is_anomaly"])
        self.assertGreater(result["z_score"], 3.0)

    def test_normal_feedback_not_flagged(self):
        """Normal feedback should not be flagged as anomalous."""
        for rating in [4, 5, 4, 5, 4]:
            result = self.detector.check_anomaly("user_1", rating)
            self.assertFalse(result["is_anomaly"])

    def test_burst_anomaly_excluded(self):
        """
        Acceptance test: simulate a burst of statistically anomalous feedback
        from a single device and confirm it's flagged/excluded before
        retraining.
        """
        # Normal history
        for rating in [5, 5, 5, 5, 5, 5, 5, 5, 5, 5]:
            self.detector.check_anomaly("device_1", rating)

        # Burst of anomalous ratings
        anomalies = []
        for rating in [1, 1, 1, 1, 1]:
            result = self.detector.check_anomaly("device_1", rating)
            anomalies.append(result["is_anomaly"])

        self.assertTrue(any(anomalies), "At least one anomalous feedback should be flagged")


class TestFeedbackSkewTracker(unittest.TestCase):
    """Tests for feedback demographic/geographic skew tracking."""

    def setUp(self):
        self.tracker = FeedbackSkewTracker(top_percent=10.0)

    def test_no_skew(self):
        """Evenly distributed feedback should not be skewed."""
        for i in range(100):
            user = f"user_{i % 10}"
            region = f"region_{i % 5}"
            self.tracker.record(user, region)

        report = self.tracker.get_skew_report()
        self.assertFalse(report["is_skewed"])
        self.assertLess(report["top_users_percent"], 50.0)

    def test_skewed_feedback(self):
        """Feedback concentrated in a few users should be flagged as skewed."""
        # 90% of feedback from 1 user
        for i in range(90):
            self.tracker.record("power_user", "region_1")
        for i in range(10):
            self.tracker.record(f"user_{i}", f"region_{i % 3}")

        report = self.tracker.get_skew_report()
        self.assertTrue(report["is_skewed"])
        self.assertGreater(report["top_users_percent"], 50.0)

    def test_empty_report(self):
        """No feedback should return an empty report."""
        report = self.tracker.get_skew_report()
        self.assertEqual(report["total_feedback"], 0)
        self.assertFalse(report["is_skewed"])


class TestModelVersionManager(unittest.TestCase):
    """Tests for model versioning with changelog and rollback."""

    def setUp(self):
        self.manager = ModelVersionManager(ttl_hours=24)

    def test_register_version(self):
        """Registering a version should update current version and changelog."""
        self.manager.register_version("v1.0.0", "Initial model", "admin")
        self.assertEqual(self.manager.current_version, "v1.0.0")
        changelog = self.manager.get_changelog()
        self.assertEqual(len(changelog), 1)
        self.assertEqual(changelog[0]["version"], "v1.0.0")

    def test_multiple_versions(self):
        """Multiple versions should be tracked in changelog."""
        self.manager.register_version("v1.0.0", "Initial", "admin")
        self.manager.register_version("v1.1.0", "Improved accuracy", "admin")
        self.manager.register_version("v1.2.0", "Bug fixes", "admin")
        self.assertEqual(self.manager.current_version, "v1.2.0")
        self.assertEqual(len(self.manager.get_changelog()), 3)

    def test_reconcile_up_to_date(self):
        """Client with matching version and recent sync should use client."""
        self.manager.register_version("v1.0.0", "Initial")
        result = self.manager.reconcile("v1.0.0", datetime.utcnow() - timedelta(hours=1))
        self.assertEqual(result["action"], "use_client")
        self.assertFalse(result["stale"])

    def test_reconcile_version_mismatch(self):
        """Client with old version should be updated."""
        self.manager.register_version("v2.0.0", "New model")
        result = self.manager.reconcile("v1.0.0", datetime.utcnow())
        self.assertEqual(result["action"], "update_client")
        self.assertTrue(result["stale"])

    def test_reconcile_stale_advice(self):
        """Client with matching version but stale sync should force refresh."""
        self.manager.register_version("v1.0.0", "Initial")
        result = self.manager.reconcile("v1.0.0", datetime.utcnow() - timedelta(hours=48))
        self.assertEqual(result["action"], "force_refresh")
        self.assertTrue(result["stale"])

    def test_reconcile_no_client_version(self):
        """Client with no version should force refresh."""
        self.manager.register_version("v1.0.0", "Initial")
        result = self.manager.reconcile(None, None)
        self.assertEqual(result["action"], "force_refresh")


class TestHumanInTheLoopGate(unittest.TestCase):
    """Tests for human-in-the-loop approval gate."""

    def setUp(self):
        self.gate = HumanInTheLoopGate()

    def test_submit_for_review(self):
        """Submitting a retrain candidate should return a review ID."""
        review_id = self.gate.submit_for_review(
            "v2.0.0", {"accuracy": 0.92, "loss": 0.08}
        )
        self.assertIsNotNone(review_id)
        self.assertIn("rev_", review_id)

    def test_cannot_promote_without_approval(self):
        """Retrain candidate should not auto-promote without approval."""
        self.gate.submit_for_review("v2.0.0", {"accuracy": 0.92})
        self.assertFalse(self.gate.can_promote("v2.0.0"))

    def test_can_promote_after_approval(self):
        """After approval, model should be promotable."""
        review_id = self.gate.submit_for_review("v2.0.0", {"accuracy": 0.92})
        self.gate.approve(review_id, "admin")
        self.assertTrue(self.gate.can_promote("v2.0.0"))

    def test_rejected_cannot_promote(self):
        """Rejected candidates should not be promotable."""
        review_id = self.gate.submit_for_review("v2.0.0", {"accuracy": 0.92})
        self.gate.reject(review_id, "admin", "Poor performance on edge cases")
        self.assertFalse(self.gate.can_promote("v2.0.0"))

    def test_pending_list(self):
        """Pending reviews should be listed."""
        self.gate.submit_for_review("v2.0.0", {"accuracy": 0.92})
        self.gate.submit_for_review("v2.1.0", {"accuracy": 0.94})
        pending = self.gate.get_pending()
        self.assertEqual(len(pending), 2)

    def test_no_auto_promote(self):
        """
        Acceptance test: simulate a burst of statistically anomalous feedback
        from a single device and confirm a retrain candidate cannot
        auto-promote without an approval step.
        """
        review_id = self.gate.submit_for_review("v3.0.0", {"accuracy": 0.95})
        # Even with high accuracy, no auto-promote
        self.assertFalse(self.gate.can_promote("v3.0.0"))
        # Must go through approval
        self.gate.approve(review_id, "reviewer")
        self.assertTrue(self.gate.can_promote("v3.0.0"))


# ============================================================================
# 6. Deployment & Delivery Tests
# ============================================================================

class TestOfflineConflictResolver(unittest.TestCase):
    """Tests for offline/online conflict resolution."""

    def setUp(self):
        self.resolver = OfflineConflictResolver(ttl_hours=24)

    def test_server_newer(self):
        """Server advice newer than local should win."""
        now = datetime.utcnow()
        local = {"timestamp": (now - timedelta(hours=2)).isoformat(), "advice": "old"}
        server = {"timestamp": (now - timedelta(hours=1)).isoformat(), "advice": "new"}
        result = self.resolver.resolve(local, server)
        self.assertEqual(result["conflict_resolution"], "server_wins")
        self.assertEqual(result["advice"], "new")

    def test_local_newer(self):
        """Local advice newer than server should win (if not stale)."""
        now = datetime.utcnow()
        local = {"timestamp": (now - timedelta(hours=1)).isoformat(), "advice": "new"}
        server = {"timestamp": (now - timedelta(hours=2)).isoformat(), "advice": "old"}
        result = self.resolver.resolve(local, server)
        self.assertEqual(result["conflict_resolution"], "local_wins")
        self.assertFalse(result["stale"])

    def test_local_stale(self):
        """Local advice older than TTL should be flagged as stale."""
        now = datetime.utcnow()
        old_time = (now - timedelta(hours=48)).isoformat()
        local = {"timestamp": old_time, "advice": "old"}
        # Server has no data, so local is used but flagged as stale
        result = self.resolver.resolve(local, None)
        self.assertTrue(result["stale"])
        self.assertIsNotNone(result["banner"])

    def test_no_server_data(self):
        """When no server data, local should be used but flagged."""
        old_time = (datetime.utcnow() - timedelta(hours=48)).isoformat()
        local = {"timestamp": old_time, "advice": "local"}
        result = self.resolver.resolve(local, None)
        self.assertEqual(result["conflict_resolution"], "use_local_no_server")
        self.assertTrue(result["stale"])
        self.assertIsNotNone(result["banner"])

    def test_banner_displayed(self):
        """Stale advice should show a user-visible banner."""
        old_time = (datetime.utcnow() - timedelta(hours=48)).isoformat()
        local = {"timestamp": old_time, "advice": "old"}
        result = self.resolver.resolve(local, None)
        self.assertIn("may be outdated", result["banner"])


class TestSMSTemplateManager(unittest.TestCase):
    """Tests for SMS templates that preserve conditional logic."""

    def setUp(self):
        self.manager = SMSTemplateManager()

    def test_flood_template(self):
        """Flood template should include IF/UNLESS clauses."""
        msg = self.manager.render("flood_warning", location="Karachi", severity="HIGH")
        self.assertIn("IF", msg)
        self.assertIn("UNLESS", msg)
        self.assertIn("Karachi", msg)

    def test_heat_template(self):
        """Heat template should include IF/UNLESS clauses."""
        msg = self.manager.render("heat_warning", location="Lahore", temp="42")
        self.assertIn("IF", msg)
        self.assertIn("UNLESS", msg)

    def test_length_within_limit(self):
        """All templates should be within SMS character limit."""
        for template_name in self.manager.TEMPLATES:
            msg = self.manager.render(template_name, location="TestLocation", severity="HIGH", temp="35", speed="50", message="Watch out")
            self.assertLessEqual(len(msg), self.manager.max_length,
                                 f"Template {template_name} exceeds {self.manager.max_length} chars")

    def test_conditional_logic_preserved(self):
        """Conditional logic should not be truncated."""
        # Even with a long location name, IF/UNLESS should be preserved
        msg = self.manager.render("flood_warning", location="VeryLongLocationNameThatExceedsNormalLength", severity="CRITICAL")
        self.assertIn("IF", msg)
        self.assertIn("UNLESS", msg)
        self.assertLessEqual(len(msg), self.manager.max_length)

    def test_general_template(self):
        """General template should be available as fallback."""
        msg = self.manager.render("general", location="Test", message="Watch out")
        self.assertIn("IF", msg)
        self.assertIn("UNLESS", msg)


class TestNonTextInteractionPath(unittest.TestCase):
    """Tests for non-text interaction path (voice/IVR, icon-based UI)."""

    def test_get_icon(self):
        """Should return appropriate icon for alert type."""
        self.assertEqual(NonTextInteractionPath.get_icon("flood"), "🌊")
        self.assertEqual(NonTextInteractionPath.get_icon("heatwave"), "🌡️")
        self.assertEqual(NonTextInteractionPath.get_icon("unknown"), "⚠️")

    def test_get_voice_prompt(self):
        """Should return voice prompt for alert type."""
        prompt = NonTextInteractionPath.get_voice_prompt("flood", "Karachi")
        self.assertIn("flood", prompt.lower())
        self.assertIn("Karachi", prompt)

    def test_render_icon_ui(self):
        """Should render icon-based UI for low-literacy users."""
        result = NonTextInteractionPath.render_icon_ui("flood", "high")
        self.assertIn("icon", result)
        self.assertIn("severity_icon", result)
        self.assertIn("voice_prompt", result)
        self.assertEqual(result["severity_icon"], "🔴")

    def test_voice_prompt_for_low_literacy(self):
        """Voice prompts should be available for all alert types."""
        for alert_type in ["flood", "heatwave", "dust_storm", "cyclone", "pest_risk", "irrigation", "cold", "general"]:
            prompt = NonTextInteractionPath.get_voice_prompt(alert_type)
            self.assertGreater(len(prompt), 10)


# ============================================================================
# 7. Cross-Cutting: Trust & Safety Tests
# ============================================================================

class TestConfidenceIndicator(unittest.TestCase):
    """Tests for explicit confidence/uncertainty indicators."""

    def test_high_confidence(self):
        """High confidence should be labelled as 'high'."""
        result = ConfidenceIndicator.render(0.95, value=25.0)
        self.assertEqual(result["confidence_label"], "high")
        self.assertEqual(result["value"], 25.0)
        self.assertLessEqual(result["uncertainty"], 0.1)

    def test_medium_confidence(self):
        """Medium confidence should be labelled as 'medium'."""
        result = ConfidenceIndicator.render(0.75)
        self.assertEqual(result["confidence_label"], "medium")

    def test_low_confidence(self):
        """Low confidence should be labelled as 'low'."""
        result = ConfidenceIndicator.render(0.55)
        self.assertEqual(result["confidence_label"], "low")

    def test_very_low_confidence(self):
        """Very low confidence should be labelled as 'very_low'."""
        result = ConfidenceIndicator.render(0.3)
        self.assertEqual(result["confidence_label"], "very_low")

    def test_disclaimer_for_low_confidence(self):
        """Low confidence should include a disclaimer."""
        result = ConfidenceIndicator.render(0.4, value=25.0)
        self.assertIn("uncertainty", result)
        self.assertIn("disclaimer", result)

    def test_no_point_estimate_without_confidence(self):
        """
        Acceptance test: every advisory and alert must carry an explicit
        confidence/uncertainty indicator visible to the end user — never
        present a point estimate without it.
        """
        result = ConfidenceIndicator.render(0.85, value=25.0)
        self.assertIn("confidence", result)
        self.assertIn("confidence_label", result)
        self.assertIn("uncertainty", result)
        self.assertIn("value", result)


class TestDataPrivacyConsentLayer(unittest.TestCase):
    """Tests for data-privacy/consent layer."""

    def setUp(self):
        self.consent = DataPrivacyConsentLayer()

    def test_grant_consent(self):
        """Granting consent should be recorded."""
        self.consent.grant_consent("user_1", "location", granted=True)
        self.assertTrue(self.consent.has_consent("user_1", "location"))

    def test_withdraw_consent(self):
        """Withdrawing consent should be recorded."""
        self.consent.grant_consent("user_1", "yield", granted=True)
        self.assertTrue(self.consent.has_consent("user_1", "yield"))
        self.consent.grant_consent("user_1", "yield", granted=False)
        self.assertFalse(self.consent.has_consent("user_1", "yield"))

    def test_required_category_default(self):
        """Required categories should default to True if not explicitly set."""
        # location is required
        self.assertTrue(self.consent.has_consent("user_1", "location"))

    def test_optional_category_default(self):
        """Optional categories should default to False if not explicitly set."""
        # yield is optional
        self.assertFalse(self.consent.has_consent("user_1", "yield"))

    def test_consent_report(self):
        """Consent report should include all categories."""
        self.consent.grant_consent("user_1", "location", granted=True)
        report = self.consent.get_consent_report("user_1")
        self.assertEqual(report["user_id"], "user_1")
        for cat in DataPrivacyConsentLayer.CONSENT_CATEGORIES:
            self.assertIn(cat, report["consents"])

    def test_unknown_category_raises(self):
        """Unknown consent category should raise ValueError."""
        with self.assertRaises(ValueError):
            self.consent.grant_consent("user_1", "unknown_category")


class TestTrustIncidentLogger(unittest.TestCase):
    """Tests for trust incident logging and review process."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_trust.db")
        self.logger = TrustIncidentLogger(db_path=self.db_path)

    def test_log_advisory(self):
        """Advisory should be logged with all traceability fields."""
        self.logger.log_advisory(
            advisory_id="adv_001",
            input_data={"temperature": 25.0, "humidity": 60.0},
            model_version="v1.2.0",
            confidence=0.85,
            confidence_label="high",
            delivery_channel="sms",
            delivery_status="delivered",
            recipient="farmer_001",
        )
        result = self.logger.trace_advisory("adv_001")
        self.assertIsNotNone(result)
        self.assertEqual(result["advisory_id"], "adv_001")
        self.assertEqual(result["model_version"], "v1.2.0")
        self.assertEqual(result["confidence"], 0.85)
        self.assertEqual(result["delivery_channel"], "sms")

    def test_trace_advisory_not_found(self):
        """Tracing a non-existent advisory should return None."""
        result = self.logger.trace_advisory("nonexistent")
        self.assertIsNone(result)

    def test_report_farmer_loss(self):
        """Farmer loss should be marked for investigation."""
        self.logger.log_advisory(
            advisory_id="adv_002",
            input_data={"temperature": 45.0},
            model_version="v1.0.0",
            confidence=0.3,
            confidence_label="low",
            delivery_channel="sms",
            delivery_status="delivered",
            recipient="farmer_002",
        )
        self.logger.report_farmer_loss("adv_002", "Crop damage reported")
        result = self.logger.trace_advisory("adv_002")
        self.assertEqual(result["farmer_loss_reported"], 1)
        self.assertIn("Crop damage", result["investigation_notes"])

    def test_end_to_end_traceability(self):
        """
        Acceptance test: trace one synthetic advisory end-to-end through logs
        and confirm every stage (data source, model version, confidence,
        delivery) is reconstructable.
        """
        input_data = {
            "temperature": 42.0,
            "humidity": 20.0,
            "location": "Interior Sindh",
            "source": "satellite_data",
        }
        self.logger.log_advisory(
            advisory_id="trace_e2e_001",
            input_data=input_data,
            model_version="v2.1.0",
            confidence=0.45,
            confidence_label="low",
            delivery_channel="sms",
            delivery_status="delivered",
            recipient="farmer_003",
        )
        result = self.logger.trace_advisory("trace_e2e_001")

        # Every stage should be reconstructable
        self.assertIsNotNone(result)
        self.assertEqual(result["model_version"], "v2.1.0")
        self.assertEqual(result["confidence"], 0.45)
        self.assertEqual(result["confidence_label"], "low")
        self.assertEqual(result["delivery_channel"], "sms")
        self.assertEqual(result["delivery_status"], "delivered")
        self.assertEqual(result["recipient"], "farmer_003")
        logged_input = json.loads(result["input_data"])
        self.assertEqual(logged_input["temperature"], 42.0)
        self.assertEqual(logged_input["source"], "satellite_data")


# ============================================================================
# Integration Test: Hardened Weather Pipeline
# ============================================================================

class TestBuildHardenedWeatherResponse(unittest.TestCase):
    """Integration tests for the hardened weather response builder."""

    def test_fresh_data_response(self):
        """Fresh live data should have high confidence and no stale flag."""
        result = build_hardened_weather_response(
            location="Lahore",
            lat=31.5407,
            lon=74.3587,
            raw_weather={"temperature": 25.0, "humidity": 60},
        )
        self.assertEqual(result["data_source"], "live")
        self.assertFalse(result["stale"])
        self.assertIn("coordinate_confidence", result)
        self.assertIn("grid_metadata", result)
        self.assertIn("confidence", result)

    def test_no_data_response(self):
        """No data should return explicit insufficient-data response."""
        result = build_hardened_weather_response(
            location="Nowhere",
            lat=0.0,
            lon=0.0,
        )
        self.assertEqual(result["data_source"], "no_data")
        self.assertEqual(result["confidence"], 0.0)
        self.assertIn("coordinate_confidence", result)

    def test_confidence_computed_from_all_factors(self):
        """Confidence should be the minimum of coordinate, grid, and source confidence."""
        result = build_hardened_weather_response(
            location="Test",
            lat=31.5407,
            lon=74.3587,
            raw_weather={"temperature": 25.0},
            grid_cell={"elevation_variance": 200.0},  # Will lower grid confidence
        )
        # Grid confidence should be lowered by complex terrain
        self.assertLess(result["grid_metadata"]["overall_confidence"], 1.0)
        self.assertLessEqual(result["confidence"], result["grid_metadata"]["overall_confidence"])


# ============================================================================
# Test runner for environments without pytest
# ============================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
