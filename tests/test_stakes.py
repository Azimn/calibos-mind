"""Tests for the loss ledger (Domain 31, Vulnerability and Stakes).

Spec: research/specs/2026-11-24-loss-ledger.md
All fixtures on /tmp — the live store is never touched.

The mechanism: calibos_mind/stakes.py — a StakesTracker owning the
stakes.json sidecar, fed ONLY from cli._run_tick after the habits
reconcile and the dormancy demotion, plus a `mind stakes` command.
It records two kinds of documented loss:

  1. Habit bypass loss: a `formed:` habit whose strength was at or
     above the bypass threshold (0.65) at the last observation and is
     now below it — one row per crossing episode (recovery to >= 0.65
     and a later re-decay mints a second row). A habit first seen
     already below 0.65 mints nothing (no backfill).
  2. Broken commitment: a commitment resolving to status `broken` —
     one row per breakage, latched by commitment id, naming the
     engine's own resolving tick and outcome text. Kept and released
     resolutions mint nothing.

Fitness functions under test:
  1. Habit seeded at 0.70, decaying across 0.65 in one observed tick
     -> exactly one loss row (habit name, tick, before/after exact);
     the next tick with no further crossing mints nothing.
  2. Habit recovering to >= 0.65 then decaying below it again -> a
     second row; recovery alone mints nothing.
  3. Habit first seen already below 0.65 -> no row, baseline recorded.
  4. Habit at exactly 0.65 stays above the loss line (no row);
     0.649... mints only if the previous observation was >= 0.65.
  5. Broken commitment -> exactly one row (id, description, tick,
     outcome); kept and released resolutions mint nothing; a second
     broken commitment mints a second row; the same breakage observed
     twice mints one row.
  6. No losses -> report prints "no losses recorded" with counts 0;
     fresh store -> no sidecar minted by the report.
  7. Read-only sweep (`mind stakes`, `mind drift`, `mind status`,
     `mind goals`) leaves the sidecar byte-identical; dream ticks
     leave it byte-identical.
  8. `init --force` wipes the sidecar (including a pending/corrupt
     one); corrupt sidecar shapes fail loud before the tick advances.
  9. Determinism: identical tick sequences -> byte-identical sidecars.
  10. Full suite green; engine + cartridge byte-identical
      (run separately).

Scenario control (mirrors test_goals.py / test_habits_silence_decay.py):
the tests pin engine.state.needs / pressures / pressure_baselines
directly, commit through a transaction, then run cli._run_tick — the
real wiring under test. Measured premises (probed 2026-10-02 against
the frozen install): fear 0.95 (baseline 0.0) vs top need 0.80 ->
pressure channel wins (0.95 >= 0.80 + 0.12) -> action "withdraw";
a formed (fear -> withdraw) habit crystallizes at 0.45 after 3
co-fires and grows +0.02 per co-fire to the 0.70 cap at tick 16;
silent hunger ticks cost one DECAY_STEP (0.05) per full 100-tick
window of trigger silence, so 0.70 -> 0.65 at tick 116 and 0.60 at
tick 216. Unit tests drive the tracker directly with a fake subject
for exact boundary control.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.stakes import (
    BROKEN_COMMITMENT,
    HABIT_BYPASS,
    LOSSES_SHOWN,
    StakesTracker,
    report,
)
from digital_subject.continuity import Commitment
from digital_subject.models import Action, Habit


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="stakes-test-"))


def _fake_subject(habits=None, commitments=None, tick=0):
    """A minimal subject exposing only what observe()/report() read."""
    return SimpleNamespace(
        engine=SimpleNamespace(state=SimpleNamespace(
            tick=tick, habits=dict(habits or {}))),
        continuity=SimpleNamespace(state=SimpleNamespace(
            commitments=dict(commitments or {}))))


def _habit(key, strength, trigger="curiosity"):
    return Habit(key, trigger, Action.WITHDRAW,
                 strength, 0, 0)


def _commitment(cid, status="open", description="do the thing",
                resolved_tick=None, outcome=None, due_tick=None):
    return Commitment(
        id=cid, actor="self", description=description, beneficiary="self",
        created_tick=0, due_tick=due_tick, importance=0.5,
        evidence_ids=(), status=status, outcome=outcome,
        resolved_tick=resolved_tick)


def _tracker(tmp: Path):
    return StakesTracker(tmp / "stakes.json")


def _sidecar_bytes(tmp: Path):
    p = tmp / "stakes.json"
    return p.read_bytes() if p.exists() else None


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


def _fear_script():
    return (_needs(energy=0.20), _pressures(fear=0.95, trust=0.5),
            _baselines(fear=0.0))


def _silent_script():
    return (_needs(hunger=0.80), _pressures(), _baselines())


def _init_store():
    """Fresh synthetic store via the CLI (paths already redirected by
    the conftest autouse fixture). Returns the subject."""
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _add_commitment(subject, description, due_tick=None,
                    commitment_id=None):
    """Create one commitment in the live store (transaction-committed)."""
    with subject._transaction():
        item = subject.continuity.create_commitment(
            "self", description, beneficiary="self",
            tick=subject.engine.state.tick, due_tick=due_tick,
            importance=0.5, commitment_id=commitment_id)
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


def _stakes_output():
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_stakes(argparse.Namespace())
    return buf.getvalue()


# -- fitness 1: one crossing mints exactly one row --------------------------

def test_crossing_mints_exactly_one_row():
    tmp = _tmp()
    tracker = _tracker(tmp)
    key = "formed:curiosity:explore"
    subject = _fake_subject(habits={key: _habit(key, 0.70)})
    assert tracker.observe(subject, 1) is True  # baseline recorded
    tracker.save()
    assert tracker.losses == []

    subject = _fake_subject(habits={key: _habit(key, 0.60, "curiosity")})
    assert tracker.observe(subject, 2) is True
    tracker.save()
    assert tracker.losses == [{
        "kind": HABIT_BYPASS, "habit": key, "tick": 2,
        "before": 0.70, "after": 0.60}]

    # The next tick with no further crossing mints nothing.
    subject = _fake_subject(habits={key: _habit(key, 0.60)})
    assert tracker.observe(subject, 3) is False
    assert tracker.losses == [{
        "kind": HABIT_BYPASS, "habit": key, "tick": 2,
        "before": 0.70, "after": 0.60}]


def test_crossing_from_cap_exact_before_after():
    """A crossing straight off the 0.70 growth cap records exact floats."""
    tmp = _tmp()
    tracker = _tracker(tmp)
    key = "formed:fear:withdraw"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 10)
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.65)}), 11)
    assert tracker.losses == []  # 0.65 is still bypass, not loss
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.60)}), 12)
    assert tracker.losses == [{
        "kind": HABIT_BYPASS, "habit": key, "tick": 12,
        "before": 0.65, "after": 0.60}]


# -- fitness 2: recovery then re-decay --------------------------------------

def test_recovery_then_redecay_mints_second_row():
    tmp = _tmp()
    tracker = _tracker(tmp)
    key = "formed:curiosity:explore"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 1)
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.60)}), 2)
    assert len(tracker.losses) == 1
    # Recovery alone mints nothing.
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 3)
    assert len(tracker.losses) == 1
    assert tracker.seen_habits[key] == 0.70
    # ... but the latch is per-episode: a later re-decay mints again.
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.64)}), 4)
    assert tracker.losses == [
        {"kind": HABIT_BYPASS, "habit": key, "tick": 2,
         "before": 0.70, "after": 0.60},
        {"kind": HABIT_BYPASS, "habit": key, "tick": 4,
         "before": 0.70, "after": 0.64}]


# -- fitness 3: first seen below the line -----------------------------------

def test_first_seen_below_threshold_mints_nothing():
    tmp = _tmp()
    tracker = _tracker(tmp)
    key = "formed:curiosity:explore"
    subject = _fake_subject(habits={key: _habit(key, 0.60)})
    assert tracker.observe(subject, 1) is True  # baseline recorded
    assert tracker.losses == []
    assert tracker.seen_habits[key] == 0.60
    # Further decay below the line is still no crossing — the ledger
    # never saw this habit above the line, so it never owned a bypass
    # to lose.
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.45)}), 2)
    assert tracker.losses == []
    assert tracker.seen_habits[key] == 0.45


# -- fitness 4: the exact threshold ------------------------------------------

def test_exact_threshold_stays_above_the_line():
    tmp = _tmp()
    tracker = _tracker(tmp)
    key = "formed:curiosity:explore"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 1)
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.65)}), 2)
    assert tracker.losses == []
    # 0.649... mints only because the previous observation was >= 0.65.
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.649)}), 3)
    assert tracker.losses == [{
        "kind": HABIT_BYPASS, "habit": key, "tick": 3,
        "before": 0.65, "after": 0.649}]


def test_first_seen_at_threshold_then_decay_mints():
    """First seen exactly at 0.65 counts as above the line: the next
    decay below it is a genuine crossing, not backfill."""
    tmp = _tmp()
    tracker = _tracker(tmp)
    key = "formed:curiosity:explore"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.65)}), 1)
    assert tracker.losses == []
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.60)}), 2)
    assert tracker.losses == [{
        "kind": HABIT_BYPASS, "habit": key, "tick": 2,
        "before": 0.65, "after": 0.60}]


def test_authored_habits_are_not_the_ledgers_domain():
    """Authored habits decay through dormancy, not through this ledger:
    an authored habit falling below 0.65 mints nothing and leaves no
    baseline behind."""
    tmp = _tmp()
    tracker = _tracker(tmp)
    key = "curious_question"
    subject = _fake_subject(habits={key: _habit(key, 0.72, "curiosity")})
    assert tracker.observe(subject, 1) is False
    assert tracker.seen_habits == {}
    subject = _fake_subject(habits={key: _habit(key, 0.60)})
    assert tracker.observe(subject, 2) is False
    assert tracker.losses == []
    assert tracker.seen_habits == {}


def test_removed_habit_prunes_baseline_without_a_row():
    """A habit that leaves engine state (removed below the formation
    floor) drops its baseline and mints no row for disappearing."""
    tmp = _tmp()
    tracker = _tracker(tmp)
    key = "formed:curiosity:explore"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 1)
    assert tracker.seen_habits == {key: 0.70}
    assert tracker.observe(_fake_subject(habits={}), 2) is True
    assert tracker.seen_habits == {}
    assert tracker.losses == []


# -- fitness 5: broken commitments -------------------------------------------

def test_broken_commitment_mints_exactly_one_row():
    tmp = _tmp()
    tracker = _tracker(tmp)
    cid = "commit-1"
    tracker.observe(_fake_subject(
        commitments={cid: _commitment(cid)}), 1)
    assert tracker.losses == []
    broken = _commitment(cid, status="broken", resolved_tick=5,
                         outcome="missed the deadline")
    tracker.observe(_fake_subject(commitments={cid: broken}), 5)
    assert tracker.losses == [{
        "kind": BROKEN_COMMITMENT, "commitment_id": cid,
        "description": "do the thing", "tick": 5,
        "outcome": "missed the deadline"}]
    # The same breakage observed twice mints one row.
    assert tracker.observe(_fake_subject(commitments={cid: broken}), 6) \
        is False
    assert len(tracker.losses) == 1


def test_first_seen_broken_mints_row_with_engine_tick():
    """A commitment created and broken between observations is still a
    store-documented event: the engine stamped the resolving tick and
    the outcome, so the row names those — never an invented tick."""
    tmp = _tmp()
    tracker = _tracker(tmp)
    cid = "commit-9"
    broken = _commitment(cid, status="broken", resolved_tick=4,
                         outcome="never started")
    assert tracker.observe(_fake_subject(commitments={cid: broken}), 7) \
        is True
    assert tracker.losses == [{
        "kind": BROKEN_COMMITMENT, "commitment_id": cid,
        "description": "do the thing", "tick": 4,
        "outcome": "never started"}]


def test_kept_and_released_mint_nothing():
    tmp = _tmp()
    tracker = _tracker(tmp)
    kept = _commitment("c-kept", status="kept", resolved_tick=3,
                       outcome="done")
    released = _commitment("c-rel", status="released", resolved_tick=4,
                           outcome="let go on purpose")
    subject = _fake_subject(commitments={"c-kept": kept,
                                         "c-rel": released})
    assert tracker.observe(subject, 5) is False
    assert tracker.losses == []
    assert tracker.counts == {"habit-bypass": 0, "broken-commitment": 0,
                              "total": 0}


def test_second_broken_commitment_mints_second_row():
    tmp = _tmp()
    tracker = _tracker(tmp)
    first = _commitment("c-1", status="broken", resolved_tick=2,
                        outcome="missed it", description="first")
    second = _commitment("c-2", status="broken", resolved_tick=3,
                         outcome="gave up", description="second")
    tracker.observe(_fake_subject(commitments={"c-1": first}), 2)
    tracker.observe(_fake_subject(
        commitments={"c-1": first, "c-2": second}), 3)
    assert [r["commitment_id"] for r in tracker.losses] == ["c-1", "c-2"]
    assert tracker.counts["broken-commitment"] == 2


def test_observe_fails_loud_on_malformed_live_state():
    """Unknown commitment status, a broken commitment with no engine
    resolving tick, or a non-numeric habit strength is a malformed
    store, never a silently skipped row."""
    tmp = _tmp()
    tracker = _tracker(tmp)
    with pytest.raises(ValueError):
        tracker.observe(_fake_subject(commitments={
            "c": _commitment("c", status="vanished")}), 1)
    broken_no_tick = _commitment("c", status="broken", outcome="x")
    with pytest.raises(ValueError):
        tracker.observe(_fake_subject(
            commitments={"c": broken_no_tick}), 1)
    broken_no_outcome = _commitment("c", status="broken", resolved_tick=1)
    with pytest.raises(ValueError):
        tracker.observe(_fake_subject(
            commitments={"c": broken_no_outcome}), 1)
    with pytest.raises(ValueError):
        tracker.observe(_fake_subject(habits={
            "formed:x:y": _habit("formed:x:y", True)}), 1)


# -- fitness 6: report is a pure read ----------------------------------------

def test_report_pure_read_and_empty_state():
    tmp = _tmp()
    tracker = _tracker(tmp)
    tracker.observe(_fake_subject(
        habits={"formed:a:b": _habit("formed:a:b", 0.70)},
        commitments={"c-1": _commitment(
            "c-1", due_tick=12, description="due soon")}), 1)
    tracker.save()
    subject = _fake_subject(
        habits={"formed:a:b": _habit("formed:a:b", 0.70)},
        commitments={"c-1": _commitment(
            "c-1", due_tick=12, description="due soon")},
        tick=1)
    before = _sidecar_bytes(tmp)
    first = report(subject, tracker)
    second = report(subject, tracker)
    assert first == second
    assert _sidecar_bytes(tmp) == before
    assert first["tick"] == 1
    assert first["at_risk_habits"] == [
        {"habit": "formed:a:b", "strength": 0.70}]
    assert first["open_commitments"] == [{
        "id": "c-1", "description": "due soon", "status": "open",
        "due_tick": 12, "created_tick": 0}]
    assert first["losses"] == []
    assert first["counts"] == {"habit-bypass": 0, "broken-commitment": 0,
                               "total": 0}


def test_report_on_fresh_store_mints_no_sidecar():
    tmp = _tmp()
    subject = _fake_subject(tick=0)
    rep = report(subject, tmp / "stakes.json")
    assert rep["losses"] == []
    assert rep["counts"]["total"] == 0
    assert rep["at_risk_habits"] == []
    assert rep["open_commitments"] == []
    assert not (tmp / "stakes.json").exists()


def test_report_losses_most_recent_first_capped():
    tmp = _tmp()
    tracker = _tracker(tmp)
    commitments = {}
    for i in range(LOSSES_SHOWN + 5):
        cid = f"c-{i:02d}"
        commitments[cid] = _commitment(
            cid, status="broken", resolved_tick=i, outcome="x",
            description=f"commitment {i}")
    tracker.observe(_fake_subject(commitments=commitments), 100)
    assert tracker.counts["total"] == LOSSES_SHOWN + 5
    rep = report(_fake_subject(commitments=commitments, tick=100),
                 tracker)
    assert rep["losses_total"] == LOSSES_SHOWN + 5
    assert rep["losses_shown"] == LOSSES_SHOWN
    assert rep["losses_limit"] == LOSSES_SHOWN
    shown_ticks = [r["tick"] for r in rep["losses"]]
    assert shown_ticks == sorted(shown_ticks, reverse=True)
    assert shown_ticks[0] == LOSSES_SHOWN + 4
    assert shown_ticks[-1] == 5


# -- fitness 8 (unit half): corrupt sidecar ----------------------------------

def test_corrupt_sidecar_variants_fail_loud():
    path = None
    variants = {
        "invalid json": "{not json",
        "non-dict top": "[1, 2]",
        "losses not list": json.dumps({"losses": {}, "seen_habits": {}}),
        "losses null": json.dumps({"losses": None, "seen_habits": {}}),
        "row not object": json.dumps({"losses": [1], "seen_habits": {}}),
        "unknown kind": json.dumps({"losses": [{"kind": "mystery",
                                                "tick": 1}],
                                    "seen_habits": {}}),
        "habit row before below line": json.dumps({"losses": [{
            "kind": HABIT_BYPASS, "habit": "formed:a:b", "tick": 1,
            "before": 0.60, "after": 0.50}], "seen_habits": {}}),
        "habit row after above line": json.dumps({"losses": [{
            "kind": HABIT_BYPASS, "habit": "formed:a:b", "tick": 1,
            "before": 0.70, "after": 0.65}], "seen_habits": {}}),
        "habit row non-formed key": json.dumps({"losses": [{
            "kind": HABIT_BYPASS, "habit": "curious_question", "tick": 1,
            "before": 0.70, "after": 0.60}], "seen_habits": {}}),
        "duplicate broken id": json.dumps({"losses": [
            {"kind": BROKEN_COMMITMENT, "commitment_id": "c",
             "description": "d", "tick": 1, "outcome": "x"},
            {"kind": BROKEN_COMMITMENT, "commitment_id": "c",
             "description": "d", "tick": 2, "outcome": "y"}],
            "seen_habits": {}}),
        "duplicate habit tick": json.dumps({"losses": [
            {"kind": HABIT_BYPASS, "habit": "formed:a:b", "tick": 1,
             "before": 0.70, "after": 0.60},
            {"kind": HABIT_BYPASS, "habit": "formed:a:b", "tick": 1,
             "before": 0.70, "after": 0.55}], "seen_habits": {}}),
        "habit rows out of order": json.dumps({"losses": [
            {"kind": HABIT_BYPASS, "habit": "formed:a:b", "tick": 5,
             "before": 0.70, "after": 0.60},
            {"kind": HABIT_BYPASS, "habit": "formed:a:b", "tick": 3,
             "before": 0.70, "after": 0.60}], "seen_habits": {}}),
        "seen not object": json.dumps({"losses": [], "seen_habits": []}),
        "seen bad key": json.dumps({"losses": [],
                                    "seen_habits": {"plain": 0.5}}),
        "seen bad value": json.dumps(
            {"losses": [], "seen_habits": {"formed:a:b": "high"}}),
        "seen bool value": json.dumps(
            {"losses": [], "seen_habits": {"formed:a:b": True}}),
    }
    for desc, body in variants.items():
        tmp = _tmp()
        path = tmp / "stakes.json"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt stakes sidecar"):
            StakesTracker(path)


# -- fitness 9: determinism ---------------------------------------------------

def test_identical_sequences_byte_identical_sidecar():
    def _run():
        tmp = _tmp()
        tracker = _tracker(tmp)
        key = "formed:curiosity:explore"
        tracker.observe(_fake_subject(
            habits={key: _habit(key, 0.70)},
            commitments={"c-1": _commitment("c-1")}), 1)
        tracker.observe(_fake_subject(
            habits={key: _habit(key, 0.60)},
            commitments={"c-1": _commitment(
                "c-1", status="broken", resolved_tick=2,
                outcome="missed it")}), 2)
        tracker.observe(_fake_subject(
            habits={key: _habit(key, 0.70)}), 3)
        tracker.observe(_fake_subject(
            habits={key: _habit(key, 0.64)}), 4)
        tracker.save()
        return (tmp / "stakes.json").read_bytes()

    assert _run() == _run()


# -- end-to-end: silence decay across the line -------------------------------

def test_silence_decay_crossing_end_to_end():
    """Form (fear -> withdraw) to the 0.70 cap (16 ticks), then silent
    hunger ticks: 100 silent ticks leave 0.70 at 0.65 with no row (the
    line holds at exactly 0.65); the next 100 cost the crossing to
    0.60 with exactly one row (tick 216, before 0.65, after 0.60);
    further silent ticks mint nothing more."""
    subject = _init_store()
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    for _ in range(16):
        result = _script_tick(subject, needs, pressures, baselines)
        assert result["action"] == "withdraw"
    assert subject.engine.state.habits[key].strength == 0.70

    for _ in range(100):  # ticks 17..116
        result = _script_tick(subject, *_silent_script())
        assert result["action"] == "seek_contact"
    assert result["tick"] == 116
    assert subject.engine.state.habits[key].strength == 0.65
    tracker = StakesTracker(cli.STAKES)
    assert tracker.losses == []

    for _ in range(100):  # ticks 117..216
        result = _script_tick(subject, *_silent_script())
    assert result["tick"] == 216
    assert subject.engine.state.habits[key].strength == 0.60
    tracker = StakesTracker(cli.STAKES)
    assert tracker.losses == [{
        "kind": HABIT_BYPASS, "habit": key, "tick": 216,
        "before": 0.65, "after": 0.60}]

    for _ in range(5):  # ticks 217..221: still below the line, no row
        _script_tick(subject, *_silent_script())
    assert StakesTracker(cli.STAKES).losses == [{
        "kind": HABIT_BYPASS, "habit": key, "tick": 216,
        "before": 0.65, "after": 0.60}]

    # The report sees the loss and no longer lists the crossed habit
    # at risk — while the (hunger, seek_contact) habit that formed and
    # grew to the cap across the same silent ticks legitimately is
    # (it keeps firing, so it holds bypass).
    rep = report(subject, StakesTracker(cli.STAKES))
    assert rep["counts"] == {"habit-bypass": 1, "broken-commitment": 0,
                             "total": 1}
    assert all(h["habit"] != key for h in rep["at_risk_habits"])
    assert {"habit": "formed:hunger:seek_contact", "strength": 0.70} \
        in rep["at_risk_habits"]
    assert rep["losses"][0]["habit"] == key


def test_broken_commitment_end_to_end():
    """Create, resolve broken through the real CLI wiring: exactly one
    row, named with the engine's resolving tick and outcome; kept and
    released resolutions mint nothing."""
    subject = _init_store()
    kept = _add_commitment(subject, "keep this one")
    released = _add_commitment(subject, "let this one go")
    broken = _add_commitment(subject, "break this one")
    _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                 _baselines())
    _resolve_commitment(subject, kept, kept=True, outcome="done")
    _release_commitment(subject, released, outcome="let go on purpose")
    _resolve_commitment(subject, broken, kept=False,
                        outcome="missed the deadline")
    tick_before = subject.engine.state.tick
    _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                 _baselines())
    tracker = StakesTracker(cli.STAKES)
    assert tracker.losses == [{
        "kind": BROKEN_COMMITMENT, "commitment_id": broken,
        "description": "break this one", "tick": tick_before,
        "outcome": "missed the deadline"}]
    # A further tick observes the same breakage again: still one row.
    _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                 _baselines())
    assert StakesTracker(cli.STAKES).losses == tracker.losses


def test_no_sidecar_minted_by_a_tick_with_nothing_to_record():
    """A waking tick with no formed habits and no commitments records
    nothing — the no-op write discipline means no sidecar appears."""
    subject = _init_store()
    _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                 _baselines())
    assert not Path(cli.STAKES).exists()


# -- fitness 7: read-only sweep and dream ticks ------------------------------

def test_readonly_sweep_and_dream_leave_sidecar_identical():
    """99 silent ticks put the habit one tick from its first silence
    window; a read-only sweep (stakes, drift, status, goals) plus
    three dream ticks must leave the sidecar byte-identical — and the
    very next silent tick must still cost exactly one step with no
    loss row, proving the baseline was not disturbed."""
    subject = _init_store()
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    for _ in range(16):
        _script_tick(subject, needs, pressures, baselines)
    assert subject.engine.state.habits[key].strength == 0.70

    for _ in range(99):  # ticks 17..115
        _script_tick(subject, *_silent_script())
    assert subject.engine.state.habits[key].strength == 0.70
    assert Path(cli.STAKES).exists()
    before = Path(cli.STAKES).read_bytes()

    cli.cmd_stakes(argparse.Namespace())
    cli.cmd_drift(argparse.Namespace(window=30))
    cli.cmd_status(argparse.Namespace(raw=False))
    cli.cmd_goals(argparse.Namespace())
    for _ in range(3):
        subject.dream_tick()
    assert Path(cli.STAKES).read_bytes() == before

    # The next silent tick (116) still costs exactly one step, no row.
    result = _script_tick(subject, *_silent_script())
    assert result["tick"] == 116
    assert subject.engine.state.habits[key].strength == 0.65
    assert StakesTracker(cli.STAKES).losses == []


# -- fitness 8 (e2e half): init --force and fail-loud ------------------------

def test_init_force_wipes_sidecar_including_corrupt():
    subject = _init_store()
    # Seed a real loss row through the tracker on the redirected path.
    tracker = StakesTracker(cli.STAKES)
    key = "formed:curiosity:explore"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 1)
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.60)}), 2)
    tracker.save()
    assert Path(cli.STAKES).exists()
    assert len(StakesTracker(cli.STAKES).losses) == 1

    cli.cmd_init(argparse.Namespace(force=True))
    assert not Path(cli.STAKES).exists(), \
        "init --force must wipe the stakes sidecar (genome: reseed reset)"
    fresh = cli._subject()
    assert report(fresh, StakesTracker(cli.STAKES))["losses"] == []

    # A corrupt sidecar is wiped the same way, not a brick.
    Path(cli.STAKES).write_text("{corrupt", encoding="utf-8")
    cli.cmd_init(argparse.Namespace(force=True))
    assert not Path(cli.STAKES).exists()


def test_corrupt_sidecar_fails_before_tick_advances():
    subject = _init_store()
    tracker = StakesTracker(cli.STAKES)
    key = "formed:curiosity:explore"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 1)
    tracker.save()
    tick_before = subject.engine.state.tick
    Path(cli.STAKES).write_text("{corrupt", encoding="utf-8")
    before = Path(cli.STAKES).read_bytes()
    with pytest.raises(ValueError, match="corrupt stakes sidecar"):
        cli._run_tick(subject)
    assert subject.engine.state.tick == tick_before
    assert Path(cli.STAKES).read_bytes() == before


# -- fitness 6 (CLI half): `mind stakes` output ------------------------------

def test_stakes_command_output():
    # Fresh store: explicit "no losses recorded", and the report mints
    # no sidecar.
    subject = _init_store()
    out = _stakes_output()
    assert "no losses recorded" in out
    assert "losses: 0 total (0 habit-bypass, 0 broken-commitment)" in out
    assert not Path(cli.STAKES).exists()

    # Seeded losses: rows print most recent first, with the stated cap.
    tracker = StakesTracker(cli.STAKES)
    key = "formed:curiosity:explore"
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.70)}), 1)
    tracker.observe(_fake_subject(habits={key: _habit(key, 0.60)}), 2)
    tracker.observe(_fake_subject(commitments={
        "c-1": _commitment("c-1", status="broken", resolved_tick=3,
                           outcome="missed it",
                           description="file the report")}), 3)
    tracker.save()
    out = _stakes_output()
    assert "no losses recorded" not in out
    assert "losses: 2 total (1 habit-bypass, 1 broken-commitment)" in out
    assert "showing 2 of 2, limit 20" in out
    assert "[tick 3] broken-commitment:" in out
    assert "file the report" in out
    assert "missed it" in out
    assert "[tick 2] habit-bypass: formed:curiosity:explore fell " \
        "0.65 -> 0.6" not in out  # before was 0.70, not 0.65
    assert "[tick 2] habit-bypass: formed:curiosity:explore fell " \
        "0.7 -> 0.6" in out
    # Most recent first: the tick-3 row precedes the tick-2 row.
    assert out.index("[tick 3]") < out.index("[tick 2]")
    # Running the command twice leaves the sidecar byte-identical.
    before = Path(cli.STAKES).read_bytes()
    _stakes_output()
    assert Path(cli.STAKES).read_bytes() == before


def test_stakes_command_lists_at_risk_and_open():
    """At-risk habits and open commitments come from the live store at
    report time: a formed habit at the 0.70 cap and an open commitment
    with a due tick both show."""
    subject = _init_store()
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    for _ in range(16):
        _script_tick(subject, needs, pressures, baselines)
    assert subject.engine.state.habits[key].strength == 0.70
    cid = _add_commitment(subject, "ship the draft", due_tick=50)
    out = _stakes_output()
    assert "at risk (formed habits at or above 0.65" in out
    assert "formed:fear:withdraw: strength 0.7" in out
    assert "due tick 50" in out
    assert "ship the draft" in out
