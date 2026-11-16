"""Critic round 1 — attack the 2026-11-15 realization follow-through tracker.

Spec: research/specs/2026-11-15-realization-followthrough.md.
Brief: spec + diff only. Every test below is an independently written
probe on synthetic /tmp stores; the live store is never touched. A
failure here is a concrete violated invariant, not prose.

Premises proved before writing (probe runs, not assumed):
- The full suite (1121 tests) passes with the builder's code in place;
  the builder's own test_followthrough.py passes under pytest.
- The claimed latent harness flaw is REAL, proved in a scratch copy
  (/tmp/critic-baseline) with the five harness files reverted to HEAD:
  all six two-run tests fail with
  "ValueError: corrupt followthrough sidecar ...: log ticks out of order
  at [1, 'explore'] (previous tick 12)". The second fresh store in one
  test restarts ticks at 1 on top of the first run's log, exactly as the
  builder claimed. The harness fix was necessary, not a cover.
- Assertion multisets in the five harness-edited files are IDENTICAL
  between HEAD and worktree (sorted uniq -c comparison): no assertion
  was weakened, added, or removed — only wrapped in the canonical
  per-run redirect.
- No CLI path restarts ticks under a live followthrough sidecar:
  cmd_init refuses when the store exists without --force, refuses
  orphan sidecars (followthrough.json IS in _init_wipe_targets, which
  _orphan_state_refusal enumerates), and --force wipes the sidecar
  before any construction. A tick restart under a live sidecar needs
  manual DB surgery, not a CLI path.
- The engine never mints swing >= conv: update() opens an episode in
  the `episode is None` branch and can only close it in a LATER
  iteration's `else` branch, so conv_tick > swing_tick strictly for
  engine-minted realizations (interoception.py). swing > conv is
  reachable only through hand-authored record text.
- The shadowing-genome tests check MODULE-LEVEL def names only
  (ast over tree.body); no genome test counts nested defs. The
  builder's CHANGELOG claim that the `_fmt` -> `_fmt_shares` rename
  was needed because "the shadowing-definitions genome test counts
  nested defs" is inaccurate — two nested `_fmt`s in different
  functions could never shadow each other. Harmless rename, wrong
  rationale; noted, not failed.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.followthrough import (
    FollowthroughTracker,
    report,
)
from calibos_mind.interoception import mint_realization


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-followthrough-r1-"))


def _needs(**over):
    d = {"energy": 0.95, "fatigue": 0.05, "hunger": 0.05, "thirst": 0.05,
         "comfort": 0.95, "pain": 0.0, "warmth": 0.9, "restlessness": 0.05,
         "curiosity": 0.10, "loneliness": 0.05, "safety": 0.9, "focus": 0.9,
         "satisfaction": 0.9}
    d.update(over)
    return d


def _pressures(**over):
    d = {"fear": 0.10, "trust": 0.5, "arousal": 0.05}
    d.update(over)
    return d


def _baselines(**over):
    d = {"fear": 0.0, "trust": 0.5, "arousal": 0.0}
    d.update(over)
    return d


def _set_state(subject, needs, pressures, baselines):
    st = subject.engine.state
    st.needs = dict(needs)
    st.pressures = dict(pressures)
    st.pressure_baselines = dict(baselines)
    st.concerns = {}


def _script_tick(subject, needs, pressures, baselines):
    with subject._transaction():
        _set_state(subject, needs, pressures, baselines)
    return cli._run_tick(subject)


def _init_store():
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _mint(subject, need, swing, conv):
    event = {"need": need, "swing_tick": swing, "swing_felt": 0.9,
             "swing_actual": 0.5, "gap_max": 0.4, "conv_tick": conv}
    with subject._transaction():
        return mint_realization(subject, event)


def _fill(start, end, action):
    return [[t, action] for t in range(start, end + 1)]


def _write_sidecar(path: Path, rows: list) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


# -- (b) tick-feed wiring: corrupt sidecar must fail BEFORE the tick ------

def test_critic_corrupt_sidecar_tick_does_not_advance():
    """A corrupt followthrough sidecar must raise before the heartbeat
    runs: engine tick unchanged, trace length unchanged, sidecar
    byte-identical. A fail-loud that fires after the tick advanced
    would be a half-tick, not fail-closed."""
    subject = _init_store()
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                     _baselines())
    sidecar = cli.FOLLOWTHROUGH
    assert sidecar.exists()
    before = sidecar.read_bytes()
    tick_before = subject.engine.state.tick
    trace_before = len(subject.inspect()["trace"])
    sidecar.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt followthrough"):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                     _baselines())
    assert subject.engine.state.tick == tick_before, \
        "corrupt sidecar let the tick advance before failing"
    assert len(subject.inspect()["trace"]) == trace_before, \
        "corrupt sidecar let the trace grow before failing"
    assert sidecar.read_bytes() == b"{not json", \
        "failed tick wrote the sidecar"


def test_critic_corrupt_sidecar_out_of_order_tick_does_not_advance():
    """Same fail-closed placement for the out-of-order shape: the
    second run's tick-1 must not advance on top of a stale log."""
    subject = _init_store()
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                     _baselines())
    tick_before = subject.engine.state.tick
    # Simulate a stale log from an earlier incarnation (ticks past the
    # live store's tick): the next tick must refuse, not append.
    _write_sidecar(cli.FOLLOWTHROUGH,
                   _fill(1, 3, "explore") + [[2, "approach"]])
    with pytest.raises(ValueError, match="out of order"):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                     _baselines())
    assert subject.engine.state.tick == tick_before


