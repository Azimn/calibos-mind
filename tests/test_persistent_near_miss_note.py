"""Tests for the REVERT of persistent-near-miss notes (domain 4).

Spec: research/specs/2026-11-11-revert-persistent-near-miss-note.md —
the 2026-10-28 note-minting path is removed. Streak tracking and the
0.5 near-miss retrieval boost (2026-09-28 familiarity trace, a kept
mutation) are unchanged; no temporal record is minted at any streak,
and the sidecar carries streaks only — no ``noted`` guard.

All fixtures live in /tmp — the live store is never touched.

Ranking control (mirrors tests/test_familiarity.py): every record is
seeded at tick 0 with zero importance, so activation ties and the stable
sort keeps insertion order — the test author places the target record
exactly. The 16 head fillers are then boosted (+5.0 importance, +3.0
activation at IMPORTANCE_WEIGHT 0.6) so the target's flat 0.5 nudge
never lifts it above the cut: it near-misses on every waking tick until
the test says otherwise.

The retired canonical note text is detected here with a local regex
kept ONLY as a detector — the writer/parser/mint functions are deleted
from the codebase. Any record matching it after the revert is a
revert-signal failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
import calibos_mind.familiarity as familiarity
from calibos_mind.familiarity import (
    FAMILIARITY_BOOST,
    FAMILIARITY_THRESHOLD,
    FamiliarityTracker,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge


# Retired canonical note text (2026-10-28), kept ONLY as a detector:
# no record minted after the revert may match it.
_RETIRED_NOTE_RE = re.compile(
    r"Something feels familiar but I cannot place it\. "
    r"That is (\d+) consecutive near-miss views of (\S+), "
    r"still unplaced, at tick (\d+)\.")

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
             "witnessed. left no other trace.")


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="pnm-revert-test-"))


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


def _retired_notes(subject):
    """Any record matching the retired note pattern — must be empty
    after the revert, globally, for every record, not just the target."""
    return [r for r in subject.workspace.records
            if _RETIRED_NOTE_RE.fullmatch(r.first_person)]


def _sidecar(tmp: Path):
    return json.loads((tmp / "familiarity.json").read_text(encoding="utf-8"))


def _streak(tmp: Path, rid: str):
    return _sidecar(tmp)["streaks"].get(rid)


def _assert_sidecar_streaks_only(tmp: Path):
    """The sidecar carries streaks and nothing else — no ``noted`` key."""
    assert set(_sidecar(tmp)) == {"streaks"}, _sidecar(tmp)


def _patched_cli(tmp: Path):
    """Point the CLI at synthetic paths — the COMPLETE redirect set
    (conftest's lesson: a partial set is how live sidecars get wiped).
    Routes through the canonical conftest.redirect_all_cli_paths.
    Returns the saved globals for _restore_cli."""
    from conftest import redirect_all_cli_paths
    saved = redirect_all_cli_paths(tmp)
    saved["_subject"] = cli._subject

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

    cli._subject = make_subject
    return saved


def _restore_cli(saved):
    for name, value in saved.items():
        setattr(cli, name, value)


# -- fitness: streak 10 / 25 -> zero notes, streaks only ----------------------

def test_streak_nine_mints_no_note():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 9)
    assert _streak(tmp, t) == 9
    assert _retired_notes(subject) == []
    _assert_sidecar_streaks_only(tmp)


def test_streak_ten_mints_no_note_but_streak_and_boost_hold():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 10)
    assert _streak(tmp, t) == 10
    # The kept 2026-09-28 behavior: boost flat 0.5 at the threshold.
    assert subject.workspace.familiarity_tracker.boost_for(t) == 0.5
    assert _retired_notes(subject) == []
    _assert_sidecar_streaks_only(tmp)


def test_streak_to_twenty_five_mints_no_note():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 25)
    assert _streak(tmp, t) == 25
    assert _retired_notes(subject) == []
    _assert_sidecar_streaks_only(tmp)


# -- fitness: admission / pruning reset the streak; still no notes -----------

def test_admission_then_restreak_mints_no_note():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 10)
    assert _retired_notes(subject) == []
    # Drop the fillers' boost: the target's 0.5 nudge now lifts it above
    # the cut, so the next waking tick admits it.
    _boost_fillers(subject, ids, delta=-5.0)
    _ticks(subject, 1)
    assert t in subject.workspace._last_view_ids, "target must be admitted"
    assert _streak(tmp, t) is None, "admission resets the streak"
    # Re-boost the fillers and streak again: a new episode still notes
    # nothing — minting is gone, not merely guarded.
    _boost_fillers(subject, ids, delta=5.0)
    _ticks(subject, 10)
    assert _streak(tmp, t) == 10
    assert _retired_notes(subject) == []
    _assert_sidecar_streaks_only(tmp)


def test_pruning_then_restreak_mints_no_note():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 10)
    assert _retired_notes(subject) == []
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
    # The target near-misses again from here: 10 more ticks, still no note.
    _ticks(subject, 10)
    assert _streak(tmp, t) == 10
    assert _retired_notes(subject) == []
    _assert_sidecar_streaks_only(tmp)


# -- fitness: read-only sweep + dream ticks mint nothing, sidecar untouched ---

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
        assert _retired_notes(cli._subject()) == []
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
    assert _retired_notes(subject) == []


# -- fitness: init --force wipes the sidecar, stale guard included ------------

def test_init_force_wipes_sidecar_including_stale_noted_key():
    tmp = _tmp()
    saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        # Pre-reseed sidecar: streaks AND a stale pre-revert "noted"
        # guard across the whole id range a reseed could recycle, so any
        # surviving state would attach to recycled ids.
        fam = tmp / "familiarity.json"
        fam.write_text(json.dumps({
            "streaks": {f"experience-{i}": 10 for i in range(1, 61)},
            "noted": {f"experience-{i}": 10 for i in range(1, 61)},
        }), encoding="utf-8")
        assert fam.exists()
        cli.cmd_init(argparse.Namespace(force=True))
        assert not fam.exists(), \
            "init --force must delete the familiarity sidecar entirely"
        fresh = cli._subject()
        assert fresh.workspace.familiarity_tracker.data == {"streaks": {}}
        # Post-reseed scenario on the fresh store: a streak to 10 notes
        # nothing — no recycled-id state survives.
        ids = _seed(fresh)
        _boost_fillers(fresh, ids)
        _ticks(fresh, 10)
        assert _retired_notes(fresh) == []
        assert _streak(tmp, ids["T"]) == 10
    finally:
        _restore_cli(saved)


# -- fitness: stale "noted" key loads cleanly, next save drops it -------------

def test_stale_noted_key_loads_and_next_write_drops_it():
    tmp = _tmp()
    path = tmp / "familiarity.json"
    path.write_text(json.dumps({
        "streaks": {"experience-18": 9},
        "noted": {"experience-18": 10},
    }), encoding="utf-8")
    tracker = FamiliarityTracker(path)
    # Loads without error; the stale guard never enters memory.
    assert tracker.data == {"streaks": {"experience-18": 9}}
    assert not hasattr(tracker, "due_notes")
    # The next write drops the key: only "streaks" is ever written.
    tracker.observe(["experience-1"], ["experience-18"])
    assert tracker.data["streaks"]["experience-18"] == 10
    tracker.save()
    assert json.loads(path.read_text(encoding="utf-8")) == \
        {"streaks": {"experience-18": 10}}


def test_old_sidecar_without_noted_loads_cleanly():
    tmp = _tmp()
    path = tmp / "familiarity.json"
    path.write_text(json.dumps({"streaks": {"experience-18": 9}}),
                    encoding="utf-8")
    tracker = FamiliarityTracker(path)
    assert tracker.data == {"streaks": {"experience-18": 9}}
    tracker.observe(["experience-1"], ["experience-18"])
    assert tracker.data["streaks"]["experience-18"] == 10


# -- fitness: byte-identical replay ------------------------------------------

def test_identical_view_sequences_produce_byte_identical_sidecar():
    def _run():
        tmp = _tmp()
        subject, ids = _seed_and_boost(tmp)
        _ticks(subject, 12)
        assert _retired_notes(subject) == []
        return (tmp / "familiarity.json").read_bytes()

    assert _run() == _run()


# -- unit: boost flat, reset, no note machinery remains -----------------------

def test_boost_unchanged_flat_at_threshold_and_beyond():
    tracker = FamiliarityTracker(_tmp() / "familiarity.json")
    tracker.data["streaks"]["experience-18"] = 3
    assert tracker.boost_for("experience-18") == FAMILIARITY_BOOST == 0.5
    tracker.data["streaks"]["experience-18"] = 10
    assert tracker.boost_for("experience-18") == 0.5
    tracker.data["streaks"]["experience-18"] = 100
    assert tracker.boost_for("experience-18") == 0.5
    assert FAMILIARITY_THRESHOLD == 3


def test_reset_wipes_streaks():
    tmp = _tmp()
    tracker = FamiliarityTracker(tmp / "familiarity.json")
    tracker.data["streaks"]["experience-18"] = 10
    tracker.save()
    tracker.reset()
    assert tracker.data == {"streaks": {}}
    assert json.loads((tmp / "familiarity.json").read_text()) == \
        {"streaks": {}}


def test_no_note_machinery_remains():
    """The revert is total: no minting path, guard, or threshold survives
    in the familiarity module. (Genome: a shadowed or leftover definition
    is how a reverted mechanism comes back.)"""
    for name in ("PERSISTENT_NEAR_MISS_THRESHOLD",
                 "due_notes", "mark_noted",
                 "near_miss_note_text", "parse_near_miss_note_text",
                 "is_near_miss_note_text", "mint_near_miss_note"):
        assert not hasattr(familiarity, name), name
    assert not hasattr(FamiliarityTracker, "due_notes")
    assert not hasattr(FamiliarityTracker, "mark_noted")
    # And the workspace no longer special-cases note text in the
    # near-miss side-channel.
    import calibos_mind.workspace as workspace
    assert not hasattr(workspace, "is_near_miss_note_text")
    assert "is_near_miss_note_text" not in dir(workspace)


def test_cartridge_fingerprint_stable_no_migration_needed():
    """The revert touches no cartridge: a fresh subject loads the used
    store without a fingerprint-migration error."""
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    _ticks(subject, 12)
    assert _retired_notes(subject) == []
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    import dataclasses
    fingerprint = hashlib.sha256(
        json.dumps(dataclasses.asdict(cart), sort_keys=True).encode()
    ).hexdigest()
    assert subject.fingerprint == fingerprint
    fresh = _make_subject(tmp)
    assert fresh.engine.state.tick == subject.engine.state.tick
