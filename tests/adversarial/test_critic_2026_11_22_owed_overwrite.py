"""Critic attack tests for the domain-29 fail-loud demotion edge.

Cold attacks only: each test targets a concrete invariant from
research/specs/2026-11-22-owed-overwrite.md that the builder's own tests
do not cover. Synthetic /tmp stores only; the live store is never
touched.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_authored_dormancy as T
from calibos_mind.authored_dormancy import AuthoredDormancyTracker
from digital_subject.models import Action, Habit

CQ = T.CQ
VBC = T.VBC


def _owed(key, baseline, tick=400, span=400):
    return {"habit_key": key, "dormancy_ticks": span, "baseline": baseline,
            "demoted_tick": tick}


def test_boundary_span_exactly_300_does_not_raise():
    # The edge requires tick - last_fired > 300; at exactly 300 it must
    # not fire, so the pre-pass must not raise either: the owed episode
    # re-emits untouched.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.72, 101, demoted=False, demoted_tick=None,
                        owed=_owed(CQ, 0.72)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 101)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed(CQ, 0.72)]
    assert tracker.data["habits"][CQ]["demoted"] is False
    assert tracker.data["habits"][CQ]["owed"] == _owed(CQ, 0.72)


def test_boundary_span_301_raises():
    # One tick past the boundary the edge would fire, so the pre-pass
    # must raise and touch nothing.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.72, 100, demoted=False, demoted_tick=None,
                        owed=_owed(CQ, 0.72)))
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 100)}
    before = path.read_bytes()
    data_before = json.loads(json.dumps(tracker.data))
    with pytest.raises(ValueError):
        tracker.observe_tick(tick=401, state_habits=live)
    assert path.read_bytes() == before
    assert tracker.data == data_before


def test_stale_baseline_resync_does_not_land_before_raise():
    # Two-phase claim vs a NON-demotion loop mutation: CQ sorts first,
    # holds a stale sidecar baseline (0.60 vs authored 0.72) that the
    # loop would re-sync, and VBC holds a pending owed mark that must
    # abort the tick. The raise must precede the re-sync in memory too.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp,
        **T._entry(CQ, 0.60, 0),
        **T._entry(VBC, 0.68, 0, demoted=False, demoted_tick=None,
                   owed=_owed(VBC, 0.68)),
    )
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.68, 3, 0)}
    before = path.read_bytes()
    data_before = json.loads(json.dumps(tracker.data))
    with pytest.raises(ValueError) as excinfo:
        tracker.observe_tick(tick=401, state_habits=live)
    assert VBC in str(excinfo.value)
    assert path.read_bytes() == before
    assert tracker.data == data_before
    assert tracker.data["habits"][CQ]["baseline"] == 0.60  # no re-sync
    assert tracker.data["habits"][CQ]["demoted"] is False


def test_live_none_with_pending_owed_reemits_without_raise():
    # No live entry: the edge cannot fire (it needs live.strength), so
    # the pre-pass must not raise; the owed episode still re-emits.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                        owed=_owed(CQ, 0.72)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    events, sc, stc = tracker.observe_tick(tick=401, state_habits={})
    assert events == [_owed(CQ, 0.72)]
    assert tracker.data["habits"][CQ]["demoted"] is False


def test_missing_entry_dormant_demotes_fresh_without_raise():
    # No sidecar entry at all: the loop seeds one with owed None, so
    # there is nothing to lose and the normal edge must demote.
    tmp = T._tmp()
    tracker = AuthoredDormancyTracker(tmp / "d.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert len(events) == 1 and stc
    assert events[0] == {"habit_key": CQ, "dormancy_ticks": 401,
                         "baseline": 0.72, "demoted_tick": 401}
    assert tracker.data["habits"][CQ]["demoted"] is True


def test_subthreshold_baseline_with_owed_reemits_without_raise():
    # Authored baseline below the bypass threshold: the edge never
    # fires for this habit, so the pre-pass must not raise. SUPERSEDED
    # 2026-11-23 (spec research/specs/2026-11-23-owed-reemit-
    # subthreshold.md, domain 30): the 2026-11-22 version of this test
    # pinned the pre-fix behavior — the loop's `continue` on baseline
    # < 0.65 skipped the whole iteration INCLUDING the owed re-emit,
    # so no event was emitted and a pending owed mark on a
    # sub-threshold entry stalled silently. That was the defect the
    # 11-23 spec fixes (critic observation 2 from the 11-22 loop): the
    # owed mark is a write-ahead mint obligation and must re-emit for
    # every baseline until acked. This test now pins the FIXED
    # behavior: no raise, no demotion, owed intact, event re-emitted.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.60, 0, demoted=False, demoted_tick=None,
                        owed=_owed(CQ, 0.60)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.60})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live)
    assert events == [_owed(CQ, 0.60)]  # the owed episode re-emits
    assert tracker.data["habits"][CQ]["demoted"] is False
    assert tracker.data["habits"][CQ]["owed"] == _owed(CQ, 0.60)
    assert not stc


def test_strength_threshold_boundary():
    # Edge and pre-pass both use >= 0.65: exactly 0.65 with a pending
    # owed mark and dormancy must raise; 0.64 must re-emit quietly.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp, **T._entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                        owed=_owed(CQ, 0.72)))
    tracker = AuthoredDormancyTracker(
        tmp / "authored-dormancy.json", {CQ: 0.72})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.65, 2, 0)}
    with pytest.raises(ValueError):
        tracker.observe_tick(tick=401, state_habits=live)
    live2 = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}
    events, sc, stc = tracker.observe_tick(tick=401, state_habits=live2)
    assert events == [_owed(CQ, 0.72)]
    assert not stc


def test_two_pending_owed_both_would_fire_names_first_sorted():
    # Both habits would fire with pending owed marks: the raise must
    # name the first in sorted order (deterministic) and leave both
    # entries untouched.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp,
        **T._entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                   owed=_owed(CQ, 0.72)),
        **T._entry(VBC, 0.68, 0, demoted=False, demoted_tick=None,
                   owed=_owed(VBC, 0.68)),
    )
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.72, 2, 0),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.68, 3, 0)}
    data_before = json.loads(json.dumps(tracker.data))
    with pytest.raises(ValueError) as excinfo:
        tracker.observe_tick(tick=401, state_habits=live)
    assert CQ in str(excinfo.value)  # sorted-first habit named
    assert tracker.data == data_before


def test_waking_tick_raise_store_tick_persistence():
    # Subject-level characterization of the spec's "before the tick
    # advances". MEASURED (and verified identical on the pristine
    # pre-fix worktree): after the waking-tick raise, a fresh subject
    # constructed on the same store reads tick 401 from disk — the
    # heartbeat's tick advance is persisted BEFORE the dormancy block
    # runs, so "before the tick advances" is true only at tracker
    # level (no dormancy mutation, mint, or sidecar write), not at
    # subject/store level. No fix exists inside this spec's scope:
    # moving the dormancy block before the heartbeat would require
    # touching cli.py ("no other module changes") and would break the
    # firing proof, which reads the stamp the heartbeat's bypass
    # branch sets. This test pins the measured behavior: disk tick
    # 401, dormancy sidecar byte-identical, no mint, owed intact.
    tmp = T._tmp()
    T._seed_dormancy_sidecar(
        tmp,
        **T._entry(CQ, 0.72, 0, demoted=False, demoted_tick=None,
                   owed=_owed(CQ, 0.72)),
        **T._entry(VBC, 0.68, 400),
    )
    subject = T._make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 400
        st.habits[CQ].last_used_tick = 0
        st.habits[VBC].last_used_tick = 400
    sidecar_before = (tmp / "authored-dormancy.json").read_bytes()
    with pytest.raises(ValueError):
        T._quiet_tick(subject)
    fresh = T._make_subject(tmp)
    assert fresh.engine.state.tick == 401  # heartbeat persisted
    assert (tmp / "authored-dormancy.json").read_bytes() == sidecar_before
    assert len(T._demotion_notes(fresh)) == 0  # no mint
    sidecar = json.loads(sidecar_before)
    assert sidecar["habits"][CQ]["owed"] == _owed(CQ, 0.72)
    assert sidecar["habits"][CQ]["demoted"] is False
