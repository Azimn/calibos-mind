"""Tests for the prediction-error trace (calibos_mind/prediction_errors.py).

Spec: research/specs/2026-11-14-prediction-error-trace.md (domain 21).
All fixtures live in /tmp — the live store, live sidecars, and inbox/
are never touched. CLI paths are exercised only through _patched_cli,
which routes through the canonical conftest.redirect_all_cli_paths.

Threshold note (measured 2026-11-14, see prediction_errors.py): the
fitness step's first error is 0.5 and its EMA-tail second error is
0.35, so THRESHOLD = 0.45 sits strictly between them and the step
traces exactly once. The mixed-stream pin below reproduces the
calibration stream exactly (6 keys, per-key random-walk valence,
sigma 0.1, 600 events, seed 20261114): the calibration measured 0
traces at 0.45, and the fitness clause requires <5%.
"""
from __future__ import annotations

import argparse
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
    THRESHOLD,
    PredictionErrorTracker,
)
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="pe-test-"))


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
    """Drive an event through own+apply (step's first half, no conduct)."""
    eng = subject.engine
    owned = eng._own_event(event)
    eng._apply_event(owned)
    return owned


def _own(subject: CalibosSubject, event: Event):
    """Drive an event through _own_event only (the hook lives there;
    _apply_event is downstream of it). Used for long streams."""
    return subject.engine._own_event(event)


def _sidecar(tmp: Path) -> Path:
    return tmp / "prediction_errors.json"


def _data(tmp: Path) -> dict:
    return json.loads(_sidecar(tmp).read_text(encoding="utf-8"))


def _patched_cli(tmp: Path):
    """Redirect every module-level path cmd_* touches at tmp.

    The complete set cmd_init --force touches is conftest's REDIRECTED
    — routed through the canonical conftest.redirect_all_cli_paths.
    make_subject wires the prediction-error tracker too, so the CLI
    commands under test observe the same sidecar the subject writes.
    """
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


# -- fitness 1: the valence step traces exactly once --------------------------

def test_step_traces_exactly_once_with_correct_record():
    """20 same-key events at 0.5, then 0.0: exactly one trace, with
    expected ~= 0.5, actual 0.0, error ~= 0.5, tick and key correct."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(20):
        _process(sub, _ev(valence=0.5))
    for _ in range(5):
        _process(sub, _ev(valence=0.0))

    data = _data(tmp)
    assert len(data["records"]) == 1
    rec = data["records"][0]
    assert rec["expected"] == 0.5
    assert rec["actual"] == 0.0
    assert rec["error"] == 0.5
    assert rec["tick"] == sub.engine.state.tick
    assert rec["key"] == "observation|t"
    # The trace is the first 0.0 event: the 20th prediction-carrying
    # event (19 predictions from the 0.5 run, then this one).
    assert rec["n"] == 20
    # Counter counts every prediction-carrying event, traced or not:
    # 19 from the 0.5 run + 5 from the 0.0 run.
    assert data["prediction_carrying_events"] == 24
    # The EMA tail after the step (0.35, 0.245, ...) stays below
    # THRESHOLD, so no second trace follows.
    assert THRESHOLD > 0.35


def test_step_tail_second_error_stays_below_threshold():
    """Pins the calibration: after the 0.5 step the EMA is 0.35, so the
    next error is exactly 0.35 — below THRESHOLD by construction."""
    tmp = _tmp()
    tracker = ExpectationTracker(tmp / "e.json")
    for _ in range(20):
        ev = _ev(valence=0.5)
        tracker.predict(ev)
        tracker.learn(ev, 0)
    ev = _ev(valence=0.0)
    assert tracker.predict(ev) == 0.5
    tracker.learn(ev, 1)
    assert tracker.predict(_ev(valence=0.0)) == pytest.approx(0.35)
    assert abs(0.0 - tracker.predict(_ev(valence=0.0))) < THRESHOLD


# -- fitness 2: unknown-key first event ----------------------------------------

def test_unknown_key_first_event_no_trace_but_learns():
    """First event of a key carries no prediction: no trace, no sidecar
    file — but the event still learns normally (EMA entry created)."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    _process(sub, _ev(tags=("new",), valence=0.8))
    assert not _sidecar(tmp).exists()
    exp = json.loads((tmp / "expectations.json").read_text(encoding="utf-8"))
    assert exp["entries"]["observation|new"]["ema"] == 0.8
    # Second event of the key carries a prediction (error 0): the
    # counter moves, still no trace, and the file now exists.
    _process(sub, _ev(tags=("new",), valence=0.8))
    data = _data(tmp)
    assert data["records"] == []
    assert data["prediction_carrying_events"] == 1


