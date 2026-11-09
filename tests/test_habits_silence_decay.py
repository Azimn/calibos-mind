"""Tests for time-based disuse decay ("silence decay") of formed habits.

Spec: research/specs/2026-11-08-habit-silence-decay.md (Domain 15,
Learning). All fixtures live in /tmp — the live store is never touched.

Fitness functions under test:
  1. One DECAY_STEP per full 100-tick window of trigger silence:
     0.70 -> 0.65 at 100 ticks, 0.60 at 200, 0.55 at 300.
  2. Boundary: 99 silent ticks -> no decay; 100 -> exactly one step.
  3. Co-fire resets the silence clock: a further 99 silent ticks after
     a co-fire cause no decay; a co-fire after a long silence is not
     charged for the windows that elapsed before it was exercised.
  4. Decay below 0.30 removes the habit with a written archival note
     naming silence as the cause; the removed habit never fires again.
  5. A habit firing regularly (within every 100-tick window) is
     byte-identical to pristine behavior: no decay, no new sidecar field
     in its entry.
  6. Read-only commands and dream ticks never apply silence decay and
     never touch silence_windows_applied (sidecar byte-identical).
  7. init --force wipes silence-decay state with the rest of the sidecar.
  8. Determinism: identical tick sequences -> byte-identical sidecar.
  9. Full existing suite green; no engine/cartridge bytes change
     (run separately).

Scenario control mirrors tests/test_habits.py: engine.state.needs /
pressures / pressure_baselines are pinned directly, committed through a
transaction, then cli._run_tick runs the real wiring. Measured premises
(probed 2026-10-02 against the frozen install):
- fear 0.95 (baseline 0.0) vs top need 0.80 -> pressure channel wins
  (0.95 >= 0.80 + 0.12) -> action "withdraw"; energy falls ~0.0021/tick
  by homeostasis, so the formation delta gate (mean <= 0) passes.
- hunger 0.80 dominant -> action "seek_contact", hunger delta ~+0.002
  (homeostasis only) -> the formation delta gate blocks, so silent
  hunger ticks never form a competing habit.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.habits import (
    DECAY_STEP,
    FORMED_PREFIX,
    GROWTH_CAP,
    REMOVAL_FLOOR,
    REMOVAL_REASON,
    REMOVAL_REASON_SILENCE,
    SILENCE_WINDOW_TICKS,
    HabitFormationTracker,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Action, Habit


# -- fixtures (mirroring tests/test_habits.py) --------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="silence-decay-test-"))


def _make_subject(tmp: Path) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        interoception_path=None,
        familiarity_path=None,
        ambivalence_path=None,
        habits_path=str(tmp / "habits-formed.json"),
    )


def _needs(**over):
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
    st = subject.engine.state
    st.needs = dict(needs)
    st.pressures = dict(pressures)
    st.pressure_baselines = dict(baselines)
    st.concerns = {}


def _script_tick(subject, needs, pressures, baselines):
    with subject._transaction():
        _set_state(subject, needs, pressures, baselines)
    return cli._run_tick(subject)


def _fear_script():
    return (_needs(energy=0.20), _pressures(fear=0.95, trust=0.5),
            _baselines(fear=0.0))


def _sidecar(tmp: Path):
    p = tmp / "habits-formed.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _formed_keys(subject):
    return {k for k in subject.engine.state.habits if k.startswith(FORMED_PREFIX)}


def _seed_tracker(tmp: Path, strength: float, last_tick: int,
                  created_tick: int = 3, peak: float | None = None,
                  silence_windows=None) -> HabitFormationTracker:
    """Load a tracker from a written sidecar holding one formed habit.

    The entry carries no silence_windows_applied key unless one is
    passed — the pre-mutation shape, which must load with default 0.
    """
    entry = {"trigger": "fear", "action": "withdraw", "strength": strength,
             "peak": strength if peak is None else peak,
             "created_tick": created_tick, "cofires": 3,
             "last_tick": last_tick}
    if silence_windows is not None:
        entry["silence_windows_applied"] = silence_windows
    path = tmp / "habits-formed.json"
    path.write_text(json.dumps({"window": [],
                                "formed": {"formed:fear:withdraw": entry},
                                "archived": []}), encoding="utf-8")
    return HabitFormationTracker(path)


def _silent_tick(tracker, tick):
    """One trigger-silent fold: hunger dominates, seek_contact chosen.

    Hunger delta +0.002 keeps the (hunger, seek_contact) pair below the
    formation gate, so the fold exercises silence decay only.
    """
    return tracker.observe_tick(
        tick=tick, trigger="hunger", action="seek_contact",
        dominant_need="hunger", need_deltas={"hunger": 0.002})


# -- fitness 1: one step per full 100-tick window ------------------------------

def test_silence_decay_one_step_per_window():
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.70, last_tick=16)
    key = "formed:fear:withdraw"

    changed, state_changed = _silent_tick(tracker, 116)
    assert (changed, state_changed) == (True, True)
    assert tracker.data["formed"][key]["strength"] == 0.65
    assert tracker.data["formed"][key]["silence_windows_applied"] == 1

    _silent_tick(tracker, 216)
    assert tracker.data["formed"][key]["strength"] == 0.60
    assert tracker.data["formed"][key]["silence_windows_applied"] == 2

    _silent_tick(tracker, 316)
    assert tracker.data["formed"][key]["strength"] == 0.55
    assert tracker.data["formed"][key]["silence_windows_applied"] == 3


def test_silence_decay_multi_window_jump_in_one_fold():
    """A single fold jumping 250 ticks charges two windows at once
    (windows_elapsed - applied), not one: the count is windows, not
    folds."""
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.70, last_tick=3)
    _silent_tick(tracker, 253)
    meta = tracker.data["formed"]["formed:fear:withdraw"]
    assert meta["strength"] == 0.60
    assert meta["silence_windows_applied"] == 2


# -- fitness 2: the 99/100 boundary -------------------------------------------

def test_silence_boundary_99_vs_100_ticks():
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.70, last_tick=16)
    key = "formed:fear:withdraw"

    # 99 silent ticks (17..115): no decay, no field materialized.
    changed, state_changed = _silent_tick(tracker, 115)
    assert (changed, state_changed) == (True, False)
    assert tracker.data["formed"][key]["strength"] == 0.70
    assert "silence_windows_applied" not in tracker.data["formed"][key]

    # The 100th silent tick (116): exactly one step.
    _silent_tick(tracker, 116)
    assert tracker.data["formed"][key]["strength"] == 0.65
    assert tracker.data["formed"][key]["silence_windows_applied"] == 1


# -- fitness 3: co-fire resets the silence clock ------------------------------

def test_cofire_resets_silence_clock():
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.70, last_tick=16)
    key = "formed:fear:withdraw"

    # 50 silent ticks: nothing.
    _silent_tick(tracker, 66)
    assert tracker.data["formed"][key]["strength"] == 0.70
    assert "silence_windows_applied" not in tracker.data["formed"][key]

    # Co-fire at tick 66: the habit is exercised; last_tick moves and
    # the (absent) window count stays absent.
    changed, _ = tracker.observe_tick(
        tick=66, trigger="fear", action="withdraw",
        dominant_need="energy", need_deltas={"energy": -0.01})
    assert changed
    meta = tracker.data["formed"][key]
    assert meta["strength"] == 0.70  # at the cap, growth is a no-op
    assert meta["last_tick"] == 66
    assert "silence_windows_applied" not in meta

    # 99 further silent ticks after the co-fire (67..165): no decay.
    _silent_tick(tracker, 165)
    assert tracker.data["formed"][key]["strength"] == 0.70

    # The 100th silent tick after the co-fire (166): exactly one step.
    _silent_tick(tracker, 166)
    assert tracker.data["formed"][key]["strength"] == 0.65
    assert tracker.data["formed"][key]["silence_windows_applied"] == 1


def test_cofire_after_long_silence_is_not_charged():
    """Ordering pin (spec judgment call, documented in habits.py): the
    co-fire is processed before silence decay, so a habit that fires
    after 150 silent ticks is not charged for the windows that elapsed
    before it was exercised — the exercise ends the silence. The
    reverse order would charge the decay on the firing tick and, at
    the removal floor, could remove-then-re-crystallize in one tick."""
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.70, last_tick=16)
    key = "formed:fear:withdraw"

    changed, _ = tracker.observe_tick(
        tick=166, trigger="fear", action="withdraw",
        dominant_need="energy", need_deltas={"energy": -0.01})
    assert changed
    meta = tracker.data["formed"][key]
    assert meta["strength"] == 0.70
    assert meta["last_tick"] == 166
    assert "silence_windows_applied" not in meta
    assert tracker.data["archived"] == []


def test_cofire_drops_materialized_window_count():
    """A habit that decayed (field == 1), then co-fires, returns to its
    pristine entry shape: the field is dropped, not left at 0."""
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.70, last_tick=16)
    key = "formed:fear:withdraw"
    _silent_tick(tracker, 116)
    assert tracker.data["formed"][key]["silence_windows_applied"] == 1
    assert tracker.data["formed"][key]["strength"] == 0.65

    tracker.observe_tick(tick=117, trigger="fear", action="withdraw",
                         dominant_need="energy",
                         need_deltas={"energy": -0.01})
    meta = tracker.data["formed"][key]
    assert meta["strength"] == round(0.65 + 0.02, 6)
    assert meta["last_tick"] == 117
    assert "silence_windows_applied" not in meta

    # And the clock really restarted: 99 silent ticks after the co-fire
    # cause no decay.
    _silent_tick(tracker, 216)
    assert tracker.data["formed"][key]["strength"] == round(0.65 + 0.02, 6)


# -- fitness 4: removal below the floor, silence named -------------------------

def test_silence_decay_removes_below_floor_with_silence_note():
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.45, last_tick=3)
    key = "formed:fear:withdraw"

    _silent_tick(tracker, 103)
    assert tracker.data["formed"][key]["strength"] == 0.40
    _silent_tick(tracker, 203)
    assert tracker.data["formed"][key]["strength"] == 0.35
    # 0.30 is not < 0.30: the habit survives exactly at the floor.
    _silent_tick(tracker, 303)
    assert tracker.data["formed"][key]["strength"] == 0.30
    assert key in tracker.data["formed"]

    # Fourth window: 0.25 < 0.30 -> removed with a silence-named note.
    changed, state_changed = _silent_tick(tracker, 403)
    assert (changed, state_changed) == (True, True)
    assert key not in tracker.data["formed"]
    assert len(tracker.data["archived"]) == 1
    note = tracker.data["archived"][0]
    assert note["key"] == key
    assert note["peak"] == 0.45
    assert note["reason"] == REMOVAL_REASON_SILENCE
    assert note["reason"] != REMOVAL_REASON
    assert "silence" in note["reason"]
    assert note["lifespan_ticks"] == 400  # created tick 3 -> archived 403
    assert note["archived_tick"] == 403

    # The removed habit never fires again: reconcile drops it from
    # live state.habits (no phantom survives the removal).
    habits = {key: Habit(key=key, trigger="fear", action=Action("withdraw"),
                         strength=0.45, cooldown=2)}
    tracker.reconcile_state_habits(habits)
    assert habits == {}


def test_silence_decay_composes_with_disuse_decay():
    """Both decays feed the same floor in one tick: trigger fear drove
    tick 103 but conceal was chosen, 100 ticks after the last co-fire —
    silence -0.05 then disuse -0.05."""
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.45, last_tick=3)
    key = "formed:fear:withdraw"

    changed, state_changed = tracker.observe_tick(
        tick=103, trigger="fear", action="conceal",
        dominant_need="energy", need_deltas={"energy": -0.01})
    assert (changed, state_changed) == (True, True)
    meta = tracker.data["formed"][key]
    assert meta["strength"] == 0.35  # 0.45 - 0.05 (silence) - 0.05 (disuse)
    assert meta["silence_windows_applied"] == 1
    # One (fear, conceal) tick cannot crystallize a competing habit.
    assert "formed:fear:conceal" not in tracker.data["formed"]


# -- fitness 5: a regularly firing habit is pristine ---------------------------

def test_regularly_firing_habit_entry_unchanged_end_to_end():
    """Form (fear -> withdraw) and grow it to the 0.70 cap over 16 ticks
    — a co-fire on every tick, far inside every 100-tick window. The
    strength trajectory matches the pristine values exactly and the
    sidecar entry carries no silence_windows_applied field at all."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    strengths = []
    for _ in range(16):
        _script_tick(subject, needs, pressures, baselines)
        h = subject.engine.state.habits.get(key)
        strengths.append(h.strength if h else None)
    assert strengths[2] == 0.45  # crystallize on tick 3
    assert strengths[3] == round(0.45 + 0.02, 6)
    assert strengths[-1] == GROWTH_CAP == 0.70
    assert max(s for s in strengths if s is not None) == 0.70

    entry = _sidecar(tmp)["formed"][key]
    assert entry == {"trigger": "fear", "action": "withdraw",
                     "strength": 0.70, "peak": 0.70, "created_tick": 3,
                     "cofires": 16, "last_tick": 16}, entry
    assert "silence_windows_applied" not in entry


