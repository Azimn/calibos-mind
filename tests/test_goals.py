"""Tests for the goal-structure instrument (Domain 17, goal structure).

Spec: research/goal-structure-instrument-spec-2026-11-10.md
All fixtures on /tmp — the live store is never touched.

The mechanism: calibos_mind/goals.py — a GoalTracker owning the
goals.json sidecar, fed ONLY from cli._run_tick after the tick, plus a
`mind goals` command and a short goals section in `mind drift` output.
The tracker snapshots the live commitment/expectation population once
per waking tick, diffs commitment ids against the ids already known
(actor != "self" -> candidate accidentally-formed goal, actor "self" ->
authored), and tracks the tension span: the longest run of consecutive
observed ticks with >= 1 open commitment, and the current run.

Fitness functions under test:
  1. Stores with 0/1/3 open commitments in mixed statuses
     (open/overdue/kept/broken/released) report exact counts;
     oldest-open age exact; a commitment due within 2 ticks is flagged
     due-soon, consistent with derive_continuity_influence's window.
  2. A commitment appearing between observations with actor != "self"
     is recorded as a candidate formed goal; actor "self" as authored.
  3. Tension span: an open goal held across K observed ticks reports
     span K; closing all goals resets the current span to 0 while the
     longest span is retained.
  4. Read-only sweep (drift, status, review, inbox, goals, think,
     dream command) leaves goals.json byte-identical.
  5. init --force wipes the sidecar including a corrupt one; corrupt
     sidecar fails closed before the tick advances.
  6. Determinism: byte-identical sidecars on replay; full suite green
     (run separately).
  7. report() is a pure read: twice-called equal, sidecar untouched;
     expectation outcome tallies exact.
  8. Row cap: after 100+ observations the sidecar holds exactly 100
     rows, oldest dropped.

Scenario control (mirrors test_personality.py): the tests pin
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
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.goals import ROW_CAP, GoalTracker, report


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="goals-test-"))


def _sidecar_data(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_sidecar(path: Path, rows=None, known=None, tension=None) -> None:
    path.write_text(json.dumps({
        "rows": rows if rows is not None else [],
        "known": known if known is not None else {},
        "tension": tension if tension is not None else {"current": 0, "longest": 0},
    }), encoding="utf-8")


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


def _tick(subject):
    return _script_tick(subject, _needs(curiosity=0.80),
                         _pressures(), _baselines())


def _add_commitment(subject, actor, description, due_tick=None,
                    importance=0.5, commitment_id=None):
    """Create one commitment in the live store (transaction-committed)."""
    with subject._transaction():
        item = subject.continuity.create_commitment(
            actor, description, beneficiary="self",
            tick=subject.engine.state.tick, due_tick=due_tick,
            importance=importance, commitment_id=commitment_id)
    return item.id


def _add_expectation(subject, proposition, due_tick=None, confidence=0.5,
                     expectation_id=None):
    """Create one expectation in the live store (transaction-committed)."""
    with subject._transaction():
        item = subject.continuity.create_expectation(
            proposition, tick=subject.engine.state.tick, due_tick=due_tick,
            confidence=confidence, expectation_id=expectation_id)
    return item.id


def _resolve_commitment(subject, cid, kept=True, outcome="done"):
    with subject._transaction():
        subject.continuity.resolve_commitment(
            cid, outcome=outcome, kept=kept,
            tick=subject.engine.state.tick)


def _release_commitment(subject, cid, outcome="deliberately released"):
    with subject._transaction():
        subject.release_commitment(
            cid, outcome=outcome, tick=subject.engine.state.tick)


def _resolve_expectation(subject, eid, confirmed, outcome="observed"):
    with subject._transaction():
        subject.continuity.resolve_expectation(
            eid, outcome=outcome, confirmed=confirmed,
            tick=subject.engine.state.tick)


def _goals_output():
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_goals(argparse.Namespace())
    return buf.getvalue()


def _drift_output():
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_drift(argparse.Namespace(window=10))
    return buf.getvalue()


def _row(**over):
    """One well-formed snapshot row; override fields per case."""
    row = {"tick": 1, "open": 0, "oldest_open_age": None,
           "commitments": {"open": 0, "overdue": 0, "kept": 0, "broken": 0,
                           "released": 0},
           "due_soon": 0, "open_expectations": 0,
           "expectations": {"pending": 0, "confirmed": 0, "violated": 0,
                            "expired": 0}}
    row.update(over)
    return row


# -- fitness 1: exact counts, oldest-open age, due-soon ----------------------

def test_zero_open_commitments_report_exact_zero():
    """Fresh store, one tick: open 0, oldest-open age null, every
    status count 0, open expectations 0."""
    subject = _init_store()
    _tick(subject)
    data = _sidecar_data(cli.GOALS)
    assert len(data["rows"]) == 1
    row = data["rows"][0]
    assert row["tick"] == 1
    assert row["open"] == 0
    assert row["oldest_open_age"] is None
    assert row["commitments"] == {"open": 0, "overdue": 0, "kept": 0,
                                  "broken": 0, "released": 0}
    assert row["due_soon"] == 0
    assert row["open_expectations"] == 0
    assert row["expectations"] == {"pending": 0, "confirmed": 0,
                                   "violated": 0, "expired": 0}
    assert data["known"] == {}
    assert data["tension"] == {"current": 0, "longest": 0}
    rep = report(subject, cli.GOALS)
    assert rep["commitments"]["open"] == 0
    assert rep["commitments"]["oldest_open_age"] is None
    assert rep["commitments"]["due_soon"] == []
    assert rep["expectations"]["open"] == 0
    assert rep["tension"] == {"current": 0, "longest": 0}


def test_one_open_commitment_exact_counts_and_age():
    """One open commitment: open 1, oldest-open age exact (created at
    tick 0, observed at tick 1 -> age 1)."""
    subject = _init_store()
    cid = _add_commitment(subject, "self", "water the plants",
                          commitment_id="goal-one-1")
    _tick(subject)
    row = _sidecar_data(cli.GOALS)["rows"][0]
    assert row["open"] == 1
    assert row["oldest_open_age"] == 1, row
    assert row["commitments"]["open"] == 1
    assert row["commitments"]["overdue"] == 0
    rep = report(subject, cli.GOALS)
    assert rep["commitments"]["open"] == 1
    assert rep["commitments"]["oldest_open_age"] == 1
    assert rep["formed"][0]["id"] == cid
    assert rep["formed"][0]["kind"] == "authored"


def test_mixed_statuses_exact_counts():
    """Five commitments, one per status (open/overdue/kept/broken/
    released): every count exact, open = open + overdue = 2, oldest
    age exact. Four expectations, one per status: tallies exact."""
    subject = _init_store()
    # Overdue and expired arrive via the engine's own advance_deadlines:
    # due_tick 0 flips once the tick advances past it.
    _add_commitment(subject, "self", "open one", commitment_id="mix-open")
    _add_commitment(subject, "self", "overdue one", due_tick=0,
                    commitment_id="mix-overdue")
    kept = _add_commitment(subject, "self", "kept one",
                           commitment_id="mix-kept")
    broken = _add_commitment(subject, "self", "broken one",
                             commitment_id="mix-broken")
    released = _add_commitment(subject, "self", "released one",
                               commitment_id="mix-released")
    _resolve_commitment(subject, kept, kept=True)
    _resolve_commitment(subject, broken, kept=False)
    _release_commitment(subject, released)
    _add_expectation(subject, "pending one", expectation_id="exp-pending")
    confirmed = _add_expectation(subject, "confirmed one",
                                 expectation_id="exp-confirmed")
    violated = _add_expectation(subject, "violated one",
                                expectation_id="exp-violated")
    _add_expectation(subject, "expired one", due_tick=0,
                     expectation_id="exp-expired")
    _resolve_expectation(subject, confirmed, confirmed=True)
    _resolve_expectation(subject, violated, confirmed=False)
    _tick(subject)
    row = _sidecar_data(cli.GOALS)["rows"][0]
    assert row["commitments"] == {"open": 1, "overdue": 1, "kept": 1,
                                  "broken": 1, "released": 1}, row
    assert row["open"] == 2
    # Oldest open/overdue commitment was created at tick 0, observed
    # at tick 1.
    assert row["oldest_open_age"] == 1, row
    # The overdue one is past due, not "due soon": due-soon counts
    # status "open" only, matching derive_continuity_influence.
    assert row["due_soon"] == 0, row
    assert row["expectations"] == {"pending": 1, "confirmed": 1,
                                   "violated": 1, "expired": 1}, row
    assert row["open_expectations"] == 1
    rep = report(subject, cli.GOALS)
    assert rep["expectations"]["outcomes"] == {"confirmed": 1,
                                               "violated": 1, "expired": 1}
    assert rep["expectations"]["open"] == 1


def test_oldest_open_age_tracks_oldest_of_several():
    """Three open commitments created at ticks 0, 1, 2: the oldest-open
    age is the age of the earliest-created one."""
    subject = _init_store()
    _add_commitment(subject, "self", "first", commitment_id="age-first")
    _tick(subject)  # tick 1
    _add_commitment(subject, "self", "second", commitment_id="age-second")
    _tick(subject)  # tick 2
    _add_commitment(subject, "self", "third", commitment_id="age-third")
    _tick(subject)  # tick 3
    rows = _sidecar_data(cli.GOALS)["rows"]
    assert [r["oldest_open_age"] for r in rows] == [1, 2, 3], rows
    assert [r["open"] for r in rows] == [1, 2, 3], rows
    rep = report(subject, cli.GOALS)
    assert rep["commitments"]["oldest_open_age"] == 3
    assert rep["commitments"]["open"] == 3


def test_due_soon_boundary_matches_engine_window():
    """Due-soon is due_tick - tick <= 2, the derive_continuity_influence
    window: remaining 2 flags, remaining 3 does not. Probed by direct
    observe() calls so the boundary tick is exact."""
    subject = _init_store()
    tmp = _tmp()
    # due_tick 5: at tick 3 the remainder is exactly 2 -> due soon.
    _add_commitment(subject, "self", "due in five", due_tick=5,
                    commitment_id="due-five")
    # due_tick 6: at tick 3 the remainder is 3 -> not due soon.
    _add_commitment(subject, "self", "due in six", due_tick=6,
                    commitment_id="due-six")
    # No due tick at all -> never due soon.
    _add_commitment(subject, "self", "no due", commitment_id="due-none")
    tracker = GoalTracker(tmp / "goals.json")
    tracker.observe(subject, 3)
    row = tracker.rows[0]
    assert row["due_soon"] == 1, row
    # One tick later the six-tick commitment crosses into the window.
    tracker.observe(subject, 4)
    assert tracker.rows[-1]["due_soon"] == 2, tracker.rows[-1]
    # (Report-level due-soon ids are covered end-to-end below: report()
    # scores due-soon at the live tick, and direct observe() calls here
    # deliberately decouple the observation tick from the engine tick.)


def test_due_soon_end_to_end_through_run_tick():
    """A commitment due in 2 ticks (at creation) is flagged due-soon
    after one tick through the real wiring; one due in 4 is not."""
    subject = _init_store()
    soon = _add_commitment(subject, "self", "due soon", due_tick=2,
                           commitment_id="e2e-soon")
    _add_commitment(subject, "self", "due later", due_tick=4,
                    commitment_id="e2e-later")
    _tick(subject)  # tick 1: remainders 1 and 3.
    row = _sidecar_data(cli.GOALS)["rows"][0]
    assert row["due_soon"] == 1, row
    rep = report(subject, cli.GOALS)
    assert rep["commitments"]["due_soon"] == [soon], rep


# -- fitness 2: new-goal detection -------------------------------------------

def test_new_commitment_actor_not_self_is_candidate():
    """A commitment appearing between observations with actor !=
    "self" is recorded as a candidate formed goal, with actor,
    description, and first-seen tick."""
    subject = _init_store()
    _tick(subject)  # tick 1: empty population observed.
    cid = _add_commitment(subject, "tester", "call me back tomorrow",
                          commitment_id="cand-1")
    _tick(subject)  # tick 2: the new id appears.
    data = _sidecar_data(cli.GOALS)
    assert data["known"] == {
        "cand-1": {"actor": "tester",
                   "description": "call me back tomorrow",
                   "kind": "candidate", "first_tick": 2},
    }, data["known"]
    rep = report(subject, cli.GOALS)
    assert rep["formed"] == [{
        "id": "cand-1", "kind": "candidate", "actor": "tester",
        "description": "call me back tomorrow", "first_tick": 2}]
    assert rep["new_in_latest_observation"] == rep["formed"]
    # A further tick with no new commitments changes nothing.
    _tick(subject)
    data = _sidecar_data(cli.GOALS)
    assert list(data["known"]) == ["cand-1"]
    rep = report(subject, cli.GOALS)
    assert rep["new_in_latest_observation"] == [], rep
    assert len(rep["formed"]) == 1
    assert cid == "cand-1"


def test_new_commitment_actor_self_is_authored_via_cli_intend():
    """A self-authored commitment registered through the real `mind
    intend` path is recorded as authored, never as a candidate."""
    subject = _init_store()
    _tick(subject)  # tick 1.
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_intend(argparse.Namespace(description="finish the report",
                                          due=None, importance=0.5))
    assert "intended" in buf.getvalue(), buf.getvalue()
    subject = cli._subject()  # re-read: cmd_intend built its own subject.
    _tick(subject)  # tick 2.
    data = _sidecar_data(cli.GOALS)
    assert len(data["known"]) == 1
    entry = next(iter(data["known"].values()))
    assert entry["kind"] == "authored", entry
    assert entry["actor"] == "self"
    assert entry["description"] == "finish the report"
    assert entry["first_tick"] == 2


def test_commitment_present_before_first_observation_is_recorded():
    """An id the sidecar has never seen is new to the sidecar even if
    it predates the instrument: the first observation records it."""
    subject = _init_store()
    _add_commitment(subject, "world", "the garden needs water",
                    commitment_id="pre-1")
    _tick(subject)
    data = _sidecar_data(cli.GOALS)
    assert data["known"]["pre-1"]["kind"] == "candidate"
    assert data["known"]["pre-1"]["first_tick"] == 1


# -- fitness 3: tension span ---------------------------------------------------

def test_tension_span_k_ticks_then_reset_keeps_longest():
    """An open goal held across 3 observed ticks reports span 3;
    closing all goals resets the current span to 0 while the longest
    span is retained."""
    subject = _init_store()
    _tick(subject)  # tick 1: zero open, current stays 0.
    cid = _add_commitment(subject, "self", "hold the tension",
                          commitment_id="tension-1")
    _tick(subject)  # tick 2: current 1.
    _tick(subject)  # tick 3: current 2.
    _tick(subject)  # tick 4: current 3.
    data = _sidecar_data(cli.GOALS)
    assert data["tension"] == {"current": 3, "longest": 3}, data["tension"]
    assert [r["open"] for r in data["rows"]] == [0, 1, 1, 1]
    _resolve_commitment(subject, cid, kept=True)
    _tick(subject)  # tick 5: zero open, current resets.
    data = _sidecar_data(cli.GOALS)
    assert data["tension"] == {"current": 0, "longest": 3}, data["tension"]
    assert data["rows"][-1]["open"] == 0
    rep = report(subject, cli.GOALS)
    assert rep["tension"] == {"current": 0, "longest": 3}
    # A new goal starts a fresh run; the longest is only beaten, never reset.
    _add_commitment(subject, "self", "a second run", commitment_id="tension-2")
    _tick(subject)  # tick 6: current 1.
    data = _sidecar_data(cli.GOALS)
    assert data["tension"] == {"current": 1, "longest": 3}, data["tension"]


def test_tension_span_needs_an_open_commitment_every_tick():
    """Two open ticks, one zero-open tick, one open tick: the run
    breaks at the zero and restarts at 1; longest stays 2."""
    tmp = _tmp()
    subject = _init_store()
    cid = _add_commitment(subject, "self", "intermittent",
                          commitment_id="span-1")
    tracker = GoalTracker(tmp / "goals.json")
    tracker.observe(subject, 1)
    tracker.observe(subject, 2)
    assert tracker.tension == {"current": 2, "longest": 2}
    _resolve_commitment(subject, cid, kept=True)
    tracker.observe(subject, 3)
    assert tracker.tension == {"current": 0, "longest": 2}
    _add_commitment(subject, "self", "again", commitment_id="span-2")
    tracker.observe(subject, 4)
    assert tracker.tension == {"current": 1, "longest": 2}


# -- fitness 4: read-only sweep --------------------------------------------------

def test_readonly_sweep_leaves_sidecar_byte_identical():
    """drift, status, review, inbox, goals, think, and a dream run
    never write the sidecar."""
    subject = _init_store()
    for _ in range(3):
        _tick(subject)
    sidecar = cli.GOALS
    assert sidecar.exists()
    before = sidecar.read_bytes()
    cli.cmd_drift(argparse.Namespace(window=10))
    cli.cmd_status(argparse.Namespace(raw=False))
    cli.cmd_review(argparse.Namespace(n=5))
    cli.cmd_inbox(argparse.Namespace(id=None))
    cli.cmd_goals(argparse.Namespace())
    cli.cmd_think(argparse.Namespace(text="a voluntary thought",
                                     inspection=False, weighed=None,
                                     discarded=None, unsure=None))
    cli.cmd_dream(argparse.Namespace(ticks=2))
    assert sidecar.read_bytes() == before, \
        "read-only command or dream tick wrote the sidecar"


def test_readonly_commands_do_not_create_sidecar_on_fresh_store():
    """On a fresh store the read-only sweep creates no goals.json at
    all — reading the report must not mint the instrument's history."""
    _init_store()
    assert not cli.GOALS.exists()
    cli.cmd_drift(argparse.Namespace(window=10))
    cli.cmd_status(argparse.Namespace(raw=False))
    cli.cmd_goals(argparse.Namespace())
    assert not cli.GOALS.exists()
    out = _goals_output()
    assert "commitments: open 0" in out, out
    assert "oldest open —" in out, out
    assert "formed goals: none detected" in out, out
    assert not cli.GOALS.exists()


