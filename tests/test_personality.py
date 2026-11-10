"""Tests for the conduct-drift instrument (Domain 16, personality drift).

Spec: research/specs/2026-11-09-personality-drift-instrument.md
All fixtures on /tmp — the live store is never touched.

The mechanism: calibos_mind/personality.py — a ConductDriftTracker
owning the personality-drift.json sidecar, fed ONLY from cli._run_tick
after the tick's action is chosen, plus a `mind drift` line reporting
the L1 distance between the trailing-window (last 100) action shares
and the lifetime action shares, alongside a trait snapshot (formed-habit
strengths, authored/learned preference counts, narrative-claim
confidences) read live from the subject at report time.

Fitness functions under test:
  1. Two scripted windows with different action mixes show drift > 0.4.
  2. Identical action mixes in window and lifetime show drift ~0
     (exactly 0.0 when the window is the whole lifetime).
  3. Distance bounded: 0 <= drift <= 2.0 on every constructed case,
     including disjoint action sets (exactly 2.0).
  4. Window cap: after 100+ action-bearing ticks the sidecar window
     holds exactly 100 entries, oldest dropped, lifetime counts keep
     growing.
  5. Below MIN_WINDOW action-bearing ticks, drift reports insufficient
     window and the sidecar still accumulates.
  6. Read-only sweep (drift, status, plus a dream tick) leaves the
     sidecar byte-identical.
  7. init --force wipes the sidecar; a fresh store starts empty.
  8. Determinism: identical scripts -> byte-identical sidecar bytes.
  9. Trait snapshot: formed-habit strengths, authored/learned
     preference counts, and narrative confidences in the report match
     live subject state.
 10. Full suite green (run separately).

Scenario control (mirrors test_habits.py): the tests pin
engine.state.needs / pressures / pressure_baselines directly (concerns
cleared), commit through a transaction, then run cli._run_tick — the
real wiring under test. Measured premise (probed 2026-10-02 against the
frozen install): curiosity 0.80 dominant, pressures low -> action
"explore", deterministic across runs.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.personality import (
    MIN_WINDOW,
    WINDOW,
    ConductDriftTracker,
    trait_snapshot,
)
from digital_subject.models import Event, Habit, NarrativeClaim, Preference


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="personality-test-"))


def _sidecar_data(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_sidecar(path: Path, lifetime: dict, window: list) -> None:
    path.write_text(json.dumps({"lifetime": lifetime, "window": window}),
                    encoding="utf-8")


def _needs(**over):
    """Full need dict; override the dominant need per tick."""
    d = {"energy": 0.95, "fatigue": 0.05, "hunger": 0.05, "thirst": 0.05,
         "comfort": 0.95, "pain": 0.0, "warmth": 0.9, "restlessness": 0.05,
         "curiosity": 0.10, "loneliness": 0.05, "safety": 0.9, "focus": 0.9,
         "satisfaction": 0.9}
    d.update(over)
    return d


def _pressures(**over):
    d = {"fear": 0.10, "trust": 0.5, "arousal": 0.05}
    d.update(over)
    return d


def _baselines(**over):
    d = {"fear": 0.0, "trust": 0.5, "arousal": 0.0}
    d.update(over)
    return d


def _set_state(subject, needs, pressures, baselines):
    """Pin the engine's motive state; concerns cleared for determinism."""
    st = subject.engine.state
    st.needs = dict(needs)
    st.pressures = dict(pressures)
    st.pressure_baselines = dict(baselines)
    st.concerns = {}


def _script_tick(subject, needs, pressures, baselines):
    """One scripted waking tick through the real cli._run_tick wiring.

    The staged state is committed through a transaction first — a bare
    in-memory stage would be discarded by the heartbeat's _restore.
    """
    with subject._transaction():
        _set_state(subject, needs, pressures, baselines)
    return cli._run_tick(subject)


def _init_store():
    """Fresh synthetic store via the CLI (paths already redirected by
    the conftest autouse fixture). Returns the subject."""
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _drift_output():
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_drift(argparse.Namespace(window=10))
    return buf.getvalue()


# -- fitness 1: different mixes show drift > 0.4 ----------------------------

