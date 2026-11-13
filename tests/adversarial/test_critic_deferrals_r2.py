"""Critic round 2 hostile tests for the deferral-buckets build (Domain 19).

Spec: research/specs/2026-11-12-deferral-buckets.md
Round 1 returned FAIL: F1 (closed items resurrectable by re-defer) and
F2 (forgotten-item feedback lost on bucket move). The builder claims
both fixed in code; the round-1 file now passes 9/9 (verified by this
critic's own run). This file attacks the FIXES at their edges.

All fixtures on /tmp — the live store is never touched (conftest autouse
fixture redirects every cli store path, including deferrals.json).
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

import calibos_mind.cli as cli
from calibos_mind.deferrals import DeferralRefused, DeferralTracker


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-deferrals-r2-"))


def _tracker(tmp=None):
    return DeferralTracker((tmp or _tmp()) / "deferrals.json")


def _sidecar_data(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _needs(**over):
    d = {"energy": 0.95, "fatigue": 0.05, "hunger": 0.05, "thirst": 0.05,
         "comfort": 0.95, "pain": 0.0, "warmth": 0.9, "restlessness": 0.05,
         "curiosity": 0.10, "loneliness": 0.05, "safety": 0.9, "focus": 0.9,
         "satisfaction": 0.9}
    d.update(over)
    return d


def _init_store():
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _pin_needs(subject, **over):
    with subject._transaction():
        subject.engine.state.needs = dict(_needs(**over))


def _queue_prompt(text="think about the garden"):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.cmd_queue(argparse.Namespace(
            prompt=text, source="invitation", experience=None,
            from_person=None))
    assert rc == 0
    files = sorted(cli.INBOX.glob("prompt-*.json"))
    assert len(files) == 1
    return files[0].stem


def _run(cmd, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd(args)
    return rc, buf.getvalue()


def _defer(pid, bucket="work", urgency="low"):
    return _run(cli.cmd_defer, argparse.Namespace(
        id=pid, bucket=bucket, urgency=urgency))


def _commitments(subject):
    return subject.continuity.state.commitments


def _open_commitments(subject):
    return [c for c in _commitments(subject).values()
            if c.status in {"open", "overdue"}]


def _defer_item(tracker, pid, bucket="work", tick=0, text="x"):
    return tracker.defer(pid, bucket, text, "low", 0.95, tick=tick,
                         reminder_id=f"rem-{pid}-{tick}")


# -- F1 edges: closed is terminal, every path -------------------------------

def test_critic_r2_redefer_of_released_item_refused_cli():
    """CLI level, released path (round 1 only tested answered): `mind
    defer` on a released item refuses, mints no new reminder, and the
    release log still says it was released exactly once."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    rc, out = _run(cli.cmd_settle, argparse.Namespace(
        id=pid, answered=False, released=True, note="not mine"))
    assert rc == 0, out
    assert len(_open_commitments(cli._subject())) == 0
    rc, out = _defer(pid, bucket="work", urgency="low")
    assert rc == 1, out
    assert "already released" in out
    assert len(_open_commitments(cli._subject())) == 0
    data = _sidecar_data(cli.DEFERRALS)
    assert data["items"][pid]["status"] == "released"
    assert data["items"][pid]["defer_count"] == 1
    assert len(data["releases"]) == 1
    assert data["releases"][0]["prompt_id"] == pid


def test_critic_r2_redefer_after_settle_then_remove_reassign_module():
    """Settle-then-remove flow, module level: an answered item whose
    bucket was removed with --to moves with its verdict intact, and
    re-deferring it into the NEW bucket still refuses — the move must
    not launder a closed item back to open."""
    tracker = _tracker()
    _defer_item(tracker, "prompt-0001", tick=0)
    tracker.settle("prompt-0001", "answered", tick=3)
    moved = tracker.remove_bucket("work", 4, reassign_to="personal",
                                  reason="one pile is enough")
    assert moved == []
    item = tracker.items["prompt-0001"]
    assert item["bucket"] == "personal"
    assert item["status"] == "answered"
    with pytest.raises(DeferralRefused, match="already answered"):
        tracker.defer("prompt-0001", "personal", "the garden question",
                      "low", 0.95, tick=5, reminder_id="rem-1")
    assert tracker.items["prompt-0001"]["status"] == "answered"
    assert tracker.items["prompt-0001"]["defer_count"] == 1


