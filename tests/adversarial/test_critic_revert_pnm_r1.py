"""Critic round 1 — attack the 2026-11-11 REVERT of persistent-near-miss notes.

Spec: research/specs/2026-11-11-revert-persistent-near-miss-note.md.
Brief: spec + diff only. Every test below is an independently written
probe of the revert on synthetic /tmp stores; the live store is never
touched. A failure here is a concrete violated invariant, not prose.

Premises proved before writing (probe scripts, not assumed):
- On the lean subject (salience + familiarity trackers only), waking
  ticks DO add engine records — interoception/body_change records — so
  "total record count unchanged" is a FALSE premise and is NOT asserted.
  The minting invariant is instead: zero new records matching the
  retired note pattern, AND zero new temporal/cognition records (the
  exact signature mint_near_miss_note used).
- Dream ticks freeze the engine tick and add no records (measured).
"""
from __future__ import annotations

import argparse
import ast
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


# Retired canonical note text (2026-10-28), kept ONLY as a detector.
_RETIRED_NOTE_RE = re.compile(
    r"Something feels familiar but I cannot place it\. "
    r"That is (\d+) consecutive near-miss views of (\S+), "
    r"still unplaced, at tick (\d+)\.")
# The exact legacy note text the 2026-10-28 mechanism would have minted
# for the target at streak 10, tick 10 — used to seed a LEGACY note.
_LEGACY_NOTE_TEXT = (
    "Something feels familiar but I cannot place it. "
    "That is 10 consecutive near-miss views of experience-17, "
    "still unplaced, at tick 10.")

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
           "witnessed. left no other trace.")
_FILLER_TAIL = [
    ("memory", "A postcard arrived with no message, only a stamp."),
    ("social", "The barista remembered my order without asking."),
    ("action_consequence", "Watering the fern revived it within a day."),
    ("action_consequence", "The letter I mailed came back unopened."),
    ("imagination", "A city built entirely of staircases and bells."),
    ("imagination", "The moon as a coin dropped in a dark well."),
]


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-revert-pnm-r1-"))


def _make_subject(tmp: Path) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        familiarity_path=str(tmp / "familiarity.json"),
    )


def _seed(subject, target_text=_TARGET[1], target_source=_TARGET[0]):
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLERS):
            ids[f"F{i}"] = subject._add(source, text).id
        ids["T"] = subject._add(target_source, target_text).id
    return ids


def _boost_fillers(subject, ids, delta: float = 5.0):
    stracker = subject.workspace.salience_tracker
    for i in range(16):
        stracker.add_importance(ids[f"F{i}"], 0, delta)
    stracker.save()


def _seed_and_boost(tmp, **kw):
    subject = _make_subject(tmp)
    ids = _seed(subject, **kw)
    _boost_fillers(subject, ids)
    return subject, ids


def _ticks(subject, n: int):
    for _ in range(n):
        cli._run_tick(subject)


def _sidecar(tmp: Path):
    return json.loads((tmp / "familiarity.json").read_text(encoding="utf-8"))


def _streak(tmp: Path, rid: str):
    return _sidecar(tmp)["streaks"].get(rid)


def _note_pattern_matches(subject):
    return [r for r in subject.workspace.records
            if _RETIRED_NOTE_RE.fullmatch(r.first_person)]


def _new_cognition_temporal(subject, before_ids):
    """Records added since `before_ids` with the exact signature the
    deleted mint_near_miss_note used: temporal + generated_by cognition."""
    return [r for r in subject.workspace.records
            if r.id not in before_ids
            and r.source == "temporal"
            and getattr(r, "generated_by", None) == "cognition"]


def _assert_no_mint(subject, before_ids, allowed_pattern_ids=()):
    """The revert signal, twice over: no record matching the retired
    note text, and no new temporal/cognition record at all."""
    notes = _note_pattern_matches(subject)
    assert [r.id for r in notes] == sorted(allowed_pattern_ids), \
        f"unexpected near-miss-note-pattern records: {[r.id for r in notes]}"
    assert _new_cognition_temporal(subject, before_ids) == [], \
        "a temporal/cognition record was minted after the revert"


def _patched_cli(tmp: Path):
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


# -- 1. Fitness: streak 10 / 25 -> zero notes, by pattern AND by signature ----

def test_critic_streak_25_zero_notes_pattern_and_signature():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    before_ids = {r.id for r in subject.workspace.records}
    _ticks(subject, 25)
    assert _streak(tmp, t) == 25
    assert _note_pattern_matches(subject) == []
    assert _new_cognition_temporal(subject, before_ids) == []
    assert set(_sidecar(tmp)) == {"streaks"}, _sidecar(tmp)
    assert subject.workspace.familiarity_tracker.boost_for(t) == 0.5