def test_drift_shifted_mix_exceeds_threshold():
    """20+ approach-dominant ticks, then 20+ seek_contact-dominant:
    drift > 0.4. Uses a directly-constructed sidecar for the distance
    math (spec) plus a small-WINDOW tracker driving observe() itself."""
    tmp = _tmp()
    path = tmp / "personality-drift.json"
    # Lifetime: 40 approach + 10 seek_contact (window not yet full).
    # Window: last 20 ticks, all seek_contact.
    _write_sidecar(path,
                   {"approach": 40, "seek_contact": 10},
                   [[100 + i, "seek_contact"] for i in range(20)])
    tracker = ConductDriftTracker(path)
    distance, wshares, lshares = tracker.drift()
    assert distance is not None
    # Window {seek_contact: 1.0}; lifetime {approach: 0.8, seek_contact: 0.2}
    # -> L1 = |1.0 - 0.2| + |0 - 0.8| = 1.6.
    assert distance == pytest.approx(1.6), (distance, wshares, lshares)
    assert distance > 0.4
    # Same shape through observe() with a small WINDOW override: 20
    # approach ticks, then 20 seek_contact ticks, window 25 (still >=
    # MIN_WINDOW, so drift scores). The window holds the last 25: 5
    # approach + 20 seek_contact.
    tracker2 = ConductDriftTracker(tmp / "p2.json", window=25)
    for i in range(20):
        assert tracker2.observe(i + 1, "approach")
    for i in range(20):
        assert tracker2.observe(21 + i, "seek_contact")
    distance2, _, _ = tracker2.drift()
    # Window {approach: 0.2, seek_contact: 0.8}; lifetime 20/20 ->
    # L1 = |0.2 - 0.5| + |0.8 - 0.5| = 0.6.
    assert distance2 == pytest.approx(0.6), distance2
    assert distance2 > 0.4


# -- fitness 2: identical mixes show drift ~0 --------------------------------

def test_drift_identical_mix_is_zero():
    """Window mix == lifetime mix -> drift exactly 0.0 when the window
    is the whole lifetime; ~0 when the window is a representative slice."""
    tmp = _tmp()
    path = tmp / "personality-drift.json"
    rows = [[i + 1, "approach"] for i in range(10)] + \
           [[11 + i, "seek_contact"] for i in range(10)]
    _write_sidecar(path, {"approach": 10, "seek_contact": 10}, rows)
    tracker = ConductDriftTracker(path)
    distance, wshares, lshares = tracker.drift()
    assert distance == 0.0, (distance, wshares, lshares)
    assert wshares == {"approach": 0.5, "seek_contact": 0.5}
    assert lshares == {"approach": 0.5, "seek_contact": 0.5}
    # Representative slice: lifetime 100/100, window 10/10 of the same mix.
    path2 = tmp / "p2.json"
    _write_sidecar(path2, {"approach": 100, "seek_contact": 100},
                   [[i + 1, "approach"] for i in range(10)] +
                   [[11 + i, "seek_contact"] for i in range(10)])
    distance2, _, _ = ConductDriftTracker(path2).drift()
    assert distance2 == pytest.approx(0.0), distance2


# -- fitness 3: distance bounded, disjoint sets exactly 2.0 -------------------

def test_drift_bounded_and_disjoint_is_two():
    """0 <= drift <= 2.0 on every constructed case; disjoint action sets
    give exactly 2.0 (the revert signal's upper bound)."""
    tmp = _tmp()
    cases = [
        # (lifetime, window rows, expected distance)
        ({"approach": 20}, [[i + 1, "seek_contact"] for i in range(20)], 2.0),
        ({"approach": 20}, [[i + 1, "approach"] for i in range(20)], 0.0),
        ({"approach": 15, "seek_contact": 5},
         [[i + 1, "approach"] for i in range(10)] +
         [[11 + i, "seek_contact"] for i in range(10)], 0.5),
        ({"approach": 30, "seek_contact": 10, "withdraw": 10},
         [[i + 1, "withdraw"] for i in range(20)], 1.6),
    ]
    for i, (lifetime, rows, expected) in enumerate(cases):
        path = tmp / f"p{i}.json"
        _write_sidecar(path, lifetime, rows)
        distance, _, _ = ConductDriftTracker(path).drift()
        assert distance is not None
        assert 0.0 <= distance <= 2.0, distance
        assert distance == pytest.approx(expected), (i, distance, expected)