# -- fitness 1 + 2 end to end ---------------------------------------------------

def test_silence_decay_end_to_end_boundary():
    """Form to the 0.70 cap (16 ticks), then silent hunger ticks: 99
    silent ticks leave 0.70 untouched; the 100th costs one step."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    for _ in range(16):
        result = _script_tick(subject, needs, pressures, baselines)
        assert result["action"] == "withdraw"
    assert subject.engine.state.habits[key].strength == 0.70

    for _ in range(99):  # ticks 17..115
        result = _script_tick(subject, _needs(hunger=0.80),
                              _pressures(), _baselines())
        assert result["action"] == "seek_contact"
    assert subject.engine.state.habits[key].strength == 0.70
    assert "silence_windows_applied" not in _sidecar(tmp)["formed"][key]

    result = _script_tick(subject, _needs(hunger=0.80),
                          _pressures(), _baselines())  # tick 116
    assert result["tick"] == 116
    assert subject.engine.state.habits[key].strength == 0.65
    assert _sidecar(tmp)["formed"][key]["silence_windows_applied"] == 1


def test_silence_removal_end_to_end():
    """Form at 0.45 (3 ticks), then 400 silent hunger ticks: the habit
    decays 0.40 / 0.35 / 0.30 and is removed at 0.25 with a
    silence-named archival note; a fresh subject sees no formed habits."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    for _ in range(3):
        result = _script_tick(subject, needs, pressures, baselines)
        assert result["action"] == "withdraw"
    assert subject.engine.state.habits[key].strength == 0.45

    last = None
    for _ in range(400):  # ticks 4..403
        last = _script_tick(subject, _needs(hunger=0.80),
                            _pressures(), _baselines())
        assert last["action"] == "seek_contact"
    assert last["tick"] == 403
    assert key not in subject.engine.state.habits
    sc = _sidecar(tmp)
    assert key not in sc["formed"]
    assert len(sc["archived"]) == 1
    note = sc["archived"][0]
    assert note["key"] == key
    assert note["reason"] == REMOVAL_REASON_SILENCE
    assert note["archived_tick"] == 403
    # Removal survives the payload round-trip: a fresh subject sees no
    # trace of the removed habit. (Scoped to the removed key: over 400
    # silent hunger ticks the feeder's food events — every 48 ticks,
    # hunger -0.35 — legitimately let (hunger, seek_contact) crystallize
    # by the formation gate; that is formation working as designed, not
    # a residue of the removed habit.)
    fresh = _make_subject(tmp)
    assert key not in _formed_keys(fresh)


