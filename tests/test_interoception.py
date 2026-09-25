"""Tests for the interoceptive gap (calibos_mind/interoception.py).

The felt body chases the actual body with asymmetric lag + seeded noise;
views re-render body interoception from FELT urgency; `mind status` shows
felt bands by default (exact floats under --raw).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_interoception.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.interoception import (
    BASELINE,
    InteroceptionTracker,
    _noise,
    felt_level,
    felt_text,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from calibos_mind.workspace import CalibosWorkspace
from digital_subject.cartridge import load_cartridge


def _tracker(tmp: Path, name: str = "interoception.json", **kw) -> InteroceptionTracker:
    return InteroceptionTracker(tmp / name, **kw)


def _needs(**over):
    base = {"hunger": 0.5, "thirst": 0.5, "fatigue": 0.5, "energy": 0.5}
    base.update(over)
    return base


# -- update rule: lag then tracking ---------------------------------------

def test_shock_lag_then_tracking():
    """Fitness 1: a stepped need leaves a real felt/actual gap (>=3 ticks),
    and felt converges once actual stabilizes (no free-floating)."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    tr = _tracker(tmp)
    tick = 100
    # Stabilize felt at hunger 0.2.
    for _ in range(30):
        tick += 1
        tr.update(_needs(hunger=0.2), tick)
    tr.save()
    felt_before = tr.data["needs"]["hunger"]["felt"]
    assert abs(felt_before - 0.2) < 0.03, felt_before
    # Step the actual to 0.9. The gap is measured the way the thinker sees
    # it: during tick t the view carries felt from t-1 (post-tick update).
    gaps = []
    for _ in range(6):
        tick += 1
        felt_seen = tr.data["needs"]["hunger"]["felt"]
        gaps.append(abs(felt_seen - 0.9))
        tr.update(_needs(hunger=0.9), tick)
    assert sum(1 for g in gaps if g > 0.25) >= 3, gaps
    # Let actual stabilize; felt must track it (free-floating is the failure).
    for _ in range(25):
        tick += 1
        tr.update(_needs(hunger=0.9), tick)
    felt_after = tr.data["needs"]["hunger"]["felt"]
    assert abs(felt_after - 0.9) < 0.03, felt_after


def test_offset_lags_longer_than_onset():
    """Asymmetry: a fading signal (offset) closes the gap slower than a
    rising one (onset). Stabilization points sit clear of the 0.5 baseline
    so the direction classification is unambiguous (near-baseline jitter
    would flip it)."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    # Onset: 0.55 -> 0.95 (displaced +0.05, moving further -> onset).
    tr = _tracker(tmp, "on.json")
    tick = 0
    for _ in range(30):
        tick += 1
        tr.update(_needs(hunger=0.55), tick)
    felt_before = tr.data["needs"]["hunger"]["felt"]
    tick += 1
    tr.update(_needs(hunger=0.95), tick)
    onset_gap = abs(tr.data["needs"]["hunger"]["felt"] - 0.95)
    # Offset: 0.95 -> 0.55, same displacement (returning -> offset).
    tr2 = _tracker(tmp, "off.json")
    tick2 = 0
    for _ in range(30):
        tick2 += 1
        tr2.update(_needs(hunger=0.95), tick2)
    felt2_before = tr2.data["needs"]["hunger"]["felt"]
    tick2 += 1
    tr2.update(_needs(hunger=0.55), tick2)
    offset_gap = abs(tr2.data["needs"]["hunger"]["felt"] - 0.55)
    # Onset rate 0.35 closes 35% of the gap per tick; offset rate 0.12
    # closes 12%. Gaps are measured against the actual pre-step felt, so
    # stabilization residuals cancel; only single-step noise (<=0.01)
    # remains inside the tolerance.
    assert onset_gap < offset_gap, (onset_gap, offset_gap)
    assert abs(onset_gap - (0.95 - felt_before) * 0.65) < 0.02, onset_gap
    assert abs(offset_gap - (felt2_before - 0.55) * 0.88) < 0.02, offset_gap


def test_felt_stays_in_bounds():
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    tr = _tracker(tmp)
    for tick in range(1, 60):
        tr.update(_needs(hunger=1.0 if tick % 2 else 0.0), tick)
        for key, e in tr.data["needs"].items():
            assert 0.0 <= e["felt"] <= 1.0, (key, e["felt"])


def test_first_contact_starts_at_baseline():
    """No silent defaults: unseen keys initialize to the documented 0.5
    baseline and are then chased — they do not jump to the actual."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    tr = _tracker(tmp)
    assert tr.data["needs"] == {}
    tr.update(_needs(hunger=0.9), 1)
    felt = tr.data["needs"]["hunger"]["felt"]
    # One onset step from baseline: 0.5 + 0.4*0.35 + noise.
    assert abs(felt - (0.5 + 0.4 * 0.35)) <= 0.011, felt
    assert tr.data["needs"]["hunger"]["last_tick"] == 1