def test_drift_never_exceeds_two_on_random_mixes():
    """Brute-force-ish sweep: random-ish mixes stay inside [0, 2]."""
    import itertools
    tmp = _tmp()
    actions = ["approach", "seek_contact", "withdraw", "explore"]
    for n, combo in enumerate(itertools.product([0, 7, 13], repeat=4)):
        if sum(combo) == 0:
            continue
        lifetime = {a: c for a, c in zip(actions, combo) if c}
        rows = []
        tick = 0
        for a, c in zip(actions, combo):
            for _ in range(c):
                tick += 1
                rows.append([tick, a])
        # Window must hold >= MIN_WINDOW rows to score; pad with the
        # first action if the mix is small.
        while len(rows) < MIN_WINDOW:
            tick += 1
            rows.append([tick, actions[0]])
            lifetime[actions[0]] = lifetime.get(actions[0], 0) + 1
        path = tmp / f"sweep{n}.json"
        _write_sidecar(path, lifetime, rows)
        distance, _, _ = ConductDriftTracker(path).drift()
        assert distance is not None
        assert math.isfinite(distance)
        assert 0.0 <= distance <= 2.0, (combo, distance)


# -- fitness 4: window cap ------------------------------------------------------

def test_window_cap_holds_100_oldest_dropped():
    """After 100+ action-bearing ticks the window holds exactly 100
    entries (oldest dropped) while lifetime counts keep growing."""
    tmp = _tmp()
    tracker = ConductDriftTracker(tmp / "personality-drift.json")
    for i in range(105):
        assert tracker.observe(i + 1, "explore")
    assert len(tracker.window) == WINDOW == 100
    assert tracker.window[0] == [6, "explore"], tracker.window[0]
    assert tracker.window[-1] == [105, "explore"]
    assert tracker.lifetime == {"explore": 105}
    # Persisted form agrees: reload and re-check.
    tracker.save()
    reloaded = ConductDriftTracker(tmp / "personality-drift.json")
    assert len(reloaded.window) == 100
    assert reloaded.window[0] == [6, "explore"]
    assert reloaded.lifetime == {"explore": 105}


# -- fitness 5: below MIN_WINDOW -------------------------------------------------

def test_below_min_window_reports_insufficient_but_accumulates():
    """Below MIN_WINDOW ticks drift is None and the sidecar still
    accumulates; the 20th tick flips the reading on."""
    tmp = _tmp()
    path = tmp / "personality-drift.json"
    tracker = ConductDriftTracker(path)
    for i in range(MIN_WINDOW - 1):
        assert tracker.observe(i + 1, "approach")
    distance, wshares, lshares = tracker.drift()
    assert distance is None, distance
    assert wshares == {"approach": 1.0} and lshares == {"approach": 1.0}
    tracker.save()
    data = _sidecar_data(path)
    assert len(data["window"]) == MIN_WINDOW - 1
    assert data["lifetime"] == {"approach": MIN_WINDOW - 1}
    # The 20th tick flips it on.
    tracker.observe(MIN_WINDOW, "approach")
    distance, _, _ = tracker.drift()
    assert distance == 0.0, distance


# -- corrupt sidecar fails loud; no-action ticks record nothing ------------------

def test_corrupt_sidecar_raises_loud():
    """Invalid JSON, non-dict top level, malformed lifetime/window all
    raise ValueError at construction — never a silent reset."""
    tmp = _tmp()
    bad = {
        "bad-json": "{not json",
        "non-dict": "[1, 2]",
        "bad-lifetime-type": json.dumps({"lifetime": [], "window": []}),
        "bad-lifetime-count": json.dumps({"lifetime": {"a": 1.5}, "window": []}),
        "negative-count": json.dumps({"lifetime": {"a": -1}, "window": []}),
        "bool-count": json.dumps({"lifetime": {"a": True}, "window": []}),
        "bad-window-type": json.dumps({"lifetime": {}, "window": {}}),
        "bad-window-row": json.dumps({"lifetime": {}, "window": [[1]]}),
        "bad-window-tick": json.dumps({"lifetime": {}, "window": [["x", "a"]]}),
        "bad-window-action": json.dumps({"lifetime": {}, "window": [[1, 7]]}),
    }
    for name, text in bad.items():
        path = tmp / f"{name}.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt personality-drift"):
            ConductDriftTracker(path)


