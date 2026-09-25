"""Critic battery R1 for Bug B: rehearsal counter over-report (independent attack).

Covers the spec contract from the adversarial side, independent of the
builder's battery:
  - SalienceTracker.note_recall returns a genuine bool (True = tick newly
    added, False = already present).
  - rehearse_from_dreams increments its returned count only on True, so the
    count is exact and idempotent: each distinct (record, tick) pair counts
    exactly once across reprocessings, and the returned number always equals
    the actual growth of the recall sets (never a quiet number).

Probes: duplicate pairs across multiple files in one pass, missing tick
fields, malformed/blank lines, non-memory sources, records appearing and
disappearing between passes, persistence round-trip idempotence (the set must
survive save/reload, not just in-memory state), read-only dream logs, no
unprompted sidecar writes, reseed reset, and a static check that
rehearse_from_dreams is still the sole caller of note_recall (the spec's
signature-change safety claim).

All fixtures are synthetic /tmp stores. The live store
(~/workspace/calibos-mind/mind.db, inbox/, dreams/, salience.json) is never
opened.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from calibos_mind.salience import SalienceTracker

ROOT = Path(__file__).resolve().parents[2]

TEXT_A = "critic memory alpha"
TEXT_B = "critic memory beta"


def _tracker(tmp_path):
    return SalienceTracker(tmp_path / "salience.json")


def _records(extra=()):
    base = [
        {"id": "r1", "tick": 10, "source": "memory", "first_person": TEXT_A},
        {"id": "r2", "tick": 20, "source": "memory", "first_person": TEXT_B},
    ]
    return base + list(extra)


def _write_log(dream_dir, name, frags):
    path = dream_dir / name
    with path.open("w", encoding="utf-8") as f:
        for frag in frags:
            f.write(json.dumps(frag) + "\n")
    return path


def _mem(text):
    return {"source": "memory", "first_person": text}


def _total_recalls(t):
    return sum(len(e["recalls"]) for e in t.data["records"].values())


# -- contract: the count is exact ---------------------------------------------

def test_critic_count_equals_actual_set_growth(tmp_path):
    """The returned count is never a quiet number: it must exactly equal the
    growth of the recall sets, even with in-fragment and in-file duplicates."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        {"tick": 5, "experiences": [_mem(TEXT_A), _mem(TEXT_A)]},  # dup in frag
        {"tick": 5, "experiences": [_mem(TEXT_A)]},                # dup in file
        {"tick": 7, "experiences": [_mem(TEXT_A), _mem(TEXT_B)]},
    ])
    t = _tracker(tmp_path)
    before = _total_recalls(t)
    n = t.rehearse_from_dreams(dream_dir, _records())
    after = _total_recalls(t)
    assert n == 3            # (r1,5), (r1,7), (r2,7)
    assert after - before == n, "count must equal actual recall-set growth"


def test_critic_duplicate_pair_across_files_one_pass(tmp_path):
    """The same (record, tick) pair split across two log files in a single
    pass counts exactly once."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "a.jsonl", [{"tick": 5, "experiences": [_mem(TEXT_A)]}])
    _write_log(dream_dir, "b.jsonl", [
        {"tick": 5, "experiences": [_mem(TEXT_A)]},   # repeat of a.jsonl's pair
        {"tick": 6, "experiences": [_mem(TEXT_A)]},   # new tick
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 2
    assert t.data["records"]["r1"]["recalls"] == [5, 6]


def test_critic_note_recall_returns_genuine_bool(tmp_path):
    """The True/False contract is a real bool, not merely truthy/falsy."""
    t = _tracker(tmp_path)
    first = t.note_recall("r1", 10, 5)
    second = t.note_recall("r1", 10, 5)
    assert type(first) is bool and first is True
    assert type(second) is bool and second is False


# -- contract: degraded input ---------------------------------------------------

def test_critic_missing_tick_field_collapses_to_zero_pair(tmp_path):
    """A fragment with no 'tick' field defaults to 0; two such fragments for
    the same record are NOT a distinct pair, so they count once (measured
    behavior locked in)."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "m.jsonl", [
        {"experiences": [_mem(TEXT_A)]},
        {"experiences": [_mem(TEXT_A)]},
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 1
    assert t.data["records"]["r1"]["recalls"] == [0]


def test_critic_malformed_lines_and_non_memory_sources_skipped(tmp_path):
    """Invalid JSON lines, blank lines, non-memory sources, and fragments
    with no experiences key are skipped — none of them count or crash."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    p = dream_dir / "x.jsonl"
    p.write_text(
        '{"tick": 5, "experiences": [{"source": "memory", "first_person": '
        f'"{TEXT_A}"}}]}}\n'
        "THIS IS NOT JSON\n"
        "\n"
        '{"tick": 6, "experiences": [{"source": "thought", "first_person": '
        f'"{TEXT_A}"}}]}}\n'
        '{"tick": 6, "experiences": [{"source": "memory", "first_person": '
        f'"{TEXT_A}"}}]}}\n'
        '{"tick": 8}\n'
    )
    t = _tracker(tmp_path)
    before = _total_recalls(t)
    n = t.rehearse_from_dreams(dream_dir, _records())
    assert n == 2                       # (r1,5) and (r1,6) only
    assert _total_recalls(t) - before == n
    assert t.data["records"]["r1"]["recalls"] == [5, 6]


def test_critic_nonexistent_dream_dir_returns_zero(tmp_path):
    """Genome (silent defaults): a missing dream dir reports an exact 0 —
    there is genuinely nothing to rehearse, not a clamped quiet zero."""
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(tmp_path / "nope", _records()) == 0
    assert t.data["records"] == {}


# -- contract: records appearing/disappearing between passes ---------------------

def test_critic_record_disappears_between_passes(tmp_path):
    """A record absent from the second pass's record list cannot be rehearsed
    by a later log; the other record's recalls are untouched."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        {"tick": 5, "experiences": [_mem(TEXT_A), _mem(TEXT_B)]},
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 2
    _write_log(dream_dir, "d2.jsonl", [
        {"tick": 9, "experiences": [_mem(TEXT_B)]},   # r2 gone from records
        {"tick": 9, "experiences": [_mem(TEXT_A)]},   # r1 present, new tick
    ])
    recs_now = [{"id": "r1", "tick": 10, "source": "memory",
                 "first_person": TEXT_A}]
    assert t.rehearse_from_dreams(dream_dir, recs_now) == 1
    assert t.data["records"]["r1"]["recalls"] == [5, 9]
    assert t.data["records"]["r2"]["recalls"] == [5]  # frozen, not extended


