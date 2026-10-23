"""Structural guard: no test may touch the live calibos-mind store.

2026-10-22: the fifth silent loss of live sidecar files (proposals journal,
authored-dormancy.json, counterfactuals.json) traced to builder/critic test
loops invoking `cli.cmd_init --force` — or sibling destructive paths — with
incomplete redirect sets. Every historical helper redirected "the paths its
test needed" and each new sidecar added a new way to get the set wrong
(2026-10-08 harness lesson, 2026-10-21 BASE lesson, 2026-10-22
COUNTERFACTUAL/DORMANCY lesson).

This autouse fixture makes the bug class structurally impossible: for every
test, ALL module-level store paths on `calibos_mind.cli` are pointed at a
fresh tmp tree before the test runs and restored afterward. A test's own
`_patched_cli`/`CliOnTmp` helper may still redirect further to its own tmp
paths — that nests safely inside this fixture.

CARTRIDGE_PATH is deliberately NOT redirected: tests need the real
cartridge to construct subjects. Nothing in the test suite writes to the
cartridge file.
"""

from __future__ import annotations

import pytest

from calibos_mind import cli


# Every module-level store path cmd_init --force (and sibling destructive
# paths) can touch. CARTRIDGE_PATH excluded — see module docstring.
# If a new sidecar path is added to cli.py, it MUST be added here too;
# test_sidecar_guard.py fails otherwise.
REDIRECTED = (
    "BASE", "DB", "INBOX", "DREAMS", "SALIENCE", "INTEROCEPTION",
    "FAMILIARITY", "AMBIVALENCE", "COUNTERFACTUAL", "HABITS",
    "DORMANCY", "PROVENANCE", "EXPECTATIONS", "ARCHIVE", "PROPOSALS",
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
    "ARCHIVE": "archive",
    "PROPOSALS": "proposals",
    "WAKE_LIVENESS": "wake_liveness.json",
    "ABLATION_SALT_FILE": "ablation_salt",
    "ABLATION_LOG": "ablation_log.jsonl",
}


@pytest.fixture(autouse=True)
def _guard_live_store(tmp_path, monkeypatch):
    """Point every cli store path at a synthetic tree for the test."""
    for name in REDIRECTED:
        if name == "BASE":
            monkeypatch.setattr(cli, name, tmp_path)
        else:
            monkeypatch.setattr(cli, name, tmp_path / _FILENAMES[name])
    # Directories the CLI expects to exist.
    (tmp_path / "inbox").mkdir(exist_ok=True)
    yield
    # monkeypatch reverts all setattr calls automatically.
