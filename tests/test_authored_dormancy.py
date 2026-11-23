"""Tests for authored-habit dormancy demotion (domain 27: developmental history).

Spec: research/specs/2026-10-21-authored-habit-dormancy-demotion.md —
calibos_mind/authored_dormancy.py. All fixtures live in /tmp — the live
store is never touched.

An authored habit above the engine's 0.65 bypass threshold that has not
fired for DORMANCY_TICKS (300) loses the bypass privilege: live strength
goes to exactly 0.64, a demoted flag latches in the sidecar, and exactly
one temporal record (generated_by=cognition) names the habit key, the
dormancy span in ticks, and the authored baseline. A proven firing (the
engine's own last_used_tick stamp == current tick), pre-demotion only,
resets the dormancy clock — silently. Demotion is a one-way ratchet:
the flag latches permanently, no restore path exists, and the note
never promises restoration. The
formed: namespace is never touched. Read-only commands and dream ticks
never demote, never mint, never write the sidecar.

Scenario control (probed against the frozen install):
- hunger 0.80 dominant, pressures low -> no authored habit can fire
  (triggers curiosity/uncertainty never match the hunger/trust channel),
  action "seek_contact". The quiet tick: demotion without interference.
- curiosity 0.95 dominant, pressures low -> curious_question (0.72,
  trigger curiosity, action explore) takes the bypass branch when
  eligible: strength >= 0.65, cooldown 2 satisfied, explore in the
  curiosity channel options (EXPLORE, ASK, OBSERVE).
- The feeder fires a food event only when maybe_feed sees tick 48; tests
  keep their ticks away from 48.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.authored_dormancy import (
    DEMOTED_STRENGTH,
    DORMANCY_TICKS,
    AuthoredDormancyTracker,
    demotion_note_text,
    is_demotion_note_text,
    mint_demotion_note,
    parse_demotion_note_text,
)
from calibos_mind.counterfactual import HABIT_FIRE_STRENGTH
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Action, Event, Habit


CQ = "curious_question"          # baseline 0.72, trigger curiosity
VBC = "verify_before_claiming"  # baseline 0.68, trigger uncertainty


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="dormancy-test-"))


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
        dormancy_path=str(tmp / "authored-dormancy.json"),
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


def _quiet_tick(subject):
    """A waking tick on which no authored habit can fire."""
    with subject._transaction():
        _set_state(subject, _needs(hunger=0.80), _pressures(), _baselines())
    return cli._run_tick(subject)


def _curiosity_tick(subject):
    """A waking tick on which curious_question's bypass is eligible."""
    with subject._transaction():
        _set_state(subject, _needs(curiosity=0.95), _pressures(), _baselines())
    return cli._run_tick(subject)


def _jump(subject, tick: int):
    with subject._transaction():
        subject.engine.state.tick = tick


def _seed_dormancy_sidecar(tmp: Path, **entries):
    """Write a dormancy sidecar with explicit per-habit entries."""
    path = tmp / "authored-dormancy.json"
    path.write_text(json.dumps({"habits": entries}), encoding="utf-8")
    return path


def _entry(key, baseline, last_fired_tick, demoted=False,
           demoted_tick=None, owed=None):
    return {key: {"baseline": baseline, "last_fired_tick": last_fired_tick,
                  "demoted": demoted, "demoted_tick": demoted_tick,
                  "owed": owed}}


def _demotion_notes(subject):
    return [r for r in subject.workspace.records
            if r.source == "temporal" and r.generated_by == "cognition"
            and is_demotion_note_text(r.first_person)]


def _live_strength(subject, key):
    return subject.engine.state.habits[key].strength


def _patched_cli(tmp: Path):
    """Point every module-level path cmd_init/cmd_status touch at tmp.
    Routes through the canonical conftest.redirect_all_cli_paths (this
    helper's hand-maintained set omitted EXPECTATIONS and MOOD, which
    cmd_init also wipes)."""
    from conftest import redirect_all_cli_paths
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    saved = redirect_all_cli_paths(tmp)
    saved_subject = cli._subject
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(
            str(db), cartridge, cognition=provider,
            salience_path=str(tmp / "salience.json"),
            interoception_path=str(tmp / "interoception.json"),
            familiarity_path=str(tmp / "familiarity.json"),
            ambivalence_path=str(tmp / "ambivalence.json"),
            counterfactual_path=str(tmp / "counterfactuals.json"),
            habits_path=str(tmp / "habits-formed.json"),
            dormancy_path=str(tmp / "authored-dormancy.json"))

    cli._subject = make_subject

    def restore():
        for n, v in saved.items():
            setattr(cli, n, v)
        cli._subject = saved_subject
    return restore


# -- fitness 1: dormant authored habit demotes with exactly one record --------

