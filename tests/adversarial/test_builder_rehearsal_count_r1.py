"""Builder battery R1 for Bug B: rehearsal counter over-report.

Tests the builder's fix (calibos_mind/salience.py):
  - SalienceTracker.note_recall now returns True when the tick was newly
    added to the record's recall set, False when already present.
  - rehearse_from_dreams increments its returned count only on True.
  - The returned count is idempotent across reprocessings of identical logs.

All fixtures are synthetic /tmp stores. The live store
(~/workspace/calibos-mind/mind.db, inbox/, dreams/, salience.json) is never
opened. Genome checks are included explicitly.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from calibos_mind.salience import SalienceTracker

TEXT_A = "the first memory text, alpha"
TEXT_B = "the second memory text, beta"
TEXT_C = "a third memory text, gamma"


def _tracker(tmp_path):
    return SalienceTracker(tmp_path / "salience.json")


def _records():
    return [
        {"id": "r1", "tick": 10, "source": "memory", "first_person": TEXT_A},
        {"id": "r2", "tick": 20, "source": "memory", "first_person": TEXT_B},
        {"id": "r3", "tick": 30, "source": "memory", "first_person": TEXT_C},
    ]


def _mem(text):
    return {"source": "memory", "first_person": text}


def _write_log(dream_dir, name, frags):
    """Write a dream log: frags is [(tick, [experiences]), ...]."""
    path = dream_dir / name
    with path.open("w", encoding="utf-8") as f:
        for tick, exps in frags:
            f.write(json.dumps({"tick": tick, "experiences": exps}) + "\n")
    return path


# -- spec fitness tests ------------------------------------------------------

def test_second_pass_returns_zero(tmp_path):
    """Reprocessing identical dream logs returns 0 on the second pass."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        (5, [_mem(TEXT_A)]),
        (7, [_mem(TEXT_B)]),
    ])
    t = _tracker(tmp_path)
    first = t.rehearse_from_dreams(dream_dir, _records())
    assert first == 2
    second = t.rehearse_from_dreams(dream_dir, _records())
    assert second == 0
    assert t.data["records"]["r1"]["recalls"] == [5]
    assert t.data["records"]["r2"]["recalls"] == [7]


def test_first_pass_counts_exactly(tmp_path):
    """First pass counts each distinct (record, tick) pair exactly once.

    - same record at two different ticks -> both count
    - same (record, tick) seen twice -> counts once
    """
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        (5, [_mem(TEXT_A)]),
        (7, [_mem(TEXT_A)]),   # same record, new tick
        (5, [_mem(TEXT_A)]),   # same record, same tick -> duplicate
        (5, [_mem(TEXT_B)]),   # different record
    ])
    t = _tracker(tmp_path)
    n = t.rehearse_from_dreams(dream_dir, _records())
    assert n == 3
    assert t.data["records"]["r1"]["recalls"] == [5, 7]
    assert t.data["records"]["r2"]["recalls"] == [5]


def test_note_recall_return_contract(tmp_path):
    """note_recall returns True on genuinely new ticks, False on repeats."""
    t = _tracker(tmp_path)
    assert t.note_recall("r1", 10, 5) is True     # new record, new tick
    assert t.note_recall("r1", 10, 5) is False    # same tick -> already present
    assert t.note_recall("r1", 10, 7) is True     # same record, new tick
    assert t.note_recall("r1", 10, 7) is False
    assert t.note_recall("r2", 20, 5) is True     # different record, same tick
    assert t.data["records"]["r1"]["recalls"] == [5, 7]
    assert t.data["records"]["r2"]["recalls"] == [5]


def test_mixed_old_and_new_logs_count_only_new(tmp_path):
    """A later log mixing old and new fragments counts only genuinely new recalls."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        (5, [_mem(TEXT_A)]),
        (6, [_mem(TEXT_B)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 2
    # Later log: two repeats, two genuinely new recalls (new tick on r1,
    # first surfacing of r3).
    _write_log(dream_dir, "d2.jsonl", [
        (5, [_mem(TEXT_A)]),   # old: already rehearsed
        (6, [_mem(TEXT_B)]),   # old: already rehearsed
        (8, [_mem(TEXT_A)]),   # new tick for r1
        (6, [_mem(TEXT_C)]),   # new record surfacing
    ])
    n = t.rehearse_from_dreams(dream_dir, _records())
    assert n == 2
    assert t.data["records"]["r1"]["recalls"] == [5, 8]
    assert t.data["records"]["r3"]["recalls"] == [6]


# -- genome checks ------------------------------------------------------------

def test_rehearse_does_not_write_dream_logs(tmp_path):
    """Genome: read-only violation check — dream logs are byte-identical after
    rehearse_from_dreams."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    p = _write_log(dream_dir, "d1.jsonl", [
        (5, [_mem(TEXT_A)]),
        (7, [_mem(TEXT_B)]),
        (9, [{"source": "other", "first_person": "noise"}]),
    ])
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    t = _tracker(tmp_path)
    t.rehearse_from_dreams(dream_dir, _records())
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


def test_rehearse_does_not_persist_sidecar_unprompted(tmp_path):
    """rehearse_from_dreams mutates in-memory state only; the sidecar file is
    untouched until the caller saves (no silent write path)."""
    sidecar = tmp_path / "salience.json"
    t = SalienceTracker(sidecar)
    t.save()  # seed the file
    before = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [(5, [_mem(TEXT_A)])])
    t.rehearse_from_dreams(dream_dir, _records())
    assert hashlib.sha256(sidecar.read_bytes()).hexdigest() == before


def test_count_is_exact_never_quiet(tmp_path):
    """Genome: silent defaults — the count is an exact integer, never a
    quiet/clamped number. Empty dirs and non-memory fragments report 0;
    non-workspace memories are ignored, not miscounted."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 0

    _write_log(dream_dir, "d1.jsonl", [
        (5, [{"source": "thought", "first_person": "not a memory surfacing"}]),
        (6, [_mem("a memory nobody in the workspace holds")]),
        (7, []),
    ])
    assert t.rehearse_from_dreams(dream_dir, _records()) == 0
    assert t.data["records"] == {}
