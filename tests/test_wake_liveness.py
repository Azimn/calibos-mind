"""Tests for the wake liveness stamp (wake_liveness.json).

A wake that died mid-loop and a wake that never ran leave the same
evidence behind. `mind wake` stamps OPENED at briefing time (carrying
the intended reconciliation set); `mind wake --affirm` and
`mind wake --close` stamp RECONCILED. The next briefing reports an
unclosed wake so the incoming session treats its open loops as
possibly half-done, not untouched.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_wake_liveness.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wake-liv-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json",
                   "FAMILIARITY": self.tmp / "familiarity.json",
                   "AMBIVALENCE": self.tmp / "ambivalence.json",
                   "HABITS": self.tmp / "habits-formed.json",
                   "PROVENANCE": self.tmp / "provenance.json",
                   "WAKE_LIVENESS": self.tmp / "wake_liveness.json",
                   "ABLATION_SALT_FILE": self.tmp / "ablation_salt",
                   "ABLATION_LOG": self.tmp / "ablation_log.jsonl"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False


def _read_stamp():
    return json.loads(cli.WAKE_LIVENESS.read_text(encoding="utf-8"))


def _run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


def test_briefing_stamps_opened():
    with CliOnTmp():
        cli._subject()  # create the store
        rc, out = _run("wake")
        assert rc == 0, rc
        stamp = _read_stamp()
        assert stamp["status"] == "opened", stamp
        assert isinstance(stamp["tick"], int), stamp
        assert "opened_at" in stamp, stamp
        assert isinstance(stamp["open_loops"], list), stamp


def test_briefing_reports_unclosed_previous_wake():
    with CliOnTmp():
        cli._subject()
        rc, _ = _run("wake")
        assert rc == 0, rc
        # Second briefing without a close: the stamp still says opened.
        rc, out = _run("wake")
        assert rc == 0, rc
        assert "never reconciled" in out, out
        assert "possibly half-done" in out, out


def test_affirm_reconciles_with_wake_id():
    with CliOnTmp():
        cli._subject()
        rc, _ = _run("wake")
        assert rc == 0, rc
        rc, _ = _run("wake", "--affirm", "picking up the check-in")
        assert rc == 0, rc
        stamp = _read_stamp()
        assert stamp["status"] == "reconciled", stamp
        assert stamp["wake_id"] is not None, stamp
        assert stamp.get("silent") is False, stamp
        # A following briefing no longer warns about an interrupted loop.
        rc, out = _run("wake")
        assert rc == 0, rc
        assert "never reconciled" not in out, out


def test_close_silently_reconciles():
    with CliOnTmp():
        cli._subject()
        rc, _ = _run("wake")
        assert rc == 0, rc
        rc, out = _run("wake", "--close")
        assert rc == 0, rc
        stamp = _read_stamp()
        assert stamp["status"] == "reconciled", stamp
        assert stamp.get("silent") is True, stamp
        assert stamp["wake_id"] is None, stamp
        rc, out = _run("wake")
        assert rc == 0, rc
        assert "never reconciled" not in out, out


def test_opened_stamp_carries_intended_reconciliation_set():
    with CliOnTmp():
        subject = cli._subject()
        # An open commitment becomes part of the intended reconciliation set.
        tick = subject.inspect()["engine"]["tick"]
        with subject._transaction():
            subject.continuity.create_commitment("calibos", "write the report",
                                                tick=tick)
        # The ablation arm now decides whether the briefing shows the list;
        # this test targets the FULL arm, so pin it.
        real_arm = cli._ablation_arm
        cli._ablation_arm = lambda tick: "FULL"  # noqa: E731
        try:
            rc, _ = _run("wake")
        finally:
            cli._ablation_arm = real_arm
        assert rc == 0, rc
        stamp = _read_stamp()
        assert any("write the report" in loop for loop in stamp["open_loops"]), stamp


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} passed")