def test_dormant_400_ticks_demotes_to_exactly_064_with_one_record():
    tmp = _tmp()
    # curious_question last fired at tick 0 (400 dormant at tick 400);
    # verify_before_claiming fired at 399 (the untouched control).
    _seed_dormancy_sidecar(
        tmp,
        **_entry(CQ, 0.72, 0),
        **_entry(VBC, 0.68, 399),
    )
    subject = _make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 399
        st.habits[CQ].last_used_tick = 0
        assert st.habits[CQ].strength == 0.72
    _quiet_tick(subject)  # -> tick 400, no firing possible
    # Strength demoted to exactly one notch below the bypass threshold.
    assert _live_strength(subject, CQ) == DEMOTED_STRENGTH == 0.64
    assert DEMOTED_STRENGTH < HABIT_FIRE_STRENGTH == 0.65
    # The untouched control is byte-identical in behavior.
    assert _live_strength(subject, VBC) == 0.68
    # The demotion persisted through the transaction restore.
    assert subject.inspect()["engine"]["habits"][CQ]["strength"] == 0.64
    # Sidecar flag set.
    sidecar = json.loads((tmp / "authored-dormancy.json").read_text())
    entry = sidecar["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 400
    assert entry["owed"] is None  # acked after the mint committed
    # Exactly one temporal record, key + span + baseline recoverable.
    notes = _demotion_notes(subject)
    assert len(notes) == 1, [r.first_person for r in notes]
    note = notes[0]
    assert note.source == "temporal" and note.generated_by == "cognition"
    assert "n't" not in note.first_person  # no contractions, genome rule
    parsed = parse_demotion_note_text(note.first_person)
    assert parsed is not None
    assert parsed["habit_key"] == CQ
    assert parsed["dormancy_ticks"] == 400
    assert parsed["baseline"] == pytest.approx(0.72)
    assert parsed["demoted_tick"] == 400
    # Round-trip: the canonical text is byte-identical from the parse.
    assert demotion_note_text(parsed) == note.first_person


# -- fitness 2: no second record, strength stays 0.64 --------------------------

def test_second_waking_tick_mints_no_second_record():
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 0),
                           **_entry(VBC, 0.68, 399))
    subject = _make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 399
        st.habits[CQ].last_used_tick = 0
    _quiet_tick(subject)   # tick 400: demotes
    _quiet_tick(subject)   # tick 401: nothing further
    assert len(_demotion_notes(subject)) == 1
    assert _live_strength(subject, CQ) == 0.64
    sidecar = json.loads((tmp / "authored-dormancy.json").read_text())
    entry = sidecar["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 400


# -- fitness 3 (ratchet): no code path restores a demoted habit ----------

def test_hand_set_stamp_post_demotion_restores_nothing():
    # The ratchet (critic round 2, RED 2): even a hand-set last_used_tick
    # stamp (in vivo impossible post-demotion — the frozen engine stamps
    # only the bypass branch at >= 0.65) leaves strength at 0.64, the
    # flag latched, and mints no second record. The restore-on-firing
    # path was removed entirely; this test pins its absence.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    # Demote at tick 400 (400 dormant ticks).
    events, sc, stc = tracker.observe_tick(tick=400, state_habits=live)
    assert len(events) == 1 and stc
    tracker.reconcile_state_habits(live, tick=400)
    assert live[CQ].strength == 0.64
    tracker.ack_demotion_notes(events)
    tracker.save()
    # Impossible stamp: hand-set last_used_tick == tick post-demotion.
    live[CQ].last_used_tick = 410
    events, sc, stc = tracker.observe_tick(tick=410, state_habits=live)
    assert events == [] and not sc and not stc  # the ratchet ignores it
    tracker.reconcile_state_habits(live, tick=410)
    assert live[CQ].strength == 0.64  # no restore path
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is True  # flag latched permanently
    assert entry["demoted_tick"] == 400  # the original episode stands
    assert entry["last_fired_tick"] == 0  # clock not reset by the stamp
    tracker.save()
    # Still demoted many ticks later: no second record, ever.
    live[CQ].last_used_tick = 410
    events, sc, stc = tracker.observe_tick(tick=900, state_habits=live)
    assert events == [] and not stc
    assert live[CQ].strength == 0.64
    assert tracker.data["habits"][CQ]["demoted"] is True


def test_pre_demotion_firing_resets_dormancy_clock():
    # Spec fitness 3 example: a genuine bypass firing at tick 250 resets
    # the clock — no demotion at tick 550, demotion at tick 551.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    # Firing proven at tick 250.
    live[CQ].last_used_tick = 250
    events, sc, stc = tracker.observe_tick(tick=250, state_habits=live)
    assert events == [] and not stc
    assert tracker.data["habits"][CQ]["last_fired_tick"] == 250
    # Tick 550: exactly 300 ticks since the firing — still no demotion.
    events, sc, stc = tracker.observe_tick(tick=550, state_habits=live)
    assert events == [] and not stc
    assert live[CQ].strength == 0.72
    assert tracker.data["habits"][CQ]["demoted"] is False
    # Tick 551: 301 ticks have elapsed — the dormancy bound expires,
    # demotion lands.
    events, sc, stc = tracker.observe_tick(tick=551, state_habits=live)
    assert len(events) == 1 and stc
    tracker.reconcile_state_habits(live, tick=551)
    assert live[CQ].strength == 0.64
    assert events[0]["dormancy_ticks"] == 301
    assert events[0]["demoted_tick"] == 551


def test_crash_between_save_and_reconcile_reprojects_on_recovery():
    # Critic round 2, RED 1: a kill after the dormancy sidecar save()
    # but before the reconcile transaction commits leaves demoted=True
    # (+owed) in the file while the store still holds the baseline
    # strength. On the recovery tick the owed mark re-mints the note;
    # observe_tick must report state_changed so the caller's reconcile
    # re-derives the projection — otherwise the minted note claims a
    # demotion that never behaviorally happened.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=400, state_habits=live)
    assert len(events) == 1 and stc
    tracker.save()  # write-ahead: demoted=True (+owed) durable...
    # ...KILL: reconcile never ran, mint never ran, ack never ran.
    # The store still holds the baseline strength.
    assert live[CQ].strength == 0.72
    # Recovery tick: a fresh tracker reads the crashed sidecar.
    recovered = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    assert recovered.data["habits"][CQ]["demoted"] is True
    assert recovered.data["habits"][CQ]["owed"] is not None
    events, sc, stc = recovered.observe_tick(tick=401, state_habits=live)
    assert len(events) == 1  # the owed episode re-mints its note
    assert events[0]["habit_key"] == CQ
    assert events[0]["demoted_tick"] == 400  # the original episode
    assert stc  # THE FIX: the projection must re-derive
    recovered.reconcile_state_habits(live, tick=401)
    assert live[CQ].strength == 0.64  # store converges; note stays truthful
    # The re-minted note is truthful: it claims demotion to 0.64, and the
    # store now holds 0.64. Minting is idempotent; the ack clears the mark.
    subject = _make_subject(tmp)
    with subject._transaction():
        first = mint_demotion_note(subject, events[0])
        second = mint_demotion_note(subject, events[0])
        assert first == second
    notes = _demotion_notes(subject)
    assert len(notes) == 1
    parsed = parse_demotion_note_text(notes[0].first_person)
    assert parsed["demoted_tick"] == 400
    assert parsed["habit_key"] == CQ
    recovered.ack_demotion_notes(events)
    recovered.save()
    reloaded = json.loads((tmp / "d.json").read_text())
    assert reloaded["habits"][CQ]["owed"] is None
    assert reloaded["habits"][CQ]["demoted"] is True


def test_live_firing_resets_clock_and_prevents_demotion():
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 390),
                           **_entry(VBC, 0.68, 399))
    subject = _make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 399
        st.habits[CQ].last_used_tick = 390  # cooldown 2 satisfied at 400
    result = _curiosity_tick(subject)  # tick 400: bypass eligible
    assert result["action"] == "explore"
    # The engine's own stamp proves the firing happened this tick.
    assert subject.engine.state.habits[CQ].last_used_tick == 400
    assert len(_demotion_notes(subject)) == 0
    assert _live_strength(subject, CQ) == 0.72
    sidecar = json.loads((tmp / "authored-dormancy.json").read_text())
    assert sidecar["habits"][CQ]["last_fired_tick"] == 400
    assert sidecar["habits"][CQ]["demoted"] is False


# -- fitness 4: dormant under the bound is untouched ---------------------------

def test_dormant_200_ticks_untouched():
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 200),
                           **_entry(VBC, 0.68, 399))
    subject = _make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 399
        st.habits[CQ].last_used_tick = 200
    _quiet_tick(subject)  # tick 400: only 200 dormant ticks
    assert len(_demotion_notes(subject)) == 0
    assert _live_strength(subject, CQ) == 0.72
    sidecar = json.loads((tmp / "authored-dormancy.json").read_text())
    entry = sidecar["habits"][CQ]
    assert entry["demoted"] is False and entry["last_fired_tick"] == 200


# -- fitness 5: the formed: namespace is never touched -------------------------

def test_formed_habit_dormant_400_ticks_left_byte_identical():
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    formed = Habit("formed:hunger:seek_contact", "hunger",
                   Action.SEEK_CONTACT, 0.70, 2, 0)
    live = {
        CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0),
        "formed:hunger:seek_contact": formed,
    }
    before = (formed.key, formed.trigger, formed.action, formed.strength,
              formed.cooldown, formed.last_used_tick)
    events, sc, stc = tracker.observe_tick(tick=400, state_habits=live)
    tracker.reconcile_state_habits(live, tick=400)
    # The authored habit demoted (the tracker works)...
    assert live[CQ].strength == 0.64 and len(events) == 1
    # ...while the formed habit is byte-identical: no strength change, no
    # stamp change, no sidecar entry, no event.
    after = (formed.key, formed.trigger, formed.action, formed.strength,
             formed.cooldown, formed.last_used_tick)
    assert before == after
    assert "formed:hunger:seek_contact" not in tracker.data["habits"]
    assert all(e["habit_key"] != "formed:hunger:seek_contact" for e in events)


