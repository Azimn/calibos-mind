"""Guard: the conftest redirect set must cover every cli store path.

If calibos_mind/cli.py gains a new module-level store path (a new sidecar),
this test fails until tests/conftest.py's REDIRECTED tuple is extended.
That is the structural fix for the 2026-10-22 incident class: five silent
live-file losses, each from a helper whose redirect set predated a new path.
"""

from pathlib import Path

from calibos_mind import cli
from conftest import REDIRECTED


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
