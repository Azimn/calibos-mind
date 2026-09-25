"""Tests for first-class "released" commitment semantics (subject.release_commitment).

Fitness: `mind resolve --released` stores the categorical state "released" —
never "broken" — with honest insight prose ("deliberately released", not
"did not follow through"). The engine's genuine-breakage path
(promise_broken events / resolve_commitment(kept=False)) still stores
"broken". "released" counts as closed in the Zeigarnik open set. Closes
pre-freeze gate 1 (categorical commitment-state semantics, Stage A).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_release.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _subject(tmp: Path):
    from calibos_mind.provider import InboxCognition
    from calibos_mind.subject import CalibosSubject
    from digital_subject.cartridge import load_cartridge
    cart = load_cartridge(Path(__file__).resolve().parents[1] / "calibos.toml")
    return CalibosSubject(
        tmp / "test.db", cart,
        cognition=InboxCognition(tmp / "inbox"),
        salience_path=tmp / "salience.json")


def test_release_stores_released_not_broken():
    sub = _subject(Path(tempfile.mkdtemp(prefix="release-")))
    with sub._transaction():
        c = sub.continuity.create_commitment(
            actor="self", description="a loop I choose to drop",
            tick=sub.engine.state.tick)
        sub.release_commitment(
            c.id, outcome="deliberately released",
            tick=sub.engine.state.tick)
    assert c.status == "released", c.status
    assert c.status != "broken"
    assert c.outcome == "deliberately released"
    assert c.resolved_tick == sub.engine.state.tick


def test_release_insight_prose_is_honest():
    sub = _subject(Path(tempfile.mkdtemp(prefix="release-")))
    with sub._transaction():
        c = sub.continuity.create_commitment(
            actor="self", description="a loop I choose to drop",
            tick=sub.engine.state.tick)
        sub.release_commitment(
            c.id, outcome="deliberately released",
            tick=sub.engine.state.tick)
    insights = [i for i in sub.continuity.state.insights
                if i.kind == "commitment_result"]
    assert insights, "release must record a commitment_result insight"
    last = insights[-1]
    assert "deliberately released" in last.proposition, last.proposition
    assert "did not follow through" not in last.proposition, last.proposition
    # Same weight formula the engine's resolve_commitment uses.
    assert last.confidence == 0.45 + c.importance * 0.45


def test_breakage_path_still_stores_broken():
    """The engine's genuine-breakage path is untouched: kept=False stays 'broken'."""
    sub = _subject(Path(tempfile.mkdtemp(prefix="release-")))
    with sub._transaction():
        c = sub.continuity.create_commitment(
            actor="self", description="a promise genuinely broken",
            tick=sub.engine.state.tick)
        sub.continuity.resolve_commitment(
            c.id, outcome="it fell through", kept=False,
            tick=sub.engine.state.tick)
    assert c.status == "broken", c.status
    insights = [i for i in sub.continuity.state.insights
                if i.kind == "commitment_result"]
    assert "did not follow through" in insights[-1].proposition


def test_kept_path_unchanged():
    sub = _subject(Path(tempfile.mkdtemp(prefix="release-")))
    with sub._transaction():
        c = sub.continuity.create_commitment(
            actor="self", description="a loop I completed",
            tick=sub.engine.state.tick)
        sub.continuity.resolve_commitment(
            c.id, outcome="done", kept=True, tick=sub.engine.state.tick)
    assert c.status == "kept", c.status
    insights = [i for i in sub.continuity.state.insights
                if i.kind == "commitment_result"]
    assert "followed through" in insights[-1].proposition


def test_release_on_closed_commitment_raises():
    sub = _subject(Path(tempfile.mkdtemp(prefix="release-")))
    with sub._transaction():
        c = sub.continuity.create_commitment(
            actor="self", description="a finished loop",
            tick=sub.engine.state.tick)
        sub.continuity.resolve_commitment(
            c.id, outcome="done", kept=True, tick=sub.engine.state.tick)
        try:
            sub.release_commitment(
                c.id, outcome="nope", tick=sub.engine.state.tick)
        except ValueError:
            return
    raise AssertionError("releasing an already-closed commitment must raise")


def test_released_counts_as_closed_in_open_set():
    """The Zeigarnik helper drops released commitments like kept/broken ones."""
    from calibos_mind.unresolved import live_open_keys
    sub = _subject(Path(tempfile.mkdtemp(prefix="release-")))
    with sub._transaction():
        c = sub.continuity.create_commitment(
            actor="self", description="a loop I choose to drop",
            tick=sub.engine.state.tick)
        keys = live_open_keys(sub.engine.state, sub.continuity.state)
        assert c.id in keys and "commitment:" + c.id in keys
        sub.release_commitment(
            c.id, outcome="deliberately released",
            tick=sub.engine.state.tick)
        keys = live_open_keys(sub.engine.state, sub.continuity.state)
    assert c.id not in keys
    assert "commitment:" + c.id not in keys


def test_cli_resolve_released_end_to_end():
    """cmd_resolve --released stores 'released' (not the engine's 'broken')."""
    import calibos_mind.cli as cli
    from types import SimpleNamespace
    sub = _subject(Path(tempfile.mkdtemp(prefix="release-")))
    with sub._transaction():
        c = sub.continuity.create_commitment(
            actor="self", description="a loop I choose to drop",
            tick=sub.engine.state.tick)
        cid = c.id
    old_subject = cli._subject
    cli._subject = lambda provider=None: sub  # noqa: E731
    try:
        args = SimpleNamespace(id=cid[:8], released=True, note=None)
        assert cli.cmd_resolve(args) == 0
    finally:
        cli._subject = old_subject
    assert sub.continuity.state.commitments[cid].status == "released"
    assert sub.continuity.state.commitments[cid].status != "broken"


if __name__ == "__main__":
    test_release_stores_released_not_broken()
    test_release_insight_prose_is_honest()
    test_breakage_path_still_stores_broken()
    test_kept_path_unchanged()
    test_release_on_closed_commitment_raises()
    test_released_counts_as_closed_in_open_set()
    test_cli_resolve_released_end_to_end()
    print("test_release: 7 passed")