def test_formed_prefix_filtered_from_authored_map():
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(
        tmp / "d.json", {"formed:x:y": 0.90, CQ: 0.72})
    assert tracker.authored == {CQ: 0.72}


def test_formed_habit_firing_stamp_ignored():
    # Even a proven bypass firing of a formed habit must not move the
    # dormancy tracker: that namespace belongs to the formation tracker.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {"formed:hunger:seek_contact": Habit(
        "formed:hunger:seek_contact", "hunger", Action.SEEK_CONTACT,
        0.70, 2, 500)}
    events, sc, stc = tracker.observe_tick(tick=500, state_habits=live)
    # The CQ entry seeds (fresh sidecar), but the formed firing moves
    # nothing: no event, no strength change, no formed: entry.
    assert events == [] and not stc
    assert list(tracker.data["habits"]) == [CQ]


# -- fitness 6: the privilege is actually gated, not relabeled -----------------

def test_bypass_branch_flips_at_the_threshold():
    """The frozen engine's own branch decides: 0.72 bypasses, 0.64 deliberates.

    A probe habit (trigger hunger, action ASK) is used because the real
    authored habits' deliberated fallbacks coincide with their bypass
    actions on their own channels; the branch under test is the engine's
    ``strength >= 0.65`` gate, identical for every habit.
    """
    tmp = _tmp()
    subject = _make_subject(tmp)
    engine = subject.engine
    probe = Habit("probe_habit", "hunger", Action.ASK, 0.72, 0,
                  last_used_tick=-10_000)
    with subject._transaction():
        _set_state(subject, _needs(hunger=0.95), _pressures(), _baselines())
        subject.engine.state.tick = 100
        # Inside the transaction on the live instance: pre-transaction
        # references are dead (_restore already swapped them).
        subject.engine.state.habits["probe_habit"] = probe
    # _restore swapped in a fresh engine instance: re-fetch it and the
    # probe (the pre-transaction references are dead).
    engine = subject.engine
    probe = engine.state.habits["probe_habit"]
    event = Event("temporal", "test", "probe")
    needs = engine._triage_needs()
    pressures = engine._triage_pressures()
    assert engine._choose_intention(event, needs, pressures) == Action.ASK
    # The bypass branch ran: the engine's own stamp proves it.
    assert probe.last_used_tick == 100
    # Demote through the real tracker at 302 dormant ticks.
    tracker = AuthoredDormancyTracker(tmp / "d.json", {"probe_habit": 0.72})
    events, sc, stc = tracker.observe_tick(
        tick=402, state_habits={"probe_habit": probe})
    assert len(events) == 1 and stc
    live = {"probe_habit": probe}
    tracker.reconcile_state_habits(live, tick=402)
    assert probe.strength == 0.64
    # Same channel, same engine: now the deliberated path wins.
    assert engine._choose_intention(event, needs, pressures) == \
        Action.SEEK_CONTACT


# -- fitness 7: read-only commands and dream ticks never write -----------------

def test_readonly_command_leaves_sidecar_byte_identical():
    tmp = _tmp()
    restore = _patched_cli(tmp)
    try:
        subject = cli._subject()
        subject.workspace.dormancy_tracker.save()
        path = tmp / "authored-dormancy.json"
        before = path.read_bytes()
        assert cli.cmd_status(argparse.Namespace(raw=True)) == 0
        assert path.read_bytes() == before
        # Constructing the subject (as every read-only command does)
        # only reads the sidecar.
        cli._subject()
        assert path.read_bytes() == before
    finally:
        restore()


def test_dream_tick_leaves_sidecar_byte_identical():
    tmp = _tmp()
    subject = _make_subject(tmp)
    subject.workspace.dormancy_tracker.save()
    path = tmp / "authored-dormancy.json"
    before = path.read_bytes()
    subject.dream_tick()
    assert path.read_bytes() == before


# -- fitness 8: init --force wipes the sidecar ---------------------------------

def test_init_force_wipes_dormancy_sidecar():
    tmp = _tmp()
    restore = _patched_cli(tmp)
    try:
        dorm = tmp / "authored-dormancy.json"
        dorm.write_text(json.dumps({"habits": {
            CQ: {"baseline": 0.72, "last_fired_tick": 0, "demoted": True,
                 "demoted_tick": 400, "owed": None}}}), encoding="utf-8")
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        assert not dorm.exists()
        # Post-wipe behavior matches a fresh tracker.
        fresh = AuthoredDormancyTracker(dorm, {CQ: 0.72})
        assert fresh.data == {"habits": {}}
    finally:
        restore()


def test_init_force_recovers_from_corrupt_dormancy_sidecar():
    # Fail-loud is only acceptable with a sanctioned recovery path:
    # cmd_init --force unlinks the sidecar BEFORE constructing the
    # subject, so a corrupt file is wiped rather than bricking _subject().
    tmp = _tmp()
    restore = _patched_cli(tmp)
    try:
        (tmp / "authored-dormancy.json").write_text("{not json", encoding="utf-8")
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        assert not (tmp / "authored-dormancy.json").exists()
    finally:
        restore()


# -- fitness 9: replay determinism ----------------------------------------------

def _replay(path: Path):
    tracker = AuthoredDormancyTracker(
        path, {CQ: 0.72, VBC: 0.68})
    live = {
        CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0),
        VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.68, 3, 50),
    }
    for tick in (100, 200, 350, 401, 402, 700):
        events, sc, stc = tracker.observe_tick(tick=tick, state_habits=live)
        tracker.reconcile_state_habits(live, tick=tick)
        tracker.ack_demotion_notes(events)
    tracker.save()


def test_identical_tick_sequences_byte_identical_sidecar():
    tmp = _tmp()
    _replay(tmp / "a.json")
    _replay(tmp / "b.json")
    assert (tmp / "a.json").read_bytes() == (tmp / "b.json").read_bytes()


# -- hostile: corrupt sidecars fail loud -----------------------------------------

@pytest.mark.parametrize("body", [
    "{not json",
    "[1, 2, 3]",
    '{"habits": [1]}',
    '{"habits": {"x": "not an object"}}',
    '{"habits": {"formed:a:b": {"baseline": 0.7, "last_fired_tick": 0, '
    '"demoted": False, "demoted_tick": null, "owed": null}}}',
    '{"habits": {"x": {"baseline": "high", "last_fired_tick": 0, '
    '"demoted": False, "demoted_tick": null, "owed": null}}}',
    '{"habits": {"x": {"baseline": 0.72, "last_fired_tick": -1, '
    '"demoted": "yes", "demoted_tick": null, "owed": null}}}',
    '{"habits": {"x": {"baseline": 0.72, "last_fired_tick": 0, '
    '"demoted": False, "demoted_tick": "soon", "owed": null}}}',
    '{"habits": {"x": {"baseline": 0.72, "last_fired_tick": 0, '
    '"demoted": False, "demoted_tick": null, '
    '"owed": {"habit_key": "other", "dormancy_ticks": 400, '
    '"baseline": 0.72, "demoted_tick": 400}}}}',
])
def test_corrupt_sidecar_raises(body):
    tmp = _tmp()
    path = tmp / "d.json"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        AuthoredDormancyTracker(path, {CQ: 0.72})


