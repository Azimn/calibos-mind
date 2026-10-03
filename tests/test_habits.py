"""Tests for conduct-chasing habit formation.

Spec: the builder-task SPEC (2026-10-02) — calibos_mind/habits.py.
All fixtures live in /tmp — the live store is never touched.

Fitness functions under test:
  1. Pristine-behavior parity: no qualifying repetition -> no formed
     habits, action sequence byte-identical to the pristine engine.
  2. Crystallization: >= 3 co-fires in the window with non-worsening
     trigger need -> formed:<trigger>:<action> at 0.45 in state.habits
     and the sidecar, surviving the payload INSERT OR REPLACE.
  3. Growth/cap: continued co-fires raise strength; never above 0.70.
  4. Automaticity: at strength >= 0.65 the formed habit fires
     within-channel — the precise observable is last_used_tick == tick
     (only the habit-fire branch writes it), plus an action-stream
     demonstration where the habit visibly bypasses the deliberated
     alternative (withdraw chosen where deliberation would say conceal,
     proven by a twin control).
  5. Disuse/removal: trigger-dominant ticks with other actions decay
     strength; below 0.30 the habit leaves state.habits with a written
     archival note in the sidecar.
  6. Authored habits byte-unchanged by all formation activity.
  7. Determinism: identical scripts -> byte-identical sidecar.
  8. Dream isolation: dream ticks leave the sidecar byte-identical.
  9. Read-only commands (drift, status) leave the sidecar byte-identical.
 10. init --force: sidecar wiped, formed habits removed, authored restored.
 11. Full suite green (run separately).

Scenario control: the tests pin engine.state.needs / pressures /
pressure_baselines directly (concerns cleared), commit through a
transaction, then run cli._run_tick — the real wiring under test.
Measured premises (probed 2026-10-02 against the frozen install):
- curiosity 0.80 dominant, pressures low -> action "explore",
  curiosity delta ~= -0.0238/tick (explore activity -0.025 +
  homeostasis +0.001), deterministic across runs.
- fear 0.95 (baseline 0.0) vs top need 0.80 -> pressure channel wins
  (0.95 >= 0.80 + 0.12) -> action "withdraw" (PRESSURE_ACTIONS["fear"][0]);
  with trust < 0.25 the deliberated action is "conceal" instead.
- hunger 0.80 dominant -> action "seek_contact", hunger delta ~= +0.002
  (homeostasis only; the seek_contact activity touches loneliness /
  restlessness, not hunger) -> the formation delta gate blocks.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.habits import (
    ARCHIVE_CAP,
    CHANNEL_DEADBAND,
    CRYSTALLIZE_STRENGTH,
    DECAY_STEP,
    FORMATION_WINDOW,
    FORMED_PREFIX,
    GROWTH_CAP,
    GROWTH_STEP,
    REMOVAL_FLOOR,
    REMOVAL_REASON,
    HabitFormationTracker,
    install_habit_observer,
    resolve_trigger,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Event


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="habits-test-"))


def _make_subject(tmp: Path, with_habits: bool = True) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        interoception_path=None,
        familiarity_path=None,
        ambivalence_path=None,
        habits_path=(str(tmp / "habits-formed.json") if with_habits else None),
    )


def _needs(**over):
    """Full need dict; override the dominant need per tick.

    Competitors stay low so the chosen need dominates by a wide margin
    (urgency recap: thirst/hunger/pain/fatigue/loneliness/restlessness/
    curiosity -> urgency = value; energy/comfort/warmth/safety/focus/
    satisfaction -> urgency = 1 - value).
    """
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


def _sidecar(tmp: Path):
    p = tmp / "habits-formed.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _formed_keys(subject):
    return {k for k in subject.engine.state.habits if k.startswith(FORMED_PREFIX)}


def _authored_snapshot(subject):
    return {k: (h.trigger, h.action.value, h.strength, h.cooldown)
            for k, h in subject.engine.state.habits.items()
            if not k.startswith(FORMED_PREFIX)}


# -- pure function: channel-rule replication --------------------------------

def test_resolve_trigger_replicates_engine_channel_rule():
    # Need channel: pressure below the deadband.
    trigger, dom = resolve_trigger([("curiosity", 0.80)], [("fear", 0.10)])
    assert (trigger, dom) == ("curiosity", "curiosity")
    # Pressure channel: p >= n + 0.12 wins.
    trigger, dom = resolve_trigger([("curiosity", 0.80)], [("fear", 0.95)])
    assert trigger == "fear" and dom == "curiosity", (trigger, dom)
    # Boundary: exactly at the deadband the pressure wins (engine: >=).
    trigger, _ = resolve_trigger([("curiosity", 0.80)], [("fear", 0.80 + CHANNEL_DEADBAND)])
    assert trigger == "fear", trigger
    # Just inside: need wins.
    trigger, _ = resolve_trigger([("curiosity", 0.80)], [("fear", 0.80 + CHANNEL_DEADBAND - 0.01)])
    assert trigger == "curiosity", trigger
    # Concern spelling is stripped to the engine's own option-lookup key.
    trigger, _ = resolve_trigger([("curiosity", 0.10)], [("concern:deadline-x", 0.90)])
    assert trigger == "deadline-x", trigger
    # Empty triage falls back to the engine's own defaults.
    trigger, dom = resolve_trigger([], [])
    assert (trigger, dom) == ("curiosity", "curiosity"), (trigger, dom)


# -- fitness 1: pristine-behavior parity --------------------------------------

def test_no_qualifying_repetition_no_formation_and_parity():
    """Six ticks, six distinct (trigger, action) pairs: no pair reaches
    the 3-co-fire threshold, so nothing forms — and the wired engine
    behaves byte-identically to the pristine one."""
    scripts = [
        (_needs(curiosity=0.80), "explore"),
        (_needs(thirst=0.80), "seek_contact"),
        (_needs(hunger=0.80), "seek_contact"),
        (_needs(fatigue=0.80), "rest"),
        (_needs(restlessness=0.80), "explore"),
        (_needs(loneliness=0.80), "seek_contact"),
    ]

    def _run(with_habits):
        tmp = _tmp()
        subject = _make_subject(tmp, with_habits=with_habits)
        actions = []
        for needs, expected in scripts:
            result = _script_tick(subject, needs, _pressures(), _baselines())
            actions.append(result["action"])
            assert result["action"] == expected, (needs, result["action"])
        trace = subject.inspect()["trace"]
        return tmp, subject, actions, [(t["kind"], t.get("action")) for t in trace]

    tmp_a, subj_a, actions_a, trace_a = _run(True)
    tmp_b, subj_b, actions_b, trace_b = _run(False)

    assert actions_a == actions_b
    assert trace_a == trace_b
    assert _formed_keys(subj_a) == set()
    assert _sidecar(tmp_a)["formed"] == {}
    assert _formed_keys(subj_b) == set()
    # Authored habits fired or not, formation touched nothing:
    assert _authored_snapshot(subj_a) == _authored_snapshot(subj_b)


# -- fitness 2: crystallization -----------------------------------------------

def test_crystallization():
    tmp = _tmp()
    subject = _make_subject(tmp)
    for _ in range(3):
        result = _script_tick(subject, _needs(curiosity=0.80),
                              _pressures(), _baselines())
        assert result["action"] == "explore"

    key = "formed:curiosity:explore"
    habits = subject.engine.state.habits
    assert key in habits, sorted(habits)
    h = habits[key]
    assert h.trigger == "curiosity"
    assert h.action.value == "explore"
    assert h.strength == CRYSTALLIZE_STRENGTH == 0.45
    assert h.cooldown == 2

    sc = _sidecar(tmp)
    assert sc["formed"][key]["strength"] == 0.45
    assert sc["formed"][key]["peak"] == 0.45
    assert sc["formed"][key]["created_tick"] == 3
    assert [w["trigger"] for w in sc["window"]] == ["curiosity"] * 3
    assert all(w["action"] == "explore" for w in sc["window"])
    # The formation delta gate saw a non-worsening need:
    assert all(w["delta"] <= 0 for w in sc["window"]), sc["window"]

    # The wrapper owns state: the formed habit survives the payload
    # INSERT OR REPLACE — a fresh subject on the same DB sees it.
    fresh = _make_subject(tmp)
    assert key in fresh.engine.state.habits
    assert fresh.engine.state.habits[key].strength == 0.45


def test_formation_delta_gate_blocks_worsening_need():
    """Three (hunger, seek_contact) co-fires, but hunger's per-tick delta
    is positive (homeostasis +0.002, the activity doesn't touch hunger) —
    the behavior IS making its driving need worse, so nothing forms."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    for _ in range(3):
        result = _script_tick(subject, _needs(hunger=0.80),
                              _pressures(), _baselines())
        assert result["action"] == "seek_contact"
    sc = _sidecar(tmp)
    assert len(sc["window"]) == 3
    assert all(w["delta"] > 0 for w in sc["window"]), sc["window"]
    assert sc["formed"] == {}
    assert _formed_keys(subject) == set()


# -- fitness 3: growth and cap --------------------------------------------------

def test_growth_and_cap():
    tmp = _tmp()
    subject = _make_subject(tmp)
    key = "formed:curiosity:explore"
    strengths = []
    for _ in range(16):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(), _baselines())
        h = subject.engine.state.habits.get(key)
        strengths.append(h.strength if h else None)
        if h:
            assert h.strength <= GROWTH_CAP, h.strength
    # Ticks 1-2: no habit yet (needs 3 co-fires). Tick 3: crystallize at
    # 0.45. Then +0.02/co-fire: 0.45 + 13*0.02 = 0.71 -> capped at 0.70.
    assert strengths[0] is None and strengths[1] is None
    assert strengths[2] == 0.45
    assert strengths[3] == round(0.45 + GROWTH_STEP, 6)
    assert strengths[-1] == GROWTH_CAP == 0.70
    assert max(s for s in strengths if s is not None) == 0.70
    sc = _sidecar(tmp)
    assert sc["formed"][key]["peak"] == 0.70
    # The formed (curiosity -> explore) habit never fires: the authored
    # curious_question (0.72) outranks it in the engine's max-strength
    # match — the spec's "authored habits define identity", and the reason
    # the growth cap sits deliberately below 0.72.
    assert subject.engine.state.habits[key].last_used_tick == -10_000


