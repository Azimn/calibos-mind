"""Tests for contested-margin ambivalence trace markers.

Spec: the builder-task SPEC (2026-10-01) — calibos_mind/ambivalence.py.
All fixtures live in /tmp — the live store is never touched.

Fitness functions under test:
  1. Near-tie scenarios (cross-channel within the 0.12 deadband; top-two
     within-channel within the margin) -> markers naming the pair, the
     margin, and the winning channel.
  2. Clear-margin scenarios -> no marker, no sidecar write.
  3. Scripted sequences produce engine action selection byte-identical to
     pristine (A/B: same script, observer installed vs not installed).
  4. Determinism: identical sequences -> byte-identical sidecar bytes.
  5. Full suite green (run separately).

Scenario control: the tests set engine.state.needs / pressures /
pressure_baselines / concerns directly, then call
engine.select_conduct() — the same selector the heartbeat uses — so the
wrapper observes the exact triage lists the frozen engine computed.
Urgency recap (digital_subject/engine.py, jelly_psiduck-0.2.0a2):
  needs: thirst/hunger/pain/fatigue/loneliness -> urgency = value;
         energy/comfort/warmth/safety/focus/satisfaction -> 1 - value.
  pressures: magnitude = |value - baseline|.
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
    CHANNEL_RULE_BYPASS_KINDS,
    CONTESTED_MARGIN,
    AmbivalenceTracker,
    contested_marker,
    install_observer,
    open_run,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Action, Event


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="amb-test-"))


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


def _set_state(subject, needs, pressures, baselines):
    """Pin the engine's motive state; returns nothing.

    Concerns are cleared so the triage lists are fully determined by the
    arguments (the frozen engine extends _triage_pressures with concerns).
    """
    st = subject.engine.state
    st.needs = dict(needs)
    st.pressures = dict(pressures)
    st.pressure_baselines = dict(baselines)
    st.concerns = {}


def _select(subject, kind="message"):
    """Run one intention selection through the observed engine path."""
    action = subject.engine.select_conduct(
        Event(kind, "tester", "a test event worth noticing"))
    tracker = subject.workspace.ambivalence_tracker
    tracker.flush()
    return action


def _sidecar(tmp: Path):
    p = tmp / "ambivalence.json"
    return json.loads(p.read_text(encoding="utf-8"))["markers"] if p.exists() else None


# -- fitness 1: near-tie scenarios emit markers ------------------------------

def test_cross_channel_near_tie_emits_marker():
    tmp = _tmp()
    subject = _make_subject(tmp)
    # fear 0.78 vs thirst 0.72: margin 0.06 < 0.12 -> contested.
    # Engine rule: 0.78 >= 0.72 + 0.12 is False -> need channel wins.
    _set_state(subject,
               needs={"thirst": 0.72, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.78, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.5})
    _select(subject)
    markers = _sidecar(tmp)
    assert markers is not None and len(markers) == 1, markers
    m = markers[0]
    assert m["contenders"] == ["fear", "thirst"], m
    assert m["channels"] == ["pressure", "need"], m
    assert m["margin"] == round(0.78 - 0.72, 4) == 0.06, m
    assert m["winner"] == "thirst", m
    assert m["winning_channel"] == "need", m
    assert m["event"] == "message", m
    assert isinstance(m["tick"], int), m


def test_clear_margin_pressure_win_no_marker():
    # Margin 0.23 >= 0.12: pressure wins uncontested -> no marker, and the
    # sidecar file is never even created (no-op write discipline).
    tmp = _tmp()
    subject = _make_subject(tmp)
    _set_state(subject,
               needs={"thirst": 0.72, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.95, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.5})
    _select(subject)
    assert _sidecar(tmp) is None  # clear margin: no marker, no file
    tracker = subject.workspace.ambivalence_tracker
    assert tracker.pending == 0


def test_within_channel_need_tie_emits_marker():
    tmp = _tmp()
    subject = _make_subject(tmp)
    # thirst 0.72 vs hunger 0.70: gap 0.02 < 0.12 -> contested within the
    # need channel, even though the cross-channel margin is wide.
    _set_state(subject,
               needs={"thirst": 0.72, "hunger": 0.70, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.10, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.5})
    _select(subject)
    markers = _sidecar(tmp)
    assert markers is not None and len(markers) == 1, markers
    m = markers[0]
    assert m["contenders"] == ["thirst", "hunger"], m
    assert m["channels"] == ["need", "need"], m
    assert m["margin"] == 0.02, m
    assert m["winner"] == "thirst", m
    assert m["winning_channel"] == "need", m  # 0.10 >= 0.72+0.12 False


def test_within_channel_pressure_tie_emits_marker():
    tmp = _tmp()
    subject = _make_subject(tmp)
    # fear 0.60 vs arousal 0.55: gap 0.05 < 0.12 -> contested within the
    # pressure channel; cross margin 0.60-0.20 = 0.40 -> pressure wins.
    _set_state(subject,
               needs={"thirst": 0.20, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.60, "arousal": 0.55, "trust": 0.5},
               baselines={"fear": 0.0, "arousal": 0.0, "trust": 0.5})
    _select(subject)
    markers = _sidecar(tmp)
    assert markers is not None and len(markers) == 1, markers
    m = markers[0]
    assert m["contenders"] == ["fear", "arousal"], m
    assert m["channels"] == ["pressure", "pressure"], m
    assert m["margin"] == 0.05, m
    assert m["winner"] == "fear", m
    assert m["winning_channel"] == "pressure", m  # 0.60 >= 0.20+0.12


def test_contested_marker_pure_function_boundaries():
    # |margin| = 0.11 -> contested; 0.13 -> clear. (Exact 0.12 is float-
    # fragile, so the tests sit clearly on either side of the deadband.)
    m = contested_marker(tick=1, event_kind="message",
                         needs=[("thirst", 0.72)], pressures=[("fear", 0.83)])
    assert m is not None and m["margin"] == 0.11, m
    m = contested_marker(tick=1, event_kind="message",
                         needs=[("thirst", 0.72)], pressures=[("fear", 0.85)])
    assert m is None, m
    # Empty lists fall back to the engine's own defaults (("trust", 0.0)
    # vs ("curiosity", 0.0)): margin 0 -> contested, need wins.
    m = contested_marker(tick=1, event_kind="message", needs=[], pressures=[])
    assert m is not None, m
    assert m["contenders"] == ["trust", "curiosity"], m
    assert m["winning_channel"] == "need", m


# -- fitness 2: clear margins -> no marker, no write -------------------------

def test_clear_margin_writes_nothing():
    tmp = _tmp()
    subject = _make_subject(tmp)
    tracker = subject.workspace.ambivalence_tracker
    assert tracker.flush() is False  # no-op write discipline
    assert not (tmp / "ambivalence.json").exists()
    # Cross margin -0.40 (need wins uncontested); pressure magnitudes
    # 0.50 vs 0.10 (gap 0.40, no tie); need urgencies 0.90 vs 0.05.
    _set_state(subject,
               needs={"thirst": 0.90, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.10, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.0})
    _select(subject)
    assert _sidecar(tmp) is None
    assert tracker.pending == 0


# -- fitness 3: zero behavior change ------------------------------------------

def _scripted_observations(tmp: Path, with_ambivalence: bool):
    """Run a fixed script; return the engine's observable behavior stream.

    A and B differ ONLY in whether the observer is installed. Identical
    streams prove the wrapper never perturbs action selection.
    """
    subject = _make_subject(tmp, with_ambivalence=with_ambivalence)
    obs = []
    for i in range(3):
        subject.enqueue(Event("message", f"tester-{i}",
                              f"scripted observation number {i}"))
        result = subject.heartbeat()
        tracker = getattr(subject.workspace, "ambivalence_tracker", None)
        if tracker is not None:
            tracker.flush()
        trace = subject.inspect()["trace"]
        obs.append({
            "action": result["action"],
            "intention": subject.engine.state.last_intention.value,
            "trace": [(t["kind"], t.get("action")) for t in trace],
            "needs": sorted((k, round(v, 4))
                            for k, v in subject.engine.state.needs.items()),
            "pressures": sorted((k, round(v, 4))
                                for k, v in subject.engine.state.pressures.items()),
        })
    return obs


def test_action_selection_byte_identical_to_pristine():
    obs_a = _scripted_observations(_tmp(), with_ambivalence=True)
    obs_b = _scripted_observations(_tmp(), with_ambivalence=False)
    assert obs_a == obs_b
    # And the script actually did something (guard against a vacuous pass):
    assert len(obs_a) == 3
    assert any(t[0] == "heartbeat" for t in obs_a[0]["trace"])


def test_observer_survives_restore_without_double_counting():
    # _restore swaps in a fresh engine on every transaction; the observer
    # must be re-installed (still exactly one wrapper -> one observation
    # per selection). Under standing-tie consolidation the two same-tick
    # selections below extend a single open run: exactly one onset marker,
    # no double-noting, and no duplicate onset after the restore cycle.
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        pass  # forces a _restore cycle
    assert getattr(subject.engine._choose_intention,
                   "_ambivalence_wrapped", False) is True
    _set_state(subject,
               needs={"thirst": 0.72, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.78, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.5})
    subject.engine.select_conduct(Event("message", "tester", "hello"))
    tracker = subject.workspace.ambivalence_tracker
    assert tracker.pending == 1, "double-wrapped observer would note twice"
    install_observer(subject)  # explicit re-install is a no-op
    subject.engine.select_conduct(Event("message", "tester", "hello again"))
    # Same tick, same standing tie: the run extends instead of noting
    # again — still exactly one staged marker.
    assert tracker.pending == 1
    run = open_run(subject)
    assert run is not None and run.absorbed == 2
    # A later tick extends the same run; a clear margin then closes it
    # with exactly one offset marker (onset + offset for a multi-tick run).
    subject.engine.state.tick += 1
    subject.engine.select_conduct(Event("message", "tester", "third"))
    assert tracker.pending == 1
    _set_state(subject,
               needs={"thirst": 0.90, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.10, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.0})
    subject.engine.state.tick += 1
    subject.engine.select_conduct(Event("message", "tester", "clear"))
    assert tracker.pending == 2, "expected onset + offset for the closed run"
    assert open_run(subject) is None


def test_dream_path_notes_nothing():
    tmp = _tmp()
    subject = _make_subject(tmp)
    subject.dream_tick()
    tracker = subject.workspace.ambivalence_tracker
    assert tracker.pending == 0
    assert not (tmp / "ambivalence.json").exists()


def test_apology_bypass_notes_nothing():
    # Regression (critic round 2): the frozen engine bypasses the channel
    # rule entirely for apology events (engine.py:337-338 ->
    # Action.REPAIR). No channel selection occurred, so a near-tie in the
    # triage lists must not produce a marker — a "winning channel" there
    # would be fabricated from a rule the engine never ran. Use the same
    # contested state as test_cross_channel_near_tie_emits_marker, so the
    # marker would definitely have been emitted without the bypass guard.
    tmp = _tmp()
    subject = _make_subject(tmp)
    _set_state(subject,
               needs={"thirst": 0.72, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.78, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.5})
    action = _select(subject, kind="apology")
    assert action == Action.REPAIR, action
    tracker = subject.workspace.ambivalence_tracker
    assert tracker.pending == 0
    assert not (tmp / "ambivalence.json").exists()


# -- fitness 4: determinism ----------------------------------------------------

def test_identical_sequences_byte_identical_sidecar():
    def _run():
        tmp = _tmp()
        subject = _make_subject(tmp)
        for fear in (0.78, 0.95, 0.78):
            _set_state(subject,
                       needs={"thirst": 0.72, "hunger": 0.05,
                              "energy": 0.95, "comfort": 0.95},
                       pressures={"fear": fear, "trust": 0.5},
                       baselines={"fear": 0.0, "trust": 0.5})
            _select(subject)
        return (tmp / "ambivalence.json").read_bytes()

    assert _run() == _run()


def test_sidecar_capped():
    tmp = _tmp()
    tracker = AmbivalenceTracker(tmp / "ambivalence.json")
    for i in range(AMBIVALENCE_CAP + 10):
        tracker.note({"tick": i, "contenders": ["a", "b"], "margin": 0.01,
                      "winner": "a", "winning_channel": "need",
                      "channels": ["need", "need"], "event": "message"})
    assert tracker.flush() is True
    markers = _sidecar(tmp)
    assert len(markers) == AMBIVALENCE_CAP, len(markers)
    assert markers[0]["tick"] == 10  # oldest evicted, newest kept
    assert markers[-1]["tick"] == AMBIVALENCE_CAP + 9


# -- genome: reseed reset, read-only discipline --------------------------------

def _patched_cli(tmp: Path):
    """Point the CLI at synthetic paths, including the ambivalence sidecar.

    Redirects the complete module-level path set cmd_init touches (2026-10-02):
    a helper that leaves SALIENCE/INTEROCEPTION/FAMILIARITY/PROPOSALS/ARCHIVE
    pointed at the live checkout lets cmd_init reset or wipe live mind state.
    Mirrors tests/test_init.py's nine-path tuple.
    """
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


def test_init_force_wipes_sidecar():
    tmp = _tmp()
    ambivalence, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        subject.enqueue(Event("message", "tester", "reseed me"))
        # Commit the near-tie through a transaction so the heartbeat's
        # _restore sees it (bare in-memory staging would be discarded).
        with subject._transaction():
            _set_state(subject,
                       needs={"thirst": 0.72, "hunger": 0.05,
                              "energy": 0.95, "comfort": 0.95},
                       pressures={"fear": 0.78, "trust": 0.5},
                       baselines={"fear": 0.0, "trust": 0.5})
        # Route through the real wiring: heartbeat + _run_tick flush.
        cli._run_tick(subject)
        assert ambivalence.exists(), "wiring must flush markers on ticks"
        assert len(json.loads(ambivalence.read_text(encoding="utf-8"))["markers"]) >= 1
        cli.cmd_init(argparse.Namespace(force=True))
        assert not ambivalence.exists(), \
            "init --force must wipe the ambivalence sidecar (genome: reseed reset)"
    finally:
        _restore_cli(saved)


def test_readonly_commands_do_not_write_sidecar():
    tmp = _tmp()
    ambivalence, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        assert not ambivalence.exists()
        cli.cmd_drift(argparse.Namespace(window=30))
        cli.cmd_status(argparse.Namespace(raw=False))
        assert not ambivalence.exists(), \
            "read-only commands must not create the sidecar"
    finally:
        _restore_cli(saved)


def test_run_tick_wiring_flushes_through_cli():
    # End-to-end through cli._run_tick: stage a near-tie, commit it through
    # a transaction (a bare in-memory stage would be discarded by the
    # heartbeat's _restore), tick, and the marker must land in the sidecar.
    tmp = _tmp()
    ambivalence, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        subject.enqueue(Event("message", "tester", "wired tick"))
        with subject._transaction():
            _set_state(subject,
                       needs={"thirst": 0.72, "hunger": 0.05,
                              "energy": 0.95, "comfort": 0.95},
                       pressures={"fear": 0.78, "trust": 0.5},
                       baselines={"fear": 0.0, "trust": 0.5})
        cli._run_tick(subject)
        markers = json.loads(ambivalence.read_text(encoding="utf-8"))["markers"]
        assert len(markers) >= 1, markers
        assert markers[-1]["contenders"] == ["fear", "thirst"], markers[-1]
        assert markers[-1]["winning_channel"] == "need", markers[-1]
        assert abs(markers[-1]["margin"]) < CONTESTED_MARGIN, markers[-1]
    finally:
        _restore_cli(saved)


# -- critic round-2 regression coverage (folded in on adjudication) ------------

def test_dreaming_guard_blocks_direct_selection():
    # _sleep_tick never calls select_conduct, so test_dream_path_notes_nothing
    # never exercises the wrapper's _dreaming guard. Force it directly.
    tmp = _tmp()
    subject = _make_subject(tmp)
    _set_state(subject,
               needs={"thirst": 0.72, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.78, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.5})
    tracker = subject.workspace.ambivalence_tracker

    subject._dreaming = True
    try:
        subject.engine.select_conduct(Event("message", "tester", "dream input"))
    finally:
        subject._dreaming = False
    assert tracker.pending == 0, "_dreaming guard failed to block noting"
    tracker.flush()
    assert _sidecar(tmp) is None

    # And the guard is the only thing that blocked it:
    _select(subject)
    assert _sidecar(tmp) is not None and len(_sidecar(tmp)) == 1


def test_engine_premises_topk_and_sort_order():
    # Within-channel detection needs top_k >= 2 and descending triage lists;
    # if the frozen engine ever violated these, the within-channel tests
    # would pass vacuously (no second contender -> no marker ever).
    tmp = _tmp()
    subject = _make_subject(tmp)
    assert subject.engine.top_k >= 2, subject.engine.top_k
    _set_state(subject,
               needs={"thirst": 0.72, "hunger": 0.05, "energy": 0.95, "comfort": 0.95},
               pressures={"fear": 0.78, "trust": 0.5},
               baselines={"fear": 0.0, "trust": 0.5})
    needs = subject.engine._triage_needs()
    pressures = subject.engine._triage_pressures()
    assert len(needs) >= 2 and len(pressures) >= 2
    assert all(a[1] >= b[1] for a, b in zip(needs, needs[1:])), needs
    assert all(a[1] >= b[1] for a, b in zip(pressures, pressures[1:])), pressures
    # And the engine's own channel rule really is p >= n + 0.12:
    assert needs[0][1] < pressures[0][1] < needs[0][1] + 0.12  # contested band
    assert subject.engine.select_conduct(
        Event("message", "tester", "x")) is not None
