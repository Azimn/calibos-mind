"""Tests for the consolidated cmd_init (single definition, sidecar reset on --force).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_init.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.

Regression for the duplicate-definition bug: a shadowing cmd_init once dropped
the salience-sidecar reset, so `mind init --force` reseeded records while
stale importance from the previous incarnation attached to the new ids.
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.salience import SalienceTracker
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge


def _patched_cli(tmp: Path):
    """Point the CLI at synthetic paths; returns (make_subject, restore).

    Every module-level path cmd_init touches must be redirected —
    DB and SALIENCE alone are not enough: cmd_init --force wipes the
    PROPOSALS and ARCHIVE sidecar directories, and a fixture that
    leaves those pointed at the live tree deletes real working files.
    """
    db = tmp / "mind.db"
    salience = tmp / "salience.json"
    inbox = tmp / "inbox"
    proposals = tmp / "proposals"
    archive = tmp / "archive"
    saved = (cli.DB, cli.SALIENCE, cli.PROPOSALS, cli.ARCHIVE, cli._subject)
    cli.DB, cli.SALIENCE, cli.PROPOSALS, cli.ARCHIVE = (
        db, salience, proposals, archive)
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(salience))

    cli._subject = make_subject
    return make_subject, saved


def _restore(saved):
    cli.DB, cli.SALIENCE, cli.PROPOSALS, cli.ARCHIVE, cli._subject = saved


def _args(force):
    return argparse.Namespace(force=force)


def _record_ids(make_subject):
    return sorted(r["id"] for r in make_subject().inspect()["workspace"]["records"])


def _sidecar_importance(salience: Path) -> dict:
    payload = json.loads(salience.read_text(encoding="utf-8"))
    return {rid: e.get("importance", 0.0)
            for rid, e in payload.get("records", {}).items()}


def test_single_definition():
    """Only one cmd_init exists — the shadowing copy is gone."""
    assert sum(1 for n in dir(cli) if n == "cmd_init") == 1
    assert "reset()" in inspect.getsource(cli.cmd_init), "sidecar reset missing"


def test_force_resets_sidecar_and_restarts_ids():
    tmp = Path(tempfile.mkdtemp(prefix="init-test-"))
    make_subject, saved = _patched_cli(tmp)
    salience = tmp / "salience.json"
    try:
        assert cli.cmd_init(_args(force=True)) == 0
        first_ids = _record_ids(make_subject)
        assert first_ids, "seeds should exist"
        # Contaminate the sidecar with importance against the seed ids.
        tracker = SalienceTracker(salience)
        for rid in first_ids:
            tracker.add_importance(rid, 0, 0.7)
        tracker.save()
        assert any(v > 0 for v in _sidecar_importance(salience).values())

        # Reseed: ids restart at experience-1, sidecar must restart too.
        assert cli.cmd_init(_args(force=True)) == 0
        second_ids = _record_ids(make_subject)
        assert first_ids == second_ids, (first_ids, second_ids)
        assert _sidecar_importance(salience) == {}, \
            "stale importance must not attach to recycled ids"
    finally:
        _restore(saved)


def test_no_force_refuses_existing():
    tmp = Path(tempfile.mkdtemp(prefix="init-test-"))
    make_subject, saved = _patched_cli(tmp)
    try:
        assert cli.cmd_init(_args(force=True)) == 0
        assert cli.cmd_init(_args(force=False)) == 1
        assert (tmp / "mind.db").exists(), "store must survive a refused init"
    finally:
        _restore(saved)


def test_fixture_redirects_all_sidecar_paths():
    """Regression: _patched_cli once redirected only DB/SALIENCE, so
    cmd_init --force wiped the LIVE proposals/ and archive/ sidecars —
    deleting the real proposal journal and availability journal during a
    routine test run. Every path cmd_init touches must point at tmp."""
    tmp = Path(tempfile.mkdtemp(prefix="init-paths-"))
    live_proposals, live_archive = cli.PROPOSALS, cli.ARCHIVE
    make_subject, saved = _patched_cli(tmp)
    try:
        for name, live in (("PROPOSALS", live_proposals),
                           ("ARCHIVE", live_archive)):
            cur = getattr(cli, name)
            assert cur != live and str(cur).startswith(str(tmp)), \
                f"cli.{name} not redirected: {cur}"
        # The --force wipe itself must only clear the tmp tree.
        (tmp / "proposals").mkdir(exist_ok=True)
        (tmp / "archive").mkdir(exist_ok=True)
        (tmp / "proposals" / "sentinel.json").write_text("{}")
        (tmp / "archive" / "sentinel.json").write_text("{}")
        assert cli.cmd_init(_args(force=True)) == 0
        assert not (tmp / "proposals" / "sentinel.json").exists()
        assert not (tmp / "archive" / "sentinel.json").exists()
    finally:
        _restore(saved)
    assert cli.PROPOSALS == live_proposals and cli.ARCHIVE == live_archive, \
        "fixture leaked redirected paths into the live CLI"


def _main():
    for fn in (test_single_definition, test_force_resets_sidecar_and_restarts_ids,
               test_no_force_refuses_existing,
               test_fixture_redirects_all_sidecar_paths):
        fn()
        print(f"PASS {fn.__name__}")
    print("all init tests passed")


if __name__ == "__main__":
    _main()