# -- fitness 5: corrupt sidecar / init --force -----------------------------------

def test_corrupt_sidecar_raises_loud():
    """Invalid JSON, non-dict top level, malformed rows/known/tension
    all raise ValueError at construction — never a silent reset."""
    tmp = _tmp()
    bad = {
        "bad-json": "{not json",
        "non-dict": "[1, 2]",
        "bad-rows-type": json.dumps({"rows": {}, "known": {}, "tension": {}}),
        "bad-row-type": json.dumps({"rows": [7], "known": {}, "tension": {}}),
        "bad-row-tick": json.dumps({"rows": [_row(tick="x")], "known": {}, "tension": {}}),
        "bad-row-open": json.dumps({"rows": [_row(open=-1)], "known": {}, "tension": {}}),
        "bad-row-age": json.dumps({"rows": [_row(oldest_open_age=-2)], "known": {}, "tension": {}}),
        "bad-row-status": json.dumps({"rows": [_row(commitments={"open": 1.5, "overdue": 0, "kept": 0, "broken": 0, "released": 0})], "known": {}, "tension": {}}),
        "bad-row-missing-status": json.dumps({"rows": [_row(commitments={"open": 0})], "known": {}, "tension": {}}),
        "bad-row-due-soon": json.dumps({"rows": [_row(due_soon=True)], "known": {}, "tension": {}}),
        "bad-row-expectations": json.dumps({"rows": [_row(expectations={"pending": 0})], "known": {}, "tension": {}}),
        "bad-known-type": json.dumps({"rows": [], "known": [], "tension": {}}),
        "bad-known-kind": json.dumps({"rows": [], "known": {"x": {"actor": "self", "description": "d", "kind": "formed", "first_tick": 1}}, "tension": {}}),
        "bad-known-tick": json.dumps({"rows": [], "known": {"x": {"actor": "self", "description": "d", "kind": "authored", "first_tick": -1}}, "tension": {}}),
        "bad-tension-type": json.dumps({"rows": [], "known": {}, "tension": []}),
        "bad-tension-current": json.dumps({"rows": [], "known": {}, "tension": {"current": -1, "longest": 0}}),
        "bad-tension-longest": json.dumps({"rows": [], "known": {}, "tension": {"current": 0, "longest": "x"}}),
    }
    for name, text in bad.items():
        path = tmp / f"{name}.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt goals"):
            GoalTracker(path)


