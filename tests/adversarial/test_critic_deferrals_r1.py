"""Critic round 1 hostile tests for the deferral-buckets build (Domain 19).

Spec: research/specs/2026-11-12-deferral-buckets.md
Builder: tests/test_deferrals.py (19 tests, all green) + calibos_mind/deferrals.py
        + cli.py wiring.

These tests are independent attacks, not a re-run of the builder's battery.
All fixtures on /tmp — the live store is never touched (conftest autouse
fixture redirects every cli store path, including deferrals.json).
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
from calibos_mind.deferrals import DeferralTracker


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-deferrals-"))


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


# -- F1: closed is terminal ---------------------------------------------------

def test_critic_redefer_of_answered_item_refused_module():
    """A settled item is closed. Deferring it again must refuse: the
    answered record and the release/close history are the written
    record, and silently resurrecting the item as 'deferred' with a
    fresh reminder contradicts them."""
    tracker = _tracker()
    tracker.defer("prompt-0001", "work", "the garden question",
                  "low", 0.95, tick=0, reminder_id="rem-0")
    tracker.settle("prompt-0001", "answered", tick=3)
    assert tracker.items["prompt-0001"]["status"] == "answered"
    with pytest.raises((ValueError, Exception), match="already"):
        tracker.defer("prompt-0001", "work", "the garden question",
                      "low", 0.95, tick=4, reminder_id="rem-1")
    assert tracker.items["prompt-0001"]["status"] == "answered"
    assert tracker.items["prompt-0001"]["defer_count"] == 1


def test_critic_redefer_of_released_item_refused_module():
    """Same terminal rule on the released path: a released item must
    not come back as deferred with a new reminder while the release
    log still says it was released."""
    tracker = _tracker()
    tracker.defer("prompt-0001", "work", "the garden question",
                  "low", 0.95, tick=0, reminder_id="rem-0")
    tracker.settle("prompt-0001", "released", tick=3, note="not mine")
    assert len(tracker.releases) == 1
    with pytest.raises((ValueError, Exception), match="already"):
        tracker.defer("prompt-0001", "work", "the garden question",
                      "low", 0.95, tick=4, reminder_id="rem-1")
    assert tracker.items["prompt-0001"]["status"] == "released"
    assert tracker.items["prompt-0001"]["defer_count"] == 1
    assert len(tracker.releases) == 1


def test_critic_redefer_of_closed_item_refused_cli():
    """CLI level: `mind defer` on an already-settled item refuses, mints
    no new reminder, and leaves the item answered."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    rc, out = _run(cli.cmd_settle, argparse.Namespace(
        id=pid, answered=True, released=False, note=""))
    assert rc == 0, out
    before = len(_open_commitments(cli._subject()))
    rc, out = _defer(pid, bucket="work", urgency="low")
    assert rc == 1, out
    assert len(_open_commitments(cli._subject())) == before
    data = _sidecar_data(cli.DEFERRALS)
    assert data["items"][pid]["status"] == "answered"
    assert data["items"][pid]["defer_count"] == 1


# -- read-path no-write, CLI level ---------------------------------------------

def test_critic_fresh_store_read_paths_create_no_sidecar():
    """`mind buckets`, `mind revisit`, and a refused `mind defer` on a
    fresh store must not create deferrals.json: a read (or a refusal)
    is not a write."""
    _init_store()
    assert not cli.DEFERRALS.exists()
    rc, _out = _run(cli.cmd_buckets, argparse.Namespace())
    assert rc == 0
    assert not cli.DEFERRALS.exists()
    rc, _out = _run(cli.cmd_revisit, argparse.Namespace(bucket=None))
    assert rc == 0
    assert not cli.DEFERRALS.exists()
    # A refused defer (rested body, low weariness) touches nothing.
    _pin_needs(cli._subject(), focus=0.9, fatigue=0.05)
    pid = _queue_prompt("think about the garden")
    rc, out = _defer(pid, bucket="work", urgency="low")
    assert rc == 1
    assert not cli.DEFERRALS.exists()
    assert len(list(cli.INBOX.glob("prompt-*.json"))) == 1


# -- forgotten penalty counted exactly once -------------------------------------

