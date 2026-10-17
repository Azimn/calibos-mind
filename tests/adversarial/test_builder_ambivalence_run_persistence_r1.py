"""Builder battery r1: ambivalence open-run persistence across processes.

Spec: research/specs/2026-10-16-ambivalence-run-persistence.md (2026-10-16).
All fixtures live in /tmp — the live store is never touched.

A "process boundary" is simulated honestly: the subject (and its tracker)
is dropped entirely and a fresh CalibosSubject is constructed on the same
/tmp paths, exactly as a new `mind` invocation would. The engine's
in-memory state is pinned explicitly per tick (_pin_*), so no committed
DB state is needed for determinism.

Fitness functions under test (spec numbering):
  1. Two-process replay (A: ticks 1-2 contested, B: tick 3 contested,
     tick 4 clear) -> exactly ONE onset + ONE offset naming onset_tick=1,
     offset_tick=4, last_tick=3, margin min/max.
  2. One-tick run -> exactly one onset, no offset, no open_run residue.
  3. Kill between persist and marker flush -> resume extends honestly;
     never a second onset for the same tick.
  4. Contender change across the boundary -> offset with reason "changed"
     for the old run + onset for the new run.
  5. 10-tick in-memory standing tie (no boundary) -> one onset + one
     offset (the 2026-10-03 fitness still holds).
  6. Read-only command sweep + dream ticks -> sidecar byte-identical;
     the resumed run still extends honestly afterward.
  7. init --force -> open_run wiped.
  8. Full existing suite green (run separately).

Adversarial section: malformed persisted runs, corrupt sidecars, failed
persists, in-process _restore clobber attempts, three-process ping-pong,
cap eviction, reset(), and the bypass path across a boundary.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.ambivalence import (
    AMBIVALENCE_CAP,
    _OpenRun,
    open_run,
    resume_open_run,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Action, Event


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="amb-runpersist-"))


def _make_subject(tmp: Path, with_ambivalence: bool = True) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        interoception_path=None,
        familiarity_path=None,
        ambivalence_path=(str(tmp / "ambivalence.json")
                          if with_ambivalence else None),
    )


def _pin_state(subject, needs, pressures, baselines):
    """Pin the engine's motive state; concerns cleared so triage is exact."""
    st = subject.engine.state
    st.needs = dict(needs)
    st.pressures = dict(pressures)
    st.pressure_baselines = dict(baselines)
    st.concerns = {}


