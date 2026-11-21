"""Tests for the realization follow-through tracker (Domain 22,
internal causality).

Spec: research/specs/2026-11-15-realization-followthrough.md
All fixtures on /tmp — the live store is never touched.

The mechanism: calibos_mind/followthrough.py — a FollowthroughTracker
owning the followthrough.json sidecar (a capped, append-only log of
action-bearing waking ticks, [[tick, action], ...]), fed ONLY from
cli._run_tick after the tick's action is chosen, plus a `mind
followthrough` report that scans the store for realization records
with the canonical interoception.parse_realization_text parser and
compares the action-share distribution in the 20 action-bearing ticks
before each realization's swing tick with the 20 after its
convergence tick, as total variation distance.

Fitness functions under test:
  1. Scripted store: 20 pre-swing ticks all `seek_contact`, 20
     post-conv ticks all `approach` -> tv exactly 1.0, correct mixes.
  2. Identical before/after mixes -> tv exactly 0.0.
  3. Mixed known distributions -> tv matches the hand-computed value.
  4. Two realizations -> two rows, each with its own windows
     (no cross-talk).
  5. Realization whose swing predates the action log -> row reports
     insufficient-window, aggregate excludes it and names the count;
     no fabricated 0.0.
  6. Read-only sweep (drift, status, review, inbox, followthrough,
     predictions, goals) leaves the sidecar byte-identical and mints
     no sidecar on a fresh store.
  7. Dream ticks never append to the action log and never write the
     sidecar.
  8. `init --force` wipes the sidecar, including a corrupt one.
  9. Corrupt sidecar shapes (truncated JSON, wrong types, row not
     [tick, action], negative tick, tick out of order) fail loud
     before the tick advances; no silent load.
 10. Determinism: identical tick/action sequences -> byte-identical
     sidecar; save/load round-trip identical.
 11. Log cap: 256 rows, oldest dropped.
 12. Full suite green (run separately).

Scenario control (mirrors test_personality.py): the tests pin
engine.state.needs / pressures / pressure_baselines directly (concerns
cleared), commit through a transaction, then run cli._run_tick — the
real wiring under test. Measured premise (probed 2026-10-02 against the
frozen install): curiosity 0.80 dominant, pressures low -> action
"explore", deterministic across runs.
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
    LOG_CAP,
    WINDOW,
    FollowthroughTracker,
    report,
    total_variation,
)
from calibos_mind.interoception import mint_realization


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="followthrough-test-"))


def _sidecar_data(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_sidecar(path: Path, rows: list) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


def _needs(**over):
    """Full need dict; override the dominant need per tick."""
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
    """Pin the engine's motive state; concerns cleared for determinism."""
    st = subject.engine.state
    st.needs = dict(needs)
    st.pressures = dict(pressures)
    st.pressure_baselines = dict(baselines)
    st.concerns = {}


def _script_tick(subject, needs, pressures, baselines):
    """One scripted waking tick through the real cli._run_tick wiring.

    The staged state is committed through a transaction first — a bare
    in-memory stage would be discarded by the heartbeat's _restore.
    """
    with subject._transaction():
        _set_state(subject, needs, pressures, baselines)
    return cli._run_tick(subject)


def _init_store():
    """Fresh synthetic store via the CLI (paths already redirected by
    the conftest autouse fixture). Returns the subject."""
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _mint(subject, need, swing, conv):
    """One realization record via the canonical mint path."""
    event = {"need": need, "swing_tick": swing, "swing_felt": 0.9,
             "swing_actual": 0.5, "gap_max": 0.4, "conv_tick": conv}
    with subject._transaction():
        return mint_realization(subject, event)


def _add_record(subject, source, text, **kw):
    """One plain record via the subject's normal record-append path."""
    with subject._transaction():
        return subject._add(source, text, generated_by="authored",
                            available_to_cognition=True,
                            salience=0.5, intensity=0.5, **kw)


def _followthrough_output():
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_followthrough(argparse.Namespace())
    return buf.getvalue()


