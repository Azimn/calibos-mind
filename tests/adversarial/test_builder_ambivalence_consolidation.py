"""Builder round-1 tests: standing-tie consolidation (ambivalence runs).

Spec: the builder-task SPEC (2026-10-03) — calibos_mind/ambivalence.py.
All fixtures live in /tmp — the live store is never touched.

The consolidation contract under test:
  1. A 10-tick standing near-tie emits at most 3 markers
     (onset + optional drift + offset), not 10.
  2. Isolated single-tick near-ties emit exactly one marker each, with
     today's seven fields intact (plus the documented "run": "onset").
  3. Margin-drift reconstruction: onset tick, offset tick, margin min/max
     recoverable from the marker stream alone.
  4. Contested/not-contested parity: at every tick the stream's verdict
     matches pristine contested_marker (bypass kinds aside, which the
     engine never contests).
  5. Run state survives the per-transaction _restore (it lives on the
     subject, not the tracker or wrapper closure).
  6. Read-only commands and dream ticks never create/extend/close runs;
     identical sequences give byte-identical sidecars.

Scenario control: the tests pin engine.state.needs / pressures /
pressure_baselines / concerns directly, set engine.state.tick explicitly,
then call engine.select_conduct() — the same selector the heartbeat uses
— so the wrapper observes the exact triage lists the frozen engine
computed. Urgency recap (digital_subject/engine.py, jelly_psiduck-0.2.0a2):
  needs: thirst/hunger/pain/fatigue/loneliness -> urgency = value;
         energy/comfort/warmth/safety/focus/satisfaction -> 1 - value.
  pressures: magnitude = |value - baseline|.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.ambivalence import (
    CHANNEL_RULE_BYPASS_KINDS,
    CONTESTED_MARGIN,
    contested_marker,
    open_run,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Event


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="ambcon-test-"))


def _make_subject(tmp: Path) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        interoception_path=None,
        familiarity_path=None,
        ambivalence_path=str(tmp / "ambivalence.json"),
    )


def _set_state(subject, needs, pressures, baselines):
    st = subject.engine.state
    st.needs = dict(needs)
    st.pressures = dict(pressures)
    st.pressure_baselines = dict(baselines)
    st.concerns = {}


# Named scenario presets. A(m): cross-channel fear/thirst near-tie with
# margin exactly m (|m| < 0.12). B: within-channel need tie
# (thirst/hunger). C: within-channel pressure tie (fear/arousal).
# CLEAR: no axis contested.
def _state_a(m):
    return ({"thirst": 0.72, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
            {"fear": 0.72 + m, "trust": 0.5},
            {"fear": 0.0, "trust": 0.5})


def _state_b():
    return ({"thirst": 0.72, "hunger": 0.70, "energy": 0.95, "comfort": 0.95},
            {"fear": 0.10, "trust": 0.5},
            {"fear": 0.0, "trust": 0.5})


def _state_c():
    return ({"thirst": 0.20, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
            {"fear": 0.60, "arousal": 0.55, "trust": 0.5},
            {"fear": 0.0, "arousal": 0.0, "trust": 0.5})


def _state_clear():
    return ({"thirst": 0.90, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
            {"fear": 0.10, "trust": 0.5},
            {"fear": 0.0, "trust": 0.0})


def _select_at(subject, tick, state, kind="message"):
    """One observed selection at an explicit tick; flush like _run_tick."""
    _select_noflush(subject, tick, state, kind=kind)
    subject.workspace.ambivalence_tracker.flush()


def _select_noflush(subject, tick, state, kind="message"):
    """One observed selection at an explicit tick; leave pending staged."""
    needs, pressures, baselines = state
    _set_state(subject, needs, pressures, baselines)
    subject.engine.state.tick = tick
    subject.engine.select_conduct(Event(kind, "tester", "scripted tick"))


def _sidecar(tmp: Path):
    p = tmp / "ambivalence.json"
    return json_markers(p)


def json_markers(p: Path):
    import json
    return json.loads(p.read_text(encoding="utf-8"))["markers"] if p.exists() else None


# -- fitness 1: 10-tick standing tie -> onset + offset only -------------------

def test_ten_tick_standing_tie_emits_onset_plus_offset_only():
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    margins = [0.02, 0.05, 0.08, 0.10, 0.06, 0.03, 0.07, 0.09, 0.04, 0.11]
    for i, m in enumerate(margins):
        _select_at(subject, t0 + i, _state_a(m))
    _select_at(subject, t0 + 10, _state_clear())  # closes the run
    markers = _sidecar(tmp)
    assert markers is not None and len(markers) == 2, markers  # <= 3, not 10
    onset, offset = markers
    assert onset["run"] == "onset"
    assert onset["tick"] == t0
    assert onset["margin"] == 0.02
    assert onset["contenders"] == ["fear", "thirst"]
    assert onset["channels"] == ["pressure", "need"]
    assert onset["winner"] == "thirst"
    assert onset["winning_channel"] == "need"
    assert offset["run"] == "offset"
    assert offset["reason"] == "clear"
    assert offset["onset_tick"] == t0
    assert offset["offset_tick"] == t0 + 10
    assert offset["last_tick"] == t0 + 9
    assert offset["margin_min"] == 0.02
    assert offset["margin_max"] == 0.11
    assert offset["contenders"] == ["fear", "thirst"]
    assert open_run(subject) is None  # run closed


# -- fitness 2: isolated single-tick ties -> exactly one marker each --------

def test_isolated_single_tick_ties_one_marker_each_schema_stable():
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    _select_at(subject, t0, _state_a(0.06))
    _select_at(subject, t0 + 1, _state_clear())
    _select_at(subject, t0 + 2, _state_b())
    _select_at(subject, t0 + 3, _state_clear())
    markers = _sidecar(tmp)
    assert markers is not None and len(markers) == 2, markers
    for m in markers:
        # Today's seven fields intact; only the documented run field added.
        assert set(m.keys()) == {"tick", "event", "contenders", "channels",
                                 "margin", "winner", "winning_channel", "run"}, m
        assert m["run"] == "onset", m  # onset immediately followed by nothing
    assert [m["tick"] for m in markers] == [t0, t0 + 2]
    assert markers[0]["contenders"] == ["fear", "thirst"]
    assert markers[1]["contenders"] == ["thirst", "hunger"]


def test_single_tick_marker_fields_match_pristine_output():
    # The one marker of a 1-tick run carries exactly what the
    # un-consolidated code would have emitted (plus "run": "onset").
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    needs, pressures, baselines = _state_a(0.06)
    _set_state(subject, needs, pressures, baselines)
    triage_n = subject.engine._triage_needs()
    triage_p = subject.engine._triage_pressures()
    expected = contested_marker(tick=t0, event_kind="message",
                                needs=triage_n, pressures=triage_p)
    assert expected is not None
    _select_at(subject, t0, _state_a(0.06))
    _select_at(subject, t0 + 1, _state_clear())
    markers = _sidecar(tmp)
    assert len(markers) == 1, markers
    for key, value in expected.items():
        assert markers[0][key] == value, (key, markers[0][key], value)


# -- fitness 3: drift reconstruction from the stream alone -------------------

def test_margin_drift_recoverable_from_stream_alone():
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    margins = [0.03, 0.09, 0.05, 0.11, 0.07]
    for i, m in enumerate(margins):
        _select_at(subject, t0 + i, _state_a(m))
    _select_at(subject, t0 + 5, _state_clear())
    markers = _sidecar(tmp)
    assert len(markers) == 2, markers
    onset, offset = markers
    # Everything recoverable without consulting the store or the run:
    assert onset["tick"] == t0                      # onset tick
    assert onset["margin"] == margins[0]             # initial margin
    assert offset["onset_tick"] == t0
    assert offset["offset_tick"] == t0 + 5
    assert offset["last_tick"] == t0 + 4
    assert offset["margin_min"] == min(margins)
    assert offset["margin_max"] == max(margins)
    # The onset margin sits inside the run's observed range:
    assert offset["margin_min"] <= onset["margin"] <= offset["margin_max"]


# -- fitness: contested/not-contested parity at every tick -------------------

def test_contested_purity_every_tick_mixed_script():
    # The critic's core invariant: contested/not-contested must match
    # pristine contested_marker at every tick. Script: two multi-tick
    # runs, a contender change, a bypass close, and clear margins.
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    script = [
        (_state_a(0.06), "message"),   # t0: A onset
        (_state_a(0.09), "message"),   # t1: A extends
        (_state_clear(), "message"),   # t2: clear -> offset A
        (_state_b(), "message"),       # t3: B onset
        (_state_b(), "message"),       # t4: B extends
        (_state_a(0.05), "apology"),   # t5: bypass -> offset B, no marker
        (_state_a(0.05), "message"),   # t6: A onset (fresh run)
        (_state_c(), "message"),       # t7: C onset (A was 1 tick: no offset)
        (_state_c(), "message"),       # t8: C extends
        (_state_clear(), "message"),   # t9: clear -> offset C
    ]
    pristine = {}
    for i, (state, kind) in enumerate(script):
        needs, pressures, baselines = state
        _set_state(subject, needs, pressures, baselines)
        subject.engine.state.tick = t0 + i
        if kind in CHANNEL_RULE_BYPASS_KINDS:
            # The engine never ran the channel rule: nothing was contested.
            pristine[t0 + i] = None
        else:
            pristine[t0 + i] = contested_marker(
                tick=t0 + i, event_kind=kind,
                needs=subject.engine._triage_needs(),
                pressures=subject.engine._triage_pressures())
    for i, (state, kind) in enumerate(script):
        _select_at(subject, t0 + i, state, kind=kind)

    markers = _sidecar(tmp)
    assert markers is not None, "script must produce markers"
    onsets = {m["tick"]: m for m in markers if m.get("run") == "onset"}
    offsets = [m for m in markers if m.get("run") == "offset"]

    def stream_cover(tick):
        """Return the (contenders, channels) the stream says was contested
        at tick, or None."""
        if tick in onsets:
            m = onsets[tick]
            return (tuple(m["contenders"]), tuple(m["channels"]))
        for o in offsets:
            if o["onset_tick"] <= tick <= o["last_tick"]:
                return (tuple(o["contenders"]), tuple(o["channels"]))
        return None

    for i in range(len(script)):
        t = t0 + i
        p = pristine[t]
        s = stream_cover(t)
        if p is None:
            assert s is None, f"tick {t}: stream claims contested, pristine says clear"
        else:
            assert s is not None, f"tick {t}: pristine contested but stream silent"
            assert s == (tuple(p["contenders"]), tuple(p["channels"])), \
                f"tick {t}: pair mismatch {s} vs {p}"
    # Spot-check the expected run structure, not just parity:
    assert len(onsets) == 4, sorted(onsets)          # A, B, A, C
    assert sorted(onsets) == [t0, t0 + 3, t0 + 6, t0 + 7]
    assert len(offsets) == 3, offsets               # A, B, C (1-tick A: none)
    off_b = next(o for o in offsets if o["onset_tick"] == t0 + 3)
    assert off_b["reason"] == "bypass", off_b
    assert off_b["offset_tick"] == t0 + 5 and off_b["last_tick"] == t0 + 4
    off_a = next(o for o in offsets if o["onset_tick"] == t0)
    assert (off_a["offset_tick"], off_a["last_tick"],
            off_a["margin_min"], off_a["margin_max"]) == (t0 + 2, t0 + 1, 0.06, 0.09)


# -- run state placement: survives the per-transaction _restore ---------------

def test_run_survives_transaction_restore_cycle():
    # The critical placement invariant: _restore swaps in a fresh engine
    # AND a fresh tracker on every transaction, so run state must live on
    # the subject. A restore mid-run must extend the run, not re-emit an
    # onset.
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    _select_noflush(subject, t0, _state_a(0.06))
    run = open_run(subject)
    assert run is not None and run.absorbed == 1
    tracker = subject.workspace.ambivalence_tracker
    assert tracker.pending == 1  # the onset, staged
    tracker.flush()              # end-of-tick flush, as _run_tick does

    with subject._transaction():
        pass  # forces a _restore cycle: fresh engine + fresh tracker
    tracker2 = subject.workspace.ambivalence_tracker
    assert tracker2 is not tracker
    assert tracker2.pending == 0
    assert open_run(subject) is not None, "run must survive _restore"

    # Same standing tie after the restore: extends, no second onset.
    _select_noflush(subject, t0, _state_a(0.08))
    assert tracker2.pending == 0, "restore must not re-emit the onset"
    assert open_run(subject).absorbed == 2
    # Next tick extends; a clear margin then closes with one offset.
    _select_noflush(subject, t0 + 1, _state_a(0.04))
    assert tracker2.pending == 0
    _select_noflush(subject, t0 + 2, _state_clear())
    assert tracker2.pending == 1
    tracker2.flush()
    markers = _sidecar(tmp)
    assert len(markers) == 2, markers
    onset, offset = markers
    assert onset["run"] == "onset" and onset["tick"] == t0
    assert offset["run"] == "offset"
    assert (offset["onset_tick"], offset["offset_tick"], offset["last_tick"],
            offset["margin_min"], offset["margin_max"]) == (t0, t0 + 2, t0 + 1, 0.04, 0.08)


def test_install_observer_never_resets_open_run():
    # Re-installation (what _restore does every transaction) wraps the new
    # engine but must leave the open run alone.
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    _select_at(subject, t0, _state_a(0.06))
    assert open_run(subject) is not None
    from calibos_mind.ambivalence import install_observer
    install_observer(subject)
    assert open_run(subject) is not None, "re-install must not clear the run"
    assert open_run(subject).absorbed == 1


# -- dream / read-only isolation ----------------------------------------------

def test_dream_ticks_neither_create_nor_extend_nor_close_runs():
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    _select_noflush(subject, t0, _state_a(0.06))
    _select_noflush(subject, t0 + 1, _state_a(0.08))
    tracker = subject.workspace.ambivalence_tracker
    assert tracker.pending == 1  # onset only; run open
    run = open_run(subject)
    assert run.absorbed == 2

    subject._dreaming = True
    try:
        _set_state(subject, *_state_a(0.03))
        subject.engine.state.tick = t0 + 2
        subject.engine.select_conduct(Event("message", "tester", "dream input"))
    finally:
        subject._dreaming = False
    # Untouched: not extended, not closed, nothing staged.
    assert open_run(subject) is run
    assert run.absorbed == 2 and run.last_tick == t0 + 1
    assert tracker.pending == 1
    tracker.flush()
    assert len(_sidecar(tmp)) == 1

    # Waking clear margin afterwards still closes the run properly.
    _select_at(subject, t0 + 3, _state_clear())
    markers = _sidecar(tmp)
    assert len(markers) == 2, markers
    assert markers[1]["run"] == "offset"
    assert markers[1]["last_tick"] == t0 + 1  # dream tick never absorbed


def test_no_tracker_means_no_run_state_no_crash():
    # ambivalence_path=None: the wrapper must stay silent and never
    # conjure run state.
    inbox = _tmp() / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    subject = CalibosSubject(
        str(_tmp() / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(_tmp() / "salience.json"),
        interoception_path=None, familiarity_path=None,
        ambivalence_path=None)
    needs, pressures, baselines = _state_a(0.06)
    _set_state(subject, needs, pressures, baselines)
    subject.engine.select_conduct(Event("message", "tester", "x"))
    assert open_run(subject) is None


# -- determinism ----------------------------------------------------------------

def test_identical_sequences_byte_identical_sidecar():
    def _run():
        tmp = _tmp()
        subject = _make_subject(tmp)
        t0 = subject.engine.state.tick
        script = [(_state_a(0.06), "message"), (_state_a(0.09), "message"),
                  (_state_clear(), "message"), (_state_b(), "message"),
                  (_state_b(), "message"), (_state_a(0.05), "apology"),
                  (_state_c(), "message"), (_state_clear(), "message")]
        for i, (state, kind) in enumerate(script):
            _select_at(subject, t0 + i, state, kind=kind)
        return (tmp / "ambivalence.json").read_bytes()

    assert _run() == _run()


# -- within-channel runs consolidate too ----------------------------------------

def test_within_channel_run_consolidates():
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    _select_at(subject, t0, _state_b())      # need tie, gap 0.02
    _select_at(subject, t0 + 1, _state_b())
    _select_at(subject, t0 + 2, _state_b())
    _select_at(subject, t0 + 3, _state_clear())
    markers = _sidecar(tmp)
    assert len(markers) == 2, markers
    onset, offset = markers
    assert onset["contenders"] == ["thirst", "hunger"]
    assert onset["channels"] == ["need", "need"]
    assert offset["margin_min"] == offset["margin_max"] == 0.02


# -- same-tick double selection extends, never duplicates ------------------------

def test_two_selections_same_tick_extend_not_duplicate():
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    needs, pressures, baselines = _state_a(0.06)
    _set_state(subject, needs, pressures, baselines)
    subject.engine.state.tick = t0
    subject.engine.select_conduct(Event("message", "tester", "first"))
    subject.engine.select_conduct(Event("message", "tester", "second"))
    tracker = subject.workspace.ambivalence_tracker
    assert tracker.pending == 1, "same-tick re-selection must not duplicate"
    _select_at(subject, t0 + 1, _state_clear())
    markers = _sidecar(tmp)
    assert len(markers) == 1, markers  # 1-tick run: onset alone, no offset
    assert markers[0]["run"] == "onset"