def _pin_contested(subject, fear=0.78):
    # fear vs thirst cross-channel near-tie, margin (fear - 0.72) < 0.12.
    # Need channel wins (fear < thirst + 0.12).
    _pin_state(subject,
               needs={"thirst": 0.72, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": fear, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.5})


def _pin_changed_key(subject):
    # Within-need tie: thirst 0.72 vs hunger 0.70 (gap 0.02); cross margin
    # wide. Key ([thirst, hunger], [need, need]) != ([fear, thirst],
    # [pressure, need]).
    _pin_state(subject,
               needs={"thirst": 0.72, "hunger": 0.70, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.10, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.5})


def _pin_clear(subject):
    # Cross margin -0.80 (need wins uncontested); trust baseline 0.0 makes
    # its magnitude 0.50, so no within-pressure tie either.
    _pin_state(subject,
               needs={"thirst": 0.90, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.10, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.0})


def _tick(subject, tick, pin, *, kind="message", flush=True, fear=0.78):
    """Run one intention selection at an explicit tick through the
    observed engine path."""
    subject.engine.state.tick = tick
    if pin is _pin_contested:
        pin(subject, fear=fear)
    else:
        pin(subject)
    action = subject.engine.select_conduct(
        Event(kind, "tester", f"tick {tick}"))
    tracker = subject.workspace.ambivalence_tracker
    if flush:
        tracker.flush()
    return action


def _sidecar(tmp: Path):
    p = tmp / "ambivalence.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _markers(tmp: Path):
    data = _sidecar(tmp)
    return data["markers"] if data else []


def _onsets(markers):
    return [m for m in markers if m.get("run") == "onset"]


def _offsets(markers):
    return [m for m in markers if m.get("run") == "offset"]


def _patched_cli(tmp: Path):
    """Point the CLI at synthetic paths (mirrors tests/test_ambivalence.py)."""
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    salience = tmp / "salience.json"
    interoception = tmp / "interoception.json"
    familiarity = tmp / "familiarity.json"
    ambivalence = tmp / "ambivalence.json"
    habits = tmp / "habits-formed.json"
    provenance = tmp / "provenance.json"
    proposals = tmp / "proposals"
    archive = tmp / "archive"
    saved = (cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY,
             cli.AMBIVALENCE, cli.HABITS, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE, cli._subject)
    cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY, cli.AMBIVALENCE, \
        cli.HABITS, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE = (
            db, inbox, salience, interoception, familiarity, ambivalence,
            habits, provenance, proposals, archive)
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(tmp / "salience.json"),
                              interoception_path=None,
                              familiarity_path=None,
                              ambivalence_path=str(ambivalence))

    cli._subject = make_subject
    return ambivalence, saved


def _restore_cli(saved):
    (cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY,
     cli.AMBIVALENCE, cli.HABITS, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE, cli._subject) = saved


# -- fitness 1: two-process replay -------------------------------------------

def test_two_process_replay_one_onset_one_offset():
    tmp = _tmp()
    # Process A: ticks 1-2 contested, same key, flushed.
    a = _make_subject(tmp)
    _tick(a, 1, _pin_contested, fear=0.78)  # margin 0.06
    _tick(a, 2, _pin_contested, fear=0.80)  # margin 0.08
    run = open_run(a)
    assert run is not None and run.onset_tick == 1 and run.last_tick == 2
    persisted = _sidecar(tmp)["open_run"]
    assert persisted["onset_tick"] == 1 and persisted["last_tick"] == 2, persisted
    del a  # "kill" process A (markers already flushed)

    # Process B: construction resumes the run; tick 3 extends, tick 4 closes.
    b = _make_subject(tmp)
    resumed = open_run(b)
    assert resumed is not None, "construction must resume the persisted run"
    assert (resumed.onset_tick, resumed.last_tick) == (1, 2), resumed.to_dict()
    _tick(b, 3, _pin_contested, fear=0.76)  # margin 0.04: extend, not re-onset
    assert open_run(b) is not None
    assert b.workspace.ambivalence_tracker.pending == 0, \
        "extension must not stage a second onset"
    _tick(b, 4, _pin_clear)
    assert open_run(b) is None

    markers = _markers(tmp)
    onsets = _onsets(markers)
    offsets = _offsets(markers)
    assert len(onsets) == 1, markers
    assert len(offsets) == 1, markers
    assert onsets[0]["tick"] == 1
    off = offsets[0]
    assert off["onset_tick"] == 1, off
    assert off["offset_tick"] == 4, off
    assert off["last_tick"] == 3, off
    assert off["margin_min"] == 0.04, off
    assert off["margin_max"] == 0.08, off
    assert off["reason"] == "clear", off
    assert off["contenders"] == ["fear", "thirst"], off
    # The offset names an onset_tick that exists in the stream: no orphan.
    assert off["onset_tick"] == onsets[0]["tick"]
    # No residue after close.
    assert _sidecar(tmp)["open_run"] is None


# -- fitness 2: one-tick run --------------------------------------------------

def test_one_tick_run_no_offset_no_residue():
    tmp = _tmp()
    s = _make_subject(tmp)
    _tick(s, 1, _pin_contested)
    assert _sidecar(tmp)["open_run"] is not None, \
        "the run is open after tick 1, persisted or not"
    _tick(s, 2, _pin_clear)
    markers = _markers(tmp)
    assert len(markers) == 1, markers
    assert markers[0].get("run") == "onset" and markers[0]["tick"] == 1
    assert _sidecar(tmp)["open_run"] is None, \
        "a one-tick close must leave no open_run residue"
    assert open_run(s) is None


# -- fitness 3: kill between persist and flush ---------------------------------

def test_kill_between_persist_and_flush_no_double_onset():
    tmp = _tmp()
    a = _make_subject(tmp)
    # Onset staged in pending, run persisted — then the process dies
    # BEFORE flush. The onset marker never reaches the sidecar file.
    _tick(a, 1, _pin_contested, flush=False)
    tracker_a = a.workspace.ambivalence_tracker
    assert tracker_a.pending == 1, "onset must be staged but unflushed"
    persisted = _sidecar(tmp)["open_run"]
    assert persisted is not None and persisted["onset_tick"] == 1, \
        "the run must be persisted even though the marker was not flushed"
    del a  # "kill": pending buffer dies with the process

    # New process: resume, then tick 1 re-runs (it was never committed).
    b = _make_subject(tmp)
    resumed = open_run(b)
    assert resumed is not None and resumed.onset_tick == 1
    tracker_b = b.workspace.ambivalence_tracker
    _tick(b, 1, _pin_contested, flush=False)
    assert tracker_b.pending == 0, \
        "re-running the uncommitted tick must EXTEND, never re-emit onset"
    assert open_run(b).onset_tick == 1 and open_run(b).last_tick == 1
    # Honest close: the run absorbed exactly one tick -> no offset due.
    _tick(b, 2, _pin_clear)
    data = _sidecar(tmp)
    assert data["markers"] == [], data
    assert data["open_run"] is None
    assert _onsets(data["markers"]) == [] and _offsets(data["markers"]) == []


# -- fitness 4: contender change across the boundary ----------------------------

def test_contender_change_across_boundary():
    tmp = _tmp()
    a = _make_subject(tmp)
    _tick(a, 1, _pin_contested)  # [fear, thirst] / [pressure, need]
    _tick(a, 2, _pin_contested)  # extend: multi-tick run
    del a

    b = _make_subject(tmp)
    assert open_run(b) is not None and open_run(b).onset_tick == 1
    _tick(b, 3, _pin_changed_key)  # [thirst, hunger] / [need, need]
    markers = _markers(tmp)
    onsets = _onsets(markers)
    offsets = _offsets(markers)
    assert len(onsets) == 2, markers
    assert len(offsets) == 1, markers
    off = offsets[0]
    assert off["reason"] == "changed", off
    assert off["onset_tick"] == 1 and off["offset_tick"] == 3 \
        and off["last_tick"] == 2, off
    assert off["contenders"] == ["fear", "thirst"], off
    assert off["onset_tick"] == onsets[0]["tick"], "no orphan offset"
    # The new run opened immediately after, onset staged for tick 3.
    assert onsets[1]["tick"] == 3 and onsets[1]["contenders"] == ["thirst", "hunger"]
    run = open_run(b)
    assert run is not None and tuple(run.contenders) == ("thirst", "hunger")
    persisted = _sidecar(tmp)["open_run"]
    assert persisted["contenders"] == ["thirst", "hunger"] and \
        persisted["onset_tick"] == 3, persisted


# -- fitness 5: 10-tick in-memory run unchanged ----------------------------------

def test_ten_tick_in_memory_run_unchanged():
    # The 2026-10-03 fitness: one onset + one offset, no boundary involved.
    tmp = _tmp()
    s = _make_subject(tmp)
    for t in range(1, 11):
        _tick(s, t, _pin_contested)
    run = open_run(s)
    assert run is not None and run.onset_tick == 1 and run.last_tick == 10
    assert run.absorbed == 10
    _tick(s, 11, _pin_clear)
    assert open_run(s) is None
    markers = _markers(tmp)
    onsets = _onsets(markers)
    offsets = _offsets(markers)
    assert len(onsets) == 1 and len(offsets) == 1, markers
    assert onsets[0]["tick"] == 1
    off = offsets[0]
    assert off["onset_tick"] == 1 and off["offset_tick"] == 11 \
        and off["last_tick"] == 10, off
    assert _sidecar(tmp)["open_run"] is None


# -- fitness 6: read-only + dream leave the sidecar byte-identical --------------

def test_readonly_and_dream_leave_sidecar_byte_identical():
    tmp = _tmp()
    ambivalence, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        subject.enqueue(Event("message", "tester", "open a run"))
        with subject._transaction():
            _pin_contested(subject)
        cli._run_tick(subject)  # onset marker + persisted open_run
        run = open_run(subject)
        assert run is not None
        onset_tick, last_tick = run.onset_tick, run.last_tick
        before = ambivalence.read_bytes()
        assert json.loads(before)["open_run"] is not None

        # Read-only sweep: each command constructs its own subject, which
        # resumes the run in memory — none may write the sidecar.
        cli.cmd_drift(argparse.Namespace(window=30))
        cli.cmd_status(argparse.Namespace(raw=False))
        assert ambivalence.read_bytes() == before, \
            "read-only commands must leave the sidecar byte-identical"

        # Dream tick: the _dreaming guard blocks the observer entirely.
        dreamer = cli._subject()
        dreamer.dream_tick()
        assert ambivalence.read_bytes() == before, \
            "dream ticks must leave the sidecar byte-identical"

        # The resumed run still extends honestly afterward: same key, next
        # tick -> extend, no second onset.
        nxt = cli._subject()
        resumed = open_run(nxt)
        assert resumed is not None and resumed.onset_tick == onset_tick
        tracker = nxt.workspace.ambivalence_tracker
        _tick(nxt, last_tick + 1, _pin_contested)
        assert tracker.pending == 0, \
            "the read-only sweep must not corrupt the resume path"
        assert open_run(nxt).last_tick == last_tick + 1
    finally:
        _restore_cli(saved)


# -- fitness 7: init --force wipes open_run --------------------------------------

def test_init_force_wipes_open_run():
    tmp = _tmp()
    ambivalence, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        subject.enqueue(Event("message", "tester", "open a run"))
        with subject._transaction():
            _pin_contested(subject)
        cli._run_tick(subject)
        assert json.loads(ambivalence.read_text())["open_run"] is not None
        cli.cmd_init(argparse.Namespace(force=True))
        assert not ambivalence.exists(), \
            "init --force must wipe open_run with the rest of the sidecar"
        fresh = cli._subject()
        assert open_run(fresh) is None
        assert fresh.workspace.ambivalence_tracker.open_run is None
    finally:
        _restore_cli(saved)


# -- adversarial battery --------------------------------------------------------

def test_open_run_serialization_round_trip():
    run = _OpenRun(contenders=["fear", "thirst"], channels=["pressure", "need"],
                   tick=7, margin=0.06123)
    run.extend(9, 0.04321)
    d = run.to_dict()
    assert d == {"contenders": ["fear", "thirst"],
                 "channels": ["pressure", "need"],
                 "onset_tick": 7, "last_tick": 9,
                 "margin_min": 0.0432, "margin_max": 0.0612}, d
    r2 = _OpenRun.from_dict(d)
    assert (r2.contenders, r2.channels) == (("fear", "thirst"), ("pressure", "need"))
    assert (r2.onset_tick, r2.last_tick) == (7, 9)
    assert (r2.margin_min, r2.margin_max) == (0.0432, 0.0612)
    # Determinism: no wall clock, rounded floats.
    assert run.to_dict() == run.to_dict()


def test_malformed_open_run_ignored_not_fabricated():
    bad_runs = [
        "not-a-dict",
        ["a", "b"],
        42,
        {"contenders": ["a", "b"]},  # missing keys
        {"contenders": "fear", "channels": ["pressure", "need"],
         "onset_tick": 1, "last_tick": 1, "margin_min": 0.05, "margin_max": 0.05},
        {"contenders": ["a", "b"], "channels": ["c", "d"],
         "onset_tick": True, "last_tick": 1, "margin_min": 0.05, "margin_max": 0.05},
        {"contenders": ["a", "b"], "channels": ["c", "d"],
         "onset_tick": 5, "last_tick": 3, "margin_min": 0.05, "margin_max": 0.05},
        {"contenders": ["a", "b"], "channels": ["c", "d"],
         "onset_tick": 1, "last_tick": 1, "margin_min": "big", "margin_max": 0.05},
    ]
    for bad in bad_runs:
        tmp = _tmp()
        (tmp / "ambivalence.json").write_text(
            json.dumps({"markers": [], "open_run": bad}), encoding="utf-8")
        s = _make_subject(tmp)  # must not raise
        assert open_run(s) is None, f"malformed run must not resume: {bad!r}"
        assert resume_open_run(s) is False
        # And a fresh contested tick opens a normal run afterward.
        _tick(s, 1, _pin_contested)
        assert open_run(s) is not None and open_run(s).onset_tick == 1
        assert len(_onsets(_markers(tmp))) == 1


def test_corrupt_sidecar_tolerated():
    tmp = _tmp()
    (tmp / "ambivalence.json").write_text("{not json", encoding="utf-8")
    s = _make_subject(tmp)  # must not raise (existing constructor discipline)
    _tick(s, 5, _pin_contested)
    markers = _markers(tmp)
    assert len(markers) == 1 and markers[0]["tick"] == 5, markers


def test_restore_does_not_clobber_live_run():
    # In-process _restore rebuilds the tracker on every transaction; the
    # persisted copy is never fresher than the live run, so resume must
    # be a no-op there — otherwise a restore mid-run would rewind it.
    tmp = _tmp()
    s = _make_subject(tmp)
    _tick(s, 1, _pin_contested)
    _tick(s, 2, _pin_contested)
    run_before = open_run(s)
    assert run_before.absorbed == 2
    with s._transaction():
        pass  # forces a _restore cycle
    assert open_run(s) is run_before, "resume must not touch a live run"
    _tick(s, 3, _pin_contested)
    assert open_run(s).absorbed == 3
    assert len(_onsets(_markers(tmp))) == 1, "no duplicate onset after restore"


def test_three_process_ping_pong():
    tmp = _tmp()
    a = _make_subject(tmp)
    _tick(a, 1, _pin_contested)
    _tick(a, 2, _pin_contested)
    del a
    b = _make_subject(tmp)
    assert open_run(b) is not None and open_run(b).onset_tick == 1
    _tick(b, 3, _pin_contested)
    _tick(b, 4, _pin_contested)
    del b
    c = _make_subject(tmp)
    assert open_run(c) is not None and open_run(c).last_tick == 4
    _tick(c, 5, _pin_clear)
    markers = _markers(tmp)
    onsets = _onsets(markers)
    offsets = _offsets(markers)
    assert len(onsets) == 1 and len(offsets) == 1, markers
    off = offsets[0]
    assert (off["onset_tick"], off["offset_tick"], off["last_tick"]) == (1, 5, 4)
    assert _sidecar(tmp)["open_run"] is None


def test_bypass_across_boundary_closes_run():
    tmp = _tmp()
    a = _make_subject(tmp)
    _tick(a, 1, _pin_contested)
    _tick(a, 2, _pin_contested)
    del a
    b = _make_subject(tmp)
    assert open_run(b) is not None
    # Apology events bypass the channel rule: the standing tie was not
    # observed this tick -> close with reason "bypass".
    action = _tick(b, 3, _pin_contested, kind="apology")
    assert action == Action.REPAIR, action
    markers = _markers(tmp)
    offsets = _offsets(markers)
    assert len(offsets) == 1 and offsets[0]["reason"] == "bypass", markers
    assert offsets[0]["onset_tick"] == 1 and offsets[0]["last_tick"] == 2
    assert open_run(b) is None
    assert _sidecar(tmp)["open_run"] is None


def test_failed_persist_keeps_live_run():
    # A persist write failure is swallowed by the observer wrapper; the
    # live in-memory run must survive, and the next _restore must not
    # clobber it with the (absent) persisted copy.
    tmp = _tmp()
    s = _make_subject(tmp)
    tracker = s.workspace.ambivalence_tracker
    real_save = tracker.save

    def flaky_save():
        raise OSError("simulated disk failure")

    tracker.save = flaky_save
    try:
        s.engine.state.tick = 1
        _pin_contested(s)
        s.engine.select_conduct(Event("message", "tester", "tick 1"))
    finally:
        tracker.save = real_save
    run = open_run(s)
    assert run is not None and run.onset_tick == 1, \
        "the live run must survive a failed persist"
    assert tracker.pending == 1, "the onset was still staged"
    with s._transaction():
        pass  # _restore: no persisted copy exists; live run must win
    assert open_run(s) is run
    # Recovery: the next change persists normally again.
    _tick(s, 2, _pin_contested)
    assert _sidecar(tmp)["open_run"]["onset_tick"] == 1


def test_cap_eviction_preserves_open_run():
    tmp = _tmp()
    s = _make_subject(tmp)
    _tick(s, 1, _pin_contested)  # opens the run, persists open_run
    tracker = s.workspace.ambivalence_tracker
    for i in range(AMBIVALENCE_CAP + 10):
        tracker.note({"tick": 100 + i, "contenders": ["a", "b"],
                      "channels": ["need", "need"], "margin": 0.01,
                      "winner": "a", "winning_channel": "need",
                      "event": "message"})
    assert tracker.flush() is True
    data = _sidecar(tmp)
    assert len(data["markers"]) == AMBIVALENCE_CAP
    # 75 markers staged (the tick-1 onset + 74 notes); the cap keeps the
    # last 64, so the first surviving note is tick 110.
    assert data["markers"][0]["tick"] == 110
    assert data["open_run"] is not None and data["open_run"]["onset_tick"] == 1, \
        "flushing past the cap must not drop the persisted run"


def test_tracker_reset_clears_open_run():
    tmp = _tmp()
    s = _make_subject(tmp)
    _tick(s, 1, _pin_contested)
    assert _sidecar(tmp)["open_run"] is not None
    s.workspace.ambivalence_tracker.reset()
    data = _sidecar(tmp)
    assert data["markers"] == [] and data["open_run"] is None, \
        "genome: reseed reset must clear the persisted run too"


def test_resume_is_pure_reattachment():
    # resume_open_run never writes the sidecar and is idempotent.
    tmp = _tmp()
    s = _make_subject(tmp)
    _tick(s, 1, _pin_contested)
    before = (tmp / "ambivalence.json").read_bytes()
    del s
    t = _make_subject(tmp)
    assert resume_open_run(t) is False, "already resumed at construction"
    assert (tmp / "ambivalence.json").read_bytes() == before
    assert open_run(t) is not None and open_run(t).onset_tick == 1


# -- round 2: producer-invariant shape validation (critic r1 hole) ----------

def _honest_run_dict():
    return {"contenders": ["fear", "thirst"], "channels": ["pressure", "need"],
            "onset_tick": 1, "last_tick": 3,
            "margin_min": 0.01, "margin_max": 0.05}


def test_from_dict_enforces_producer_invariants():
    # The producer (contested_marker -> _OpenRun -> to_dict) can only ever
    # emit exactly-2 string tuples, margin_min <= margin_max, and ticks
    # >= 0 (round() is monotone, extend keeps min/max ordered). Any other
    # shape is hand-edited or bit-rotted input and must raise so the
    # resume path ignores it — a missed resume is the safe direction.
    import pytest
    bad = []
    for mutation in [
        # arity: empty, arity-1, arity-3
        ({"contenders": []},),
        ({"channels": []},),
        ({"contenders": ["a"]},),
        ({"contenders": ["a", "b", "c"]},),
        ({"channels": ["a", "b", "c"]},),
        ({"contenders": ("fear", "thirst"), "channels": ["pressure"]},),
        # non-string element
        ({"contenders": ["fear", 7]},),
        # inverted margins
        ({"margin_min": 0.09, "margin_max": 0.01},),
        ({"margin_min": 0.05, "margin_max": 0.0499},),
        # negative ticks
        ({"onset_tick": -5, "last_tick": -1},),
        ({"onset_tick": -1, "last_tick": 3},),
        # bool margins / ticks-as-bool
        ({"margin_min": True, "margin_max": 0.05},),
    ]:
        d = _honest_run_dict()
        d.update(mutation[0])
        bad.append(d)
    for d in bad:
        with pytest.raises((KeyError, TypeError, ValueError)):
            _OpenRun.from_dict(d)


def test_malformed_shapes_ignored_at_resume_no_orphan_offset():
    # Behavioral half: a malformed persisted run is ignored at resume,
    # the next contested tick opens a fresh run, and the stream can never
    # carry an orphan offset (offset naming an onset_tick with no onset
    # marker) or a margin-inverted offset from the malformed input.
    shapes = [
        {**_honest_run_dict(), "contenders": [], "channels": []},
        {**_honest_run_dict(), "contenders": ["a", "b", "c"]},
        {**_honest_run_dict(), "margin_min": 0.09, "margin_max": 0.01},
        {**_honest_run_dict(), "onset_tick": -5, "last_tick": -1},
    ]
    for bad in shapes:
        tmp = _tmp()
        (tmp / "ambivalence.json").write_text(
            json.dumps({"markers": [], "open_run": bad}), encoding="utf-8")
        s = _make_subject(tmp)
        assert open_run(s) is None, f"malformed run must be ignored: {bad!r}"
        assert resume_open_run(s) is False
        # Fresh contested tick opens a fresh run from THIS tick's onset.
        _tick(s, 10, _pin_contested)
        run = open_run(s)
        assert run is not None and run.onset_tick == 10
        markers = _markers(tmp)
        onsets = _onsets(markers)
        assert len(onsets) == 1 and onsets[0]["tick"] == 10, markers
        # No offsets at all yet: nothing to be an orphan of.
        assert not [m for m in markers if m.get("run") == "offset"]
