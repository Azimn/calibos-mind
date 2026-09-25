"""Tests for the friction mutation: fatigue-scaled cognition admission.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_friction.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.

Spec (artificiality audit, Gap 1): in _warrants_cognition the activation
threshold scales with body weariness — below the focus floor only
high-urgency triggers warrant a cognition call, so silence becomes
state-driven rather than merely the absence of triggers.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jelly_psiduck.endogenous import EndogenousSubject
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Concern

from calibos_mind import friction
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition

BASE = Path(__file__).resolve().parents[1]
CARTRIDGE = load_cartridge(BASE / "calibos.toml")

DESC = "an unfinished matter weighing on me"


def _subject(tmp: Path):
    inbox = tmp / "inbox"
    return CalibosSubject(str(tmp / "mind.db"), CARTRIDGE,
                          cognition=InboxCognition(inbox),
                          salience_path=str(tmp / "salience.json"))


def _arm_concern(subject, activation: float, urgency: float = 0.9):
    """Install one unresolved concern with a fixed activation level."""
    key = "test-concern"
    subject.engine.state.concerns[key] = Concern(
        key=key, description=DESC, urgency=urgency,
        persistence=1.0, last_updated_tick=0)
    if DESC not in subject.engine.state.unresolved:
        subject.engine.state.unresolved.append(DESC)
    subject.endogenous["activation"]["concern:" + key] = activation


def _warranted(subject) -> bool:
    return bool(subject._warrants_cognition(False, False, False))


# -- pure scaling function --------------------------------------------------

def test_rested_unchanged():
    assert friction.cognition_threshold(0.8, {"focus": 1.0, "fatigue": 0.0}) == 0.8
    assert friction.cognition_threshold(0.8, {"focus": 0.5, "fatigue": 0.0}) == 0.8
    assert friction.cognition_threshold(0.8, {}) == 0.8  # defaults


def test_exhaustion_doubles():
    assert friction.cognition_threshold(0.8, {"focus": 0.0, "fatigue": 0.0}) == 1.6
    assert friction.cognition_threshold(0.8, {"focus": 0.5, "fatigue": 1.0}) == 1.6


def test_monotonic_in_focus():
    prev = 0.8
    for focus in (0.9, 0.7, 0.5, 0.34, 0.2, 0.0):
        t = friction.cognition_threshold(0.8, {"focus": focus, "fatigue": 0.0})
        assert t >= prev, (focus, t, prev)
        prev = t
    assert friction.cognition_threshold(0.8, {"focus": 0.2, "fatigue": 0.0}) > 0.8


def test_weariness_bounds():
    assert friction.weariness({}) == 0.5
    assert friction.weariness({"focus": 1.0, "fatigue": 0.0}) == 0.0
    assert friction.weariness({"focus": 0.0, "fatigue": 1.0}) == 1.0


# -- end-to-end on synthetic stores ------------------------------------------

def test_exhaustion_suppresses_admitted_trigger():
    tmp = Path(tempfile.mkdtemp(prefix="friction-test-"))
    subject = _subject(tmp)
    # Activation 1.0 clears the base threshold (0.8) but not the
    # exhaustion-scaled one (focus 0.1 -> ~1.37).
    _arm_concern(subject, activation=1.0)
    subject.engine.state.needs["focus"] = 1.0
    subject.engine.state.needs["fatigue"] = 0.0
    assert _warranted(subject), "rested: trigger should be admitted"
    assert subject.engine.state.needs.get("fatigue", 0.0) == 0.0

    tmp2 = Path(tempfile.mkdtemp(prefix="friction-test-"))
    subject2 = _subject(tmp2)
    _arm_concern(subject2, activation=1.0)
    subject2.engine.state.needs["focus"] = 0.1
    subject2.engine.state.needs["fatigue"] = 0.6
    assert not _warranted(subject2), "exhausted: same trigger should be suppressed"
    # Threshold must be restored after the call — nothing persists.
    assert subject2.config.activation_threshold == 0.8


def test_zero_fatigue_matches_unscaled_behavior():
    """With rested needs the override must behave exactly like the engine."""
    def build():
        tmp = Path(tempfile.mkdtemp(prefix="friction-test-"))
        s = _subject(tmp)
        _arm_concern(s, activation=1.0)
        s.engine.state.needs["focus"] = 1.0
        s.engine.state.needs["fatigue"] = 0.0
        return s

    a, b = build(), build()
    got = _warranted(a)
    want = bool(EndogenousSubject._warrants_cognition(b, False, False, False))
    assert got == want == True
    assert a.config.activation_threshold == 0.8


def test_threshold_restored_even_when_admitted():
    tmp = Path(tempfile.mkdtemp(prefix="friction-test-"))
    subject = _subject(tmp)
    _arm_concern(subject, activation=1.0)
    subject.engine.state.needs["focus"] = 0.2  # scaled path taken
    subject.engine.state.needs["fatigue"] = 0.0
    _warranted(subject)
    assert subject.config.activation_threshold == 0.8, "must not leak into config"


def _main():
    for fn in (test_rested_unchanged, test_exhaustion_doubles,
               test_monotonic_in_focus, test_weariness_bounds,
               test_exhaustion_suppresses_admitted_trigger,
               test_zero_fatigue_matches_unscaled_behavior,
               test_threshold_restored_even_when_admitted):
        fn()
        print(f"PASS {fn.__name__}")
    print("all friction tests passed")


if __name__ == "__main__":
    _main()
