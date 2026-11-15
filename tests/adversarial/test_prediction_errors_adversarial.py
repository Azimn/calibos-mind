"""Critic round 1 — adversarial attacks on the prediction-error trace.

Spec: research/specs/2026-11-14-prediction-error-trace.md (domain 21).
Brief: spec + diff only. Every objection here is a test, not prose.

All fixtures live in /tmp under the conftest autouse guard — the live
store and live sidecars are never touched. CLI paths are exercised only
through the canonical conftest.redirect_all_cli_paths.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from digital_subject.cartridge import load_cartridge
from digital_subject.engine import clamp
from digital_subject.models import Event

from calibos_mind import cli
from calibos_mind import expectation as EXP
from calibos_mind.expectation import ExpectationTracker
from calibos_mind.prediction_errors import (
    PREDICTION_ERROR_CAP,
    PredictionErrorTracker,
)
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject


def _tmp():
    return Path(tempfile.mkdtemp(prefix="pe-adv-"))


def _make_subject(tmp: Path, prediction_errors: bool = True) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        interoception_path=str(tmp / "interoception.json"),
        expectation_path=str(tmp / "expectations.json"),
        prediction_errors_path=(
            str(tmp / "prediction_errors.json") if prediction_errors else None),
    )


def _ev(kind="observation", valence=0.0, intensity=0.5, tags=("t",),
       expected=None, actual=None, description="a synthetic event"):
    return Event(kind=kind, source="world", description=description,
                 tags=tags, intensity=intensity, valence=valence,
                 expected_valence=expected, actual_valence=actual)


def _process(subject: CalibosSubject, event: Event):
    eng = subject.engine
    owned = eng._own_event(event)
    eng._apply_event(owned)
    return owned


def _patched_cli(tmp: Path):
    """Redirect every cli path at tmp (canonical conftest helper)."""
    from conftest import redirect_all_cli_paths
    saved = redirect_all_cli_paths(tmp)
    saved_subject = cli._subject
    paths = {n: getattr(cli, n) for n in (
        "BASE", "DB", "INBOX", "SALIENCE", "INTEROCEPTION",
        "FAMILIARITY", "AMBIVALENCE", "COUNTERFACTUAL", "HABITS",
        "DORMANCY", "PROVENANCE", "PROPOSALS", "ARCHIVE", "EXPECTATIONS",
        "PREDICTION_ERRORS")}
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(paths["INBOX"])
        return CalibosSubject(
            str(paths["DB"]), cartridge, cognition=provider,
            salience_path=str(paths["SALIENCE"]),
            interoception_path=str(paths["INTEROCEPTION"]),
            expectation_path=str(paths["EXPECTATIONS"]),
            prediction_errors_path=str(paths["PREDICTION_ERRORS"]),
        )

    cli._subject = make_subject

    def restore():
        for n, v in saved.items():
            setattr(cli, n, v)
        cli._subject = saved_subject

    return restore, make_subject


# -- Attack 1: the recorded error must be the engine's own surprise --------

def test_recorded_error_equals_engine_surprise_in_normal_regime():
    """The trace claims to observe the engine's own surprise. In the
    normal regime (error <= 1, where clamp is the identity) the recorded
    error must equal owned.surprise exactly — a mismatch would mean the
    overlay records a quantity the engine never felt."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(20):
        _process(sub, _ev(valence=0.5))
    owned = _process(sub, _ev(valence=0.0))
    rec = json.loads((tmp / "prediction_errors.json").read_text())["records"][0]
    assert owned.surprise == clamp(abs(0.0 - 0.5))
    assert rec["error"] == owned.surprise == 0.5
    assert rec["expected"] == 0.5 and rec["actual"] == 0.0