def test_corrupt_sidecar_fails_closed_before_tick_advances():
    """A corrupt sidecar raises inside _run_tick before the heartbeat
    runs: the engine tick does not advance and the file is untouched."""
    subject = _init_store()
    for _ in range(2):
        _tick(subject)
    sidecar = cli.GOALS
    before = sidecar.read_bytes()
    sidecar.write_text("{corrupt", encoding="utf-8")
    tick_before = subject.engine.state.tick
    with pytest.raises(ValueError, match="corrupt goals"):
        cli._run_tick(subject)
    assert subject.engine.state.tick == tick_before, \
        "corrupt sidecar let the tick advance before failing"
    assert sidecar.read_bytes() == b"{corrupt", \
        "failing construction must not touch the sidecar bytes"


def test_init_force_wipes_sidecar_and_corrupt_sidecar():
    """init --force wipes the sidecar; a corrupt sidecar cannot brick
    the reseed (wipe runs before construction)."""
    subject = _init_store()
    for _ in range(3):
        _tick(subject)
    assert cli.GOALS.exists()
    cli.cmd_init(argparse.Namespace(force=True))
    assert not cli.GOALS.exists(), \
        "init --force must wipe the goals sidecar (genome: reseed reset)"
    fresh = cli._subject()
    assert fresh.engine.state.tick == 0
    assert report(fresh, cli.GOALS)["observed_ticks"] == 0
    # Corrupt variant: garbage in the sidecar, same reseed path.
    cli.GOALS.write_text("{not json", encoding="utf-8")
    cli.cmd_init(argparse.Namespace(force=True))
    assert not cli.GOALS.exists(), \
        "init --force must wipe even a corrupt goals sidecar"


