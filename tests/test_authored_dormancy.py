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
    """Point every module-level path cmd_init/cmd_status touch at tmp."""
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    dreams = tmp / "dreams"
    saved = (cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE, cli.INTEROCEPTION,
             cli.FAMILIARITY, cli.AMBIVALENCE, cli.COUNTERFACTUAL, cli.HABITS,
             cli.DORMANCY, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE,
             cli._subject)
    (cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE, cli.INTEROCEPTION,
     cli.FAMILIARITY, cli.AMBIVALENCE, cli.COUNTERFACTUAL, cli.HABITS,
     cli.DORMANCY, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE) = (
        db, inbox, dreams, tmp / "salience.json", tmp / "interoception.json",
        tmp / "familiarity.json", tmp / "ambivalence.json",
        tmp / "counterfactuals.json", tmp / "habits-formed.json",
        tmp / "authored-dormancy.json", tmp / "provenance.json",
        tmp / "proposals", tmp / "archive")
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
        (cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE, cli.INTEROCEPTION,
         cli.FAMILIARITY, cli.AMBIVALENCE, cli.COUNTERFACTUAL, cli.HABITS,
         cli.DORMANCY, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE,
         cli._subject) = saved
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
