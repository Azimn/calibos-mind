"""Tests for the foregone-option trace for habit bypasses.

Spec: hidden_files/spec-counterfactual-trace-2026-10-09.md —
calibos_mind/counterfactual.py. All fixtures live in /tmp — the live
store is never touched.

Fitness functions under test:
  1. A scripted habit-bypass tick -> exactly one record naming the
     correct foregone action (withdraw chosen where deliberation would
     say conceal, proven by the trust<0.25 rule + twin control).
  2. Clear-margin ticks (habit action == options[0]), apology ticks, and
     CONCEAL ticks with no habit -> zero records, sidecar never created.
  3. Fidelity: deliberated_choice matches the pristine engine's
     no-habit selection across a battery of scenarios (channel rule,
     deadband boundary, trust override, concern keys, unknown keys,
     empty lists); NEED_ACTIONS/PRESSURE_ACTIONS are the real tables,
     imported, never copied.
  4. 100-tick soak with periodic bypasses -> records only on bypass
     ticks with a displaced alternative (4% < 5%).
  5. Determinism: identical sequences -> byte-identical sidecar bytes.
  6. Read-only commands (drift, status, review, consolidate dry-run)
     never write the sidecar; dream ticks never write; init --force
     wipes it.
  7. Zero behavior change: action selection byte-identical with and
     without the observer; full suite green (run separately).

Scenario control: the tests pin engine.state.needs / pressures /
pressure_baselines directly (concerns cleared), then drive selection
through engine.select_conduct (the same selector the heartbeat uses) or
through cli._run_tick (the real wiring). The canonical bypass scenario
is the fear script from tests/test_habits.py: a formed (fear -> withdraw)
habit at 0.65, then a low-trust tick (trust 0.10 < 0.25) where the
engine's own rule would choose CONCEAL but the firing habit returns
WITHDRAW.

Urgency recap (digital_subject/engine.py, jelly_psiduck-0.2.0a2):
  needs: thirst/hunger/pain/fatigue/loneliness -> urgency = value;
         energy/comfort/warmth/safety/focus/satisfaction -> 1 - value.
  pressures: magnitude = |value - baseline|.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
import calibos_mind.counterfactual as CF
from calibos_mind.counterfactual import (
    CHANNEL_DEADBAND,
    CHANNEL_RULE_BYPASS_KINDS,
    COUNTERFACTUAL_CAP,
    HABIT_FIRE_STRENGTH,
    TRUST_CONCEAL_THRESHOLD,
    CounterfactualTracker,
    deliberated_choice,
    install_observer,
    _fired_habit,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject import engine as engine_module
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Action, Event, Habit


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="cf-test-"))


def _make_subject(tmp: Path, with_counterfactual: bool = True,
                  with_habits: bool = False) -> CalibosSubject:
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
        habits_path=(str(tmp / "habits-formed.json") if with_habits else None),
        counterfactual_path=(str(tmp / "counterfactuals.json")
                              if with_counterfactual else None),
    )


def _needs(**over):
    """Full need dict; override the dominant need per tick.

    Competitors stay low so the chosen need dominates by a wide margin.
    """
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


def _fear_script(trust=0.5):
    """fear 0.95 (baseline 0.0) beats energy-urgency 0.80 by the 0.12
    deadband: pressure channel wins, options = PRESSURE_ACTIONS["fear"]
    (withdraw, conceal, remain_silent). With trust < 0.25 the deliberated
    choice is conceal instead. No authored habit has trigger "fear"."""
    return (_needs(energy=0.20), _pressures(fear=0.95, trust=trust),
            _baselines(fear=0.0))


def _inject_habit(subject, *, key="injected:fear:withdraw", trigger="fear",
                  action=Action.WITHDRAW, strength=0.65, cooldown=0):
    subject.engine.state.habits[key] = Habit(
        key=key, trigger=trigger, action=action, strength=strength,
        cooldown=cooldown, last_used_tick=-10_000)


def _select(subject, kind="message"):
    """One intention selection on the next tick through the observed path."""
    subject.engine.state.tick += 1
    action = subject.engine.select_conduct(
        Event(kind, "tester", "a test event worth noticing"))
    tracker = subject.workspace.counterfactual_tracker
    tracker.flush()
    return action


def _sidecar(tmp: Path):
    p = tmp / "counterfactuals.json"
    return json.loads(p.read_text(encoding="utf-8"))["records"] if p.exists() else None


# -- fitness 1: scripted bypass -> exactly one record -------------------------

def test_scripted_bypass_records_foregone_action():
    tmp = _tmp()
    subject = _make_subject(tmp)
    _inject_habit(subject)
    needs, pressures, baselines = _fear_script(trust=0.10)
    _set_state(subject, needs, pressures, baselines)
    action = _select(subject)
    assert action == Action.WITHDRAW, action
    records = _sidecar(tmp)
    assert records is not None and len(records) == 1, records
    r = records[0]
    assert r == {
        "tick": 1,
        "habit_key": "injected:fear:withdraw",
        "trigger_channel": "pressure",
        "chosen_action": "withdraw",
        "foregone_action": "conceal",
        "dominant_need": "energy",
        "dominant_pressure": "fear",
        "margin": 0.15,
    }, r


def test_fired_habit_detection_unit():
    # The stamp is read-only proof: only the bypass branch writes it
    # (engine.py:350), so detection needs no other engine cooperation.
    tmp = _tmp()
    subject = _make_subject(tmp)
    engine = subject.engine
    engine.state.tick = 7
    _inject_habit(subject, key="h", strength=0.65)
    engine.state.habits["h"].last_used_tick = 7
    assert _fired_habit(engine, Action.WITHDRAW)[0] == "h"
    # Stale stamp (previous tick): not this tick's bypass.
    engine.state.habits["h"].last_used_tick = 6
    assert _fired_habit(engine, Action.WITHDRAW) is None
    # Below the fire threshold, or a different action: no detection.
    engine.state.habits["h"].last_used_tick = 7
    engine.state.habits["h"].strength = 0.64
    assert _fired_habit(engine, Action.WITHDRAW) is None
    engine.state.habits["h"].strength = 0.65
    assert _fired_habit(engine, Action.CONCEAL) is None


# -- fitness 2: no-record cases -------------------------------------------------

def test_clear_margin_habit_equals_deliberated_no_record():
    # The habit fires (stamp proves it) but withdraw IS the deliberated
    # choice at trust 0.5: the bypass displaced nothing — no foregone
    # option, no record, and the sidecar file is never even created.
    tmp = _tmp()
    subject = _make_subject(tmp)
    _inject_habit(subject)
    needs, pressures, baselines = _fear_script(trust=0.5)
    _set_state(subject, needs, pressures, baselines)
    action = _select(subject)
    assert action == Action.WITHDRAW, action
    h = subject.engine.state.habits["injected:fear:withdraw"]
    assert h.last_used_tick == subject.engine.state.tick == 1, \
        "the bypass must have fired for this to be a real clear-margin case"
    tracker = subject.workspace.counterfactual_tracker
    assert tracker.pending == 0
    assert _sidecar(tmp) is None


def test_apology_tick_no_record():
    # The frozen engine bypasses the channel rule entirely for apology
    # events (engine.py:337-338 -> REPAIR): no deliberated alternative
    # exists to forgo, so the observer must stay silent even with a
    # hair-trigger habit staged.
    tmp = _tmp()
    subject = _make_subject(tmp)
    _inject_habit(subject)
    needs, pressures, baselines = _fear_script(trust=0.10)
    _set_state(subject, needs, pressures, baselines)
    action = _select(subject, kind="apology")
    assert action == Action.REPAIR, action
    tracker = subject.workspace.counterfactual_tracker
    assert tracker.pending == 0
    assert not (tmp / "counterfactuals.json").exists()


def test_conceal_tick_without_habit_no_record():
    # trust < 0.25 with no formed habit: the engine deliberates CONCEAL
    # on its own — no bypass fired, nothing displaced, no record.
    tmp = _tmp()
    subject = _make_subject(tmp)
    needs, pressures, baselines = _fear_script(trust=0.10)
    _set_state(subject, needs, pressures, baselines)
    action = _select(subject)
    assert action == Action.CONCEAL, action
    tracker = subject.workspace.counterfactual_tracker
    assert tracker.pending == 0
    assert not (tmp / "counterfactuals.json").exists()


def test_no_habit_no_record():
    tmp = _tmp()
    subject = _make_subject(tmp)
    needs, pressures, baselines = _fear_script(trust=0.5)
    _set_state(subject, needs, pressures, baselines)
    action = _select(subject)
    assert action == Action.WITHDRAW, action
    assert _sidecar(tmp) is None


def test_weak_habit_below_threshold_no_record():
    # 0.64 < 0.65: the engine's fire rule does not engage; the
    # deliberated CONCEAL is chosen by deliberation itself — no bypass,
    # no record.
    tmp = _tmp()
    subject = _make_subject(tmp)
    _inject_habit(subject, strength=0.64)
    needs, pressures, baselines = _fear_script(trust=0.10)
    _set_state(subject, needs, pressures, baselines)
    action = _select(subject)
    assert action == Action.CONCEAL, action
    h = subject.engine.state.habits["injected:fear:withdraw"]
    assert h.last_used_tick == -10_000, "the bypass must NOT have fired"
    assert _sidecar(tmp) is None


def test_deliberated_choice_rejects_apology_and_bad_trust():
    # Apology: no channel selection ran -> None (never a record).
    assert deliberated_choice(event_kind="apology", needs=[("thirst", 0.72)],
                              pressures=[("fear", 0.95)],
                              trust_value=0.5) is None
    assert "apology" in CHANNEL_RULE_BYPASS_KINDS
    # Non-finite trust: the engine's own trust read would be garbage —
    # missed record is the safe direction.
    assert deliberated_choice(event_kind="message", needs=[("thirst", 0.72)],
                              pressures=[("fear", 0.95)],
                              trust_value=float("nan")) is None


# -- fitness 3: fidelity battery --------------------------------------------------

def test_real_tables_imported_not_copied():
    # Spec fidelity rule: import the real tables from
    # digital_subject.engine, never copy them. Identity, not equality.
    assert CF.NEED_ACTIONS is engine_module.NEED_ACTIONS
    assert CF.PRESSURE_ACTIONS is engine_module.PRESSURE_ACTIONS
    assert CHANNEL_DEADBAND == 0.12
    assert HABIT_FIRE_STRENGTH == 0.65
    assert TRUST_CONCEAL_THRESHOLD == 0.25


def _pristine_deliberated(tmp: Path, kind, needs, pressures, baselines,
                          trust=None, hand_lists=None):
    """The pristine engine's own no-habit selection for one scenario.

    A twin subject with habits cleared: its _choose_intention IS the
    deliberated path (the habit branch can find nothing). The wrapper is
    not installed on the twin (counterfactual_path=None), so the pristine
    method is called directly with the exact triage lists.
    """
    twin = _make_subject(tmp, with_counterfactual=False)
    twin.engine.state.habits = {}
    _set_state(twin, needs, pressures, baselines)
    if trust is not None:
        twin.engine.state.pressures["trust"] = trust
    if hand_lists is not None:
        triage_needs, triage_pressures = hand_lists
    else:
        triage_needs = twin.engine._triage_needs()
        triage_pressures = twin.engine._triage_pressures()
    expected = twin.engine._choose_intention(
        Event(kind, "tester", "x"), triage_needs, triage_pressures)
    got = deliberated_choice(
        event_kind=kind, needs=triage_needs, pressures=triage_pressures,
        trust_value=twin.engine.state.pressures.get("trust", 0.5))
    return got, expected


def test_deliberated_choice_matches_pristine_engine():
    # Pressure channel wins by a clear margin.
    got, expected = _pristine_deliberated(
        _tmp(), "message", *_fear_script(trust=0.5))
    assert got[0] == expected == Action.WITHDRAW, (got, expected)
    assert got[1] == "pressure" and round(got[4], 4) == round(0.95 - 0.80, 4)
    # Need channel wins: thirst dominant.
    got, expected = _pristine_deliberated(
        _tmp(), "message",
        _needs(thirst=0.72), _pressures(fear=0.10), _baselines())
    assert got[0] == expected == Action.SEEK_CONTACT, (got, expected)
    assert got[1] == "need"
    # Low trust preempts with CONCEAL when available (fear options).
    got, expected = _pristine_deliberated(
        _tmp(), "message", *_fear_script(trust=0.10))
    assert got[0] == expected == Action.CONCEAL, (got, expected)
    # Low trust, CONCEAL not in the need options (thirst): options[0].
    got, expected = _pristine_deliberated(
        _tmp(), "message",
        _needs(thirst=0.72), _pressures(fear=0.10), _baselines(), trust=0.10)
    assert got[0] == expected == Action.SEEK_CONTACT, (got, expected)
    # Deadband boundary: p == n + 0.12 exactly -> pressure wins (>=).
    got, expected = _pristine_deliberated(
        _tmp(), "message",
        _needs(energy=0.20), _pressures(fear=0.92), _baselines(fear=0.0))
    assert got[0] == expected == Action.WITHDRAW, (got, expected)
    # Just inside the deadband -> need channel (energy options[0]).
    got, expected = _pristine_deliberated(
        _tmp(), "message",
        _needs(energy=0.20), _pressures(fear=0.91), _baselines(fear=0.0))
    assert got[0] == expected == Action.REST, (got, expected)
    assert got[1] == "need"
    # Unknown need key -> NEED_ACTIONS default (ANSWER, ASK, OBSERVE).
    got, expected = _pristine_deliberated(
        _tmp(), "message",
        {"novel_drive": 0.90, "energy": 0.95}, _pressures(fear=0.10),
        _baselines())
    assert got[0] == expected == Action.ANSWER, (got, expected)
    # Unknown pressure key -> PRESSURE_ACTIONS default (ANSWER, ASK,
    # DEFLECT); concern: prefix stripped for the lookup.
    got, expected = _pristine_deliberated(
        _tmp(), "message",
        _needs(thirst=0.72), _pressures(fear=0.10), _baselines(),
        hand_lists=([("thirst", 0.72)], [("concern:rent", 0.95)]))
    assert got[0] == expected == Action.ANSWER, (got, expected)
    assert got[3] == "concern:rent", got  # dominant key kept unstripped
    # Empty lists -> the engine's own defaults (("trust", 0.0) vs
    # ("curiosity", 0.0)): need channel, EXPLORE.
    got, expected = _pristine_deliberated(
        _tmp(), "message", _needs(), _pressures(), _baselines(),
        hand_lists=([], []))
    assert got[0] == expected == Action.EXPLORE, (got, expected)
    assert got[1] == "need" and got[2] == "curiosity" and got[3] == "trust"
    # Apology: pristine returns REPAIR; the mirror returns None (no
    # deliberated alternative exists).
    twin_tmp = _tmp()
    twin = _make_subject(twin_tmp, with_counterfactual=False)
    twin.engine.state.habits = {}
    rep = twin.engine._choose_intention(Event("apology", "t", "x"), [], [])
    assert rep == Action.REPAIR
    assert deliberated_choice(event_kind="apology", needs=[], pressures=[],
                              trust_value=0.5) is None


# -- fitness 4: 100-tick soak -----------------------------------------------------

def test_100_tick_soak_records_only_genuine_bypasses():
    # 100 ticks, one selection per tick: base fear script (habit fires,
    # foregone == chosen -> no record), trust dips at ticks 25/50/75/90
    # (habit fires, foregone=conceal -> record), curiosity-dominant ticks
    # at 10/60 (need channel, no firing habit matches -> no record; the
    # authored curious_question fires there with foregone == chosen).
    # Expected: exactly 4 records (4% < 5%), all at the dip ticks.
    tmp = _tmp()
    subject = _make_subject(tmp)
    _inject_habit(subject, cooldown=0)
    dip_ticks = {25, 50, 75, 90}
    curious_ticks = {10, 60}
    for tick in range(1, 101):
        if tick in curious_ticks:
            needs, pressures, baselines = (
                _needs(curiosity=0.90), _pressures(fear=0.05), _baselines())
        else:
            trust = 0.10 if tick in dip_ticks else 0.5
            needs, pressures, baselines = _fear_script(trust=trust)
        _set_state(subject, needs, pressures, baselines)
        subject.engine.state.tick += 1
        subject.engine.select_conduct(Event("message", "tester", "tick"))
    tracker = subject.workspace.counterfactual_tracker
    tracker.flush()
    records = _sidecar(tmp)
    assert records is not None, "expected 4 bypass records"
    assert len(records) == 4, [r["tick"] for r in records]
    assert len(records) / 100 < 0.05
    assert [r["tick"] for r in records] == sorted(dip_ticks), records
    for r in records:
        assert r["chosen_action"] == "withdraw", r
        assert r["foregone_action"] == "conceal", r
        assert r["habit_key"] == "injected:fear:withdraw", r
        assert r["trigger_channel"] == "pressure", r


# -- fitness 5: determinism --------------------------------------------------------

def test_identical_sequences_byte_identical_sidecar():
    def _run():
        tmp = _tmp()
        subject = _make_subject(tmp)
        _inject_habit(subject)
        for trust in (0.5, 0.10, 0.5):
            needs, pressures, baselines = _fear_script(trust=trust)
            _set_state(subject, needs, pressures, baselines)
            _select(subject)
        return (tmp / "counterfactuals.json").read_bytes()

    assert _run() == _run()


def test_sidecar_capped():
    tmp = _tmp()
    tracker = CounterfactualTracker(tmp / "counterfactuals.json")
    for i in range(COUNTERFACTUAL_CAP + 10):
        tracker.note({"tick": i, "habit_key": "h", "trigger_channel": "need",
                      "chosen_action": "rest", "foregone_action": "observe",
                      "dominant_need": "fatigue", "dominant_pressure": "trust",
                      "margin": -0.2})
    assert tracker.flush() is True
    records = _sidecar(tmp)
    assert len(records) == COUNTERFACTUAL_CAP, len(records)
    assert records[0]["tick"] == 10  # oldest evicted, newest kept
    assert records[-1]["tick"] == COUNTERFACTUAL_CAP + 9


# -- dream isolation ---------------------------------------------------------------

def test_dream_path_notes_nothing():
    tmp = _tmp()
    subject = _make_subject(tmp)
    _inject_habit(subject)
    subject.dream_tick()
    tracker = subject.workspace.counterfactual_tracker
    assert tracker.pending == 0
    assert not (tmp / "counterfactuals.json").exists()


def test_dreaming_guard_blocks_direct_selection():
    # dream_tick() never calls select_conduct, so the test above never
    # exercises the wrapper's _dreaming guard. Force it directly.
    tmp = _tmp()
    subject = _make_subject(tmp)
    _inject_habit(subject)
    needs, pressures, baselines = _fear_script(trust=0.10)
    _set_state(subject, needs, pressures, baselines)
    tracker = subject.workspace.counterfactual_tracker

    subject._dreaming = True
    try:
        action = subject.engine.select_conduct(
            Event("message", "tester", "dream input"))
    finally:
        subject._dreaming = False
    assert action == Action.WITHDRAW, action  # the tick itself is untouched
    assert tracker.pending == 0, "_dreaming guard failed to block noting"
    tracker.flush()
    assert _sidecar(tmp) is None

    # And the guard is the only thing that blocked it:
    _select(subject)
    assert _sidecar(tmp) is not None and len(_sidecar(tmp)) == 1


# -- observer hygiene: idempotence, restore, zero behavior change ------------------

def test_observer_survives_restore_without_double_counting():
    # _restore swaps in a fresh engine on every transaction; the observer
    # must be re-installed exactly once (a double wrap would note twice
    # per firing).
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        pass  # forces a _restore cycle
    assert getattr(subject.engine._choose_intention,
                   "_counterfactual_wrapped", False) is True
    # Inject AFTER the restore: the injection is in-memory scenario
    # control, and a transaction would discard it (never persisted).
    _inject_habit(subject, cooldown=0)
    needs, pressures, baselines = _fear_script(trust=0.10)
    _set_state(subject, needs, pressures, baselines)
    subject.engine.state.tick = 1
    subject.engine.select_conduct(Event("message", "tester", "one"))
    tracker = subject.workspace.counterfactual_tracker
    assert tracker.pending == 1, "double-wrapped observer would note twice"
    install_observer(subject)  # explicit re-install is a no-op
    subject.engine.state.tick = 2
    subject.engine.select_conduct(Event("message", "tester", "two"))
    assert tracker.pending == 2, "one record per genuine firing, not more"


def _scripted_observations(tmp: Path, with_counterfactual: bool):
    """Run a fixed script; return the engine's observable behavior stream.

    A and B differ ONLY in whether the observer is installed. Identical
    streams prove the wrapper never perturbs action selection.
    """
    subject = _make_subject(tmp, with_counterfactual=with_counterfactual)
    _inject_habit(subject, key="inj", cooldown=0)
    obs = []
    for trust in (0.5, 0.10, 0.5):
        needs, pressures, baselines = _fear_script(trust=trust)
        _set_state(subject, needs, pressures, baselines)
        subject.engine.state.tick += 1
        action = subject.engine.select_conduct(
            Event("message", "tester", "scripted selection"))
        tracker = getattr(subject.workspace, "counterfactual_tracker", None)
        if tracker is not None:
            tracker.flush()
        obs.append({
            "action": action.value,
            "intention": subject.engine.state.last_intention.value,
            "needs": sorted((k, round(v, 4))
                            for k, v in subject.engine.state.needs.items()),
            "pressures": sorted((k, round(v, 4))
                                for k, v in subject.engine.state.pressures.items()),
            "stamps": sorted((k, h.last_used_tick)
                             for k, h in subject.engine.state.habits.items()),
        })
    return obs


def test_action_selection_byte_identical_to_pristine():
    obs_a = _scripted_observations(_tmp(), with_counterfactual=True)
    obs_b = _scripted_observations(_tmp(), with_counterfactual=False)
    assert obs_a == obs_b
    # And the script actually bypassed (guard against a vacuous pass):
    assert [o["action"] for o in obs_a] == ["withdraw", "withdraw", "withdraw"]
    assert obs_a[1]["stamps"] and any(
        k == "inj" and v == 2 for k, v in obs_a[1]["stamps"])


# -- genome: CLI wiring, reseed reset, read-only discipline -------------------------

def _patched_cli(tmp: Path):
    """Point the CLI at synthetic paths, including the counterfactual sidecar.

    Redirects the complete module-level path set cmd_init touches (2026-10-02):
    a helper that leaves any path pointed at the live checkout lets
    cmd_init reset or wipe live mind state. Mirrors
    tests/test_ambivalence.py's helper with the counterfactual sidecar added.
    """
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    salience = tmp / "salience.json"
    interoception = tmp / "interoception.json"
    familiarity = tmp / "familiarity.json"
    ambivalence = tmp / "ambivalence.json"
    counterfactual = tmp / "counterfactuals.json"
    habits = tmp / "habits-formed.json"
    provenance = tmp / "provenance.json"
    proposals = tmp / "proposals"
    archive = tmp / "archive"
    saved = (cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY,
             cli.AMBIVALENCE, cli.COUNTERFACTUAL, cli.HABITS, cli.PROVENANCE,
             cli.PROPOSALS, cli.ARCHIVE, cli._subject)
    cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY, \
        cli.AMBIVALENCE, cli.COUNTERFACTUAL, cli.HABITS, cli.PROVENANCE, \
        cli.PROPOSALS, cli.ARCHIVE = (
            db, inbox, salience, interoception, familiarity, ambivalence,
            counterfactual, habits, provenance, proposals, archive)
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(tmp / "salience.json"),
                              interoception_path=None,
                              familiarity_path=None,
                              ambivalence_path=str(ambivalence),
                              habits_path=str(habits),
                              counterfactual_path=str(counterfactual))

    cli._subject = make_subject
    return counterfactual, saved


def _restore_cli(saved):
    (cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY,
     cli.AMBIVALENCE, cli.COUNTERFACTUAL, cli.HABITS, cli.PROVENANCE,
     cli.PROPOSALS, cli.ARCHIVE, cli._subject) = saved


def _natural_bypass(subject):
    """Form (fear -> withdraw) through the real formation path, then run
    the low-trust bypass tick: 13 fear ticks grow the formed habit to
    0.65; tick 14 with trust 0.10 fires it against a deliberated CONCEAL.
    Mirrors tests/test_habits.py's automaticity script; returns the tick
    result of the bypass tick."""
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script(trust=0.5)
    for _ in range(13):
        with subject._transaction():
            _set_state(subject, needs, pressures, baselines)
        result = cli._run_tick(subject)
        assert result["action"] == "withdraw", result["action"]
    assert subject.engine.state.habits[key].strength == 0.65
    low_trust = _pressures(fear=0.95, trust=0.10)
    with subject._transaction():
        _set_state(subject, needs, low_trust, baselines)
    result = cli._run_tick(subject)
    assert result["action"] == "withdraw", result["action"]
    h = subject.engine.state.habits[key]
    assert h.last_used_tick == result["tick"]
    return result


def test_run_tick_wiring_flushes_through_cli():
    # End-to-end through cli._run_tick with no injected state: the habit
    # forms through the real formation path, the bypass fires on the
    # low-trust tick, and exactly one record lands in the sidecar naming
    # the displaced deliberated alternative.
    tmp = _tmp()
    counterfactual, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        subject.enqueue(Event("message", "tester", "wired tick"))
        result = _natural_bypass(subject)
        records = json.loads(counterfactual.read_text(
            encoding="utf-8"))["records"]
        assert len(records) == 1, records
        r = records[0]
        assert r["tick"] == result["tick"], r
        assert r["habit_key"] == "formed:fear:withdraw", r
        assert r["chosen_action"] == "withdraw", r
        assert r["foregone_action"] == "conceal", r
        assert r["trigger_channel"] == "pressure", r
        assert r["dominant_need"] == "energy", r
        assert r["dominant_pressure"] == "fear", r
        # Exact margin equality is asserted in the unit test
        # (test_scripted_bypass_records_foregone_action), where the
        # selection runs with no heartbeat preamble. Here the heartbeat's
        # advance_body (homeostasis + pressure decay) moves needs between
        # the test's pin and the engine's own triage, so the record must
        # instead be consistent with the decision it reports: a
        # pressure-channel win requires margin >= the 0.12 deadband, and
        # the sign must favor pressure.
        assert r["margin"] >= 0.12, r
        assert r["margin"] > 0, r
    finally:
        _restore_cli(saved)


def test_init_force_wipes_sidecar():
    tmp = _tmp()
    counterfactual, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        subject.enqueue(Event("message", "tester", "reseed me"))
        _natural_bypass(subject)
        assert counterfactual.exists(), "wiring must flush records on ticks"
        assert len(json.loads(counterfactual.read_text(
            encoding="utf-8"))["records"]) == 1
        cli.cmd_init(argparse.Namespace(force=True))
        assert not counterfactual.exists(), \
            "init --force must wipe the counterfactual sidecar (genome: reseed reset)"
    finally:
        _restore_cli(saved)


def test_readonly_commands_do_not_write_sidecar():
    tmp = _tmp()
    counterfactual, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        assert not counterfactual.exists()
        (tmp / "proposals").mkdir(exist_ok=True)
        (tmp / "archive").mkdir(exist_ok=True)
        cli.cmd_drift(argparse.Namespace(window=30))
        cli.cmd_status(argparse.Namespace(raw=False))
        cli.cmd_review(argparse.Namespace(n=5))
        cli.cmd_consolidate(argparse.Namespace(
            list=False, accept=None, reject=None, quarantine=None, reason=""))
        assert not counterfactual.exists(), \
            "read-only commands must not create the sidecar"
    finally:
        _restore_cli(saved)