def test_observe_and_report_fail_closed_without_continuity():
    """A subject with no continuity state raises instead of reporting
    a silent zero (genome: silent defaults)."""
    tmp = _tmp()
    tracker = GoalTracker(tmp / "goals.json")
    with pytest.raises(ValueError, match="continuity"):
        tracker.observe(object(), 1)
    with pytest.raises(ValueError, match="continuity"):
        report(object(), tracker)
    with pytest.raises(ValueError, match="integer tick"):
        tracker.observe(_init_store(), "x")


# -- fitness 6: determinism ---------------------------------------------------------

def _run_script(root: Path, ticks: int = 12) -> bytes:
    """Run an identical scripted tick sequence on a synthetic tree at
    `root` through the canonical redirect; return the sidecar bytes."""
    from conftest import redirect_all_cli_paths
    previous = redirect_all_cli_paths(root)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        # Fixed commitment id: ids are store input, and two runs minting
        # different uuids would legitimately differ. The instrument is
        # deterministic given identical store evolution.
        _add_commitment(subject, "tester", "deterministic promise",
                        due_tick=20, commitment_id="det-commit-1")
        for _ in range(ticks):
            _tick(subject)
        return cli.GOALS.read_bytes()
    finally:
        for name, value in previous.items():
            setattr(cli, name, value)


