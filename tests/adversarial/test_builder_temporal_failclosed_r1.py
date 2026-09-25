"""Builder battery R1 for temporal fail-closed rehearsal semantics.

Tests the builder's fix (calibos_mind/salience.py + calibos_mind/cli.py):
  - rehearse_from_dreams validates every dream fragment's tick FAIL-CLOSED.
    A fragment whose tick is missing, null, non-integer, negative, or
    future-relative-to-engine yields NO rehearsal mutation for its
    experiences, records an EXPLICIT diagnostic on tracker.diagnostics, and
    is NEVER reinterpreted as tick 0 (tick 0 is legitimate engine time).
  - The future check takes an optional now_tick; cmd_dream passes
    subject.engine.state.tick (fragments are written with the frozen dream
    tick, so a fragment tick above the current engine tick is causally
    impossible). now_tick=None skips the future check (documented degraded
    validation).
  - activation() hardens against already-stored malformed recall ticks
    (defense in depth for sidecars written before this fix).

All fixtures are synthetic /tmp stores and /tmp dream dirs. The live store
(~/workspace/calibos-mind/mind.db, inbox/, dreams/, salience.json) is never
opened. Genome checks are included explicitly.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from calibos_mind.salience import SalienceTracker

ROOT = Path(__file__).resolve().parents[2]

TEXT_A = "i left the kettle on the stove"
TEXT_B = "the train was late again"


def _tracker(tmp_path):
    return SalienceTracker(tmp_path / "salience.json")


def _records():
    return [
        {"id": "r1", "tick": 10, "source": "memory", "first_person": TEXT_A},
        {"id": "r2", "tick": 20, "source": "memory", "first_person": TEXT_B},
    ]


def _mem(rid, text):
    return {"source": "memory", "first_person": text, "record_id": rid}


def _write_frags(dream_dir, name, frags):
    """Write a dream log from full fragment dicts (caller controls "tick")."""
    path = dream_dir / name
    with path.open("w", encoding="utf-8") as f:
        for frag in frags:
            f.write(json.dumps(frag) + "\n")
    return path


def _frag(tick_value, exps, include_tick=True):
    frag = {"trigger": "echo", "experiences": exps}
    if include_tick:
        frag["tick"] = tick_value
    return frag


def _reasons(t):
    return [d["reason"] for d in t.diagnostics]


# -- the six malformed classes ------------------------------------------------

def test_missing_tick_no_mutation_explicit_diagnostic(tmp_path):
    """(1) A fragment with NO 'tick' key: no rehearsal, explicit diagnostic,
    never reinterpreted as tick 0."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "m.jsonl", [
        _frag(None, [_mem("r1", TEXT_A)], include_tick=False),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=50) == 0
    # No recall history was altered anywhere — and 0 was NOT credited.
    assert t.data["records"] == {}, t.data["records"]
    assert _reasons(t) == ["missing_tick"]
    d = t.diagnostics[0]
    assert d["log"] == "m.jsonl" and d["line"] == 1


def test_null_tick_no_mutation_no_crash_on_activation(tmp_path):
    """(2) An explicit "tick": null: no rehearsal, explicit diagnostic — and
    crucially no None reaches the recall set, so activation() cannot crash
    on `now_tick - t` (the pre-existing hazard)."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "n.jsonl", [
        _frag(None, [_mem("r1", TEXT_A)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=50) == 0
    assert t.data["records"] == {}, t.data["records"]
    assert _reasons(t) == ["null_tick"]


def test_non_integer_float_tick_rejected(tmp_path):
    """(3) A float tick (5.5): non-integer -> no rehearsal, explicit
    diagnostic."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "f.jsonl", [
        _frag(5.5, [_mem("r1", TEXT_A)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=50) == 0
    assert t.data["records"] == {}, t.data["records"]
    assert _reasons(t) == ["non_integer_tick"]


def test_negative_tick_rejected(tmp_path):
    """(4) A negative tick: no rehearsal, explicit diagnostic."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "g.jsonl", [
        _frag(-3, [_mem("r1", TEXT_A)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=50) == 0
    assert t.data["records"] == {}, t.data["records"]
    assert _reasons(t) == ["negative_tick"]


def test_future_tick_rejected_when_now_tick_given(tmp_path):
    """(5) A fragment tick above now_tick: causally impossible -> rejected.
    The boundary (tick == now_tick) still rehearses."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "u.jsonl", [
        _frag(51, [_mem("r1", TEXT_A)]),   # future relative to now_tick=50
        _frag(50, [_mem("r2", TEXT_B)]),   # boundary: exactly now_tick
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=50) == 1
    assert t.data["records"]["r2"]["recalls"] == [50]
    assert "r1" not in t.data["records"]
    assert _reasons(t) == ["future_tick"]


