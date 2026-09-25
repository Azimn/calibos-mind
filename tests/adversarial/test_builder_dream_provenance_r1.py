"""Builder battery R1 for Bug C: dream rehearsal provenance by text.

Tests the builder's fix (calibos_mind/provider.py + calibos_mind/salience.py):
  - DreamCognition.think stamps each fragment experience with the private
    record_id taken from the engine's SubjectiveExperience.id.
  - rehearse_from_dreams matches memory experiences by record_id when
    present; an id naming no current record is skipped with NO text
    fallback; id-less (legacy) fragments fall back to exact
    (source, first_person) text matching.
  - record_id never reaches any display path (mind recall shows
    [source] first_person only).
  - Bug B's count idempotence (note_recall -> bool) still holds through the
    new matching path.

All fixtures are synthetic /tmp stores and /tmp dream dirs. The live store
(~/workspace/calibos-mind/mind.db, inbox/, dreams/, salience.json) is never
opened. Genome checks are included explicitly.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jelly_psiduck.workspace import FeltExperience

from calibos_mind.provider import DreamCognition
from calibos_mind.salience import SalienceTracker
from calibos_mind.workspace import CalibosWorkspace

SAME_TEXT = "i left the kettle on the stove"
OTHER_TEXT = "the train was late again"


def _tracker(tmp_path):
    return SalienceTracker(tmp_path / "salience.json")


def _records():
    # r1 and r2 are DISTINCT records with IDENTICAL text — the hazard case.
    return [
        {"id": "r1", "tick": 10, "source": "memory", "first_person": SAME_TEXT},
        {"id": "r2", "tick": 20, "source": "memory", "first_person": SAME_TEXT},
        {"id": "r3", "tick": 30, "source": "memory", "first_person": OTHER_TEXT},
    ]


def _id_exp(rid, text):
    return {"source": "memory", "first_person": text, "record_id": rid}


def _old_exp(text):
    # Legacy fragment: no record_id at all.
    return {"source": "memory", "first_person": text}


def _write_log(dream_dir, name, frags):
    """Write a dream log: frags is [(tick, [experiences]), ...]."""
    path = dream_dir / name
    with path.open("w", encoding="utf-8") as f:
        for tick, exps in frags:
            f.write(json.dumps({"tick": tick, "trigger": "echo",
                                "experiences": exps}) + "\n")
    return path


def _recalls(t, rid):
    return sorted(t.data["records"][rid]["recalls"])


# -- spec fitness tests -------------------------------------------------------

def test_distinct_records_identical_text_rehearsed_independently(tmp_path):
    """(a) Two records with identical text are rehearsed independently."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "n1.jsonl", [
        (5, [_id_exp("r1", SAME_TEXT)]),
        (7, [_id_exp("r2", SAME_TEXT)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 2
    # Each record's recall set gets exactly its own tick.
    assert _recalls(t, "r1") == [5]
    assert _recalls(t, "r2") == [7]
    assert "r3" not in t.data["records"]


def test_old_idless_fragment_still_rehearses_via_text_fallback(tmp_path):
    """(b) Fragments written before record_id existed still rehearse."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "old.jsonl", [
        (9, [_old_exp(OTHER_TEXT)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 1
    assert _recalls(t, "r3") == [9]


def test_unknown_record_id_skips_with_no_text_fallback(tmp_path):
    """(c) record_id naming no current record: no credit, NO text fallback.

    The ghost id 'rG' carries SAME_TEXT, which text-matches live records r1
    and r2. If the code fell back to text here, r1 or r2 would be wrongly
    credited — the exact hazard this bug fixes.
    """
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "g.jsonl", [
        (11, [_id_exp("rG", SAME_TEXT)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 0
    assert t.data["records"] == {}, t.data["records"]


def test_mixed_new_id_and_old_fragments_in_one_run(tmp_path):
    """(d) New-id and legacy fragments coexist in a single run."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    # Legacy fragments with ambiguous text credit the text-match winner
    # (pre-existing collapsing behavior); use unambiguous legacy text here so
    # each path's target is deterministic.
    _write_log(dream_dir, "mix.jsonl", [
        (5, [_id_exp("r1", SAME_TEXT)]),       # new path -> r1
        (6, [_old_exp(OTHER_TEXT)]),           # legacy path -> r3 (text match)
        (7, [_id_exp("r2", SAME_TEXT)]),       # new path -> r2
        (8, [_id_exp("rG", SAME_TEXT)]),       # ghost id -> skipped, no fallback
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 3
    assert _recalls(t, "r1") == [5]
    assert _recalls(t, "r2") == [7]
    assert _recalls(t, "r3") == [6]


def test_record_id_never_appears_in_recall_display(tmp_path, monkeypatch, capsys):
    """(e) mind recall shows [source] first_person only; record_id stays private."""
    import calibos_mind.cli as cli
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        (5, [_id_exp("r1", SAME_TEXT)]),
        (7, [_id_exp("r2", OTHER_TEXT)]),
    ])
    monkeypatch.setattr(cli, "DREAMS", dream_dir)
    args = types.SimpleNamespace(n=5)
    assert cli.cmd_recall(args) == 0
    out = capsys.readouterr().out
    assert SAME_TEXT in out
    assert OTHER_TEXT in out
    assert "[memory]" in out
    # The private provenance id must not leak into the display.
    for token in ("r1", "r2", "record_id"):
        assert token not in out, f"leaked into recall display: {token!r}"


def test_idempotence_holds_through_new_matching_path(tmp_path):
    """(f) Bug B's count idempotence still holds via the id-matching path."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "i1.jsonl", [
        (5, [_id_exp("r1", SAME_TEXT)]),
        (6, [_id_exp("r2", SAME_TEXT)]),
        (7, [_old_exp(OTHER_TEXT)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 3
    # Reprocessing the identical logs adds nothing and counts nothing.
    assert t.rehearse_from_dreams(dream_dir, _records()) == 0
    assert _recalls(t, "r1") == [5]
    assert _recalls(t, "r2") == [6]
    assert _recalls(t, "r3") == [7]


# -- end-to-end: wired resolver through a real dream tick ---------------------

def test_end_to_end_wired_dream_tick_stamps_real_ids(tmp_path):
    """A real dream tick with the resolver wired (as cmd_dream wires it)
    stamps each surfaced memory experience with the id of the record that
    actually surfaced — verified against the live workspace records."""
    from calibos_mind.subject import CalibosSubject
    from digital_subject.cartridge import load_cartridge
    base = Path(__file__).resolve().parents[2]
    tmp = tmp_path / "e2e"
    tmp.mkdir()
    dreamer = DreamCognition(tmp / "dreams")
    cart = load_cartridge(base / "calibos.toml")
    sub = CalibosSubject(tmp / "test.db", cart, cognition=dreamer,
                         salience_path=tmp / "salience.json")
    dreamer.track_ids(lambda: sub.workspace._last_view_ids)
    with sub._transaction():
        sub._add("memory", "a synthetic lived memory for provenance",
                 concepts=("synthetic",), generated_by="cognition")
        tick = sub.engine.state.tick
        sub.endogenous["echoes"] = [{
            "text": "a half-remembered image of rain on a window",
            "parent": "experience-1", "due": tick,
            "expires": tick + 6, "depth": 1}]
    sub.dream_tick()
    assert dreamer.fragments, "no dream fragment recorded"
    live = {r.id: r for r in sub.workspace.records}
    mem_exps = [e for f in dreamer.fragments for e in f
                if e["source"] == "memory"]
    assert mem_exps, "no memory experience surfaced in the fragments"
    for e in mem_exps:
        assert "record_id" in e, f"missing record_id: {e}"
        assert e["record_id"] in live, f"stale id: {e['record_id']}"
        assert live[e["record_id"]].first_person == e["first_person"]


# -- regression genome checks -------------------------------------------------

def _real_workspace_two_memories():
    """A real CalibosWorkspace with two memory records, viewed honestly."""
    ws = CalibosWorkspace()
    ws.add(1, "memory", "the kettle sang at dawn")
    ws.add(2, "memory", "rain on the skylight")
    view = ws.view()
    return ws, view


def test_genome_provenance_honesty_view_carries_no_id():
    """Provenance-honesty (Bug A lesson): verify the engine field claim
    against the actual install, not the spec's assertion.

    The spec claimed view.experiences are SubjectiveExperience objects with
    .id. The install says otherwise: the cognitive view holds FeltExperience
    (source, first_person) — NO id. This test pins that engine truth, so a
    future "just read e.id" regression is caught here, not in production.
    """
    _, view = _real_workspace_two_memories()
    assert len(view.experiences) == 2
    for e in view.experiences:
        assert isinstance(e, FeltExperience)
        assert not hasattr(e, "id"), (
            "engine FeltExperience gained .id; the stash mechanism may be redundant")


def test_think_stamps_ids_from_resolver_positionally():
    """DreamCognition.think stamps record_id from the wired resolver, and the
    stamped id attributes each experience to the record with matching text."""
    ws, view = _real_workspace_two_memories()
    by_id = {r.id: r for r in ws.records}
    dc = DreamCognition("/tmp/unused-dream-provenance-test")
    dc.track_ids(lambda: ws._last_view_ids)
    dc.think(view)
    frag = dc.fragments[0]
    assert [d["record_id"] for d in frag] == list(ws._last_view_ids)
    for d in frag:
        assert by_id[d["record_id"]].first_person == d["first_person"]
        assert by_id[d["record_id"]].source == d["source"]


def test_unwired_provider_writes_legacy_shape():
    """Without a resolver (bare use), fragments carry no record_id at all —
    rehearsal then uses the legacy text fallback, unchanged."""
    _, view = _real_workspace_two_memories()
    dc = DreamCognition("/tmp/unused-dream-provenance-test")
    dc.think(view)
    frag = dc.fragments[0]
    assert all("record_id" not in d for d in frag)
    assert [d["first_person"] for d in frag] == [e.first_person for e in view.experiences]


def test_length_mismatch_omits_ids_rather_than_misattributing():
    """Silent-default guard: a stale resolver (wrong length) yields no ids,
    never a shifted/wrong attribution."""
    _, view = _real_workspace_two_memories()
    dc = DreamCognition("/tmp/unused-dream-provenance-test")
    dc.track_ids(lambda: ("stale-only-one",))
    dc.think(view)
    assert all("record_id" not in d for d in dc.fragments[0])


def test_genome_rehearsal_is_read_only_on_the_sidecar(tmp_path):
    """Read-only violations: rehearse_from_dreams must not write the sidecar
    file — it mutates in-memory state only; the caller saves."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "ro.jsonl", [(5, [_id_exp("r1", SAME_TEXT)])])
    sidecar = tmp_path / "salience.json"
    t = SalienceTracker(sidecar)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 1
    assert not sidecar.exists(), "rehearse_from_dreams wrote the sidecar"


def test_genome_unknown_id_is_explicit_skip_not_silent_default(tmp_path):
    """Silent defaults: an unmatched record_id must report 'undefined'/skip
    explicitly — zero credit anywhere, and never a quiet text credit."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    # Ghost id with text matching r3, plus a legacy fragment for r3 at the
    # same tick: proves the ghost adds nothing while the legacy path works.
    _write_log(dream_dir, "s.jsonl", [
        (13, [_id_exp("rG", OTHER_TEXT)]),
        (13, [_old_exp(OTHER_TEXT)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 1
    assert _recalls(t, "r3") == [13]
    assert "rG" not in t.data["records"]
