"""Critic attack tests for the domain-30 sub-threshold owed re-emit.

Cold attacks only: each test targets a concrete invariant from
research/specs/2026-11-23-owed-reemit-subthreshold.md that the builder's
own tests do not cover. Synthetic /tmp stores only; the live store is
never touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_authored_dormancy as T

from calibos_mind.authored_dormancy import AuthoredDormancyTracker
from digital_subject.models import Action, Habit

CQ = T.CQ
VBC = T.VBC


def _owed(key, baseline, tick=400, span=350):
    return {"habit_key": key, "dormancy_ticks": span, "baseline": baseline,
            "demoted_tick": tick}


def test_two_subthreshold_owed_entries_emit_exactly_once_each():
    # Two pending owed marks in one call: exactly two events, one per
    # entry, no duplicates, sorted-key order. The revert signal forbids
    # an owed episode being emitted twice for one mint in one call.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp,
        **T._entry(CQ, 0.64, 50, demoted=True, demoted_tick=400,
                   owed=_owed(CQ, 0.64)),
        **T._entry(VBC, 0.60, 50, demoted=True, demoted_tick=400,
                   owed=_owed(VBC, 0.60)),
    )
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64, VBC: 0.60})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 50),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.60, 3, 50)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed(CQ, 0.64), _owed(VBC, 0.60)]
    assert len(events) == len({json.dumps(e, sort_keys=True)
                               for e in events})  # no duplicates
    # A second tick re-emits the same two, still exactly once each.
    events, _, _ = tracker.observe_tick(tick=402, state_habits=live)
    assert events == [_owed(CQ, 0.64), _owed(VBC, 0.60)]


def test_ack_clears_only_on_matching_demoted_tick():
    # ack_demotion_notes clears only on a matching demoted_tick: an
    # ack carrying a wrong tick leaves the owed mark standing and the
    # next tick re-emits; the matching ack clears it.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.64, 50, demoted=True, demoted_tick=400,
                        owed=_owed(CQ, 0.64)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 50)}
    events, _, _ = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed(CQ, 0.64)]
    wrong = [dict(events[0], demoted_tick=399)]
    tracker.ack_demotion_notes(wrong)
    assert tracker.data["habits"][CQ]["owed"] == _owed(CQ, 0.64)
    events, _, _ = tracker.observe_tick(tick=402, state_habits=live)
    assert events == [_owed(CQ, 0.64)]  # still owed
    tracker.ack_demotion_notes(events)
    assert tracker.data["habits"][CQ]["owed"] is None
    events, _, _ = tracker.observe_tick(tick=403, state_habits=live)
    assert events == []


def test_ack_of_one_entry_leaves_sibling_owed_intact():
    # Acking one entry's event must not clear a sibling's owed mark.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp,
        **T._entry(CQ, 0.64, 50, demoted=True, demoted_tick=400,
                   owed=_owed(CQ, 0.64)),
        **T._entry(VBC, 0.60, 50, demoted=True, demoted_tick=400,
                   owed=_owed(VBC, 0.60)),
    )
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64, VBC: 0.60})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 50),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.60, 3, 50)}
    events, _, _ = tracker.observe_tick(tick=401, state_habits=live)
    assert len(events) == 2
    tracker.ack_demotion_notes([events[0]])  # CQ only
    assert tracker.data["habits"][CQ]["owed"] is None
    assert tracker.data["habits"][VBC]["owed"] == _owed(VBC, 0.60)
    events, _, _ = tracker.observe_tick(tick=402, state_habits=live)
    assert events == [_owed(VBC, 0.60)]


def test_reemitted_event_is_a_copy_not_an_alias():
    # The re-emitted event must be a copy: mutating the returned dict
    # must not corrupt the sidecar's owed mark (the next tick must
    # re-emit the original episode, not the caller's edit).
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.64, 50, demoted=True, demoted_tick=400,
                        owed=_owed(CQ, 0.64)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 50)}
    events, _, _ = tracker.observe_tick(tick=401, state_habits=live)
    events[0]["dormancy_ticks"] = 999
    events[0]["baseline"] = 0.01
    assert tracker.data["habits"][CQ]["owed"] == _owed(CQ, 0.64)
    events, _, _ = tracker.observe_tick(tick=402, state_habits=live)
    assert events == [_owed(CQ, 0.64)]


def test_subthreshold_demoted_no_owed_handset_strength_reprojects():
    # Demoted, no owed mark, live strength hand-set to 0.72 (not a
    # crash — the sidecar already says demoted): observe_tick reports
    # state_changed so reconcile converges the store to 0.64, emits no
    # event, mints no new owed mark, and leaves the episode identity
    # untouched. This is the crash-recovery branch's shape, not a new
    # demotion.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.64, 50, demoted=True, demoted_tick=400,
                        owed=None))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 50)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == []
    assert stc is True
    assert tracker.data["habits"][CQ]["demoted_tick"] == 400
    assert tracker.data["habits"][CQ]["owed"] is None
    tracker.reconcile_state_habits(live, tick=401)
    assert live[CQ].strength == 0.64
    events, sc, stc = tracker.observe_tick(tick=402, state_habits=live)
    assert events == [] and stc is False


def test_subthreshold_no_owed_with_none_state_habits():
    # state_habits=None is normalized to {}: a no-owed sub-threshold
    # entry still emits nothing and changes nothing.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.64, 50, demoted=True, demoted_tick=400,
                        owed=None))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64})
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=None)
    assert events == [] and sc is False and stc is False
    # And an owed mark still re-emits with state_habits=None.
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.64, 50, demoted=True, demoted_tick=400,
                        owed=_owed(CQ, 0.64)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64})
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=None)
    assert events == [_owed(CQ, 0.64)]


def test_prepass_raise_leaves_subthreshold_resync_unlanded():
    # The fail-loud pre-pass runs before the loop mutates anything: a
    # sub-threshold entry whose baseline re-sync is pending (sidecar
    # 0.68 vs authored 0.64) must NOT be re-synced in memory when an
    # at-threshold sibling's pending owed mark aborts the tick.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp,
        **T._entry(CQ, 0.68, 50, demoted=True, demoted_tick=400,
                   owed=_owed(CQ, 0.68)),
        **T._entry(VBC, 0.68, 0, demoted=False, demoted_tick=None,
                   owed=_owed(VBC, 0.68)),
    )
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.64, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 50),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.68, 3, 0)}
    before = path.read_bytes()
    data_before = json.loads(json.dumps(tracker.data))
    with pytest.raises(ValueError) as excinfo:
        tracker.observe_tick(tick=401, state_habits=live)
    assert VBC in str(excinfo.value)
    assert path.read_bytes() == before
    assert tracker.data == data_before
    assert tracker.data["habits"][CQ]["baseline"] == 0.68  # no re-sync
    assert tracker.data["habits"][CQ]["owed"] == _owed(CQ, 0.68)


def test_reconcile_boundary_shape_matches_changelog():
    # The CHANGELOG's known boundary, verified rather than assumed:
    # a NON-demoted sub-threshold entry with a hand-set live strength
    # >= 0.65 and a firing stamp this tick reports NO state change from
    # observe_tick itself — but when an at-threshold sibling's
    # demotion triggers the caller's reconcile, the untouched
    # reconcile_state_habits firing branch targets the sub-threshold
    # entry at its baseline (0.64). observe_tick never mints, never
    # demotes, and never reports state_changed for this entry alone.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp,
        **T._entry(CQ, 0.64, 0, demoted=False, demoted_tick=None, owed=None),
        **T._entry(VBC, 0.68, 0),
    )
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.64, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.68, 3, 0)}
    # CQ alone (VBC absent): no state change, no event, no demotion.
    events, sc, stc = tracker.observe_tick(
        tick=401, state_habits={CQ: live[CQ]})
    assert events == [] and stc is False
    assert tracker.data["habits"][CQ]["demoted"] is False
    assert tracker.data["habits"][CQ]["owed"] is None
    assert live[CQ].strength == 0.72
    # With the sibling present and a firing stamp on CQ this tick:
    # VBC demotes (state_changed True); CQ's clock is untouched by
    # observe_tick, and only the sibling-triggered reconcile moves CQ.
    live[CQ].last_used_tick = 401
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert stc is True  # from VBC's demotion, not from CQ
    assert len(events) == 1 and events[0]["habit_key"] == VBC
    assert tracker.data["habits"][CQ]["demoted"] is False
    assert tracker.data["habits"][CQ]["last_fired_tick"] == 0
    assert tracker.data["habits"][CQ]["owed"] is None
    tracker.reconcile_state_habits(live, tick=401)
    assert live[VBC].strength == 0.64  # the demotion
    assert live[CQ].strength == 0.64  # the disclosed boundary shape
