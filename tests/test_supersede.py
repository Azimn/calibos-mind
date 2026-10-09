"""Tests for queue-time prompt supersede (2026-09-28).

Background: every heartbeat tick whose engine action is seek_contact calls
InboxCognition.think(), which queued one prompt per tick — but a wake
answers at most the newest before the store moves past the older views, so
every prompt but the last was refused-stale-view by answer time. Three
wakes of evidence; the stillborn invitations were noise the refusal path
had to dispose of.

The fix: the inbox holds at most one standing engine prompt. think()
supersedes the unanswered engine prompt(s) when queueing the new one and
records the replaced ids in the newcomer's "supersedes" list; sync()
confirms those expectations "superseded" (considered, closed by a newer
view — neutral on the expiry streak) instead of marking them "expired".

Invariants pinned here:
- external prompts (mind queue) are a distinct invitation channel: never
  superseded, never counted as the standing prompt.
- hand-written/legacy files (no engine marker) are never superseded:
  supersede only eats invitations think() itself minted.
- an unreadable prompt file blocks supersede (fail closed): nothing is
  deleted and the tick queues alongside it.
- prompt ids stay monotonic across replacements.
- a superseded prompt's registered expectation is confirmed/"superseded",
  not expired, and the expiry streak is untouched.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_supersede.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.inbox_expectations import (
    POLICY_FILE_NAME,
    expectation_id,
    sync,
)


@dataclass
class _Exp:
    source: str
    first_person: str


@dataclass
class _View:
    experiences: list = field(default_factory=list)


def _view():
    return _View(experiences=[_Exp("memory", "a standing memory")])


def _payloads():
    out = {}
    for p in sorted(Path(cli.INBOX).glob("prompt-*.json")):
        out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return out


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="supersede-cli-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json",
                   "HABITS": self.tmp / "habits-formed.json",
                   "AMBIVALENCE": self.tmp / "ambivalence.json",
                   "FAMILIARITY": self.tmp / "familiarity.json",
                   "PROVENANCE": self.tmp / "provenance.json",
                   "ARCHIVE": self.tmp / "archive",
                   "PROPOSALS": self.tmp / "proposals"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        with redirect_stdout(io.StringIO()):
            cli.main(["init"])
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False


def test_second_think_supersedes_first():
    with CliOnTmp():
        subject = cli._subject()
        provider = subject.cognition
        provider.think(_view())
        first = _payloads()
        assert list(first) == ["prompt-0001"], first
        provider.think(_view())
        pending = _payloads()
        assert list(pending) == ["prompt-0002"], pending
        assert pending["prompt-0002"]["supersedes"] == ["prompt-0001"], pending


def test_three_thinks_leave_one_prompt():
    with CliOnTmp():
        provider = cli._subject().cognition
        for _ in range(3):
            provider.think(_view())
        pending = _payloads()
        assert list(pending) == ["prompt-0003"], pending
        assert pending["prompt-0003"]["supersedes"] == ["prompt-0002"], pending


def test_external_prompt_never_superseded():
    with CliOnTmp() as ctx:
        provider = cli._subject().cognition
        with redirect_stdout(io.StringIO()):
            assert cli.main(["queue", "a relay message"]) == 0
        ext_before = _payloads()["prompt-0001"]
        assert ext_before["external"] is True
        provider.think(_view())
        pending = _payloads()
        # Both the external invitation and the new engine prompt stand.
        assert sorted(pending) == ["prompt-0001", "prompt-0002"], pending
        # The external file is byte-identical: never touched.
        assert _payloads()["prompt-0001"] == ext_before
        # And the external prompt does not count as a standing engine
        # prompt: the engine prompt superseded nothing.
        assert "supersedes" not in pending["prompt-0002"], pending
        provider.think(_view())
        pending = _payloads()
        assert sorted(pending) == ["prompt-0001", "prompt-0003"], pending
        assert pending["prompt-0003"]["supersedes"] == ["prompt-0002"], pending
        assert _payloads()["prompt-0001"] == ext_before


def test_handwritten_prompt_never_superseded():
    """Supersede is scoped to prompts think() itself minted: a
    hand-written/legacy file (no engine marker) survives a heartbeat's
    think() untouched, and the new prompt records no supersession."""
    with CliOnTmp():
        provider = cli._subject().cognition
        legacy = {"id": "prompt-0001", "prompt": "hand-written",
                  "view_tick": 0, "view_sequence": 0,
                  "experiences": [{"source": "x", "first_person": "y"}]}
        (Path(cli.INBOX) / "prompt-0001.json").write_text(
            json.dumps(legacy), encoding="utf-8")
        provider.think(_view())
        pending = _payloads()
        assert sorted(pending) == ["prompt-0001", "prompt-0002"], pending
        assert pending["prompt-0001"] == legacy
        assert "supersedes" not in pending["prompt-0002"], pending


def test_unreadable_file_blocks_supersede():
    with CliOnTmp():
        provider = cli._subject().cognition
        provider.think(_view())
        # An id outside the .seq counter's range, so the file survives the
        # next queue (no id collision with _next_id's normal behavior).
        corrupt = Path(cli.INBOX) / "prompt-0099.json"
        corrupt.write_text("{not valid json", encoding="utf-8")
        provider.think(_view())
        stems = sorted(p.stem for p in Path(cli.INBOX).glob("prompt-*.json"))
        # Nothing deleted (prompt-0001 survives), the tick queued alongside.
        assert stems == ["prompt-0001", "prompt-0002", "prompt-0099"], stems
        p2 = json.loads((Path(cli.INBOX) / "prompt-0002.json").read_text(
            encoding="utf-8"))
        assert "supersedes" not in p2, p2
        assert corrupt.read_text(encoding="utf-8") == "{not valid json"


def test_sync_confirms_superseded_expectation():
    with CliOnTmp():
        subject = cli._subject()
        provider = subject.cognition
        provider.think(_view())
        # Force registration: age the prompt past the TTL.
        out = sync(subject, cli.INBOX, ttl_ticks=0)
        assert out["registered"] == ["inbox:prompt-0001"], out
        # A new engine invitation supersedes the old one.
        provider.think(_view())
        out = sync(subject, cli.INBOX, ttl_ticks=0)
        assert out["expired"] == [], out
        assert out["superseded"] == ["inbox:prompt-0001"], out
        item = subject.continuity.state.expectations[
            expectation_id("prompt-0001")]
        assert item.status == "confirmed", item
        assert item.outcome == "superseded", item
        # Neutral on the expiry streak: the sidecar is never written.
        assert not (Path(cli.INBOX).parent / POLICY_FILE_NAME).exists()
        # The newcomer is still pending business, not yet registered here.
        assert subject.continuity.state.expectations[
            expectation_id("prompt-0002")].status == "pending"


def test_genuine_expiry_still_expires():
    """A prompt file deleted without any supersession still expires."""
    with CliOnTmp():
        subject = cli._subject()
        subject.cognition.think(_view())
        out = sync(subject, cli.INBOX, ttl_ticks=0)
        assert out["registered"] == ["inbox:prompt-0001"], out
        (Path(cli.INBOX) / "prompt-0001.json").unlink()
        out = sync(subject, cli.INBOX, ttl_ticks=0)
        assert out["superseded"] == [], out
        assert out["expired"] == ["inbox:prompt-0001"], out
        assert subject.continuity.state.expectations[
            expectation_id("prompt-0001")].status == "expired"


if __name__ == "__main__":
    test_second_think_supersedes_first()
    test_three_thinks_leave_one_prompt()
    test_external_prompt_never_superseded()
    test_handwritten_prompt_never_superseded()
    test_unreadable_file_blocks_supersede()
    test_sync_confirms_superseded_expectation()
    test_genuine_expiry_still_expires()
    print("test_supersede: all 7 tests passed")