# -- (d) read-only / dream isolation, independently re-run -----------------

def test_critic_readonly_sweep_leaves_sidecar_byte_identical():
    """Independent re-run of the read-only sweep: drift, status,
    review, inbox, followthrough, predictions, goals never write the
    sidecar (byte-identical, not just equal rows)."""
    subject = _init_store()
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                     _baselines())
    sidecar = cli.FOLLOWTHROUGH
    assert sidecar.exists()
    before = sidecar.read_bytes()
    cli.cmd_drift(argparse.Namespace(window=10))
    cli.cmd_status(argparse.Namespace(raw=False))
    cli.cmd_review(argparse.Namespace(n=5))
    cli.cmd_inbox(argparse.Namespace())
    cli.cmd_followthrough(argparse.Namespace())
    cli.cmd_predictions(argparse.Namespace())
    cli.cmd_goals(argparse.Namespace())
    assert sidecar.read_bytes() == before, \
        "read-only command wrote the sidecar"


def test_critic_readonly_commands_mint_no_sidecar_on_fresh_store():
    """On a fresh store, no read-only command mints followthrough.json
    — not even cmd_followthrough itself."""
    _init_store()
    assert not cli.FOLLOWTHROUGH.exists()
    cli.cmd_drift(argparse.Namespace(window=10))
    cli.cmd_status(argparse.Namespace(raw=False))
    cli.cmd_followthrough(argparse.Namespace())
    cli.cmd_predictions(argparse.Namespace())
    cli.cmd_goals(argparse.Namespace())
    assert not cli.FOLLOWTHROUGH.exists(), \
        "a read-only command minted the sidecar"


def test_critic_dream_tick_never_appends():
    """A dream tick leaves the action log byte-identical (independent
    re-run; dream_tick() is a separate path from _run_tick)."""
    subject = _init_store()
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(),
                     _baselines())
    sidecar = cli.FOLLOWTHROUGH
    before = sidecar.read_bytes()
    subject.dream_tick()
    assert sidecar.read_bytes() == before, \
        "dream tick wrote the sidecar"


# -- (c) insufficient-window honesty: no fabricated 0.0 anywhere -----------

