"""Critic round 2: goal-structure instrument, round-1 cross-field fix.

Spec: research/goal-structure-instrument-spec-2026-11-10.md
Diff under attack: calibos_mind/goals.py — GoalTracker._validate_row
gained three cross-field checks (open == commitments["open"] +
commitments["overdue"], due_soon <= open, open_expectations ==
expectations["pending"]), tests/test_goals.py — four cross-field corrupt
cases in test_corrupt_sidecar_raises_loud plus
test_consistent_row_at_cross_field_boundaries_loads, CHANGELOG note.

Round-1 critic file (tests/adversarial/test_critic_goals_r1.py, 18 tests)
re-verified green in this round before these probes were written.

Every objection here is a failing test or a concretely violated
invariant — prose-only critique is rejected by the loop. All fixtures
on /tmp; the live store is never touched (conftest autouse redirect).

Attack plan:
  A. Fresh variants of the three named invariants, not covered by the
     builder's four cases: open miscounted by ignoring overdue (the
     overdue-only row), open_expectations exceeding a NONZERO pending
     count (and under-counting it), and a row that is cross-field
     consistent but shape-invalid (proving the new checks did not
     displace the old shape checks).
  B. Genuine-file positive, built the honest way: a boundary row
     produced by a real observe() on a synthetic store (one open
     due-soon commitment + one overdue commitment) must load, and the
     builder's boundary test must be reproducing a row save() can
     actually produce — cross-checked field by field against the
     live observe() output.
  C. Determinism: a save/load round-trip preserves a consistent row
     exactly, byte-identically on re-save.
  D. Stricter invariants of the SAME defect class, not named in round
     1: save() can never produce due_soon on a row with zero
     status-"open" commitments (due_soon counts status "open" only,
     goals.py _live_counts), nor oldest_open_age present with zero
     open, nor oldest_open_age null with open commitments. These are
     the same tampered/half-written-row class the fix claims to close.
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


# -- fixtures (independent copies of the r1 harness) ------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-goals-r2-"))


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


# -- A. fresh variants of the three named invariants -------------------------

def test_critic_overdue_only_open_miscount_rejected():
    """open must count open + overdue. An overdue-only row where open
    counts only the status-"open" bucket (open=1 with open=1, overdue=1)
    is a tampered row the builder's four cases do not cover."""
    tmp = _tmp()
    bad_rows = {
        # open counts only "open", ignoring the overdue commitment.
        "open_ignores_overdue": _row(
            open=1, oldest_open_age=2, tick=1,
            commitments={"open": 1, "overdue": 1, "kept": 0, "broken": 0,
                         "released": 0}),
        # open counts an overdue commitment twice (open=2, only 1 total).
        "open_double_counts_overdue": _row(
            open=2, oldest_open_age=2, tick=1,
            commitments={"open": 0, "overdue": 1, "kept": 0, "broken": 0,
                         "released": 0}),
    }
    for name, row in bad_rows.items():
        path = tmp / f"{name}.json"
        _write_sidecar(path, rows=[row])
        with pytest.raises(ValueError, match="corrupt goals"):
            GoalTracker(path)


def test_critic_open_expectations_exceeds_nonzero_pending_rejected():
    """open_expectations must equal pending exactly — not just when
    pending is 0 (the builder's case). Both directions, nonzero."""
    tmp = _tmp()
    bad_rows = {
        # open_expectations 3 while only 2 are pending.
        "open_expectations_overcounts": _row(
            open_expectations=3, tick=1,
            expectations={"pending": 2, "confirmed": 1, "violated": 0,
                          "expired": 0}),
        # open_expectations 1 while 2 are pending (under-count).
        "open_expectations_undercounts": _row(
            open_expectations=1, tick=1,
            expectations={"pending": 2, "confirmed": 0, "violated": 0,
                          "expired": 0}),
    }
    for name, row in bad_rows.items():
        path = tmp / f"{name}.json"
        _write_sidecar(path, rows=[row])
        with pytest.raises(ValueError, match="corrupt goals"):
            GoalTracker(path)


def test_critic_cross_field_consistent_but_shape_invalid_rejected():
    """A row can satisfy all three cross-field checks and still be
    malformed. The new checks must not have displaced the shape checks:
    a negative oldest_open_age (and a negative kept count) on an
    otherwise consistent row must still raise."""
    tmp = _tmp()
    bad_rows = {
        "negative_oldest_age": _row(
            open=1, oldest_open_age=-1, tick=1,
            commitments={"open": 1, "overdue": 0, "kept": 0, "broken": 0,
                         "released": 0}),
        "negative_kept_count": _row(
            open=1, oldest_open_age=1, tick=1,
            commitments={"open": 1, "overdue": 0, "kept": -1, "broken": 0,
                         "released": 0}),
    }
    for name, row in bad_rows.items():
        path = tmp / f"{name}.json"
        _write_sidecar(path, rows=[row])
        with pytest.raises(ValueError, match="corrupt goals"):
            GoalTracker(path)


# -- B. genuine-file positive, from a real observe() --------------------------