def test_bool_tick_rejected(tmp_path):
    """(6a) bool is an int subclass in Python but never a real tick ->
    rejected, not silently accepted as 1/0."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "b.jsonl", [
        _frag(True, [_mem("r1", TEXT_A)]),
        _frag(False, [_mem("r2", TEXT_B)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=50) == 0
    assert t.data["records"] == {}, t.data["records"]
    assert _reasons(t) == ["non_integer_tick", "non_integer_tick"]


def test_string_tick_rejected(tmp_path):
    """(6b) A string tick ("5" — even one that parses): rejected, never
    coerced."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "s.jsonl", [
        _frag("5", [_mem("r1", TEXT_A)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=50) == 0
    assert t.data["records"] == {}, t.data["records"]
    assert _reasons(t) == ["non_integer_tick"]


# -- positive case ------------------------------------------------------------

def test_valid_tick_zero_rehearses_normally(tmp_path):
    """Tick 0 is legitimate engine time, not an error sentinel: a fragment
    with tick 0 rehearses exactly like any valid tick."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "z.jsonl", [
        _frag(0, [_mem("r1", TEXT_A)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=50) == 1
    assert t.data["records"]["r1"]["recalls"] == [0]
    assert t.diagnostics == []


# -- per-fragment fail-closed: one bad fragment poisons nothing ---------------

def test_malformed_fragment_does_not_block_sibling_fragments(tmp_path):
    """Fail-closed is per-fragment: a malformed fragment's siblings in the
    same log still rehearse."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "x.jsonl", [
        _frag(5, [_mem("r1", TEXT_A)]),
        _frag(None, [_mem("r2", TEXT_B)]),   # null tick: skipped alone
        _frag(7, [_mem("r2", TEXT_B)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=50) == 2
    assert t.data["records"]["r1"]["recalls"] == [5]
    assert t.data["records"]["r2"]["recalls"] == [7]
    assert _reasons(t) == ["null_tick"]
    assert t.diagnostics[0]["line"] == 2  # 1-based, points at the bad fragment


def test_diagnostics_refreshed_per_call_not_accumulated(tmp_path):
    """diagnostics reflects exactly the call that just ran."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "a.jsonl", [_frag(None, [_mem("r1", TEXT_A)])])
    _write_frags(dream_dir, "b.jsonl", [_frag(-1, [_mem("r1", TEXT_A)])])
    t = _tracker(tmp_path)
    t.rehearse_from_dreams(dream_dir, _records(), now_tick=50)
    assert _reasons(t) == ["null_tick", "negative_tick"]
    t.rehearse_from_dreams(dream_dir, _records(), now_tick=50)
    # Still 2, not 4: refreshed, not accumulated.
    assert _reasons(t) == ["null_tick", "negative_tick"]


# -- degraded validation: now_tick=None ---------------------------------------

def test_future_check_skipped_when_now_tick_is_none(tmp_path):
    """Documented degraded validation: without a now-reference the future
    check cannot run, so a far-future tick rehearses. The CLI always passes
    a now-reference; this documents the fallback's behavior."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "d.jsonl", [
        _frag(99999, [_mem("r1", TEXT_A)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 1
    assert t.data["records"]["r1"]["recalls"] == [99999]
    assert t.diagnostics == []


# -- defense in depth: activation() on already-stored malformed recalls ------

def test_activation_skips_preexisting_malformed_recalls_without_crashing(
        tmp_path):
    """Sidecars written before fail-closed validation may carry malformed
    recall ticks (None passed through by the old code). activation() must
    not crash on them, and they must contribute nothing to the score."""
    t = _tracker(tmp_path)
    t.note_recall("r1", 10, 5)
    t.data["records"]["r1"]["recalls"].extend([None, "7", True, -2.5])
    # No TypeError from `now_tick - t` on the malformed entries.
    got = t.activation("r1", 10, 30)
    assert isinstance(got, float)

    clean = _tracker(tmp_path.parent / "clean")
    clean.note_recall("r1", 10, 5)
    # The malformed entries contribute exactly nothing: identical score.
    assert got == clean.activation("r1", 10, 30)


def test_activation_still_counts_valid_recalls(tmp_path):
    """The hardening filter must not drop legitimate recall ticks."""
    t = _tracker(tmp_path)
    t.note_recall("r1", 10, 5)
    t.note_recall("r1", 10, 0)   # tick 0 is legitimate
    assert t.data["records"]["r1"]["recalls"] == [5, 0]
    got = t.activation("r1", 10, 30)
    # Both valid recalls shape the score; a single-recall baseline differs.
    single = _tracker(tmp_path.parent / "single")
    single.note_recall("r1", 10, 5)
    assert got != single.activation("r1", 10, 30)


# -- wiring -------------------------------------------------------------------

def test_cli_wires_engine_now_tick_into_rehearsal():
    """The future check needs a now-reference: the sole caller (cmd_dream)
    must pass subject.engine.state.tick as now_tick. Enforced statically so
    a future edit cannot silently drop the degraded-validation guard."""
    src = (ROOT / "calibos_mind" / "cli.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "rehearse_from_dreams"]
    assert len(calls) == 1, calls
    kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert kw.get("now_tick") == "subject.engine.state.tick", kw


# -- regression genome checks -------------------------------------------------

def test_genome_rehearsal_stays_read_only_on_the_sidecar(tmp_path):
    """Read-only violations: rejection diagnostics are in-memory only —
    rehearse_from_dreams must not write the sidecar file, and a save() must
    not persist diagnostics."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "ro.jsonl", [_frag(None, [_mem("r1", TEXT_A)])])
    sidecar = tmp_path / "salience.json"
    t = SalienceTracker(sidecar)
    t.rehearse_from_dreams(dream_dir, _records(), now_tick=50)
    assert not sidecar.exists(), "rehearse_from_dreams wrote the sidecar"
    assert len(t.diagnostics) == 1
    t.save()
    assert "diagnostics" not in json.loads(sidecar.read_text(encoding="utf-8"))
    t2 = SalienceTracker(sidecar)
    assert t2.diagnostics == [], "diagnostics must not survive a save/load"


def test_genome_malformed_tick_is_explicit_rejection_not_silent_default(
        tmp_path):
    """Silent defaults: every rejected fragment leaves an explicit,
    machine-readable reason — never a quiet zero, never a log line only."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_frags(dream_dir, "e.jsonl", [
        _frag(None, [_mem("r1", TEXT_A)], include_tick=False),
        _frag(None, [_mem("r1", TEXT_A)]),
        _frag(4.5, [_mem("r1", TEXT_A)]),
        _frag(-1, [_mem("r1", TEXT_A)]),
        _frag(101, [_mem("r1", TEXT_A)]),
        _frag(True, [_mem("r1", TEXT_A)]),
        _frag("12", [_mem("r1", TEXT_A)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records(), now_tick=100) == 0
    assert t.data["records"] == {}
    assert _reasons(t) == [
        "missing_tick", "null_tick", "non_integer_tick", "negative_tick",
        "future_tick", "non_integer_tick", "non_integer_tick",
    ]
    # Every diagnostic is a complete, self-describing record.
    for d in t.diagnostics:
        assert set(d) == {"reason", "raw", "log", "line"}
        assert d["log"] == "e.jsonl"
