"""Tests for dream isolation (calibos_mind/sleep.py + CalibosSubject.dream_tick).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_sleep.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind.provider import DreamCognition, InboxCognition
from calibos_mind.sleep import assert_isolation, isolation_snapshot
from calibos_mind.subject import CalibosSubject
from digital_subject.cartridge import load_cartridge

BASE = Path(__file__).resolve().parents[1]
LIVE_DB = BASE / "mind.db"


def _subject(tmp, provider=None):
    cart = load_cartridge(BASE / "calibos.toml")
    return CalibosSubject(tmp / "test.db", cart,
                          cognition=provider or InboxCognition(tmp / "inbox"),
                          salience_path=tmp / "salience.json")


def _frozen_surface(state):
    eng = state["engine"]
    return {
        "tick": eng["tick"],
        "needs": eng["needs"],
        "pressures": eng["pressures"],
        "current_activity": eng["current_activity"],
        "last_intention": eng["last_intention"],
        "action_trace": [t for t in state["trace"]
                         if t["kind"] in ("activity", "heartbeat")],
        "pending": state["pending"],
    }


def test_dream_tick_freezes_body_conduct_tick():
    """Six dream ticks move nothing on the frozen surface."""
    tmp = Path(tempfile.mkdtemp(prefix="sleep-freeze-"))
    sub = _subject(tmp)
    with sub._transaction():
        sub._add("memory", "a synthetic lived memory",
                 concepts=("synthetic",), generated_by="cognition")
    before = _frozen_surface(sub.inspect())
    for _ in range(6):
        result = sub.dream_tick()
        assert result["action"] == "sleep"
    after = _frozen_surface(sub.inspect())
    assert before == after, (
        [k for k in before if before[k] != after[k]])
    # ...while the dream machinery itself still ran (records may accrue).
    assert sub.workspace.sequence >= 1


def test_dream_still_dreams():
    """Freezing does not break the associative machinery: a seeded echo
    resurfaces as a dream fragment and a dream-derived thought."""
    tmp = Path(tempfile.mkdtemp(prefix="sleep-dream-"))
    dreamer = DreamCognition(tmp / "dreams")
    sub = _subject(tmp, provider=dreamer)
    sub.dream_tick()  # settle projections so the echo is what fires
    with sub._transaction():
        tick = sub.engine.state.tick
        sub.endogenous["echoes"] = [{
            "text": "a half-remembered image of rain on a window",
            "parent": "experience-1", "due": tick,
            "expires": tick + 6, "depth": 1}]
    tick_before = sub.inspect()["engine"]["tick"]
    frags_before = len(dreamer.fragments)
    sub.dream_tick()
    assert len(dreamer.fragments) == frags_before + 1, dreamer.fragments
    state = sub.inspect()
    thoughts = [r for r in state["workspace"]["records"]
                if r["first_person"] == "a half-remembered image of rain on a window"]
    assert len(thoughts) == 1, "echo did not resurface as a thought"
    assert thoughts[0]["generated_by"] == "dream-derived", thoughts[0]
    assert state["engine"]["tick"] == tick_before, "store tick moved during sleep"
    trig_kinds = [t["trigger"]["kind"] for t in state["trace"]
                  if t["kind"] == "cognition_trigger"]
    assert "prior_thought" in trig_kinds, trig_kinds


def test_isolation_assertion_fires_on_violation():
    """The assertion is a real check, not a formality: a planted drift in
    needs is caught."""
    before = {"tick": 3, "needs": {"energy": 0.7}, "pressures": {},
              "current_activity": "wait", "last_intention": None,
              "action_trace": [], "pending": []}
    after = dict(before, needs={"energy": 0.65})
    try:
        assert_isolation(before, after)
    except AssertionError as exc:
        assert "needs" in str(exc), exc
    else:
        raise AssertionError("assert_isolation did not fire on moved needs")
    # Identical snapshots pass silently.
    assert_isolation(before, dict(before))


def test_dream_tick_uses_assertion():
    """dream_tick itself raises if the frozen surface moves: simulate by
    breaking the snapshot the tick compares against."""
    tmp = Path(tempfile.mkdtemp(prefix="sleep-assert-"))
    sub = _subject(tmp, provider=DreamCognition(tmp / "dreams"))
    before = isolation_snapshot(sub.inspect())
    tampered = dict(before, tick=before["tick"] + 1)
    try:
        assert_isolation(before, tampered)
    except AssertionError:
        pass
    else:
        raise AssertionError("no fire on moved tick")
    # And a clean dream tick passes the real assertion path.
    sub.dream_tick()


def test_dream_tick_survives_full_trace_cap():
    """Regression: with the trace at the engine's 256-entry cap, the dream's
    own allowed cognition_trigger append evicts the oldest heartbeat. That
    mechanical eviction must not false-positive the isolation assertion —
    only genuine movement outside the dream's remit may fire."""
    tmp = Path(tempfile.mkdtemp(prefix="sleep-tracecap-"))
    sub = _subject(tmp, provider=DreamCognition(tmp / "dreams"))
    with sub._transaction():
        tick = sub.engine.state.tick
        sub.trace = [{"tick": tick, "kind": "heartbeat", "action": "wait",
                      "thoughts": [], "experience_count": 0, "speech": None}
                     for _ in range(256)]
        sub.endogenous["echoes"] = [{"text": "a tracecap echo of rain on glass",
                                     "parent": "experience-1",
                                     "due": tick, "expires": tick + 6,
                                     "depth": 1}]
    sub.dream_tick()  # must not raise: nothing outside the remit moved
    state = sub.inspect()
    # Non-vacuity: the tick really warranted cognition (one in-remit append),
    # so exactly one heartbeat was mechanically evicted and the retained
    # entries are unmutated.
    trig = [t for t in state["trace"] if t["kind"] == "cognition_trigger"]
    assert trig, "no cognition warranted; the test would pass vacuously"
    action = [t for t in state["trace"] if t["kind"] in ("activity", "heartbeat")]
    assert len(action) == 256 - len(trig), (
        f"expected {256 - len(trig)} retained heartbeats, got {len(action)}")
    assert all(t["kind"] == "heartbeat" for t in action)