def test_missing_sidecar_is_fresh_start():
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    assert tracker.data == {"habits": {}}


# -- hostile: the floor and the threshold -----------------------------------------

def test_demotion_never_below_064():
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    # A habit already at/below the threshold cannot demote further: no
    # event, no strength change, no flag.
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=400, state_habits=live)
    assert events == [] and not stc
    assert live[CQ].strength == 0.64
    assert tracker.data["habits"][CQ]["demoted"] is False
    # And the reconcile projection never writes below the floor either.
    tracker.data["habits"][CQ]["demoted"] = True
    tracker.reconcile_state_habits(live, tick=400)
    assert live[CQ].strength == 0.64


def test_sub_threshold_baseline_never_tracked():
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {"weak": 0.50})
    live = {"weak": Habit("weak", "curiosity", Action.EXPLORE, 0.50, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=10_000, state_habits=live)
    assert events == [] and not sc and not stc
    assert tracker.data["habits"] == {}
    assert tracker.authored == {"weak": 0.50}


def test_constants_agree_with_engine():
    # The one-notch-below-floor contract, pinned against the engine's own
    # branch constant (imported, not redefined).
    assert HABIT_FIRE_STRENGTH == 0.65
    assert DEMOTED_STRENGTH == 0.64
    assert DEMOTED_STRENGTH < HABIT_FIRE_STRENGTH
    assert DORMANCY_TICKS == 300


# -- hostile: mint idempotency and owed recovery -----------------------------------

def test_mint_is_idempotent_on_episode_identity():
    tmp = _tmp()
    subject = _make_subject(tmp)
    event = {"habit_key": CQ, "dormancy_ticks": 400,
             "baseline": 0.72, "demoted_tick": 400}
    with subject._transaction():
        first = mint_demotion_note(subject, event)
        second = mint_demotion_note(subject, event)
        assert first == second
    assert len(_demotion_notes(subject)) == 1


def test_owed_episode_recovers_after_crash_before_mint():
    # Demotion saved the owed mark; the mint never ran (kill between save
    # and mint). The next tick recovers the episode and mints exactly one
    # record. (Round 2 ratchet: a post-demotion stamp is in vivo
    # impossible and ignored — the owed episode is untouched either way.)
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=400, state_habits=live)
    assert len(events) == 1
    tracker.save()  # write-ahead mark durable; mint never ran, no ack
    assert tracker.data["habits"][CQ]["owed"] is not None
    # A later tick recovers the owed episode untouched...
    live[CQ].last_used_tick = 401  # in vivo impossible post-demotion;
    live[CQ].strength = 0.64       # ignored by the ratchet
    recovered, _, _ = AuthoredDormancyTracker(
        tmp / "d.json", {CQ: 0.72}).observe_tick(tick=401, state_habits=live)
    assert len(recovered) == 1
    assert recovered[0]["habit_key"] == CQ
    assert recovered[0]["demoted_tick"] == 400


def test_owed_mint_then_ack_clears_it():
    tmp = _tmp()
    subject = _make_subject(tmp)
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, _, _ = tracker.observe_tick(tick=400, state_habits=live)
    tracker.save()
    with subject._transaction():
        mint_demotion_note(subject, events[0])
    fresh = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    fresh.ack_demotion_notes(events)
    fresh.save()
    reloaded = json.loads((tmp / "d.json").read_text())
    assert reloaded["habits"][CQ]["owed"] is None
    assert len(_demotion_notes(subject)) == 1


# -- hostile: note text discipline ---------------------------------------------------

def test_note_text_round_trip_and_rejections():
    event = {"habit_key": CQ, "dormancy_ticks": 400,
             "baseline": 0.72, "demoted_tick": 400}
    text = demotion_note_text(event)
    assert "n't" not in text
    assert "’" not in text and "‘" not in text
    parsed = parse_demotion_note_text(text)
    assert parsed == event
    assert demotion_note_text(parsed) == text
    assert is_demotion_note_text(text)
    # Tampering breaks the parse: prose/machine disagreement, wrong
    # floor, formed: key, non-note text.
    assert parse_demotion_note_text(
        text.replace("dormant 400 ticks", "dormant 401 ticks")) is None
    assert parse_demotion_note_text(
        text.replace("demoted to 0.640000", "demoted to 0.630000")) is None
    assert parse_demotion_note_text(
        text.replace(CQ, "formed:curiosity:explore")) is None
    assert parse_demotion_note_text("just a thought") is None
    assert parse_demotion_note_text(None) is None


def test_exactly_300_ticks_is_not_dormant():
    # The bound is strict: tick - last_fired_tick > 300.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 100)}
    events, _, stc = tracker.observe_tick(tick=400, state_habits=live)
    assert events == [] and not stc
    assert live[CQ].strength == 0.72


# -- lost-latch recovery (domain 28, 2026-11-21) ------------------------------
# Spec: research/specs/2026-11-21-dormancy-latch-recovery.md. The live
# store holds both demotion notes while the sidecar says demoted: False,
# and at 0.64 the demotion edge can never re-fire. Recovery restores
# the latch from the store's own note: demoted True, demoted_tick the
# note's tick, owed None. It never mints, never writes a live strength,
# never touches the formed: namespace, and runs on waking ticks only,
# before the heartbeat — so a fail-loud ValueError aborts before the
# tick advances. All fixtures live in /tmp; the live store is never
# touched.

def _note_record(key, baseline, span, tick):
    """A record-shaped object carrying one canonical demotion note.

    Carries the provenance a real minted note carries (source temporal,
    generated_by cognition): recovery trusts only such records (round 2
    hardening — a pasted canonical note text in a message record must
    not latch).
    """
    return SimpleNamespace(
        source="temporal", generated_by="cognition",
        first_person=demotion_note_text(
            {"habit_key": key, "dormancy_ticks": span, "baseline": baseline,
             "demoted_tick": tick}))


def test_recovery_restores_lost_latch_from_note():
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 0))
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    records = [_note_record(CQ, 0.72, 400, 400)]
    assert tracker.recover_lost_latches(
        tick=400, state_habits=live, records=records) is True
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 400
    assert entry["owed"] is None
    assert entry["last_fired_tick"] == 0  # the seeded clock stands
    assert live[CQ].strength == 0.64  # live strength never written
    assert len(records) == 1  # no new record minted
    # Second tick: idempotent, sidecar byte-identical.
    tracker.save()
    first = path.read_bytes()
    assert tracker.recover_lost_latches(
        tick=401, state_habits=live, records=records) is False
    tracker.save()
    assert path.read_bytes() == first