def test_critic_streak_10_zero_notes_after_admission_and_restreak():
    """Admission resets the streak; a second episode to 10 still mints
    nothing — minting is gone, not merely guarded for one episode."""
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    before_ids = {r.id for r in subject.workspace.records}
    _ticks(subject, 10)
    _assert_no_mint(subject, before_ids)
    _boost_fillers(subject, ids, delta=-5.0)
    _ticks(subject, 1)
    assert t in subject.workspace._last_view_ids
    assert _streak(tmp, t) is None
    _boost_fillers(subject, ids, delta=5.0)
    _ticks(subject, 10)
    assert _streak(tmp, t) == 10
    _assert_no_mint(subject, before_ids)
    assert set(_sidecar(tmp)) == {"streaks"}


# -- 2. Streak/boost behavior unchanged: 3:1 rhythm, flat boost --------------

def test_critic_three_near_misses_then_fourth_view_admits():
    """The designed 3:1 rhythm, on the premise it actually holds under:
    the view+observe pair with TIED activations (test_familiarity's
    fixture — no filler boost). The 0.5 nudge lifts T above a tied
    16th-place filler; it is NOT designed to beat the +3.0 activation
    gap of the boosted-filler fixture (an earlier draft asserted
    admission there and measured streak 4 instead — test-premise error,
    not a code defect: the builder's admission test drops the filler
    boost first, for exactly this reason)."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLERS):
            ids[f"F{i}"] = subject._add(source, text).id
        ids["T"] = subject._add(*_TARGET).id
        for i, (source, text) in enumerate(_FILLER_TAIL):
            ids[f"tail{i}"] = subject._add(source, text).id
    t = ids["T"]
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    for cycle in range(1, 4):
        ws.view()
        assert t in ws._last_near_miss_ids, f"cycle {cycle}: T near-misses"
        assert t not in ws._last_view_ids, f"cycle {cycle}: T not admitted"
        changed = tracker.observe(ws._last_view_ids, ws._last_near_miss_ids)
        assert changed is True
        tracker.save()
        assert _streak(tmp, t) == cycle
        assert tracker.boost_for(t) == (0.5 if cycle >= 3 else 0.0)
    assert _streak(tmp, t) == FAMILIARITY_THRESHOLD == 3
    assert tracker.boost_for(t) == FAMILIARITY_BOOST == 0.5
    ws.view()
    assert t in ws._last_view_ids, "boosted T admits on the 4th view"
    assert t not in ws._last_near_miss_ids
    changed = tracker.observe(ws._last_view_ids, ws._last_near_miss_ids)
    assert changed is True
    tracker.save()
    assert _streak(tmp, t) is None, "admission resets the streak"
    assert tracker.boost_for(t) == 0.0


def test_critic_boost_flat_and_exact_at_streak_100():
    tracker = FamiliarityTracker(_tmp() / "familiarity.json")
    tracker.data["streaks"]["experience-9"] = 2
    assert tracker.boost_for("experience-9") == 0.0
    tracker.data["streaks"]["experience-9"] = 3
    b = tracker.boost_for("experience-9")
    assert b == 0.5 and isinstance(b, float) and b == FAMILIARITY_BOOST
    tracker.data["streaks"]["experience-9"] = 100
    assert tracker.boost_for("experience-9") == 0.5


# -- 3. Archived / pinned exclusion ------------------------------------------

def test_critic_archived_record_never_near_misses():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    tracker2 = ws.salience_tracker
    tracker2.add_importance(t, 0, 5.0)
    archive = tmp / "archive"
    archive.mkdir(exist_ok=True)
    (archive / "availability.json").write_text(
        json.dumps({"excluded": {t: {"reason": "critic archive"}}}),
        encoding="utf-8")
    ws.availability_path = archive / "availability.json"
    before_ids = {r.id for r in subject.workspace.records}
    for _ in range(4):
        _ticks(subject, 1)
        assert t not in ws._last_view_ids
        assert t not in ws._last_near_miss_ids
    assert t not in tracker.data["streaks"]
    assert tracker.boost_for(t) == 0.0
    _assert_no_mint(subject, before_ids)


def test_critic_pinned_record_never_near_misses():
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        pinned = subject._add("memory", "A cartridge root that pins the view.",
                              generated_by="cartridge")
    ids = _seed(subject)
    for _ in range(4):
        _ticks(subject, 1)
        # Pinned admission is structural (generated_by == "cartridge"),
        # so a fresh synchronous view on the LIVE workspace (re-read: a
        # mid-tick transaction restores the workspace object) is the
        # deterministic check — not the post-tick transient, which a
        # mid-tick restore may wipe.
        ws = subject.workspace
        ws.view()
        assert pinned.id in ws._last_view_ids, "pinned records always admit"
        assert pinned.id not in ws._last_near_miss_ids
    assert pinned.id not in subject.workspace.familiarity_tracker.data["streaks"]


# -- 4. No-op write discipline + view construction ----------------------------

def test_critic_noop_observe_writes_nothing():
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        subject._add("thought", "A quiet synthetic sentence for the critic no-op.")
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    sidecar = tmp / "familiarity.json"
    assert not sidecar.exists()
    ws.view()
    assert tracker.observe(ws._last_view_ids, ws._last_near_miss_ids) is False
    assert not sidecar.exists(), "absent stays absent when nothing changed"
    # With a present sidecar, a no-op observe leaves the bytes identical.
    # The genuine no-op is EMPTY streaks: observe finds nothing to
    # increment, reset, or prune. (An earlier draft seeded a streak for
    # a non-competing id and expected False — measured True: pruning a
    # stale streak IS a change, by design. That behavior is asserted
    # separately below, at unit level.)
    tracker.save()  # present but empty streaks
    before = sidecar.read_bytes()
    ws.view()
    assert tracker.observe(ws._last_view_ids, ws._last_near_miss_ids) is False
    assert sidecar.read_bytes() == before, "present stays byte-identical"
    # And the pruning direction, unit-level: a streak for an id in
    # neither set is dropped and observe reports the change; a repeat
    # observe is then a no-op.
    tracker.data["streaks"]["experience-99"] = 2
    assert tracker.observe(ws._last_view_ids, ws._last_near_miss_ids) is True
    assert "experience-99" not in tracker.data["streaks"]
    assert tracker.observe(ws._last_view_ids, ws._last_near_miss_ids) is False


def test_critic_view_construction_alone_never_writes():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 9)
    assert _streak(tmp, t) == 9
    sidecar = tmp / "familiarity.json"
    before = sidecar.read_bytes()
    ws = subject.workspace
    for _ in range(5):
        ws.view()
    assert sidecar.read_bytes() == before
    assert _streak(tmp, t) == 9
    assert _note_pattern_matches(subject) == []


# -- 5. Dream / read-only isolation -------------------------------------------

def test_critic_dream_ticks_freeze_tick_records_and_sidecar():
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp)
    t = ids["T"]
    _ticks(subject, 9)
    assert _streak(tmp, t) == 9
    sidecar = tmp / "familiarity.json"
    before_bytes = sidecar.read_bytes()
    tick_before = subject.engine.state.tick
    n_before = len(subject.workspace.records)
    before_ids = {r.id for r in subject.workspace.records}
    for _ in range(3):
        subject.dream_tick()
    assert subject.engine.state.tick == tick_before, "dream ticks freeze tick"
    assert len(subject.workspace.records) == n_before
    assert sidecar.read_bytes() == before_bytes
    _assert_no_mint(subject, before_ids)


def test_critic_readonly_sweep_leaves_sidecar_byte_identical():
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
        before_ids = {r.id for r in subject.workspace.records}
        cli.cmd_drift(argparse.Namespace(window=30))
        cli.cmd_status(argparse.Namespace(raw=False))
        cli.cmd_review(argparse.Namespace(n=5))
        cli.cmd_inbox(argparse.Namespace(id=None))
        assert sidecar.read_bytes() == before
        assert _streak(tmp, t) == 9
        _assert_no_mint(cli._subject(), before_ids)
    finally:
        _restore_cli(saved)


# -- 6. Stale "noted" key: weird types, extra keys, reset ---------------------

def test_critic_stale_noted_weird_types_load_and_drop():
    for noted_value in ({"experience-18": 10}, ["experience-18"], "garbage", None, 7):
        tmp = _tmp()
        path = tmp / "familiarity.json"
        path.write_text(json.dumps({
            "streaks": {"experience-18": 9},
            "noted": noted_value,
            "surprise_key": {"x": 1},
        }), encoding="utf-8")
        tracker = FamiliarityTracker(path)  # must not raise, any shape
        assert tracker.data == {"streaks": {"experience-18": 9}}
        tracker.observe(["experience-1"], ["experience-18"])
        tracker.save()
        assert json.loads(path.read_text(encoding="utf-8")) == \
            {"streaks": {"experience-18": 10}}, noted_value


def test_critic_reset_after_loading_stale_sidecar_wipes_everything():
    tmp = _tmp()
    path = tmp / "familiarity.json"
    path.write_text(json.dumps({
        "streaks": {"experience-18": 10},
        "noted": {"experience-18": 10},
    }), encoding="utf-8")
    tracker = FamiliarityTracker(path)
    tracker.reset()
    assert tracker.data == {"streaks": {}}
    assert json.loads(path.read_text(encoding="utf-8")) == {"streaks": {}}


# -- 7. Legacy notes are ordinary records again (round-2 exclusion gone) ------

def test_critic_legacy_note_near_misses_boosts_and_mints_nothing():
    """The seeded record IS a 2026-10-28 note text. Post-revert it is an
    ordinary temporal record: it must near-miss and boost (proving the
    round-2 exclusion is gone), and no note-about-note may ever mint."""
    tmp = _tmp()
    subject, ids = _seed_and_boost(tmp, target_text=_LEGACY_NOTE_TEXT,
                                   target_source="temporal")
    t = ids["T"]
    before_ids = {r.id for r in subject.workspace.records}
    assert len(_note_pattern_matches(subject)) == 1, "only the seeded legacy note"
    for cycle in range(1, 4):
        ws = subject.workspace
        ws.view()
        assert t in ws._last_near_miss_ids, \
            f"cycle {cycle}: legacy note near-misses"
        _ticks(subject, 1)
        assert _streak(tmp, t) == cycle
    assert subject.workspace.familiarity_tracker.boost_for(t) == 0.5
    # Admit the legacy note itself: drop the fillers' +5 boost first —
    # the 0.5 nudge cannot beat a +3.0 activation gap (same premise as
    # the builder's admission test), then re-boost for the re-streak.
    _boost_fillers(subject, ids, delta=-5.0)
    _ticks(subject, 1)
    assert _streak(tmp, t) is None, "admission resets the legacy note's streak"
    # Re-streak the legacy note to 10: still exactly one pattern match
    # (the seeded record), no note about it, no new cognition record.
    _boost_fillers(subject, ids, delta=5.0)
    _ticks(subject, 10)
    assert _streak(tmp, t) == 10
    assert subject.workspace.familiarity_tracker.boost_for(t) == 0.5
    _assert_no_mint(subject, before_ids, allowed_pattern_ids=(t,))
    assert set(_sidecar(tmp)) == {"streaks"}


# -- 8. No code-level remnants, no shadowing (genome) -------------------------

_REMOVED_CODE_NAMES = (
    "due_notes", "mark_noted", "mint_near_miss_note",
    "near_miss_note_text", "parse_near_miss_note_text",
    "is_near_miss_note_text", "PERSISTENT_NEAR_MISS_THRESHOLD",
    "_NEAR_MISS_NOTE_RE",
)


def _code_names(path: Path):
    """Name/Attribute identifiers + string-literal values from the AST:
    comments and docstrings are excluded, so a revert note in a comment
    cannot trip this, but any live reference will."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def test_critic_no_code_level_remnant_of_note_machinery():
    root = Path(__file__).resolve().parents[2]
    for rel in ("calibos_mind/familiarity.py", "calibos_mind/cli.py",
                "calibos_mind/workspace.py"):
        names = _code_names(root / rel)
        for removed in _REMOVED_CODE_NAMES:
            assert removed not in names, f"{removed} still referenced in {rel}"
        assert "noted" not in names, f"stale 'noted' key still written in {rel}"
    # The module no longer imports re at all (the only regex was the
    # retired note-text pattern).
    fam_src = (root / "calibos_mind" / "familiarity.py").read_text()
    assert "import re" not in fam_src
    assert "re.compile" not in fam_src