def test_critic_genuine_boundary_row_from_live_observe_loads():
    """Positive guard, built the honest way: drive a real synthetic
    store to a boundary row (one open due-soon commitment + one
    overdue commitment, due_soon == open == 2) via observe(), save,
    reload, and assert the row survives exactly. This is the row shape
    the builder's boundary test claims save() can produce — here it is
    produced by save() itself, not hand-written."""
    subject = _init_store()
    _add_commitment(subject, "self", "past due", due_tick=0,
                    commitment_id="r2-overdue")
    _add_commitment(subject, "self", "due soon", due_tick=3,
                    commitment_id="r2-soon")
    _tick(subject)  # tick 1: first flips overdue, second is due soon.
    data = _sidecar_data(cli.GOALS)
    assert len(data["rows"]) == 1
    row = data["rows"][0]
    assert row["open"] == 2, row
    assert row["commitments"] == {"open": 1, "overdue": 1, "kept": 0,
                                  "broken": 0, "released": 0}, row
    assert row["due_soon"] == 1, row
    assert row["oldest_open_age"] == 1, row
    # Reload the file the live path wrote: it must load, and a re-save
    # must be byte-identical (deterministic serialization).
    before = (cli.GOALS).read_bytes()
    tracker = GoalTracker(cli.GOALS)
    assert tracker.rows == [row]
    tracker.save()
    assert (cli.GOALS).read_bytes() == before


def test_critic_builder_boundary_row_matches_live_shape():
    """The builder's boundary test must be honest, not vacuous: its
    hand-written rows must match, field by field, a row the live
    observe() path actually produces for the same population. Rebuild
    the same population live and compare the row the engine path
    writes against the builder's claimed boundary row."""
    subject = _init_store()
    _add_commitment(subject, "self", "past due", due_tick=0,
                    commitment_id="b-overdue")
    _tick(subject)  # tick 1: overdue, open=1, oldest age 1.
    live_row = _sidecar_data(cli.GOALS)["rows"][0]
    builder_row = _row(tick=1, open=1, oldest_open_age=1,
                       commitments={"open": 0, "overdue": 1, "kept": 0,
                                    "broken": 0, "released": 0},
                       due_soon=0, open_expectations=0,
                       expectations={"pending": 0, "confirmed": 0,
                                     "violated": 0, "expired": 0})
    assert live_row == builder_row, (live_row, builder_row)
    # And that row, hand-written exactly as the builder writes it,
    # loads and round-trips.
    tmp = _tmp()
    path = tmp / "goals.json"
    _write_sidecar(path, rows=[builder_row])
    tracker = GoalTracker(path)
    assert tracker.rows == [builder_row]
    tracker.save()
    assert GoalTracker(path).rows == [builder_row]


# -- C. determinism: save/load round-trip preserves a row exactly -------------

def test_critic_save_load_round_trip_preserves_consistent_row_exactly():
    """A consistent row at the cross-field boundaries (overdue
    contributing to open, due_soon == open, open_expectations ==
    pending) must survive load -> save -> load with rows identical
    and the file byte-identical on re-save."""
    tmp = _tmp()
    path = tmp / "goals.json"
    row = _row(tick=4, open=2, oldest_open_age=3,
               commitments={"open": 1, "overdue": 1, "kept": 1, "broken": 0,
                            "released": 0},
               due_soon=1, open_expectations=2,
               expectations={"pending": 2, "confirmed": 1, "violated": 0,
                             "expired": 0})
    row2 = _row(tick=5, open=2, oldest_open_age=4,
                commitments={"open": 2, "overdue": 0, "kept": 1, "broken": 0,
                             "released": 0},
                due_soon=2, open_expectations=1,
                expectations={"pending": 1, "confirmed": 2, "violated": 0,
                              "expired": 0})
    _write_sidecar(path, rows=[row, row2],
                   known={"c1": {"actor": "self", "description": "d",
                                 "kind": "authored", "first_tick": 1}},
                   tension={"current": 2, "longest": 2})
    tracker = GoalTracker(path)
    assert tracker.rows == [row, row2]
    tracker.save()
    first_bytes = path.read_bytes()
    assert GoalTracker(path).rows == [row, row2]
    GoalTracker(path).save()
    assert path.read_bytes() == first_bytes


# -- D. stricter invariants of the same defect class --------------------------

def test_critic_due_soon_on_overdue_only_row_rejected():
    """FAIL (stricter invariant, same class): due_soon counts status
    "open" commitments ONLY (goals.py _live_counts: `status == "open"`
    gates the due-soon bucket). A row with zero status-"open"
    commitments and due_soon=1 satisfies due_soon <= open (open=1 via
    the overdue commitment) yet save() can never produce it."""
    tmp = _tmp()
    row = _row(open=1, oldest_open_age=2, tick=1,
               commitments={"open": 0, "overdue": 1, "kept": 0, "broken": 0,
                            "released": 0},
               due_soon=1)
    path = tmp / "goals.json"
    _write_sidecar(path, rows=[row])
    with pytest.raises(ValueError, match="corrupt goals"):
        GoalTracker(path)


def test_critic_oldest_age_present_with_zero_open_rejected():
    """FAIL (stricter invariant, same class): save() writes
    oldest_open_age None exactly when open == 0 (no open/overdue
    commitment exists to age). A zero-open row carrying an age is a
    tampered or half-written row."""
    tmp = _tmp()
    row = _row(open=0, oldest_open_age=3, tick=1)
    path = tmp / "goals.json"
    _write_sidecar(path, rows=[row])
    with pytest.raises(ValueError, match="corrupt goals"):
        GoalTracker(path)


def test_critic_oldest_age_null_with_open_rejected():
    """FAIL (stricter invariant, same class): save() always writes an
    integer oldest_open_age when open >= 1 (at least one open/overdue
    commitment exists to age). A row with open commitments and a null
    age is a tampered or half-written row."""
    tmp = _tmp()
    row = _row(open=1, oldest_open_age=None, tick=1,
               commitments={"open": 1, "overdue": 0, "kept": 0, "broken": 0,
                            "released": 0})
    path = tmp / "goals.json"
    _write_sidecar(path, rows=[row])
    with pytest.raises(ValueError, match="corrupt goals"):
        GoalTracker(path)
