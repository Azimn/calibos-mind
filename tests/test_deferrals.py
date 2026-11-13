"""Tests for the weariness-gated prompt-deferral channel (Domain 19).

Spec: research/specs/2026-11-12-deferral-buckets.md
All fixtures on /tmp — the live store is never touched (conftest
autouse fixture redirects every cli store path, including the new
deferrals.json sidecar).

The mechanism: calibos_mind/deferrals.py — a DeferralTracker owning
deferrals.json (named buckets with per-bucket gates, deferred items
with revisit times and reminder commitments, a release log, and a
category-decision log), wired to the CLI as `mind defer` /
`mind revisit` / `mind settle` / `mind buckets` / `mind bucket`.

Fitness functions under test:
  1. Defer is a real third option: a low-urgency prompt at high
     weariness defers, minting exactly one reminder commitment
     naming the item, the bucket, and the revisit time.
  2. High-urgency prompts are never deferred on weariness alone;
     low weariness refuses too — the gate actually gates, and a
     refused defer leaves the prompt untouched.
  3. Bucket operations: add/rename/set/remove; removal reassigns
     or releases with a written record; no item is ever silently
     lost. Categories are the mind's own decision: every change is
     recorded in the decision log with its reason.
  4. Repeat defers escalate: an item deferred 3 times has strictly
     higher priority and a strictly stricter gate on the 4th defer
     than on the 1st.
  5. Forgotten-item feedback: a bucket whose items passed their
     revisit windows unactioned defers less readily than an
     identical bucket with no forgotten items (same weariness,
     same prompt); the scan folds each item into `forgotten`
     exactly once.
  6. Revisit times fall inside the bucket's window and vary across
     draws (jitter is real, not a fixed offset).
  7. The revisit ritual surfaces a random thought and a
     remembering pass over the bucket's contents, deterministically.
  8. Read-only commands and dream ticks never defer, never mint
     reminders, never touch bucket state (sidecar byte-identical).
  9. `mind init --force` wipes bucket/reminder state, including
     corrupt sidecars (fail closed, never silently reset); a corrupt
     sidecar fails loud.
 10. Determinism: identical sequences produce byte-identical
     sidecars.
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
from calibos_mind.deferrals import (
    BASE_PRIORITY,
    DeferralRefused,
    DeferralTracker,
    draw_revisit_tick,
    priority_for,
    weariness,
)


# -- helpers ----------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="deferrals-test-"))


def _tracker(tmp=None):
    return DeferralTracker((tmp or _tmp()) / "deferrals.json")


def _sidecar_data(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _needs(**over):
    """Full need dict; override per case (friction reads focus/fatigue)."""
    d = {"energy": 0.95, "fatigue": 0.05, "hunger": 0.05, "thirst": 0.05,
         "comfort": 0.95, "pain": 0.0, "warmth": 0.9, "restlessness": 0.05,
         "curiosity": 0.10, "loneliness": 0.05, "safety": 0.9, "focus": 0.9,
         "satisfaction": 0.9}
    d.update(over)
    return d


def _init_store():
    """Fresh synthetic store via the CLI (paths already redirected)."""
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _pin_needs(subject, **over):
    """Commit a need dict to the store so a fresh subject reads it."""
    with subject._transaction():
        subject.engine.state.needs = dict(_needs(**over))


def _queue_prompt(text="think about the garden"):
    """Queue one prompt exactly the way `mind queue` does (clock
    wired, so the payload carries queue-time provenance). Returns
    the prompt id."""
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


# -- fitness: weariness + gate (pure) ----------------------------------------

def test_weariness_is_body_or_load_whichever_is_higher():
    """friction.weariness on the body, load from open items; max wins."""
    rested = _needs(focus=0.9, fatigue=0.05)
    assert weariness(rested) == pytest.approx(0.1)
    exhausted = _needs(focus=0.1, fatigue=0.95)
    assert weariness(exhausted) == pytest.approx(0.95)
    # Five open items alone lift a rested body to 0.5.
    assert weariness(rested, open_commitments=3,
                     open_expectations=1, inbox_depth=1) == pytest.approx(0.5)
    # Ten open items saturate the load term at 1.0.
    assert weariness(rested, open_commitments=10) == pytest.approx(1.0)
    # A worn body is not rescued by an empty plate.
    assert weariness(exhausted, 0, 0, 0) == pytest.approx(0.95)


def test_gate_allows_low_urgency_at_high_weariness_only():
    """decide(): low urgency + weariness >= gate allows; anything
    else refuses, with a reason. High urgency never defers."""
    tracker = _tracker()
    allowed, _reason, gate = tracker.decide(
        bucket_name="work", urgency="low", weariness_value=0.95,
        defer_count=0, now_tick=0)
    assert allowed
    assert gate == pytest.approx(0.55)
    allowed, reason, _gate = tracker.decide(
        bucket_name="work", urgency="low", weariness_value=0.1,
        defer_count=0, now_tick=0)
    assert not allowed
    assert "below the work gate" in reason
    allowed, reason, _gate = tracker.decide(
        bucket_name="work", urgency="high", weariness_value=0.95,
        defer_count=0, now_tick=0)
    assert not allowed
    assert "high urgency" in reason
    # Personal is stricter than work: same weariness, different answer.
    allowed, _reason, gate = tracker.decide(
        bucket_name="personal", urgency="low", weariness_value=0.6,
        defer_count=0, now_tick=0)
    assert not allowed
    assert gate == pytest.approx(0.70)
    with pytest.raises(ValueError, match="no bucket"):
        tracker.decide(bucket_name="nope", urgency="low",
                       weariness_value=0.95, defer_count=0, now_tick=0)
    with pytest.raises(ValueError, match="unknown urgency"):
        tracker.decide(bucket_name="work", urgency="urgent",
                       weariness_value=0.95, defer_count=0, now_tick=0)


# -- fitness 6: jitter is real ------------------------------------------------

def test_revisit_times_inside_window_and_varied():
    """Revisit draws fall inside the bucket's window (4-24h -> 2-12
    ticks) and vary across draws: jitter, not a fixed offset."""
    tracker = _tracker()
    window = tracker.buckets["work"]["revisit_window"]
    assert window == [4, 24]
    offsets = [draw_revisit_tick("work", "prompt-0001", 1, tick, window) - tick
               for tick in range(10)]
    assert all(2 <= o <= 12 for o in offsets)
    assert len(set(offsets)) >= 2
    # Deterministic on replay: the same inputs draw the same tick.
    assert draw_revisit_tick("work", "prompt-0001", 1, 3, window) == \
        draw_revisit_tick("work", "prompt-0001", 1, 3, window)


# -- fitness 4 + 5: escalation and forgotten feedback (pure) -------------------

def test_repeat_defers_escalate_priority_and_gate():
    """An item deferred 3 times has strictly higher priority and a
    strictly stricter gate on the 4th defer than on the 1st."""
    tracker = _tracker()
    bucket = tracker.buckets["work"]
    assert priority_for(bucket, 0) == pytest.approx(BASE_PRIORITY)
    assert priority_for(bucket, 3) > priority_for(bucket, 0)
    assert tracker.effective_gate("work", 3, 0) \
        > tracker.effective_gate("work", 0, 0)
    # The gate itself, through defer(): three defers at high weariness.
    item = None
    for i in range(3):
        item = tracker.defer("prompt-0001", "work", "the garden question",
                             "low", 0.95, tick=i, reminder_id=f"rem-{i}")
    assert item["defer_count"] == 3
    assert item["priority"] > BASE_PRIORITY
    # The 4th defer faces a strictly stricter gate than the 1st did.
    gate_first = tracker.effective_gate("work", 0, 3)
    gate_fourth = tracker.effective_gate("work", 3, 3)
    assert gate_fourth > gate_first
    assert gate_fourth == pytest.approx(0.55 + 0.1)


def test_forgotten_items_make_a_bucket_defer_less_readily():
    """Same weariness, same prompt: a bucket with an item past its
    revisit window unactioned refuses where an identical clean bucket
    allows."""
    clean = _tracker()
    dirty = _tracker()
    dirty.defer("prompt-0001", "work", "the garden question",
                "low", 0.95, tick=0, reminder_id="rem-0")
    # Weariness 0.57 clears the clean work gate (0.55) but not the
    # dirty one (0.55 + one forgotten item at 0.05 = 0.60).
    allowed, _r, _g = clean.decide(
        bucket_name="work", urgency="low", weariness_value=0.57,
        defer_count=0, now_tick=20)
    assert allowed
    allowed, _r, _g = dirty.decide(
        bucket_name="work", urgency="low", weariness_value=0.57,
        defer_count=0, now_tick=20)
    assert not allowed
    # The scan folds the overdue item into `forgotten` exactly once.
    newly = dirty.scan_overdue(20)
    assert [i["prompt_id"] for i in newly] == ["prompt-0001"]
    assert dirty.buckets["work"]["forgotten"] == 1
    assert dirty.scan_overdue(21) == []
    assert dirty.buckets["work"]["forgotten"] == 1
    assert dirty.items["prompt-0001"]["status"] == "overdue"


# -- fitness 1 (pure): defer records the item ---------------------------------

def test_defer_records_item_and_refuses_without_reminder():
    """A defer records the item with its revisit time, count, and
    priority — and a defer with no reminder id is refused outright:
    a defer with no reminder is a bug, not a defer."""
    tracker = _tracker()
    item = tracker.defer("prompt-0001", "personal", "call Maya back",
                         "low", 0.95, tick=5, reminder_id="rem-abc")
    assert item["prompt_id"] == "prompt-0001"
    assert item["bucket"] == "personal"
    assert item["defer_count"] == 1
    assert item["status"] == "deferred"
    assert 5 + 2 <= item["revisit_tick"] <= 5 + 12
    assert item["reminder_id"] == "rem-abc"
    assert item["history"][0]["action"].startswith("deferred to personal")
    with pytest.raises(ValueError, match="no reminder"):
        tracker.defer("prompt-0002", "work", "x", "low", 0.95,
                      tick=5, reminder_id=None)
    with pytest.raises(ValueError, match="no bucket"):
        tracker.defer("prompt-0002", "nope", "x", "low", 0.95,
                      tick=5, reminder_id="rem-x")
    with pytest.raises(DeferralRefused):
        tracker.defer("prompt-0002", "work", "x", "low", 0.1,
                      tick=5, reminder_id="rem-x")
    assert "prompt-0002" not in tracker.items


# -- fitness 7: revisit ritual --------------------------------------------------

def test_revisit_surfaces_random_thought_and_remembering_pass():
    """revisit(): a random thought drawn from the bucket's contents,
    plus the full contents for the remembering pass. Deterministic
    on replay; varied across buckets."""
    tracker = _tracker()
    tracker.defer("prompt-0001", "work", "the garden question",
                  "low", 0.95, tick=0, reminder_id="rem-0")
    tracker.defer("prompt-0002", "work", "the invoice question",
                  "low", 0.95, tick=0, reminder_id="rem-1")
    tracker.defer("prompt-0003", "personal", "call Maya back",
                  "low", 0.95, tick=0, reminder_id="rem-2")
    report = tracker.revisit(0)
    work = report["buckets"]["work"]
    assert {i["prompt_id"] for i in work["items"]} == \
        {"prompt-0001", "prompt-0002"}
    assert work["random_thought"]["prompt_id"] in \
        {"prompt-0001", "prompt-0002"}
    assert report["buckets"]["personal"]["random_thought"]["prompt_id"] \
        == "prompt-0003"
    # Deterministic: an identical tracker revisits to the same thought.
    twin = _tracker()
    twin.defer("prompt-0001", "work", "the garden question",
               "low", 0.95, tick=0, reminder_id="rem-0")
    twin.defer("prompt-0002", "work", "the invoice question",
               "low", 0.95, tick=0, reminder_id="rem-1")
    twin.defer("prompt-0003", "personal", "call Maya back",
               "low", 0.95, tick=0, reminder_id="rem-2")
    assert twin.revisit(0) == report
    # An empty bucket surfaces no thought and an empty remembering pass.
    tracker.settle("prompt-0003", "answered", tick=0)
    report2 = tracker.revisit(0, "personal")
    assert report2["buckets"]["personal"]["items"] == []
    assert report2["buckets"]["personal"]["random_thought"] is None
    with pytest.raises(ValueError, match="no bucket"):
        tracker.revisit(0, "nope")


# -- fitness 3: bucket operations, self-directed -------------------------------

def test_bucket_add_rename_set_remove_with_decision_log():
    """Categories are the mind's own decision: every add/rename/set/
    remove is recorded in the decision log with its reason, and
    removal never silently drops an item."""
    tracker = _tracker()
    tracker.add_bucket("macrame", 0, reason="I keep setting aside knots")
    assert "macrame" in tracker.buckets
    assert tracker.decisions[-1]["action"] == "add"
    assert tracker.decisions[-1]["reason"] == "I keep setting aside knots"
    tracker.defer("prompt-0001", "macrame", "which knot next",
                  "low", 0.95, tick=0, reminder_id="rem-0")
    tracker.rename_bucket("macrame", "knots", 1,
                          reason="macrame is the craft, knots the question")
    assert "knots" in tracker.buckets
    assert tracker.items["prompt-0001"]["bucket"] == "knots"
    assert tracker.decisions[-1]["action"] == "rename"
    tracker.set_bucket("knots", 2, reason="knots can wait longer",
                       gate=0.8)
    assert tracker.buckets["knots"]["gate"] == pytest.approx(0.8)
    assert tracker.decisions[-1]["action"] == "set"
    # Remove with reassign: the item moves, nothing lost.
    moved = tracker.remove_bucket("knots", 3, reassign_to="personal",
                                  reason="one pile is enough")
    assert [i["prompt_id"] for i in moved] == ["prompt-0001"]
    assert tracker.items["prompt-0001"]["bucket"] == "personal"
    assert tracker.items["prompt-0001"]["status"] == "deferred"
    assert tracker.releases == []
    assert tracker.decisions[-1]["action"] == "remove"
    # Remove without reassign: released, with a written record.
    released = tracker.remove_bucket("personal", 4,
                                     reason="personal pile goes away")
    assert [i["prompt_id"] for i in released] == ["prompt-0001"]
    assert tracker.items["prompt-0001"]["status"] == "released"
    assert len(tracker.releases) == 1
    assert tracker.releases[0]["prompt_id"] == "prompt-0001"
    assert tracker.releases[0]["bucket"] == "personal"
    assert "removed" in tracker.releases[0]["reason"]
    with pytest.raises(ValueError, match="already exists"):
        tracker.add_bucket("work", 5)
    with pytest.raises(ValueError, match="no bucket"):
        tracker.remove_bucket("nope", 5)
    with pytest.raises(ValueError, match="unknown bucket config"):
        tracker.add_bucket("other", 5, bogus=1.0)
    with pytest.raises(ValueError, match="gate"):
        tracker.set_bucket("work", 5, gate=1.5)


def test_bucket_removal_never_recloses_a_closed_item():
    """A bucket removal touches open items only. A closed item keeps
    its verdict, gains a history note, and — without a reassign —
    keeps its old bucket name as history (validation still loads
    the sidecar)."""
    tracker = _tracker()
    tracker.defer("prompt-0001", "work", "the garden question",
                  "low", 0.95, tick=0, reminder_id="rem-0")
    tracker.defer("prompt-0002", "work", "the invoice question",
                  "low", 0.95, tick=0, reminder_id="rem-1")
    tracker.settle("prompt-0001", "answered", tick=1)
    released = tracker.remove_bucket("work", 2, reason="work pile goes")
    assert [i["prompt_id"] for i in released] == ["prompt-0002"]
    assert tracker.items["prompt-0001"]["status"] == "answered"
    assert tracker.items["prompt-0001"]["bucket"] == "work"
    assert len(tracker.releases) == 1
    # The sidecar reloads cleanly with the closed item naming a
    # removed bucket; the open item is gone from work.
    tracker.save()
    reloaded = DeferralTracker(tracker.path)
    assert reloaded.items["prompt-0001"]["status"] == "answered"
    assert reloaded.items["prompt-0002"]["status"] == "released"


def test_settle_closes_items_with_a_record():
    """settle(): answered/released close the item with history; a
    release writes the release log; closing twice refuses."""
    tracker = _tracker()
    tracker.defer("prompt-0001", "work", "the garden question",
                  "low", 0.95, tick=0, reminder_id="rem-0")
    tracker.defer("prompt-0002", "work", "the invoice question",
                  "low", 0.95, tick=0, reminder_id="rem-1")
    closed = tracker.settle("prompt-0001", "answered", tick=3,
                            note="thought it through")
    assert closed["status"] == "answered"
    assert closed["history"][-1]["action"] == "answered"
    closed = tracker.settle("prompt-0002", "released", tick=4,
                            note="not mine to carry")
    assert closed["status"] == "released"
    assert len(tracker.releases) == 1
    assert tracker.releases[0]["reason"] == "not mine to carry"
    with pytest.raises(ValueError, match="already answered"):
        tracker.settle("prompt-0001", "answered", tick=5)
    with pytest.raises(ValueError, match="no deferred item"):
        tracker.settle("prompt-9999", "answered", tick=5)
    with pytest.raises(ValueError, match="settle action"):
        tracker.settle("prompt-0001", "ignored", tick=5)


# -- fitness 9 + 10: corruption, determinism -----------------------------------

def test_corrupt_sidecar_fails_loud():
    """Invalid JSON, a non-dict top level, an item naming a missing
    bucket, and a malformed decision entry all raise ValueError —
    never a silent reset or a quiet zero."""
    root = _tmp()
    path = root / "deferrals.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt deferrals sidecar"):
        DeferralTracker(path)
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="not an object"):
        DeferralTracker(path)
    path.unlink()
    tracker = _tracker(root)
    tracker.defer("prompt-0001", "work", "x", "low", 0.95, tick=0,
                  reminder_id="rem-0")
    tracker.save()
    data = _sidecar_data(path)
    data["items"]["prompt-0001"]["bucket"] = "nope"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="missing bucket"):
        DeferralTracker(path)
    data = _sidecar_data(path)
    data["items"]["prompt-0001"]["bucket"] = "work"
    data["decisions"] = [{"action": "explode", "bucket": "work",
                          "detail": "", "reason": "", "tick": 0}]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="decision"):
        DeferralTracker(path)


def test_determinism_identical_sequences_byte_identical_sidecars():
    """Two trackers run through the same operations with the same
    reminder ids produce byte-identical sidecars (seeded jitter,
    sorted keys, fixed form)."""
    def run(root):
        tracker = DeferralTracker(root / "deferrals.json")
        tracker.add_bucket("macrame", 0, reason="knots")
        tracker.defer("prompt-0001", "work", "the garden question",
                      "low", 0.95, tick=0, reminder_id="rem-0")
        tracker.defer("prompt-0002", "macrame", "which knot next",
                      "low", 0.95, tick=0, reminder_id="rem-1")
        tracker.defer("prompt-0001", "work", "the garden question",
                      "low", 0.95, tick=1, reminder_id="rem-2")
        tracker.revisit(20)
        tracker.settle("prompt-0002", "answered", tick=21)
        tracker.save()
        return (root / "deferrals.json").read_bytes()

    assert run(_tmp()) == run(_tmp())


# -- fitness 1 + 2: the CLI third option ----------------------------------------

def test_cli_defer_mints_exactly_one_reminder_naming_item_bucket_time():
    """`mind defer` on a low-urgency prompt at high weariness: the
    prompt leaves the inbox, exactly one reminder commitment is
    minted naming the item, the bucket, and the revisit time, due at
    that tick, and the sidecar item points at it."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    before = len(_open_commitments(subject))
    rc, out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0, out
    assert "deferred to work (#1)" in out
    assert list(cli.INBOX.glob("prompt-*.json")) == []
    subject = cli._subject()
    open_now = _open_commitments(subject)
    assert len(open_now) == before + 1
    reminder = open_now[-1]
    assert pid in reminder.description
    assert "work" in reminder.description
    assert f"tick {reminder.due_tick}" in reminder.description
    data = _sidecar_data(cli.DEFERRALS)
    item = data["items"][pid]
    assert item["status"] == "deferred"
    assert item["bucket"] == "work"
    assert item["revisit_tick"] == reminder.due_tick
    assert item["reminder_id"] == reminder.id
    assert item["defer_count"] == 1
    assert reminder.importance == pytest.approx(item["priority"])