def test_critic_no_shadowed_definitions_genome():
    """Genome: a duplicate definition silently wins. Exactly one of each
    familiarity definition and one _run_tick must exist."""
    root = Path(__file__).resolve().parents[2]
    fam = ast.parse((root / "calibos_mind" / "familiarity.py").read_text())
    defs = [n.name for n in ast.walk(fam) if isinstance(n, ast.FunctionDef)]
    for name in ("observe", "save", "reset", "boost_for"):
        assert defs.count(name) == 1, f"{name} defined {defs.count(name)}x"
    cli_tree = ast.parse((root / "calibos_mind" / "cli.py").read_text())
    cli_defs = [n.name for n in ast.walk(cli_tree)
                if isinstance(n, ast.FunctionDef)]
    assert cli_defs.count("_run_tick") == 1
    assert cli_defs.count("cmd_init") == 1


def test_critic_deleted_critic_files_leave_no_references():
    root = Path(__file__).resolve().parents[2]
    for name in ("tests/adversarial/test_critic_pnm_r1.py",
                 "tests/adversarial/test_critic_pnm_r2.py"):
        assert not (root / name).exists(), f"{name} still exists"
    for path in list(root.rglob("*.py")):
        if ".venv" in path.parts:
            continue
        if path.name.startswith("test_critic_revert"):
            continue  # this file itself names the deleted files, by design
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "test_critic_pnm" not in text, f"dangling ref in {path}"


# -- 9. Determinism ------------------------------------------------------------

def test_critic_identical_view_sequences_byte_identical_sidecar():
    def _run():
        tmp = _tmp()
        subject, ids = _seed_and_boost(tmp)
        _ticks(subject, 12)
        assert _note_pattern_matches(subject) == []
        return (tmp / "familiarity.json").read_bytes()

    assert _run() == _run()