def test_cofire_reset_end_to_end():
    """Form to 0.70, 50 silent ticks, one co-fire, then 99 silent ticks:
    no decay anywhere in the run; the 100th silent tick after the
    co-fire costs one step."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    for _ in range(16):
        _script_tick(subject, needs, pressures, baselines)
    for _ in range(50):  # ticks 17..66
        _script_tick(subject, _needs(hunger=0.80), _pressures(), _baselines())
    assert subject.engine.state.habits[key].strength == 0.70

    result = _script_tick(subject, needs, pressures, baselines)  # tick 67
    assert result["action"] == "withdraw"
    assert result["tick"] == 67
    assert subject.engine.state.habits[key].strength == 0.70

    for _ in range(99):  # ticks 68..166
        _script_tick(subject, _needs(hunger=0.80), _pressures(), _baselines())
    assert subject.engine.state.habits[key].strength == 0.70

    _script_tick(subject, _needs(hunger=0.80), _pressures(), _baselines())
    assert subject.engine.state.habits[key].strength == 0.65


# -- fitness 6: read-only commands and dream ticks ----------------------------

def _patched_cli(tmp: Path):
    """Point the CLI at synthetic paths, including the habits sidecar
    (every module-level path cmd_init touches must be redirected — routes
    through the canonical conftest.redirect_all_cli_paths)."""
    from conftest import redirect_all_cli_paths
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    habits = tmp / "habits-formed.json"
    salience = tmp / "salience.json"
    interoception = tmp / "interoception.json"
    familiarity = tmp / "familiarity.json"
    ambivalence = tmp / "ambivalence.json"
    proposals = tmp / "proposals"
    archive = tmp / "archive"
    provenance = tmp / "provenance.json"
    saved = redirect_all_cli_paths(tmp)
    saved["_subject"] = cli._subject
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(salience),
                              interoception_path=str(interoception),
                              familiarity_path=str(familiarity),
                              ambivalence_path=str(ambivalence),
                              habits_path=str(habits))

    cli._subject = make_subject
    return habits, saved


def _restore_cli(saved):
    for name, value in saved.items():
        setattr(cli, name, value)


def test_readonly_and_dream_never_apply_silence_decay():
    """99 silent ticks put the habit one tick from its first silence
    window; a read-only sweep (drift, status) plus three dream ticks
    must leave the sidecar byte-identical — and the very next silent
    tick must still cost exactly one step, proving the clock was not
    disturbed in either direction."""
    tmp = _tmp()
    habits, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        needs, pressures, baselines = _fear_script()
        for _ in range(16):
            with subject._transaction():
                _set_state(subject, needs, pressures, baselines)
            cli._run_tick(subject)
        key = "formed:fear:withdraw"
        assert subject.engine.state.habits[key].strength == 0.70

        for _ in range(99):  # ticks 17..115
            with subject._transaction():
                _set_state(subject, _needs(hunger=0.80),
                           _pressures(), _baselines())
            cli._run_tick(subject)
        assert subject.engine.state.habits[key].strength == 0.70
        assert habits.exists()
        before = habits.read_bytes()

        cli.cmd_drift(argparse.Namespace(window=30))
        cli.cmd_status(argparse.Namespace(raw=False))
        for _ in range(3):
            subject.dream_tick()
        assert habits.read_bytes() == before

        # The next silent tick (116) still costs exactly one step.
        with subject._transaction():
            _set_state(subject, _needs(hunger=0.80),
                       _pressures(), _baselines())
        result = cli._run_tick(subject)
        assert result["tick"] == 116
        assert subject.engine.state.habits[key].strength == 0.65
    finally:
        _restore_cli(saved)


# -- fitness 7: init --force -----------------------------------------------------

def test_init_force_wipes_silence_state():
    """A silence-decayed habit (field materialized, strength 0.65) is
    wiped with the rest of the sidecar on reseed; no silence state
    survives to attach to the reseeded incarnation's ticks."""
    tmp = _tmp()
    habits, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        needs, pressures, baselines = _fear_script()
        for _ in range(16):
            with subject._transaction():
                _set_state(subject, needs, pressures, baselines)
            cli._run_tick(subject)
        key = "formed:fear:withdraw"
        for _ in range(100):  # ticks 17..116
            with subject._transaction():
                _set_state(subject, _needs(hunger=0.80),
                           _pressures(), _baselines())
            cli._run_tick(subject)
        assert subject.engine.state.habits[key].strength == 0.65
        assert json.loads(habits.read_text(encoding="utf-8"))[
            "formed"][key]["silence_windows_applied"] == 1

        cli.cmd_init(argparse.Namespace(force=True))
        assert not habits.exists(), \
            "init --force must wipe the habits sidecar (genome: reseed reset)"
        fresh = cli._subject()
        assert _formed_keys(fresh) == set()
    finally:
        _restore_cli(saved)


