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

    Routes through the canonical conftest.redirect_all_cli_paths — the
    complete REDIRECTED set, not a hand-picked subset. (Earlier versions
    of this helper redirected only the paths their author knew about:
    DB and SALIENCE alone are not enough — cmd_init --force wipes the
    PROPOSALS and ARCHIVE sidecar directories, and the expectation-policy
    sidecar sits at INBOX's parent, so a partial fixture deletes real
    working files from the live tree.)
    """
    from conftest import redirect_all_cli_paths
    saved = redirect_all_cli_paths(tmp)
    saved["_subject"] = cli._subject
    db = tmp / "mind.db"
    salience = tmp / "salience.json"
    interoception = tmp / "interoception.json"
    inbox = tmp / "inbox"
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(salience),
                              interoception_path=str(interoception))

    cli._subject = make_subject
    return make_subject, saved


def _restore(saved):
    for name, value in saved.items():
        setattr(cli, name, value)


def _patched_cli_with_habits(tmp: Path):
    """_patched_cli plus habits/familiarity/ambivalence wiring on the
    subject. The F3 recovery path only engages when _subject() actually
    reads the habits sidecar — a corrupt file must raise inside
    _subject() without the fix, so the plain _patched_cli subject (no
    habits_path) would never exercise the failure."""
    make_subject, saved = _patched_cli(tmp)
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject_with_habits(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(
            str(db), cartridge, cognition=provider,
            salience_path=str(tmp / "salience.json"),
            interoception_path=str(tmp / "interoception.json"),
            familiarity_path=str(tmp / "familiarity.json"),
            ambivalence_path=str(tmp / "ambivalence.json"),
            habits_path=str(tmp / "habits-formed.json"))
    cli._subject = make_subject_with_habits
    return make_subject_with_habits, saved


def test_init_force_recovers_from_corrupt_habits_sidecar():
    """F3 (round-2 critic): fail-loud is only acceptable with a sanctioned
    recovery path. cmd_init --force unlinks the sidecars BEFORE
    constructing the subject, so a corrupt habits-formed.json is wiped
    rather than bricking _subject() — the mind reseeds with authored
    habits only."""
    tmp = Path(tempfile.mkdtemp(prefix="init-force-habits-"))
    make_subject, saved = _patched_cli_with_habits(tmp)
    try:
        assert cli.cmd_init(_args(force=False)) == 0
        habits = tmp / "habits-formed.json"
        habits.write_text('{"window": [{"tick": 1,', encoding="utf-8")
        assert cli.cmd_init(_args(force=True)) == 0
        assert not habits.exists(), \
            "init --force must wipe the corrupt sidecar"
        fresh = make_subject()
        assert {k for k in fresh.engine.state.habits
                if k.startswith("formed:")} == set()
        assert set(fresh.engine.state.habits) == {
            "curious_question", "verify_before_claiming"}
    finally:
        _restore(saved)


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

        # Contaminate the policy sidecar with a streak carried from the
        # previous incarnation — a fresh mind must not inherit failure.
        policy = tmp / "expectation_policy.json"
        policy.write_text(json.dumps({"expiry_streak": 1}), encoding="utf-8")

        # Reseed: ids restart at experience-1, sidecar must restart too.
        assert cli.cmd_init(_args(force=True)) == 0
        second_ids = _record_ids(make_subject)
        assert first_ids == second_ids, (first_ids, second_ids)
        assert _sidecar_importance(salience) == {}, \
            "stale importance must not attach to recycled ids"
        assert not policy.exists(), \
            "stale expiry streak must not penalize a reseeded mind"
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
    live_interoception = cli.INTEROCEPTION
    live_inbox = cli.INBOX
    make_subject, saved = _patched_cli(tmp)
    try:
        for name, live in (("PROPOSALS", live_proposals),
                           ("ARCHIVE", live_archive),
                           ("INTEROCEPTION", live_interoception),
                           ("INBOX", live_inbox)):
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
    assert cli.INTEROCEPTION == live_interoception, \
        "fixture leaked redirected interoception path into the live CLI"
    assert cli.INBOX == live_inbox, \
        "fixture leaked redirected inbox path into the live CLI"


def test_fresh_init_refuses_orphaned_sidecars_without_force():
    """2026-11-08 regression: the incident shape. A caller redirected DB
    (here: the whole canonical set, to tmp) but the tree holds sidecar
    state with NO store — a fresh init must refuse rather than unlink
    state it cannot account for. Before the fix, this exact shape
    (cmd_init(force=False), DB absent) silently wiped the sidecars."""
    tmp = Path(tempfile.mkdtemp(prefix="init-orphan-"))
    make_subject, saved = _patched_cli(tmp)
    try:
        mood = tmp / "mood.json"
        mood.write_text('{"residues": {"attachment": 0.327}}', encoding="utf-8")
        proposals = tmp / "proposals"
        proposals.mkdir(exist_ok=True)
        (proposals / "proposals.json").write_text("{}", encoding="utf-8")
        assert cli.cmd_init(_args(force=False)) == 1
        assert mood.read_text(encoding="utf-8") == \
            '{"residues": {"attachment": 0.327}}', "orphaned sidecar must survive"
        assert (proposals / "proposals.json").exists(), \
            "orphaned journal must survive"
        assert not (tmp / "mind.db").exists(), \
            "a refused init must not create a store"
    finally:
        _restore(saved)


def test_fresh_init_force_wipes_only_when_redirected():
    """--force lifts the orphan refusal when every present sidecar has
    been redirected away from the live tree — the test suite's legitimate
    tmp-dir reseed shape, preserved."""
    tmp = Path(tempfile.mkdtemp(prefix="init-orphan-force-"))
    make_subject, saved = _patched_cli(tmp)
    try:
        mood = tmp / "mood.json"
        mood.write_text('{"residues": {"attachment": 0.327}}', encoding="utf-8")
        assert cli.cmd_init(_args(force=True)) == 0
        assert not mood.exists(), "redirected orphan must be wiped under --force"
        assert (tmp / "mind.db").exists(), "forced fresh init must seed a store"
    finally:
        _restore(saved)


def test_fresh_init_refuses_live_located_sidecar_even_with_force():
    """--force must NOT let an init delete state at its live location.
    Simulates the 2026-11-08 incident directly: DB redirected to a
    nonexistent tmp path (so init sees 'no store') while MOOD still
    points at the live tree (here, a fake live root)."""
    tmp = Path(tempfile.mkdtemp(prefix="init-live-"))
    make_subject, saved = _patched_cli(tmp)
    fake_live = Path(tempfile.mkdtemp(prefix="init-fake-live-"))
    live_mood = fake_live / "mood.json"
    live_mood.write_text('{"residues": {"attachment": 0.327}}', encoding="utf-8")
    saved_live_base, saved_mood = cli._live_base, cli.MOOD
    cli._live_base, cli.MOOD = fake_live, live_mood
    try:
        assert cli.cmd_init(_args(force=True)) == 1
        assert live_mood.read_text(encoding="utf-8") == \
            '{"residues": {"attachment": 0.327}}', \
            "live-located sidecar must survive even a forced fresh init"
        assert not (tmp / "mind.db").exists(), \
            "a refused init must not create a store"
    finally:
        cli._live_base, cli.MOOD = saved_live_base, saved_mood
        _restore(saved)


def _main():
    for fn in (test_single_definition, test_force_resets_sidecar_and_restarts_ids,
               test_no_force_refuses_existing,
               test_fixture_redirects_all_sidecar_paths,
               test_init_force_recovers_from_corrupt_habits_sidecar,
               test_fresh_init_refuses_orphaned_sidecars_without_force,
               test_fresh_init_force_wipes_only_when_redirected,
               test_fresh_init_refuses_live_located_sidecar_even_with_force):
        fn()
        print(f"PASS {fn.__name__}")
    print("all init tests passed")


if __name__ == "__main__":
    _main()