def test_observe_ignores_ticks_with_no_action():
    """A tick with no chosen action records nothing (spec: ticks with
    no chosen action record nothing)."""
    tmp = _tmp()
    tracker = ConductDriftTracker(tmp / "personality-drift.json")
    assert tracker.observe(1, None) is False
    assert tracker.observe(2, "") is False
    assert tracker.observe(3, "approach") is True
    assert tracker.window == [[3, "approach"]]
    assert tracker.lifetime == {"approach": 1}


def test_save_load_roundtrip_deterministic():
    """Save twice -> identical bytes; reload -> identical data."""
    tmp = _tmp()
    path = tmp / "personality-drift.json"
    tracker = ConductDriftTracker(path)
    for i in range(30):
        tracker.observe(i + 1, "approach" if i % 3 else "seek_contact")
    tracker.save()
    first = path.read_bytes()
    tracker.save()
    assert path.read_bytes() == first
    reloaded = ConductDriftTracker(path)
    assert reloaded.window == tracker.window
    assert reloaded.lifetime == tracker.lifetime
    reloaded.save()
    assert path.read_bytes() == first


# -- fitness 6: read-only sweep ---------------------------------------------------

def test_readonly_commands_and_dream_leave_sidecar_byte_identical():
    """drift, status, and a dream tick never write the sidecar."""
    subject = _init_store()
    sidecar = cli.PERSONALITY_DRIFT
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    assert sidecar.exists()
    before = sidecar.read_bytes()
    cli.cmd_drift(argparse.Namespace(window=10))
    cli.cmd_status(argparse.Namespace(raw=False))
    subject.dream_tick()
    assert sidecar.read_bytes() == before, \
        "read-only command or dream tick wrote the sidecar"


def test_drift_line_reports_insufficient_window_on_fresh_store():
    """Fresh store: the drift line reports insufficient window (0/20)
    and the trait line shows the cartridge preferences."""
    _init_store()
    assert not cli.PERSONALITY_DRIFT.exists()
    out = _drift_output()
    assert "conduct drift: insufficient window (0/20)" in out, out
    assert "traits:" in out, out
    assert "preferences authored=4 learned=0" in out, out
    assert "formed habits {}" in out, out
    # Still no sidecar: reading the report must not create it.
    assert not cli.PERSONALITY_DRIFT.exists()


def test_drift_line_shows_number_once_window_fills():
    """After 25 identical-action ticks the drift line shows 0.00
    (window mix == lifetime mix) and the sidecar holds 25 rows."""
    subject = _init_store()
    actions = []
    for _ in range(25):
        actions.append(_script_tick(
            subject, _needs(curiosity=0.80), _pressures(), _baselines())["action"])
    assert set(actions) == {"explore"}, actions
    out = _drift_output()
    assert "conduct drift (last 100 vs lifetime): 0.00" in out, out
    assert "window {explore: 1.00}" in out, out
    assert "lifetime {explore: 1.00}" in out, out
    data = _sidecar_data(cli.PERSONALITY_DRIFT)
    assert len(data["window"]) == 25
    assert data["lifetime"] == {"explore": 25}


# -- fitness 7: init --force ------------------------------------------------------

def test_init_force_wipes_sidecar_and_fresh_store_starts_empty():
    """init --force wipes the sidecar; a fresh store starts empty."""
    subject = _init_store()
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    assert cli.PERSONALITY_DRIFT.exists()
    cli.cmd_init(argparse.Namespace(force=True))
    assert not cli.PERSONALITY_DRIFT.exists(), \
        "init --force must wipe the personality-drift sidecar (genome: reseed reset)"
    fresh = cli._subject()
    assert ConductDriftTracker(cli.PERSONALITY_DRIFT).drift()[0] is None
    out = _drift_output()
    assert "conduct drift: insufficient window (0/20)" in out, out
    assert fresh.engine.state.tick == 0


# -- fitness 8: determinism ---------------------------------------------------------