def test_cli_high_urgency_and_low_weariness_refuse_and_keep_prompt():
    """High urgency is never deferred on weariness alone, and low
    weariness refuses a low-urgency defer too. In both cases the
    prompt stays in the inbox, no commitment is minted, no item
    is recorded."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("urgent: the client call")
    rc, out = _defer(pid, bucket="work", urgency="high")
    assert rc == 1
    assert "high urgency" in out
    assert len(list(cli.INBOX.glob("prompt-*.json"))) == 1
    assert _open_commitments(cli._subject()) == []
    if cli.DEFERRALS.exists():
        assert pid not in _sidecar_data(cli.DEFERRALS)["items"]
    # Low weariness refuses the same prompt.
    _pin_needs(subject, focus=0.9, fatigue=0.05)
    rc, out = _defer(pid, bucket="work", urgency="low")
    assert rc == 1
    assert "below the work gate" in out
    assert len(list(cli.INBOX.glob("prompt-*.json"))) == 1
    assert _open_commitments(cli._subject()) == []


def test_cli_redefer_escalates_and_settles_old_reminder():
    """Re-deferring the same item increments its count, raises its
    priority after the threshold, draws a new revisit time, and
    settles the old reminder as kept ('re-deferred') — exactly one
    open reminder per item at any time."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    for _ in range(3):
        rc, out = _defer(pid, bucket="work", urgency="low")
        assert rc == 0, out
    subject = cli._subject()
    item = _sidecar_data(cli.DEFERRALS)["items"][pid]
    assert item["defer_count"] == 3
    assert item["priority"] > BASE_PRIORITY
    reminders = [c for c in _commitments(subject).values()
                 if pid in c.description]
    assert len(reminders) == 3
    assert len([c for c in reminders if c.status in {"open", "overdue"}]) == 1
    kept = [c for c in reminders if c.status == "kept"]
    assert len(kept) == 2
    assert all(c.outcome.startswith("re-deferred") for c in kept)
    # The 4th defer still works at this weariness but faces the
    # escalated gate (checked pure in the module tests).
    rc, out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0, out
    assert _sidecar_data(cli.DEFERRALS)["items"][pid]["defer_count"] == 4


