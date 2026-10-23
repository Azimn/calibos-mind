"""Regression: `mind inbox <short-id>` must resolve the same way `mind answer` does.

2026-10-22: `cmd_answer` normalizes '0028' -> 'prompt-0028' via
_normalize_prompt_id, but `cmd_inbox` matched the raw id, so
`mind inbox 0445` answered "no queued prompt" for a prompt the listing
had just shown. Reading a prompt's question before answering it is the
normal flow; the read path must not fail on the id the list implies.

The autouse conftest fixture redirects every store path at a tmp tree,
so the live store is never touched.
"""
from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli


def _queue_one(prompt_text: str) -> str:
    provider = None
    from calibos_mind.provider import InboxCognition
    provider = InboxCognition(cli.INBOX)
    cli._subject(provider=provider)
    return provider.queue_external(prompt_text, source="test",
                                   first_person="a test experience")


def _inbox_output(prompt_id):
    args = argparse.Namespace(id=prompt_id)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.cmd_inbox(args)
    return rc, buf.getvalue()


def test_inbox_short_id_resolves(capsys=None):
    pid = _queue_one("the short-id regression question")
    short = pid.split("-", 1)[1]  # 'prompt-0446' -> '0446'
    rc, out = _inbox_output(short)
    assert rc == 0, out
    assert "the short-id regression question" in out
    assert pid in out


def test_inbox_full_id_still_resolves(capsys=None):
    pid = _queue_one("the full-id control question")
    rc, out = _inbox_output(pid)
    assert rc == 0, out
    assert "the full-id control question" in out
