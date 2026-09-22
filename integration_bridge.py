"""
integration_bridge.py
======================
SystemBridge integration layer.
Hooks recommendation engine logic to Phase 6 GracefulDegradationManager, CacheMode,
FallbackCacheSystem, and ConfidencePenaltySystem.
"""

from typing import Dict, Any, Tuple
from edge_case_hardening_v2 import (
    GracefulDegradationManager,
    ConfidencePenaltySystem,
    FallbackCacheSystem,
    CacheMode
)

class SystemBridge:
    """
    Connects AgriAdvisor Recommendation Engine with Phase 6 Edge-Case Hardening.
    Manages connectivity degradation (ONLINE, CACHE, OFFLINE) and staleness penalties.
    """
    def __init__(self):
        self.degradation_manager = GracefulDegradationManager(cache_dir='cache')
        self.confidence_system = ConfidencePenaltySystem()
        self.cache_system = FallbackCacheSystem(cache_dir='cache')

    def resolve_connectivity_and_penalties(
        self,
        override_state: str = "ONLINE",
        data_age_hours: float = 0.0
    ) -> Tuple[str, float, str]:
        """
        Resolves operational mode (ONLINE, CACHE, OFFLINE) and confidence penalty.
        Returns: (effective_state, confidence_penalty, penalty_reason)
        """
        state_upper = override_state.upper()

        if state_upper == "OFFLINE":
            return ("OFFLINE", 1.0, "System is OFFLINE; weather and satellite telemetry withheld.")

        if state_upper == "CACHE":
            # Apply penalty: 0.05 per hour stale
            stale_penalty = min(0.9, data_age_hours * 0.05)
            reason = f"Stale data ({data_age_hours:.1f}h old): -{stale_penalty*100:.1f}% confidence penalty"
            return ("CACHE", stale_penalty, reason)

        # ONLINE
        return ("ONLINE", 0.0, "ONLINE mode; fresh telemetry available.")