# -- determinism ------------------------------------------------------------

def test_noise_is_seeded_hash():
    n1 = _noise(0, 42, "hunger", 0.01)
    n2 = _noise(0, 42, "hunger", 0.01)
    assert n1 == n2
    assert -0.01 <= n1 <= 0.01
    assert _noise(0, 42, "thirst", 0.01) != n1  # key decorrelates
    assert _noise(0, 43, "hunger", 0.01) != n1  # tick decorrelates
    assert _noise(7, 42, "hunger", 0.01) != n1  # seed decorrelates


def test_identical_sequences_replay_byte_identical():
    """Fitness 3: two identical tick/need sequences -> byte-identical sidecar."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    seq = []
    tick = 500
    for i in range(40):
        tick += 1
        seq.append((tick, _needs(hunger=0.2 + 0.02 * (i % 7),
                                energy=0.8 - 0.01 * i)))
    paths = []
    for name in ("a.json", "b.json"):
        tr = _tracker(tmp, name)
        for t, actuals in seq:
            tr.update(actuals, t)
        tr.save()
        paths.append(tmp / name)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    # And the bytes are stable across a save/load round-trip.
    tr3 = InteroceptionTracker(paths[0])
    tr3.save()
    assert paths[0].read_bytes() == paths[1].read_bytes()


def test_params_override_is_honored():
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    tr = _tracker(tmp, params={"rate_onset": 1.0, "noise_scale": 0.0})
    tr.update(_needs(hunger=0.5), 1)
    tr.update(_needs(hunger=0.9), 2)
    # Instant tracking, no noise.
    assert tr.data["needs"]["hunger"]["felt"] == 0.9
    assert tr.data["params"]["rate_onset"] == 1.0


# -- vocabulary -------------------------------------------------------------

def test_felt_level_hysteresis_matches_engine():
    # Thresholds .45/.65/.85; downward hysteresis margin .03.
    assert felt_level(0.9, "hunger", 0) == 3
    assert felt_level(0.7, "hunger", 0) == 2
    assert felt_level(0.5, "hunger", 0) == 1
    assert felt_level(0.4, "hunger", 0) == 0
    # Falling from 3 to urgency 0.83: above 0.85-0.03 -> stays 3.
    assert felt_level(0.83, "hunger", 3) == 3
    # Falling to 0.81: below the guard -> drops to 2.
    assert felt_level(0.81, "hunger", 3) == 2
    # LOW_IS_BAD: energy 0.2 -> urgency 0.8 -> level 2.
    assert felt_level(0.2, "energy", 0) == 2


def test_felt_text_reuses_engine_vocabulary():
    assert felt_text("hunger", 3) == "It is hard to think past this: I'm hungry."
    assert felt_text("hunger", 2) == "I'm hungry."
    assert felt_text("hunger", 1) == "I am beginning to notice this: I'm hungry."
    assert felt_text("hunger", 0) == "That feeling is easing."
    assert felt_text("energy", 2) == "I feel drained."


# -- view substitution -------------------------------------------------------

def _ws_with_tracker(texts, tracker):
    """texts: list of (source, text, concepts)."""
    ws = CalibosWorkspace()
    tick = 1
    for source, text, concepts in texts:
        ws.add(tick, source, text, concepts=concepts)
        tick += 1
    ws.interoception_tracker = tracker
    try:
        return ws.view()
    finally:
        ws.interoception_tracker = None


def test_view_substitutes_body_interoception_from_felt():
    """Fitness 2 (part 1): interoception records re-render from felt urgency."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    tr = _tracker(tmp)
    tick = 0
    for _ in range(30):  # stabilize felt hunger high
        tick += 1
        tr.update(_needs(hunger=0.95), tick)
    assert tr.data["needs"]["hunger"]["level"] == 3
    view = _ws_with_tracker([
        ("interoception", "I am beginning to notice this: I'm hungry.", ("hunger",)),
        ("memory", "I remember the garden gate.", ()),
    ], tr)
    by_source = {e.source: e.first_person for e in view.experiences}
    assert by_source["interoception"] == "It is hard to think past this: I'm hungry."
    assert by_source["memory"] == "I remember the garden gate."


