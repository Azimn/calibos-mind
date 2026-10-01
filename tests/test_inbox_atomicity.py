"""Tests for inbox prompt-file atomicity (2026-09-30).

Background: a wake run showed `mind inbox` reporting "inbox empty" while
prompt-0082.json was on disk. Forensics: heartbeat and inbox ran
concurrently, and think()'s queue-time supersede did unlink-then-write —
the glob in pending() caught the window between the unlink and the write.
The same non-atomic layout had two sibling races: a concurrent reader could
catch a half-written file (plain write_text is not atomic -> JSONDecodeError
crash of `mind inbox`), and a concurrent `mind answer` (consume) could race
think()'s unlink() into a FileNotFoundError traceback.

The fix, in calibos_mind/provider.py:
- _atomic_write(): temp file + os.replace for every prompt-file write.
- think(): mint and write the replacement FIRST, then unlink the superseded
  files with missing_ok=True. The window shows two prompts, never zero.
- pending(): skip unreadable files instead of raising (the listing must
  never crash on a file a writer is still touching); sync()'s own
  _read_payload pass still sees unreadable files and defers expiry on them,
  so the no-expiry-on-ambiguous-evidence rule is untouched.
- consume(): a FileNotFoundError between the exists-check and the unlink
  (concurrent supersede deleted it first) surfaces as the same clean
  ValueError as "already answered", not a traceback.

Invariants pinned here:
- pending() never raises on a corrupt prompt file; it lists the readable ones.
- think() writes the replacement before unlinking the superseded prompt(s).
- no *.tmp-* files survive a queue/think cycle.
- consume() raises ValueError (not FileNotFoundError) when the file vanishes
  under it, and ids stay monotonic across replacements.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_inbox_atomicity.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.provider as prov
from calibos_mind.provider import InboxCognition, _atomic_write


@dataclass
class _Exp:
    source: str
    first_person: str


@dataclass
class _View:
    experiences: list = field(default_factory=list)


def _tmp_inbox():
    inbox = Path(tempfile.mkdtemp(prefix="inbox-atomic-")) / "inbox"
    return InboxCognition(inbox)


def _view():
    return _View(experiences=[_Exp("memory", "a standing memory")])


def test_pending_skips_unreadable_file():
    provider = _tmp_inbox()
    provider.think(_view())
    corrupt = provider.inbox / "prompt-9999.json"
    corrupt.write_text("{ not valid json", encoding="utf-8")
    # Must not raise; the corrupt file is simply not listed.
    listed = provider.pending()
    assert [p["id"] for p in listed] == ["prompt-0001"], listed
    assert provider.inbox / "prompt-9999.json"  # file itself untouched


def test_think_writes_replacement_before_unlinking_superseded():
    provider = _tmp_inbox()
    events: list[tuple[str, str]] = []
    orig_atomic = prov._atomic_write
    orig_unlink = Path.unlink

    def rec_write(path, text):
        events.append(("write", path.name))
        return orig_atomic(path, text)

    def rec_unlink(self, *args, **kwargs):
        events.append(("unlink", self.name))
        return orig_unlink(self, *args, **kwargs)

    with mock.patch.object(prov, "_atomic_write", rec_write), \
         mock.patch.object(Path, "unlink", rec_unlink):
        provider.think(_view())          # prompt-0001, nothing to supersede
        provider.think(_view())          # prompt-0002 supersedes prompt-0001
    kinds = [(k, n) for k, n in events if n.startswith("prompt-")]
    writes = [n for k, n in kinds if k == "write"]
    unlinks = [n for k, n in kinds if k == "unlink"]
    assert writes == ["prompt-0001.json", "prompt-0002.json"], kinds
    assert unlinks == ["prompt-0001.json"], kinds
    # The replacement's write precedes the superseded file's unlink.
    assert kinds.index(("write", "prompt-0002.json")) < \
        kinds.index(("unlink", "prompt-0001.json")), kinds
    # Provenance intact, old file gone, no temp files left behind.
    payload = json.loads((provider.inbox / "prompt-0002.json").read_text())
    assert payload["supersedes"] == ["prompt-0001"], payload
    assert not (provider.inbox / "prompt-0001.json").exists()
    leftovers = list(provider.inbox.glob("*.tmp-*"))
    assert leftovers == [], leftovers


def test_consume_vanishing_file_raises_value_error():
    provider = _tmp_inbox()
    provider.think(_view())
    with mock.patch.object(Path, "unlink", side_effect=FileNotFoundError):
        try:
            provider.consume("prompt-0001")
        except ValueError as e:
            assert "no such pending prompt" in str(e), e
        else:
            raise AssertionError("expected ValueError, got no exception")


def test_consume_missing_prompt_still_value_error():
    provider = _tmp_inbox()
    try:
        provider.consume("prompt-0001")
    except ValueError as e:
        assert "no such pending prompt" in str(e), e
    else:
        raise AssertionError("expected ValueError, got no exception")


def test_atomic_write_content_intact_no_tmp_leftovers():
    inbox = Path(tempfile.mkdtemp(prefix="inbox-atomic-")) / "inbox"
    inbox.mkdir(parents=True)
    target = inbox / "prompt-0001.json"
    _atomic_write(target, '{"id": "prompt-0001"}')
    assert target.read_text(encoding="utf-8") == '{"id": "prompt-0001"}'
    assert list(inbox.glob("*.tmp-*")) == []


def test_ids_monotonic_after_supersede():
    provider = _tmp_inbox()
    for _ in range(3):
        provider.think(_view())
    listed = provider.pending()
    assert [p["id"] for p in listed] == ["prompt-0003"], listed
    assert (provider.inbox / ".seq").read_text().strip() == "3"


if __name__ == "__main__":
    test_pending_skips_unreadable_file()
    test_think_writes_replacement_before_unlinking_superseded()
    test_consume_vanishing_file_raises_value_error()
    test_consume_missing_prompt_still_value_error()
    test_atomic_write_content_intact_no_tmp_leftovers()
    test_ids_monotonic_after_supersede()
    print("test_inbox_atomicity: all 6 passed")
