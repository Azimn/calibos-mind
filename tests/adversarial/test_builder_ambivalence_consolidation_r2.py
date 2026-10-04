"""Builder round-2 tests: ambivalence run fixes (critic round-1 FAILs).

Covers the two critic failures plus the production-path regression for the
cli._run_tick flush reorder. All fixtures live in /tmp — the live store is
never touched.

Context: heartbeat() itself runs inside _transaction(), so every tick ends
with a _restore that swaps in a fresh AmbivalenceTracker. The staged onset
must therefore either be flushed before any later transaction (the
_run_tick reorder: flush immediately after heartbeat(), before the habits
block) or carried across the swap (CalibosSubject._restore pending
carryover). Both are exercised below.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.ambivalence import (
    AmbivalenceTracker,
    open_run,
)
from calibos_mind.habits import HabitFormationTracker
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Event


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="ambcon-r2-test-"))


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
        habits_path=str(tmp / "habits-formed.json"),
    )


def _set_state(subject, needs, pressures, baselines):
    st = subject.engine.state
    st.needs = dict(needs)
    st.pressures = dict(pressures)
    st.pressure_baselines = dict(baselines)
    st.concerns = {}


def _state_a(m):
    return ({"thirst": 0.72, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
            {"fear": 0.72 + m, "trust": 0.5},
            {"fear": 0.0, "trust": 0.5})


def _state_clear():
    return ({"thirst": 0.90, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
            {"fear": 0.10, "trust": 0.5},
            {"fear": 0.0, "trust": 0.0})


def _select_noflush(subject, tick, state, kind="message"):
    needs, pressures, baselines = state
    _set_state(subject, needs, pressures, baselines)
    subject.engine.state.tick = tick
    subject.engine.select_conduct(Event(kind, "tester", "scripted tick"))


def _sidecar(tmp: Path):
    p = tmp / "ambivalence.json"
    return json.loads(p.read_text(encoding="utf-8"))["markers"] if p.exists() else None


# -- FAIL 1, production path: _run_tick flushes before the habits ----------

def test_run_tick_flushes_onset_before_habits_transaction():
    # End-to-end through cli._run_tick with a forced habits-reconcile
    # transaction every tick. The ambivalence flush must run after
    # heartbeat() but BEFORE the habits block's transaction: the first
    # flush event carrying the staged onset must precede the first
    # post-heartbeat transaction. Then a two-tick standing tie closed by
    # a clear tick must land onset + offset with matching ticks.
    tmp = _tmp()
    saved_inbox = cli.INBOX
    cli.INBOX = tmp / "inbox"
    (tmp / "inbox").mkdir(exist_ok=True)
    orig_flush = AmbivalenceTracker.flush
    orig_observe = HabitFormationTracker.observe_tick
    flush_events = []
    tx_events = []
    try:
        def recording_flush(self):
            flush_events.append((self.pending, tx_events.count("tx")))
            return orig_flush(self)

        AmbivalenceTracker.flush = recording_flush
        # Force the habits reconcile transaction on every tick.
        HabitFormationTracker.observe_tick = lambda self, **kw: (False, True)

        subject = _make_subject(tmp)
        orig_tx = subject._transaction

        def recording_tx():
            tx_events.append("tx")
            return orig_tx()

        subject._transaction = recording_tx
        subject.enqueue(Event("message", "tester", "wired tick"))

        def pin(state):
            with subject._transaction():
                _set_state(subject, *state)

        pin(_state_a(0.06))
        flush_events.clear()
        tx_events.clear()
        t0 = subject.engine.state.tick
        cli._run_tick(subject)  # contested tick 1: onset
        assert any(p >= 1 for p, _ in flush_events), (
            "no flush carried a staged onset")
        first_onset_flush = next(tx for p, tx in flush_events if p >= 1)
        assert first_onset_flush == 1, (
            f"onset flush happened after {first_onset_flush - 1} "
            f"post-heartbeat transaction(s): flush={flush_events} tx={tx_events}")
        assert "tx" in tx_events, "harness failed to force the habits transaction"
        markers = _sidecar(tmp)
        assert markers and markers[-1].get("run") == "onset", markers
        onset_tick = markers[-1]["tick"]
        assert onset_tick >= t0

        # Contested tick 2 extends the run; a clear tick 3 closes it.
        pin(_state_a(0.08))
        cli._run_tick(subject)
        pin(_state_clear())
        cli._run_tick(subject)
        markers = _sidecar(tmp)
        onsets = [m for m in markers if m.get("run") == "onset"]
        offsets = [m for m in markers if m.get("run") == "offset"]
        assert len(onsets) == 1 and len(offsets) == 1, markers
        assert offsets[0]["onset_tick"] == onsets[0]["tick"] == onset_tick, markers
        assert offsets[0]["last_tick"] > offsets[0]["onset_tick"], markers
    finally:
        AmbivalenceTracker.flush = orig_flush
        HabitFormationTracker.observe_tick = orig_observe
        cli.INBOX = saved_inbox


# -- FAIL 1, interleaving (critic attack, folded in) --------------------------

def test_pending_onset_carried_across_restore_not_orphaned():
    # Critic round-1 attack: a transaction between the onset note and the
    # flush must not orphan the onset. _restore carries the pending buffer
    # onto the fresh tracker, so the tick's own flush still writes it.
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    _select_noflush(subject, t0, _state_a(0.06))   # onset staged on tracker A
    tracker_a = subject.workspace.ambivalence_tracker
    assert tracker_a.pending == 1, "expected the onset staged"
    with subject._transaction():
        pass
    tracker_b = subject.workspace.ambivalence_tracker
    assert tracker_b is not tracker_a, "restore must swap the tracker"
    assert tracker_b.pending == 1, "pending onset must survive the restore"
    tracker_b.flush()
    _select_noflush(subject, t0 + 1, _state_a(0.08))
    assert tracker_b.pending == 0
    _select_noflush(subject, t0 + 2, _state_clear())
    assert tracker_b.pending == 1, "expected the offset staged"
    tracker_b.flush()
    markers = _sidecar(tmp)
    assert markers, "expected markers in the sidecar"
    onsets = [m for m in markers if m.get("run") == "onset"]
    offsets = [m for m in markers if m.get("run") == "offset"]
    assert len(offsets) == 1, markers
    assert any(o["tick"] == offsets[0]["onset_tick"] for o in onsets), (
        f"ORPHAN OFFSET: onset_tick={offsets[0]['onset_tick']} has no onset "
        f"marker in the stream; stream={markers}")


def test_flushed_onset_not_duplicated_by_carryover():
    # The carryover must not re-stage an onset that was already flushed:
    # exactly one onset marker per run, even with a restore mid-run.
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    _select_noflush(subject, t0, _state_a(0.06))
    subject.workspace.ambivalence_tracker.flush()  # onset on disk
    with subject._transaction():
        pass
    tracker_b = subject.workspace.ambivalence_tracker
    assert tracker_b.pending == 0, "flushed onset must not be re-staged"
    _select_noflush(subject, t0 + 1, _state_a(0.08))
    assert tracker_b.pending == 0, "extend must not re-emit the onset"
    _select_noflush(subject, t0 + 2, _state_clear())
    tracker_b.flush()
    markers = _sidecar(tmp)
    onsets = [m for m in markers if m.get("run") == "onset"]
    offsets = [m for m in markers if m.get("run") == "offset"]
    assert len(onsets) == 1 and len(offsets) == 1, markers
    assert offsets[0]["onset_tick"] == onsets[0]["tick"] == t0, markers


# -- FAIL 2 (critic attack, folded in) ----------------------------------------

def test_failed_onset_note_leaves_no_orphan_run():
    # Critic round-1 attack: if staging the onset fails, no run may remain
    # — the next contested tick must open a fresh run with its own onset.
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    tracker = subject.workspace.ambivalence_tracker
    real_note = tracker.note
    calls = {"n": 0}

    def boom(marker):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IOError("simulated staging failure")
        return real_note(marker)

    tracker.note = boom
    try:
        _select_noflush(subject, t0, _state_a(0.06))  # onset staging fails
    finally:
        tracker.note = real_note
    assert tracker.pending == 0
    assert open_run(subject) is None, "failed onset must not leave a run"
    _select_noflush(subject, t0 + 1, _state_a(0.08))
    assert tracker.pending == 1, (
        "the second contested tick extended a run whose onset never reached "
        "the stream instead of emitting its own onset")
    tracker.flush()
    markers = _sidecar(tmp)
    onsets = [m for m in markers if m.get("run") == "onset"]
    assert any(o["tick"] == t0 + 1 for o in onsets), markers


# -- mirror hardening: failed offset note keeps the run open ------------------

def test_offset_note_failure_keeps_run_open_for_retry():
    # Symmetric with the FAIL 2 fix: the offset is staged BEFORE the run
    # is cleared, so a staging failure leaves the run open and a later
    # close emits one offset for the whole span — never a dropped run.
    tmp = _tmp()
    subject = _make_subject(tmp)
    t0 = subject.engine.state.tick
    _select_noflush(subject, t0, _state_a(0.06))
    subject.workspace.ambivalence_tracker.flush()
    _select_noflush(subject, t0 + 1, _state_a(0.08))
    tracker = subject.workspace.ambivalence_tracker
    real_note = tracker.note
    calls = {"n": 0}

    def boom(marker):
        calls["n"] += 1
        if marker.get("run") == "offset":
            raise IOError("simulated staging failure")
        return real_note(marker)

    tracker.note = boom
    try:
        _select_noflush(subject, t0 + 2, _state_clear())  # offset fails
    finally:
        tracker.note = real_note
    assert open_run(subject) is not None, "failed offset must keep the run open"
    _select_noflush(subject, t0 + 3, _state_clear())  # retry closes it
    assert open_run(subject) is None
    tracker.flush()
    markers = _sidecar(tmp)
    onsets = [m for m in markers if m.get("run") == "onset"]
    offsets = [m for m in markers if m.get("run") == "offset"]
    assert len(onsets) == 1 and len(offsets) == 1, markers
    assert (offsets[0]["onset_tick"], offsets[0]["last_tick"]) == (t0, t0 + 1), markers
