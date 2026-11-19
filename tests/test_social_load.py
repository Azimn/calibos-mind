"""Tests for the social-saturation load term (Domain 25).

Spec: research/specs/2026-11-18-social-saturation-load.md
All fixtures on /tmp — the live store is never touched (conftest
autouse fixture redirects every cli store path, including
deferrals.json).

The mechanism: calibos_mind/deferrals.py — a pure `social_load`
term (contact events in the trailing 50 ticks, 0.0 at the ordinary
rhythm, 1.0 near the historical max) composed into `weariness()` as
a third term, computed live from store records by
`cli._deferral_weariness` at defer time, and shown by `mind status`.
No new sidecar, no store write, no new refusal path.

Fitness functions under test:
  1. Zero contact in the window -> term exactly 0.0; `weariness()`
     with `social=0.0` is identical to the no-social call.
  2. Calibration: 10 events -> 0.0; 11 -> 1/12; 16 -> 0.5;
     22 -> 1.0; 30 -> 1.0 (saturates, never exceeds 1.0).
  3. Boundaries: event at tick-50 excluded, tick-49 included, tick
     included, tick+1 excluded (window 50).
  4. Only contact counts: perception/memory/temporal/interoception
     records and non-answered thoughts (generated_by `voluntary`,
     `wake`, `cognition`, None) do not count; `source == 'social'`
     counts; `generated_by` starting `answered:` counts.
  5. Composition: `weariness()` returns the max of the three terms
     — body 0.9 with social 0.3 -> 0.9; body 0.2 with social 0.58 ->
     0.58; social never lowers weariness.
  6. End-to-end gate: on a synthetic store, a low-urgency defer at
     high social load passes the gate where the identical defer at
     zero social load is refused (through `_deferral_weariness` /
     `cmd_defer`, with body weariness and open load held low so the
     social term is the deciding input).
  7. High urgency never deferred on social load alone, through the
     same path.
  8. Isolation: read-only sweep + dream ticks create no sidecar and
     leave the store payload identical; byte-identical replay of
     read-only output. (Scoped per the regression genome: CLI
     construction itself rewrites the SQLite payload row, and dream
     ticks mint a cognition prompt by design — so the assertions are
     file-set + inspect-payload + pre-existing-records identity,
     not raw mind.db bytes across a CLI construction.)
  9. Status line: shows term and count; empty store -> 0.0 / 0 events.
 10. Full suite green (run, not a test here).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.deferrals import (
    SOCIAL_LOAD_BASELINE,
    SOCIAL_LOAD_SCALE,
    SOCIAL_LOAD_WINDOW,
    social_contact_count,
    social_load,
    weariness,
)


# -- helpers ----------------------------------------------------------------

def _run(cmd, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd(args)
    return rc, buf.getvalue()


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
    """Queue one prompt exactly the way `mind queue` does. Returns id."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.cmd_queue(argparse.Namespace(
            prompt=text, source="invitation", experience=None,
            from_person=None))
    assert rc == 0
    files = sorted(cli.INBOX.glob("prompt-*.json"))
    assert len(files) == 1
    return files[0].stem


def _defer(pid, bucket="work", urgency="low"):
    return _run(cli.cmd_defer, argparse.Namespace(
        id=pid, bucket=bucket, urgency=urgency))


def _inject_contacts(subject, n, tick=0):
    """Inject n answered-prompt thoughts (contact events) at one tick.

    Distinct texts, stamped exactly the way `mind answer` stamps
    them (`answered:<prompt-id>@<tick>`). Injected before any prompt
    is queued, so no queued view can go stale on them.
    """
    for i in range(n):
        subject.inject_thought(
            f"contact thought number {i}, thinking it through",
            trigger_kind="answered",
            generated_by=f"answered:contact-{i}@{tick}")


def _rec(tick, generated_by=None, source="thought"):
    """A stub record dict (what inspect() returns)."""
    return {"tick": tick, "generated_by": generated_by, "source": source}


def _recs_at(tick, n, generated_by="answered:probe@0", source="thought"):
    return [_rec(tick, generated_by, source) for _ in range(n)]


def _snapshot_files(root: Path):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _inspect_json(subject):
    return json.dumps(subject.inspect(), sort_keys=True, default=str)


