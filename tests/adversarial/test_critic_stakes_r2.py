"""Critic round 2 — loss ledger (Domain 31, 2026-11-24), re-verification.

Spec: research/specs/2026-11-24-loss-ledger.md
Brief: spec + diff only. Every objection here arrives as a failing test
or a concretely violated invariant; prose-only critique is rejected.

Round-1 context (only this much): round 1 returned FAIL on exactly one
test, test_stakes_sidecar_has_backup_visibility_decision (stakes.json
had no .gitignore entry; the daily backup would have pushed verbatim
commitment text to the remote repo). The builder claims to have fixed
it with a dated local-only .gitignore entry plus a CHANGELOG line.

This round:
  1. Re-runs the round-1 attack file (13 tests) — all pass, including
     the previously failing test (verified separately, not duplicated
     here except as the dry-run guard below).
  2. Fresh attacks on the same code:
     a. The atomic-save shadow: StakesTracker.save() writes
        stakes.json.tmp (same dir, os.replace) on EVERY save. A
        crash/kill between the write and the replace leaves
        stakes.json.tmp on disk holding the full ledger body —
        verbatim commitment descriptions and outcome text — and the
        .gitignore has no *.tmp entry, so the next daily backup
        (`git add -A`) commits and pushes it. Same leak class as the
        round-1 FAIL, via the same backup path, through the file the
        round-1 fix itself did not cover. FAILING test below.
     b. Backup-path dry-run guard: `git add -A --dry-run` must stage
        no stakes.json / stakes.json.tmp.
     c. The .gitignore entry must follow sibling precedent (dated
        comment, local-only wording, placed with the other sidecars)
        and the CHANGELOG must record the decision.
     d. The save write-set is exactly {stakes.json} after a clean
        save, so the .tmp shadow is the complete hole — no other
        stray file the code can leave behind.

All fixtures on /tmp — the live store is never touched (conftest
autouse redirect). No file is created in the real repo by these
tests; the ignore checks are read-only `git check-ignore` calls.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind.stakes import StakesTracker
from digital_subject.models import Action, Habit


def _repo_root() -> Path:
    import calibos_mind.cli as cli
    return Path(cli.__file__).resolve().parent.parent


def _check_ignore(*names: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "check-ignore", "-q", "--", *names],
        cwd=_repo_root(), capture_output=True, text=True)


# -- FAILING: the atomic-save shadow file is not ignored ---------------------

def test_stakes_tmp_shadow_is_ignored():
    """StakesTracker.save() writes stakes.json.tmp in the repo root on
    every save and removes it only via os.replace. A crash or kill
    between the write and the replace leaves stakes.json.tmp on disk
    holding the complete ledger body — verbatim commitment
    descriptions and outcome text, the exact content the round-1 fix
    (dated local-only .gitignore entry for stakes.json) exists to
    keep out of the repo. .gitignore has no *.tmp entry and no
    stakes.json.tmp entry, so the next daily backup (`git add -A` in
    calibos-mind-backup) will commit and push the leftover shadow.
    The fix is one line: ignore the shadow (stakes.json.tmp, or
    *.tmp) alongside the ledger."""
    result = _check_ignore("stakes.json.tmp")
    assert result.returncode == 0, (
        "stakes.json.tmp is not gitignored: a crash-leftover atomic-save "
        "shadow holds the full loss ledger and the next `git add -A` "
        "backup will publish it. Ignore stakes.json.tmp (or *.tmp) "
        "alongside stakes.json.")


def test_tmp_shadow_is_covered_in_gitignore():
    """Documents the scope of the fix (round 3 rename: the old name
    described the hole, this one the coverage): the blanket *.tmp
    entry covers every tracker that atomic-saves (followthrough,
    habits, authored_dormancy, stakes), not just the stakes shadow.
    This test fails until at least the stakes shadow is covered;
    the sibling shadows are pre-existing and out of this mutation's
    scope, flagged to the design lead as a genome candidate, not as
    this builder's defect."""
    gitignore = (_repo_root() / ".gitignore").read_text(encoding="utf-8")
    assert "stakes.json.tmp" in gitignore or "*.tmp" in gitignore, (
        ".gitignore covers neither stakes.json.tmp nor *.tmp: the "
        "atomic-save shadow of the new ledger is publishable.")


