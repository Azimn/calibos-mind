"""Guard: the canonical redirect must cover every cli store path.

If calibos_mind/cli.py gains a new module-level store path (a new sidecar),
test_redirect_set_covers_every_store_path fails until tests/conftest.py's
REDIRECTED tuple is extended. That is the structural fix for the 2026-10-22
incident class: five silent live-file losses, each from a helper whose
redirect set predated a new path.

Two further guards (2026-11-08, after a standalone script outside pytest
lost four live sidecars to a partial hand-rolled patch set):

- test_helper_redirects_the_complete_set pins conftest.redirect_all_cli_paths
  — the single canonical helper fixtures, harnesses, and standalone scripts
  route through — to the same REDIRECTED set, including the derived
  expectation-policy path, which has no cli constant of its own.
- test_no_store_paths_outside_cli fails if any other calibos_mind module
  grows a module-level path constant into the tree: such a path cannot be
  redirected by patching cli (and a `from .cli import X` re-export would be
  a stale snapshot besides), so it would silently reopen the bug class.
  Store paths belong in cli.py + REDIRECTED; a genuinely non-store path
  constant earns an explicit exclusion here, with a reason.
"""

import importlib
import pkgutil
from pathlib import Path

import calibos_mind
from calibos_mind import cli
from conftest import REDIRECTED, redirect_all_cli_paths


def test_redirect_set_covers_every_store_path():
    base = Path(cli.__file__).resolve().parent.parent  # repo root, not cli.BASE
    live_base = base  # paths are BASE-derived; compare names, not values
    del live_base
    names = {n for n in dir(cli)
             if n.isupper() and n not in ("__name__",)}
    # Store paths are the UPPERCASE module attrs holding BASE-derived Paths,
    # excluding the cartridge (read-only input, never a write target) and
    # non-path constants.
    store_paths = set()
    for n in names:
        v = getattr(cli, n)
        if isinstance(v, Path):
            try:
                v.relative_to(Path(cli.BASE))
                is_base_derived = True
            except ValueError:
                is_base_derived = False
            if is_base_derived and n != "CARTRIDGE_PATH":
                store_paths.add(n)
    # _subject is a factory, not a path, but it must also never point at the
    # live store — the fixture leaves it alone; helpers override per-test.
    # (No assertion: out of scope for the path set.)
    missing = store_paths - set(REDIRECTED)
    extra = set(REDIRECTED) - store_paths - {"BASE"}
    assert not missing, (
        f"cli has store paths not covered by conftest REDIRECTED: {sorted(missing)}. "
        f"Add them to tests/conftest.py or the next reseed-test will eat live files."
    )
    assert not extra, (
        f"conftest REDIRECTED lists paths cli no longer has: {sorted(extra)}. "
        f"Prune tests/conftest.py."
    )


def test_helper_redirects_the_complete_set(tmp_path):
    root = tmp_path / "helper-tree"
    cartridge_before = cli.CARTRIDGE_PATH
    previous = redirect_all_cli_paths(root)
    try:
        # The helper covers exactly the canonical set — no more, no less.
        assert set(previous) == set(REDIRECTED), (
            f"redirect_all_cli_paths covers {sorted(previous)}, "
            f"REDIRECTED is {sorted(REDIRECTED)}"
        )
        for name in REDIRECTED:
            v = getattr(cli, name)
            assert v == root or v.parent == root, (
                f"cli.{name} not redirected under {root}: {v}"
            )
        # The expectation-policy sidecar has no constant of its own; it is
        # derived from INBOX's parent, so it must land under root too.
        from calibos_mind.inbox_expectations import POLICY_FILE_NAME
        policy = cli.INBOX.parent / POLICY_FILE_NAME
        assert policy.parent == root, (
            f"expectation-policy path escapes the redirect: {policy}"
        )
        # The cartridge is deliberately untouched (read-only input).
        assert cli.CARTRIDGE_PATH == cartridge_before
    finally:
        for name, value in previous.items():
            setattr(cli, name, value)


def test_no_store_paths_outside_cli():
    repo_root = Path(cli.__file__).resolve().parent.parent
    offenders = []
    for info in pkgutil.iter_modules(calibos_mind.__path__):
        if info.name == "cli":
            continue
        mod = importlib.import_module(f"calibos_mind.{info.name}")
        for n in dir(mod):
            if not n.isupper():
                continue
            v = getattr(mod, n)
            if not isinstance(v, Path):
                continue
            try:
                v.relative_to(repo_root)
            except ValueError:
                continue
            offenders.append(f"{info.name}.{n} = {v}")
    assert not offenders, (
        "module-level path constants into the tree outside cli.py cannot be "
        "redirected by the canonical helper and silently reopen the "
        f"live-file-loss bug class: {offenders}. Move store paths into "
        "calibos_mind/cli.py and tests/conftest.py REDIRECTED; if a constant "
        "is genuinely not a store path, exclude it here with a reason."
    )