# -- fitness 1: zero contact ------------------------------------------------

def test_zero_contact_is_zero_and_weariness_default_unchanged():
    """No contact in the window -> term exactly 0.0; `weariness()`
    with `social=0.0` is identical to the no-social call."""
    assert social_load([], 100) == 0.0
    assert social_load(None, 100) == 0.0
    assert social_contact_count([], 100) == 0
    # Non-contact records only: still exactly zero.
    recs = ([_rec(100, "voluntary", "thought")]
            + [_rec(99, None, "memory")]
            + [_rec(98, "cognition", "temporal")])
    assert social_load(recs, 100) == 0.0
    rested = _needs()
    assert weariness(rested) == weariness(rested, social=0.0)
    assert weariness(rested, 3, 2, 1) == weariness(rested, 3, 2, 1,
                                                  social=0.0)
    exhausted = _needs(focus=0.1, fatigue=0.95)
    assert weariness(exhausted) == weariness(exhausted, social=0.0)


# -- fitness 2: calibration --------------------------------------------------

def test_calibration_points():
    """10 -> 0.0; 11 -> 1/12; 16 -> 0.5; 22 -> 1.0; 30 -> 1.0."""
    assert social_load(_recs_at(100, 10), 100) == 0.0
    assert social_load(_recs_at(100, 11), 100) == pytest.approx(1 / 12)
    assert social_load(_recs_at(100, 16), 100) == pytest.approx(0.5)
    assert social_load(_recs_at(100, 22), 100) == pytest.approx(1.0)
    assert social_load(_recs_at(100, 30), 100) == pytest.approx(1.0)
    assert social_load(_recs_at(100, 100), 100) == pytest.approx(1.0)
    # Defaults are the calibrated constants, not guesses.
    assert SOCIAL_LOAD_WINDOW == 50
    assert SOCIAL_LOAD_BASELINE == 10
    assert SOCIAL_LOAD_SCALE == 12


# -- fitness 3: window boundaries -------------------------------------------

def test_window_boundaries_exact():
    """tick-50 excluded, tick-49 included, tick included, tick+1
    excluded (window 50, tick 100)."""
    tick = 100
    assert social_contact_count([_rec(50, "answered:x@50")], tick) == 0
    assert social_contact_count([_rec(51, "answered:x@51")], tick) == 1
    assert social_contact_count([_rec(100, "answered:x@100")], tick) == 1
    assert social_contact_count([_rec(101, "answered:x@101")], tick) == 0
    # Mixed: only the in-window events count.
    recs = [_rec(50, "answered:a@50"), _rec(51, "answered:b@51"),
            _rec(100, "answered:c@100"), _rec(101, "answered:d@101")]
    assert social_contact_count(recs, tick) == 2
    assert social_load(recs, tick) == 0.0  # 2 <= baseline
    # A custom window obeys the same boundary rule.
    assert social_contact_count([_rec(95, "answered:x@95")], tick,
                                window=5) == 0
    assert social_contact_count([_rec(96, "answered:x@96")], tick,
                                window=5) == 1


# -- fitness 4: only contact counts ------------------------------------------

