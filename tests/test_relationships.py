"""Tests for the relationship contact-gap instrument (`mind relationships`).

Spec: research/specs/2026-11-19-relationship-contact-gap.md (Domain 26).
Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python -m pytest tests/test_relationships.py -q
All fixtures live in /tmp — the live store is never touched (the autouse
conftest fixture redirects every cli store path at a synthetic tree).
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.relationships import STALE_AFTER, relationships_report


# ---------------------------------------------------------------- pure function

def _state(tick, rels):
    return {"engine": {"tick": tick, "relationships": rels},
            "workspace": {"records": []}, "trace": []}


def _rel(last, fam=0.055):
    rel = {"trust": 0.32, "comfort": 0.28, "respect": 0.35,
           "familiarity": fam, "uncertainty": 1.0}
    if last is not None:
        rel["last_contact_tick"] = last
    return rel


def test_gap_arithmetic_exact_live_values():
    """The live gaps from the spec (143/533/670 at tick 1199) must come
    out exactly: 1056/666/529, all stale."""
    rep = relationships_report(_state(1199, {
        "kiki": _rel(143), "jay": _rel(533), "rowan": _rel(670)}))
    by_person = {r["person"]: r for r in rep["rows"]}
    assert by_person["kiki"]["gap"] == 1056, by_person
    assert by_person["jay"]["gap"] == 666, by_person
    assert by_person["rowan"]["gap"] == 529, by_person
    assert all(r["stale"] for r in rep["rows"]), rep
    assert rep["tick"] == 1199 and rep["n_relationships"] == 3


def test_gap_zero_at_current_tick():
    rep = relationships_report(_state(100, {"kiki": _rel(100)}))
    row = rep["rows"][0]
    assert row["gap"] == 0 and row["stale"] is False
    assert row["last_contact_tick"] == 100


def test_staleness_boundary_500_501():
    assert STALE_AFTER == 500
    rep = relationships_report(_state(1000, {
        "a": _rel(501),   # gap 499
        "b": _rel(500),   # gap 500
        "c": _rel(499),   # gap 501
    }))
    by_person = {r["person"]: r for r in rep["rows"]}
    assert by_person["a"]["gap"] == 499 and by_person["a"]["stale"] is False
    assert by_person["b"]["gap"] == 500 and by_person["b"]["stale"] is False
    assert by_person["c"]["gap"] == 501 and by_person["c"]["stale"] is True


def test_rows_sorted_by_person_id_each_exactly_once():
    rep = relationships_report(_state(50, {
        "zoe": _rel(10), "amy": _rel(20), "kiki": _rel(30)}))
    assert [r["person"] for r in rep["rows"]] == ["amy", "kiki", "zoe"]
    assert len(rep["rows"]) == 3


def test_empty_relationships_reports_no_rows():
    rep = relationships_report(_state(12, {}))
    assert rep["rows"] == [] and rep["n_relationships"] == 0


def test_missing_last_contact_tick_is_undefined_not_zero():
    # Pure-function level only, by design: through the engine round-trip
    # the Relationship dataclass default would refill a missing key with
    # 0, so the CLI path can never observe the absence — but the report
    # must still treat a missing stamp as undefined, never as gap 0,
    # for any hand-built or corrupted store handed to it directly.
    rep = relationships_report(_state(100, {
        "kiki": _rel(None), "jay": _rel(90)}))
    by_person = {r["person"]: r for r in rep["rows"]}
    kiki = by_person["kiki"]
    assert kiki["gap"] is None and kiki["last_contact_tick"] is None
    assert kiki["stale"] is False, "undefined gap must never flag stale"
    assert by_person["jay"]["gap"] == 10


@pytest.mark.parametrize("rels", [
    {"kiki": {"familiarity": "high", "last_contact_tick": 10}},
    {"kiki": {"familiarity": None, "last_contact_tick": 10}},
    {"kiki": {"familiarity": True, "last_contact_tick": 10}},
    {"kiki": {"last_contact_tick": 10}},                       # missing familiarity
    {"kiki": {"familiarity": 0.05, "last_contact_tick": "10"}},
    {"kiki": {"familiarity": 0.05, "last_contact_tick": 10.0}},
    {"kiki": {"familiarity": 0.05, "last_contact_tick": True}},
    {"kiki": {"familiarity": 0.05, "last_contact_tick": 101}},  # future of tick 100
    {"kiki": "not-a-dict"},
    {"kiki": {"familiarity": float("nan"), "last_contact_tick": 10}},
])
def test_malformed_relationship_values_fail_loud(rels):
    with pytest.raises(ValueError):
        relationships_report(_state(100, rels))


@pytest.mark.parametrize("state", [
    {"workspace": {"records": []}},                            # no engine
    {"engine": {"relationships": {}}},                         # no tick
    {"engine": {"tick": "100", "relationships": {}}},          # tick not int
    {"engine": {"tick": True, "relationships": {}}},           # tick is bool
    {"engine": {"tick": 100}},                                 # no relationships
    {"engine": {"tick": 100, "relationships": ["kiki"]}},      # not a dict
])
def test_malformed_store_shape_fails_loud(state):
    with pytest.raises(ValueError):
        relationships_report(state)


def test_report_does_not_mutate_input_state():
    state = _state(1199, {"kiki": _rel(143), "jay": _rel(533)})
    before = copy.deepcopy(state)
    relationships_report(state)
    assert state == before


def test_report_is_deterministic():
    state = _state(1199, {"kiki": _rel(143), "jay": _rel(533)})
    assert relationships_report(state) == relationships_report(state)
    assert (json.dumps(relationships_report(state), sort_keys=True)
            == json.dumps(relationships_report(state), sort_keys=True))


# ---------------------------------------------------------------- CLI integration

def _run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def _logical_store():
    """Canonical payload content (genome: CLI construction rewrites the
    SQLite payload row, so raw file bytes move; payload *content* is the
    correct no-write scope for a read-only command)."""
    return json.dumps(cli._subject().inspect(), sort_keys=True)


def _raw_payload():
    """The payload string itself, straight from the SQLite row."""
    db = sqlite3.connect(cli.DB)
    try:
        return db.execute("SELECT payload FROM subject WHERE id=1").fetchone()[0]
    finally:
        db.close()


def _rewrite_payload(mutate):
    """Edit the synthetic store's payload JSON in place (test-only
    surgery on the tmp DB the conftest fixture redirected cli.DB to)."""
    db = sqlite3.connect(cli.DB)
    try:
        raw = db.execute("SELECT payload FROM subject WHERE id=1").fetchone()[0]
        payload = json.loads(raw)
        mutate(payload)
        db.execute("UPDATE subject SET payload=? WHERE id=1",
                   (json.dumps(payload),))
        db.commit()
    finally:
        db.close()


def _seed_contact(person, prompt, answer):
    rc, _ = _run(["queue", prompt, "--from", person])
    assert rc == 0, rc
    pid = sorted(p.name for p in cli.INBOX.glob("prompt-*.json"))[-1][:-5]
    rc, _ = _run(["answer", pid, answer])
    assert rc == 0, rc
    # The contact event is pending until a tick processes it (same as
    # test_person_contact: answer, then heartbeat, then relationships).
    rc, _ = _run(["heartbeat", "--ticks", "1"])
    assert rc == 0, rc


def test_cli_reports_real_relationships_with_exact_gaps():
    cli._subject()
    _seed_contact("kiki", "have you dreamt lately?", "a thought about kiki")
    _seed_contact("jay", "quiet hello", "a thought about jay")
    rc, _ = _run(["heartbeat", "--ticks", "3"])
    assert rc == 0, rc
    state = cli._subject().inspect()
    tick = state["engine"]["tick"]
    expected = {p: tick - r["last_contact_tick"]
                for p, r in state["engine"]["relationships"].items()}
    assert set(expected) == {"kiki", "jay"}, expected
    rc, out = _run(["relationships"])
    assert rc == 0, rc
    assert f"tick {tick}" in out
    for person, gap in expected.items():
        assert f"{person}: gap {gap} ticks" in out, out
    # Rows in stable sorted order.
    assert out.index("jay:") < out.index("kiki:"), out


def test_cli_boundary_500_not_stale_501_stale():
    cli._subject()
    _seed_contact("kiki", "hello", "a thought")
    # The synthetic store sits at tick 1, so a 500-tick gap cannot be
    # seeded by moving last_contact_tick back: tick - 500 would be
    # negative, which the report correctly rejects as malformed (a
    # contact stamp before tick 0 cannot exist). Advance the engine
    # tick instead, then place last contact 500/501 ticks back.
    _rewrite_payload(lambda p: p["engine"].__setitem__("tick", 600))

    def set_last(last):
        _rewrite_payload(
            lambda p: p["engine"]["relationships"]["kiki"].__setitem__(
                "last_contact_tick", last))

    set_last(100)
    rc, out = _run(["relationships"])
    assert rc == 0, rc
    assert "gap 500 ticks" in out and "STALE" not in out, out
    set_last(99)
    rc, out = _run(["relationships"])
    assert rc == 0, rc
    assert "gap 501 ticks" in out and "STALE" in out, out


def test_cli_empty_store_reports_no_relationships():
    cli._subject()
    assert cli._subject().inspect()["engine"]["relationships"] == {}
    rc, out = _run(["relationships"])
    assert rc == 0, rc
    assert "no relationships" in out, out


def test_cli_malformed_fails_loud_before_any_output():
    cli._subject()
    _seed_contact("kiki", "hello", "a thought")
    _rewrite_payload(
        lambda p: p["engine"]["relationships"]["kiki"].__setitem__(
            "familiarity", "high"))
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(ValueError):
            cli.main(["relationships"])
    assert buf.getvalue() == "", "malformed store must print no partial report"


def test_cli_is_read_only_and_deterministic():
    cli._subject()
    _seed_contact("kiki", "hello", "a thought")
    _seed_contact("jay", "hi", "another thought")

    def snapshot():
        files = {}
        for p in sorted(cli.BASE.rglob("*")):
            if p.is_file() and p.name != "mind.db":
                # mind.db's raw bytes move on every CLI construction
                # (genome: the frozen engine's __init__ rewrites the
                # payload row, bumping the SQLite change counter) — the
                # payload *content* is the no-write scope, asserted
                # separately below via _raw_payload().
                files[str(p.relative_to(cli.BASE))] = hashlib.sha256(
                    p.read_bytes()).hexdigest()
        return _logical_store(), _raw_payload(), files

    before_store, before_raw, before_files = snapshot()
    rc, out1 = _run(["relationships"])
    assert rc == 0, rc
    rc, out2 = _run(["relationships"])
    assert rc == 0, rc
    assert out1 == out2, "identical store invoked twice must print byte-identical output"
    after_store, after_raw, after_files = snapshot()
    assert after_store == before_store, "relationships changed the store payload"
    assert after_raw == before_raw, "relationships changed the raw payload string"
    assert after_files == before_files, (
        "relationships created or changed a file: "
        f"{sorted(set(after_files) ^ set(before_files))}")


def test_cli_help_lists_relationships():
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--help"])
    assert exc.value.code == 0
    help_text = buf.getvalue()
    assert "relationships" in help_text, help_text
    assert "drift" in help_text, help_text  # alongside the other read-only reports

    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit) as exc:
            cli.main(["relationships", "--help"])
    assert exc.value.code == 0
    assert "stale" in buf.getvalue().lower(), buf.getvalue()