def _run_script(root: Path, ticks: int = 12) -> bytes:
    """Run an identical scripted tick sequence on a synthetic tree at
    `root` through the canonical redirect; return the sidecar bytes."""
    from conftest import redirect_all_cli_paths
    previous = redirect_all_cli_paths(root)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        for _ in range(ticks):
            _script_tick(subject, _needs(curiosity=0.80),
                         _pressures(), _baselines())
        return cli.PERSONALITY_DRIFT.read_bytes()
    finally:
        for name, value in previous.items():
            setattr(cli, name, value)


def test_identical_scripts_yield_byte_identical_sidecar():
    """Identical scripted tick sequences -> byte-identical sidecar."""
    a = _run_script(_tmp(), ticks=12)
    b = _run_script(_tmp(), ticks=12)
    assert a == b, (a, b)
    data = json.loads(a)
    assert len(data["window"]) == 12
    assert sum(data["lifetime"].values()) == 12


# -- fitness 9: trait snapshot ------------------------------------------------------

def test_trait_snapshot_matches_live_state_on_injected_state():
    """Snapshot values equal the live engine state they were read from:
    formed-habit strengths, authored/learned preference counts,
    narrative-claim confidences."""
    subject = _init_store()
    from digital_subject.models import Action
    st = subject.engine.state
    # Inject trait state live (read immediately, no transaction in
    # between — the snapshot reads the live engine state).
    st.habits["formed:curiosity:explore"] = Habit(
        "formed:curiosity:explore", "curiosity", Action.EXPLORE, 0.52)
    st.habits["formed:fear:withdraw"] = Habit(
        "formed:fear:withdraw", "fear", Action.WITHDRAW, 0.66)
    st.preferences["learned:garden"] = Preference(
        "learned:garden", "garden", 0.4, 0.5, True, 3)
    st.narrative["i-am-patient"] = NarrativeClaim(
        "i-am-patient", "I am patient", 0.81, 0.3, (), 3)
    snap = trait_snapshot(subject)
    assert snap["formed_habits"] == {
        "formed:curiosity:explore": 0.52,
        "formed:fear:withdraw": 0.66,
    }, snap
    # 4 cartridge preferences authored + 1 learned.
    assert snap["preferences"] == {"authored": 4, "learned": 1}, snap
    assert snap["narrative"] == {"i-am-patient": 0.81}, snap
    # Authored habits are NOT formed habits and never appear there.
    assert "curious_question" not in snap["formed_habits"]


def test_trait_snapshot_matches_live_state_after_real_ticks():
    """End-to-end: formation ticks grow a real formed habit and a
    valenced event learns a real preference; the snapshot reports both
    exactly as live state holds them."""
    subject = _init_store()
    # Form a habit: curiosity 0.80 -> explore, repeated co-fires.
    for _ in range(5):
        _script_tick(subject, _needs(curiosity=0.80),
                     _pressures(), _baselines())
    assert any(k.startswith("formed:")
               for k in subject.engine.state.habits), \
        "formation script grew no formed habit"
    # Learn a preference: a valenced message event with a tag.
    subject.enqueue(Event("message", "tester", "The garden was lovely today",
                          tags=("garden",), valence=0.8))
    _script_tick(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    # Read live state at report time — the same moment the snapshot
    # reads it. (Capturing strengths before the last tick is stale: the
    # habit keeps growing on each further explore co-fire.)
    formed = {k: h.strength for k, h in subject.engine.state.habits.items()
              if k.startswith("formed:")}
    snap = trait_snapshot(subject)
    assert snap["formed_habits"] == formed, (snap, formed)
    live_prefs = subject.engine.state.preferences
    assert snap["preferences"] == {
        "authored": sum(1 for p in live_prefs.values() if not p.learned),
        "learned": sum(1 for p in live_prefs.values() if p.learned),
    }, snap
    assert snap["preferences"]["learned"] >= 1, snap
    assert "learned:garden" in live_prefs, list(live_prefs)
    live_narrative = {k: c.confidence
                      for k, c in subject.engine.state.narrative.items()}
    assert snap["narrative"] == live_narrative, (snap, live_narrative)


def test_trait_snapshot_empty_state_is_empty_not_guessed():
    """A subject with no trait state snapshots to empty sections,
    never to invented values."""
    snap = trait_snapshot(object())
    assert snap == {"formed_habits": {},
                    "preferences": {"authored": 0, "learned": 0},
                    "narrative": {}}, snap


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