def test_recorded_error_is_unclamped_per_spec():
    """Documents the boundary: the spec defines error = |actual - ema|
    unclamped, while the engine's surprise is clamp(|actual - ema|).
    A full swing (EMA -0.9 -> actual +0.9) records error 1.8 where the
    engine felt surprise 1.0. The trace DECISION is unaffected (clamp
    is monotone, threshold 0.45 < 1), but the stored error can exceed
    anything the engine ever reported. Spec-faithful; noted, not failed."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(20):
        _process(sub, _ev(valence=-0.9))
    owned = _process(sub, _ev(valence=0.9))
    rec = json.loads((tmp / "prediction_errors.json").read_text())["records"][0]
    assert rec["error"] == 1.8
    assert owned.surprise == 1.0


# -- Attack 2: a failed engine body must mint nothing ------------------------

def test_failed_engine_body_mints_nothing(monkeypatch):
    """Spec: the observation runs AFTER the engine body, so a failed
    event never traces. Force the pristine body to raise: the exception
    must propagate, no trace, no learn, no sidecar file."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(3):
        _process(sub, _ev(valence=0.8))

    def _boom(self, event):
        raise RuntimeError("pristine body failed")

    monkeypatch.setattr(EXP, "_ORIGINAL_OWN_EVENT", _boom)
    pe_before = (tmp / "prediction_errors.json").read_bytes()
    exp_before = (tmp / "expectations.json").read_bytes()
    with pytest.raises(RuntimeError):
        _process(sub, _ev(valence=0.0))
    # The failed event minted nothing: both sidecars byte-identical.
    assert (tmp / "prediction_errors.json").read_bytes() == pe_before
    assert (tmp / "expectations.json").read_bytes() == exp_before
    exp = json.loads((tmp / "expectations.json").read_text())
    assert exp["entries"]["observation|t"]["count"] == 3


# -- Attack 3: an observation failure must never break the tick --------------

def test_observe_failure_never_breaks_tick():
    """Counterfactual discipline: the observation is strictly
    subordinate to the tick. Break the sidecar write mid-stream: the
    event must still complete with the correct surprise, and learn()
    (which runs before observe) must still have landed."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(3):
        _process(sub, _ev(valence=0.8))
    before = (tmp / "prediction_errors.json").read_bytes()
    petracker = sub._prediction_error_tracker

    def _broken_save():
        raise OSError("disk gone")

    petracker.save = _broken_save
    owned = _process(sub, _ev(valence=0.0))
    assert owned.surprise == clamp(abs(0.0 - 0.8))
    # Learn ran (EMA moved 0.8 -> 0.56); observe failed silently.
    exp = json.loads((tmp / "expectations.json").read_text())
    assert exp["entries"]["observation|t"]["ema"] == pytest.approx(0.56)
    assert (tmp / "prediction_errors.json").read_bytes() == before


# -- Attack 4: the window rate must never silently undercount ----------------
# FAILING (round 1): report()'s docstring claims "The cap cannot hide a
# window trace: at most window traces can fall in the window and the cap
# keeps the newest 128, so every in-window trace is still present." That
# holds only for window <= 128. For a wider window the cap evicts
# in-window traces and the reported rate is diluted as if exact.

def test_report_window_beyond_cap_never_silently_undercounts():
    """200 prediction-carrying events, every one traced: the true rate
    over the last 200 is 1.0. Only the newest 128 records survive the
    cap, so report(window=200) today returns window_traces=128 and a
    64% rate — a silently wrong number. Either refuse the
    unrepresentable window or report an exact rate for the window
    actually covered; never dilute."""
    tmp = _tmp()
    tracker = PredictionErrorTracker(tmp / "pe.json")
    for i in range(200):
        tracker.observe(tick=i, key="k", expected=0.0, actual=0.9, error=0.9)
    assert len(tracker.records) == PREDICTION_ERROR_CAP
    try:
        rep = tracker.report(window=200)
    except ValueError:
        return  # refusing an unrepresentable window is honest
    assert rep["window_rate"] == pytest.approx(1.0)


# -- Attack 5: `mind predictions` via main() must touch nothing --------------

def test_predictions_via_main_leaves_everything_untouched(capsys):
    """Genome (read-only violations; CLI construction mtime): the
    report goes through the real main() dispatch, constructs no
    subject, and must leave the store bytes, the sidecar bytes, and
    the directory listing identical — proving no write path is
    touched, not just that the payload happens to match."""
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        for v in (0.8, -0.2, 0.5):
            _process(sub, _ev(valence=v))
        db_before = (tmp / "mind.db").read_bytes()
        pe_before = (tmp / "prediction_errors.json").read_bytes()
        listing_before = sorted(p.name for p in tmp.iterdir())
        assert cli.main(["predictions"]) == 0
        out = capsys.readouterr().out
        assert "prediction-carrying owned events: 2" in out
        assert (tmp / "mind.db").read_bytes() == db_before
        assert (tmp / "prediction_errors.json").read_bytes() == pe_before
        assert sorted(p.name for p in tmp.iterdir()) == listing_before
        # Fresh store through main(): zero report, nothing minted.
        assert cli.main(["init", "--force"]) == 0
        assert cli.main(["predictions"]) == 0
        capsys.readouterr()
        assert not (tmp / "prediction_errors.json").exists()
    finally:
        restore()


# -- Attack 6: --force must survive BOTH trackers corrupt --------------------

def test_init_force_wipes_both_corrupt_sidecars():
    """The wipe runs before construction, so a corrupt expectations
    sidecar must not brick the reseed either — both files go, and the
    fresh store starts cold on both trackers."""
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        for _ in range(4):
            _process(sub, _ev(valence=+0.8))
        (tmp / "prediction_errors.json").write_text("{not json")
        (tmp / "expectations.json").write_text("{also not json")
        assert cli.main(["init", "--force"]) == 0
        assert not (tmp / "prediction_errors.json").exists()
        assert not (tmp / "expectations.json").exists()
        sub2 = make_subject()
        _process(sub2, _ev(valence=+0.8))
        assert not (tmp / "prediction_errors.json").exists()
    finally:
        restore()


# -- Attack 7: suspension gating must block observe AND learn ----------------

def test_suspension_flag_blocks_observe_and_learn():
    """Belt-and-braces gating: with _expectation_suspended set, a
    prediction-carrying event must neither trace nor learn, and the
    engine must fall back to its pristine surprise."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(3):
        _process(sub, _ev(valence=+0.8))
    pe_before = (tmp / "prediction_errors.json").read_bytes()
    exp_before = (tmp / "expectations.json").read_bytes()
    sub.engine._expectation_suspended = True
    try:
        owned = _process(sub, _ev(valence=0.0))
    finally:
        sub.engine._expectation_suspended = False
    assert owned.surprise == clamp(0.5 * 0.35)
    assert (tmp / "prediction_errors.json").read_bytes() == pe_before
    assert (tmp / "expectations.json").read_bytes() == exp_before


