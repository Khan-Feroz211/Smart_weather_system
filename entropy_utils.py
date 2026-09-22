"""
entropy_utils.py
================
Pure mathematical utilities for entropy calculation and next-question selection.
No external network dependencies (Offline-Safe).
"""

import math
from typing import List, Dict, Any, Optional

AMBIGUITY_THRESHOLD = 0.75

def compute_entropy(probs: List[float]) -> Dict[str, Any]:
    """
    Computes Shannon entropy (in nats) and normalized entropy for a list of candidate probabilities.
    """
    valid_probs = [p for p in probs if p > 0]
    if not valid_probs or len(probs) <= 1:
        return {
            "raw_entropy_nats": 0.0,
            "normalized_entropy": 0.0,
            "n_candidates": len(probs),
            "is_ambiguous": False,
            "ambiguity_threshold": AMBIGUITY_THRESHOLD
        }

    raw_entropy = -sum(p * math.log(p) for p in valid_probs)
    max_entropy = math.log(len(probs))
    normalized_entropy = raw_entropy / max_entropy if max_entropy > 0 else 0.0
    is_ambiguous = normalized_entropy > AMBIGUITY_THRESHOLD

    return {
        "raw_entropy_nats": round(raw_entropy, 4),
        "normalized_entropy": round(normalized_entropy, 4),
        "n_candidates": len(probs),
        "is_ambiguous": is_ambiguous,
        "ambiguity_threshold": AMBIGUITY_THRESHOLD
    }

def suggest_next_question(symptoms_present: Dict[str, bool], candidates: List[Dict[str, Any]]) -> Optional[str]:
    """
    Identifies a key unasked symptom to ask next to minimize diagnosis ambiguity.
    """
    if len(candidates) <= 1:
        return None

    # Gather candidate symptom profiles
    symptom_frequency: Dict[str, int] = {}
    for cand in candidates:
        for sym in cand.get("symptoms", []):
            if not symptoms_present.get(sym, False):
                symptom_frequency[sym] = symptom_frequency.get(sym, 0) + 1

    if not symptom_frequency:
        return None

    # Pick symptom closest to 50% split among candidates for max information gain
    target_split = len(candidates) / 2.0
    best_symptom = min(symptom_frequency.keys(), key=lambda s: abs(symptom_frequency[s] - target_split))
    return best_symptom