def _fill(start, end, action):
    """[[tick, action], ...] for ticks start..end inclusive."""
    return [[t, action] for t in range(start, end + 1)]


# -- fitness 1: full conduct change -> tv exactly 1.0 ------------------------

def test_tv_exactly_one_on_full_conduct_change():
    """20 pre-swing ticks all seek_contact, 20 post-conv ticks all
    approach -> tv exactly 1.0, with correct before/after mixes."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    rows = (_fill(1, 20, "seek_contact") + _fill(21, 40, "seek_contact")
            + _fill(41, 60, "approach"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    assert rep["realizations"] == 1
    assert rep["measurable"] == 1 and rep["insufficient"] == 0
    assert rep["observed_ticks"] == 60
    row = rep["rows"][0]
    assert row["need"] == "comfort"
    assert row["swing_tick"] == 21 and row["conv_tick"] == 40
    assert row["status"] == "measured"
    assert row["tv"] == 1.0, row
    assert row["before_mix"] == {"seek_contact": 1.0}, row
    assert row["after_mix"] == {"approach": 1.0}, row
    assert rep["mean_tv"] == 1.0
    # The ticks between swing and convergence are in neither window:
    # the before window is ticks 1-20, the after window 41-60.
    assert row["before_count"] == 20 and row["after_count"] == 20


# -- fitness 2: identical mixes -> tv exactly 0.0 -----------------------------

def test_tv_zero_on_identical_mixes():
    """Before and after mixes identical -> tv exactly 0.0."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    rows = (_fill(1, 10, "approach") + _fill(11, 20, "seek_contact")
            + _fill(21, 40, "explore")
            + _fill(41, 50, "approach") + _fill(51, 60, "seek_contact"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    row = rep["rows"][0]
    assert row["status"] == "measured"
    assert row["tv"] == 0.0, row
    assert row["before_mix"] == {"approach": 0.5, "seek_contact": 0.5}
    assert row["after_mix"] == {"approach": 0.5, "seek_contact": 0.5}
    assert rep["mean_tv"] == 0.0


# -- fitness 3: mixed distributions match hand computation ---------------------

def test_tv_mixed_matches_hand_computed():
    """Before {approach .5, seek_contact .5}, after {approach .75,
    seek_contact .25} -> tv = (|0.5-0.75| + |0.5-0.25|) / 2 = 0.25."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    rows = (_fill(1, 10, "approach") + _fill(11, 20, "seek_contact")
            + _fill(21, 40, "explore")
            + _fill(41, 55, "approach") + _fill(56, 60, "seek_contact"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    row = rep["rows"][0]
    assert row["tv"] == pytest.approx(0.25), row
    assert row["before_mix"] == {"approach": 0.5, "seek_contact": 0.5}
    assert row["after_mix"] == {"approach": 0.75, "seek_contact": 0.25}
    # Pure-math checks on the distance itself, including the bounds.
    assert total_variation({"a": 1.0}, {"b": 1.0}) == 1.0
    assert total_variation({"a": 1.0}, {"a": 1.0}) == 0.0
    assert total_variation({"a": 0.5, "b": 0.5}, {"a": 0.75, "b": 0.25}) \
        == pytest.approx(0.25)
    assert total_variation({}, {}) == 0.0
    assert 0.0 <= total_variation({"a": 0.3}, {"b": 0.7, "c": 0.3}) <= 1.0


# -- fitness 4: two realizations, independent windows ---------------------------

def test_two_realizations_have_independent_windows():
    """Two realizations -> two rows, each with its own windows (no
    cross-talk): R1 flips seek_contact -> approach (tv 1.0), R2 stays
    approach -> approach (tv 0.0), mean 0.5."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    _mint(subject, "loneliness", swing=61, conv=80)
    rows = (_fill(1, 20, "seek_contact") + _fill(21, 40, "seek_contact")
            + _fill(41, 60, "approach") + _fill(61, 80, "approach")
            + _fill(81, 100, "approach"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    assert rep["realizations"] == 2
    assert rep["measurable"] == 2 and rep["insufficient"] == 0
    r1, r2 = rep["rows"]
    assert (r1["need"], r1["swing_tick"], r1["conv_tick"]) == \
        ("comfort", 21, 40)
    assert r1["tv"] == 1.0, r1
    assert (r2["need"], r2["swing_tick"], r2["conv_tick"]) == \
        ("loneliness", 61, 80)
    assert r2["tv"] == 0.0, r2
    assert r2["before_mix"] == {"approach": 1.0}
    assert r2["after_mix"] == {"approach": 1.0}
    assert rep["mean_tv"] == pytest.approx(0.5)


# -- fitness 5: insufficient window — never a quiet 0.0 -------------------------

def test_insufficient_window_when_swing_predates_log():
    """A realization whose swing predates the action log reports
    insufficient-window; the aggregate excludes it and names the
    count; no fabricated 0.0 anywhere."""
    subject = _init_store()
    _mint(subject, "comfort", swing=30, conv=60)
    _mint(subject, "loneliness", swing=90, conv=110)
    # Log starts at tick 50: nothing before swing 30 exists.
    rows = _fill(50, 130, "approach")
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    assert rep["realizations"] == 2
    r1, r2 = rep["rows"]
    assert r1["status"] == "insufficient-window", r1
    assert r1["tv"] is None and r1["before_mix"] is None \
        and r1["after_mix"] is None, r1
    assert r1["before_count"] == 0, r1
    assert r1["after_count"] == 20, r1  # ticks 61-80 are present
    assert r2["status"] == "measured", r2
    assert r2["tv"] == 0.0, r2
    assert rep["measurable"] == 1 and rep["insufficient"] == 1
    # The mean is over measurable rows only — the insufficient row
    # contributes no 0.0 to it.
    assert rep["mean_tv"] == 0.0
    # Short after-window is insufficient too: conv 125 with the log
    # ending at 130 leaves only 5 post-conv ticks.
    _mint(subject, "hunger", swing=90, conv=125)
    rep2 = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    r3 = [r for r in rep2["rows"] if r["need"] == "hunger"][0]
    assert r3["status"] == "insufficient-window", r3
    assert r3["before_count"] == 20 and r3["after_count"] == 5, r3
    assert rep2["insufficient"] == 2 and rep2["measurable"] == 1


def test_all_insufficient_means_undefined_not_zero():
    """Zero measurable realizations -> mean_tv is None (undefined),
    never a quiet 0.0."""
    subject = _init_store()
    _mint(subject, "comfort", swing=30, conv=60)
    _write_sidecar(cli.FOLLOWTHROUGH, _fill(50, 80, "approach"))
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    assert rep["measurable"] == 0 and rep["insufficient"] == 1
    assert rep["mean_tv"] is None, rep


def test_non_realization_records_are_ignored():
    """Plain records and near-miss realization-like text never mint a
    row: only the canonical parser decides."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    _add_record(subject, "memory", "I like the garden in the morning.")
    # Near-miss: canonical text with the final period swapped for "!".
    _add_record(subject, "temporal",
                "At tick 5 I felt comfort as settled, but my body was "
                "only settled; by tick 9 the feeling caught up!")
    _add_record(subject, "temporal",
                "At tick 5 I felt comfort as settled")
    rows = (_fill(1, 20, "seek_contact") + _fill(21, 40, "seek_contact")
            + _fill(41, 60, "approach"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    rep = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    assert rep["realizations"] == 1, rep
    assert rep["rows"][0]["need"] == "comfort"


# -- corrupt sidecar fails loud; no-action ticks record nothing ------------------

def test_corrupt_sidecar_raises_loud():
    """Invalid JSON, non-list top level, malformed rows, negative
    ticks, and out-of-order ticks all raise ValueError at
    construction — never a silent reset."""
    tmp = _tmp()
    bad = {
        "bad-json": "{not json",
        "truncated-json": '[[1, "approach"], [2,',
        "non-list": '{"rows": []}',
        "bad-row-len": json.dumps([[1]]),
        "bad-row-type": json.dumps([["x", "a"]]),
        "bad-row-tick": json.dumps([["x", "a"]]),
        "bool-tick": json.dumps([[True, "a"]]),
        "negative-tick": json.dumps([[-1, "a"]]),
        "bad-row-action": json.dumps([[1, 7]]),
        "row-not-list": json.dumps([{"tick": 1, "action": "a"}]),
        "out-of-order": json.dumps([[2, "a"], [1, "b"]]),
        "duplicate-tick": json.dumps([[1, "a"], [1, "b"]]),
    }
    for name, text in bad.items():
        path = tmp / f"{name}.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt followthrough"):
            FollowthroughTracker(path)


def test_observe_ignores_ticks_with_no_action():
    """A tick with no chosen action records nothing (spec: ticks with
    no chosen action record nothing)."""
    tmp = _tmp()
    tracker = FollowthroughTracker(tmp / "followthrough.json")
    assert tracker.observe(1, None) is False
    assert tracker.observe(2, "") is False
    assert tracker.observe(3, "approach") is True
    assert tracker.rows == [[3, "approach"]]


def test_log_cap_holds_256_oldest_dropped():
    """After 256+ action-bearing ticks the log holds exactly 256
    entries, oldest dropped."""
    tmp = _tmp()
    tracker = FollowthroughTracker(tmp / "followthrough.json")
    for i in range(300):
        assert tracker.observe(i + 1, "explore")
    assert len(tracker.rows) == LOG_CAP == 256
    assert tracker.rows[0] == [45, "explore"], tracker.rows[0]
    assert tracker.rows[-1] == [300, "explore"]
    tracker.save()
    reloaded = FollowthroughTracker(tmp / "followthrough.json")
    assert len(reloaded.rows) == 256
    assert reloaded.rows[0] == [45, "explore"]


def test_save_load_roundtrip_deterministic():
    """Save twice -> identical bytes; reload -> identical data."""
    tmp = _tmp()
    path = tmp / "followthrough.json"
    tracker = FollowthroughTracker(path)
    for i in range(30):
        tracker.observe(i + 1, "approach" if i % 3 else "seek_contact")
    tracker.save()
    first = path.read_bytes()
    tracker.save()
    assert path.read_bytes() == first
    reloaded = FollowthroughTracker(path)
    assert reloaded.rows == tracker.rows
    reloaded.save()
    assert path.read_bytes() == first


# -- fitness 6: read-only sweep ---------------------------------------------------

def test_readonly_commands_leave_sidecar_byte_identical():
    """drift, status, review, inbox, followthrough, predictions, and
    goals never write the sidecar."""
    subject = _init_store()
    sidecar = cli.FOLLOWTHROUGH
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(), _baselines())
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


def test_followthrough_mints_no_sidecar_on_fresh_store():
    """Fresh store: the report prints 'no realization records yet'
    and the sidecar is still absent afterward."""
    _init_store()
    assert not cli.FOLLOWTHROUGH.exists()
    out = _followthrough_output()
    assert "no realization records yet" in out, out
    assert "followthrough (tick 0, 0 observed action-bearing ticks, " \
        "0 realization(s)) — read-only" in out, out
    assert not cli.FOLLOWTHROUGH.exists()


# -- fitness 7: dream ticks never append ------------------------------------------

def test_dream_tick_never_appends():
    """A dream tick leaves the action log byte-identical."""
    subject = _init_store()
    sidecar = cli.FOLLOWTHROUGH
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    assert sidecar.exists()
    before = sidecar.read_bytes()
    subject.dream_tick()
    assert sidecar.read_bytes() == before, \
        "dream tick wrote the sidecar"


# -- fitness 8: init --force ------------------------------------------------------

def test_init_force_wipes_sidecar_and_fresh_store_starts_empty():
    """init --force wipes the sidecar; a fresh store starts empty."""
    subject = _init_store()
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    assert cli.FOLLOWTHROUGH.exists()
    cli.cmd_init(argparse.Namespace(force=True))
    assert not cli.FOLLOWTHROUGH.exists(), \
        "init --force must wipe the followthrough sidecar (genome: reseed reset)"
    out = _followthrough_output()
    assert "no realization records yet" in out, out


def test_init_force_wipes_corrupt_sidecar():
    """A corrupt sidecar must not brick --force: the wipe runs before
    any tracker construction."""
    _init_store()
    cli.FOLLOWTHROUGH.write_text("{not json", encoding="utf-8")
    assert cli.cmd_init(argparse.Namespace(force=True)) == 0
    assert not cli.FOLLOWTHROUGH.exists()


# -- wiring: the tick feed records the engine's chosen action ----------------------

def test_wiring_records_engine_action_per_tick():
    """Scripted ticks through cli._run_tick append one row per
    action-bearing tick with the engine's chosen action."""
    subject = _init_store()
    actions = []
    for _ in range(5):
        actions.append(_script_tick(
            subject, _needs(curiosity=0.80), _pressures(), _baselines())["action"])
    assert set(actions) == {"explore"}, actions
    data = _sidecar_data(cli.FOLLOWTHROUGH)
    assert len(data["log"]) == 5
    assert [r[1] for r in data["log"]] == ["explore"] * 5
    assert [r[0] for r in data["log"]] == [1, 2, 3, 4, 5], data
    assert data["surfaced"] == []


# -- fitness 10: determinism ---------------------------------------------------------

def _run_script(root: Path, ticks: int = 12) -> bytes:
    """Run an identical scripted tick sequence on a synthetic tree at
    `root` through the canonical redirect; return the sidecar bytes."""
    from conftest import redirect_all_cli_paths
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


def test_identical_scripts_yield_byte_identical_sidecar():
    """Identical scripted tick sequences -> byte-identical sidecar."""
    a = _run_script(_tmp(), ticks=12)
    b = _run_script(_tmp(), ticks=12)
    assert a == b, (a, b)
    data = json.loads(a)
    assert len(data["log"]) == 12
    assert [r[0] for r in data["log"]] == list(range(1, 13))
    assert data["surfaced"] == []


# -- CLI report output ----------------------------------------------------------------

def test_cli_report_shows_tv_and_mixes():
    """The `mind followthrough` report prints the mean, and per row
    the tv distance with the before/after mixes."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    rows = (_fill(1, 20, "seek_contact") + _fill(21, 40, "seek_contact")
            + _fill(41, 60, "approach"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    out = _followthrough_output()
    assert "followthrough (tick 0, 60 observed action-bearing ticks, " \
        "1 realization(s)) — read-only" in out, out
    assert "mean tv distance: 1.00 over 1 measurable " \
        "(0 insufficient-window)" in out, out
    assert "comfort swing 21 conv 40" in out, out
    assert "tv 1.00" in out, out
    assert "before {seek_contact: 1.00}" in out, out
    assert "after {approach: 1.00}" in out, out


def test_cli_report_shows_insufficient_window_row():
    """An insufficient-window row prints its counts, never a tv."""
    subject = _init_store()
    _mint(subject, "comfort", swing=30, conv=60)
    _write_sidecar(cli.FOLLOWTHROUGH, _fill(50, 80, "approach"))
    out = _followthrough_output()
    assert "mean tv distance: undefined — no measurable realization " \
        "(1 insufficient-window)" in out, out
    assert "insufficient window (before 0/20, after 20/20)" in out, out
    row_line = [l for l in out.splitlines()
                if "insufficient window" in l][0]
    assert "tv " not in row_line, row_line


def test_report_is_repeatable_and_sidecar_stable():
    """Calling report() twice returns equal dicts and leaves the
    sidecar byte-identical (pure read)."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    rows = (_fill(1, 20, "seek_contact") + _fill(21, 40, "seek_contact")
            + _fill(41, 60, "approach"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    before = cli.FOLLOWTHROUGH.read_bytes()
    rep1 = report(subject, tracker)
    rep2 = report(subject, tracker)
    assert rep1 == rep2
    assert cli.FOLLOWTHROUGH.read_bytes() == before


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
