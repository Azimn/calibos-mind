"""Critic round 1 — loss ledger (Domain 31, 2026-11-24).

Spec: research/specs/2026-11-24-loss-ledger.md
Brief: spec + diff only. Every objection here arrives as a failing test
or a concretely violated invariant; prose-only critique is rejected.

Attack surface covered:
  1. Regression genome, item by item (shadowing, reseed reset,
     read-only violations, silent defaults / quiet zeros, dream
     isolation, silent zero on missing sections, CLI-construction
     mtime scoping, no duplicate definitions).
  2. Fitness-function boundary re-verification independent of the
     builder's own tests (report ordering across kinds and ties,
     re-observation of the same tick, steady-state no-op discipline,
     fail-loud on corrupt/empty sidecars from the CLI path, save
     round-trip determinism, exact strength rendering).
  3. Backup visibility: a new sidecar with no .gitignore decision is
     silently published by the daily backup (`git add -A`), which
     contradicts the per-sidecar local-only pattern every sibling
     tracker follows (followthrough.json, provenance.json,
     counterfactuals.json, ...). This is the one FAILING test.

All fixtures on /tmp — the live store is never touched (conftest
autouse redirect).
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind import counterfactual, stakes as stakes_mod
from calibos_mind.cli import _fmt_strength
from calibos_mind.stakes import StakesTracker, report
from digital_subject.continuity import Commitment
from digital_subject.models import Action, Habit


# -- helpers (mirroring tests/test_stakes.py scenario control) -------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-stakes-"))


def _fake_subject(habits=None, commitments=None, tick=0):
    return SimpleNamespace(
        engine=SimpleNamespace(state=SimpleNamespace(
            tick=tick, habits=dict(habits or {}))),
        continuity=SimpleNamespace(state=SimpleNamespace(
            commitments=dict(commitments or {}))))


def _habit(key, strength, trigger="curiosity"):
    return Habit(key, trigger, Action.WITHDRAW, strength, 0, 0)


def _commitment(cid, status="open", description="do the thing",
                resolved_tick=None, outcome=None, due_tick=None):
    return Commitment(
        id=cid, actor="self", description=description, beneficiary="self",
        created_tick=0, due_tick=due_tick, importance=0.5,
        evidence_ids=(), status=status, outcome=outcome,
        resolved_tick=resolved_tick)


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


def _add_commitment(subject, description, due_tick=None):
    with subject._transaction():
        item = subject.continuity.create_commitment(
            "self", description, beneficiary="self",
            tick=subject.engine.state.tick, due_tick=due_tick,
            importance=0.5)
    return item.id


def _resolve_commitment(subject, cid, kept=True, outcome="done"):
    with subject._transaction():
        subject.continuity.resolve_commitment(
            cid, outcome=outcome, kept=kept,
            tick=subject.engine.state.tick)


# -- genome: shadowing definitions ------------------------------------------

def test_threshold_is_the_single_source_not_a_redefinition():
    """HABIT_FIRE_STRENGTH must be the counterfactual object, not a
    second literal 0.65 (genome: duplicate definitions silently win)."""
    assert stakes_mod.HABIT_FIRE_STRENGTH is \
        counterfactual.HABIT_FIRE_STRENGTH
    assert stakes_mod.HABIT_FIRE_STRENGTH == 0.65
    src = Path(stakes_mod.__file__).read_text(encoding="utf-8")
    assert "HABIT_FIRE_STRENGTH =" not in src, \
        "stakes.py redefines the threshold instead of importing it"
    assert src.count("class StakesTracker") == 1
    cli_src = Path(cli.__file__).read_text(encoding="utf-8")
    assert cli_src.count("def cmd_stakes") == 1, \
        "duplicate cmd_stakes definition — the second would silently win"
    assert cli_src.count("STAKES = ") == 1


# -- genome: silent zero on missing sections ---------------------------------

def test_missing_engine_sections_fail_loud_not_quiet_zero():
    """A subject with no readable habit population or no continuity
    state is malformed, never an empty ledger (genome: silent zero on
    missing sections)."""
    tmp = _tmp()
    tracker = StakesTracker(tmp / "stakes.json")
    # No engine at all.
    with pytest.raises(ValueError):
        tracker.observe(SimpleNamespace(engine=None, continuity=None), 1)
    # Engine state with habits=None (not a dict).
    bad_habits = _fake_subject()
    bad_habits.engine.state.habits = None
    with pytest.raises(ValueError):
        tracker.observe(bad_habits, 1)
    # No continuity state.
    bad_cont = _fake_subject(habits={})
    bad_cont.continuity = SimpleNamespace(state=None)
    with pytest.raises(ValueError):
        tracker.observe(bad_cont, 1)
    # report() on a subject with no continuity state likewise.
    with pytest.raises(ValueError):
        report(bad_cont, tracker)


# -- genome: read-only violations, extended sweep ----------------------------

def test_extended_readonly_sweep_leaves_sidecar_identical():
    """Builder covered stakes/drift/status/goals. The remaining
    read-only reporters (predictions, followthrough) must also leave
    stakes.json byte-identical, as must a second `mind stakes`."""
    subject = _init_store()
    key = "formed:fear:withdraw"
    for _ in range(16):
        _script_tick(subject, _needs(energy=0.20),
                     _pressures(fear=0.95, trust=0.5), _baselines(fear=0.0))
    assert subject.engine.state.habits[key].strength == 0.70
    assert Path(cli.STAKES).exists()
    before = Path(cli.STAKES).read_bytes()

    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_stakes(argparse.Namespace())
        cli.cmd_predictions(argparse.Namespace())
        cli.cmd_followthrough(argparse.Namespace())
        cli.cmd_stakes(argparse.Namespace())
    assert Path(cli.STAKES).read_bytes() == before
    assert "no losses recorded" in buf.getvalue()


# -- genome: dream/conduct isolation, commitment path ------------------------

def test_dream_ticks_do_not_mint_pending_breakage():
    """A commitment broken between observations, then dream ticks,
    then one waking tick: exactly one row, named with the engine's
    resolving tick — and the sidecar is byte-identical across the
    dreams (builder's dream test covered only the habit path)."""
    subject = _init_store()
    cid = _add_commitment(subject, "break this in a dream")
    _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                 _baselines())
    _resolve_commitment(subject, cid, kept=False, outcome="missed it")
    tick_before = subject.engine.state.tick
    before = (Path(cli.STAKES).read_bytes()
              if Path(cli.STAKES).exists() else None)
    for _ in range(3):
        subject.dream_tick()
    after_dreams = (Path(cli.STAKES).read_bytes()
                    if Path(cli.STAKES).exists() else None)
    assert after_dreams == before
    _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                 _baselines())
    tracker = StakesTracker(cli.STAKES)
    assert tracker.losses == [{
        "kind": "broken-commitment", "commitment_id": cid,
        "description": "break this in a dream", "tick": tick_before,
        "outcome": "missed it"}]