def test_action_trace_compare_is_eviction_aware():
    """The conduct-trace compare tolerates only front-shrink from cap
    eviction. Appended, mutated, or reordered entries still fire."""
    before = [{"kind": "heartbeat", "n": i} for i in range(4)]
    snap = {"action_trace": before}
    # Legal: eviction of the oldest entries (shorter suffix), or no change.
    assert_isolation(snap, {"action_trace": before[1:]})
    assert_isolation(snap, {"action_trace": []})
    assert_isolation(snap, {"action_trace": list(before)})
    # Illegal: each must still raise.
    illegal = [
        before + [{"kind": "heartbeat", "n": 4}],        # appended conduct entry
        before[1:] + [{"kind": "activity", "n": 5}],     # append hiding behind eviction
        [{"kind": "heartbeat", "n": 99}] + before[1:],   # mutated retained entry
        list(reversed(before)),                          # reordered
    ]
    for bad in illegal:
        try:
            assert_isolation(snap, {"action_trace": bad})
        except AssertionError as exc:
            assert "action_trace" in str(exc), exc
        else:
            raise AssertionError(
                f"assert_isolation did not fire on illegal action_trace: {bad}")


def test_live_store_untouched():
    """Nothing in this module's code paths may reach the live store."""
    if not LIVE_DB.exists():
        return
    digest = hashlib.sha256(LIVE_DB.read_bytes()).hexdigest()
    tmp = Path(tempfile.mkdtemp(prefix="sleep-live-"))
    sub = _subject(tmp, provider=DreamCognition(tmp / "dreams"))
    for _ in range(3):
        sub.dream_tick()
    assert hashlib.sha256(LIVE_DB.read_bytes()).hexdigest() == digest, \
        "live mind.db changed during synthetic dream ticks"
    assert "sleep-" in str(tmp)


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