def test_critic_r2_redefer_after_settle_then_remove_release_module():
    """Settle-then-remove flow, no reassign: an answered item keeps
    its verdict and its old bucket name as history after the bucket
    is gone. Re-deferring into a live bucket refuses as closed; into
    the removed bucket refuses as no-bucket. Both are refusals, and
    neither resurrects the item."""
    tracker = _tracker()
    _defer_item(tracker, "prompt-0001", tick=0)
    tracker.settle("prompt-0001", "answered", tick=3)
    released = tracker.remove_bucket("work", 4, reason="work pile goes")
    assert released == []
    item = tracker.items["prompt-0001"]
    assert item["status"] == "answered"
    assert item["bucket"] == "work"
    with pytest.raises(DeferralRefused, match="already answered"):
        tracker.defer("prompt-0001", "personal", "the garden question",
                      "low", 0.95, tick=5, reminder_id="rem-1")
    with pytest.raises(ValueError, match="no bucket"):
        tracker.defer("prompt-0001", "work", "the garden question",
                      "low", 0.95, tick=5, reminder_id="rem-1")
    assert tracker.items["prompt-0001"]["status"] == "answered"
    assert tracker.items["prompt-0001"]["defer_count"] == 1
    assert tracker.releases == []


def test_critic_r2_redefer_after_settle_then_remove_cli():
    """CLI level, settle-then-remove flow: defer, settle answered,
    `mind bucket remove work --to personal`, then `mind defer` into
    personal refuses and mints no new reminder."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    rc, out = _run(cli.cmd_settle, argparse.Namespace(
        id=pid, answered=True, released=False, note=""))
    assert rc == 0, out
    rc, out = _run(cli.cmd_bucket, argparse.Namespace(
        action="remove", name="work", to="personal",
        reason="one pile is enough"))
    assert rc == 0, out
    data = _sidecar_data(cli.DEFERRALS)
    assert data["items"][pid]["status"] == "answered"
    assert data["items"][pid]["bucket"] == "personal"
    before = len(_open_commitments(cli._subject()))
    rc, out = _defer(pid, bucket="personal", urgency="low")
    assert rc == 1, out
    assert "already answered" in out
    assert len(_open_commitments(cli._subject())) == before
    assert _sidecar_data(cli.DEFERRALS)["items"][pid]["defer_count"] == 1


def test_critic_r2_redefer_of_overdue_item_still_allowed():
    """The F1 fix must not overcorrect: "overdue" is open, not
    terminal. Re-deferring an overdue item works — status back to
    deferred, count increments, new revisit drawn."""
    tracker = _tracker()
    _defer_item(tracker, "prompt-0001", tick=0)
    item = tracker.items["prompt-0001"]
    assert 2 <= item["revisit_tick"] <= 12
    newly = tracker.scan_overdue(item["revisit_tick"])
    assert [i["prompt_id"] for i in newly] == ["prompt-0001"]
    assert tracker.items["prompt-0001"]["status"] == "overdue"
    again = tracker.defer("prompt-0001", "work", "the garden question",
                          "low", 0.95, tick=item["revisit_tick"],
                          reminder_id="rem-1")
    assert again["status"] == "deferred"
    assert again["defer_count"] == 2
    assert again["revisit_tick"] > item["revisit_tick"]


# -- F2 edges: forgotten feedback survives moves, exactly once --------------

def test_critic_r2_unscanned_overdue_moved_counts_live():
    """An item past its window but never scanned moves with status
    "deferred": no carry (nothing was folded), but it still counts
    live against the new bucket's gate — and after the first scan in
    the new bucket the gate is identical, folded exactly once."""
    tracker = _tracker()
    _defer_item(tracker, "prompt-0001", tick=0)
    revisit = tracker.items["prompt-0001"]["revisit_tick"]
    moved = tracker.remove_bucket("work", 20, reassign_to="personal",
                                  reason="one pile is enough")
    assert [i["prompt_id"] for i in moved] == ["prompt-0001"]
    assert tracker.buckets["personal"]["forgotten"] == 0
    gate_before = tracker.effective_gate("personal", 0, 20)
    assert gate_before == pytest.approx(0.75)
    newly = tracker.scan_overdue(20)
    assert [i["prompt_id"] for i in newly] == ["prompt-0001"]
    assert tracker.buckets["personal"]["forgotten"] == 1
    gate_after = tracker.effective_gate("personal", 0, 20)
    assert gate_after == pytest.approx(0.75)
    assert tracker.scan_overdue(21) == []
    assert tracker.buckets["personal"]["forgotten"] == 1


def test_critic_r2_mixed_scanned_and_unscanned_moved_together():
    """One item scanned (folded) in the old bucket, one past its
    window but unscanned: moving both together must count two
    forgettings in the new bucket — one carried, one live — and the
    gate must be identical before and after the scan folds the
    second. Neither direction may double-count."""
    tracker = _tracker()
    _defer_item(tracker, "prompt-0001", tick=0)
    _defer_item(tracker, "prompt-0002", tick=0)
    r1 = tracker.items["prompt-0001"]["revisit_tick"]
    r2 = tracker.items["prompt-0002"]["revisit_tick"]
    first, second = ("prompt-0001", "prompt-0002") \
        if r1 <= r2 else ("prompt-0002", "prompt-0001")
    first_tick = tracker.items[first]["revisit_tick"]
    second_tick = tracker.items[second]["revisit_tick"]
    # Scan at the earlier item's window: folds exactly the earlier one.
    newly = tracker.scan_overdue(first_tick)
    assert [i["prompt_id"] for i in newly] == [first]
    assert tracker.buckets["work"]["forgotten"] == 1
    # Move at the later item's window: it is now past-window too.
    moved = tracker.remove_bucket("work", second_tick,
                                  reassign_to="personal",
                                  reason="one pile is enough")
    assert {i["prompt_id"] for i in moved} == {"prompt-0001", "prompt-0002"}
    assert tracker.buckets["personal"]["forgotten"] == 1
    gate_before = tracker.effective_gate("personal", 0, second_tick)
    assert gate_before == pytest.approx(0.80)
    newly = tracker.scan_overdue(second_tick)
    assert [i["prompt_id"] for i in newly] == [second]
    assert tracker.buckets["personal"]["forgotten"] == 2
    gate_after = tracker.effective_gate("personal", 0, second_tick)
    assert gate_after == pytest.approx(0.80)
    assert tracker.scan_overdue(second_tick + 1) == []
    assert tracker.buckets["personal"]["forgotten"] == 2


def test_critic_r2_overdue_carried_exactly_once_through_two_moves():
    """A scanned overdue item moved twice (work -> personal ->
    knots) carries its forgotten feedback exactly once through the
    chain: each destination gains +1, the dead bucket's count dies
    with it, and a later scan never re-folds it."""
    tracker = _tracker()
    _defer_item(tracker, "prompt-0001", tick=0)
    tracker.scan_overdue(20)
    assert tracker.buckets["work"]["forgotten"] == 1
    tracker.remove_bucket("work", 21, reassign_to="personal",
                          reason="one pile is enough")
    assert tracker.buckets["personal"]["forgotten"] == 1
    assert tracker.effective_gate("personal", 0, 21) == pytest.approx(0.75)
    tracker.add_bucket("knots", 21, reason="knots pile")
    tracker.remove_bucket("personal", 22, reassign_to="knots",
                          reason="personal pile goes")
    assert tracker.buckets["knots"]["forgotten"] == 1
    assert tracker.effective_gate("knots", 0, 22) == pytest.approx(0.65)
    assert tracker.scan_overdue(22) == []
    assert tracker.scan_overdue(30) == []
    assert tracker.buckets["knots"]["forgotten"] == 1
    assert tracker.items["prompt-0001"]["status"] == "overdue"
    assert tracker.items["prompt-0001"]["bucket"] == "knots"


def test_critic_r2_revisit_after_move_folds_once_cli():
    """CLI level: defer, run past the window, move the bucket, then
    `mind revisit` on the new bucket folds the item once; a second
    revisit reports no newly overdue and the count stands."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    item = _sidecar_data(cli.DEFERRALS)["items"][pid]
    for _ in range(item["revisit_tick"] + 1):
        rc, _out = _run(cli.cmd_heartbeat,
                        argparse.Namespace(ticks=1, note=None))
        assert rc == 0
    rc, out = _run(cli.cmd_bucket, argparse.Namespace(
        action="remove", name="work", to="personal",
        reason="one pile is enough"))
    assert rc == 0, out
    rc, out = _run(cli.cmd_revisit, argparse.Namespace(bucket="personal"))
    assert rc == 0, out
    assert "counted as forgotten" in out
    assert _sidecar_data(cli.DEFERRALS)["buckets"]["personal"]["forgotten"] == 1
    rc, out = _run(cli.cmd_revisit, argparse.Namespace(bucket="personal"))
    assert rc == 0, out
    assert "counted as forgotten" not in out
    assert _sidecar_data(cli.DEFERRALS)["buckets"]["personal"]["forgotten"] == 1