def test_identical_scripts_yield_byte_identical_sidecar():
    """Identical scripted tick sequences -> byte-identical sidecar."""
    a = _run_script(_tmp(), ticks=12)
    b = _run_script(_tmp(), ticks=12)
    assert a == b, (a, b)
    data = json.loads(a)
    assert len(data["rows"]) == 12
    assert data["known"]["det-commit-1"]["kind"] == "candidate"
    assert data["tension"] == {"current": 12, "longest": 12}


def test_save_load_roundtrip_deterministic():
    """Save twice -> identical bytes; reload -> identical data."""
    tmp = _tmp()
    path = tmp / "goals.json"
    subject = _init_store()
    _add_commitment(subject, "self", "roundtrip", commitment_id="rt-1")
    tracker = GoalTracker(path)
    for i in range(5):
        tracker.observe(subject, i + 1)
    tracker.save()
    first = path.read_bytes()
    tracker.save()
    assert path.read_bytes() == first
    reloaded = GoalTracker(path)
    assert reloaded.rows == tracker.rows
    assert reloaded.known == tracker.known
    assert reloaded.tension == tracker.tension
    reloaded.save()
    assert path.read_bytes() == first


# -- fitness 7: report purity and tallies -------------------------------------------

def test_report_is_pure_read_twice_equal_sidecar_untouched():
    """report() called twice — via tracker and via path — returns
    equal dicts and leaves the sidecar byte-identical."""
    subject = _init_store()
    _add_commitment(subject, "tester", "pure read", commitment_id="pure-1")
    _add_expectation(subject, "pure expectation", expectation_id="pure-exp-1")
    for _ in range(2):
        _tick(subject)
    before = cli.GOALS.read_bytes()
    rep1 = report(subject, GoalTracker(cli.GOALS))
    rep2 = report(subject, cli.GOALS)
    assert rep1 == rep2
    assert cli.GOALS.read_bytes() == before
    assert rep1["observed_ticks"] == 2
    assert rep1["latest_observation_tick"] == 2
    assert rep1["formed"][0]["first_tick"] == 1


