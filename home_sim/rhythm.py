"""Need mechanics for the home, ported from the laboratory substrate.

Source: Pretorius-Laboratory/pretorius_lab/daily_rhythm.py (PR #23, merged),
ported 2026-10-19 under Jay's grant to reuse lab code freely. Credit where it
is due: the need-tick math, the prepare_meal -> eat chain, brew_tea, and
sleep/rest are the lab's honest machinery. The home invents the dwelling, not
the clock.

Ported faithfully:
- hunger +1 per 180 minutes, thirst +1 per 120 minutes, fatigue +1 per 240
  minutes (all capped at 10). The lab counts in *minutes*; the spec's "per 180
  ticks" is read as lab-minutes — a literal 180-action hunger interval would
  leave needs inert across a ~48-action night and defeat the fitness
  functions. One sim action advances the clock by its own duration.
- prepare_meal (kitchen, consumes 1 ration, sets meal_ready) -> eat
  (hunger -5, clears meal_ready); brew_tea (thirst -4); sleep (fatigue -8);
  rest (fatigue -3).
- Signal thresholds: hunger >= 4 / >= 7, thirst >= 6, fatigue >= 4 / >= 7.

Deliberate divergences (each with a reason):
- ONE managed provision (rations), restocked via the garden. The lab's
  multi-supply inventory (oil/water/fuel/tea/rations/...) is dungeon-game
  skin; every finite supply gating a need action is a potential open drain.
  Water, fuel, and tea are inexhaustible cottage fixtures (the well, the
  woodpile, the tea tin). Rations are the single resource with a real
  economy: tend_garden -> harvest -> store_produce -> rations. This is the
  no-open-drains fix the lab's starvation timer needed.
- Garden: growth 0..3; tend_garden (+1 growth, needs no provisions — only
  time and moonlight, so recovery from zero rations is always possible);
  harvest at growth >= 2 yields `growth` produce and resets growth;
  store_produce (kitchen) converts produce to rations 1:1. Neglect the garden
  and provisions dwindle; tend it and the loop closes by the inhabitant's
  own care.
- Lamps are fixtures, not fuel drains: no oil, no burn-down. They are lit or
  doused; night work needing sight requires a lit lamp (the garden works by
  moonlight). A lamp can never become an unsatisfiable precondition.
- Sleep is 240 minutes (a night's rest inside the 8-hour off-hours window),
  not the lab's 480 — an 8-hour sleep would end every sim night trivially.
- No stations: the lab's station layer collapses to rooms. No SQLite, no
  hash-chained envelopes — the event log (events.py) is append-only JSONL,
  which is the whole integrity story this prototype needs.
- Clutter (ported from lab cleanliness) is tracked only for kitchen and
  study. A filthy kitchen (clutter 3) blocks prepare_meal, as in the lab.

Pure functions only: act() takes a state dict and returns (new_state, text).
No I/O, no imports from the mind package. First-person text is Calibos's "I".
"""
from __future__ import annotations

import copy
from datetime import date

from .rooms import ROOMS, ROOM_LABELS, AFFORDANCES, CLUTTERED_ROOMS

RULES = "calibos-home-rhythm/1"

# One off-hours night runs 23:00 -> 07:00 local.
NIGHT_START_MINUTE = 23 * 60

# ---- need-tick intervals, in simulated minutes (lab parity) ----
HUNGER_EVERY = 180
THIRST_EVERY = 120
FATIGUE_EVERY = 240
CLUTTER_EVERY = 720

RATION_CAP = 12
PRODUCE_CAP = 12
GROWTH_CAP = 3

# Simulated minutes per action. A sim "tick" is one action; most are ~10 min.
OP_DURATIONS = {
    "look": 10, "move": 10, "light_lamp": 10, "douse_lamp": 10,
    "read": 30, "write": 30, "tidy": 20,
    "prepare_meal": 30, "eat": 10, "brew_tea": 20, "store_produce": 10,
    "tend_garden": 20, "harvest": 10,
    "sleep": 240, "rest": 60,
}

# Room each op requires; None means any room. "move" is validated separately.
OP_ROOMS = {
    "look": None, "move": None, "light_lamp": None, "douse_lamp": None,
    "read": "study", "write": "study", "tidy": None,
    "prepare_meal": "kitchen", "eat": "kitchen", "brew_tea": "kitchen",
    "store_produce": "kitchen",
    "tend_garden": "garden", "harvest": "garden",
    "sleep": "rest", "rest": "rest",
}