# -- F3 (genome: silent zero on missing sections) -----------------------------

def test_critic_r2_missing_sidecar_sections_raise():
    """The writer always writes all four keys, so a sidecar missing
    one is malformed and must raise — never silently load with zero
    buckets, zero items, or an empty release/decision record (the
    2026-09-24 silent-zero genome item: a missing section the writer
    always writes is malformed, only a genuinely empty section may
    report empty)."""
    root = _tmp()
    path = root / "deferrals.json"
    work_cfg = {
        "gate": 0.55, "revisit_window": [4, 24],
        "escalation_threshold": 3, "escalation_step": 0.1,
        "forgotten_penalty": 0.05, "forgotten_penalty_cap": 0.25,
        "forgotten": 0,
    }
    full = {
        "buckets": {"work": dict(work_cfg),
                    "personal": dict(work_cfg, gate=0.70)},
        "items": {}, "releases": [], "decisions": [],
    }
    for missing in ("buckets", "items", "releases", "decisions"):
        data = {k: v for k, v in full.items() if k != missing}
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt deferrals sidecar"):
            DeferralTracker(path)
    # Genuinely empty sections are fine: all four keys present, no
    # items, no releases, no decisions loads clean.
    path.write_text(json.dumps(full), encoding="utf-8")
    tracker = DeferralTracker(path)
    assert set(tracker.buckets) == {"work", "personal"}
    assert tracker.items == {}
    assert tracker.releases == []
    assert tracker.decisions == []