# -- fitness: fail-loud from the CLI path ------------------------------------

def test_stakes_command_fails_loud_on_corrupt_sidecar():
    """`mind stakes` on a corrupt sidecar must raise, never print an
    empty report (a quiet zero from the read path would hide the loss
    history)."""
    _init_store()
    Path(cli.STAKES).write_text("{corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt stakes sidecar"):
        cli.cmd_stakes(argparse.Namespace())


def test_empty_sidecar_file_fails_loud():
    """A 0-byte sidecar (crash shell, truncated write) is corrupt,
    not an empty ledger."""
    tmp = _tmp()
    path = tmp / "stakes.json"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="corrupt stakes sidecar"):
        StakesTracker(path)


# -- fitness: determinism and atomic save ------------------------------------

def test_save_reload_save_is_byte_identical_and_leaves_no_tmp():
    tmp = _tmp()
    tracker = StakesTracker(tmp / "stakes.json")
    key = "formed:curiosity:explore"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 1)
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.60)}), 2)
    tracker.observe(_fake_subject(commitments={
        "c-1": _commitment("c-1", status="broken", resolved_tick=2,
                           outcome="x")}), 3)
    tracker.save()
    first = (tmp / "stakes.json").read_bytes()
    assert not (tmp / "stakes.json.tmp").exists()
    reloaded = StakesTracker(tmp / "stakes.json")
    reloaded.save()
    assert (tmp / "stakes.json").read_bytes() == first
    assert not (tmp / "stakes.json.tmp").exists()
    # Float fidelity across the JSON round-trip: 0.65 stays 0.65.
    assert reloaded.losses[0]["before"] == 0.70
    assert reloaded.losses[0]["after"] == 0.60


# -- fitness: exact strength rendering ----------------------------------------

def test_fmt_strength_exact_on_engine_values():
    """The engine rounds strengths to 6 decimals, so a 6-decimal
    render must be exact: 0.649999 must not collapse to 0.65 (the
    loss line), and 0.65 must stay 0.65."""
    assert _fmt_strength(0.649999) == "0.649999"
    assert _fmt_strength(0.65) == "0.65"
    assert _fmt_strength(0.7) == "0.7"
    assert _fmt_strength(0.6) == "0.6"
    assert _fmt_strength(1.0) == "1"


# -- fitness: report ordering across kinds and ties ---------------------------

