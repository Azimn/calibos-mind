"""Tests for `mind queue`: externally-authored prompts with queue-time provenance.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_queue.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.

Background: prompt-0005 (the 2026-09-24 "school outing" invitation) was
hand-written as JSON with view_tick/view_sequence null, so `mind answer`
fail-closed refused it and discarded the file. There was no supported
write path for external prompts — only the engine's think(view) path.
`mind queue` closes that gap: external text gets the same queue-time
provenance stamp, answerable in the same wake.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.provider import InboxCognition, StalePromptError, check_prompt_fresh

BASE = Path(__file__).resolve().parents[1]


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="queue-cli-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False

    def thoughts(self):
        subject = cli._subject()
        return [r for r in subject.inspect()["workspace"]["records"]
                if r["source"] == "thought"]


def _queued_payload():
    files = list(cli.INBOX.glob("prompt-*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_queue_stamps_queue_time_provenance():
    with CliOnTmp():
        subject = cli._subject()
        tick, seq = subject.engine.state.tick, subject.workspace.sequence
        rc = cli.main(["queue", "go explore before bed",
                       "--source", "evening conversation",
                       "--experience", "Jay told me to go explore."])
        assert rc == 0, rc
        payload = _queued_payload()
        assert payload["id"] == "prompt-0001", payload
        assert payload["prompt"] == "go explore before bed", payload
        assert payload["view_tick"] == tick, payload
        assert payload["view_sequence"] == seq, payload
        assert payload["experiences"] == [
            {"source": "evening conversation",
             "first_person": "Jay told me to go explore."}], payload


def test_queue_defaults():
    with CliOnTmp():
        cli._subject()
        rc = cli.main(["queue", "plain invitation"])
        assert rc == 0, rc
        payload = _queued_payload()
        assert payload["experiences"] == [
            {"source": "invitation", "first_person": "plain invitation"}], payload


def test_queue_answer_fresh_succeeds():
    with CliOnTmp() as ctx:
        cli._subject()
        rc = cli.main(["queue", "read something tonight"])
        assert rc == 0, rc
        n_before = len(ctx.thoughts())
        rc = cli.main(["answer", "prompt-0001", "a thought about the invitation"])
        assert rc == 0, rc
        after = ctx.thoughts()
        assert len(after) == n_before + 1, after
        assert any(r["first_person"] == "a thought about the invitation"
                   for r in after)
        assert not list(cli.INBOX.glob("prompt-*.json"))


def test_queue_answer_after_intervening_thought_refused():
    with CliOnTmp() as ctx:
        subject = cli._subject()
        rc = cli.main(["queue", "read something tonight"])
        assert rc == 0, rc
        subject.inject_thought("an intervening thought",
                               trigger_kind="voluntary",
                               generated_by="voluntary")
        n_before = len(ctx.thoughts())
        rc = cli.main(["answer", "prompt-0001", "a late answer"])
        assert rc == 1, rc
        assert not list(cli.INBOX.glob("prompt-*.json"))
        assert len(ctx.thoughts()) == n_before
        assert not any(r["first_person"] == "a late answer"
                       for r in ctx.thoughts())


def test_queue_without_clock_is_unanswerable():
    """queue_external with no wired clock carries no provenance: fail closed."""
    with CliOnTmp() as ctx:
        cli._subject()
        provider = InboxCognition(cli.INBOX)  # no track_queue_time
        pid = provider.queue_external("clockless invitation")
        payload = _queued_payload()
        assert payload["id"] == pid
        assert payload["view_sequence"] is None, payload
        n_before = len(ctx.thoughts())
        rc = cli.main(["answer", pid, "answering blind"])
        assert rc == 1, rc
        assert len(ctx.thoughts()) == n_before


def test_queue_ids_monotonic_after_consume():
    with CliOnTmp():
        cli._subject()
        assert cli.main(["queue", "first"]) == 0
        assert cli.main(["answer", "prompt-0001", "--silent"]) == 0
        assert cli.main(["queue", "second"]) == 0
        assert (cli.INBOX / "prompt-0002.json").exists()