def test_critic_forgotten_penalty_counted_once_after_scan():
    """The live count (unscanned overdue item) and the recorded count
    (folded by the scan) are the same item: the gate must be identical
    before and after the scan, not one penalty higher after it."""
    tracker = _tracker()
    tracker.defer("prompt-0001", "work", "the garden question",
                  "low", 0.95, tick=0, reminder_id="rem-0")
    gate_before = tracker.effective_gate("work", 0, 20)
    assert gate_before == pytest.approx(0.60)
    newly = tracker.scan_overdue(20)
    assert [i["prompt_id"] for i in newly] == ["prompt-0001"]
    assert tracker.buckets["work"]["forgotten"] == 1
    gate_after = tracker.effective_gate("work", 0, 20)
    assert gate_after == pytest.approx(0.60)
    assert tracker.scan_overdue(21) == []


# -- re-defer across buckets ----------------------------------------------------

def test_critic_redefer_across_buckets_moves_reminder():
    """Re-deferring into a different bucket moves the item, settles the
    old reminder as kept, and leaves exactly one open reminder."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    rc, out = _defer(pid, bucket="personal", urgency="low")
    assert rc == 0, out
    data = _sidecar_data(cli.DEFERRALS)
    assert data["items"][pid]["bucket"] == "personal"
    assert data["items"][pid]["defer_count"] == 2
    subject = cli._subject()
    reminders = [c for c in _commitments(subject).values()
                 if pid in c.description]
    assert len(reminders) == 2
    assert len([c for c in reminders
                if c.status in {"open", "overdue"}]) == 1
    assert data["items"][pid]["reminder_id"] == \
        [c for c in reminders if c.status in {"open", "overdue"}][0].id


# -- reminder-as-commitment judgment call: visible, counted ----------------------

def test_critic_reminder_visible_in_status_and_goals():
    """The reminder is a real engine commitment: it shows as an open
    loop in `mind status` and counts as an open authored commitment in
    `mind goals`. That is the design (the reminder must be seen to be
    kept); this test pins it so a future change cannot silently hide
    reminders from the wake."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    rc, out = _run(cli.cmd_status, argparse.Namespace(raw=False))
    assert rc == 0
    assert pid in out
    assert "revisit deferred" in out
    rc, out = _run(cli.cmd_goals, argparse.Namespace())
    assert rc == 0
    from calibos_mind.goals import report as goals_report
    rep = goals_report(cli._subject(), cli.GOALS)
    assert rep["commitments"]["open"] == 1
    assert rep["commitments"]["by_status"]["open"] == 1


# -- revisit scan folds exactly once, CLI level ----------------------------------

def test_critic_revisit_twice_cli_folds_overdue_once():
    """Two `mind revisit` runs past the same window fold the item into
    `forgotten` once, and the second run reports no newly overdue."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    item = _sidecar_data(cli.DEFERRALS)["items"][pid]
    assert item["revisit_tick"] <= 12
    # Advance the engine tick past the revisit window with heartbeats.
    for _ in range(item["revisit_tick"] + 1):
        rc, _out = _run(cli.cmd_heartbeat,
                        argparse.Namespace(ticks=1, note=None))
        assert rc == 0
    rc, out = _run(cli.cmd_revisit, argparse.Namespace(bucket="work"))
    assert rc == 0, out
    assert "counted as forgotten" in out
    assert _sidecar_data(cli.DEFERRALS)["buckets"]["work"]["forgotten"] == 1
    rc, out = _run(cli.cmd_revisit, argparse.Namespace(bucket="work"))
    assert rc == 0, out
    assert "counted as forgotten" not in out
    assert _sidecar_data(cli.DEFERRALS)["buckets"]["work"]["forgotten"] == 1


# -- bucket removal with reassign keeps an overdue item intact --------------------

def test_critic_remove_bucket_reassign_keeps_overdue_item():
    """Removing a bucket with --to moves an overdue item intact (status,
    count, revisit tick stand) and the new bucket's gate still counts
    it as a forgotten item until it is acted on."""
    tracker = _tracker()
    tracker.defer("prompt-0001", "work", "the garden question",
                  "low", 0.95, tick=0, reminder_id="rem-0")
    tracker.scan_overdue(20)
    assert tracker.items["prompt-0001"]["status"] == "overdue"
    moved = tracker.remove_bucket("work", 21, reassign_to="personal",
                                  reason="one pile is enough")
    assert [i["prompt_id"] for i in moved] == ["prompt-0001"]
    item = tracker.items["prompt-0001"]
    assert item["bucket"] == "personal"
    assert item["status"] == "overdue"
    assert item["defer_count"] == 1
    # The moved overdue item still counts against the new bucket's gate.
    assert tracker.effective_gate("personal", 0, 21) == pytest.approx(0.75)