def test_report_ordering_mixed_kinds_and_same_tick_ties():
    """Most recent first across kinds; on a tick tie the later-minted
    row comes first (documented tie-break)."""
    tmp = _tmp()
    tracker = StakesTracker(tmp / "stakes.json")
    a = "formed:a:b"
    c = "formed:c:d"
    tracker.observe(_fake_subject(habits={a: _habit(a, 0.70)}), 1)
    tracker.observe(_fake_subject(habits={a: _habit(a, 0.60)}), 5)
    # Commitment row at the same tick 5, minted after the habit row.
    tracker.observe(_fake_subject(
        habits={a: _habit(a, 0.60)},
        commitments={"c-1": _commitment(
            "c-1", status="broken", resolved_tick=5, outcome="x")}), 5)
    tracker.observe(_fake_subject(
        habits={a: _habit(a, 0.60), c: _habit(c, 0.70)}), 6)
    tracker.observe(_fake_subject(
        habits={a: _habit(a, 0.60), c: _habit(c, 0.60)}), 9)
    tracker.observe(_fake_subject(
        habits={a: _habit(a, 0.60), c: _habit(c, 0.60)},
        commitments={"c-2": _commitment(
            "c-2", status="broken", resolved_tick=3, outcome="y")}), 9)
    rep = report(_fake_subject(
        habits={a: _habit(a, 0.60), c: _habit(c, 0.60)}, tick=9), tracker)
    assert [(r["kind"], r["tick"]) for r in rep["losses"]] == [
        ("habit-bypass", 9),
        ("broken-commitment", 5),
        ("habit-bypass", 5),
        ("broken-commitment", 3),
    ]


# -- fitness: re-observation and steady-state discipline ----------------------

def test_reobserving_same_tick_after_crossing_mints_nothing_more():
    """observe() called twice with the same tick and strengths after a
    crossing: still exactly one row, and the second call reports no
    change (no-op write discipline)."""
    tmp = _tmp()
    tracker = StakesTracker(tmp / "stakes.json")
    key = "formed:curiosity:explore"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 1)
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.60)}), 2)
    assert len(tracker.losses) == 1
    assert tracker.observe(
        _fake_subject(habits={key: _habit(key, 0.60)}), 2) is False
    assert len(tracker.losses) == 1


def test_steady_state_observation_reports_no_change():
    """A repeated identical observation moves nothing: baseline
    unchanged, no row, observe() returns False."""
    tmp = _tmp()
    tracker = StakesTracker(tmp / "stakes.json")
    key = "formed:curiosity:explore"
    assert tracker.observe(
        _fake_subject(habits={key: _habit(key, 0.70)}), 1) is True
    assert tracker.observe(
        _fake_subject(habits={key: _habit(key, 0.70)}), 2) is False
    assert tracker.losses == []


# -- spec-gray, confirmed as documented (not a violation) ---------------------

def test_first_seen_broken_mints_one_row_with_engine_tick():
    """Confirms the builder's documented reading: a commitment first
    seen already broken mints one row naming the engine's resolving
    tick, never an invented one. The spec states the no-backfill rule
    for habits only; the CHANGELOG records this as a store-documented
    event. Flagged for the design lead's confirmation, not failed:
    the revert signal does not cover it."""
    tmp = _tmp()
    tracker = StakesTracker(tmp / "stakes.json")
    broken = _commitment("c-9", status="broken", resolved_tick=4,
                         outcome="never started")
    assert tracker.observe(
        _fake_subject(commitments={"c-9": broken}), 7) is True
    assert tracker.losses == [{
        "kind": "broken-commitment", "commitment_id": "c-9",
        "description": "do the thing", "tick": 4,
        "outcome": "never started"}]


# -- FAILING: backup visibility of the new sidecar ----------------------------

def test_stakes_sidecar_has_backup_visibility_decision():
    """Every sibling tracker sidecar carries an explicit .gitignore
    decision with a dated comment (followthrough.json, provenance.json,
    counterfactuals.json, prediction_errors.json, ...): per-run loss
    history is private metacognitive state, local-only. stakes.json
    has no entry, so the daily backup (`git add -A` in
    calibos-mind-backup) will commit and push the live loss ledger —
    including verbatim commitment descriptions and outcome text — to
    the remote repo on the first backup after the live mind ticks.
    The defect is the missing decision, not the file: add the
    local-only entry (sibling precedent) or record a deliberate
    decision to track it."""
    repo_root = Path(cli.__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", "stakes.json"],
        cwd=repo_root, capture_output=True, text=True)
    assert result.returncode == 0, (
        "stakes.json is not gitignored: the next `git add -A` backup "
        "will publish the live loss ledger. Add a dated .gitignore "
        "entry (sibling-tracker precedent) or record a deliberate "
        "decision to track it.")