# -- fitness 3: explicit expected_valence never traces, never learns ----------

def test_explicit_expected_valence_no_trace_no_learn():
    """Even with an EMA entry for the key, an explicit expected_valence
    event traces nothing and leaves both sidecars byte-identical."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(3):
        _process(sub, _ev(valence=+0.8))
    exp_before = (tmp / "expectations.json").read_bytes()
    pe_before = _sidecar(tmp).read_bytes()
    owned = _process(sub, _ev(valence=+0.8, expected=0.2, actual=0.9))
    assert owned.surprise == clamp(abs(0.9 - 0.2))
    assert (tmp / "expectations.json").read_bytes() == exp_before
    assert _sidecar(tmp).read_bytes() == pe_before


def test_feeder_style_event_mints_no_sidecar():
    """Feeder's 0.0/0.0 events: no prediction, no learn, no sidecar at
    all — the machinery never observes the host's scheduled feedings."""
    from calibos_mind.feeder import event_for
    tmp = _tmp()
    sub = _make_subject(tmp)
    owned = _process(sub, event_for("food"))
    assert owned.surprise == 0.0
    assert not _sidecar(tmp).exists()
    assert not (tmp / "expectations.json").exists()


# -- fitness 4: small errors do not trace ---------------------------------------

def test_small_error_event_no_trace_but_counted():
    """Error 0.2 after a 0.5 history: below THRESHOLD, so no trace —
    but the event still counts as prediction-carrying."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(5):
        _process(sub, _ev(valence=0.5))
    _process(sub, _ev(valence=0.3))
    data = _data(tmp)
    assert data["records"] == []
    # 4 predictions from the 0.5 run + 1 for the 0.3 event.
    assert data["prediction_carrying_events"] == 5


# -- fitness 5: threshold calibration -------------------------------------------

def _mixed_stream(seed=20261114, sigma=0.1, n=600, nkeys=6):
    """The calibration mixed stream: 6 keys, per-key random-walk
    valence, start U(-0.5, 0.5), step N(0, sigma), clipped to [-1, 1]."""
    rng = random.Random(seed)
    keys = [f"k{i}" for i in range(nkeys)]
    walk = {k: rng.uniform(-0.5, 0.5) for k in keys}
    out = []
    for _ in range(n):
        k = rng.choice(keys)
        walk[k] = max(-1.0, min(1.0, walk[k] + rng.gauss(0, sigma)))
        out.append(("observation", (k,), round(walk[k], 4)))
    return out


def test_mixed_stream_trace_rate_below_five_percent():
    """Long mixed synthetic stream through the real overlay: trace rate
    <5% of prediction-carrying owned events. The calibration measured
    0 traces at THRESHOLD 0.45 on this exact stream; the pin below
    reproduces that measurement."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for kind, tags, valence in _mixed_stream():
        _own(sub, _ev(kind=kind, tags=tags, valence=valence))
    data = _data(tmp)
    total = data["prediction_carrying_events"]
    assert total == 594  # 600 events minus 6 first-per-key events
    assert len(data["records"]) == 0  # calibration reproduction
    assert len(data["records"]) / total < 0.05


