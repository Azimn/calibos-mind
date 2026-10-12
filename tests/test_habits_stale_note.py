"""Tests for stale-habit notes (domain 17: repeated failed attempts).

Spec: research/specs/2026-10-12-habit-stale-note.md — calibos_mind/habits.py.
All fixtures live in /tmp — the live store is never touched.

A FORMED habit firing by bypass (>= 0.65 strength, the engine's own
last_used_tick stamp proving the bypass branch ran) while its
trigger-relevant need does NOT improve (per-tick delta >= 0) for
STALE_FIRINGS consecutive firings mints exactly one temporal record
noting the failure. Record-only: no auto-removal, no strength change.
An improving firing (delta < 0) resets the streak and re-arms. Authored
habits are never touched.

Scenario control (probed 2026-10-12 against the frozen install):
- hunger 0.80 dominant, pressures low -> action "seek_contact", trigger
  "hunger", hunger delta ~= +0.002/tick (homeostasis only; seek_contact
  touches loneliness/restlessness, not hunger) -> non-improving.
- A formed (hunger -> seek_contact) habit never crystallizes on its own
  (the formation delta gate blocks: mean delta > 0), so tests seed the
  sidecar's formed map directly at 0.65. Tick 1 grows it to 0.67 without
  firing (not yet reconciled into state.habits); the bypass then fires
  every 3rd tick (cooldown 2): ticks 2, 5, 8, 11, 14, ...
- fear 0.95 vs energy 0.20 -> pressure channel wins -> action "withdraw",
  trigger "fear", dominant need "energy", energy delta ~= -0.0021/tick
  (improving per the spec's delta<0 definition) -> the improving control.
- The host feeder emits a food event when maybe_feed sees tick 48
  (result_tick 49): hunger -0.35, an in-vivo improving firing. Tests
  jump state.tick to 48 to place one deterministically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.habits import (
    FORMED_PREFIX,
    STALE_FIRINGS,
    HabitFormationTracker,
    is_stale_note_text,
    mint_stale_note,
    parse_stale_note_text,
    stale_note_text,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge


HUNGER_KEY = "formed:hunger:seek_contact"
FEAR_KEY = "formed:fear:withdraw"


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="stale-note-test-"))


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
        ambivalence_path=None,
        habits_path=str(tmp / "habits-formed.json"),
    )


def _seed_formed(tmp: Path, key: str, trigger: str, action: str,
                 strength: float = 0.65) -> None:
    """Write a sidecar with one formed habit (pre-mutation shape: no
    stale_streaks key — the loader must treat it as fresh empty)."""
    (tmp / "habits-formed.json").write_text(json.dumps({
        "window": [], "archived": [], "formed": {
            key: {"trigger": trigger, "action": action,
                  "strength": strength, "peak": strength,
                  "created_tick": 0, "cofires": 3, "last_tick": 0}},
    }), encoding="utf-8")


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


def _hunger_tick(subject):
    with subject._transaction():
        _set_state(subject, _needs(hunger=0.80), _pressures(), _baselines())
    return cli._run_tick(subject)


def _fear_tick(subject):
    with subject._transaction():
        _set_state(subject, _needs(energy=0.20),
                   _pressures(fear=0.95, trust=0.5), _baselines(fear=0.0))
    return cli._run_tick(subject)


def _jump_to_food_tick(subject):
    """Place the next scripted tick on the feeder's food rhythm: maybe_feed
    sees tick 48 -> food event -> hunger -0.35 (an improving firing)."""
    with subject._transaction():
        subject.engine.state.tick = 48


def _stale_notes(subject):
    return [r for r in subject.workspace.records
            if r.source == "temporal" and r.generated_by == "cognition"
            and is_stale_note_text(r.first_person)]


def _sidecar(tmp: Path):
    p = tmp / "habits-formed.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _streak(tmp: Path, key: str):
    return _sidecar(tmp)["stale_streaks"][key]


# -- fitness 1: five non-improving firings mint exactly one note --------------

def test_five_nonimproving_firings_mint_exactly_one_note():
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    subject = _make_subject(tmp)
    for _ in range(14):
        result = _hunger_tick(subject)
        assert result["action"] == "seek_contact", result["action"]
    notes = _stale_notes(subject)
    assert len(notes) == 1, [r.first_person for r in notes]
    note = notes[0]
    assert note.source == "temporal"
    assert note.generated_by == "cognition"
    # No contractions in minted text (regression genome:
    # contraction-negation blindness).
    assert "n't" not in note.first_person, note.first_person
    assert "’" not in note.first_person and "‘" not in note.first_person
    parsed = parse_stale_note_text(note.first_person)
    assert parsed is not None
    assert parsed["habit_key"] == HUNGER_KEY
    assert parsed["consecutive_firings"] == STALE_FIRINGS == 5
    assert parsed["trigger_need"] == "hunger"
    assert parsed["trigger"] == "hunger" and parsed["action"] == "seek_contact"
    assert parsed["last_delta"] == pytest.approx(0.002, abs=1e-6)
    assert parsed["last_delta"] >= 0
    assert parsed["start_tick"] == 2, parsed   # first bypass firing
    assert parsed["end_tick"] == 14, parsed    # fifth bypass firing
    # Round-trip: the canonical text is byte-identical from the parse.
    assert stale_note_text(parsed) == note.first_person
    # Sidecar state agrees with the record.
    entry = _streak(tmp, HUNGER_KEY)
    assert entry["count"] == 5
    assert entry["owed"] is None and entry["noted_start"] == 2


# -- fitness 2: no second note within the episode ------------------------------

def test_sixth_and_seventh_firings_mint_no_second_note():
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    subject = _make_subject(tmp)
    for _ in range(20):
        _hunger_tick(subject)
    assert len(_stale_notes(subject)) == 1
    entry = _streak(tmp, HUNGER_KEY)
    assert entry["count"] == 7, entry
    assert entry["noted_start"] == 2 and entry["owed"] is None


# -- fitness 3: improving firing resets; a new episode mints again -------------

def test_improving_firing_resets_then_new_episode_mints_second_note():
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    subject = _make_subject(tmp)
    for _ in range(14):
        _hunger_tick(subject)
    assert len(_stale_notes(subject)) == 1
    # The feeder food tick: hunger -0.35, an improving bypass firing.
    _jump_to_food_tick(subject)
    result = _hunger_tick(subject)
    assert result["tick"] == 49 and result["action"] == "seek_contact"
    h = subject.engine.state.habits[HUNGER_KEY]
    assert h.last_used_tick == 49  # it fired (by bypass), and improved
    assert len(_stale_notes(subject)) == 1
    entry = _streak(tmp, HUNGER_KEY)
    assert entry["count"] == 0 and entry["last_tick"] == 49, entry
    # Five more non-improving firings: 52, 55, 58, 61, 64.
    for _ in range(15):
        _hunger_tick(subject)
    notes = _stale_notes(subject)
    assert len(notes) == 2, [n.first_person for n in notes]
    starts = sorted(parse_stale_note_text(n.first_person)["start_tick"]
                    for n in notes)
    assert starts == [2, 52], starts
    second = [n for n in notes
              if parse_stale_note_text(n.first_person)["start_tick"] == 52][0]
    parsed = parse_stale_note_text(second.first_person)
    assert (parsed["start_tick"], parsed["end_tick"]) == (52, 64)
    assert parsed["consecutive_firings"] == 5


# -- fitness 4: improving firings never mint (control) -------------------------

def test_improving_firings_mint_no_note():
    tmp = _tmp()
    _seed_formed(tmp, FEAR_KEY, "fear", "withdraw")
    subject = _make_subject(tmp)
    for _ in range(14):
        result = _fear_tick(subject)
        assert result["action"] == "withdraw", result["action"]
    # Five bypass firings happened (ticks 2, 5, 8, 11, 14), each with
    # energy delta < 0 -> every firing reset the streak.
    assert subject.engine.state.habits[FEAR_KEY].last_used_tick == 14
    assert _stale_notes(subject) == []
    entry = _streak(tmp, FEAR_KEY)
    assert entry["count"] == 0, entry
    assert entry["trigger_need"] == "energy", entry
    assert entry["owed"] is None and entry["noted_start"] is None


# -- fitness 5: sub-threshold ---------------------------------------------------

def test_four_nonimproving_firings_mint_no_note():
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    subject = _make_subject(tmp)
    for _ in range(12):
        _hunger_tick(subject)
    assert _stale_notes(subject) == []
    entry = _streak(tmp, HUNGER_KEY)
    assert entry["count"] == 4, entry
    assert entry["owed"] is None and entry["noted_start"] is None


# -- fitness 6: improving on the would-be fifth firing --------------------------

def test_improving_fifth_firing_mints_no_note():
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    subject = _make_subject(tmp)
    for _ in range(11):
        _hunger_tick(subject)
    assert _streak(tmp, HUNGER_KEY)["count"] == 4
    _jump_to_food_tick(subject)
    result = _hunger_tick(subject)
    assert result["tick"] == 49
    assert subject.engine.state.habits[HUNGER_KEY].last_used_tick == 49
    assert _stale_notes(subject) == []
    entry = _streak(tmp, HUNGER_KEY)
    assert entry["count"] == 0 and entry["last_tick"] == 49, entry


# -- fitness 7: determinism ------------------------------------------------------

def test_identical_scripts_produce_byte_identical_sidecar():
    def _run():
        tmp = _tmp()
        _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
        subject = _make_subject(tmp)
        for _ in range(16):
            _hunger_tick(subject)
        assert len(_stale_notes(subject)) == 1
        return (tmp / "habits-formed.json").read_bytes()

    assert _run() == _run()


# -- unit: text/parse/mint -------------------------------------------------------

def test_note_text_parse_round_trip_and_rejections():
    event = {"habit_key": HUNGER_KEY, "trigger": "hunger",
             "action": "seek_contact", "trigger_need": "hunger",
             "consecutive_firings": 5, "last_delta": 0.002,
             "start_tick": 2, "end_tick": 14}
    text = stale_note_text(event)
    parsed = parse_stale_note_text(text)
    assert parsed == {"action": "seek_contact", "trigger": "hunger",
                      "trigger_need": "hunger", "consecutive_firings": 5,
                      "habit_key": HUNGER_KEY, "last_delta": 0.002,
                      "start_tick": 2, "end_tick": 14}
    assert stale_note_text(parsed) == text
    # Negative deltas render and parse.
    neg = stale_note_text({**event, "last_delta": -0.348})
    assert parse_stale_note_text(neg)["last_delta"] == pytest.approx(-0.348)
    # Near-misses are rejected, never partially matched.
    assert not is_stale_note_text(text.replace("I have done", "I've done"))
    assert not is_stale_note_text(
        text.replace(HUNGER_KEY, "formed:hunger:ask"))  # key/trigger mismatch
    assert not is_stale_note_text(text + " ")
    assert not is_stale_note_text(" " + text)
    assert not is_stale_note_text("just some text")
    assert not is_stale_note_text(None)
    assert not is_stale_note_text(42)


def test_mint_is_idempotent_on_habit_key_and_start_tick():
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    subject = _make_subject(tmp)
    event = {"habit_key": HUNGER_KEY, "trigger": "hunger",
             "action": "seek_contact", "trigger_need": "hunger",
             "consecutive_firings": 5, "last_delta": 0.002,
             "start_tick": 2, "end_tick": 14}
    with subject._transaction():
        first = mint_stale_note(subject, event)
    with subject._transaction():
        second = mint_stale_note(subject, event)
    assert first == second
    assert len(_stale_notes(subject)) == 1
    # A different episode (new start_tick) mints separately.
    with subject._transaction():
        third = mint_stale_note(subject, {**event, "start_tick": 52,
                                          "end_tick": 64})
    assert third != first
    assert len(_stale_notes(subject)) == 2


def test_recovery_after_crashed_mint_mints_exactly_once():
    """Simulate a kill between the write-ahead save and the mint: the
    owed mark is durable in the sidecar; the next tick re-mints, and the
    idempotent mint keeps it to exactly one record."""
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    subject = _make_subject(tmp)
    for _ in range(14):
        _hunger_tick(subject)
    assert len(_stale_notes(subject)) == 1
    # Crash simulation: a fresh store whose sidecar carries an owed mark
    # for an episode the store never recorded (kill between the
    # write-ahead save and the mint). The next tick must re-mint it.
    tmp2 = _tmp()
    _seed_formed(tmp2, HUNGER_KEY, "hunger", "seek_contact")
    sc2 = _sidecar(tmp2)
    sc2["stale_streaks"] = {HUNGER_KEY: {
        "count": 5, "start_tick": 2, "last_tick": 14, "last_delta": 0.002,
        "trigger_need": "hunger", "noted_start": None,
        "owed": {"trigger": "hunger", "action": "seek_contact",
                 "trigger_need": "hunger", "consecutive_firings": 5,
                 "last_delta": 0.002, "start_tick": 2, "end_tick": 14}}}
    (tmp2 / "habits-formed.json").write_text(json.dumps(sc2))
    subject2 = _make_subject(tmp2)
    _hunger_tick(subject2)  # tick 1: recovery re-mints the owed episode
    notes = _stale_notes(subject2)
    assert len(notes) == 1, [n.first_person for n in notes]
    assert parse_stale_note_text(notes[0].first_person)["start_tick"] == 2
    _hunger_tick(subject2)  # ack ran; no duplicate
    assert len(_stale_notes(subject2)) == 1
    entry2 = _streak(tmp2, HUNGER_KEY)
    assert entry2["owed"] is None and entry2["noted_start"] == 2


# -- strict loading of the new sidecar key ----------------------------------------

def _streaks_doc(entry):
    return {"window": [], "formed": {}, "archived": [],
            "stale_streaks": {HUNGER_KEY: entry}}


def _good_streak(**over):
    d = {"count": 3, "start_tick": 2, "last_tick": 8, "last_delta": 0.002,
         "trigger_need": "hunger", "owed": None, "noted_start": None}
    d.update(over)
    return d


def test_malformed_stale_streaks_raise_fail_loud():
    variants = {
        "non-dict": {"stale_streaks": ["nope"]},
        "bad key": _streaks_doc(_good_streak()),
        "non-dict entry": {"stale_streaks": {HUNGER_KEY: [1, 2]}},
        "bool count": {"stale_streaks":
                       {HUNGER_KEY: _good_streak(count=True)}},
        "negative count": {"stale_streaks":
                           {HUNGER_KEY: _good_streak(count=-1)}},
        "non-int start": {"stale_streaks":
                          {HUNGER_KEY: _good_streak(start_tick="two")}},
        "last before start": {"stale_streaks":
                              {HUNGER_KEY: _good_streak(start_tick=9,
                                                        last_tick=8)}},
        "non-finite delta": {"stale_streaks":
                             {HUNGER_KEY: _good_streak(
                                 last_delta=float("inf"))}},
        "empty trigger_need": {"stale_streaks":
                               {HUNGER_KEY: _good_streak(trigger_need="")}},
        "non-dict owed": {"stale_streaks":
                          {HUNGER_KEY: _good_streak(owed=[])}},
        "owed bad field": {"stale_streaks": {HUNGER_KEY: _good_streak(owed={
            "trigger": "hunger", "action": "seek_contact",
            "trigger_need": "hunger", "consecutive_firings": 5,
            "last_delta": 0.002, "start_tick": 20, "end_tick": 10})}},
        "negative noted_start": {"stale_streaks":
                                 {HUNGER_KEY: _good_streak(noted_start=-1)}},
    }
    # Fix the "bad key" variant to actually use a bad key.
    variants["bad key"] = {"stale_streaks":
                           {"hunger:seek_contact": _good_streak()}}
    for desc, doc in variants.items():
        tmp = _tmp()
        path = tmp / "habits-formed.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(ValueError, match="stale_streaks|malformed"):
            HabitFormationTracker(path)
        assert desc in variants  # keep the loop honest


def test_missing_stale_streaks_key_loads_empty():
    """Pre-mutation sidecars (no stale_streaks key) load fine — a missing
    key is a legitimate fresh start, not malformed."""
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    tracker = HabitFormationTracker(tmp / "habits-formed.json")
    assert tracker.data["stale_streaks"] == {}


def test_streak_pruned_when_habit_removed():
    """A streak for a habit no longer in the formed map is self-healed
    away — a removed habit cannot fire."""
    tmp = _tmp()
    path = tmp / "habits-formed.json"
    path.write_text(json.dumps(_streaks_doc(_good_streak())),
                    encoding="utf-8")
    tracker = HabitFormationTracker(path)
    events, changed = tracker.observe_bypass_streak(
        tick=10, trigger="hunger", action="seek_contact",
        dominant_need="hunger", need_deltas={"hunger": 0.002},
        state_habits={})
    assert events == []
    assert changed is True
    assert tracker.data["stale_streaks"] == {}


# -- genome: read-only, dream isolation, reseed, fingerprint ------------------------

def _patched_cli(tmp: Path):
    """Point the CLI's store and sidecars at a tmp dir (mirrors
    test_habits._patched_cli)."""
    saved = (cli.DB, cli.INBOX, cli.HABITS, cli.SALIENCE, cli.INTEROCEPTION,
             cli.FAMILIARITY, cli.AMBIVALENCE, cli.COUNTERFACTUAL,
             cli.PROVENANCE, cli.ARCHIVE, cli.PROPOSALS, cli._subject)
    (tmp / "inbox").mkdir(exist_ok=True)

    def make_subject(provider=None):
        from calibos_mind.provider import InboxCognition
        cart = load_cartridge(cli.CARTRIDGE_PATH)
        if provider is None:
            provider = InboxCognition(cli.INBOX)
        return CalibosSubject(
            str(cli.DB), cart, cognition=provider,
            salience_path=str(cli.SALIENCE),
            interoception_path=str(cli.INTEROCEPTION),
            familiarity_path=str(cli.FAMILIARITY),
            ambivalence_path=str(cli.AMBIVALENCE),
            counterfactual_path=str(cli.COUNTERFACTUAL),
            habits_path=str(cli.HABITS))

    cli.DB = tmp / "mind.db"
    cli.INBOX = tmp / "inbox"
    cli.HABITS = tmp / "habits-formed.json"
    cli.SALIENCE = tmp / "salience.json"
    cli.INTEROCEPTION = tmp / "interoception.json"
    cli.FAMILIARITY = tmp / "familiarity.json"
    cli.AMBIVALENCE = tmp / "ambivalence.json"
    cli.COUNTERFACTUAL = tmp / "counterfactuals.json"
    cli.PROVENANCE = tmp / "provenance.json"
    cli.ARCHIVE = tmp / "archive"
    cli.PROPOSALS = tmp / "proposals"
    cli._subject = make_subject
    return saved


def _restore_cli(saved):
    (cli.DB, cli.INBOX, cli.HABITS, cli.SALIENCE, cli.INTEROCEPTION,
     cli.FAMILIARITY, cli.AMBIVALENCE, cli.COUNTERFACTUAL,
     cli.PROVENANCE, cli.ARCHIVE, cli.PROPOSALS, cli._subject) = saved


def test_readonly_commands_never_touch_streak_state():
    tmp = _tmp()
    saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        subject = cli._subject()
        _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
        subject = cli._subject()  # reload so the tracker sees the seed
        for _ in range(11):
            with subject._transaction():
                _set_state(subject, _needs(hunger=0.80),
                           _pressures(), _baselines())
            cli._run_tick(subject)
        assert _streak(tmp, HUNGER_KEY)["count"] == 4
        import sqlite3
        habits_path = tmp / "habits-formed.json"
        before_sidecar = habits_path.read_bytes()

        def _payload():
            con = sqlite3.connect(str(tmp / "mind.db"))
            try:
                return con.execute(
                    "select payload from subject where id = 1").fetchone()[0]
            finally:
                con.close()

        before_payload = _payload()
        cli.cmd_drift(argparse.Namespace(window=30))
        cli.cmd_status(argparse.Namespace(raw=False))
        cli.cmd_inbox(argparse.Namespace(id=None))
        cli.cmd_recall(argparse.Namespace(n=5))
        assert habits_path.read_bytes() == before_sidecar
        # Genome: CLI construction is never mtime-silent (the frozen
        # engine's __init__ rewrites the payload row), so the assertion
        # scopes to byte-identical payload *content*, not file bytes.
        assert _payload() == before_payload
        assert _stale_notes(cli._subject()) == []
    finally:
        _restore_cli(saved)


def test_dream_ticks_never_touch_streak_state():
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    subject = _make_subject(tmp)
    for _ in range(11):
        _hunger_tick(subject)
    assert _streak(tmp, HUNGER_KEY)["count"] == 4
    before = (tmp / "habits-formed.json").read_bytes()
    for _ in range(3):
        subject.dream_tick()
    assert (tmp / "habits-formed.json").read_bytes() == before
    assert _stale_notes(subject) == []


def test_init_force_wipes_streak_state():
    tmp = _tmp()
    saved = _patched_cli(tmp)
    try:
        cli.cmd_init(argparse.Namespace(force=False))
        path = tmp / "habits-formed.json"
        path.write_text(json.dumps(_streaks_doc(_good_streak())),
                        encoding="utf-8")
        assert path.exists()
        cli.cmd_init(argparse.Namespace(force=True))
        assert not path.exists(), \
            "init --force must wipe the habits sidecar including stale_streaks"
        fresh = cli._subject()
        assert fresh.workspace.habits_tracker.data["stale_streaks"] == {}
    finally:
        _restore_cli(saved)


def test_cartridge_fingerprint_stable_no_migration_needed():
    """The mutation touches no cartridge: a fresh subject loads the used
    store without a fingerprint-migration error."""
    tmp = _tmp()
    _seed_formed(tmp, HUNGER_KEY, "hunger", "seek_contact")
    subject = _make_subject(tmp)
    for _ in range(14):
        _hunger_tick(subject)
    assert len(_stale_notes(subject)) == 1
    import dataclasses
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    fingerprint = hashlib.sha256(
        json.dumps(dataclasses.asdict(cart), sort_keys=True).encode()
    ).hexdigest()
    assert subject.fingerprint == fingerprint
    # A fresh subject on the same store loads cleanly — a cartridge
    # change would raise here (schema/cartridge mismatch).
    fresh = _make_subject(tmp)
    assert fresh.engine.state.tick == subject.engine.state.tick