def test_critic_swing_after_conv_never_reports_measured_tv():
    """FAIL: a realization whose swing tick is AFTER its convergence
    tick has no disjoint before/after windows at all — the before
    window (last 20 rows < swing) and the after window (first 20 rows
    > conv) are the SAME rows (ticks 41-60 in both below), so the
    reported tv 0.0 measures nothing: a quiet 0.0 in substance, the
    exact hazard class the spec's honesty rule exists to prevent.
    Engine minting can never produce swing > conv (verified in
    interoception.update(): an episode opens in one iteration and can
    only close in a LATER iteration), so this is reachable only
    through hand-authored record text or a future minter bug — but the
    report is the honesty boundary and must not certify a vacuous
    reading as 'measured'. Expected: status insufficient-window (or
    the row rejected), tv None, mean None."""
    subject = _init_store()
    text = ("At tick 60 I felt comfort as settled, but my body was "
            "only settled; by tick 40 the feeling caught up.")
    with subject._transaction():
        subject._add("temporal", text, generated_by="authored",
                     available_to_cognition=True, salience=0.5,
                     intensity=0.5)
    _write_sidecar(cli.FOLLOWTHROUGH, _fill(1, 100, "approach"))
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    row = rep["rows"][0]
    assert (row["swing_tick"], row["conv_tick"]) == (60, 40), row
    assert row["status"] == "insufficient-window", row
    assert row["tv"] is None, row
    assert rep["mean_tv"] is None, rep

def test_critic_zero_measurable_cli_never_prints_zero_mean():
    """Zero measurable realizations -> the CLI prints 'undefined' and
    never a 'mean tv distance: 0.00' line; report mean_tv is None."""
    subject = _init_store()
    _mint(subject, "comfort", swing=30, conv=60)
    _write_sidecar(cli.FOLLOWTHROUGH, _fill(50, 80, "approach"))
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    assert rep["mean_tv"] is None, rep
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_followthrough(argparse.Namespace())
    out = buf.getvalue()
    assert "undefined" in out, out
    assert "mean tv distance: 0.00" not in out, out
    assert "tv 0.00" not in out, out


def test_critic_partial_windows_never_contribute_zero_to_mean():
    """One measured row (tv 0.0, genuine) + one insufficient row ->
    the mean is the measured row's 0.0 only; the insufficient row
    contributes no second 0.0. Distinguished by the counts, not by
    the mean value: measurable == 1, insufficient == 1."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    _mint(subject, "loneliness", swing=90, conv=110)
    rows = (_fill(1, 20, "approach") + _fill(21, 40, "approach")
            + _fill(41, 60, "approach"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    assert rep["measurable"] == 1 and rep["insufficient"] == 1, rep
    assert rep["mean_tv"] == 0.0
    assert [r["status"] for r in rep["rows"]] == [
        "measured", "insufficient-window"], rep


# -- tick-restart poison: characterization of the spec'd fail-loud --------

def test_critic_tick_restart_writes_poison_then_next_tick_refuses():
    """Characterization (passes on spec'd behavior): if ticks restart
    under a live sidecar — reachable only by manual DB surgery, since
    cmd_init refuses orphan sidecars and --force wipes first — the
    restarted tick's observe() appends silently and save() writes an
    out-of-order file; only the NEXT construction refuses. The poison
    is written by a tick that itself completes. Documented here so the
    failure mode is on record, not hidden: fail-loud lands one tick
    late for this shape."""
    tmp = _tmp()
    path = tmp / "followthrough.json"
    tracker = FollowthroughTracker(path)
    for t in range(1, 11):
        assert tracker.observe(t, "explore") is True
    tracker.save()
    # Tick restart (manual DB restore): tick 1 observed again.
    restarted = FollowthroughTracker(path)
    assert restarted.observe(1, "approach") is True  # no guard here
    restarted.save()
    assert json.loads(path.read_text(encoding="utf-8"))[-1] == [1, "approach"]
    with pytest.raises(ValueError, match="out of order"):
        FollowthroughTracker(path)


# -- determinism + cap, independently re-run --------------------------------

def test_critic_identical_scripts_byte_identical_sidecar():
    """Independent determinism re-run through the real _run_tick wiring
    on two fresh synthetic trees."""
    from conftest import redirect_all_cli_paths

    def _run_script(root: Path, ticks: int = 12) -> bytes:
        previous = redirect_all_cli_paths(root)
        try:
            cli.cmd_init(argparse.Namespace(force=False))
            subject = cli._subject()
            for _ in range(ticks):
                _script_tick(subject, _needs(curiosity=0.80),
                             _pressures(), _baselines())
            return cli.FOLLOWTHROUGH.read_bytes()
        finally:
            for name, value in previous.items():
                setattr(cli, name, value)

    a = _run_script(_tmp(), ticks=12)
    b = _run_script(_tmp(), ticks=12)
    assert a == b, (a, b)


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