def test_expectation_outcome_tallies_exact():
    """Pending/confirmed/violated/expired tallies match live state."""
    subject = _init_store()
    _add_expectation(subject, "pending", expectation_id="tally-pending")
    confirmed = _add_expectation(subject, "confirmed",
                                 expectation_id="tally-confirmed")
    violated = _add_expectation(subject, "violated",
                                expectation_id="tally-violated")
    _add_expectation(subject, "expired", due_tick=0,
                     expectation_id="tally-expired")
    _resolve_expectation(subject, confirmed, confirmed=True)
    _resolve_expectation(subject, violated, confirmed=False)
    _tick(subject)  # flips the due_tick=0 expectation to expired.
    rep = report(subject, cli.GOALS)
    assert rep["expectations"]["by_status"] == {
        "pending": 1, "confirmed": 1, "violated": 1, "expired": 1}, rep
    assert rep["expectations"]["outcomes"] == {
        "confirmed": 1, "violated": 1, "expired": 1}, rep
    assert rep["expectations"]["open"] == 1


# -- fitness 8: row cap -------------------------------------------------------------

def test_row_cap_holds_100_oldest_dropped():
    """After 100+ observations the sidecar holds exactly 100 rows
    (oldest dropped) while known-goal and tension history survive."""
    tmp = _tmp()
    subject = _init_store()
    _add_commitment(subject, "self", "cap holder", commitment_id="cap-1")
    tracker = GoalTracker(tmp / "goals.json")
    for i in range(105):
        tracker.observe(subject, i + 1)
    assert len(tracker.rows) == ROW_CAP == 100
    assert tracker.rows[0]["tick"] == 6, tracker.rows[0]
    assert tracker.rows[-1]["tick"] == 105
    assert tracker.known["cap-1"]["kind"] == "authored"
    assert tracker.tension == {"current": 105, "longest": 105}
    # Persisted form agrees: reload and re-check.
    tracker.save()
    reloaded = GoalTracker(tmp / "goals.json")
    assert len(reloaded.rows) == 100
    assert reloaded.rows[0]["tick"] == 6
    assert reloaded.tension == {"current": 105, "longest": 105}