# -- Attack 8: the recorded tick must be the event's tick ---------------------

def test_trace_tick_is_event_tick_after_time_advance():
    """state.tick does not move during _own_event (only _advance_time
    moves it), so the recorded tick must equal the ambient tick at
    own-time — after advancing the clock, not the pre-advance tick and
    not a later one."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(20):
        _process(sub, _ev(valence=0.5))
    sub.engine._advance_time(7)
    tick_at_own = sub.engine.state.tick
    _process(sub, _ev(valence=0.0))
    rec = json.loads((tmp / "prediction_errors.json").read_text())["records"][0]
    assert rec["tick"] == tick_at_own
    assert sub.engine.state.tick == tick_at_own


# -- Attack 9: calibration premise, reproduced at the tracker level ----------

def test_calibration_premise_reproduced_at_tracker_level():
    """Test-premise drift check: reproduce the calibration's mixed
    stream against ExpectationTracker directly (no overlay). The pin
    the builder's test asserts (0 traces at 0.45) must follow from the
    tracker math itself, not from the overlay swallowing events."""
    rng = random.Random(20261114)
    keys = [f"k{i}" for i in range(6)]
    walk = {k: rng.uniform(-0.5, 0.5) for k in keys}
    stream = []
    for _ in range(600):
        k = rng.choice(keys)
        walk[k] = max(-1.0, min(1.0, walk[k] + rng.gauss(0, 0.1)))
        stream.append((k, round(walk[k], 4)))
    tmp = _tmp()
    tracker = ExpectationTracker(tmp / "e.json")
    errors, traced, carrying = [], 0, 0
    for k, v in stream:
        ev = _ev(tags=(k,), valence=v)
        ema = tracker.predict(ev)
        if ema is not None:
            carrying += 1
            err = abs(v - ema)
            errors.append(err)
            if err >= 0.45:
                traced += 1
        tracker.learn(ev, 0)
    assert carrying == 594
    assert traced == 0
    assert max(errors) < 0.45
    assert sum(errors) / len(errors) == pytest.approx(0.109, abs=0.01)
