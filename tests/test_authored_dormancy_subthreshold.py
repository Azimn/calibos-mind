"""Builder tests: owed re-emit under a sub-threshold baseline (domain 30).

Spec: research/specs/2026-11-23-owed-reemit-subthreshold.md. Follow-up to
the 2026-11-22 owed-overwrite loop (critic observation 2): the old
`observe_tick` loop `continue`d on baseline < 0.65, skipping the whole
iteration INCLUDING the owed re-emit, so a pending owed mark on a
sub-threshold entry stalled silently forever — and the fail-loud
pre-pass, which mirrors the demotion edge, never raised either.

The fix: the sub-threshold skip covers only the demotion edge, the
firing branch, and the clock reset. The owed re-emit and the
crash-recovery re-derivation run for every baseline. No new demotion
may fire below threshold, and a sub-threshold habit with no sidecar
entry stays untracked. All fixtures live in /tmp — the live store is
never touched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_authored_dormancy as T

import calibos_mind.cli as cli
from calibos_mind.authored_dormancy import (
    AuthoredDormancyTracker,
    demotion_note_text,
    mint_demotion_note,
    parse_demotion_note_text,
)
from digital_subject.models import Action, Habit

CQ = T.CQ
VBC = T.VBC


def _owed(key, baseline, tick=400, span=350):
    return {"habit_key": key, "dormancy_ticks": span, "baseline": baseline,
            "demoted_tick": tick}


def _subthreshold_seed(tmp: Path, *, live_strength=0.64, owed=True,
                       demoted=True, last_fired=50, baseline=0.64):
    """The spec's fitness seed: baseline 0.64, demoted, owed{400}.

    Sidecar entry: baseline 0.64, last_fired_tick 50, demoted True,
    demoted_tick 400, owed {habit_key CQ, dormancy_ticks 350, baseline
    0.64, demoted_tick 400}. Live strength 0.64 unless overridden.
    Returns (tracker, live)."""
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, baseline, last_fired, demoted=demoted,
                        demoted_tick=400 if demoted else None,
                        owed=_owed(CQ, baseline) if owed else None))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: baseline})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, live_strength, 2,
                      last_fired)}
    return tracker, live


# -- fitness 1: sub-threshold owed re-emits until acked ------------------------

def test_subthreshold_owed_reemits_until_ack():
    tmp = T._tmp()
    tracker, live = _subthreshold_seed(tmp)
    owed = _owed(CQ, 0.64)
    # Tick 401: exactly the owed event, once — no duplicates in one call.
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [owed]
    assert sc is False and stc is False  # nothing else changed
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 400
    assert entry["owed"] == owed  # the mark stands until acked
    assert entry["last_fired_tick"] == 50  # clock untouched
    assert live[CQ].strength == 0.64
    # Tick 402: re-emits again — the obligation stands until acked.
    events, sc, stc = tracker.observe_tick(tick=402, state_habits=live)
    assert events == [owed]
    assert sc is False and stc is False
    # The matching ack clears it; the next tick emits nothing.
    tracker.ack_demotion_notes(events)
    assert tracker.data["habits"][CQ]["owed"] is None
    tracker.save()
    fresh = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64})
    assert fresh.data["habits"][CQ]["owed"] is None
    events, sc, stc = fresh.observe_tick(tick=403, state_habits=live)
    assert events == []
    assert sc is False and stc is False
    assert fresh.data["habits"][CQ]["demoted"] is True
    assert fresh.data["habits"][CQ]["demoted_tick"] == 400


def test_subthreshold_reemitted_owed_mints_exactly_one_note():
    # The re-emitted event is the same episode identity the idempotent
    # mint already knows: one note, ever, for this demotion.
    tmp = T._tmp()
    tracker, live = _subthreshold_seed(tmp)
    events, _, _ = tracker.observe_tick(tick=401, state_habits=live)
    assert len(events) == 1
    subject = T._make_subject(tmp)
    with subject._transaction():
        first = mint_demotion_note(subject, events[0])
        second = mint_demotion_note(subject, events[0])
        assert first == second
    assert len(T._demotion_notes(subject)) == 1
    parsed = parse_demotion_note_text(
        T._demotion_notes(subject)[0].first_person)
    assert parsed["habit_key"] == CQ and parsed["demoted_tick"] == 400
    assert parsed["baseline"] == pytest.approx(0.64)
    assert parsed["dormancy_ticks"] == 350
    assert demotion_note_text(parsed) == \
        T._demotion_notes(subject)[0].first_person


def test_subthreshold_owed_reemits_with_no_live_entry():
    # No live entry: nothing to prove or project, but the owed episode
    # still needs its mint — no raise, one event, mark intact.
    tmp = T._tmp()
    tracker, _live = _subthreshold_seed(tmp)
    events, sc, stc = tracker.observe_tick(tick=401, state_habits={})
    assert events == [_owed(CQ, 0.64)]
    assert sc is False and stc is False
    assert tracker.data["habits"][CQ]["demoted"] is True
    assert tracker.data["habits"][CQ]["owed"] == _owed(CQ, 0.64)


def test_subthreshold_owed_reemits_when_not_demoted_and_does_not_raise():
    # The hand-edit shape (demoted False + owed, baseline 0.60): the
    # edge cannot fire below threshold, so the pre-pass stays silent —
    # and the owed episode re-emits instead of stalling. This is the
    # superseded 2026-11-22 critic pin
    # (test_subthreshold_baseline_with_owed_does_not_raise), now
    # asserting the fixed behavior.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.60, 0, demoted=False, demoted_tick=None,
                        owed=_owed(CQ, 0.60, span=400)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.60})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed(CQ, 0.60, span=400)]
    assert tracker.data["habits"][CQ]["demoted"] is False
    assert tracker.data["habits"][CQ]["demoted_tick"] is None
    assert tracker.data["habits"][CQ]["owed"] == _owed(CQ, 0.60, span=400)
    assert not stc  # no projection: nothing demoted
    assert live[CQ].strength == 0.72  # untouched


# -- fitness 2: crash recovery re-derives under a sub-threshold baseline -------

def test_subthreshold_crash_recovery_reprojects_and_reemits():
    # Same seed with live strength 0.65 (kill between the sidecar save
    # and the reconcile commit): state_changed=True so the caller
    # re-derives the projection to 0.64, and the owed event still
    # re-emits.
    tmp = T._tmp()
    tracker, live = _subthreshold_seed(tmp, live_strength=0.65)
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed(CQ, 0.64)]
    assert stc  # THE FIX: the projection must re-derive
    tracker.reconcile_state_habits(live, tick=401)
    assert live[CQ].strength == 0.64  # store converges; note truthful
    assert tracker.data["habits"][CQ]["demoted"] is True
    assert tracker.data["habits"][CQ]["demoted_tick"] == 400
    # A second tick with the converged strength reports no change and
    # still re-emits until acked.
    events, sc, stc = tracker.observe_tick(tick=402, state_habits=live)
    assert events == [_owed(CQ, 0.64)]
    assert not stc


# -- fitness 3: no owed mark, no event, no change ------------------------------

def test_subthreshold_no_owed_no_event_no_change():
    tmp = T._tmp()
    tracker, live = _subthreshold_seed(tmp, owed=False)
    before = json.loads(json.dumps(tracker.data))
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == []
    assert sc is False and stc is False  # the exact flags, no hedging
    assert tracker.data == before  # in-memory state byte-identical
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 400
    assert entry["owed"] is None and entry["last_fired_tick"] == 50
    assert live[CQ].strength == 0.64  # never demoted, never projected
    # The sidecar is byte-identical too: save() after a no-change tick
    # rewrites the same bytes.
    tracker.save()
    first = (tmp / "authored-dormancy.json").read_bytes()
    events, sc, stc = tracker.observe_tick(tick=402, state_habits=live)
    assert events == [] and not sc and not stc
    tracker.save()
    assert (tmp / "authored-dormancy.json").read_bytes() == first


# -- fitness 4: the 0.65 / 0.649 boundary ----------------------------------------

def test_boundary_baseline_exactly_065_edge_still_fires():
    # Baseline exactly 0.65 behaves exactly as before: the edge can
    # fire when dormant.
    tmp = T._tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.65})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.65, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert len(events) == 1 and stc
    assert events[0] == {"habit_key": CQ, "dormancy_ticks": 401,
                         "baseline": 0.65, "demoted_tick": 401}
    tracker.reconcile_state_habits(live, tick=401)
    assert live[CQ].strength == 0.64
    assert tracker.data["habits"][CQ]["demoted"] is True


def test_boundary_baseline_0649_reemits_owed_but_never_demotes():
    # Baseline 0.649: a pending owed mark re-emits, but no new
    # demotion ever fires — even dormant for far more than 300 ticks,
    # even with a live strength above the threshold (the hand-edit
    # shape: the gate is the BASELINE, not the live strength).
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.649, 0, demoted=False, demoted_tick=None,
                        owed=_owed(CQ, 0.649, span=400)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.649})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=10_000, state_habits=live)
    assert events == [_owed(CQ, 0.649, span=400)]
    assert not stc
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is False and entry["demoted_tick"] is None
    assert entry["owed"] == _owed(CQ, 0.649, span=400)
    assert live[CQ].strength == 0.72  # no projection, no demotion


def test_boundary_baseline_0649_dormant_never_demotes_without_owed():
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.649, 0, demoted=False, demoted_tick=None,
                        owed=None))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.649})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=10_000, state_habits=live)
    assert events == [] and not sc and not stc
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is False and entry["demoted_tick"] is None
    assert entry["owed"] is None  # no new owed minted
    assert entry["last_fired_tick"] == 0  # clock never reset
    assert live[CQ].strength == 0.72


def test_subthreshold_firing_stamp_resets_nothing():
    # The firing/clock-reset logic is skipped below threshold even
    # when the engine's stamp proves a firing this tick (reachable
    # only with a hand-set live strength >= 0.65): the clock stands,
    # no projection is reported, and the owed episode still re-emits.
    tmp = T._tmp()
    tracker, live = _subthreshold_seed(tmp)
    live[CQ].last_used_tick = 401  # proven firing this tick
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed(CQ, 0.64)]
    assert not stc
    assert tracker.data["habits"][CQ]["last_fired_tick"] == 50


def test_subthreshold_fresh_habit_stays_untracked():
    # A sub-threshold habit with no sidecar entry seeds nothing: no
    # event, no flags, no entry. (The design decision the fix keeps:
    # only an EXISTING entry gets the re-emit/re-derivation treatment.)
    tmp = T._tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {"weak": 0.50})
    live = {"weak": Habit("weak", "curiosity", Action.EXPLORE, 0.50, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=10_000, state_habits=live)
    assert events == [] and not sc and not stc
    assert tracker.data["habits"] == {}
    assert tracker.authored == {"weak": 0.50}


def test_subthreshold_baseline_resync_then_reemit():
    # The cartridge-change path: the sidecar entry still names the old
    # 0.68 baseline while the authored map says 0.64. The re-sync runs
    # (it is not demotion-edge/firing/clock logic), the owed episode
    # re-emits under the new baseline, and no new demotion fires.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.68, 50, demoted=True, demoted_tick=400,
                        owed=_owed(CQ, 0.68)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 50)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed(CQ, 0.68)]  # the original episode, untouched
    assert sc  # the baseline re-sync changed the sidecar
    assert not stc  # live strength already 0.64: no re-derivation
    entry = tracker.data["habits"][CQ]
    assert entry["baseline"] == 0.64
    assert entry["demoted"] is True and entry["demoted_tick"] == 400


# -- fitness 5: at-threshold regression, byte-identical to pre-change ---------

# Goldens captured pre-change (2026-11-23) by replaying the existing
# owed-overwrite and latch-recovery sequences on the pristine worktree
# (/tmp/golden_capture/test_golden_capture.py, now deleted). These are
# byte literals, not "tests pass": any drift in the at-threshold path
# fails here even if the behavioral tests still pass.
_GOLDEN_REPLAY = (
    b'{\n "habits": {\n  "curious_question": {\n   "baseline": 0.72,\n'
    b'   "last_fired_tick": 0,\n   "demoted": true,\n   "demoted_tick": 350,\n'
    b'   "owed": null\n  },\n  "verify_before_claiming": {\n'
    b'   "baseline": 0.68,\n   "last_fired_tick": 50,\n   "demoted": true,\n'
    b'   "demoted_tick": 401,\n   "owed": null\n  }\n }\n}')
_GOLDEN_RECOVERY = (
    b'{\n "habits": {\n  "curious_question": {\n   "baseline": 0.72,\n'
    b'   "last_fired_tick": 0,\n   "demoted": true,\n   "demoted_tick": 735,\n'
    b'   "owed": null\n  },\n  "verify_before_claiming": {\n'
    b'   "baseline": 0.68,\n   "last_fired_tick": 0,\n   "demoted": true,\n'
    b'   "demoted_tick": 735,\n   "owed": null\n  }\n }\n}')
_GOLDEN_OWED_EVENTS_400 = [
    {"habit_key": "curious_question", "dormancy_ticks": 400,
     "baseline": 0.72, "demoted_tick": 400}]
_GOLDEN_OWED_SIDECAR = (
    b'{\n "habits": {\n  "curious_question": {\n   "baseline": 0.72,\n'
    b'   "last_fired_tick": 0,\n   "demoted": true,\n   "demoted_tick": 400,\n'
    b'   "owed": null\n  }\n }\n}')
_GOLDEN_CRASH_EVENTS_401 = [
    {"habit_key": "curious_question", "dormancy_ticks": 400,
     "baseline": 0.72, "demoted_tick": 400}]


def test_at_threshold_replay_byte_identical_to_prechange():
    # The dormancy demotion replay (ticks 100..700) and the
    # latch-recovery replay (ticks 736..738), byte for byte.
    tmp = T._tmp()
    T._replay(tmp / "replay.json")
    assert (tmp / "replay.json").read_bytes() == _GOLDEN_REPLAY
    T._replay_recovery(tmp / "recovery.json")
    assert (tmp / "recovery.json").read_bytes() == _GOLDEN_RECOVERY


def test_at_threshold_owed_sequence_byte_identical_to_prechange():
    # The owed-overwrite sequence: demote at 400, save, re-emit at 401
    # unacked, ack, save. Same events, same final sidecar bytes.
    tmp = T._tmp()
    tracker = AuthoredDormancyTracker(tmp / "owed.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, _, _ = tracker.observe_tick(tick=400, state_habits=live)
    assert events == _GOLDEN_OWED_EVENTS_400
    tracker.reconcile_state_habits(live, tick=400)
    tracker.save()
    events2, _, _ = tracker.observe_tick(tick=401, state_habits=live)
    assert events2 == _GOLDEN_OWED_EVENTS_400  # same episode, untouched
    tracker.ack_demotion_notes(events2)
    tracker.save()
    assert (tmp / "owed.json").read_bytes() == _GOLDEN_OWED_SIDECAR


def test_at_threshold_crash_sequence_byte_identical_to_prechange():
    # The crash-recovery sequence: demote at 400, save (kill), fresh
    # tracker observes at 401, reconciles, acks, saves.
    tmp = T._tmp()
    tracker = AuthoredDormancyTracker(tmp / "crash.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, _, _ = tracker.observe_tick(tick=400, state_habits=live)
    tracker.save()
    recovered = AuthoredDormancyTracker(tmp / "crash.json", {CQ: 0.72})
    events, _, stc = recovered.observe_tick(tick=401, state_habits=live)
    assert events == _GOLDEN_CRASH_EVENTS_401
    assert stc is True
    recovered.reconcile_state_habits(live, tick=401)
    assert live[CQ].strength == 0.64
    recovered.ack_demotion_notes(events)
    recovered.save()
    assert (tmp / "crash.json").read_bytes() == _GOLDEN_OWED_SIDECAR


# -- fitness 6: read-only / dream isolation, init --force wipe -----------------

def test_readonly_and_dream_leave_subthreshold_pending_owed_byte_identical():
    # A sidecar holding a pending owed mark on a sub-threshold entry
    # is byte-identical after every read-only command and after a dream
    # tick — none of them may demote, mint, or write.
    tmp = T._tmp()
    restore = T._patched_cli(tmp)
    try:
        T._seed_dormancy_sidecar(
            tmp, **T._entry(CQ, 0.64, 50, demoted=True, demoted_tick=400,
                            owed=_owed(CQ, 0.64)))
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
        assert len(T._demotion_notes(subject)) == 0  # no mint
        sidecar = json.loads(before)
        assert sidecar["habits"][CQ]["owed"] == _owed(CQ, 0.64)
        assert sidecar["habits"][CQ]["demoted"] is True
    finally:
        restore()


def test_init_force_wipes_subthreshold_pending_owed_sidecar():
    # init --force wipes the sidecar even when it holds a pending owed
    # mark on a sub-threshold entry — a reseed starts with no dormancy
    # history at all.
    tmp = T._tmp()
    restore = T._patched_cli(tmp)
    try:
        dorm = tmp / "authored-dormancy.json"
        dorm.write_text(json.dumps({"habits": {
            CQ: {"baseline": 0.64, "last_fired_tick": 50, "demoted": True,
                 "demoted_tick": 400,
                 "owed": _owed(CQ, 0.64)}}}), encoding="utf-8")
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        assert not dorm.exists()
        fresh = AuthoredDormancyTracker(dorm, {CQ: 0.64})
        assert fresh.data == {"habits": {}}
    finally:
        restore()


# -- fitness 7: determinism, including the re-emit path ------------------------

def _replay_subthreshold(path: Path):
    """Mixed replay: a sub-threshold owed entry plus an at-threshold
    sibling that demotes mid-sequence. Returns the event lists.

    Seeds <dir>/authored-dormancy.json (the helper's fixed filename),
    replays on it, then moves the final bytes to `path` so two runs
    can be compared file to file."""
    T._seed_dormancy_sidecar(
        path.parent,
        **T._entry(CQ, 0.64, 50, demoted=True, demoted_tick=400,
                   owed=_owed(CQ, 0.64)),
        **T._entry(VBC, 0.68, 50),
    )
    # _seed_dormancy_sidecar writes to <dir>/authored-dormancy.json;
    # the replay file is that same path — re-point it.
    tracker = AuthoredDormancyTracker(
        path.parent / "authored-dormancy.json", {CQ: 0.64, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 50),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.68, 3, 50)}
    all_events = []
    for tick in (401, 402, 403):
        events, _, stc = tracker.observe_tick(tick=tick, state_habits=live)
        tracker.reconcile_state_habits(live, tick=tick)
        if tick == 402:
            tracker.ack_demotion_notes(events)
        all_events.append(list(events))
    tracker.save()
    # Move the final sidecar bytes to the replay file for comparison.
    (path.parent / "authored-dormancy.json").replace(path)
    return all_events


def test_subthreshold_reemit_replay_deterministic():
    tmp = T._tmp()
    a_dir = tmp / "a"
    b_dir = tmp / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    events_a = _replay_subthreshold(a_dir / "replay.json")
    events_b = _replay_subthreshold(b_dir / "replay.json")
    assert events_a == events_b
    # Tick 401: CQ re-emits its owed episode; VBC demotes (351 dormant).
    assert events_a[0] == [
        _owed(CQ, 0.64),
        {"habit_key": VBC, "dormancy_ticks": 351, "baseline": 0.68,
         "demoted_tick": 401},
    ]
    # Tick 402: both owed episodes re-emit (neither was acked yet).
    assert events_a[1] == [
        _owed(CQ, 0.64),
        {"habit_key": VBC, "dormancy_ticks": 351, "baseline": 0.68,
         "demoted_tick": 401},
    ]
    # Tick 403: both owed marks were acked at 402, so nothing emits.
    assert events_a[2] == []
    assert (a_dir / "replay.json").read_bytes() == \
        (b_dir / "replay.json").read_bytes()


def test_fail_loud_determinism_with_subthreshold_sibling():
    # The fail-loud path is deterministic with a sub-threshold sibling
    # present: the at-threshold entry raises identically on both runs,
    # the sub-threshold entry's owed mark is untouched by the abort,
    # and the sidecar bytes never move.
    tmp = T._tmp()
    messages = []
    for name in ("a", "b"):
        path = tmp / f"{name}.json"
        path.write_text(json.dumps({"habits": {
            CQ: {"baseline": 0.72, "last_fired_tick": 0, "demoted": False,
                 "demoted_tick": None,
                 "owed": {"habit_key": CQ, "dormancy_ticks": 400,
                          "baseline": 0.72, "demoted_tick": 400}},
            VBC: {"baseline": 0.64, "last_fired_tick": 50,
                  "demoted": True, "demoted_tick": 400,
                  "owed": _owed(VBC, 0.64)}}}), encoding="utf-8")
        tracker = AuthoredDormancyTracker(path, {CQ: 0.72, VBC: 0.64})
        tracker.save()  # normalize the seed to the tracker's own bytes
        before = path.read_bytes()
        live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0),
                VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.64, 3, 50)}
        with pytest.raises(ValueError) as excinfo:
            tracker.observe_tick(tick=401, state_habits=live)
        messages.append(str(excinfo.value))
        assert path.read_bytes() == before
        assert tracker.data["habits"][CQ]["owed"]["demoted_tick"] == 400
        assert tracker.data["habits"][VBC]["owed"] == _owed(VBC, 0.64)
        tracker.save()  # no mutation happened, so the save is a no-op
        assert path.read_bytes() == before
    assert messages[0] == messages[1]  # identical raise, twice
    assert CQ in messages[0] and "400" in messages[0]
    assert (tmp / "a.json").read_bytes() == (tmp / "b.json").read_bytes()
