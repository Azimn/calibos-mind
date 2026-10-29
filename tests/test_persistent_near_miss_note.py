"""Tests for persistent-near-miss notes (domain 4: familiar-but-unplaceable).

Spec: research/specs/2026-10-28-persistent-near-miss-note.md —
calibos_mind/familiarity.py, wired in cli._run_tick.
All fixtures live in /tmp — the live store is never touched.

A familiarity near-miss streak reaching PERSISTENT_NEAR_MISS_THRESHOLD
(10) consecutive near-miss views without admission mints exactly one
temporal record (generated_by=cognition) naming the record id and the
streak count — never any content of the target record. One note per
episode: admission or pruning clears the sidecar guard, so a later
streak to 10 may note again.

Ranking control (mirrors tests/test_familiarity.py): every record is
seeded at tick 0 with zero importance, so activation ties and the stable
sort keeps insertion order — the test author places the target record
exactly. The 16 head fillers are then boosted (+5.0 importance, +3.0
activation at IMPORTANCE_WEIGHT 0.6) so the target's flat 0.5 nudge
never lifts it above the cut: it near-misses on every waking tick until
the test says otherwise. The target text carries a unique marker string
that must never appear in any minted note.

Subjects are lean (salience + familiarity trackers only): the heartbeat
still adds a few engine records of its own, and those may legitimately
near-miss and note in their own episodes — the mechanism notes per
record, not per tick. Assertions therefore scope to the target record's
notes (exactly one per episode, never a duplicate), plus a global
no-duplicate-(record_id, tick) check; they never claim the target is
the only record that notes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.familiarity import (
    FAMILIARITY_BOOST,
    FAMILIARITY_THRESHOLD,
    PERSISTENT_NEAR_MISS_THRESHOLD,
    FamiliarityTracker,
    is_near_miss_note_text,
    mint_near_miss_note,
    near_miss_note_text,
    parse_near_miss_note_text,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge


MARKER = "ZEBRAQUILL-7X9"

_FILLER_TOPICS = [
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
_T_TARGET = ("memory",
             "The lighthouse keeper's log mentioned a storm I never "
             f"witnessed. {MARKER} left no other trace.")


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="pnm-test-"))


def _make_subject(tmp: Path) -> CalibosSubject:
    """Lean subject: salience + familiarity trackers only. _run_tick
    degrades gracefully over missing trackers, and the lean shape keeps
    the heartbeat's own records few so the target stays inside the
    near-miss window for a full 25-tick streak."""
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        familiarity_path=str(tmp / "familiarity.json"),
    )


def _seed(subject: CalibosSubject):
    """Seed 16 head fillers, then the target T. Returns ids. Exactly
    VIEW_LIMIT fillers admit, so T sits just below the cut."""
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLER_TOPICS):
            r = subject._add(source, text)
            ids[f"F{i}"] = r.id
        t = subject._add(*_T_TARGET)
        ids["T"] = t.id
    return ids


def _boost_fillers(subject, ids, delta: float = 5.0):
    """Lift (or drop, with a negative delta) the 16 head fillers' importance
    and persist it, so the next waking ticks rank the target accordingly."""
    stracker = subject.workspace.salience_tracker
    for i in range(16):
        stracker.add_importance(ids[f"F{i}"], 0, delta)
    stracker.save()


def _seed_and_boost(tmp: Path):
    """Subject + ids with the target pinned below the view cut."""
    subject = _make_subject(tmp)
    ids = _seed(subject)
    _boost_fillers(subject, ids)
    return subject, ids


def _ticks(subject, n: int):
    for _ in range(n):
        cli._run_tick(subject)


def _notes(subject):
    """All persistent-near-miss notes (the anchored parse filters out
    every other cognition note)."""
    return [r for r in subject.workspace.records
            if r.source == "temporal" and r.generated_by == "cognition"
            and is_near_miss_note_text(r.first_person)]


def _notes_for(subject, rid: str):
    """The target record's notes only — the per-episode assertions scope
    here, since other records may legitimately note their own episodes."""
    return [r for r in _notes(subject)
            if parse_near_miss_note_text(r.first_person)["record_id"] == rid]


def _assert_no_duplicate_episodes(subject):
    """No (record_id, tick) pair notes twice — the mint's idempotency
    key, checked globally."""
    keys = [ (parse_near_miss_note_text(r.first_person)["record_id"],
              parse_near_miss_note_text(r.first_person)["tick"])
             for r in _notes(subject)]
    assert len(keys) == len(set(keys)), keys


def _sidecar(tmp: Path):
    return json.loads((tmp / "familiarity.json").read_text(encoding="utf-8"))


def _streak(tmp: Path, rid: str):
    return _sidecar(tmp)["streaks"].get(rid)


def _noted(tmp: Path, rid: str):
    return _sidecar(tmp)["noted"].get(rid)


def _patched_cli(tmp: Path):
    """Point the CLI at synthetic paths — the COMPLETE redirect set
    (conftest's lesson: a partial set is how live sidecars get wiped).
    Returns the saved globals for _restore_cli."""
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


# -- fitness 1: streak 9 -> zero notes ----------------------------------------

def test_streak_nine_mints_no_note():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 9)
    assert _streak(tmp, t) == 9
    assert _noted(tmp, t) is None
    assert _notes_for(subject, t) == []


# -- fitness 2: streak 10 -> exactly one cognition note, id + streak, no content

def test_streak_ten_mints_exactly_one_note_without_target_content():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 10)
    assert _streak(tmp, t) == 10
    notes = _notes_for(subject, t)
    assert len(notes) == 1, [r.first_person for r in notes]
    _assert_no_duplicate_episodes(subject)
    note = notes[0]
    assert note.source == "temporal"
    assert note.generated_by == "cognition"
    # No contractions (regression genome: contraction-negation blindness).
    assert "n't" not in note.first_person
    assert "’" not in note.first_person and "‘" not in note.first_person
    parsed = parse_near_miss_note_text(note.first_person)
    assert parsed is not None
    assert parsed["record_id"] == t
    assert parsed["streak"] == PERSISTENT_NEAR_MISS_THRESHOLD == 10
    # Content ban, proved by unique marker: the note names the id and the
    # streak only — none of the target's text may appear in it.
    assert MARKER not in note.first_person
    assert "lighthouse" not in note.first_person
    assert "storm" not in note.first_person
    # Round-trip: the canonical text is byte-identical from the parse.
    assert near_miss_note_text(parsed) == note.first_person
    # The guard latched at the threshold streak.
    assert _noted(tmp, t) == 10


# -- fitness 3: streak to 25 -> still exactly one note -------------------------

def test_streak_to_twenty_five_mints_no_second_note():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 25)
    assert _streak(tmp, t) == 25
    notes = _notes_for(subject, t)
    assert len(notes) == 1, [r.first_person for r in notes]
    assert parse_near_miss_note_text(notes[0].first_person)["streak"] == 10
    assert _noted(tmp, t) == 10
    _assert_no_duplicate_episodes(subject)


# -- fitness 4: admission ends the episode; a fresh streak notes again --------

def test_admission_then_restreak_mints_second_note():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 10)
    assert len(_notes_for(subject, t)) == 1
    # Drop the fillers' boost: the target's 0.5 nudge now lifts it above
    # the cut, so the next waking tick admits it.
    _boost_fillers(subject, ids, delta=-5.0)
    _ticks(subject, 1)
    assert t in subject.workspace._last_view_ids, "target must be admitted"
    assert _streak(tmp, t) is None, "admission resets the streak"
    assert _noted(tmp, t) is None, "admission clears the episode guard"
    # Re-boost the fillers and streak again: a new episode notes again.
    _boost_fillers(subject, ids, delta=5.0)
    _ticks(subject, 10)
    notes = _notes_for(subject, t)
    assert len(notes) == 2, [r.first_person for r in notes]
    ticks = sorted(parse_near_miss_note_text(n.first_person)["tick"]
                   for n in notes)
    assert ticks[0] < ticks[1], "second note is a later episode"
    assert all(parse_near_miss_note_text(n.first_person)["streak"] == 10
               for n in notes)
    _assert_no_duplicate_episodes(subject)


# -- fitness 5: pruning ends the episode; a fresh streak notes again ----------

def test_pruning_then_restreak_mints_second_note():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 10)
    assert len(_notes_for(subject, t)) == 1
    # Simulate one waking tick in which the target stops competing: it is
    # in neither the admitted nor the near-miss set, so observe() prunes
    # its streak — exactly the input cli._run_tick would pass from such a
    # view (e.g. the target archived or outranked past the window).
    ws = subject.workspace
    ftracker = ws.familiarity_tracker
    admitted = tuple(ws._last_view_ids)
    near = tuple(r for r in ws._last_near_miss_ids if r != t)
    assert t not in admitted and t not in near
    assert ftracker.observe(admitted, near) is True
    ftracker.save()
    assert _streak(tmp, t) is None, "pruning drops the streak"
    assert _noted(tmp, t) is None, "pruning clears the episode guard"
    # The target near-misses again from here: 10 more ticks, second note.
    _ticks(subject, 10)
    notes = _notes_for(subject, t)
    assert len(notes) == 2, [r.first_person for r in notes]
    ticks = sorted(parse_near_miss_note_text(n.first_person)["tick"]
                   for n in notes)
    assert ticks[0] < ticks[1]
    _assert_no_duplicate_episodes(subject)


# -- fitness 6: read-only sweep + dream ticks mint nothing, sidecar untouched --

def test_readonly_commands_mint_no_note_and_leave_sidecar_identical():
    tmp = _tmp()
    saved = _patched_cli(tmp)
    try:
        subject = cli._subject()
        ids = _seed(subject)
        _boost_fillers(subject, ids)
        _ticks(subject, 9)
        t = ids["T"]
        assert _streak(tmp, t) == 9
        sidecar = tmp / "familiarity.json"
        before = sidecar.read_bytes()
        before_count = len(subject.workspace.records)
        cli.cmd_drift(argparse.Namespace(window=30))
        cli.cmd_status(argparse.Namespace(raw=False))
        cli.cmd_review(argparse.Namespace(n=5))
        cli.cmd_inbox(argparse.Namespace(id=None))
        cli.cmd_consolidate(argparse.Namespace(
            list=True, accept=None, reject=False, quarantine=False))
        assert sidecar.read_bytes() == before, \
            "read-only commands must not touch the familiarity sidecar"
        # Genome: CLI construction is never mtime-silent (the frozen
        # engine's __init__ rewrites the payload row), so the assertion
        # scopes to record count, not file bytes.
        assert len(cli._subject().workspace.records) == before_count
        assert _notes_for(cli._subject(), t) == []
    finally:
        _restore_cli(saved)


def test_dream_ticks_mint_no_note_and_leave_sidecar_identical():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 9)
    assert _streak(tmp, t) == 9
    sidecar = tmp / "familiarity.json"
    before = sidecar.read_bytes()
    for _ in range(3):
        subject.dream_tick()
    assert sidecar.read_bytes() == before, \
        "dream ticks must not touch the familiarity sidecar"
    assert _notes_for(subject, t) == []


# -- fitness 7: init --force wipes streaks AND guard ----------------------------

def test_init_force_wipes_streaks_and_guard_no_recycled_attachment():
    tmp = _tmp()
    saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        # Pre-reseed sidecar: streaks AND guards across the whole id
        # range a reseed could recycle, so any surviving guard would
        # suppress the post-reseed target's note.
        fam = tmp / "familiarity.json"
        fam.write_text(json.dumps({
            "streaks": {f"experience-{i}": 10 for i in range(1, 61)},
            "noted": {f"experience-{i}": 10 for i in range(1, 61)},
        }), encoding="utf-8")
        assert fam.exists()
        cli.cmd_init(argparse.Namespace(force=True))
        assert not fam.exists(), \
            "init --force must delete the familiarity sidecar including the guard"
        fresh = cli._subject()
        assert fresh.workspace.familiarity_tracker.data == \
            {"streaks": {}, "noted": {}}
        # Post-reseed scenario on the fresh store: a streak to 10 notes
        # exactly once — no recycled-id guard suppresses it.
        ids = _seed(fresh)
        _boost_fillers(fresh, ids)
        _ticks(fresh, 10)
        notes = _notes_for(fresh, ids["T"])
        assert len(notes) == 1, [r.first_person for r in notes]
        assert parse_near_miss_note_text(notes[0].first_person)["record_id"] \
            == ids["T"]
    finally:
        _restore_cli(saved)


# -- fitness 8: byte-identical replay ------------------------------------------

def test_identical_view_sequences_produce_byte_identical_sidecar_and_notes():
    def _run():
        tmp = _tmp()
        subject, ids = _seed_and_boost(tmp)
        _ticks(subject, 12)
        notes = _notes_for(subject, ids["T"])
        assert len(notes) == 1
        return ((tmp / "familiarity.json").read_bytes(),
                notes[0].first_person)

    assert _run() == _run()


# -- unit: text/parse/mint, guard semantics ------------------------------------

def test_note_text_parse_round_trip_and_rejections():
    event = {"record_id": "experience-18", "streak": 10, "tick": 47}
    text = near_miss_note_text(event)
    parsed = parse_near_miss_note_text(text)
    assert parsed == event
    assert near_miss_note_text(parsed) == text
    assert not is_near_miss_note_text(text.replace("cannot", "can't"))
    assert not is_near_miss_note_text(text + " ")
    assert not is_near_miss_note_text(" " + text)
    assert not is_near_miss_note_text("just some text")
    assert not is_near_miss_note_text(None)
    assert not is_near_miss_note_text(42)


def test_mint_is_idempotent_on_record_id_and_tick():
    tmp = _tmp()
    subject = _make_subject(tmp)
    event = {"record_id": "experience-18", "streak": 10, "tick": 47}
    with subject._transaction():
        first = mint_near_miss_note(subject, event)
    with subject._transaction():
        second = mint_near_miss_note(subject, event)
    assert first == second
    assert len(_notes(subject)) == 1
    # A later episode (new tick) mints separately.
    with subject._transaction():
        third = mint_near_miss_note(subject, {**event, "tick": 99})
    assert third != first
    assert len(_notes(subject)) == 2


def test_old_sidecar_without_guard_loads_cleanly_and_notes():
    tmp = _tmp()
    path = tmp / "familiarity.json"
    path.write_text(json.dumps({"streaks": {"experience-18": 9}}),
                    encoding="utf-8")
    tracker = FamiliarityTracker(path)
    assert tracker.data == {"streaks": {"experience-18": 9}, "noted": {}}
    assert tracker.due_notes() == []
    tracker.observe(["experience-1"], ["experience-18"])
    assert tracker.due_notes() == [("experience-18", 10)]
    tracker.mark_noted("experience-18", 10)
    assert tracker.due_notes() == []


def test_guard_clears_on_admission_and_pruning_at_tracker_level():
    tracker = FamiliarityTracker(_tmp() / "familiarity.json")
    tracker.data["streaks"]["experience-18"] = 10
    tracker.mark_noted("experience-18", 10)
    assert tracker.due_notes() == []
    # Admission clears streak and guard.
    assert tracker.observe(["experience-18"], []) is True
    assert tracker.data == {"streaks": {}, "noted": {}}
    # Re-streak to the threshold: due again.
    for _ in range(10):
        tracker.observe([], ["experience-18"])
    assert tracker.due_notes() == [("experience-18", 10)]
    tracker.mark_noted("experience-18", 10)
    # Pruning clears streak and guard.
    assert tracker.observe([], []) is True
    assert tracker.data == {"streaks": {}, "noted": {}}


def test_orphan_guard_never_suppresses_a_fresh_streak():
    tracker = FamiliarityTracker(_tmp() / "familiarity.json")
    # Hand-crafted sidecar: guard without a streak (crashed or edited).
    tracker.data["noted"]["experience-18"] = 10
    assert tracker.observe([], ["experience-18"]) is True
    assert tracker.data["noted"] == {}, "orphan guard must self-heal away"
    for _ in range(9):
        tracker.observe([], ["experience-18"])
    assert tracker.due_notes() == [("experience-18", 10)]


def test_boost_unchanged_flat_at_threshold_and_beyond():
    tracker = FamiliarityTracker(_tmp() / "familiarity.json")
    tracker.data["streaks"]["experience-18"] = 10
    assert tracker.boost_for("experience-18") == FAMILIARITY_BOOST == 0.5
    tracker.data["streaks"]["experience-18"] = 100
    assert tracker.boost_for("experience-18") == 0.5
    assert FAMILIARITY_THRESHOLD == 3


def test_reset_wipes_streaks_and_guard():
    tmp = _tmp()
    tracker = FamiliarityTracker(tmp / "familiarity.json")
    tracker.data["streaks"]["experience-18"] = 10
    tracker.mark_noted("experience-18", 10)
    tracker.save()
    tracker.reset()
    assert tracker.data == {"streaks": {}, "noted": {}}
    assert json.loads((tmp / "familiarity.json").read_text()) == \
        {"streaks": {}, "noted": {}}


def test_cartridge_fingerprint_stable_no_migration_needed():
    """The mutation touches no cartridge: a fresh subject loads the used
    store without a fingerprint-migration error."""
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    _ticks(subject, 12)
    assert len(_notes_for(subject, ids["T"])) == 1
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    import dataclasses
    fingerprint = hashlib.sha256(
        json.dumps(dataclasses.asdict(cart), sort_keys=True).encode()
    ).hexdigest()
    assert subject.fingerprint == fingerprint
    fresh = _make_subject(tmp)
    assert fresh.engine.state.tick == subject.engine.state.tick
