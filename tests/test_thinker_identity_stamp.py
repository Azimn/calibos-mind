"""Tests for the thinker-identity stamp (Domain 23, LLM leakage).

Spec: research/specs/2026-11-16-thinker-identity-stamp.md
All fixtures live in /tmp — the live store is never touched (the
autouse conftest fixture redirects every cli store path at a fresh
tmp tree; paired-store tests re-redirect the complete set via
redirect_all_cli_paths, never a hand-rolled partial set).

The stamp is provenance metadata in the same class as `generated_by`:
it records which thinker authored a thought so a host-model swap is
observable in the store. It must never change retrieval, salience,
drift, or conduct.
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from conftest import redirect_all_cli_paths
from calibos_mind import cli
from calibos_mind.subject import validate_thinker_label
from calibos_mind.thinkers import is_self_authored, thinkers_report
from calibos_mind.workspace import CalibosWorkspace


def _capture(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


def _records():
    return cli._subject().inspect()["workspace"]["records"]


def _payload():
    return cli._subject().inspect()


def _thoughts():
    return [r for r in _records() if r["source"] == "thought"]


def _think(text, *extra):
    return _capture("think", *extra, text)


# 1. Stamp contract ------------------------------------------------------

def test_think_stamps_record_with_thinker():
    rc, _ = _think("a stamped voluntary thought", "--thinker", "model-A")
    assert rc == 0
    matches = [r for r in _thoughts()
               if r["first_person"] == "a stamped voluntary thought"]
    assert len(matches) == 1
    assert matches[0]["thinker"] == "model-A"
    assert matches[0]["generated_by"] == "voluntary"


def test_answer_stamps_record_with_thinker():
    rc, _ = _capture("queue", "an invitation to think")
    assert rc == 0
    rc, _ = _capture("answer", "prompt-0001", "a considered answer",
                     "--thinker", "model-B")
    assert rc == 0
    matches = [r for r in _thoughts()
               if r["first_person"] == "a considered answer"]
    assert len(matches) == 1
    assert matches[0]["thinker"] == "model-B"
    # `mind queue` authors externally, so the origin is answered-external.
    assert matches[0]["generated_by"].startswith(
        "answered-external:prompt-0001@")


def test_distinct_labels_produce_distinct_stamps():
    _think("first stamped thought", "--thinker", "model-A")
    _think("second stamped thought", "--thinker", "model-B")
    by_text = {r["first_person"]: r for r in _thoughts()}
    assert by_text["first stamped thought"]["thinker"] == "model-A"
    assert by_text["second stamped thought"]["thinker"] == "model-B"


def test_thinker_label_is_stripped():
    rc, _ = _think("a stripped label thought", "--thinker", "  model-A  ")
    assert rc == 0
    match = next(r for r in _thoughts()
                 if r["first_person"] == "a stripped label thought")
    assert match["thinker"] == "model-A"


def test_wake_affirm_records_unstamped_wake_thought():
    rc, _ = _capture("wake", "--affirm", "I assume the identity deliberately")
    assert rc == 0
    matches = [r for r in _thoughts()
               if r["first_person"] == "I assume the identity deliberately"]
    assert len(matches) == 1
    assert matches[0]["generated_by"] == "wake"
    assert "thinker" not in matches[0]


# 2. Unstamped behavior is byte-identical to pre-mutation ----------------

def test_unstamped_think_carries_no_thinker_key():
    rc, _ = _think("a plain voluntary thought")
    assert rc == 0
    match = next(r for r in _thoughts()
                 if r["first_person"] == "a plain voluntary thought")
    assert "thinker" not in match
    assert set(match.keys()) == {
        "id", "tick", "source", "first_person", "salience", "intensity",
        "concepts", "affect", "memory_links", "concern_links",
        "expectation_links", "generated_by", "private",
        "available_to_cognition",
    }


def test_stamped_record_differs_only_by_the_stamp(tmp_path, monkeypatch):
    """Paired fresh stores: the same think, one stamped, one not. The
    stamped record dict must equal the unstamped one modulo the stamp."""
    recs = {}
    for half, extra in (("a", ["--thinker", "model-A"]), ("b", [])):
        redirect_all_cli_paths(tmp_path / half, setter=monkeypatch.setattr)
        rc, _ = _think("the paired thought", *extra)
        assert rc == 0
        recs[half] = next(r for r in _thoughts()
                          if r["first_person"] == "the paired thought")
    stamped = dict(recs["a"])
    assert stamped.pop("thinker") == "model-A"
    assert stamped == recs["b"]


# 3. Label validation -----------------------------------------------------

@pytest.mark.parametrize("bad", [
    "",
    "   ",
    "x" * 81,
    "model\nA",
    "model\rA",
    "model\x01A",
    "model\x7fA",
])
def test_validate_thinker_label_rejects(bad):
    with pytest.raises(ValueError):
        validate_thinker_label(bad)


def test_validate_thinker_label_accepts_boundary():
    assert validate_thinker_label("x" * 80) == "x" * 80
    assert validate_thinker_label("  model-A  ") == "model-A"


@pytest.mark.parametrize("bad", ["", "   ", "x" * 81, "bad\nlabel"])
def test_think_rejects_invalid_label_without_recording(bad):
    before = len(_records())
    rc, out = _think("a thought with a bad label", "--thinker", bad)
    assert rc == 1
    assert "refused" in out.lower()
    assert "nothing recorded" in out
    assert len(_records()) == before


def test_answer_rejects_invalid_label_and_keeps_prompt():
    rc, _ = _capture("queue", "an invitation to think")
    assert rc == 0
    before = len(_records())
    rc, out = _capture("answer", "prompt-0001", "a considered answer",
                       "--thinker", "bad\nlabel")
    assert rc == 1
    assert "refused" in out.lower()
    assert "prompt kept" in out
    assert len(_records()) == before
    # The prompt survived: answering it cleanly still works.
    rc, _ = _capture("answer", "prompt-0001", "a considered answer")
    assert rc == 0


def test_answer_refuses_thinker_with_silent_and_consumes_nothing():
    rc, _ = _capture("queue", "an invitation to think")
    assert rc == 0
    rc, out = _capture("answer", "prompt-0001", "--silent",
                       "--thinker", "model-A")
    assert rc == 1
    assert "--thinker" in out
    # Prompt still waiting: it was not consumed.
    rc, _ = _capture("answer", "prompt-0001", "--silent")
    assert rc == 0


# 4. Report ---------------------------------------------------------------

def _seed_mix():
    _think("alpha thought one", "--thinker", "alpha")
    _think("beta thought one", "--thinker", "beta")
    _think("unstamped thought one")
    _think("alpha thought two", "--thinker", "alpha")
    _think("beta thought two", "--thinker", "beta")
    _capture("wake", "--affirm", "wake thought, unstamped")


def test_thinkers_counts_exact_mix():
    _seed_mix()
    rc, out = _capture("thinkers")
    assert rc == 0
    # Labels print JSON-quoted, so no label line can ever reproduce the
    # reserved `unstamped: N` line (round-3 fix for the disambiguator
    # collision); the reserved line itself stays unquoted.
    assert '"alpha": 2' in out
    assert '"beta": 2' in out
    assert "unstamped: 2" in out  # one plain think + one wake --affirm
    assert out.count("unstamped:") == 1
    rep = thinkers_report(_records())
    assert rep["counts"] == {"alpha": 2, "beta": 2}
    assert rep["unstamped"] == 2
    assert rep["n"] == 6
    assert rep["tick_min"] is not None and rep["tick_max"] is not None
    assert rep["tick_min"] <= rep["tick_max"]


def test_thinkers_window_bounds_the_scan():
    _seed_mix()
    # Most recent 3 thinker-authored: unstamped think, alpha two, beta two.
    rep = thinkers_report(_records(), window=3)
    assert rep["n"] == 3
    assert rep["counts"] == {"alpha": 1, "beta": 1}
    assert rep["unstamped"] == 1
    rc, out = _capture("thinkers", "--window", "3")
    assert rc == 0
    assert "last 3 thinker-authored thoughts" in out


def test_thinkers_empty_store_reports_undefined_range():
    rep = thinkers_report(_records())
    assert rep == {"counts": {}, "unstamped": 0, "n": 0,
                   "tick_min": None, "tick_max": None}
    rc, out = _capture("thinkers")
    assert rc == 0
    assert "no thinker-authored thoughts" in out
    assert "undefined" in out


def test_thinkers_rejects_nonpositive_window():
    rc, out = _capture("thinkers", "--window", "0")
    assert rc == 1
    assert "at least 1" in out


def test_thinkers_excludes_non_self_authored_thoughts():
    _think("a stamped thought", "--thinker", "alpha")
    # Heartbeat cognition thoughts (generated_by="cognition") and
    # cartridge seeds are not thinker-authored and never enter the count.
    rc, _ = _capture("heartbeat", "--ticks", "2")
    assert rc == 0
    rep = thinkers_report(_records())
    assert rep["n"] == 1
    assert rep["counts"] == {"alpha": 1}
    assert rep["unstamped"] == 0


def test_is_self_authored_set():
    for gb in ("voluntary", "voluntary:inspection", "wake",
               "answered:prompt-0001@12",
               "answered-external:prompt-0002@12"):
        assert is_self_authored(gb) is True
    for gb in ("cognition", "dream-derived", "chat", "cartridge",
               None, "", "answered"):
        assert is_self_authored(gb) is False


# 5. Persistence ----------------------------------------------------------

def test_stamp_survives_save_load_and_restore():
    _think("a stamped thought to persist", "--thinker", "model-A")
    _think("an unstamped thought to persist")
    # A fresh subject construction restores from the DB payload, and
    # every inject opens a transaction that restores again: the stamp
    # must survive both.
    recs = _records()
    match = next(r for r in recs
                 if r["first_person"] == "a stamped thought to persist")
    assert match["thinker"] == "model-A"
    plain = next(r for r in recs
                 if r["first_person"] == "an unstamped thought to persist")
    assert "thinker" not in plain
    # A second think forces another restore cycle; the stamp is still there.
    _think("a later thought")
    recs = _records()
    match = next(r for r in recs
                 if r["first_person"] == "a stamped thought to persist")
    assert match["thinker"] == "model-A"


def test_workspace_from_dict_round_trips_stamps():
    data = {"records": [
        {"id": "experience-1", "tick": 0, "source": "thought",
         "first_person": "stamped legacy", "generated_by": "voluntary",
         "thinker": "model-Z"},
        {"id": "experience-2", "tick": 1, "source": "thought",
         "first_person": "unstamped legacy", "generated_by": "voluntary"},
    ], "sequence": 2}
    ws = CalibosWorkspace.from_dict(data)
    assert ws._thinker_stamps == {"experience-1": "model-Z"}
    out = ws.to_dict()
    assert out["records"][0]["thinker"] == "model-Z"
    assert "thinker" not in out["records"][1]
    assert out["sequence"] == 2


def test_workspace_from_dict_treats_legacy_records_as_unstamped():
    data = {"records": [
        {"id": "experience-1", "tick": 0, "source": "thought",
         "first_person": "legacy thought", "generated_by": "voluntary"},
    ], "sequence": 1}
    ws = CalibosWorkspace.from_dict(data)
    assert ws._thinker_stamps == {}
    assert "thinker" not in ws.to_dict()["records"][0]


def test_workspace_from_dict_drops_stamp_on_non_thought_source():
    # Round-3 fix: from_dict is a second landing path and must enforce
    # the same source rule as add() — a stamp persisted on a "memory"
    # record (e.g. written by the round-1 build, which stamped any
    # source) is dropped at the load boundary, never laundered back
    # into the store by to_dict().
    data = {"records": [
        {"id": "experience-1", "tick": 0, "source": "memory",
         "first_person": "a cartridge memory", "generated_by": "cartridge",
         "thinker": "model-A"},
        {"id": "experience-2", "tick": 1, "source": "thought",
         "first_person": "a genuine thought", "generated_by": "voluntary",
         "thinker": "model-A"},
    ], "sequence": 2}
    ws = CalibosWorkspace.from_dict(data)
    assert ws._thinker_stamps == {"experience-2": "model-A"}
    stamped = [r for r in ws.to_dict()["records"] if "thinker" in r]
    assert [r["source"] for r in stamped] == ["thought"]
    ws2 = CalibosWorkspace.from_dict(ws.to_dict())
    assert ws2.to_dict() == ws.to_dict()


def test_thinkers_report_quotes_labels_so_none_can_forge_a_line():
    # Round-3 fix: the round-2 disambiguator "unstamped (thinker
    # label)" is itself a valid free-text label, so two labels printed
    # byte-identical lines. JSON-quoted label lines are injective and
    # can never reproduce the unquoted reserved line.
    _think("a thought from a host literally called unstamped",
           "--thinker", "unstamped")
    _think("a thought from a host called unstamped (thinker label)",
           "--thinker", "unstamped (thinker label)")
    rc, out = _capture("thinkers")
    assert rc == 0
    label_lines = [l for l in out.splitlines() if "unstamped" in l]
    assert len(label_lines) == len(set(label_lines)), (
        "two distinct labels printed identical report lines:\n" + out)
    assert '"unstamped": 1' in out
    assert '"unstamped (thinker label)": 1' in out
    assert "unstamped: 0" in out  # reserved legacy-count line, unquoted


# 6. No behavioral delta ---------------------------------------------------

def _paired_stores(tmp_path, monkeypatch, stamp_argv):
    """Two synthetic stores differing ONLY in the thinker stamp."""
    outs = []
    for half in ("a", "b"):
        redirect_all_cli_paths(tmp_path / half, setter=monkeypatch.setattr)
        stamp = stamp_argv[half]
        rc, _ = _capture("think", *stamp, "the lamp hums in the dark")
        assert rc == 0
        rc, _ = _capture("think", *stamp, "hunger rises slowly now")
        assert rc == 0
        drift_rc, drift = _capture("drift")
        assert drift_rc == 0
        status_rc, status = _capture("status")
        assert status_rc == 0
        review_rc, review = _capture("review")
        assert review_rc == 0
        subject = cli._subject()
        view_ids = subject.workspace.view().record_ids
        outs.append({
            "drift": drift, "status": status, "review": review,
            "view_ids": view_ids,
            "salience": (tmp_path / half / "salience.json").read_bytes(),
            "records": subject.inspect()["workspace"]["records"],
        })
    return outs


def test_paired_stores_byte_identical_surfaces(tmp_path, monkeypatch):
    outs = _paired_stores(tmp_path, monkeypatch,
                          {"a": [], "b": ["--thinker", "model-A"]})
    a, b = outs
    assert a["drift"] == b["drift"], "drift output must be byte-identical"
    assert a["status"] == b["status"], "status output must be byte-identical"
    assert a["review"] == b["review"], "review output must be byte-identical"
    assert a["salience"] == b["salience"], \
        "salience sidecar must be byte-identical"
    assert a["view_ids"] == b["view_ids"], \
        "admitted view ids must be identical"
    stripped = [{k: v for k, v in r.items() if k != "thinker"}
                for r in b["records"]]
    assert stripped == a["records"], \
        "records must match modulo the stamp itself"


def test_paired_stores_identical_after_heartbeat(tmp_path, monkeypatch):
    """A waking tick on paired stores: same conduct, same records."""
    outs = _paired_stores(tmp_path, monkeypatch,
                          {"a": [], "b": ["--thinker", "model-A"]})
    # _paired_stores leaves the "b" store active; re-run both halves.
    results = []
    for half in ("a", "b"):
        redirect_all_cli_paths(tmp_path / half, setter=monkeypatch.setattr)
        rc, out = _capture("heartbeat", "--ticks", "2")
        assert rc == 0
        results.append({
            "heartbeat": out,
            "records": [{k: v for k, v in r.items() if k != "thinker"}
                        for r in cli._subject().inspect()["workspace"]["records"]],
            "trace": cli._subject().inspect()["trace"],
        })
    assert results[0]["heartbeat"] == results[1]["heartbeat"], \
        "heartbeat output must be byte-identical"
    assert results[0]["records"] == results[1]["records"], \
        "records after a waking tick must match modulo the stamp"
    assert results[0]["trace"] == results[1]["trace"], \
        "conduct trace must be identical"


# 7. Read-only sweep -------------------------------------------------------

def test_readonly_sweep_leaves_payload_identical():
    _seed_mix()
    before = _payload()
    for argv in (["drift"], ["status"], ["review"], ["thinkers"], ["inbox"]):
        rc, _ = _capture(*argv)
        assert rc == 0, f"{argv} must succeed"
    after = _payload()
    assert before == after, "read-only commands must not mutate the payload"


# 8. Stamp containment ------------------------------------------------------

def test_dream_never_stamps():
    Path(cli.DREAMS).mkdir(parents=True, exist_ok=True)
    _think("a stamped waking thought", "--thinker", "model-A")
    rc, _ = _capture("dream", "--ticks", "2")
    assert rc == 0
    stamped = [r for r in _records() if "thinker" in r]
    assert len(stamped) == 1
    assert stamped[0]["first_person"] == "a stamped waking thought"
    dream_derived = [r for r in _records()
                     if r.get("generated_by") == "dream-derived"]
    assert all("thinker" not in r for r in dream_derived)


def test_heartbeat_and_note_records_never_carry_stamps():
    _think("a stamped thought", "--thinker", "model-A")
    rc, _ = _capture("heartbeat", "--ticks", "2")
    assert rc == 0
    rc, _ = _capture("note", "something observed in the world")
    assert rc == 0
    for r in _records():
        if r["first_person"] == "a stamped thought":
            assert r["thinker"] == "model-A"
        else:
            assert "thinker" not in r, \
                f"non-thinker-authored record carries a stamp: {r['id']} " \
                f"({r['source']}, {r.get('generated_by')})"


def test_init_force_wipes_stamped_records():
    _think("a stamped record to be wiped", "--thinker", "model-A")
    assert any("thinker" in r for r in _records())
    rc, _ = _capture("init", "--force")
    assert rc == 0
    assert not any("thinker" in r for r in _records()), \
        "init --force must wipe stamped records"