def test_cli_settle_and_bucket_remove_release_reminders():
    """`mind settle` closes the item and settles its reminder; a
    bucket removal without --to releases the item with a written
    record and releases the reminder commitment."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    rc, out = _run(cli.cmd_settle, argparse.Namespace(
        id=pid, answered=True, released=False, note="thought it through"))
    assert rc == 0, out
    assert "settled as answered" in out
    item = _sidecar_data(cli.DEFERRALS)["items"][pid]
    assert item["status"] == "answered"
    subject = cli._subject()
    reminder = next(c for c in _commitments(subject).values()
                    if c.id == item["reminder_id"])
    assert reminder.status == "kept"
    assert "thought it through" in reminder.outcome
    # Settling twice refuses.
    rc, out = _run(cli.cmd_settle, argparse.Namespace(
        id=pid, answered=True, released=False, note=""))
    assert rc == 1
    # A second item, released by bucket removal.
    pid2 = _queue_prompt("the invoice question")
    rc, _out = _defer(pid2, bucket="work", urgency="low")
    assert rc == 0
    rc, out = _run(cli.cmd_bucket, argparse.Namespace(
        action="remove", name="work", to=None, reason="work pile goes away"))
    assert rc == 0, out
    assert "released with a written record" in out
    data = _sidecar_data(cli.DEFERRALS)
    # The already-answered first item keeps its verdict: a bucket
    # removal never re-closes a closed item.
    assert data["items"][pid]["status"] == "answered"
    assert data["items"][pid2]["status"] == "released"
    assert len(data["releases"]) == 1
    assert data["releases"][0]["prompt_id"] == pid2
    assert data["decisions"][-1]["action"] == "remove"
    assert data["decisions"][-1]["reason"] == "work pile goes away"
    subject = cli._subject()
    reminder2 = next(c for c in _commitments(subject).values()
                     if c.id == data["items"][pid2]["reminder_id"])
    assert reminder2.status == "released"


def test_cli_bucket_add_rename_set_and_reassign():
    """`mind bucket` add/rename/set/reassign through the CLI, with
    the decision log recording each of the mind's own choices."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("which knot next")
    rc, out = _run(cli.cmd_bucket, argparse.Namespace(
        action="add", name="macrame", gate=None, window=None,
        escalation_threshold=None, escalation_step=None,
        forgotten_penalty=None, forgotten_penalty_cap=None,
        reason="I keep setting aside knots"))
    assert rc == 0, out
    rc, _out = _defer(pid, bucket="macrame", urgency="low")
    assert rc == 0
    rc, out = _run(cli.cmd_bucket, argparse.Namespace(
        action="rename", name="macrame", new="knots",
        reason="macrame is the craft, knots the question"))
    assert rc == 0, out
    assert _sidecar_data(cli.DEFERRALS)["items"][pid]["bucket"] == "knots"
    rc, out = _run(cli.cmd_bucket, argparse.Namespace(
        action="set", name="knots", gate=0.8, window=None,
        escalation_threshold=None, escalation_step=None,
        forgotten_penalty=None, forgotten_penalty_cap=None,
        reason="knots can wait longer"))
    assert rc == 0, out
    assert _sidecar_data(cli.DEFERRALS)["buckets"]["knots"]["gate"] == 0.8
    rc, out = _run(cli.cmd_bucket, argparse.Namespace(
        action="remove", name="knots", to="personal",
        reason="one pile is enough"))
    assert rc == 0, out
    assert "moved to 'personal'" in out
    data = _sidecar_data(cli.DEFERRALS)
    assert data["items"][pid]["bucket"] == "personal"
    assert data["items"][pid]["status"] == "deferred"
    assert [d["action"] for d in data["decisions"]] == \
        ["add", "rename", "set", "remove"]