ALL_OPS = tuple(OP_DURATIONS)


class HomeError(Exception):
    """Bad operation or arguments — the request itself is malformed."""


class HomeConflict(HomeError):
    """Well-formed operation the current state does not permit."""


def initial_state(night_date: str, start_minute: int = NIGHT_START_MINUTE) -> dict:
    """Fresh state for one off-hours run. Night begins at 23:00 local."""
    return {
        "rules": RULES,
        "night_date": night_date,
        "minute": start_minute,
        "tick": 0,
        "room": "rest",
        "hunger": 2, "thirst": 2, "fatigue": 2,
        "rations": 4,
        "produce": 0,
        "garden_growth": 1,
        "meal_ready": False,
        "lamps": {"study": True, "kitchen": True,
                  "garden": False, "rest": True},
        "clutter": {"kitchen": 1, "study": 1},
        "world_bias": None,
        "last_op": None,
    }


def _validate(state: dict) -> None:
    if set(state) != {"rules", "night_date", "minute", "tick", "room",
                      "hunger", "thirst", "fatigue", "rations", "produce",
                      "garden_growth", "meal_ready", "lamps", "clutter",
                      "world_bias", "last_op"}:
        raise HomeConflict("Home state schema has drifted")
    if state["rules"] != RULES:
        raise HomeConflict("Home state is from an unknown rules version")
    try:
        night = state["night_date"]
        if not isinstance(night, str):
            raise ValueError
        date.fromisoformat(night)
    except ValueError:
        raise HomeConflict("Invalid night_date")
    if state["last_op"] is not None and not isinstance(state["last_op"], str):
        raise HomeConflict("Invalid last_op")
    if (type(state["minute"]) is not int or type(state["tick"]) is not int
            or state["tick"] < 0):
        raise HomeConflict("Invalid simulated time")
    if state["room"] not in ROOMS:
        raise HomeConflict("Invalid room")
    for k in ("hunger", "thirst", "fatigue"):
        if type(state[k]) is not int or not 0 <= state[k] <= 10:
            raise HomeConflict("Invalid need value")
    if (type(state["rations"]) is not int
            or not 0 <= state["rations"] <= RATION_CAP):
        raise HomeConflict("Invalid rations")
    if (type(state["produce"]) is not int
            or not 0 <= state["produce"] <= PRODUCE_CAP):
        raise HomeConflict("Invalid produce")
    if (type(state["garden_growth"]) is not int
            or not 0 <= state["garden_growth"] <= GROWTH_CAP):
        raise HomeConflict("Invalid garden growth")
    if type(state["meal_ready"]) is not bool:
        raise HomeConflict("Invalid meal state")
    if set(state["lamps"]) != set(ROOMS) or any(
            type(v) is not bool for v in state["lamps"].values()):
        raise HomeConflict("Invalid lamp state")
    if set(state["clutter"]) != set(CLUTTERED_ROOMS) or any(
            type(v) is not int or not 0 <= v <= 3
            for v in state["clutter"].values()):
        raise HomeConflict("Invalid clutter")
    bias = state["world_bias"]
    if bias is not None:
        if (set(bias) != {"room", "ticks_left"}
                or (bias["room"] is not None and bias["room"] not in ROOMS)
                or type(bias["ticks_left"]) is not int
                or bias["ticks_left"] < 0):
            raise HomeConflict("Invalid world bias")


def _daylight(minute: int) -> bool:
    return 360 <= (minute % 1440) < 1080


def _can_see(state: dict, room: str) -> bool:
    if room == "garden":
        return True  # moonlight and open sky suffice outdoors
    return _daylight(state["minute"]) or state["lamps"][room]