def test_only_contact_counts():
    """Perception/memory/temporal/interoception records and
    non-answered thoughts never count; `source == 'social'` and
    `generated_by` starting `answered:` do."""
    tick = 100
    non_contact = []
    for source in ("perception", "memory", "temporal", "interoception",
                   "thought"):
        non_contact.append(_rec(tick, "voluntary", source))
    for gb in ("voluntary", "wake", "cognition", None,
               "voluntary:inspection", "dream-derived"):
        non_contact.append(_rec(tick, gb, "thought"))
    # "answered-external:" is NOT "answered:" — externally-authored
    # answers are a different provenance, not contact by this stamp.
    non_contact.append(_rec(tick, "answered-external:p1@100", "thought"))
    assert social_contact_count(non_contact, tick) == 0
    contact = [_rec(tick, "answered:prompt-1@100", "thought"),
               _rec(tick, None, "social"),
               _rec(tick, "voluntary", "social")]
    assert social_contact_count(contact, tick) == 3
    # Malformed records never crash: missing/None/non-int tick,
    # missing generated_by/source, non-dict junk shapes.
    malformed = [
        {"generated_by": "answered:x@100", "source": "thought"},
        {"tick": None, "generated_by": "answered:x@100"},
        {"tick": "100", "generated_by": "answered:x@100"},
        {"tick": 100.5, "generated_by": "answered:x@100"},
        {"tick": True, "generated_by": "answered:x@100"},
        {"tick": 100},
        {},
        {"tick": 100, "generated_by": 123, "source": "thought"},
    ]
    assert social_contact_count(malformed, tick) == 0
    assert social_load(malformed, tick) == 0.0
    # A record with an answered stamp but source None still counts:
    # the stamp alone names the contact; source is only consulted
    # for the social-source rule.
    assert social_contact_count(
        [{"tick": 100, "generated_by": "answered:x@100", "source": None}],
        tick) == 1
    # Object records (workspace in-memory shape) read the same as dicts.
    obj_contact = types.SimpleNamespace(
        tick=100, generated_by="answered:obj@100", source="thought")
    obj_noise = types.SimpleNamespace(
        tick=100, generated_by="wake", source="thought")
    obj_bare = types.SimpleNamespace(first_person="no fields at all")
    assert social_contact_count([obj_contact, obj_noise, obj_bare],
                                tick) == 1
    assert social_contact_count([obj_bare], tick) == 0


# -- fitness 5: composition ---------------------------------------------------

def test_weariness_is_max_of_three_terms():
    """Body 0.9 with social 0.3 -> 0.9; body 0.2 with social 0.58 ->
    0.58; social never lowers weariness."""
    exhausted = _needs(focus=0.1, fatigue=0.9)  # body 0.9
    assert weariness(exhausted, social=0.3) == pytest.approx(0.9)
    rested = _needs(focus=0.8, fatigue=0.05)  # body 0.2
    assert weariness(rested, social=0.58) == pytest.approx(0.58)
    assert weariness(rested, social=0.0) == pytest.approx(0.2)
    # Load still composes alongside social: the largest of three wins.
    assert weariness(rested, open_commitments=10,
                     social=0.3) == pytest.approx(1.0)
    assert weariness(rested, open_commitments=5,
                     social=0.3) == pytest.approx(0.5)
    # A social term above 1.0 cannot push weariness past 1.0.
    assert weariness(rested, social=2.0) == pytest.approx(1.0)
    # A negative social term cannot lower weariness.
    assert weariness(exhausted, social=-0.5) == pytest.approx(0.9)


# -- fitness 6: end-to-end gate ----------------------------------------------

def test_gate_refuses_identical_defer_at_zero_social_load():
    """Low-urgency defer at rested body, empty plate, zero contact:
    refused — weariness 0.1 is below the work gate 0.55."""
    subject = _init_store()
    _pin_needs(subject, focus=0.9, fatigue=0.05)
    pid = _queue_prompt("think about the garden")
    assert cli._deferral_weariness(subject) == pytest.approx(0.1)
    rc, out = _defer(pid, bucket="work", urgency="low")
    assert rc == 1
    assert "below the work gate" in out
    assert len(list(cli.INBOX.glob("prompt-*.json"))) == 1


def test_gate_allows_identical_defer_at_high_social_load():
    """Same needs, same bucket, same urgency — but 17 contact events
    in the window: the social term (7/12 ≈ 0.58) clears the work gate
    0.55 where body (0.1) and load (0.1) alone could not."""
    subject = _init_store()
    _pin_needs(subject, focus=0.9, fatigue=0.05)
    _inject_contacts(subject, 17)
    w = cli._deferral_weariness(cli._subject())
    assert w == pytest.approx(7 / 12)
    pid = _queue_prompt("think about the garden")
    rc, out = _defer(pid, bucket="work", urgency="low")
    assert rc == 0, out
    assert "deferred to work (#1)" in out
    assert list(cli.INBOX.glob("prompt-*.json")) == []
    assert pid in json.loads(cli.DEFERRALS.read_text(
        encoding="utf-8"))["items"]


# -- fitness 7: high urgency ---------------------------------------------------