# -- fitness 4: automaticity -----------------------------------------------------

def _fear_script():
    """Shared script: fear-dominant ticks with energy as the dominant need.

    fear 0.95 beats energy-urgency 0.80 by the 0.12 deadband, so the
    pressure channel wins and the engine chooses withdraw
    (PRESSURE_ACTIONS["fear"][0]). Energy falls ~0.0021/tick by
    homeostasis (no withdraw activity exists), so the formation delta
    gate (mean <= 0) passes. No authored habit has trigger "fear", so a
    formed (fear -> withdraw) habit is the engine's max-strength match
    and can genuinely fire — unlike (curiosity -> explore), which the
    authored curious_question (0.72) always outranks.
    """
    return (_needs(energy=0.20), _pressures(fear=0.95, trust=0.5),
            _baselines(fear=0.0))


def test_automaticity_last_used_tick():
    """At strength >= 0.65 the engine's within-channel fire rule stamps
    last_used_tick — the precise observable that the habit (not the
    deliberated path) chose the action. The deliberated path never writes
    last_used_tick."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    # Ticks 1-3: crystallize at 0.45. Ticks 4-13: +0.02 each -> 0.65.
    for _ in range(13):
        result = _script_tick(subject, needs, pressures, baselines)
        assert result["action"] == "withdraw", result["action"]
    assert subject.engine.state.habits[key].strength == 0.65
    assert subject.engine.state.habits[key].last_used_tick == -10_000
    # Tick 14: the engine sees 0.65 at selection time and fires within-channel.
    result = _script_tick(subject, needs, pressures, baselines)
    assert result["action"] == "withdraw"
    h = subject.engine.state.habits[key]
    assert h.last_used_tick == result["tick"] == 14, \
        (h.last_used_tick, result["tick"])
    # And the stamp survives the payload round-trip.
    fresh = _make_subject(tmp)
    assert fresh.engine.state.habits[key].last_used_tick == 14


def test_automaticity_bypasses_deliberated_alternative():
    """Stronger demonstration: a formed (fear -> withdraw) habit at 0.65
    visibly overrides the deliberated choice. With trust < 0.25 the
    engine's own rule would choose CONCEAL (trust override); the firing
    habit returns WITHDRAW instead. A twin subject with the same staged
    state but no habit proves the deliberated alternative is conceal."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    for _ in range(13):
        result = _script_tick(subject, needs, pressures, baselines)
        assert result["action"] == "withdraw", result["action"]
    assert subject.engine.state.habits[key].strength == 0.65

    # The preemption tick: trust collapses below 0.25.
    low_trust = _pressures(fear=0.95, trust=0.10)
    result = _script_tick(subject, needs, low_trust, baselines)
    assert result["action"] == "withdraw", result["action"]
    h = subject.engine.state.habits[key]
    assert h.last_used_tick == result["tick"]

    # Twin control: identical staged state, no formed habit -> conceal.
    twin_tmp = _tmp()
    twin = _make_subject(twin_tmp)
    with twin._transaction():
        _set_state(twin, needs, low_trust, baselines)
    assert twin.engine.select_conduct().value == "conceal"