def test_view_substitution_gap_is_visible():
    """The thinker can be wrong about its body: actual high, felt low."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    tr = _tracker(tmp)
    tick = 0
    for _ in range(30):  # felt settled low while actual will read high
        tick += 1
        tr.update(_needs(hunger=0.2), tick)
    assert tr.data["needs"]["hunger"]["level"] == 0
    view = _ws_with_tracker([
        ("interoception", "It is hard to think past this: I'm hungry.", ("hunger",)),
    ], tr)
    texts = [e.first_person for e in view.experiences]
    assert texts == ["That feeling is easing."], texts


def test_non_body_interoception_passes_through():
    """Recall-unease / concern interoceptions carry no need key: untouched,
    never a silent default."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    tr = _tracker(tmp)
    tr.update(_needs(hunger=0.9), 1)
    view = _ws_with_tracker([
        ("interoception", "Remembering that leaves me uneasy.", ()),
        ("interoception", "I am concerned: the meeting might go badly.", ()),
        ("thought", "I should prepare.", ()),
    ], tr)
    texts = [(e.source, e.first_person) for e in view.experiences]
    assert ("interoception", "Remembering that leaves me uneasy.") in texts
    assert ("interoception", "I am concerned: the meeting might go badly.") in texts
    assert ("thought", "I should prepare.") in texts


def test_view_without_tracker_is_byte_identical():
    """No tracker attached (plain engine use): the old view, unchanged."""
    ws = CalibosWorkspace()
    ws.add(1, "interoception", "I'm hungry.", concepts=("hunger",))
    ws.add(2, "memory", "I remember the gate.", concepts=())
    view = ws.view()
    assert [(e.source, e.first_person) for e in view.experiences] == [
        ("interoception", "I'm hungry."),
        ("memory", "I remember the gate."),
    ]


def test_view_without_felt_state_passes_through():
    """Tracker attached but no update ever ran: no silent defaults — the
    record text stands until felt state exists."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-"))
    tr = _tracker(tmp)  # file absent; needs empty
    view = _ws_with_tracker([
        ("interoception", "I'm hungry.", ("hunger",)),
    ], tr)
    assert [e.first_person for e in view.experiences] == ["I'm hungry."]


# -- status bands ---------------------------------------------------------------

def _patched_cli(tmp: Path):
    db = tmp / "mind.db"
    intero = tmp / "interoception.json"
    salience = tmp / "salience.json"
    inbox = tmp / "inbox"
    proposals = tmp / "proposals"
    archive = tmp / "archive"
    saved = (cli.DB, cli.SALIENCE, cli.INTEROCEPTION, cli.PROPOSALS,
             cli.ARCHIVE, cli._subject)
    cli.DB, cli.SALIENCE, cli.INTEROCEPTION, cli.PROPOSALS, cli.ARCHIVE = (
        db, salience, intero, proposals, archive)
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(salience),
                              interoception_path=str(intero))

    cli._subject = make_subject
    return make_subject, saved, intero


def _restore(saved):
    (cli.DB, cli.SALIENCE, cli.INTEROCEPTION, cli.PROPOSALS,
     cli.ARCHIVE, cli._subject) = saved


def _stdout(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args)
    return rc, buf.getvalue()


def test_status_shows_felt_bands_by_default():
    tmp = Path(tempfile.mkdtemp(prefix="intero-status-"))
    make_subject, saved, intero = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        tr = subject.workspace.interoception_tracker
        tick = 0
        for _ in range(40):
            tick += 1
            tr.update(_needs(hunger=0.95, thirst=0.1), tick)
        tr.save()
        rc, out = _stdout(cli.cmd_status, argparse.Namespace(raw=False))
        assert rc == 0
        assert "needs (felt):" in out, out
        assert "urgent" in out, out  # hunger felt level 3
        assert "0.95" not in out and "hunger': 0.9" not in out, out
    finally:
        _restore(saved)


def test_status_raw_shows_exact_floats():
    tmp = Path(tempfile.mkdtemp(prefix="intero-status-"))
    make_subject, saved, intero = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        rc, out = _stdout(cli.cmd_status, argparse.Namespace(raw=True))
        assert rc == 0
        assert "needs (off-baseline):" in out, out
    finally:
        _restore(saved)


# -- wiring: init reset, run-tick hook, drift read-only -----------------------------

def test_init_force_resets_interoception_sidecar():
    """Fitness 4: reseed restarts felt state; no stale felt on recycled ids."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-init-"))
    make_subject, saved, intero = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 20):
            tr.update(_needs(hunger=0.95), tick)
        tr.save()
        assert tr.data["needs"]["hunger"]["level"] == 3
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        payload = json.loads(intero.read_text(encoding="utf-8"))
        assert payload["needs"] == {}, payload["needs"]
    finally:
        _restore(saved)


