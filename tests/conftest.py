"""Structural guard: no test may touch the live calibos-mind store.

2026-10-22: the fifth silent loss of live sidecar files (proposals journal,
authored-dormancy.json, counterfactuals.json) traced to builder/critic test
loops invoking `cli.cmd_init --force` — or sibling destructive paths — with
incomplete redirect sets. Every historical helper redirected "the paths its
test needed" and each new sidecar added a new way to get the set wrong
(2026-10-08 harness lesson, 2026-10-21 BASE lesson, 2026-10-22
COUNTERFACTUAL/DORMANCY lesson).

2026-11-08: a standalone critic script running OUTSIDE pytest — so, no
conftest fixture — patched only part of the set and its cmd_init(force=False)
on a nonexistent tmp DB proceeded as a fresh init, unlinking four live
sidecars. The fixture alone cannot close that vector; the redirect set now
lives in ONE canonical helper, `redirect_all_cli_paths`, which fixtures,
harnesses, and standalone scripts all route through. Standalone usage:

    import sys
    sys.path.insert(0, "tests")  # or otherwise make conftest importable
    from conftest import redirect_all_cli_paths
    previous = redirect_all_cli_paths(some_tmp_dir)
    try:
        ... work (cmd_init included) ...
    finally:
        for name, value in previous.items():
            setattr(cli, name, value)

The autouse fixture below makes the bug class structurally impossible
under pytest: for every test, ALL module-level store paths on
`calibos_mind.cli` are pointed at a fresh tmp tree before the test runs
and restored afterward. A test's own helper may still redirect further to
its own tmp paths — that nests safely inside this fixture.

CARTRIDGE_PATH is deliberately NOT redirected: tests need the real
cartridge to construct subjects. Nothing in the test suite writes to the
cartridge file.

Adding a new sidecar: add the path constant to calibos_mind/cli.py, its
live filename to _FILENAMES below, and its name to REDIRECTED.
test_sidecar_guard.py fails until all three are done.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from calibos_mind import cli


# Every module-level store path cmd_init --force (and sibling destructive
# paths) can touch. CARTRIDGE_PATH excluded — see module docstring.
# If a new sidecar path is added to cli.py, it MUST be added here too;
# test_sidecar_guard.py fails otherwise.
REDIRECTED = (
    "BASE", "DB", "INBOX", "DREAMS", "SALIENCE", "INTEROCEPTION",
    "FAMILIARITY", "AMBIVALENCE", "COUNTERFACTUAL", "HABITS",
    "DORMANCY", "PROVENANCE", "EXPECTATIONS", "MOOD", "ARCHIVE", "PROPOSALS",
    "WAKE_LIVENESS", "ABLATION_SALT_FILE", "ABLATION_LOG",
)

# Basenames for the synthetic tree, mirroring the live layout.
_FILENAMES = {
    "DB": "mind.db",
    "INBOX": "inbox",
    "DREAMS": "dreams",
    "SALIENCE": "salience.json",
    "INTEROCEPTION": "interoception.json",
    "FAMILIARITY": "familiarity.json",
    "AMBIVALENCE": "ambivalence.json",
    "COUNTERFACTUAL": "counterfactuals.json",
    "HABITS": "habits-formed.json",
    "DORMANCY": "authored-dormancy.json",
    "PROVENANCE": "provenance.json",
    "EXPECTATIONS": "expectations.json",
    "MOOD": "mood.json",
    "ARCHIVE": "archive",
    "PROPOSALS": "proposals",
    "WAKE_LIVENESS": "wake_liveness.json",
    "ABLATION_SALT_FILE": "ablation_salt",
    "ABLATION_LOG": "ablation_log.jsonl",
}


def redirect_all_cli_paths(root, setter=None):
    """Point EVERY calibos_mind.cli store path at a synthetic tree.

    THE canonical redirect — the single place the complete set is applied.
    Hand-rolled partial sets are the recurring mechanism behind the
    live-file losses: a caller redirected DB (and the sidecars it knew
    about) but left a newer sidecar pointed at the live tree, and a
    destructive command wiped the live file.

    `root` plays the role of the repo root (BASE); every other path is
    laid out under it with the live tree's filename. One path has no cli
    constant of its own: the expectation-policy sidecar is derived from
    INBOX's parent (calibos_mind.inbox_expectations.POLICY_FILE_NAME), so
    redirecting INBOX carries it along — after this call it lives at
    root / "expectation_policy.json".

    `setter` defaults to plain setattr (standalone scripts, manual
    harnesses); the pytest fixture passes monkeypatch.setattr so the
    redirect unwinds automatically. Either way, returns
    {name: previous value} covering exactly REDIRECTED, so plain-setattr
    callers can restore precisely what they replaced.
    """
    root = Path(root)
    previous = {}
    for name in REDIRECTED:
        target = root if name == "BASE" else root / _FILENAMES[name]
        previous[name] = getattr(cli, name)
        if setter is None:
            setattr(cli, name, target)
        else:
            setter(cli, name, target)
    # Directories the CLI expects to exist.
    (root / "inbox").mkdir(parents=True, exist_ok=True)
    return previous


@pytest.fixture(autouse=True)
def _guard_live_store(tmp_path, monkeypatch):
    """Point every cli store path at a synthetic tree for the test."""
    redirect_all_cli_paths(tmp_path, setter=monkeypatch.setattr)
    yield
    # monkeypatch reverts all setattr calls automatically.
