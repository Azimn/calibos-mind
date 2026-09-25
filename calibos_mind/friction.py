"""Cognitive friction: fatigue-scaled cognition admission.

Mutation #1 from the artificiality audit (Domain 30, lack of friction):
cognition should cost something. When the body's weariness is high, the
activation threshold for an unresolved concern to warrant a cognition
call rises, so silence becomes state-driven rather than merely the
absence of triggers. Wants and avoids stop being free labels.

Zero new state, zero new schedules, fully deterministic. The pure
scaling function is tested directly; CalibosSubject wraps the frozen
engine's _warrants_cognition with a temporary threshold adjustment,
restored before return so nothing persists.
"""
from __future__ import annotations

FOCUS_FLOOR = 0.35  # focus at/above this (and fatigue below 1-floor): unchanged
MAX_SCALE = 2.0     # threshold multiplier at total exhaustion


def weariness(needs: dict) -> float:
    """0.0 (rested) .. 1.0 (exhausted), from the body's own need signals."""
    focus = needs.get("focus", 0.5)      # low-is-bad
    fatigue = needs.get("fatigue", 0.0)  # high-is-bad
    return max(0.0, min(1.0, max(1.0 - focus, fatigue)))


def cognition_threshold(base: float, needs: dict) -> float:
    """Scale the unresolved-concern admission threshold by body weariness.

    At or above the focus floor with fatigue in bounds, the threshold is
    returned unchanged — rested behavior is identical to before. Below the
    floor it rises linearly to MAX_SCALE * base at full exhaustion, so
    only high-urgency triggers warrant cognition when the organism is
    worn out. Deterministic: a pure function of (base, needs).
    """
    focus = needs.get("focus", 0.5)
    fatigue = needs.get("fatigue", 0.0)
    deficit = max(FOCUS_FLOOR - focus, fatigue - (1.0 - FOCUS_FLOOR), 0.0)
    if deficit <= 0.0:
        return base
    scale = 1.0 + (MAX_SCALE - 1.0) * deficit / FOCUS_FLOOR
    return base * scale