def test_run_tick_updates_felt():
    """The _run_tick hook advances felt from live engine needs."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-tick-"))
    make_subject, saved, intero = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        before = (json.loads(intero.read_text(encoding="utf-8"))["needs"]
                  if intero.exists() else {})
        assert before == {}
        cli._run_tick(subject)
        payload = json.loads(intero.read_text(encoding="utf-8"))
        needs = payload["needs"]
        assert needs, "hook must write felt state after a waking tick"
        tick = subject.engine.state.tick
        for key, e in needs.items():
            assert e["last_tick"] == tick, (key, e)
            assert 0.0 <= e["felt"] <= 1.0
            assert e["level"] in (0, 1, 2, 3)
    finally:
        _restore(saved)


def test_drift_leaves_sidecar_untouched():
    """Fitness 6 (genome: read-only violations): `mind drift` must not
    touch the interoception sidecar — byte-identical before/after."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-drift-"))
    make_subject, saved, intero = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 10):
            tr.update(_needs(hunger=0.8), tick)
        tr.save()
        digest_before = hashlib.sha256(intero.read_bytes()).hexdigest()
        rc, out = _stdout(cli.cmd_drift, argparse.Namespace(window=10))
        assert rc == 0
        assert intero.exists()
        digest_after = hashlib.sha256(intero.read_bytes()).hexdigest()
        assert digest_before == digest_after, "drift mutated the sidecar"
    finally:
        _restore(saved)


def test_status_leaves_sidecar_untouched():
    """`mind status` reads felt state; it must not write it."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-status-ro-"))
    make_subject, saved, intero = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 10):
            tr.update(_needs(hunger=0.8), tick)
        tr.save()
        digest_before = hashlib.sha256(intero.read_bytes()).hexdigest()
        rc, _ = _stdout(cli.cmd_status, argparse.Namespace(raw=False))
        assert rc == 0
        assert hashlib.sha256(intero.read_bytes()).hexdigest() == digest_before
    finally:
        _restore(saved)


def test_dream_ticks_never_update_felt():
    """Spec: the update hook runs after waking ticks only. Dream ticks
    freeze the body, so the felt body must freeze with it — byte-identical
    sidecar across dream_tick()."""
    tmp = Path(tempfile.mkdtemp(prefix="intero-dream-"))
    make_subject, saved, intero = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 10):
            tr.update(_needs(hunger=0.8), tick)
        tr.save()
        digest_before = hashlib.sha256(intero.read_bytes()).hexdigest()
        subject.dream_tick()
        subject.dream_tick()
        assert hashlib.sha256(intero.read_bytes()).hexdigest() == digest_before, \
            "dream tick moved the felt body"
    finally:
        _restore(saved)


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"all {len(fns)} interoception tests passed")


if __name__ == "__main__":
    _main()
