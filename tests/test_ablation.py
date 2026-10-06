"""Tests for the carrying-list ablation (prereg 2026-10-04, live 2026-10-05).

Pre-registered design: a random subset of wakes runs with the carrying
list withheld (empty handoff); arm assignment is sha256(salt : tick) mod 2,
fixed before the first arm ran. The wake may still rediscover loops from
salience, inbox, or dreams — the rediscovery rate is the measurement.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_ablation.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import hashlib
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
        self.tmp = Path(tempfile.mkdtemp(prefix="ablation-"))
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


def _run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


def _log_rows():
    try:
        text = cli.ABLATION_LOG.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _pin_arm(arm):
    """Force the arm for a deterministic test. Returns a restorer."""
    real = cli._ablation_arm
    cli._ablation_arm = lambda tick: arm  # noqa: E731
    return lambda: setattr(cli, "_ablation_arm", real)


def test_salt_generated_once_and_stable():
    with CliOnTmp():
        s1 = cli._ablation_salt()
        s2 = cli._ablation_salt()
        assert s1 == s2 and len(s1) >= 16, (s1, s2)
        assert cli.ABLATION_SALT_FILE.exists()


def test_arm_matches_preregistered_rule():
    with CliOnTmp():
        salt = cli._ablation_salt()
        for tick in (0, 1, 391, 392, 10**6):
            digest = hashlib.sha256(f"{salt}:{tick}".encode()).hexdigest()
            expected = "WITHHELD" if int(digest, 16) % 2 == 0 else "FULL"
            assert cli._ablation_arm(tick) == expected, tick


def test_arms_roughly_balanced_and_both_present():
    with CliOnTmp():
        cli._ablation_salt()
        arms = [cli._ablation_arm(t) for t in range(2000)]
        n_w = arms.count("WITHHELD")
        assert 0 < n_w < 2000, n_w
        assert 800 <= n_w <= 1200, n_w  # ~50/50 for 2000 draws


def test_withheld_briefing_hides_loops_and_recap():
    with CliOnTmp():
        subject = cli._subject()
        tick = subject.inspect()["engine"]["tick"]
        with subject._transaction():
            subject.continuity.create_commitment("calibos", "write the report",
                                                tick=tick)
        restore = _pin_arm("WITHHELD")
        try:
            rc, out = _run("wake")
        finally:
            restore()
        assert rc == 0, rc
        assert "withheld by design" in out, out
        assert "write the report" not in out, out  # no loop leaks
        assert "last wake" in out and "withheld" in out, out  # recap withheld too
        stamp = json.loads(cli.WAKE_LIVENESS.read_text(encoding="utf-8"))
        assert stamp["ablation_arm"] == "WITHHELD", stamp
        assert stamp["open_loops"] == [], stamp  # intended set honestly empty
        rows = _log_rows()
        assert rows and rows[0]["event"] == "briefed", rows
        assert rows[0]["arm"] == "WITHHELD" and rows[0]["loops_shown"] == 0, rows


def test_full_briefing_shows_loops():
    with CliOnTmp():
        subject = cli._subject()
        tick = subject.inspect()["engine"]["tick"]
        with subject._transaction():
            subject.continuity.create_commitment("calibos", "write the report",
                                                tick=tick)
        restore = _pin_arm("FULL")
        try:
            rc, out = _run("wake")
        finally:
            restore()
        assert rc == 0, rc
        assert "write the report" in out, out
        stamp = json.loads(cli.WAKE_LIVENESS.read_text(encoding="utf-8"))
        assert stamp["ablation_arm"] == "FULL", stamp
        assert any("write the report" in loop for loop in stamp["open_loops"]), stamp
        rows = _log_rows()
        assert rows[0]["arm"] == "FULL" and rows[0]["loops_shown"] == 1, rows


def test_affirm_logs_reconciliation_row():
    with CliOnTmp():
        cli._subject()
        restore = _pin_arm("FULL")
        try:
            rc, _ = _run("wake")
            assert rc == 0, rc
            rc, _ = _run("wake", "--affirm", "picking up", "--carrying", "the report")
            assert rc == 0, rc
        finally:
            restore()
        rows = _log_rows()
        rec = [r for r in rows if r.get("event") == "reconciled"]
        assert len(rec) == 1, rows
        assert rec[0]["arm"] == "FULL" and rec[0]["wake_id"] is not None, rec


def test_ablation_command_reports_readiness():
    with CliOnTmp():
        cli._subject()
        rc, out = _run("ablation")
        assert rc == 0, rc
        assert "0 wakes briefed" in out and str(cli.ABLATION_MIN_WAKES) in out, out


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} passed")
