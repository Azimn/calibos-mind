"""Critic round-2 attack tests for the relationship contact-gap instrument.

Spec: research/specs/2026-11-19-relationship-contact-gap.md (Domain 26).
Round-1 fix under test: the future-tick check became a range check
(0..tick) for last_contact_tick. These tests probe whether the negative
side of that range is enforced everywhere the round-1 fix claims it is.

All fixtures live in /tmp — the live store is never touched (the autouse
conftest fixture redirects every cli store path at a synthetic tree).

Run: cd ~/workspace/calibos-mind && ./.venv/bin/python -m pytest tests/adversarial/test_relationships_attack_round2.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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


# ---------------------------------------------------------------- attack R2-1:
# Round 1 fixed negative last_contact_tick, but the fix is enforced only
# inside the per-row range check (last < 0 or last > tick). A negative
# ENGINE tick slips through whenever no row triggers the range check:
# with an empty relationships dict, or with every relationship missing
# its last_contact_tick stamp, relationships_report returns a report
# headed by an impossible tick instead of failing loud. The module's own
# docstring says "ticks start at 0", and the spec's "malformed values
# fail loud" clause covers a negative tick regardless of row count.

def test_negative_engine_tick_empty_relationships_fails_loud():
    with pytest.raises(ValueError):
        relationships_report(_state(-1, {}))


def test_negative_engine_tick_missing_stamp_rows_fails_loud():
    with pytest.raises(ValueError):
        relationships_report(_state(-5, {"kiki": _rel(None)}))


# ---------------------------------------------------------------- attack R2-2:
# Same hole at the CLI level: payload surgery to a negative engine tick
# must raise before any partial report, not print a report headed by an
# impossible tick. (If the engine clamps/rejects the tick on load, this
# test will show that instead — run it and see.)

def test_cli_negative_engine_tick_fails_loud():
    import io
    import json
    import sqlite3
    from contextlib import redirect_stdout

    import calibos_mind.cli as cli

    cli._subject()
    db = sqlite3.connect(cli.DB)
    try:
        raw = db.execute("SELECT payload FROM subject WHERE id=1").fetchone()[0]
        payload = json.loads(raw)
        payload["engine"]["tick"] = -1
        db.execute("UPDATE subject SET payload=? WHERE id=1",
                   (json.dumps(payload),))
        db.commit()
    finally:
        db.close()
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(ValueError):
            cli.main(["relationships"])
    assert buf.getvalue() == "", "must print no partial report"