def test_crash_leftover_tmp_is_not_staged_by_backup():
    """End-to-end regression guard, in a throwaway repo using the REAL
    .gitignore copied verbatim: a crash-leftover stakes.json.tmp
    holding ledger content must NOT be staged by the backup's
    `git add -A`. save() writes the tmp then removes it only via
    os.replace, so a kill between the write and the replace leaves
    exactly this file behind; the blanket *.tmp entry (2026-11-24)
    keeps it out of the repo. Round-2 form of this test asserted the
    bypass (that the leftover WAS staged) as the proof of the hole;
    once the fix landed that assertion inverted to this guard — same
    scenario, same proof, now asserting the fixed behavior."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-stakes-r2-bypass-"))
    (tmp / ".gitignore").write_text(
        (_repo_root() / ".gitignore").read_text(encoding="utf-8"),
        encoding="utf-8")
    (tmp / "stakes.json.tmp").write_text(
        '{"losses": [{"kind": "broken-commitment", "commitment_id": '
        '"c-1", "description": "verbatim private text", "tick": 3, '
        '"outcome": "missed it"}], "seen_habits": {}}',
        encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    result = subprocess.run(
        ["git", "add", "-A", "--dry-run"],
        cwd=tmp, capture_output=True, text=True)
    assert result.returncode == 0
    assert "stakes.json.tmp" not in result.stdout, (
        "a crash-leftover stakes.json.tmp holding ledger content "
        "would be staged by the backup's `git add -A`; the *.tmp "
        f"ignore is not covering it. Dry-run output was:\n{result.stdout}")


def test_guard_is_sensitive_to_the_tmp_ignore():
    """Positive control for the inverted guard above (round 3): the
    SAME scenario with the blanket *.tmp line stripped from the
    copied .gitignore must stage the leftover — i.e. the guard above
    is a real regression test, not vacuous. If this control ever
    stops staging the file, the guard's inversion was test-weakening
    (the scenario no longer proves the hole) and the guard must be
    rewritten, not trusted."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-stakes-r2-control-"))
    lines = (_repo_root() / ".gitignore").read_text(
        encoding="utf-8").splitlines()
    stripped = [ln for ln in lines if ln.strip() != "*.tmp"]
    assert len(stripped) == len(lines) - 1, (
        "expected exactly one *.tmp line in .gitignore to strip")
    (tmp / ".gitignore").write_text("\n".join(stripped) + "\n",
                                    encoding="utf-8")
    (tmp / "stakes.json.tmp").write_text(
        '{"losses": [{"kind": "broken-commitment", "commitment_id": '
        '"c-1", "description": "verbatim private text", "tick": 3, '
        '"outcome": "missed it"}], "seen_habits": {}}',
        encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    result = subprocess.run(
        ["git", "add", "-A", "--dry-run"],
        cwd=tmp, capture_output=True, text=True)
    assert result.returncode == 0
    assert "stakes.json.tmp" in result.stdout, (
        "control failed: without the *.tmp ignore the leftover "
        "shadow should be staged by `git add -A` — if it is not, "
        "the guard above proves nothing and its inversion was "
        "test-weakening. Dry-run output was:\n" + result.stdout)


# -- passing guards: the round-1 fix is real, not test-weakening -------------

def test_blanket_tmp_covers_all_tracker_shadows():
    """Round 3, fresh attack on the systemic claim: the blanket *.tmp
    entry must keep EVERY tracker's crash-leftover shadow out of the
    backup, not just stakes.json.tmp — followthrough.py, habits.py,
    and authored_dormancy.py all write the same `<sidecar>.tmp`
    pattern, and consolidate.py mkstemps `proposals.*.tmp` /
    `availability.*.tmp`. End-to-end in a throwaway repo with the
    REAL .gitignore: none of the shadows, including one nested in a
    subdirectory, may be staged by `git add -A`."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-stakes-r3-shadows-"))
    (tmp / ".gitignore").write_text(
        (_repo_root() / ".gitignore").read_text(encoding="utf-8"),
        encoding="utf-8")
    shadows = [
        "stakes.json.tmp",
        "followthrough.json.tmp",
        "habits.json.tmp",
        "authored_dormancy.json.tmp",
        "proposals.abc123.tmp",
        "availability.xyz.tmp",
        "nested/dir/deep.tmp",
    ]
    (tmp / "nested" / "dir").mkdir(parents=True)
    for name in shadows:
        (tmp / name).write_text(
            '{"losses": [{"kind": "broken-commitment", "commitment_id": '
            '"c-1", "description": "verbatim private text", "tick": 3, '
            '"outcome": "missed it"}], "seen_habits": {}}',
            encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    result = subprocess.run(
        ["git", "add", "-A", "--dry-run"],
        cwd=tmp, capture_output=True, text=True)
    assert result.returncode == 0
    for name in shadows:
        assert name not in result.stdout, (
            f"crash-leftover shadow {name} would be staged by the "
            f"backup's `git add -A`; the blanket *.tmp entry does not "
            f"cover it. Dry-run output was:\n{result.stdout}")

def test_backup_dry_run_stages_no_ledger_file():
    """The actual backup command is `git add -A` from the repo root.
    A dry run must stage no file whose name is stakes.json or
    stakes.json.tmp. (The tracked sources calibos_mind/stakes.py and
    the tests are expected and allowed.)"""
    result = subprocess.run(
        ["git", "add", "-A", "--dry-run"],
        cwd=_repo_root(), capture_output=True, text=True)
    assert result.returncode == 0
    staged = result.stdout
    assert "stakes.json" not in staged.replace(
        "calibos_mind/stakes.py", "").replace(
        "tests/test_stakes.py", "").replace(
        "tests/adversarial/test_critic_stakes_r1.py", ""), (
        f"backup dry-run would stage a ledger file:\n{staged}")
    assert "stakes.json.tmp" not in staged


def test_gitignore_entry_follows_sibling_precedent():
    """The round-1 fix must be a real decision in the sibling form:
    a dated comment, local-only wording, placed with the other
    private sidecars — not a bare bare pattern with no record."""
    gitignore = (_repo_root() / ".gitignore").read_text(encoding="utf-8")
    lines = gitignore.splitlines()
    idx = lines.index("stakes.json")
    block = "\n".join(lines[max(0, idx - 8):idx + 1])
    assert "2026-11-24" in block, "stakes.json entry has no dated comment"
    assert "local-only" in block, "entry does not state local-only"
    assert "never tracked" in block, "entry does not record the decision"
    # Placement: among the sidecar entries, after followthrough.json.
    assert lines.index("followthrough.json") < idx
    assert lines.index("salience.json") < idx


def test_changelog_records_backup_decision():
    """The CHANGELOG must record the backup-visibility decision, so a
    later reader does not re-litigate whether the ledger is tracked."""
    changelog = (_repo_root() / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "stakes.json" in changelog
    assert "local-only" in changelog
    assert "never tracked" in changelog


def test_clean_save_write_set_is_exactly_the_sidecar():
    """After a clean observe+save the directory holds exactly
    stakes.json — no leftover .tmp, no second file. This bounds the
    hole: the only file this code can leave behind besides the
    ledger itself is the .tmp shadow covered by the failing test
    above."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-stakes-r2-"))
    tracker = StakesTracker(tmp / "stakes.json")
    key = "formed:curiosity:explore"
    tracker.observe(SimpleNamespace(
        engine=SimpleNamespace(state=SimpleNamespace(
            tick=1, habits={key: Habit(key, "curiosity", Action.WITHDRAW,
                                       0.70, 0, 0)})),
        continuity=SimpleNamespace(state=SimpleNamespace(
            commitments={}))), 1)
    tracker.observe(SimpleNamespace(
        engine=SimpleNamespace(state=SimpleNamespace(
            tick=2, habits={key: Habit(key, "curiosity", Action.WITHDRAW,
                                       0.60, 0, 0)})),
        continuity=SimpleNamespace(state=SimpleNamespace(
            commitments={}))), 2)
    tracker.save()
    assert sorted(p.name for p in tmp.iterdir()) == ["stakes.json"]
    assert len(tracker.losses) == 1