def test_high_urgency_never_deferred_on_social_load_alone():
    """17 contact events in the window, rested body: a high-urgency
    defer is still refused and the prompt stays queued."""
    subject = _init_store()
    _pin_needs(subject, focus=0.9, fatigue=0.05)
    _inject_contacts(subject, 17)
    assert cli._deferral_weariness(cli._subject()) == pytest.approx(7 / 12)
    pid = _queue_prompt("urgent: the client call")
    rc, out = _defer(pid, bucket="work", urgency="high")
    assert rc == 1
    assert "high urgency" in out
    assert len(list(cli.INBOX.glob("prompt-*.json"))) == 1
    if cli.DEFERRALS.exists():
        assert pid not in json.loads(cli.DEFERRALS.read_text(
            encoding="utf-8"))["items"]


# -- fitness 8: isolation -------------------------------------------------------

def test_read_only_sweep_and_dream_create_no_sidecar():
    """drift, status, review, and dream ticks create no deferrals.json
    and leave the store payload identical; read-only output replays
    byte-identically.

    Scoped per the regression genome (CLI construction rewrites the
    SQLite payload row, and dream ticks mint a cognition prompt by
    design): the assertions are the file set across the CLI sweep,
    inspect() payload identity across the sweep, byte-identical
    replay of `mind status`, and pre-existing-record identity plus a
    frozen tick across dream ticks — not raw mind.db bytes across a
    CLI construction.
    """
    subject = _init_store()
    _inject_contacts(subject, 17)
    assert not cli.DEFERRALS.exists()
    before_files = _snapshot_files(cli.BASE)
    before_inspect = _inspect_json(subject)
    status_outs = []
    for cmd, args in (
            (cli.cmd_drift, argparse.Namespace(window=10)),
            (cli.cmd_status, argparse.Namespace(raw=False)),
            (cli.cmd_review, argparse.Namespace(n=5)),
            (cli.cmd_status, argparse.Namespace(raw=True)),
            (cli.cmd_status, argparse.Namespace(raw=False)),
    ):
        rc, out = _run(cmd, args)
        assert rc == 0
        if cmd is cli.cmd_status and not args.raw:
            status_outs.append(out)
    assert not cli.DEFERRALS.exists()
    after_files = _snapshot_files(cli.BASE)
    assert set(after_files) == set(before_files), \
        "read-only commands must not add or remove files"
    assert {k: v for k, v in after_files.items() if k != "mind.db"} == \
        {k: v for k, v in before_files.items() if k != "mind.db"}, \
        "read-only commands must not rewrite any sidecar file"
    # (mind.db bytes are excluded here on purpose: CLI construction
    # itself rewrites the SQLite payload row — the genome's
    # "CLI construction is never mtime-silent" item. The payload
    # identity assertion below is the operation-scoped check.)
    assert _inspect_json(cli._subject()) == before_inspect, \
        "read-only commands must leave the store payload identical"
    assert status_outs[0] == status_outs[1], \
        "status output must replay byte-identically"
    # Dream ticks: no sidecar, tick frozen, pre-existing records
    # byte-identical (the dream may ADD its prompt record; it must
    # not mutate what was already there).
    subject2 = cli._subject()
    tick_before = subject2.engine.state.tick
    recs_before = {r["id"]: r for r in
                   subject2.inspect()["workspace"]["records"]}
    for _ in range(2):
        assert subject2.dream_tick()["action"] == "sleep"
    assert not cli.DEFERRALS.exists()
    assert subject2.engine.state.tick == tick_before
    recs_after = {r["id"]: r for r in
                  subject2.inspect()["workspace"]["records"]}
    for rid, rec in recs_before.items():
        assert recs_after[rid] == rec, \
            f"dream tick mutated pre-existing record {rid}"


# -- fitness 9: status line ------------------------------------------------------

def test_status_line_shows_term_and_count():
    """`mind status` prints the term and the trailing-window count;
    an empty store reports 0.0 / 0 events."""
    _init_store()
    _rc, out = _run(cli.cmd_status, argparse.Namespace(raw=False))
    assert "social load 0.00 (0 contact events / 50 ticks)" in out
    subject = cli._subject()
    _inject_contacts(subject, 17)
    _rc, out = _run(cli.cmd_status, argparse.Namespace(raw=False))
    assert "social load 0.58 (17 contact events / 50 ticks)" in out
    _rc, out = _run(cli.cmd_status, argparse.Namespace(raw=True))
    assert "social load 0.58 (17 contact events / 50 ticks)" in out