def test_constant_stream_traces_zero():
    """Constant valence: the EMA converges exactly, every error is 0.0."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(100):
        _own(sub, _ev(valence=0.3))
    data = _data(tmp)
    assert data["records"] == []
    assert data["prediction_carrying_events"] == 99


# -- fitness 6: mind predictions report ------------------------------------------

def test_predictions_report_counts_and_traces(capsys):
    """Seeded sidecar: correct counts, window rate, recent + largest
    traces, through the real CLI entry point."""
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        for _ in range(20):
            _process(sub, _ev(valence=0.5))
        for _ in range(5):
            _process(sub, _ev(valence=0.0))
        assert cli.cmd_predictions(argparse.Namespace()) == 0
        out = capsys.readouterr().out
        assert "prediction-carrying owned events: 24" in out
        assert "suprathreshold traces: 1" in out
        # 1 trace in a 24-event window: 4.2%.
        assert "trace rate (last 24 prediction-carrying events): 4.2%" in out
        assert "observation|t: expected 0.50 -> actual 0.00 (error 0.50)" in out
    finally:
        restore()


def test_predictions_report_on_fresh_store_mints_no_sidecar(capsys):
    """Fresh store: reports zero, rate undefined (never a quiet zero),
    and mints no sidecar file."""
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        make_subject()
        assert cli.cmd_predictions(argparse.Namespace()) == 0
        out = capsys.readouterr().out
        assert "prediction-carrying owned events: 0" in out
        assert "suprathreshold traces: 0" in out
        assert "trace rate: undefined" in out
        assert not (tmp / "prediction_errors.json").exists()
    finally:
        restore()


def test_predictions_report_window_rate_and_largest():
    """report(): the last-100 window counts only in-window traces, and
    largest is sorted by error descending. Seeded directly through the
    tracker so the window math is pinned without a 150-event drive."""
    tmp = _tmp()
    tracker = PredictionErrorTracker(tmp / "pe.json")
    # 150 prediction-carrying events; traces at n = 10, 50, 120, 149, 150.
    for i in range(1, 151):
        error = {10: 0.5, 50: 0.9, 120: 0.6, 149: 0.45, 150: 0.7}.get(i, 0.0)
        tracker.observe(tick=i, key="k", expected=0.0, actual=error, error=error)
    rep = tracker.report()
    assert rep["prediction_carrying_events"] == 150
    assert rep["traces"] == 5
    assert rep["window"] == 100
    # In-window traces: n > 50, i.e. 120, 149, 150.
    assert rep["window_traces"] == 3
    assert rep["window_rate"] == pytest.approx(0.03)
    assert [r["n"] for r in rep["recent"]] == [10, 50, 120, 149, 150]
    assert [r["error"] for r in rep["largest"]] == [0.9, 0.7, 0.6, 0.5, 0.45]
    # A fresh tracker reports the rate as undefined, never a quiet zero.
    fresh = PredictionErrorTracker(tmp / "none.json")
    assert fresh.report()["window_rate"] is None


# -- fitness 7: read-only + dream paths never write -------------------------------

def test_readonly_cli_commands_leave_sidecar_byte_identical(capsys):
    """drift, status, review, inbox, predictions through the real CLI
    entry points (paths redirected): the sidecar stays byte-identical."""
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        for v in (0.8, -0.2, 0.5):
            _process(sub, _ev(valence=v))
        sidecar = tmp / "prediction_errors.json"
        before = sidecar.read_bytes()

        cli.cmd_drift(argparse.Namespace(window=50))
        assert cli.cmd_status(argparse.Namespace(raw=True)) == 0
        assert cli.cmd_review(argparse.Namespace(n=5)) == 0
        assert cli.cmd_inbox(argparse.Namespace()) == 0
        assert cli.cmd_predictions(argparse.Namespace()) == 0
        capsys.readouterr()

        assert sidecar.read_bytes() == before
    finally:
        restore()


def test_readonly_paths_never_create_sidecar(capsys):
    """A fresh store's read-only sweep must not conjure the file."""
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        cli.cmd_drift(argparse.Namespace(window=50))
        assert cli.cmd_status(argparse.Namespace(raw=True)) == 0
        assert cli.cmd_review(argparse.Namespace(n=5)) == 0
        assert cli.cmd_inbox(argparse.Namespace()) == 0
        assert cli.cmd_predictions(argparse.Namespace()) == 0
        capsys.readouterr()
        assert not (tmp / "prediction_errors.json").exists()
        sub.inspect()
        assert not (tmp / "prediction_errors.json").exists()
    finally:
        restore()