# -- CLI output ---------------------------------------------------------------------

def test_mind_goals_output_shows_counts_and_formed():
    """`mind goals` prints exact counts, oldest-open age, tension,
    and the detected formed goals."""
    subject = _init_store()
    _add_commitment(subject, "tester", "call the clinic",
                    commitment_id="out-1")
    _tick(subject)
    out = _goals_output()
    assert "goals (tick 1, 1 observed ticks) — read-only" in out, out
    assert "commitments: open 1" in out, out
    assert "oldest open 1 ticks" in out, out
    assert "due soon: none" in out, out
    assert "expectations: open 0" in out, out
    assert "tension: current 1 tick(s), longest 1 tick(s)" in out, out
    assert "formed goals (1 detected):" in out, out
    assert "[candidate] actor=tester" in out, out
    assert "call the clinic" in out, out


def test_drift_goals_section_reports_live_counts():
    """The `mind drift` goals section shows open counts, oldest-open
    age, and the tension span, and reads nothing into the sidecar."""
    _init_store()
    out = _drift_output()
    assert ("goals: open commitments 0 | oldest open — | tension 0 "
            "(longest 0) | open expectations 0") in out, out
    assert not cli.GOALS.exists()
    subject = cli._subject()
    _add_commitment(subject, "self", "drift section",
                    commitment_id="drift-1")
    _tick(subject)
    out = _drift_output()
    assert ("goals: open commitments 1 | oldest open 1 ticks | tension 1 "
            "(longest 1) | open expectations 0") in out, out


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