# -- read-path no-write: revisit on a live sidecar ----------------------------

def test_critic_r2_revisit_with_nothing_overdue_writes_nothing():
    """`mind revisit` and `mind buckets` on an existing sidecar with
    nothing overdue leave deferrals.json byte-identical; `mind
    settle` on an unknown id refuses without creating anything."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    before = cli.DEFERRALS.read_bytes()
    rc, _out = _run(cli.cmd_revisit, argparse.Namespace(bucket=None))
    assert rc == 0
    assert cli.DEFERRALS.read_bytes() == before
    rc, _out = _run(cli.cmd_buckets, argparse.Namespace())
    assert rc == 0
    assert cli.DEFERRALS.read_bytes() == before
    rc, out = _run(cli.cmd_settle, argparse.Namespace(
        id="prompt-9999", answered=True, released=False, note=""))
    assert rc == 1
    assert cli.DEFERRALS.read_bytes() == before


# -- two genuine episodes are two forgettings (pinned semantics) --------------

def test_critic_r2_two_genuine_episodes_count_twice():
    """An item forgotten, re-deferred, and forgotten again through a
    NEW revisit window is two genuine pass-window episodes, not one
    item double-counted: the recorded total rises to 2. (The
    exactly-once rule binds one episode; a re-defer opens a new one.)"""
    tracker = _tracker()
    _defer_item(tracker, "prompt-0001", tick=0)
    r1 = tracker.items["prompt-0001"]["revisit_tick"]
    tracker.scan_overdue(r1)
    assert tracker.buckets["work"]["forgotten"] == 1
    tracker.defer("prompt-0001", "work", "the garden question",
                  "low", 0.95, tick=r1, reminder_id="rem-1")
    r2 = tracker.items["prompt-0001"]["revisit_tick"]
    assert r2 > r1
    tracker.scan_overdue(r2)
    assert tracker.buckets["work"]["forgotten"] == 2
    assert tracker.items["prompt-0001"]["status"] == "overdue"


# -- genome: shadowing definitions --------------------------------------------

def test_critic_r2_no_shadowed_definitions():
    """No function defined twice in cli.py or deferrals.py (genome:
    the 2026-09-24 duplicate cmd_init silently dropped the
    salience-sidecar reset)."""
    for rel in ("calibos_mind/cli.py", "calibos_mind/deferrals.py"):
        path = Path(__file__).resolve().parents[2] / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Module-level definitions only: nested helpers with the same
        # name in different scopes (e.g. each _validate_* method's
        # local bad()) are distinct closures, not shadowing. The
        # genome item is two same-named definitions at one level,
        # where the second silently wins.
        top = [n.name for n in tree.body
               if isinstance(n, ast.FunctionDef)]
        dups = {n for n in top if top.count(n) > 1}
        assert not dups, f"{rel}: shadowed top-level defs {sorted(dups)}"
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods = [n.name for n in node.body
                           if isinstance(n, ast.FunctionDef)]
                mdups = {n for n in methods if methods.count(n) > 1}
                assert not mdups, \
                    f"{rel}: shadowed methods in {node.name}: " \
                    f"{sorted(mdups)}"