# -- fitness 5: disuse decay and removal ------------------------------------------

def test_disuse_decay_and_removal_with_archive():
    """Form (fear -> withdraw), then fear-dominant ticks choosing conceal
    (trust < 0.25): -0.05/tick. At 0.25 < 0.30 the habit is removed from
    state.habits with a written archival note. The (fear, conceal) pair
    itself must NOT form: hunger's delta is positive there (delta gate)."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    key = "formed:fear:withdraw"
    needs, pressures, baselines = _fear_script()
    for _ in range(3):
        result = _script_tick(subject, needs, pressures, baselines)
        assert result["action"] == "withdraw", result["action"]
    assert subject.engine.state.habits[key].strength == 0.45

    low_trust = _pressures(fear=0.95, trust=0.10)
    expected = [0.40, 0.35, 0.30]
    for i, want in enumerate(expected):
        result = _script_tick(subject, _needs(hunger=0.80),
                              low_trust, baselines)
        assert result["action"] == "conceal", result["action"]
        got = subject.engine.state.habits[key].strength
        assert got == want, (i, got)
    assert key in subject.engine.state.habits  # 0.30 is not < 0.30

    # Fourth decay tick: 0.25 < 0.30 -> removed + archived.
    result = _script_tick(subject, _needs(hunger=0.80), low_trust, baselines)
    assert key not in subject.engine.state.habits
    sc = _sidecar(tmp)
    assert key not in sc["formed"]
    assert len(sc["archived"]) == 1, sc["archived"]
    note = sc["archived"][0]
    assert note["key"] == key
    assert note["peak"] == 0.45
    assert note["reason"] == REMOVAL_REASON
    assert note["lifespan_ticks"] == note["archived_tick"] - 3  # created tick 3
    assert note["archived_tick"] == result["tick"]
    # The (fear, conceal) pair co-fired 4x but never formed (delta gate):
    assert "formed:fear:conceal" not in sc["formed"]
    # Removal survives the payload round-trip: a fresh subject sees no
    # formed habits at all.
    fresh = _make_subject(tmp)
    assert _formed_keys(fresh) == set()


# -- fitness 6: authored habits untouched -------------------------------------------

def test_authored_habits_byte_unchanged():
    tmp = _tmp()
    subject = _make_subject(tmp)
    before = _authored_snapshot(subject)
    assert set(before) == {"curious_question", "verify_before_claiming"}
    assert before["curious_question"] == ("curiosity", "explore", 0.72, 2)
    assert before["verify_before_claiming"] == ("uncertainty", "observe", 0.68, 3)

    # Full lifecycle: formation, growth, decay, removal.
    needs, pressures, baselines = _fear_script()
    for _ in range(6):
        _script_tick(subject, needs, pressures, baselines)
    low_trust = _pressures(fear=0.95, trust=0.10)
    for _ in range(4):
        _script_tick(subject, _needs(hunger=0.80), low_trust, baselines)

    after = _authored_snapshot(subject)
    assert after == before
    # (last_used_tick is engine-owned — the fire rule stamps it when an
    # authored habit fires, identically with or without this mutation —
    # so the byte-identity assertion scopes to the fields formation owns.)


# -- fitness 7: determinism --------------------------------------------------------

def test_identical_sequences_byte_identical_sidecar():
    def _run():
        tmp = _tmp()
        subject = _make_subject(tmp)
        for _ in range(5):
            _script_tick(subject, _needs(curiosity=0.80),
                         _pressures(), _baselines())
        return (tmp / "habits-formed.json").read_bytes()

    assert _run() == _run()


# -- fitness 8: dream isolation ------------------------------------------------------

def test_dream_ticks_leave_sidecar_byte_identical():
    tmp = _tmp()
    subject = _make_subject(tmp)
    for _ in range(3):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    before = (tmp / "habits-formed.json").read_bytes()
    for _ in range(3):
        subject.dream_tick()
    after = (tmp / "habits-formed.json").read_bytes()
    assert before == after
    # No dream-formed habits either.
    assert _formed_keys(subject) == {"formed:curiosity:explore"}


def test_dream_ticks_create_no_sidecar():
    tmp = _tmp()
    subject = _make_subject(tmp)
    subject.dream_tick()
    assert not (tmp / "habits-formed.json").exists()


# -- fitness 9: read-only commands -----------------------------------------------------

def _patched_cli(tmp: Path):
    """Point the CLI at synthetic paths, including the habits sidecar.

    Every module-level path cmd_init touches must be redirected — an
    unredirected sidecar path means cmd_init --force wipes the LIVE file
    during a routine test run (this actually deleted the live
    familiarity.json before the HABITS line was added here).
    """
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    habits = tmp / "habits-formed.json"
    salience = tmp / "salience.json"
    interoception = tmp / "interoception.json"
    familiarity = tmp / "familiarity.json"
    ambivalence = tmp / "ambivalence.json"
    proposals = tmp / "proposals"
    archive = tmp / "archive"
    saved = (cli.DB, cli.INBOX, cli.HABITS, cli.SALIENCE, cli.INTEROCEPTION,
             cli.FAMILIARITY, cli.AMBIVALENCE, cli.PROPOSALS, cli.ARCHIVE,
             cli._subject)
    (cli.DB, cli.INBOX, cli.HABITS, cli.SALIENCE, cli.INTEROCEPTION,
     cli.FAMILIARITY, cli.AMBIVALENCE, cli.PROPOSALS,
     cli.ARCHIVE) = (db, inbox, habits, salience, interoception,
                     familiarity, ambivalence, proposals, archive)
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(salience),
                              interoception_path=str(interoception),
                              familiarity_path=str(familiarity),
                              ambivalence_path=str(ambivalence),
                              habits_path=str(habits))

    cli._subject = make_subject
    return habits, saved


def _restore_cli(saved):
    (cli.DB, cli.INBOX, cli.HABITS, cli.SALIENCE, cli.INTEROCEPTION,
     cli.FAMILIARITY, cli.AMBIVALENCE, cli.PROPOSALS, cli.ARCHIVE,
     cli._subject) = saved


def test_readonly_commands_leave_sidecar_byte_identical():
    tmp = _tmp()
    habits, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        for _ in range(3):
            with subject._transaction():
                _set_state(subject, _needs(curiosity=0.80),
                           _pressures(), _baselines())
            cli._run_tick(subject)
        assert habits.exists()
        before = habits.read_bytes()
        cli.cmd_drift(argparse.Namespace(window=30))
        cli.cmd_status(argparse.Namespace(raw=False))
        assert habits.read_bytes() == before
    finally:
        _restore_cli(saved)


# -- fitness 10: init --force -----------------------------------------------------------

def test_init_force_wipes_sidecar_and_formed_habits():
    tmp = _tmp()
    habits, saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        for _ in range(3):
            with subject._transaction():
                _set_state(subject, _needs(curiosity=0.80),
                           _pressures(), _baselines())
            cli._run_tick(subject)
        assert "formed:curiosity:explore" in subject.engine.state.habits
        assert habits.exists()

        cli.cmd_init(argparse.Namespace(force=True))
        assert not habits.exists(), \
            "init --force must wipe the habits sidecar (genome: reseed reset)"
        fresh = cli._subject()
        assert _formed_keys(fresh) == set(), \
            "formed habits must not survive reseed"
        assert _authored_snapshot(fresh) == {
            "curious_question": ("curiosity", "explore", 0.72, 2),
            "verify_before_claiming": ("uncertainty", "observe", 0.68, 3),
        }, "authored habits must be restored from the cartridge"
    finally:
        _restore_cli(saved)


# -- genome and mechanism edge cases --------------------------------------------------

def test_apology_bypass_notes_nothing():
    # The frozen engine bypasses the channel rule for apology events
    # (engine.py -> Action.REPAIR): no trigger drove the tick, so the
    # observer must stay silent — a (trigger, repair) window entry would
    # fabricate a co-fire the engine never made.
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        _set_state(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    subject.enqueue(Event("apology", "tester", "I am sorry about that"))
    result = cli._run_tick(subject)
    assert result["action"] == "repair", result["action"]
    sc = _sidecar(tmp)
    assert sc is None or sc["window"] == [], sc


def test_stale_pending_note_is_dropped():
    # A select_conduct call outside a tick stages a note; the next
    # _run_tick runs its own heartbeat (overwriting the slot on the
    # apology path: nothing), and the stale note must be dropped, never
    # applied to the later tick.
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        _set_state(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    subject.engine.select_conduct()  # stages (tick 0, curiosity, explore)
    assert subject.workspace.habits_tracker.take_pending() is not None
    # Re-stage, then run a tick whose heartbeat notes nothing (apology
    # bypass): the stale note must not leak into the window.
    subject.engine.select_conduct()
    subject.enqueue(Event("apology", "tester", "sorry"))
    result = cli._run_tick(subject)
    assert result["action"] == "repair"
    sc = _sidecar(tmp)
    assert sc is None or sc["window"] == [], sc
    assert subject.workspace.habits_tracker.take_pending() is None


def test_window_rolls_off_old_ticks():
    tmp = _tmp()
    subject = _make_subject(tmp)
    for _ in range(FORMATION_WINDOW + 2):
        _script_tick(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    sc = _sidecar(tmp)
    assert len(sc["window"]) == FORMATION_WINDOW, len(sc["window"])
    assert sc["window"][0]["tick"] == 3  # oldest evicted, newest kept
    assert sc["window"][-1]["tick"] == FORMATION_WINDOW + 2


def test_observer_survives_restore_without_double_noting():
    # _restore swaps in a fresh engine on every transaction; the observer
    # must be re-installed exactly once (a double wrap would note twice
    # per tick — but the slot is single-entry, so this asserts the wrap
    # flag, not the count).
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        pass  # forces a _restore cycle
    assert getattr(subject.engine.select_conduct, "_habits_wrapped", False) is True
    install_habit_observer(subject)  # explicit re-install is a no-op
    with subject._transaction():
        _set_state(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    cli._run_tick(subject)
    sc = _sidecar(tmp)
    assert len(sc["window"]) == 1, sc["window"]


def test_pressure_trigger_uses_dominant_need_delta():
    # Documents the module's ambiguity resolution: a pressure trigger has
    # no need-delta of its own, so the window entry carries the dominant
    # need's delta (curiosity's here, not fear's — fear is not a need).
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        _set_state(subject, _needs(curiosity=0.80),
                   _pressures(fear=0.95, trust=0.5), _baselines(fear=0.0))
    # Snapshot exactly what cli._run_tick snapshots: the staged in-memory
    # state, before the heartbeat's _restore.
    before_curiosity = dict(subject.engine.state.needs)["curiosity"]
    cli._run_tick(subject)
    after_curiosity = subject.engine.state.needs["curiosity"]
    sc = _sidecar(tmp)
    entry = sc["window"][-1]
    assert entry["trigger"] == "fear", entry
    assert entry["dominant_need"] == "curiosity", entry
    assert entry["delta"] == round(after_curiosity - before_curiosity, 6), entry


def test_reconcile_preserves_last_used_tick_and_heals_stale():
    # Unit: reconcile syncs strength from the sidecar, preserves the
    # engine's last_used_tick, and deletes stale formed: keys.
    from digital_subject.models import Action, Habit
    tmp = _tmp()
    tracker = HabitFormationTracker(tmp / "habits-formed.json")
    tracker.data["formed"]["formed:curiosity:explore"] = {
        "trigger": "curiosity", "action": "explore", "strength": 0.51,
        "peak": 0.51, "created_tick": 3, "cofires": 6, "last_tick": 9,
    }
    habits = {
        "curious_question": Habit(key="curious_question", trigger="curiosity",
                                  action=Action("explore"), strength=0.72,
                                  cooldown=2, last_used_tick=4),
        "formed:curiosity:explore": Habit(key="formed:curiosity:explore",
                                          trigger="curiosity",
                                          action=Action("explore"),
                                          strength=0.45, cooldown=2,
                                          last_used_tick=8),
        "formed:stale:pair": Habit(key="formed:stale:pair", trigger="stale",
                                   action=Action("wait"), strength=0.5,
                                   cooldown=2),
    }
    tracker.reconcile_state_habits(habits)
    assert habits["formed:curiosity:explore"].strength == 0.51
    assert habits["formed:curiosity:explore"].last_used_tick == 8
    assert "formed:stale:pair" not in habits
    assert habits["curious_question"].strength == 0.72  # untouched


def test_archive_capped():
    # Unit: the removal archive never grows past ARCHIVE_CAP, newest kept.
    tmp = _tmp()
    tracker = HabitFormationTracker(tmp / "habits-formed.json")
    tracker.data["archived"] = [
        {"key": f"formed:old:{i}", "peak": 0.5, "lifespan_ticks": 10,
         "reason": REMOVAL_REASON, "archived_tick": i}
        for i in range(ARCHIVE_CAP)
    ]
    tracker.data["formed"]["formed:curiosity:explore"] = {
        "trigger": "curiosity", "action": "explore", "strength": 0.31,
        "peak": 0.45, "created_tick": 3, "cofires": 3, "last_tick": 9,
    }
    # One decay tick on the same trigger with a different action.
    changed = tracker.observe_tick(
        tick=10, trigger="curiosity", action="ask",
        dominant_need="curiosity", need_deltas={"curiosity": -0.01})
    assert changed == (True, True)
    assert len(tracker.data["archived"]) == ARCHIVE_CAP
    assert tracker.data["archived"][-1]["key"] == "formed:curiosity:explore"
    assert tracker.data["archived"][-1]["archived_tick"] == 10
    assert "formed:curiosity:explore" not in tracker.data["formed"]


def test_dreaming_guard_blocks_direct_selection():
    # Belt-and-braces: even a direct select_conduct call notes nothing
    # while _dreaming is set.
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        _set_state(subject, _needs(curiosity=0.80), _pressures(), _baselines())
    tracker = subject.workspace.habits_tracker
    subject._dreaming = True
    try:
        subject.engine.select_conduct()
    finally:
        subject._dreaming = False
    assert tracker.take_pending() is None


# -- round-1 critic fix: corrupt sidecar fails loud, writes are atomic -----

def test_missing_sidecar_loads_empty_without_raise():
    """Genome: silent zero. A missing sidecar is a legitimate fresh start
    — the fail-loud contract must only fire on a present-but-malformed
    file, never on absence."""
    tmp = _tmp()
    tracker = HabitFormationTracker(tmp / "habits-formed.json")
    assert tracker.data == {"window": [], "formed": {}, "archived": []}


def test_corrupt_sidecar_raises_fail_loud():
    """Genome: silent zero on malformed state (round-1 critic fix).

    A truncated sidecar (crash mid-write) must raise, never silently
    adopt empty state — the emptied map would let the next reconcile
    delete every formed habit from state.habits with no archival note,
    violating 'history archived with a reason, never silently deleted'.
    """
    tmp = _tmp()
    path = tmp / "habits-formed.json"
    path.write_text('{"window": [{"tick": 1,', encoding="utf-8")
    with pytest.raises(ValueError, match="not parseable JSON"):
        HabitFormationTracker(path)


def test_non_object_sidecar_raises():
    """Valid JSON with the wrong top-level shape is malformed too — the
    old code silently kept empty state here as well."""
    tmp = _tmp()
    path = tmp / "habits-formed.json"
    path.write_text('["not", "an", "object"]', encoding="utf-8")
    with pytest.raises(ValueError, match="must hold a JSON object"):
        HabitFormationTracker(path)


def test_malformed_formed_entries_raise():
    """Strict formed-map validation: a silently filtered entry would let
    reconcile_state_habits delete the live habit without a written note.
    Every malformed variant raises instead."""
    def _formed(entry):
        return {"formed:fear:withdraw": entry}

    good = {"trigger": "fear", "action": "withdraw", "strength": 0.45,
            "peak": 0.45, "created_tick": 3, "cofires": 3, "last_tick": 3}
    variants = {
        "non-formed key": ({"junk": good},),
        "non-dict entry": _formed("withdraw"),
        "bogus action": _formed({**good, "action": "fly"}),
        "missing action": _formed({k: v for k, v in good.items()
                                   if k != "action"}),
        "non-finite strength": _formed({**good, "strength": float("inf")}),
        "string strength": _formed({**good, "strength": "hot"}),
        "non-int created_tick": _formed({**good, "created_tick": "three"}),
        "empty trigger": _formed({**good, "trigger": ""}),
    }
    for desc, formed in variants.items():
        tmp = _tmp()
        path = tmp / "habits-formed.json"
        path.write_text(json.dumps({"window": [], "formed": formed,
                                    "archived": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="malformed|must be"):
            HabitFormationTracker(path)
        # keep the loop honest about which variant ran:
        assert desc in variants


def test_corrupt_sidecar_tick_raises_before_any_reconcile():
    """End-to-end fail loud: with a truncated sidecar, the next waking
    tick raises ValueError — _restore rebuilds the tracker from disk
    before any reconcile can run — instead of silently adopting empty
    state and deleting the formed habit. The DB habit is untouched and
    the corrupt bytes survive (save() never overwrote the evidence)."""
    tmp = _tmp()
    subject = _make_subject(tmp)
    key = "formed:fear:withdraw"
    for _ in range(3):
        result = _script_tick(subject, *_fear_script())
        assert result["action"] == "withdraw"
    assert key in subject.engine.state.habits

    corrupt = '{"window": [{"tick": 1,'
    (tmp / "habits-formed.json").write_text(corrupt, encoding="utf-8")
    with pytest.raises(ValueError, match="not parseable JSON"):
        _script_tick(subject, *_fear_script())
    assert key in subject.engine.state.habits
    assert (tmp / "habits-formed.json").read_text(encoding="utf-8") == corrupt
    assert not (tmp / "habits-formed.json.tmp").exists()


def test_save_is_atomic_no_tmp_residue():
    """A save leaves no temp file behind and the sidecar parses whole."""
    tmp = _tmp()
    path = tmp / "habits-formed.json"
    tracker = HabitFormationTracker(path)
    tracker.observe_tick(tick=1, trigger="fear", action="withdraw",
                         dominant_need="energy", need_deltas={"energy": -0.01})
    tracker.save()
    assert not (tmp / "habits-formed.json.tmp").exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert len(loaded["window"]) == 1
    assert loaded["window"][0]["delta"] == -0.01


def test_save_failure_between_write_and_rename_keeps_old_body(monkeypatch):
    """The atomicity guarantee, testable: if the rename step fails (crash
    after writing, before replacing), the pre-existing sidecar bytes are
    untouched — no truncated shell is ever left behind."""
    tmp = _tmp()
    path = tmp / "habits-formed.json"
    tracker = HabitFormationTracker(path)
    tracker.observe_tick(tick=1, trigger="fear", action="withdraw",
                         dominant_need="energy", need_deltas={"energy": -0.01})
    tracker.save()
    before = path.read_bytes()

    def _boom(src, dst):
        raise OSError("simulated crash between write and rename")
    monkeypatch.setattr("os.replace", _boom)
    tracker.observe_tick(tick=2, trigger="fear", action="withdraw",
                         dominant_need="energy", need_deltas={"energy": -0.01})
    with pytest.raises(OSError, match="simulated crash"):
        tracker.save()
    assert path.read_bytes() == before
    # The old body is still a complete, parseable sidecar.
    assert len(json.loads(before)["window"]) == 1


# -- round-2 critic fix: window rows validated at load (F1), formed --------
# -- entries confined to the tracker's written domain (F2) ----------------


def _poisoned_sidecar(tmp, formed=None, window=None):
    path = tmp / "habits-formed.json"
    path.write_text(json.dumps({"window": window or [],
                                "formed": formed or {},
                                "archived": []}), encoding="utf-8")
    return path


def _good_formed(**over):
    d = {"trigger": "fear", "action": "withdraw", "strength": 0.45,
         "peak": 0.45, "created_tick": 3, "cofires": 3, "last_tick": 3}
    d.update(over)
    return d


def test_window_row_missing_keys_dropped_at_load():
    """F1: the lenient window filter's own rationale — 'a dropped row only
    misses a future formation'. A row missing required keys is dropped at
    load, not kept to KeyError on every observe_tick."""
    tmp = _tmp()
    tracker = HabitFormationTracker(_poisoned_sidecar(tmp, window=[{"tick": 1}]))
    assert tracker.data["window"] == []
    tracker.observe_tick(tick=2, trigger="fear", action="withdraw",
                         dominant_need="energy", need_deltas={"energy": -0.01})


def test_window_row_wrong_typed_fields_dropped_at_load():
    """F1: a parseable-but-garbage window row (string delta) is dropped,
    not kept to TypeError on the next mean-delta sum."""
    tmp = _tmp()
    tracker = HabitFormationTracker(_poisoned_sidecar(tmp, window=[{
        "tick": 1, "trigger": "fear", "action": "withdraw",
        "dominant_need": "energy", "delta": "hot"}]))
    assert tracker.data["window"] == []
    for t in (2, 3, 4):
        tracker.observe_tick(tick=t, trigger="fear", action="withdraw",
                             dominant_need="energy",
                             need_deltas={"energy": -0.01})


def test_window_row_bool_tick_and_empty_strings_dropped():
    """Bools are not ints and empty strings are not triggers: the
    load-time window filter drops both."""
    tmp = _tmp()
    tracker = HabitFormationTracker(_poisoned_sidecar(tmp, window=[
        {"tick": True, "trigger": "fear", "action": "withdraw",
         "dominant_need": "energy", "delta": -0.01},
        {"tick": 2, "trigger": "", "action": "withdraw",
         "dominant_need": "energy", "delta": -0.01},
    ]))
    assert tracker.data["window"] == []


def test_window_keeps_well_formed_rows():
    """The stricter filter must not starve formation: a well-formed
    window still drives crystallization exactly as before."""
    tmp = _tmp()
    tracker = HabitFormationTracker(_poisoned_sidecar(tmp, window=[
        {"tick": t, "trigger": "fear", "action": "withdraw",
         "dominant_need": "energy", "delta": -0.01} for t in (1, 2)]))
    assert len(tracker.data["window"]) == 2
    changed, state_changed = tracker.observe_tick(
        tick=3, trigger="fear", action="withdraw", dominant_need="energy",
        need_deltas={"energy": -0.01})
    assert changed and state_changed
    assert "formed:fear:withdraw" in tracker.data["formed"]


def test_formed_strength_outside_domain_rejected():
    """F2: shape-valid but out-of-domain strengths — above the growth cap
    (phantom super-habit), below the removal floor, bool — all raise."""
    variants = {
        "above cap": _good_formed(strength=999.0, peak=999.0),
        "below floor": _good_formed(strength=-0.5, peak=0.45),
        "bool": _good_formed(strength=True),
    }
    for desc, entry in variants.items():
        tmp = _tmp()
        path = _poisoned_sidecar(tmp, formed={"formed:fear:withdraw": entry})
        with pytest.raises(ValueError, match="strength"):
            HabitFormationTracker(path)
        assert desc in variants  # loop honesty


def test_formed_peak_outside_bounds_rejected():
    """F2: peak must sit in [strength, GROWTH_CAP] — the tracker only ever
    writes peak as the max strength seen."""
    variants = {
        "peak above cap": _good_formed(strength=0.45, peak=5.0),
        "peak below strength": _good_formed(strength=0.60, peak=0.45),
    }
    for desc, entry in variants.items():
        tmp = _tmp()
        path = _poisoned_sidecar(tmp, formed={"formed:fear:withdraw": entry})
        with pytest.raises(ValueError, match="peak"):
            HabitFormationTracker(path)
        assert desc in variants


def test_formed_tick_bounds_rejected():
    """F2: ticks are non-bool, non-negative ints with
    last_tick >= created_tick."""
    variants = {
        "negative created_tick": (_good_formed(created_tick=-5),
                                  "created_tick"),
        "bool last_tick": (_good_formed(last_tick=True), "last_tick"),
        "last_tick before created_tick": (
            _good_formed(created_tick=9, last_tick=3), "last_tick"),
    }
    for desc, (entry, field) in variants.items():
        tmp = _tmp()
        path = _poisoned_sidecar(tmp, formed={"formed:fear:withdraw": entry})
        with pytest.raises(ValueError, match=field):
            HabitFormationTracker(path)
        assert desc in variants


def test_formed_key_must_name_its_trigger_action():
    """F2: a key naming a different habit than its meta would reconcile
    into a Habit whose trigger mismatches its namespace — the engine
    matches on trigger, so the sidecar would name one habit and the
    engine fire another."""
    tmp = _tmp()
    path = _poisoned_sidecar(
        tmp, formed={"formed:fear:withdraw": _good_formed(trigger="curiosity")})
    with pytest.raises(ValueError, match="trigger"):
        HabitFormationTracker(path)


def test_formed_domain_boundary_values_load():
    """The bounds are inclusive: cap/floor strengths, peak == strength,
    tick 0, and last_tick == created_tick are all tracker-writable."""
    tmp = _tmp()
    tracker = HabitFormationTracker(_poisoned_sidecar(tmp, formed={
        "formed:fear:withdraw": _good_formed(
            strength=GROWTH_CAP, peak=GROWTH_CAP,
            created_tick=0, last_tick=0),
        "formed:curiosity:explore": _good_formed(
            trigger="curiosity", action="explore",
            strength=REMOVAL_FLOOR, peak=REMOVAL_FLOOR,
            created_tick=5, last_tick=5),
    }))
    assert set(tracker.data["formed"]) == {
        "formed:fear:withdraw", "formed:curiosity:explore"}