# -- fitness 8: read-only + dream isolation ------------------------------------

def test_read_only_commands_and_dream_never_touch_bucket_state():
    """drift, status, review, inbox, goals, buckets, and the dream
    command leave deferrals.json byte-identical."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    before = cli.DEFERRALS.read_bytes()
    for cmd, args in (
            (cli.cmd_drift, argparse.Namespace(window=10)),
            (cli.cmd_status, argparse.Namespace(raw=False)),
            (cli.cmd_review, argparse.Namespace(n=5)),
            (cli.cmd_inbox, argparse.Namespace(id=None)),
            (cli.cmd_goals, argparse.Namespace()),
            (cli.cmd_buckets, argparse.Namespace()),
    ):
        rc, _out = _run(cmd, args)
        assert rc == 0
        assert cli.DEFERRALS.read_bytes() == before
    rc, _out = _run(cli.cmd_dream, argparse.Namespace(ticks=1))
    # A dream may refuse on pending events; either way the sidecar
    # must be untouched.
    assert cli.DEFERRALS.read_bytes() == before


# -- fitness 9: init --force wipes ----------------------------------------------

def test_init_force_wipes_deferral_state_including_corrupt():
    """`mind init --force` wipes the deferral sidecar — including a
    corrupt one, which must not brick the reseed — and an orphaned
    sidecar without a store refuses a fresh init."""
    subject = _init_store()
    _pin_needs(subject, focus=0.1, fatigue=0.95)
    pid = _queue_prompt("think about the garden")
    rc, _out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0
    assert cli.DEFERRALS.exists()
    cli.DEFERRALS.write_text("{corrupt", encoding="utf-8")
    rc, _out = _run(cli.cmd_init, argparse.Namespace(force=True))
    assert rc == 0
    assert not cli.DEFERRALS.exists()
    # Orphan refusal: sidecar present, store gone, no --force.
    cli.DEFERRALS.write_text(json.dumps(
        {"buckets": {}, "items": {}, "releases": [], "decisions": []}),
        encoding="utf-8")
    cli.DB.unlink()
    rc, out = _run(cli.cmd_init, argparse.Namespace(force=False))
    assert rc == 1
    assert "refusing to init" in out
    assert cli.DEFERRALS.exists()