def test_critic_record_appears_between_passes(tmp_path):
    """A record that only appears in the second pass's record list gets its
    pairs counted then — reprocessing the old log is what surfaces it."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        {"tick": 5, "experiences": [_mem(TEXT_A), _mem(TEXT_B)]},
    ])
    t = _tracker(tmp_path)
    only_r1 = [{"id": "r1", "tick": 10, "source": "memory",
                "first_person": TEXT_A}]
    assert t.rehearse_from_dreams(dream_dir, only_r1) == 1
    # r2 joins the workspace; reprocessing the same log surfaces its pair.
    assert t.rehearse_from_dreams(dream_dir, _records()) == 1
    assert t.data["records"]["r2"]["recalls"] == [5]


# -- contract: idempotence survives the sidecar ----------------------------------

def test_critic_idempotence_survives_save_reload(tmp_path):
    """The (record, tick) set is real persisted state, not an in-memory
    artifact: after save + fresh instance, reprocessing returns 0."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        {"tick": 5, "experiences": [_mem(TEXT_A)]},
        {"tick": 7, "experiences": [_mem(TEXT_B)]},
    ])
    sidecar = tmp_path / "salience.json"
    t = SalienceTracker(sidecar)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 2
    t.save()
    t2 = SalienceTracker(sidecar)
    assert t2.data["records"]["r1"]["recalls"] == [5]
    assert t2.rehearse_from_dreams(dream_dir, _records()) == 0


# -- genome: read-only behavior ----------------------------------------------------

def test_critic_rehearse_never_writes_dream_logs(tmp_path):
    """Genome (read-only violations): dream logs are byte-identical after any
    number of reprocessings."""
    import hashlib
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    p = _write_log(dream_dir, "d1.jsonl", [
        {"tick": 5, "experiences": [_mem(TEXT_A)]},
        {"tick": 7, "experiences": [_mem(TEXT_B)]},
    ])
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    t = _tracker(tmp_path)
    t.rehearse_from_dreams(dream_dir, _records())
    t.rehearse_from_dreams(dream_dir, _records())
    assert hashlib.sha256(p.read_bytes()).hexdigest() == digest


def test_critic_rehearse_does_not_persist_sidecar_unprompted(tmp_path):
    """rehearse_from_dreams mutates in-memory state only — no silent write
    path to the sidecar file."""
    import hashlib
    sidecar = tmp_path / "salience.json"
    t = SalienceTracker(sidecar)
    t.save()
    digest = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [{"tick": 5, "experiences": [_mem(TEXT_A)]}])
    t.rehearse_from_dreams(dream_dir, _records())
    assert hashlib.sha256(sidecar.read_bytes()).hexdigest() == digest


# -- genome: reseed reset ------------------------------------------------------------

def test_critic_reset_clears_rehearsed_state(tmp_path):
    """Genome (sidecar reset on reseed): reset() wipes rehearsed recalls, so
    recycled ids never inherit stale rehearsal — and a reprocess then counts
    the pairs again, honestly."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [{"tick": 5, "experiences": [_mem(TEXT_A)]}])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 1
    t.reset()
    assert t.data["records"] == {}
    assert t.rehearse_from_dreams(dream_dir, _records()) == 1


# -- genome: the signature-change safety claim ---------------------------------------

def test_critic_note_recall_sole_caller_is_rehearse(tmp_path):
    """The spec asserts note_recall's sole caller is rehearse_from_dreams, so
    the None -> bool signature change breaks nothing. Enforce it statically:
    any new caller of note_recall anywhere in calibos_mind/ must update this."""
    callers = set()
    for py in (ROOT / "calibos_mind").rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr == "note_recall"):
                        callers.add((py.name, node.name))
    assert callers == {("salience.py", "rehearse_from_dreams")}, callers


# -- genome: old behavior is gone ------------------------------------------------------

def test_critic_second_pass_of_identical_logs_returns_zero(tmp_path):
    """Spec fitness, restated independently: reprocessing identical dream logs
    returns 0 on the second pass and adds nothing."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        {"tick": 5, "experiences": [_mem(TEXT_A)]},
        {"tick": 7, "experiences": [_mem(TEXT_B)]},
        {"tick": 5, "experiences": [_mem(TEXT_A)]},  # dup inside the log
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 2
    assert t.rehearse_from_dreams(dream_dir, _records()) == 0
    assert t.data["records"]["r1"]["recalls"] == [5]
    assert t.data["records"]["r2"]["recalls"] == [7]
