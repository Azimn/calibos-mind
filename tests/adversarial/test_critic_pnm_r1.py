"""CRITIC tests, round 1, for the persistent-near-miss-note mutation
(spec: research/specs/2026-10-28-persistent-near-miss-note.md).

All fixtures live in /tmp — the live store is never touched. These tests
are independent of the builder's test file (own fixtures, own helpers) so
a builder fix-round edit to their file cannot silently change them.

FAILURES EXPECTED on the round-1 code (the verdict is FAIL):
- test_long_run_note_rate_stays_at_signal_level
- test_notes_never_note_themselves
Everything else is expected to PASS and is included to pin the parts of
the work that do hold, so a fix cannot regress them silently.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind.familiarity import (
    PERSISTENT_NEAR_MISS_THRESHOLD,
    FamiliarityTracker,
    is_near_miss_note_text,
    mint_near_miss_note,
    parse_near_miss_note_text,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge


MARKER = "CRITICQUILL-3Q7"

_FILLERS = [
    ("thought", "I keep turning over the chess opening I lost last Tuesday."),
    ("thought", "The neighbor's dog barks in prime numbers, or so it seems."),
    ("thought", "Rain on the skylight makes the kitchen feel like a cave."),
    ("thought", "I should learn to sharpen knives properly this winter."),
    ("memory", "Grandmother's radio played static between the stations."),
    ("memory", "The ferry crossing took exactly as long as the song."),
    ("memory", "We got lost in the market and found the spice alley."),
    ("perception", "The hallway smells faintly of fresh paint today."),
    ("perception", "A moth is circling the desk lamp in slow ellipses."),
    ("perception", "The floorboards creak in a new place by the door."),
    ("interoception", "My shoulders hold yesterday's tension like a debt."),
    ("interoception", "There is a lightness behind my eyes this morning."),
    ("temporal", "The deadline moved again; the week reshuffles itself."),
    ("temporal", "Three days until the visit; the countdown has texture."),
    ("social", "Mara laughed at the joke before I finished telling it."),
    ("social", "The meeting ended early and nobody knew what to do."),
]
_TARGET = ("memory",
           "The lighthouse keeper's log mentioned a storm I never "
           f"witnessed. {MARKER} left no other trace.")


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-pnm-"))


def _lean_subject(tmp: Path) -> CalibosSubject:
    (tmp / "inbox").mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(tmp / "inbox"),
        salience_path=str(tmp / "salience.json"),
        familiarity_path=str(tmp / "familiarity.json"))


def _full_subject(tmp: Path) -> CalibosSubject:
    """Every tracker path on synthetic paths (mirrors cli._subject)."""
    (tmp / "inbox").mkdir(exist_ok=True)
    (tmp / "archive").mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    subject = CalibosSubject(
        str(tmp / "mind.db"), cart, cognition=InboxCognition(tmp / "inbox"),
        salience_path=str(tmp / "salience.json"),
        interoception_path=str(tmp / "interoception.json"),
        familiarity_path=str(tmp / "familiarity.json"),
        ambivalence_path=str(tmp / "ambivalence.json"),
        counterfactual_path=str(tmp / "counterfactuals.json"),
        habits_path=str(tmp / "habits-formed.json"),
        dormancy_path=str(tmp / "authored-dormancy.json"),
        expectation_path=str(tmp / "expectations.json"))
    subject.workspace.availability_path = tmp / "archive" / "availability.json"
    return subject


def _seed(subject, n_targets=1):
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLERS):
            ids[f"F{i}"] = subject._add(source, text).id
        for j in range(n_targets):
            ids[f"T{j}"] = subject._add(
                _TARGET[0], _TARGET[1] + f" (copy {j})").id
    return ids


def _boost(subject, ids, delta=5.0):
    stracker = subject.workspace.salience_tracker
    for i in range(16):
        stracker.add_importance(ids[f"F{i}"], 0, delta)
    stracker.save()


def _notes(subject):
    return [r for r in subject.workspace.records
            if r.source == "temporal" and r.generated_by == "cognition"
            and is_near_miss_note_text(r.first_person)]


def _max_window(mint_ticks, n_ticks, width=100):
    return max(sum(1 for tk in mint_ticks if s <= tk < s + width)
               for s in range(0, n_ticks))


def _patched_cli(tmp: Path):
    """Minimal patched-CLI redirect (complete set) for read-only tests."""
    saved = (cli.BASE, cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE,
             cli.INTEROCEPTION, cli.FAMILIARITY, cli.AMBIVALENCE,
             cli.COUNTERFACTUAL, cli.HABITS, cli.DORMANCY, cli.PROVENANCE,
             cli.EXPECTATIONS, cli.ARCHIVE, cli.PROPOSALS, cli.WAKE_LIVENESS,
             cli.ABLATION_SALT_FILE, cli.ABLATION_LOG, cli._subject)
    (tmp / "inbox").mkdir(exist_ok=True)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(cli.INBOX)
        cart = load_cartridge(cli.CARTRIDGE_PATH)
        subject = CalibosSubject(
            str(cli.DB), cart, cognition=provider,
            salience_path=str(cli.SALIENCE),
            familiarity_path=str(cli.FAMILIARITY))
        subject.workspace.availability_path = cli.ARCHIVE / "availability.json"
        return subject

    cli.BASE = tmp
    cli.DB = tmp / "mind.db"
    cli.INBOX = tmp / "inbox"
    cli.DREAMS = tmp / "dreams"
    cli.SALIENCE = tmp / "salience.json"
    cli.INTEROCEPTION = tmp / "interoception.json"
    cli.FAMILIARITY = tmp / "familiarity.json"
    cli.AMBIVALENCE = tmp / "ambivalence.json"
    cli.COUNTERFACTUAL = tmp / "counterfactuals.json"
    cli.HABITS = tmp / "habits-formed.json"
    cli.DORMANCY = tmp / "authored-dormancy.json"
    cli.PROVENANCE = tmp / "provenance.json"
    cli.EXPECTATIONS = tmp / "expectations.json"
    cli.ARCHIVE = tmp / "archive"
    cli.PROPOSALS = tmp / "proposals"
    cli.WAKE_LIVENESS = tmp / "wake_liveness.json"
    cli.ABLATION_SALT_FILE = tmp / "ablation_salt"
    cli.ABLATION_LOG = tmp / "ablation_log.jsonl"
    cli._subject = make_subject
    return saved


def _restore_cli(saved):
    (cli.BASE, cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE,
     cli.INTEROCEPTION, cli.FAMILIARITY, cli.AMBIVALENCE,
     cli.COUNTERFACTUAL, cli.HABITS, cli.DORMANCY, cli.PROVENANCE,
     cli.EXPECTATIONS, cli.ARCHIVE, cli.PROPOSALS, cli.WAKE_LIVENESS,
     cli.ABLATION_SALT_FILE, cli.ABLATION_LOG, cli._subject) = saved


# -- RETIRED 2026-10-28 by design-lead adjudication (critic round 2) ------
# Former test_long_run_note_rate_stays_at_signal_level asserted a global
# cap (worst 100-tick window <= 5 notes) on a fixture that synchronizes
# ~10 genuine episodes: 16 boosted fillers hold ~10 genuine records below
# the view cut from tick 0, and all cross threshold together at ticks
# 10-16. That premise contradicts the spec's per-record-per-episode
# mechanism (one note per episode, no global cap) and mismeasures the
# spec's revert signal, which targets duplicates, cascades, and
# steady-state noise. Proven test-premise error (regression genome):
# round 1 measured 194 notes / worst window 101 / 181 cascade; round 2
# measures 12 notes / worst window 10 / 0 cascade, every note at streak
# exactly 10 about a distinct genuine record, steady-state worst window
# (ticks 50-200) 2. The corrected premise lives in
# tests/adversarial/test_critic_pnm_r2.py::test_long_run_corrected_premise
# (cascade = 0, one note per record at streak 10, steady-state window
# <= 5), which passes. This retired test is NOT deleted silently: its
# assertion is superseded, not weakened away, and the replacement test
# governs the long-run rate from here on.


def test_notes_never_note_themselves():
    """A note is a report about a genuine record, never about another
    report: notes must be excluded from near-miss candidacy, or the
    mechanism feeds its own input without bound (depth 19 measured in
    200 lean ticks, 38 in 400 full ticks)."""
    tmp = _tmp()
    subject = _lean_subject(tmp)
    ids = _seed(subject)
    _boost(subject, ids)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(60):
            cli._run_tick(subject)
    notes = _notes(subject)
    note_ids = {r.id for r in notes}
    cascade = [r for r in notes
               if parse_near_miss_note_text(r.first_person)["record_id"]
               in note_ids]
    assert cascade == [], (
        f"{len(cascade)} of {len(notes)} notes are notes about notes, "
        f"e.g. {cascade[0].first_person if cascade else ''}")


# -- expected PASS: boundary read-only / dream -------------------------------

def test_readonly_at_unnoted_threshold_boundary_mints_nothing():
    """Streak already AT 10 with no guard (pre-mutation sidecar shape):
    read-only commands must still mint nothing and leave the sidecar
    byte-identical — the boundary the builder's streak-9 test does not
    cover."""
    tmp = _tmp()
    saved = _patched_cli(tmp)
    try:
        subject = cli._subject()
        ids = _seed(subject)
        t = ids["T0"]
        sidecar = tmp / "familiarity.json"
        sidecar.write_text(json.dumps({"streaks": {t: 10}, "noted": {}}),
                           encoding="utf-8")
        before = sidecar.read_bytes()
        before_count = len(subject.workspace.records)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_drift(argparse.Namespace(window=30))
            cli.cmd_status(argparse.Namespace(raw=False))
            cli.cmd_review(argparse.Namespace(n=5))
            cli.cmd_inbox(argparse.Namespace(id=None))
            cli.cmd_consolidate(argparse.Namespace(
                list=True, accept=None, reject=False, quarantine=False))
        assert sidecar.read_bytes() == before
        fresh = cli._subject()
        assert len(fresh.workspace.records) == before_count
        assert _notes(fresh) == []
    finally:
        _restore_cli(saved)


def test_dream_ticks_at_unnoted_threshold_boundary_mint_nothing():
    tmp = _tmp()
    subject = _lean_subject(tmp)
    ids = _seed(subject)
    t = ids["T0"]
    sidecar = tmp / "familiarity.json"
    sidecar.write_text(json.dumps({"streaks": {t: 10}, "noted": {}}),
                       encoding="utf-8")
    before = sidecar.read_bytes()
    tick_before = subject.engine.state.tick
    for _ in range(3):
        subject.dream_tick()
    assert sidecar.read_bytes() == before, \
        "dream ticks must not touch the familiarity sidecar"
    assert subject.engine.state.tick == tick_before, \
        "dream ticks must not advance the store tick"
    assert _notes(subject) == []


# -- expected PASS: mint / multi-due / old-sidecar / content ban -------------

def test_mint_idempotent_within_a_single_transaction():
    """Docstring claim: mints earlier in the SAME transaction are visible
    to the idempotency check. The builder's test covers across
    transactions only."""
    tmp = _tmp()
    subject = _lean_subject(tmp)
    event = {"record_id": "experience-18", "streak": 10, "tick": 47}
    with subject._transaction():
        first = mint_near_miss_note(subject, event)
        second = mint_near_miss_note(subject, event)
    assert first == second
    assert len(_notes(subject)) == 1


def test_three_targets_note_once_each_on_shared_threshold_tick():
    tmp = _tmp()
    subject = _lean_subject(tmp)
    ids = _seed(subject, n_targets=3)
    _boost(subject, ids)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(10):
            cli._run_tick(subject)
    notes = _notes(subject)
    by_target = {}
    for r in notes:
        by_target.setdefault(
            parse_near_miss_note_text(r.first_person)["record_id"], []).append(r)
    for j in range(3):
        got = by_target.get(ids[f"T{j}"], [])
        assert len(got) == 1, (f"T{j}", [r.first_person for r in got])
    keys = [(parse_near_miss_note_text(r.first_person)["record_id"],
             parse_near_miss_note_text(r.first_person)["tick"]) for r in notes]
    assert len(keys) == len(set(keys))


def test_pre_mutation_streak_eighteen_notes_exactly_once():
    """Old sidecar with a streak already past threshold: the first waking
    tick notes exactly once (streak value = incremented streak), and the
    next tick adds no duplicate."""
    tmp = _tmp()
    subject = _lean_subject(tmp)
    ids = _seed(subject)
    _boost(subject, ids)
    t = ids["T0"]
    (tmp / "familiarity.json").write_text(
        json.dumps({"streaks": {t: 18}, "noted": {}}), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        cli._run_tick(subject)
    notes = [r for r in _notes(subject)
             if parse_near_miss_note_text(r.first_person)["record_id"] == t]
    assert len(notes) == 1, [r.first_person for r in notes]
    assert parse_near_miss_note_text(notes[0].first_person)["streak"] == 19
    with contextlib.redirect_stdout(io.StringIO()):
        cli._run_tick(subject)
    notes2 = [r for r in _notes(subject)
              if parse_near_miss_note_text(r.first_person)["record_id"] == t]
    assert len(notes2) == 1


def test_note_contains_no_target_content_token():
    """Strict token-level content ban (beyond the builder's marker check):
    no content token of the target text may appear in the note."""
    tmp = _tmp()
    subject = _lean_subject(tmp)
    ids = _seed(subject)
    _boost(subject, ids)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(10):
            cli._run_tick(subject)
    notes = [r for r in _notes(subject)
             if parse_near_miss_note_text(r.first_person)["record_id"]
             == ids["T0"]]
    assert len(notes) == 1
    note = notes[0].first_person
    assert MARKER not in note
    toks = lambda s: {w.strip(".,;:!?\"'").lower() for w in s.split()}
    target_toks = {w for w in toks(_TARGET[1]) if len(w) > 3}
    note_toks = toks(note)
    assert target_toks & note_toks == set(), target_toks & note_toks


def test_no_shadowed_definitions():
    """Genome: duplicate cmd_init silently won once. The new names must
    each be defined exactly once."""
    cli_src = (Path(__file__).resolve().parents[2] / "calibos_mind"
               / "cli.py").read_text(encoding="utf-8")
    fam_src = (Path(__file__).resolve().parents[2] / "calibos_mind"
               / "familiarity.py").read_text(encoding="utf-8")
    assert cli_src.count("def cmd_init(") == 1
    for name in ("def due_notes(", "def mark_noted(",
                 "def near_miss_note_text(", "def mint_near_miss_note(",
                 "def parse_near_miss_note_text("):
        assert fam_src.count(name) == 1, name
    assert PERSISTENT_NEAR_MISS_THRESHOLD == 10


def test_old_sidecar_without_guard_loads_and_tracker_reset_wipes_guard():
    tmp = _tmp()
    path = tmp / "familiarity.json"
    path.write_text(json.dumps({"streaks": {"experience-18": 9}}),
                    encoding="utf-8")
    tracker = FamiliarityTracker(path)
    assert tracker.data == {"streaks": {"experience-18": 9}, "noted": {}}
    tracker.mark_noted("experience-18", 10)
    tracker.save()
    tracker.reset()
    assert tracker.data == {"streaks": {}, "noted": {}}
    assert json.loads(path.read_text()) == {"streaks": {}, "noted": {}}