def test_recovery_recovers_both_live_habits_in_one_tick():
    # The exact live shape: curious_question 0.72/735 and
    # verify_before_claiming 0.68/735, sidecar demoted: False for both.
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 7),
                           **_entry(VBC, 0.68, -10000))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.64, 3, 0)}
    records = [_note_record(CQ, 0.72, 728, 735),
               _note_record(VBC, 0.68, 10735, 735)]
    assert tracker.recover_lost_latches(
        tick=736, state_habits=live, records=records) is True
    for key in (CQ, VBC):
        entry = tracker.data["habits"][key]
        assert entry["demoted"] is True and entry["demoted_tick"] == 735
        assert entry["owed"] is None
    assert live[CQ].strength == 0.64 and live[VBC].strength == 0.64
    assert len(records) == 2  # no new record minted


def test_recovery_seeds_fresh_sidecar_entry_in_same_tick():
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    assert tracker.data == {"habits": {}}
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    note = _note_record(CQ, 0.72, 400, 400)
    assert tracker.recover_lost_latches(
        tick=400, state_habits=live, records=[note]) is True
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 400
    assert entry["owed"] is None
    assert entry["last_fired_tick"] == 0  # seeded from the live stamp
    assert live[CQ].strength == 0.64


def test_recovery_no_note_raises_and_touches_nothing():
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 7))
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    before = path.read_bytes()
    data_before = json.loads(before)
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=735, state_habits=live, records=[])
    assert path.read_bytes() == before  # sidecar bytes untouched
    assert tracker.data == data_before  # no in-memory mutation either
    assert live[CQ].strength == 0.64


def test_recovery_failure_leaves_sibling_unrecovered():
    # Two-phase validation: CQ has its note, VBC has none. The raise
    # must not leave CQ's latch half-restored in memory.
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 7),
                           **_entry(VBC, 0.68, -10000))
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(
        path, {CQ: 0.72, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.64, 3, 0)}
    records = [_note_record(CQ, 0.72, 728, 735)]
    before = path.read_bytes()
    data_before = json.loads(json.dumps(tracker.data))
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=736, state_habits=live, records=records)
    assert tracker.data == data_before
    assert path.read_bytes() == before
    assert tracker.data["habits"][CQ]["demoted"] is False
    assert tracker.data["habits"][VBC]["demoted"] is False


def test_recovery_two_notes_raises():
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 0))
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    records = [_note_record(CQ, 0.72, 400, 400),
               _note_record(CQ, 0.72, 401, 401)]
    before = path.read_bytes()
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=402, state_habits=live, records=records)
    assert path.read_bytes() == before
    assert tracker.data["habits"][CQ]["demoted"] is False


def test_recovery_note_baseline_mismatch_raises():
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 0))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    # The note names a 0.70 baseline; the toml says 0.72.
    note = _note_record(CQ, 0.70, 400, 400)
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=400, state_habits=live, records=[note])
    assert tracker.data["habits"][CQ]["demoted"] is False


def test_recovery_note_future_tick_raises():
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 0))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    # A note demoted at tick 500 cannot be trusted at tick 400.
    note = _note_record(CQ, 0.72, 400, 500)
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=400, state_habits=live, records=[note])
    assert tracker.data["habits"][CQ]["demoted"] is False


def test_recovery_pending_owed_raises_and_touches_nothing():
    # Critic round 1, FAIL 1: the sidecar holds a pending owed mint
    # for tick 400 while the store holds a note for a different
    # episode (tick 399). Recovery must refuse — latching from the
    # 399 note while clearing owed would silently discard the tick-400
    # mint obligation; observe_tick would never re-emit it and nothing
    # would ever mint it. Preserving owed instead is refused: it would
    # mint a second demotion note for the same habit, contradicting
    # the one-episode ratchet.
    tmp = _tmp()
    owed_400 = {"habit_key": CQ, "dormancy_ticks": 400, "baseline": 0.72,
                "demoted_tick": 400}
    _seed_dormancy_sidecar(
        tmp, **_entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                      owed=owed_400))
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    records = [_note_record(CQ, 0.72, 399, 399)]
    before = path.read_bytes()
    data_before = json.loads(json.dumps(tracker.data))
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=401, state_habits=live, records=records)
    assert path.read_bytes() == before  # sidecar bytes untouched
    assert tracker.data == data_before  # no in-memory mutation either
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is False and entry["owed"] == owed_400
    assert live[CQ].strength == 0.64
    # The owed episode is still recoverable by the normal path:
    # observe_tick re-emits it so its note gets minted.
    events, _, _ = tracker.observe_tick(tick=401, state_habits=live)
    assert any(e["demoted_tick"] == 400 for e in events)


def test_recovery_distrusts_pasted_note_in_message_record():
    # Round 2 hardening 1: a canonical demotion note text pasted into
    # a message record is not a demotion — recovery trusts only
    # source temporal + generated_by cognition, the same boundary as
    # mint_demotion_note's idempotency check.
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 0))
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    pasted = SimpleNamespace(
        source="message", generated_by="authored",
        first_person=demotion_note_text(
            {"habit_key": CQ, "dormancy_ticks": 400, "baseline": 0.72,
             "demoted_tick": 400}))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=400, state_habits=live, records=[pasted])
    assert path.read_bytes() == before
    assert tracker.data["habits"][CQ]["demoted"] is False


def test_recovery_distrusts_temporal_note_by_other_author():
    # Same boundary, other half: a temporal record stamped by another
    # author (not cognition) is not an engine-minted demotion note.
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 0))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    forged = SimpleNamespace(
        source="temporal", generated_by="authored",
        first_person=demotion_note_text(
            {"habit_key": CQ, "dormancy_ticks": 400, "baseline": 0.72,
             "demoted_tick": 400}))
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=400, state_habits=live, records=[forged])
    assert tracker.data["habits"][CQ]["demoted"] is False


def test_recovery_note_within_dormancy_window_raises():
    # Round 2 hardening 2: the demotion edge requires tick -
    # last_fired > 300, so a note demoted 300 ticks (or fewer) after
    # the last firing contradicts the engine's own firing stamp.
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 400))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    # Exactly 300 ticks after the last firing: not more than 300.
    note = _note_record(CQ, 0.72, 300, 700)
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=700, state_habits=live, records=[note])
    assert tracker.data["habits"][CQ]["demoted"] is False
    # Well inside the window is no better.
    note = _note_record(CQ, 0.72, 100, 500)
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=700, state_habits=live, records=[note])
    assert tracker.data["habits"][CQ]["demoted"] is False


def test_recovery_note_just_over_dormancy_window_recovers():
    # Boundary: 301 ticks after the last firing is more than 300, so
    # the note is temporally consistent and the latch recovers.
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 400))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    note = _note_record(CQ, 0.72, 301, 701)
    assert tracker.recover_lost_latches(
        tick=701, state_habits=live, records=[note]) is True
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 701
    assert entry["owed"] is None


def test_recovery_noop_at_baseline_then_normal_demotion_still_fires():
    # Normal paths unchanged: at baseline strength with no note there
    # is nothing to recover, and the demotion edge still fires after
    # 300 dormant ticks with exactly one note.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    assert tracker.recover_lost_latches(
        tick=100, state_habits=live, records=[]) is False
    assert tracker.data["habits"] == {}  # no entry seeded
    events, sc, stc = tracker.observe_tick(tick=400, state_habits=live)
    assert len(events) == 1 and stc
    assert events[0]["demoted_tick"] == 400
    assert events[0]["habit_key"] == CQ