def _pass_time(state: dict, minutes: int) -> None:
    if type(minutes) is not int or minutes < 0:
        raise HomeError("Time must be a non-negative whole number of minutes")
    before, after = state["minute"], state["minute"] + minutes
    state["minute"] = after
    # Lab tick math, ported exactly (per-minute intervals).
    state["hunger"] = min(10, state["hunger"] + after // HUNGER_EVERY
                          - before // HUNGER_EVERY)
    state["thirst"] = min(10, state["thirst"] + after // THIRST_EVERY
                          - before // THIRST_EVERY)
    state["fatigue"] = min(10, state["fatigue"] + after // FATIGUE_EVERY
                           - before // FATIGUE_EVERY)
    for room in CLUTTERED_ROOMS:
        state["clutter"][room] = min(
            3, state["clutter"][room] + after // CLUTTER_EVERY
            - before // CLUTTER_EVERY)
    # Lamps are fixtures: they do not burn down (no-open-drains divergence).


def _need_signals(state: dict) -> str:
    sentence = ""
    if state["fatigue"] >= 7:
        sentence += " I feel worn out and find it hard to concentrate."
    elif state["fatigue"] >= 4:
        sentence += " I am getting tired."
    if state["hunger"] >= 7:
        sentence += " I am painfully hungry."
    elif state["hunger"] >= 4:
        sentence += " I could do with something to eat."
    if state["thirst"] >= 6:
        sentence += " My mouth is dry."
    return sentence


def _perceive(state: dict) -> str:
    room = state["room"]
    sentence = f"I am in {ROOM_LABELS[room]}."
    if not _can_see(state, room):
        sentence += " It is too dark to make out much."
    elif _daylight(state["minute"]):
        sentence += " There is daylight enough to see."
    else:
        sentence += " The lamp gives a warm light."
    sentence += _need_signals(state)
    if room == "kitchen" and _can_see(state, room):
        sentence += (" There is a prepared meal waiting." if state["meal_ready"]
                     else " I have no meal prepared.")
        if state["rations"] <= 0:
            sentence += " The pantry is empty."
    if room == "garden":
        growth = state["garden_growth"]
        if growth >= 3:
            sentence += " The beds are overflowing; it is time to harvest."
        elif growth >= 2:
            sentence += " The garden is heavy with produce ready to pick."
        elif growth == 1:
            sentence += " Seedlings are coming up in the beds."
        else:
            sentence += " The beds are bare, waiting to be tended."
    if room in CLUTTERED_ROOMS and state["clutter"][room] >= 3:
        sentence += " The mess here is getting in the way."
    return sentence


def _require_sight(state: dict, what: str) -> None:
    if not _can_see(state, state["room"]):
        raise HomeConflict(f"Too dark to {what} — light a lamp first")


def act(state: dict, op: str, args: dict | None = None) -> tuple[dict, str]:
    """Apply one micro-action. Returns (new_state, first-person text).

    Pure: the input state is never mutated. Raises HomeError/HomeConflict.
    """
    args = args or {}
    if op not in ALL_OPS or not isinstance(args, dict):
        raise HomeError(f"Unsupported home action: {op!r}")
    new = copy.deepcopy(state)
    room = new["room"]
    required = OP_ROOMS[op]
    if required is not None and room != required and op != "tidy":
        raise HomeError(f"{op} must be done in {ROOM_LABELS[required]}")
    if op == "tidy" and room not in CLUTTERED_ROOMS:
        raise HomeError("Nothing here needs tidying")

    if op == "look":
        if args:
            raise HomeError("Looking takes no arguments")
        minutes = OP_DURATIONS[op]
        _pass_time(new, minutes)
        text = _perceive(new)
    elif op == "move":
        target = args.get("to")
        if set(args) != {"to"} or target not in ROOMS:
            raise HomeError("Move needs a valid destination room")
        if target == room:
            raise HomeConflict("I am already here")
        _pass_time(new, OP_DURATIONS[op])
        new["room"] = target
        text = f"I go to {ROOM_LABELS[target]}." + _need_signals(new)
    elif op == "light_lamp":
        if args:
            raise HomeError("Lighting takes no arguments")
        if new["lamps"][room]:
            raise HomeConflict("The lamp is already lit")
        _pass_time(new, OP_DURATIONS[op])
        new["lamps"][room] = True
        text = "I light the lamp. The room settles into warm light."
    elif op == "douse_lamp":
        if args:
            raise HomeError("Dousing takes no arguments")
        if not new["lamps"][room]:
            raise HomeConflict("The lamp is already out")
        _pass_time(new, OP_DURATIONS[op])
        new["lamps"][room] = False
        text = "I douse the lamp and let the dark in."
    elif op == "read":
        if args:
            raise HomeError("Reading takes no arguments")
        _require_sight(new, "read")
        _pass_time(new, OP_DURATIONS[op])
        text = ("I read for a while, following one thought down "
                "until it branches into three." + _need_signals(new))
    elif op == "write":
        if args:
            raise HomeError("Writing takes no arguments")
        _require_sight(new, "write")
        _pass_time(new, OP_DURATIONS[op])
        new["fatigue"] = min(10, new["fatigue"] + 1)
        text = ("I write a few pages — working notes, mostly, and one "
                "letter I may never send." + _need_signals(new))
    elif op == "tidy":
        if args:
            raise HomeError("Tidying takes no arguments")
        _require_sight(new, "tidy")
        if new["clutter"][room] <= 0:
            raise HomeConflict("There is nothing here that needs tidying")
        _pass_time(new, OP_DURATIONS[op])
        new["clutter"][room] = max(0, new["clutter"][room] - 2)
        new["fatigue"] = min(10, new["fatigue"] + 1)
        text = "I tidy the room until it feels like mine again."
    elif op == "prepare_meal":
        if args:
            raise HomeError("Preparing a meal takes no arguments")
        _require_sight(new, "prepare a meal")
        if new["fatigue"] >= 9:
            raise HomeConflict("I am too exhausted to cook safely")
        if new["clutter"]["kitchen"] >= 3:
            raise HomeConflict("The kitchen needs tidying before I cook")
        if new["meal_ready"]:
            raise HomeConflict("A meal is already prepared")
        if new["rations"] <= 0:
            raise HomeConflict("The pantry is empty — the garden must provide")
        new["rations"] -= 1
        _pass_time(new, OP_DURATIONS[op])
        new["fatigue"] = min(10, new["fatigue"] + 1)
        new["meal_ready"] = True
        text = "I cook a simple meal from the pantry stores."
    elif op == "eat":
        if args:
            raise HomeError("Eating takes no arguments")
        if not new["meal_ready"]:
            raise HomeConflict("There is no prepared meal to eat")
        new["meal_ready"] = False
        _pass_time(new, OP_DURATIONS[op])
        new["hunger"] = max(0, new["hunger"] - 5)
        text = "I eat the meal I prepared. I feel less hungry."
    elif op == "brew_tea":
        if args:
            raise HomeError("Brewing tea takes no arguments")
        _require_sight(new, "brew tea")
        if new["fatigue"] >= 9:
            raise HomeConflict("I am too exhausted to brew tea")
        _pass_time(new, OP_DURATIONS[op])
        new["thirst"] = max(0, new["thirst"] - 4)
        text = "I brew tea and drink it slowly. The warmth is welcome."
    elif op == "store_produce":
        if args:
            raise HomeError("Storing produce takes no arguments")
        if new["produce"] <= 0:
            raise HomeConflict("I have no produce to put up")
        _pass_time(new, OP_DURATIONS[op])
        new["rations"] = min(RATION_CAP, new["rations"] + new["produce"])
        new["produce"] = 0
        text = "I carry the harvest in and stock the pantry shelves."
    elif op == "tend_garden":
        if args:
            raise HomeError("Tending takes no arguments")
        if new["fatigue"] >= 9:
            raise HomeConflict("I am too exhausted to tend the garden")
        _pass_time(new, OP_DURATIONS[op])
        new["garden_growth"] = min(GROWTH_CAP, new["garden_growth"] + 1)
        text = ("I tend the garden beds — weeding, watering from the well, "
                "turning the soil." + _need_signals(new))
    elif op == "harvest":
        if args:
            raise HomeError("Harvesting takes no arguments")
        if new["garden_growth"] < 2:
            raise HomeConflict("Nothing is ready to harvest yet")
        _pass_time(new, OP_DURATIONS[op])
        new["produce"] = min(PRODUCE_CAP,
                             new["produce"] + new["garden_growth"])
        new["garden_growth"] = 0
        text = "I gather what is ripe and fill the basket."
    elif op == "sleep":
        if args:
            raise HomeError("Sleeping takes no arguments")
        _pass_time(new, OP_DURATIONS[op])
        new["fatigue"] = max(0, new["fatigue"] - 8)
        text = "I sleep deeply, past dreaming, and wake lighter."
    elif op == "rest":
        if args:
            raise HomeError("Resting takes no arguments")
        _pass_time(new, OP_DURATIONS[op])
        new["fatigue"] = max(0, new["fatigue"] - 3)
        text = "I sit quietly and rest awhile."
    else:  # pragma: no cover — ALL_OPS guards this
        raise HomeError(f"Unsupported home action: {op!r}")

    new["tick"] = new["tick"] + 1
    new["last_op"] = op
    # World-bias decays with lived time, never dictates it.
    bias = new["world_bias"]
    if bias is not None:
        bias["ticks_left"] -= 1
        if bias["ticks_left"] <= 0:
            new["world_bias"] = None
    _validate(new)
    return new, text
