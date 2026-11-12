"""Critic round 3 (final round): goal-structure instrument, round-2 fix.

Spec: research/goal-structure-instrument-spec-2026-11-10.md
Diff under attack: calibos_mind/goals.py — GoalTracker._validate_row
gained the round-2 stricter checks (due_soon <= commitments["open"],
oldest_open_age null iff open == 0, oldest_open_age <= tick, tick >= 0),
tests/test_goals.py — the round-2 corrupt cases plus the overdue-only
positive. Round-1 critic file (18 tests) and round-2 critic file
(9 tests) re-verified green in this round before these probes were
written, and both files are unchanged since their rounds (mtime and
test-count evidence; see the round-3 report).

Every objection here is a failing test or a concretely violated
invariant — prose-only critique is rejected by the loop. All fixtures
on /tmp; the live store is never touched (conftest autouse redirect).

Attack plan:
  A. Fresh variants of the two newly closed invariants, not covered by
     the builder's cases: age-exceeds-tick AT the boundary (age ==
     tick + 1, where the builder used tick=1 age=5, far from the
     boundary) in both an overdue-only and a status-open row, and a
     negative tick in an otherwise fully populated, fully consistent
     row (the builder's negative-tick case used the all-zero default
     row). Plus the positive boundary guard age == tick must load,
     proving the check is not over-tightened to age < tick.
  B. Genuine-file positive, built the honest way: a mixed row (one
     open due-soon commitment + one overdue commitment) produced by a
     real observe() on a synthetic store must load and re-save
     BYTE-identically — a stronger check than the builder's dict-level
     round-trip.
  C. Full sweep of _validate_row against _live_counts, the sole row
     producer, for any remaining same-class hole — cross-field,
     cross-row, derived-value — including the tension invariant the
     round-2 builder explicitly scoped out (tension.current >
     tension.longest is unproducible by save(): observe() maintains
     longest = max(longest, current) on every tick). That one loads
     silently: a failing test. Two further sweep probes are recorded
     in the report, not as failing tests, with the evidence for why
     they are out of the _validate_row-vs-_live_counts comparison:
     rows with decreasing ticks load (tick order is not derived from
     _live_counts, which takes tick as input; no ordering invariant
     is stated anywhere), and tension-vs-rows consistency cannot be
     enforced from the sidecar at all (ROW_CAP truncates rows, so a
     trailing run may legitimately extend past the stored rows).
     due_soon's lower bound is likewise not derivable from a row
     alone (it needs due_tick, which the row does not store).
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
from calibos_mind.goals import GoalTracker


# -- fixtures (independent copies of the r1/r2 harness) ----------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-goals-r3-"))


def _sidecar_data(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


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


def _init_store():
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _tick(subject):
    return _script_tick(subject, _needs(curiosity=0.80),
                         _pressures(), _baselines())


def _add_commitment(subject, actor, description, due_tick=None,
                    importance=0.5, commitment_id=None):
    with subject._transaction():
        item = subject.continuity.create_commitment(
            actor, description, beneficiary="self",
            tick=subject.engine.state.tick, due_tick=due_tick,
            importance=importance, commitment_id=commitment_id)
    return item.id


def _row(**over):
    row = {"tick": 1, "open": 0, "oldest_open_age": None,
           "commitments": {"open": 0, "overdue": 0, "kept": 0, "broken": 0,
                           "released": 0},
           "due_soon": 0, "open_expectations": 0,
           "expectations": {"pending": 0, "confirmed": 0, "violated": 0,
                            "expired": 0}}
    row.update(over)
    return row


def _write_sidecar(path: Path, rows=None, known=None, tension=None) -> None:
    path.write_text(json.dumps({
        "rows": rows if rows is not None else [],
        "known": known if known is not None else {},
        "tension": tension if tension is not None else {"current": 0, "longest": 0},
    }), encoding="utf-8")


# -- A. fresh boundary variants of the newly closed invariants ---------------

def test_critic_age_one_past_tick_overdue_only_rejected():
    """age == tick + 1, the exact boundary: a commitment created at
    tick 0 observed at tick 3 has age at most 3. The builder's
    age-exceeds-tick case (tick=1, age=5) sits far from the boundary;
    this overdue-only row is the minimal violation."""
    tmp = _tmp()
    path = tmp / "goals.json"
    _write_sidecar(path, rows=[_row(
        tick=3, open=1, oldest_open_age=4,
        commitments={"open": 0, "overdue": 1, "kept": 0, "broken": 0,
                     "released": 0})])
    with pytest.raises(ValueError, match="corrupt goals"):
        GoalTracker(path)


def test_critic_age_one_past_tick_status_open_rejected():
    """Same boundary, status-open population: tick=10, age=11. An age
    one past the tick implies a negative created tick in either
    population; the check must not depend on which status is open."""
    tmp = _tmp()
    path = tmp / "goals.json"
    _write_sidecar(path, rows=[_row(
        tick=10, open=1, oldest_open_age=11,
        commitments={"open": 1, "overdue": 0, "kept": 0, "broken": 0,
                     "released": 0})])
    with pytest.raises(ValueError, match="corrupt goals"):
        GoalTracker(path)


def test_critic_negative_tick_fully_populated_row_rejected():
    """A negative tick in an otherwise fully populated, fully
    consistent row (open=1 with matching status counts, age present,
    a pending expectation counted). The builder's negative-tick case
    used the all-zero default row; a negative tick must be rejected
    no matter how consistent the rest of the row looks."""
    tmp = _tmp()
    path = tmp / "goals.json"
    _write_sidecar(path, rows=[_row(
        tick=-2, open=1, oldest_open_age=1,
        commitments={"open": 1, "overdue": 0, "kept": 0, "broken": 0,
                     "released": 0},
        due_soon=0, open_expectations=1,
        expectations={"pending": 1, "confirmed": 0, "violated": 0,
                      "expired": 0})])
    with pytest.raises(ValueError, match="corrupt goals"):
        GoalTracker(path)


def test_critic_age_equal_to_tick_loads():
    """Positive boundary guard: age == tick is genuine (a commitment
    created at tick 0 observed at tick 4 has age exactly 4). The
    age check must reject age > tick only, not over-tighten to
    age < tick."""
    tmp = _tmp()
    path = tmp / "goals.json"
    row = _row(tick=4, open=1, oldest_open_age=4,
               commitments={"open": 1, "overdue": 0, "kept": 0, "broken": 0,
                            "released": 0})
    _write_sidecar(path, rows=[row])
    tracker = GoalTracker(path)
    assert tracker.rows == [row]
    tracker.save()
    assert GoalTracker(path).rows == [row]


# -- B. genuine-file positive via a real observe() ---------------------------

def test_critic_genuine_mixed_row_from_live_observe_round_trips_byte_identical():
    """A mixed row produced by a real observe() on a synthetic store:
    one open commitment due within the 2-tick window (due_soon=1)
    plus one overdue commitment, both created at tick 0, observed at
    tick 1. The live-written file must load, and a load/save cycle
    must reproduce it BYTE-identically (stronger than the builder's
    dict-level round-trip)."""
    tmp = _tmp()
    subject = _init_store()
    _add_commitment(subject, "self", "due soon", due_tick=2,
                    commitment_id="r3-due-soon")
    _add_commitment(subject, "self", "past due", due_tick=0,
                    commitment_id="r3-overdue")
    _tick(subject)  # tick 1: the due_tick=0 commitment flips overdue.
    live_path = cli.GOALS
    expected = _row(tick=1, open=2, oldest_open_age=1,
                    commitments={"open": 1, "overdue": 1, "kept": 0,
                                 "broken": 0, "released": 0},
                    due_soon=1, open_expectations=0)
    live_row = _sidecar_data(live_path)["rows"][0]
    assert live_row == expected, (live_row, expected)
    before = live_path.read_bytes()
    tracker = GoalTracker(live_path)
    assert tracker.rows == [expected]
    tracker.save()
    assert live_path.read_bytes() == before


# -- C. tension invariant scoped out by the round-2 builder ------------------

def test_critic_tension_current_exceeds_longest_rejected():
    """FAIL (round 3): tension.current > tension.longest loads
    silently. observe() maintains longest = max(longest, current) on
    every tick, so save() can never produce current=5, longest=2 —
    a tampered tension section, the same fail-closed class the
    round-1/round-2 row fixes close. The spec's corrupt-sidecar
    clause covers a malformed tension section explicitly."""
    tmp = _tmp()
    path = tmp / "goals.json"
    _write_sidecar(path, rows=[_row()], tension={"current": 5, "longest": 2})
    with pytest.raises(ValueError, match="corrupt goals"):
        GoalTracker(path)


def test_critic_tension_current_equal_longest_loads():
    """Positive guard for the tension check: current == longest is
    genuine (a run still in progress) and must keep loading."""
    tmp = _tmp()
    path = tmp / "goals.json"
    _write_sidecar(path, rows=[_row()], tension={"current": 2, "longest": 2})
    tracker = GoalTracker(path)
    assert tracker.tension == {"current": 2, "longest": 2}