def test_recovery_already_demoted_is_noop():
    tmp = _tmp()
    _seed_dormancy_sidecar(
        tmp, **_entry(CQ, 0.72, 0, demoted=True, demoted_tick=400))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    note = _note_record(CQ, 0.72, 400, 400)
    assert tracker.recover_lost_latches(
        tick=401, state_habits=live, records=[note]) is False
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 400


def test_recovery_ignores_formed_habit_at_064():
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {
        CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0),
        "formed:hunger:seek_contact": Habit(
            "formed:hunger:seek_contact", "hunger", Action.SEEK_CONTACT,
            0.64, 2, 0),
    }
    assert tracker.recover_lost_latches(
        tick=400, state_habits=live, records=[]) is False
    assert "formed:hunger:seek_contact" not in tracker.data["habits"]
    assert tracker.data["habits"] == {}


def test_recovery_ignores_sub_threshold_baseline_at_064():
    # A 0.64 baseline never held the bypass privilege, so a 0.64 live
    # strength there is not a lost latch — and must not fail loud on a
    # healthy habit.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {"weak": 0.64})
    live = {"weak": Habit("weak", "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    assert tracker.recover_lost_latches(
        tick=400, state_habits=live, records=[]) is False
    assert tracker.data["habits"] == {}


def _replay_recovery(path: Path):
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.64, 3, 0)}
    records = [_note_record(CQ, 0.72, 728, 735),
               _note_record(VBC, 0.68, 10735, 735)]
    for tick in (736, 737, 738):
        tracker.recover_lost_latches(
            tick=tick, state_habits=live, records=records)
        events, _, _ = tracker.observe_tick(tick=tick, state_habits=live)
        tracker.reconcile_state_habits(live, tick=tick)
        tracker.ack_demotion_notes(events)
    tracker.save()


def test_recovery_determinism_byte_identical():
    tmp = _tmp()
    _replay_recovery(tmp / "a.json")
    _replay_recovery(tmp / "b.json")
    assert (tmp / "a.json").read_bytes() == (tmp / "b.json").read_bytes()


# -- recovery through the waking tick (cli._run_tick) --------------------------

def test_waking_tick_recovers_both_live_latches_without_mint():
    # The exact live shape, end to end: both habits at 0.64, sidecar
    # demoted: False, both notes in the store. One waking tick heals
    # both latches and mints nothing.
    tmp = _tmp()
    _seed_dormancy_sidecar(
        tmp,
        **_entry(CQ, 0.72, 7),
        **_entry(VBC, 0.68, -10000),
    )
    subject = _make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 735
        st.habits[CQ].strength = 0.64
        st.habits[VBC].strength = 0.64
        mint_demotion_note(subject, {"habit_key": CQ, "dormancy_ticks": 728,
                                     "baseline": 0.72, "demoted_tick": 735})
        mint_demotion_note(subject, {"habit_key": VBC, "dormancy_ticks": 10735,
                                     "baseline": 0.68, "demoted_tick": 735})
    assert len(_demotion_notes(subject)) == 2
    _quiet_tick(subject)  # tick 736: recovery heals both latches
    assert len(_demotion_notes(subject)) == 2  # no new record minted
    assert _live_strength(subject, CQ) == 0.64
    assert _live_strength(subject, VBC) == 0.64
    sidecar = json.loads((tmp / "authored-dormancy.json").read_text())
    for key in (CQ, VBC):
        entry = sidecar["habits"][key]
        assert entry["demoted"] is True and entry["demoted_tick"] == 735
        assert entry["owed"] is None
    # Second tick: idempotent, sidecar byte-identical.
    after_first = (tmp / "authored-dormancy.json").read_bytes()
    _quiet_tick(subject)
    assert (tmp / "authored-dormancy.json").read_bytes() == after_first
    assert len(_demotion_notes(subject)) == 2


def test_waking_tick_recovers_lost_latch_with_fresh_sidecar():
    tmp = _tmp()
    subject = _make_subject(tmp)  # no sidecar file at all
    assert not (tmp / "authored-dormancy.json").exists()
    with subject._transaction():
        st = subject.engine.state
        st.tick = 735
        st.habits[CQ].strength = 0.64
        # VBC's clock must be recent, or the fresh sidecar seeds it from
        # the never-fired stamp and the normal edge demotes it this tick.
        st.habits[VBC].last_used_tick = 735
        mint_demotion_note(subject, {"habit_key": CQ, "dormancy_ticks": 728,
                                     "baseline": 0.72, "demoted_tick": 735})
    _quiet_tick(subject)  # tick 736: the seeded entry recovers at once
    sidecar = json.loads((tmp / "authored-dormancy.json").read_text())
    entry = sidecar["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 735
    assert entry["owed"] is None
    assert len(_demotion_notes(subject)) == 1
    assert _live_strength(subject, CQ) == 0.64
    assert _live_strength(subject, VBC) == 0.68


def test_waking_tick_without_note_raises_before_tick_advances():
    tmp = _tmp()
    _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 7),
                           **_entry(VBC, 0.68, 399))
    subject = _make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 735
        st.habits[CQ].strength = 0.64
    sidecar_before = (tmp / "authored-dormancy.json").read_bytes()
    n_before = len(subject.workspace.records)
    with pytest.raises(ValueError):
        _quiet_tick(subject)
    # The tick never advanced, the sidecar is byte-identical, and no
    # record was appended — the fail-loud path fired before the
    # heartbeat, before any mint.
    assert subject.engine.state.tick == 735
    assert (tmp / "authored-dormancy.json").read_bytes() == sidecar_before
    assert len(subject.workspace.records) == n_before
    assert len(_demotion_notes(subject)) == 0


def test_waking_tick_pending_owed_raises_before_tick_advances():
    # Critic round 1, FAIL 1, end to end: a lost latch plus a pending
    # owed episode for a different tick must not silently drop the
    # owed episode's mint. The waking tick raises before the heartbeat,
    # before the tick advances, before any mint.
    tmp = _tmp()
    owed_400 = {"habit_key": CQ, "dormancy_ticks": 400, "baseline": 0.72,
                "demoted_tick": 400}
    _seed_dormancy_sidecar(
        tmp,
        **_entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                  owed=owed_400),
        **_entry(VBC, 0.68, 399),
    )
    subject = _make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 400
        st.habits[CQ].strength = 0.64
        st.habits[VBC].last_used_tick = 400
        mint_demotion_note(subject, {"habit_key": CQ, "dormancy_ticks": 399,
                                     "baseline": 0.72, "demoted_tick": 399})
    assert len(_demotion_notes(subject)) == 1
    sidecar_before = (tmp / "authored-dormancy.json").read_bytes()
    n_before = len(subject.workspace.records)
    with pytest.raises(ValueError):
        _quiet_tick(subject)
    assert subject.engine.state.tick == 400  # tick never advanced
    assert (tmp / "authored-dormancy.json").read_bytes() == sidecar_before
    assert len(subject.workspace.records) == n_before  # no mint
    assert len(_demotion_notes(subject)) == 1
    sidecar = json.loads(sidecar_before)
    assert sidecar["habits"][CQ]["owed"] == owed_400  # obligation kept
    assert sidecar["habits"][CQ]["demoted"] is False


