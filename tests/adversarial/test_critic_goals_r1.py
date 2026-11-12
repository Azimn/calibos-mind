"""Critic round 1: goal-structure instrument (Domain 17).

Spec: research/goal-structure-instrument-spec-2026-11-10.md
Diff under attack: calibos_mind/goals.py (new), calibos_mind/cli.py
(`mind goals`, drift goals section, _run_tick feed, init --force wipe),
tests/test_goals.py (24 tests), tests/conftest.py (GOALS in REDIRECTED /
_FILENAMES), 2026-11-10 CHANGELOG entry.

Every objection here is a failing test or a concretely violated
invariant — prose-only critique is rejected by the loop. All fixtures
on /tmp; the live store is never touched (conftest autouse redirect).

Attack plan:
  A. Fitness re-verification, independently of the builder's tests:
     exact counts across mixed statuses (incl. 3 open), oldest-open
     age with the oldest commitment overdue, due-soon cross-checked
     against the frozen engine's own derive_continuity_influence
     (not just the module's own formula), candidate formation through
     the real promise_made event path, tension counting overdue as
     open, the 100-row cap boundary, determinism on a mixed-population
     replay, report never backfilling `known` before any observation.
  B. Read-only / isolation: full sweep with a live population
     byte-identical, dream ticks byte-identical with the store tick
     frozen, `mind goals` twice equal.
  C. Fail-closed: corrupt sidecar fails `mind goals` and `mind drift`
     loudly (no silent-zero report), a failed observe() leaves the
     tracker untouched, reseed does not resurrect the tension span.
  D. Regression genome, item by item: shadowing definitions (AST
     count), sidecar reset on reseed with the complete path set
     (sidecar-guard tests invoked directly), read-only violations,
     silent defaults (no-continuity subject raises), dream/conduct
     isolation, CLI-construction mtime scope (byte-identity of the
     sidecar only, per the genome — the DB payload rewrite on
     _subject() construction is the known, scoped-out behavior).
     Negation/regex genome items: not applicable — goals.py has no
     text-similarity math and no regex. Missing-section defaults
     mirror personality.py exactly (verified by reading it), so they
     are checked-and-mirrored, not attacked.
  E. FAIL: cross-field row inconsistency loads silently. A row is
     the module's own schema; save() can never produce open !=
     open+overdue, due_soon > open, or open_expectations != pending.
     Such a row is a malformed rows section and the spec's corrupt
     clause ("a malformed rows/known/tension section ... raises
     ValueError") covers it. Loading it silently lets a tampered or
     half-written sidecar flow through as data.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_subject.continuity_influence import derive_continuity_influence
from digital_subject.models import Event

import calibos_mind.cli as cli
from calibos_mind.goals import ROW_CAP, GoalTracker, report


# -- fixtures (independent copies of the builder's harness) -----------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-goals-r1-"))


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


def _add_expectation(subject, proposition, due_tick=None, confidence=0.5,
                     expectation_id=None):
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


# -- A. fitness re-verification ----------------------------------------------

def test_critic_three_open_mixed_statuses_exact():
    """3 open (2 open + 1 overdue), 1 kept, 1 broken, 1 released, 4
    expectations one per status: every count exact, open = 3, oldest
    age exact, overdue contributes no due-soon."""
    subject = _init_store()
    _add_commitment(subject, "self", "open a", commitment_id="c-open-a")
    _add_commitment(subject, "self", "open b", commitment_id="c-open-b")
    _add_commitment(subject, "self", "overdue", due_tick=0,
                    commitment_id="c-overdue")
    kept = _add_commitment(subject, "self", "kept", commitment_id="c-kept")
    broken = _add_commitment(subject, "self", "broken",
                             commitment_id="c-broken")
    released = _add_commitment(subject, "self", "released",
                               commitment_id="c-released")
    _resolve_commitment(subject, kept, kept=True)
    _resolve_commitment(subject, broken, kept=False)
    _release_commitment(subject, released)
    _add_expectation(subject, "pending", expectation_id="e-pending")
    confirmed = _add_expectation(subject, "confirmed",
                                 expectation_id="e-confirmed")
    violated = _add_expectation(subject, "violated",
                                expectation_id="e-violated")
    _add_expectation(subject, "expired", due_tick=0,
                     expectation_id="e-expired")
    _resolve_expectation(subject, confirmed, confirmed=True)
    _resolve_expectation(subject, violated, confirmed=False)
    _tick(subject)
    row = _sidecar_data(cli.GOALS)["rows"][0]
    assert row["commitments"] == {"open": 2, "overdue": 1, "kept": 1,
                                  "broken": 1, "released": 1}, row
    assert row["open"] == 3, row
    assert row["oldest_open_age"] == 1, row
    assert row["due_soon"] == 0, row
    assert row["expectations"] == {"pending": 1, "confirmed": 1,
                                   "violated": 1, "expired": 1}, row
    assert row["open_expectations"] == 1
    rep = report(subject, cli.GOALS)
    assert rep["commitments"]["open"] == 3
    assert rep["commitments"]["oldest_open_age"] == 1
    assert rep["commitments"]["due_soon"] == []


def test_critic_oldest_age_tracks_oldest_overdue():
    """The oldest commitment flips to overdue after its deadline; the
    oldest-open age must keep tracking it (overdue counts as open)."""
    subject = _init_store()
    _add_commitment(subject, "self", "oldest", due_tick=0,
                    commitment_id="age-old")
    _tick(subject)  # tick 1: flips to overdue, age 1.
    _add_commitment(subject, "self", "newer", commitment_id="age-new")
    _tick(subject)  # tick 2.
    _tick(subject)  # tick 3.
    rows = _sidecar_data(cli.GOALS)["rows"]
    assert rows[0]["commitments"]["overdue"] == 1, rows[0]
    assert [r["oldest_open_age"] for r in rows] == [1, 2, 3], rows
    assert [r["open"] for r in rows] == [1, 2, 2], rows


def test_critic_overdue_keeps_tension_running():
    """Tension is a run of ticks with >= 1 open commitment, and the
    engine's open set includes overdue: a lone overdue commitment
    must keep the run alive, not reset it."""
    subject = _init_store()
    _add_commitment(subject, "self", "past due", due_tick=0,
                    commitment_id="t-overdue")
    for _ in range(3):
        _tick(subject)
    data = _sidecar_data(cli.GOALS)
    assert data["tension"] == {"current": 3, "longest": 3}, data["tension"]
    assert [r["open"] for r in data["rows"]] == [1, 1, 1]


def test_critic_due_soon_matches_engine_window():
    """Due-soon must agree with the frozen engine's own
    derive_continuity_influence, measured on the same store state —
    not just with the module's own restatement of the formula.
    Remainder 2: engine raises commitment_due_soon AND goals flags
    it. Remainder 3: neither does. (Measured premise, 2026-11-11:
    report() scores due-soon at the LIVE engine tick, so the
    report-level check below uses live-tick remainders, while the
    observe-level check uses the observation tick.)"""
    for due_tick, tick, expect_due in ((5, 3, True), (6, 3, False)):
        # Fresh store per case: re-init with --force on the same
        # redirected tree (a second force=False init would refuse).
        cli.cmd_init(argparse.Namespace(force=True))
        subject = cli._subject()
        cid = _add_commitment(subject, "tester", "due probe",
                              due_tick=due_tick, commitment_id=f"ds-{due_tick}")
        event = Event(kind="observation", source="tester",
                      description="checking in", tags=("t",),
                      intensity=0.5, valence=0.0)
        influence = derive_continuity_influence(
            subject.continuity, event, tick=tick)
        assert ("commitment_due_soon" in influence.reasons) == expect_due, \
            (due_tick, tick, influence.reasons)
        tmp = _tmp()
        tracker = GoalTracker(tmp / "goals.json")
        tracker.observe(subject, tick)
        assert (tracker.rows[0]["due_soon"] == 1) == expect_due, \
            tracker.rows[0]
    # Report level: live engine tick is 0 on a fresh store, so a
    # commitment due at tick 2 (remainder 2) is due soon in the
    # report, while one due at tick 3 (remainder 3) is not.
    cli.cmd_init(argparse.Namespace(force=True))
    subject = cli._subject()
    soon = _add_commitment(subject, "self", "due in two", due_tick=2,
                           commitment_id="rep-soon")
    _add_commitment(subject, "self", "due in three", due_tick=3,
                    commitment_id="rep-later")
    rep = report(subject, GoalTracker(_tmp() / "goals.json"))
    assert rep["tick"] == 0, rep
    assert rep["commitments"]["due_soon"] == [soon], rep


def test_critic_due_soon_ids_sorted():
    """Two due-soon commitments: report lists ids sorted, so output
    is deterministic regardless of store iteration order."""
    subject = _init_store()
    _add_commitment(subject, "self", "zeta", due_tick=2,
                    commitment_id="zz-first")
    _add_commitment(subject, "self", "alpha", due_tick=2,
                    commitment_id="aa-second")
    _tick(subject)
    rep = report(subject, cli.GOALS)
    assert rep["commitments"]["due_soon"] == ["aa-second", "zz-first"], rep


def test_critic_promise_made_event_forms_candidate():
    """The formation path the spec's rationale depends on: the frozen
    engine mints a non-self commitment from a promise_made event
    (continuity.py _apply_event_conventions). Drive one through the
    real continuity.observe, tick, and the instrument must record it
    as a candidate with actor == the event source."""
    subject = _init_store()
    with subject._transaction():
        subject.continuity.observe(
            Event(kind="promise_made", source="tester",
                  description="bring the book on Thursday",
                  tags=("promise",), intensity=0.6, valence=0.0),
            tick=subject.engine.state.tick,
            interpretation="a promise was made")
    created = [c for c in subject.continuity.state.commitments.values()
               if c.actor == "tester"]
    assert len(created) == 1, "engine did not mint the commitment"
    assert created[0].description == "bring the book on Thursday"
    _tick(subject)
    data = _sidecar_data(cli.GOALS)
    assert len(data["known"]) == 1
    entry = next(iter(data["known"].values()))
    assert entry == {"actor": "tester",
                     "description": "bring the book on Thursday",
                     "kind": "candidate", "first_tick": 1}, entry
    rep = report(subject, cli.GOALS)
    assert rep["formed"][0]["kind"] == "candidate"
    assert rep["formed"][0]["actor"] == "tester"


def test_critic_intend_authored_and_promise_candidate_together():
    """Authored (real `mind intend` path) and candidate (promise_made
    event) goals coexist in one store; the report keeps the kinds
    apart and orders by first-seen tick."""
    subject = _init_store()
    _tick(subject)  # tick 1: empty observation.
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_intend(argparse.Namespace(description="finish the report",
                                          due=None, importance=0.5))
    assert "intended" in buf.getvalue()
    subject = cli._subject()
    with subject._transaction():
        subject.continuity.observe(
            Event(kind="promise_made", source="world",
                  description="the roof will be fixed",
                  tags=("promise",), intensity=0.5, valence=0.0),
            tick=subject.engine.state.tick,
            interpretation="a promise was made")
    _tick(subject)  # tick 2: both appear.
    rep = report(subject, cli.GOALS)
    assert len(rep["formed"]) == 2, rep["formed"]
    kinds = {g["description"]: g["kind"] for g in rep["formed"]}
    assert kinds == {"finish the report": "authored",
                     "the roof will be fixed": "candidate"}, kinds
    assert all(g["first_tick"] == 2 for g in rep["formed"])
    assert rep["new_in_latest_observation"] == rep["formed"]


def test_critic_row_cap_exact_boundary():
    """Exactly 100 observations hold ticks 1..100; the 101st drops
    tick 1 only; known-goal and tension history survive the cap."""
    tmp = _tmp()
    subject = _init_store()
    _add_commitment(subject, "self", "cap holder", commitment_id="cap-1")
    tracker = GoalTracker(tmp / "goals.json")
    for i in range(100):
        tracker.observe(subject, i + 1)
    assert len(tracker.rows) == ROW_CAP == 100
    assert tracker.rows[0]["tick"] == 1
    assert tracker.rows[-1]["tick"] == 100
    tracker.observe(subject, 101)
    assert len(tracker.rows) == 100
    assert tracker.rows[0]["tick"] == 2, tracker.rows[0]
    assert tracker.rows[-1]["tick"] == 101
    assert tracker.known["cap-1"]["kind"] == "authored"
    assert tracker.tension == {"current": 101, "longest": 101}
    tracker.save()
    reloaded = GoalTracker(tmp / "goals.json")
    assert len(reloaded.rows) == 100
    assert reloaded.rows[0]["tick"] == 2
    assert reloaded.tension == {"current": 101, "longest": 101}


def _run_script(root: Path, ticks: int = 8) -> bytes:
    """Identical scripted mixed-population sequence on a synthetic
    tree; return the sidecar bytes."""
    from conftest import redirect_all_cli_paths
    previous = redirect_all_cli_paths(root)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        _add_commitment(subject, "tester", "deterministic promise",
                        due_tick=20, commitment_id="det-commit-1")
        _add_commitment(subject, "self", "will keep",
                        commitment_id="det-keep")
        _add_expectation(subject, "will confirm",
                         expectation_id="det-exp")
        _resolve_commitment(subject, "det-keep", kept=True)
        for _ in range(ticks):
            _tick(subject)
        return cli.GOALS.read_bytes()
    finally:
        for name, value in previous.items():
            setattr(cli, name, value)


def test_critic_determinism_replay_mixed_population():
    """Identical mixed-population scripts -> byte-identical sidecars."""
    a = _run_script(_tmp(), ticks=8)
    b = _run_script(_tmp(), ticks=8)
    assert a == b, (a, b)
    data = json.loads(a)
    assert len(data["rows"]) == 8
    assert data["known"]["det-commit-1"]["kind"] == "candidate"
    assert data["known"]["det-keep"]["kind"] == "authored"
    assert data["tension"] == {"current": 8, "longest": 8}


# -- B. read-only / isolation ------------------------------------------------

def test_critic_report_never_backfills_known():
    """A commitment present before any observation is live state, not
    instrument history: `mind goals` must show it open, must NOT
    create goals.json, and must list zero formed goals — until a tick
    observes it."""
    subject = _init_store()
    _add_commitment(subject, "tester", "unobserved promise",
                    commitment_id="unobs-1")
    _add_expectation(subject, "unobserved expectation",
                     expectation_id="unobs-exp")
    assert not cli.GOALS.exists()
    out = _goals_output()
    assert "commitments: open 1" in out, out
    assert "expectations: open 1" in out, out
    assert "formed goals: none detected" in out, out
    assert not cli.GOALS.exists(), \
        "report() minted the sidecar it must only read"
    rep = report(subject, cli.GOALS)
    assert rep["formed"] == []
    assert rep["observed_ticks"] == 0
    assert not cli.GOALS.exists()
    _tick(subject)
    rep = report(subject, cli.GOALS)
    assert len(rep["formed"]) == 1
    assert rep["formed"][0]["first_tick"] == 1


def test_critic_readonly_sweep_with_live_population_byte_identical():
    """Full read-only sweep over a live goal population — drift,
    status, review, inbox, goals (twice, outputs equal), think, and
    a dream run — leaves goals.json byte-identical."""
    subject = _init_store()
    _add_commitment(subject, "tester", "live promise",
                    commitment_id="live-1")
    _add_expectation(subject, "live expectation",
                     expectation_id="live-exp")
    for _ in range(2):
        _tick(subject)
    sidecar = cli.GOALS
    before = sidecar.read_bytes()
    tick_before = cli._subject().engine.state.tick
    cli.cmd_drift(argparse.Namespace(window=10))
    cli.cmd_status(argparse.Namespace(raw=False))
    cli.cmd_review(argparse.Namespace(n=5))
    cli.cmd_inbox(argparse.Namespace(id=None))
    out1 = _goals_output()
    out2 = _goals_output()
    assert out1 == out2, "read-only report is not stable across calls"
    cli.cmd_think(argparse.Namespace(text="a voluntary thought",
                                     inspection=False, weighed=None,
                                     discarded=None, unsure=None))
    cli.cmd_dream(argparse.Namespace(ticks=2))
    assert sidecar.read_bytes() == before, \
        "read-only command or dream tick wrote the sidecar"
    tick_after = cli._subject().engine.state.tick
    assert tick_after == tick_before, \
        "dream ticks advanced the store tick (sleep isolation broken)"
    data = _sidecar_data(sidecar)
    assert data["tension"] == {"current": 2, "longest": 2}, data["tension"]


# -- C. fail-closed ------------------------------------------------------------

def test_critic_corrupt_sidecar_fails_goals_and_drift_loud():
    """A corrupt sidecar must fail `mind goals` and `mind drift`
    loudly — never a silent-zero report pretending the population
    is empty."""
    _init_store()
    _tick(subject := cli._subject())
    assert cli.GOALS.exists()
    for payload in ("{corrupt",
                    json.dumps({"rows": [], "known": {},
                                "tension": {"current": "x", "longest": 0}})):
        cli.GOALS.write_text(payload, encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt goals"):
            cli.cmd_goals(argparse.Namespace())
        with pytest.raises(ValueError, match="corrupt goals"):
            cli.cmd_drift(argparse.Namespace(window=10))


def test_critic_failed_observe_leaves_tracker_untouched():
    """A commitment status the engine cannot produce raises inside
    observe() BEFORE any mutation: rows, known, and tension stay
    exactly as they were (genome: fail closed, never half-write)."""
    tmp = _tmp()
    subject = _init_store()
    cid = _add_commitment(subject, "self", "will corrupt",
                          commitment_id="bad-status-1")
    tracker = GoalTracker(tmp / "goals.json")
    with subject._transaction():
        subject.continuity.state.commitments[cid].status = "bogus"
    with pytest.raises(ValueError, match="unknown commitment status"):
        tracker.observe(subject, 1)
    assert tracker.rows == []
    assert tracker.known == {}
    assert tracker.tension == {"current": 0, "longest": 0}
    assert not (tmp / "goals.json").exists()
    with pytest.raises(ValueError, match="unknown commitment status"):
        report(subject, tracker)


def test_critic_reseed_does_not_resurrect_tension():
    """Genome: sidecar/state reset on reseed. A longest span of 3
    must not survive init --force: after reseed, one open tick
    reports current 1 / longest 1, not a resurrected 3."""
    subject = _init_store()
    cid = _add_commitment(subject, "self", "hold", commitment_id="rs-1")
    for _ in range(3):
        _tick(subject)
    assert _sidecar_data(cli.GOALS)["tension"] == {"current": 3,
                                                   "longest": 3}
    cli.cmd_init(argparse.Namespace(force=True))
    assert not cli.GOALS.exists()
    fresh = cli._subject()
    _add_commitment(fresh, "self", "fresh hold", commitment_id="rs-2")
    _tick(fresh)
    data = _sidecar_data(cli.GOALS)
    assert data["tension"] == {"current": 1, "longest": 1}, data["tension"]
    assert len(data["rows"]) == 1
    assert data["known"] == {
        "rs-2": {"actor": "self", "description": "fresh hold",
                 "kind": "authored", "first_tick": 1}}, data["known"]


# -- D. regression genome ------------------------------------------------------

def test_critic_no_shadowed_definitions():
    """Genome: shadowing definitions. Exactly one cmd_goals in cli.py,
    exactly one GoalTracker / report / observe in goals.py — a
    duplicate definition silently winning would drop behavior."""
    cli_tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    goals_tree = ast.parse(
        (Path(cli.__file__).parent / "goals.py").read_text(encoding="utf-8"))
    cli_defs = [n.name for n in ast.walk(cli_tree)
                if isinstance(n, ast.FunctionDef)]
    goals_defs = [n.name for n in ast.walk(goals_tree)
                  if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert cli_defs.count("cmd_goals") == 1, cli_defs
    assert goals_defs.count("GoalTracker") == 1, goals_defs
    assert goals_defs.count("report") == 1, goals_defs
    assert goals_defs.count("observe") == 1, goals_defs
    assert goals_defs.count("_live_counts") == 1, goals_defs


def test_critic_sidecar_guard_covers_goals_with_complete_set():
    """Genome: sidecar/state reset on reseed with the COMPLETE path
    set. GOALS is in conftest REDIRECTED and _FILENAMES, and the
    structural guard tests themselves pass (they fail if any cli
    store path escapes the redirect set)."""
    from conftest import _FILENAMES, REDIRECTED
    import test_sidecar_guard as guard
    assert "GOALS" in REDIRECTED
    assert _FILENAMES["GOALS"] == "goals.json"
    guard.test_redirect_set_covers_every_store_path()
    guard.test_no_store_paths_outside_cli()


def test_critic_observe_and_report_fail_closed_without_continuity():
    """Genome: silent defaults. A subject with no continuity state
    raises instead of reporting a quiet zero."""
    tmp = _tmp()
    tracker = GoalTracker(tmp / "goals.json")
    with pytest.raises(ValueError, match="continuity"):
        tracker.observe(object(), 1)
    with pytest.raises(ValueError, match="continuity"):
        report(object(), tracker)


# -- E. FAIL: cross-field row inconsistency loads silently --------------------

def test_critic_cross_field_row_inconsistency_rejected():
    """FAIL: a row violating the module's own save-invariants loads
    silently. save() can never produce open != open+overdue,
    due_soon > open, or open_expectations != pending — such a row is
    a malformed rows section under the spec's corrupt clause, and a
    tampered or half-written sidecar must fail closed, not flow
    through as data."""
    tmp = _tmp()
    bad_rows = {
        # open says 5 while every status count says 0.
        "open_mismatch": _row(open=5, tick=1),
        # due_soon 3 with zero open commitments.
        "due_soon_exceeds_open": _row(open=0, due_soon=3, tick=1),
        # open_expectations 2 while pending is 0.
        "open_expectations_mismatch": _row(
            open_expectations=2, expectations={"pending": 0, "confirmed": 0,
                                               "violated": 0, "expired": 0},
            tick=1),
        # negative oldest-open age is already rejected; a due-soon
        # count on a row whose commitments say none are open is the
        # same class of self-contradiction.
        "due_soon_without_open_status": _row(
            open=0, due_soon=1,
            commitments={"open": 0, "overdue": 0, "kept": 1, "broken": 0,
                         "released": 0},
            tick=1),
    }
    for name, row in bad_rows.items():
        path = tmp / f"{name}.json"
        _write_sidecar(path, rows=[row])
        with pytest.raises(ValueError, match="corrupt goals"):
            GoalTracker(path)
