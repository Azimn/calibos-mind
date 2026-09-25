"""Tests for clean refusal on oversize/duplicate thoughts (cli cmd_think/cmd_answer).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_think_validation.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind import cli


def _fresh_cli_paths(tmp):
    tmp = Path(tmp)
    cli.DB = tmp / "mind.db"
    cli.INBOX = tmp / "inbox"
    cli.SALIENCE = tmp / "salience.json"
    cli.INBOX.mkdir(parents=True, exist_ok=True)


def test_length_error_reports_count():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        subject = cli._subject()
        try:
            subject.inject_thought("x" * 601, trigger_kind="voluntary",
                                   generated_by="voluntary")
        except ValueError as exc:
            assert "601" in str(exc), f"message should name the count: {exc}"
        else:
            raise AssertionError("601-char thought was not refused")


def test_cmd_think_refuses_cleanly():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        before = len(cli._subject().inspect()["workspace"]["records"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_think(SimpleNamespace(text="x" * 700))
        out = buf.getvalue()
        assert rc == 1, f"expected rc 1, got {rc}"
        assert "Traceback" not in out, "refusal must not print a traceback"
        assert "700" in out, f"refusal should name the count: {out!r}"
        assert "nothing recorded" in out
        after = len(cli._subject().inspect()["workspace"]["records"])
        assert after == before, "refused thought must not be recorded"


def test_cmd_think_accepts_short_thought():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_think(SimpleNamespace(text="a short voluntary thought"))
        assert rc == 0, f"expected rc 0, got {rc}: {buf.getvalue()!r}"
        recs = cli._subject().inspect()["workspace"]["records"]
        assert any(r["first_person"] == "a short voluntary thought"
                   for r in recs), "valid thought must be recorded"


def test_oversize_answer_keeps_prompt():
    """A malformed answer must not consume the prompt it was meant to answer.

    Regression: cmd_answer used to consume() before validating, so one
    over-long draft ate the queued prompt and the thought was lost with it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        cli._subject()
        assert cli.main(["queue", "an invitation"]) == 0
        rc = cli.main(["answer", "prompt-0001", "x" * 601])
        assert rc == 1, rc
        assert (cli.INBOX / "prompt-0001.json").exists(), \
            "refused answer must leave the prompt queued"
        rc = cli.main(["answer", "prompt-0001", "a fitting thought"])
        assert rc == 0, rc
        assert not list(cli.INBOX.glob("prompt-*.json"))


if __name__ == "__main__":
    test_length_error_reports_count()
    test_cmd_think_refuses_cleanly()
    test_cmd_think_accepts_short_thought()
    test_oversize_answer_keeps_prompt()
    print("ok: test_think_validation (4 tests)")