def test_readonly_and_dream_leave_lost_latch_unrecovered():
    tmp = _tmp()
    restore = _patched_cli(tmp)
    try:
        _seed_dormancy_sidecar(tmp, **_entry(CQ, 0.72, 7))
        subject = cli._subject()
        with subject._transaction():
            st = subject.engine.state
            st.tick = 735
            st.habits[CQ].strength = 0.64
            mint_demotion_note(subject, {"habit_key": CQ,
                                         "dormancy_ticks": 728,
                                         "baseline": 0.72,
                                         "demoted_tick": 735})
        path = tmp / "authored-dormancy.json"
        before = path.read_bytes()
        assert cli.cmd_status(argparse.Namespace(raw=True)) == 0
        assert path.read_bytes() == before
        assert cli.cmd_drift(argparse.Namespace(window=50)) == 0
        assert path.read_bytes() == before
        subject.dream_tick()
        assert path.read_bytes() == before
        sidecar = json.loads(before)
        assert sidecar["habits"][CQ]["demoted"] is False
    finally:
        restore()


# -- fail-loud on pending owed overwrite (domain 29, 2026-11-22) -----------
# Spec: research/specs/2026-11-22-owed-overwrite.md. The demotion edge
# unconditionally overwrote entry["owed"]: a second demotion for what is,
# behaviorally, the same episode silently discarded the earlier
# episode's pending mint obligation. The fix: when the edge would fire
# with a pending owed mark, observe_tick raises ValueError before any
# mutation — the same fail-loud discipline recover_lost_latches
# already applies to the same state. All fixtures live in /tmp; the
# live store is never touched.

def _owed_400(key, baseline, span=400):
    return {"habit_key": key, "dormancy_ticks": span, "baseline": baseline,
            "demoted_tick": 400}


def test_edge_with_pending_owed_raises_and_touches_nothing():
    # Fitness 1: seeded entry demoted False + owed{400}, habit dormant
    # (401 ticks since the last firing, strength 0.72 >= 0.65) ->
    # observe_tick at tick 401 raises ValueError before the tick
    # advances; sidecar bytes and in-memory state byte-identical to
    # before the call; owed{400} intact; no mint event emitted.
    tmp = _tmp()
    owed = _owed_400(CQ, 0.72)
    _seed_dormancy_sidecar(
        tmp, **_entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                      owed=owed))
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    before = path.read_bytes()
    data_before = json.loads(json.dumps(tracker.data))
    with pytest.raises(ValueError) as excinfo:
        tracker.observe_tick(tick=401, state_habits=live)
    # The error names the habit, the pending episode's tick, and the
    # tick that would have overwritten it.
    msg = str(excinfo.value)
    assert CQ in msg and "400" in msg and "401" in msg
    assert path.read_bytes() == before  # sidecar bytes untouched
    assert tracker.data == data_before  # no in-memory mutation either
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is False and entry["owed"] == owed
    assert entry["last_fired_tick"] == 0
    assert live[CQ].strength == 0.72  # no projection, no reconcile
    # The raise is stable: a second identical call raises identically
    # and still touches nothing.
    with pytest.raises(ValueError) as again:
        tracker.observe_tick(tick=401, state_habits=live)
    assert str(again.value) == msg
    assert path.read_bytes() == before
    assert tracker.data == data_before


def test_edge_with_pending_owed_leaves_sibling_unmutated():
    # The raise is decided for every key before the loop mutates
    # anything: CQ sorts before VBC and would demote in this same tick
    # (owed None), but VBC's pending owed mark must abort the tick
    # before CQ's demotion lands in memory. An in-branch check would
    # leave CQ half-demoted when the raise fired.
    tmp = _tmp()
    _seed_dormancy_sidecar(
        tmp,
        **_entry(CQ, 0.72, 0),
        **_entry(VBC, 0.68, 0, demoted=False, demoted_tick=None,
                  owed=_owed_400(VBC, 0.68)),
    )
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.68, 3, 0)}
    before = path.read_bytes()
    data_before = json.loads(json.dumps(tracker.data))
    with pytest.raises(ValueError) as excinfo:
        tracker.observe_tick(tick=401, state_habits=live)
    msg = str(excinfo.value)
    assert VBC in msg and "400" in msg and "401" in msg
    assert path.read_bytes() == before
    assert tracker.data == data_before
    assert tracker.data["habits"][CQ]["demoted"] is False
    assert tracker.data["habits"][VBC]["owed"] == _owed_400(VBC, 0.68)
    assert live[CQ].strength == 0.72 and live[VBC].strength == 0.68


def test_pending_owed_with_proven_firing_does_not_raise():
    # The firing branch takes precedence over the edge: a proven
    # bypass firing this tick resets the clock, so the edge does not
    # fire and the pending owed episode re-emits untouched instead of
    # raising. The guard is edge-gated, not owed-gated.
    tmp = _tmp()
    _seed_dormancy_sidecar(
        tmp, **_entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                      owed=_owed_400(CQ, 0.72)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 401)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed_400(CQ, 0.72)]  # the owed episode re-emits
    assert tracker.data["habits"][CQ]["last_fired_tick"] == 401
    assert tracker.data["habits"][CQ]["demoted"] is False
    assert live[CQ].strength == 0.72


def test_pending_owed_reemits_without_raise_when_not_dormant():
    # Same guard, other side: only 51 dormant ticks at tick 401, so
    # the edge would not fire — the owed episode re-emits for its mint
    # and nothing raises. A pending owed mark alone is the normal
    # crash-recovery shape, not an inconsistency.
    tmp = _tmp()
    _seed_dormancy_sidecar(
        tmp, **_entry(CQ, 0.72, 350, demoted=False, demoted_tick=None,
                      owed=_owed_400(CQ, 0.72)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 350)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed_400(CQ, 0.72)]
    assert not stc  # no demotion, no projection
    assert tracker.data["habits"][CQ]["demoted"] is False
    assert tracker.data["habits"][CQ]["owed"] == _owed_400(CQ, 0.72)


def test_normal_edge_demotes_exactly_once_then_reemits_owed():
    # Fitness 2: owed None, dormant, strength >= 0.65 -> demotes
    # exactly once; a second observe_tick at the next tick (mint not
    # yet acked — the crash shape) emits the same owed episode again
    # and mints nothing twice.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=400, state_habits=live)
    assert len(events) == 1 and stc
    assert events[0] == {"habit_key": CQ, "dormancy_ticks": 400,
                         "baseline": 0.72, "demoted_tick": 400}
    tracker.reconcile_state_habits(live, tick=400)
    assert live[CQ].strength == 0.64
    tracker.save()
    # No ack: the mint never committed. The next tick re-emits the
    # same episode — same identity, no second demotion.
    events2, sc2, stc2 = tracker.observe_tick(tick=401, state_habits=live)
    assert events2 == events  # the same owed episode, untouched
    assert tracker.data["habits"][CQ]["demoted_tick"] == 400
    assert live[CQ].strength == 0.64
    # Minting the re-emitted episode is idempotent on the episode
    # identity: one note, ever, for this demotion.
    subject = _make_subject(tmp)
    with subject._transaction():
        first = mint_demotion_note(subject, events2[0])
        second = mint_demotion_note(subject, events2[0])
        assert first == second
    assert len(_demotion_notes(subject)) == 1


