"""Critic attack tests for the relationship contact-gap instrument (round 1).

Spec: research/specs/2026-11-19-relationship-contact-gap.md (Domain 26).
All fixtures live in /tmp — the live store is never touched (the autouse
conftest fixture redirects every cli store path at a synthetic tree).

Run: cd ~/workspace/calibos-mind && ./.venv/bin/python -m pytest tests/adversarial/test_relationships_attack.py -q
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind.relationships import relationships_report


def _state(tick, rels):
    return {"engine": {"tick": tick, "relationships": rels},
            "workspace": {"records": []}, "trace": []}


def _rel(last, fam=0.055):
    rel = {"trust": 0.32, "comfort": 0.28, "respect": 0.35,
           "familiarity": fam, "uncertainty": 1.0}
    if last is not None:
        rel["last_contact_tick"] = last
    return rel


# ---------------------------------------------------------------- attack 1:
# impossible ticks are silently accepted. The builder rejects a future
# last_contact_tick ("contact can only ever stamp the tick it happens on")
# but accepts a NEGATIVE last_contact_tick and a NEGATIVE engine tick,
# both equally impossible. A negative last_contact_tick reports a gap
# larger than the organism's entire lifetime (gap > current tick) with
# no complaint — a concretely wrong report from a corrupted store, and a
# gap the spec's "malformed relationship values fail loud" clause covers.

def test_negative_last_contact_tick_fails_loud():
    with pytest.raises(ValueError):
        relationships_report(_state(100, {"kiki": _rel(-5)}))


def test_negative_engine_tick_fails_loud():
    with pytest.raises(ValueError):
        relationships_report(_state(-1, {"kiki": _rel(0)}))


def test_negative_last_contact_tick_via_cli_fails_loud():
    """Same root cause at the CLI level: payload surgery to a negative
    stamp must raise before any partial report, not print gap > tick."""
    cli._subject()
    _seed_contact("kiki", "hello", "a thought")
    tick = cli._subject().inspect()["engine"]["tick"]
    _rewrite_payload(
        lambda p: p["engine"]["relationships"]["kiki"].__setitem__(
            "last_contact_tick", -3))
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(ValueError):
            cli.main(["relationships"])
    assert buf.getvalue() == "", "must print no partial report"


# ---------------------------------------------------------------- attack 2 (probe):
# missing last_contact_tick through the CLI. The builder's test comment
# claims the engine refills a missing key with 0 on load (Relationship
# dataclass default), so the CLI can never observe the absence and the
# pure-function undefined handling is unreachable from the command. Verify
# the claim empirically: delete the key from the synthetic payload and
# record what the command actually prints.

def test_cli_missing_last_contact_key_engine_refill():
    cli._subject()
    _seed_contact("kiki", "hello", "a thought")
    tick = cli._subject().inspect()["engine"]["tick"]

    def delete_last(p):
        del p["engine"]["relationships"]["kiki"]["last_contact_tick"]

    _rewrite_payload(delete_last)
    rc, out = _run(["relationships"])
    assert rc == 0, out
    # If the engine refills the missing key with its 0 default, the report
    # prints gap == tick (contact "at tick 0"), NOT "gap undefined".
    assert f"gap {tick} ticks" in out, out
    assert "gap undefined" not in out, out


# ---------------------------------------------------------------- attack 3:
# explicit null last_contact_tick through the CLI must fail loud (the
# engine's .get default only applies when the key is ABSENT; an explicit
# null is malformed, not missing).

def test_cli_null_last_contact_tick_fails_loud():
    cli._subject()
    _seed_contact("kiki", "hello", "a thought")
    _rewrite_payload(
        lambda p: p["engine"]["relationships"]["kiki"].__setitem__(
            "last_contact_tick", None))
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(ValueError):
            cli.main(["relationships"])
    assert buf.getvalue() == "", "must print no partial report"


# ---------------------------------------------------------------- attack 4:
# future last_contact_tick through the CLI (pure-function level is covered
# by the builder; verify the CLI path too, since inspect() round-trips
# through the engine dataclass).

def test_cli_future_last_contact_tick_fails_loud():
    cli._subject()
    _seed_contact("kiki", "hello", "a thought")
    tick = cli._subject().inspect()["engine"]["tick"]
    _rewrite_payload(
        lambda p: p["engine"]["relationships"]["kiki"].__setitem__(
            "last_contact_tick", tick + 10))
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(ValueError):
            cli.main(["relationships"])
    assert buf.getvalue() == "", "must print no partial report"


# ---------------------------------------------------------------- attack 5:
# independent read-only check (critic's own snapshot, not the builder's):
# two invocations, byte-identical output, payload content + raw payload
# string unchanged, no file under the store tree created or changed
# (mind.db raw bytes excluded per the genome's CLI-construction note).

def test_critic_independent_readonly_check():
    cli._subject()
    _seed_contact("kiki", "hello", "a thought")
    _seed_contact("jay", "hi", "another thought")

    def snapshot():
        files = {}
        for p in sorted(cli.BASE.rglob("*")):
            if p.is_file() and p.name != "mind.db":
                files[str(p.relative_to(cli.BASE))] = hashlib.sha256(
                    p.read_bytes()).hexdigest()
        db = sqlite3.connect(cli.DB)
        try:
            raw = db.execute(
                "SELECT payload FROM subject WHERE id=1").fetchone()[0]
        finally:
            db.close()
        return json.dumps(cli._subject().inspect(), sort_keys=True), raw, files

    before_store, before_raw, before_files = snapshot()
    rc, out1 = _run(["relationships"])
    assert rc == 0, rc
    rc, out2 = _run(["relationships"])
    assert rc == 0, rc
    assert out1 == out2, "determinism: identical store, identical output"
    after_store, after_raw, after_files = snapshot()
    assert after_store == before_store, "relationships changed the store payload"
    assert after_raw == before_raw, "relationships changed the raw payload string"
    assert after_files == before_files, (
        "relationships created or changed a file: "
        f"{sorted(set(after_files) ^ set(before_files))}")


# ---------------------------------------------------------------- helpers (same
# synthetic-store surgery pattern as the builder's tests; conftest fixture
# redirects every cli path at a tmp tree, live store never touched).

def _run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def _rewrite_payload(mutate):
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
    rc, _ = _run(["heartbeat", "--ticks", "1"])
    assert rc == 0, rc