def test_dream_ticks_leave_sidecar_byte_identical():
    """Dream ticks process no events; the sidecar (and the suspension
    flag path) must leave it byte-identical."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for v in (0.8, -0.2, 0.5):
        _process(sub, _ev(valence=v))
    sidecar = _sidecar(tmp)
    before = sidecar.read_bytes()
    for _ in range(3):
        sub.dream_tick()
    assert sidecar.read_bytes() == before
    # The suspension flag is always restored, even mid-dream.
    assert getattr(sub.engine, "_expectation_suspended", False) is False


# -- fitness 8: init --force wipes the sidecar --------------------------------------

def test_init_force_wipes_sidecar_and_reseed_starts_cold():
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        for _ in range(20):
            _process(sub, _ev(valence=0.5))
        _process(sub, _ev(valence=0.0))
        assert (tmp / "prediction_errors.json").exists()

        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        assert not (tmp / "prediction_errors.json").exists()

        # Post-reseed, the first event of the key is cold again.
        sub2 = make_subject()
        owned = _process(sub2, _ev(valence=+0.8, intensity=0.5))
        assert owned.surprise == clamp(0.5 * 0.35)
        assert not (tmp / "prediction_errors.json").exists()
    finally:
        restore()


def test_init_force_wipes_corrupt_sidecar():
    """A corrupt sidecar must not brick --force: the wipe runs before
    construction, so the reseed succeeds and the file is gone."""
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        for _ in range(4):
            _process(sub, _ev(valence=+0.8))
        (tmp / "prediction_errors.json").write_text("{not json", encoding="utf-8")
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        assert not (tmp / "prediction_errors.json").exists()
    finally:
        restore()


def test_corrupt_sidecar_fails_loud_before_tick_advances():
    """Constructing a subject against a corrupt sidecar raises
    ValueError before any tick runs, and leaves the bytes untouched."""
    tmp = _tmp()
    p = tmp / "prediction_errors.json"
    p.write_text("{not json", encoding="utf-8")
    before = p.read_bytes()
    with pytest.raises(ValueError):
        _make_subject(tmp)
    assert p.read_bytes() == before
    # Malformed shapes fail loud too — a missing section is malformed,
    # never a quiet zero (genome: silent zero on missing sections).
    for bad in (
        json.dumps({"records": []}),
        json.dumps({"records": [], "prediction_carrying_events": -1}),
        json.dumps({"records": [{"tick": 1}]}),
        json.dumps({"records": "nope",
                    "prediction_carrying_events": 0}),
    ):
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError):
            PredictionErrorTracker(p)
        assert p.read_bytes() == bad.encode("utf-8")


# -- fitness 9: replay determinism --------------------------------------------------

def _script(sub: CalibosSubject):
    seq = [
        ("observation", ("a",), 0.8, 0.5),
        ("observation", ("b", "a"), -0.4, 0.7),
        ("note", ("a",), 0.1, 0.3),
        ("observation", ("a",), 0.6, 0.5),
        ("note", ("c",), -0.9, 0.9),
        ("observation", ("b", "a"), 0.2, 0.4),
        ("observation", ("a",), -0.9, 0.5),
        ("observation", ("a",), 0.8, 0.5, 0.2, 0.9),  # explicit: no trace
    ]
    for row in seq:
        kind, tags, valence, intensity = row[:4]
        expected = row[4] if len(row) > 4 else None
        actual = row[5] if len(row) > 5 else None
        _process(sub, _ev(kind=kind, tags=tags, valence=valence,
                          intensity=intensity, expected=expected,
                          actual=actual))


def test_identical_sequences_replay_byte_identical():
    tmp_a, tmp_b = _tmp(), _tmp()
    _script(_make_subject(tmp_a))
    _script(_make_subject(tmp_b))
    assert _sidecar(tmp_a).read_bytes() == _sidecar(tmp_b).read_bytes()
    # The script contains a real suprathreshold step (0.6 -> -0.9 on
    # key observation|a), so the replay is not trivially empty.
    assert len(_data(tmp_a)["records"]) >= 1


def test_save_load_round_trip_identical():
    tmp = _tmp()
    tracker = PredictionErrorTracker(tmp / "pe.json")
    tracker.observe(tick=3, key="a|x", expected=0.5, actual=0.0, error=0.5)
    tracker.observe(tick=4, key="a|x", expected=0.35, actual=0.0, error=0.35)
    before = (tmp / "pe.json").read_bytes()
    loaded = PredictionErrorTracker(tmp / "pe.json")
    assert loaded.records == tracker.records
    assert (loaded.prediction_carrying_events
            == tracker.prediction_carrying_events == 2)
    loaded.save()
    assert (tmp / "pe.json").read_bytes() == before


# -- fitness 10: no tracker means the pristine path -----------------------------------

def test_no_tracker_means_pristine_path():
    """An engine with no prediction-error tracker attached (path None)
    takes the expectation path untouched: surprise still learns, and
    no sidecar file is created."""
    tmp = _tmp()
    sub = _make_subject(tmp, prediction_errors=False)
    assert getattr(sub.engine, "_prediction_error_tracker", None) is None
    for _ in range(3):
        _process(sub, _ev(valence=+0.8))
    owned = _process(sub, _ev(valence=+0.4))
    assert owned.event.expected_valence == 0.8
    assert not _sidecar(tmp).exists()


# -- tracker unit behavior ----------------------------------------------------------

def test_observe_threshold_boundary():
    """error == THRESHOLD traces; just below does not. The boundary is
    inclusive by the spec's `error >= THRESHOLD`."""
    tmp = _tmp()
    tracker = PredictionErrorTracker(tmp / "pe.json")
    assert tracker.observe(tick=1, key="k", expected=0.0, actual=0.45,
                           error=0.45) is True
    assert tracker.observe(tick=2, key="k", expected=0.0, actual=0.4499,
                           error=0.4499) is False
    assert len(tracker.records) == 1
    assert tracker.prediction_carrying_events == 2