def test_already_demoted_owed_reemits_untouched_and_reprojects():
    # Fitness 3: demoted True + owed (crash between the sidecar save
    # and the reconcile commit) still re-emits the episode untouched
    # and still re-derives the strength projection.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=400, state_habits=live)
    assert len(events) == 1 and stc
    tracker.save()  # write-ahead: demoted True (+owed) durable...
    # ...KILL: reconcile never ran, mint never ran, ack never ran.
    assert live[CQ].strength == 0.72
    recovered = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    events, sc, stc = recovered.observe_tick(tick=401, state_habits=live)
    assert events == [{"habit_key": CQ, "dormancy_ticks": 400,
                       "baseline": 0.72, "demoted_tick": 400}]
    assert stc  # the projection re-derives
    recovered.reconcile_state_habits(live, tick=401)
    assert live[CQ].strength == 0.64
    assert recovered.data["habits"][CQ]["demoted"] is True
    assert recovered.data["habits"][CQ]["demoted_tick"] == 400


def test_ack_clears_only_on_matching_demoted_tick():
    # Fitness 4: ack_demotion_notes clears only when demoted_tick
    # matches the event; a mismatched ack — or one naming another
    # habit — leaves the mark.
    tmp = _tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, _, _ = tracker.observe_tick(tick=400, state_habits=live)
    assert len(events) == 1
    owed = dict(events[0])
    tracker.ack_demotion_notes(
        [{"habit_key": CQ, "demoted_tick": 401}])  # wrong tick
    assert tracker.data["habits"][CQ]["owed"] == owed
    tracker.ack_demotion_notes(
        [{"habit_key": VBC, "demoted_tick": 400}])  # wrong habit
    assert tracker.data["habits"][CQ]["owed"] == owed
    tracker.ack_demotion_notes(events)  # the matching ack clears it
    assert tracker.data["habits"][CQ]["owed"] is None
    assert tracker.data["habits"][CQ]["demoted"] is True


def test_readonly_and_dream_leave_pending_owed_sidecar_byte_identical():
    # Fitness 5, read-only half: a sidecar holding a pending owed
    # mark is byte-identical after every read-only command and after
    # a dream tick — none of them may demote, mint, or write.
    tmp = _tmp()
    restore = _patched_cli(tmp)
    try:
        _seed_dormancy_sidecar(
            tmp, **_entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                          owed=_owed_400(CQ, 0.72)))
        subject = cli._subject()
        path = tmp / "authored-dormancy.json"
        before = path.read_bytes()
        assert cli.cmd_status(argparse.Namespace(raw=True)) == 0
        assert path.read_bytes() == before
        assert cli.cmd_drift(argparse.Namespace(window=50)) == 0
        assert path.read_bytes() == before
        assert cli.cmd_review(argparse.Namespace(n=5)) == 0
        assert path.read_bytes() == before
        assert cli.cmd_relationships(argparse.Namespace()) == 0
        assert path.read_bytes() == before
        subject.dream_tick()
        assert path.read_bytes() == before
        # The dream tick records dream thoughts of its own; what must
        # not appear is a demotion note.
        assert len(_demotion_notes(subject)) == 0  # no mint
        sidecar = json.loads(before)
        assert sidecar["habits"][CQ]["owed"] == _owed_400(CQ, 0.72)
        assert sidecar["habits"][CQ]["demoted"] is False
    finally:
        restore()


def test_init_force_wipes_sidecar_holding_pending_owed():
    # Fitness 5, reseed half: init --force wipes the sidecar even
    # when it holds a pending owed mark — a reseed starts with no
    # dormancy history at all.
    tmp = _tmp()
    restore = _patched_cli(tmp)
    try:
        dorm = tmp / "authored-dormancy.json"
        dorm.write_text(json.dumps({"habits": {
            CQ: {"baseline": 0.72, "last_fired_tick": 0, "demoted": False,
                 "demoted_tick": None,
                 "owed": _owed_400(CQ, 0.72)}}}), encoding="utf-8")
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        assert not dorm.exists()
        fresh = AuthoredDormancyTracker(dorm, {CQ: 0.72})
        assert fresh.data == {"habits": {}}
    finally:
        restore()


def test_waking_tick_pending_owed_raises_before_mint():
    # Fitness 1, end to end: the live shape — CQ dormant with a
    # pending owed{400} at strength 0.72 — must fail loud on the
    # waking tick. The heartbeat has already run (it precedes the
    # dormancy block in _run_tick, as for every tracker), so the
    # engine tick has advanced to 401; what the raise precedes is
    # any dormancy mutation, any mint, and any sidecar write.
    tmp = _tmp()
    _seed_dormancy_sidecar(
        tmp,
        **_entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                  owed=_owed_400(CQ, 0.72)),
        **_entry(VBC, 0.68, 400),
    )
    subject = _make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 400
        st.habits[CQ].last_used_tick = 0
        st.habits[VBC].last_used_tick = 400
    sidecar_before = (tmp / "authored-dormancy.json").read_bytes()
    with pytest.raises(ValueError) as excinfo:
        _quiet_tick(subject)
    msg = str(excinfo.value)
    assert CQ in msg and "400" in msg and "401" in msg
    assert subject.engine.state.tick == 401  # heartbeat already ran
    assert (tmp / "authored-dormancy.json").read_bytes() == sidecar_before
    assert len(_demotion_notes(subject)) == 0  # no mint
    assert _live_strength(subject, CQ) == 0.72  # no projection
    sidecar = json.loads(sidecar_before)
    assert sidecar["habits"][CQ]["owed"] == _owed_400(CQ, 0.72)
    assert sidecar["habits"][CQ]["demoted"] is False
    assert sidecar["habits"][VBC]["demoted"] is False  # sibling untouched


def test_fail_loud_path_is_deterministic():
    # Fitness 6: identical sequences -> byte-identical sidecars,
    # including the fail-loud path — two runs raise identically,
    # bytes untouched.
    tmp = _tmp()
    for name in ("a", "b"):
        path = tmp / f"{name}.json"
        path.write_text(json.dumps({"habits": {
            CQ: {"baseline": 0.72, "last_fired_tick": 0, "demoted": False,
                 "demoted_tick": None, "owed": _owed_400(CQ, 0.72)},
            VBC: {"baseline": 0.68, "last_fired_tick": 400,
                  "demoted": False, "demoted_tick": None,
                  "owed": None}}}), encoding="utf-8")
    messages = []
    for name in ("a", "b"):
        path = tmp / f"{name}.json"
        tracker = AuthoredDormancyTracker(path, {CQ: 0.72, VBC: 0.68})
        # Normalize the seed to the tracker's own serialization first:
        # save() re-writes with indent=1, so the raw json.dumps seed
        # bytes are not the byte baseline.
        tracker.save()
        before = path.read_bytes()
        live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0),
                VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.68,
                           3, 400)}
        with pytest.raises(ValueError) as excinfo:
            tracker.observe_tick(tick=401, state_habits=live)
        messages.append(str(excinfo.value))
        assert path.read_bytes() == before
        assert tracker.data["habits"][CQ]["owed"] == _owed_400(CQ, 0.72)
        tracker.save()  # no mutation happened, so the save is a no-op
        assert path.read_bytes() == before
    assert messages[0] == messages[1]  # identical raise, twice
    assert (tmp / "a.json").read_bytes() == (tmp / "b.json").read_bytes()