# -- fitness 8: determinism -------------------------------------------------------

def test_silence_decay_determinism_byte_identical_sidecar():
    def _run():
        tmp = _tmp()
        tracker = _seed_tracker(tmp, strength=0.70, last_tick=16)
        _silent_tick(tracker, 116)
        _silent_tick(tracker, 216)
        tracker.observe_tick(tick=217, trigger="fear", action="withdraw",
                             dominant_need="energy",
                             need_deltas={"energy": -0.01})
        _silent_tick(tracker, 316)
        tracker.save()
        return (tmp / "habits-formed.json").read_bytes()

    assert _run() == _run()


# -- validation: the new field at load -------------------------------------------

def test_silence_field_missing_loads_as_zero():
    """Pre-mutation entries carry no silence_windows_applied key: they
    load fine, and silence decay applies from a default of 0 (the
    field materializes only when a window is actually charged)."""
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.70, last_tick=16)
    assert "silence_windows_applied" not in \
        tracker.data["formed"]["formed:fear:withdraw"]
    _silent_tick(tracker, 116)
    assert tracker.data["formed"]["formed:fear:withdraw"][
        "silence_windows_applied"] == 1


def test_silence_field_malformed_raises():
    """Strict-load discipline (genome: silent zero): a present-but-bad
    silence_windows_applied raises rather than silently mis-decaying."""
    variants = {
        "bool": True,
        "negative": -1,
        "string": "1",
        "float": 1.5,
    }
    for desc, value in variants.items():
        tmp = _tmp()
        path = tmp / "habits-formed.json"
        path.write_text(json.dumps({
            "window": [],
            "formed": {"formed:fear:withdraw": {
                "trigger": "fear", "action": "withdraw", "strength": 0.70,
                "peak": 0.70, "created_tick": 3, "cofires": 3,
                "last_tick": 16, "silence_windows_applied": value}},
            "archived": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="silence_windows_applied"):
            HabitFormationTracker(path)
        assert desc in variants  # loop honesty


def test_silence_field_zero_loads():
    """The boundary value 0 is tracker-writable: an explicit 0 loads
    exactly like an absent key."""
    tmp = _tmp()
    tracker = _seed_tracker(tmp, strength=0.70, last_tick=16,
                            silence_windows=0)
    assert tracker.data["formed"]["formed:fear:withdraw"][
        "silence_windows_applied"] == 0
    _silent_tick(tracker, 116)
    assert tracker.data["formed"]["formed:fear:withdraw"][
        "silence_windows_applied"] == 1