def test_observe_rejects_non_finite_and_bad_types():
    tmp = _tmp()
    tracker = PredictionErrorTracker(tmp / "pe.json")
    with pytest.raises(ValueError):
        tracker.observe(tick=1, key="k", expected=0.0, actual=0.0,
                        error=float("nan"))
    with pytest.raises(ValueError):
        tracker.observe(tick=1, key="k", expected=0.0, actual=0.0,
                        error=float("inf"))
    with pytest.raises(ValueError):
        tracker.observe(tick="1", key="k", expected=0.0, actual=0.0,
                        error=0.5)
    assert not (tmp / "pe.json").exists()


def test_sidecar_caps_at_128_keeping_newest():
    tmp = _tmp()
    tracker = PredictionErrorTracker(tmp / "pe.json")
    for i in range(200):
        tracker.observe(tick=i, key="k", expected=0.0, actual=0.9, error=0.9)
    assert len(tracker.records) == PREDICTION_ERROR_CAP
    assert tracker.records[0]["n"] == 73  # traces 1..72 evicted
    assert tracker.records[-1]["n"] == 200
    assert tracker.prediction_carrying_events == 200


def test_reset_clears_records_and_counter():
    tmp = _tmp()
    tracker = PredictionErrorTracker(tmp / "pe.json")
    tracker.observe(tick=1, key="k", expected=0.0, actual=0.9, error=0.9)
    tracker.reset()
    assert tracker.records == []
    assert tracker.prediction_carrying_events == 0
    assert json.loads((tmp / "pe.json").read_text(encoding="utf-8")) == {
        "records": [], "prediction_carrying_events": 0}


def test_constructor_never_mints_sidecar():
    tmp = _tmp()
    PredictionErrorTracker(tmp / "pe.json")
    assert not (tmp / "pe.json").exists()


def test_uninstall_restores_pristine_overlay():
    """Overlay uninstall (the expectation module's own escape hatch)
    removes the prediction-error hook with it: the event runs the
    pristine path (no prediction fed, no learn, no trace) and the
    sidecar stays byte-identical."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(3):
        _process(sub, _ev(valence=+0.8))
    before = _sidecar(tmp).read_bytes()
    EXP.uninstall()
    try:
        owned = _process(sub, _ev(valence=0.0))
    finally:
        EXP.install()
    assert owned.surprise == clamp(0.5 * 0.35)
    assert _sidecar(tmp).read_bytes() == before
