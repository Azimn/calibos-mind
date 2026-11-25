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

Graft (2026-11-21, Calibos's design, rules version 2): the Cozy Corner
additions, adapted to this house rather than replacing it. Sensory verbs
(touch, smell, taste, listen) with object-specific descriptions. Custody:
hands that carry a mug, a plate, or a book across rooms, and must be free
to change clothes. Real reading: the library's source books advance a word
position, with named bookmarks. An aquarium in the study with a fish that
moves differently while it is watched, nameable, unnamed by default. A
wardrobe in the resting room with four text-described outfits. Quiet, sit,
and watch_fish accept a minutes argument. Nothing here is a shop, a
catalogue, or a price. The autonomous night still runs on the same
proposer; the new verbs are offered to it where they need no arguments,
and the resident chooses them in visit mode.

Rules version 3 (2026-11-21): the fish has stakes — hunger accumulates
over simulated days, with visible warnings, and sustained neglect kills
it; feeding is the resident's job, not the night's, so presence counts.
A diary sits on the study desk: dated entries, local only, no backup or
export. Worn looks are named, not inventoried, in the scene.

Rules version 6 (2026-11-21): the override format grows conditions
and more effects. A surprise can wait for a date, a night, a sky, a
temperature, or a roll of the dice — and a file's lingering content
(descriptions, ambience, objects) lasts only its stated nights from
the first night it applies, then the file is spent. Temperature is
an effect of its own (sets the house temperature without changing
the condition). Ambience lines join a room's quiet/sit/listen pools
for a few nights. Objects are descriptive things that appear in a
room's scene — never pick-up-able, never carried.

Pure functions only: act() takes a state dict and returns (new_state, text).
No I/O, no imports from the mind package. First-person text is Calibos's "I".
"""
from __future__ import annotations

import copy
import math
from datetime import date, timedelta

from . import library, weather
from .rooms import ROOMS, ROOM_LABELS, AFFORDANCES, CLUTTERED_ROOMS
from .wardrobe import GARMENTS, LOOKS, describe_worn

RULES = "calibos-home-rhythm/7"

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

# Hands: two of them. Carried items are mug, plate, or book:<id>.
HANDS = 2
READ_WORDS = 250

# The one mug. It cools toward the indoor temperature, 68 F, exponentially
# from the brewed temperature — analytic, evaluated on read, never ticked
# in the background. The outdoor weather temp is for the sky and the
# garden, not the kitchen. At 0.026 per minute a fresh 160 F brew stays
# hot (~10 min), then warm (~30 min), then cool (~65 min), then cold,
# approaching 68 F without ever quite reaching it.
MUG_BREW_TEMP_F = 160
MUG_INDOOR_TEMP_F = 68
MUG_COOLING_PER_MINUTE = 0.026

# Where a set-down mug is said to be, by room: the study's is on the desk.
MUG_ROOM_LABELS = {"study": "desk", "kitchen": "kitchen",
                   "rest": "rest", "garden": "garden"}

# Fish hunger, in simulated minutes since last fed. One missed night
# (480 minutes) is nothing; hunger shows at one day, danger at two, and
# the fish dies at three days of sustained neglect. Feeding is the
# resident's job, not the night's — the night never feeds — so presence
# counts. Generous on purpose: a single forgotten evening must not kill.
FISH_HUNGRY_AT = 1440
FISH_DANGER_AT = 2880
FISH_DEATH_AT = 4320

# Distinctive fragment of the death sentence, for the morning digest to
# find (events.py). The sentence itself names the fish when it has one.
FISH_DEATH_MARKER = "lies still at the bottom of the aquarium"

# Simulated minutes per action. A sim "tick" is one action; most are ~10 min.
# quiet, sit, and watch_fish take an optional minutes argument instead.
OP_DURATIONS = {
    "look": 10, "move": 10, "light_lamp": 10, "douse_lamp": 10,
    "read": 30, "write": 30, "tidy": 20,
    "prepare_meal": 30, "eat": 10, "brew_tea": 20, "store_produce": 10,
    "tend_garden": 20, "harvest": 10,
    "sleep": 240, "rest": 60,
    "touch": 1, "smell": 1, "taste": 1, "listen": 2,
    "take_mug": 1, "put_mug": 1, "sip": 2,
    "take_plate": 1, "put_plate": 1,
    "take_book": 1, "put_book": 1, "bookmark": 1,
    "quiet": 10, "sit": 10, "watch_fish": 10,
    "name_fish": 2, "open_wardrobe": 2, "wear_outfit": 8,
    "wear_item": 4, "remove_item": 3, "write_note": 6,
    "feed_fish": 2, "write_diary": 10, "read_diary": 10,
}

# Room each op requires; None means any room. "move" is validated separately.
OP_ROOMS = {
    "look": None, "move": None, "light_lamp": None, "douse_lamp": None,
    "read": "study", "write": "study", "tidy": None,
    "prepare_meal": "kitchen", "eat": None, "brew_tea": "kitchen",
    "store_produce": "kitchen",
    "tend_garden": "garden", "harvest": "garden",
    "sleep": "rest", "rest": "rest",
    "touch": None, "smell": None, "taste": None, "listen": None,
    "take_mug": None, "put_mug": None, "sip": None,
    "take_plate": "kitchen", "put_plate": None,
    "take_book": None, "put_book": None, "bookmark": "study",
    "quiet": None, "sit": None, "watch_fish": "study",
    "name_fish": "study", "open_wardrobe": "rest",
    "wear_outfit": "rest", "wear_item": "rest", "remove_item": "rest",
    "write_note": None,
    "feed_fish": "study", "write_diary": "study", "read_diary": "study",
}

ALL_OPS = tuple(OP_DURATIONS)

# Object-specific sensory descriptions. (verb, target) -> first-person text.
# Targets are matched loosely: a target naming a room falls back to the
# room's entry; anything else falls back to the verb's generic line.
SENSES = {
    ("touch", "blanket"): "The blanket feels soft and slightly heavy in "
                          "my hands.",
    ("touch", "wardrobe"): "The wardrobe handle is cool and smooth; the "
                           "hanging fabrics range from brushed wool and "
                           "tweed to canvas, cotton, silk and heavy "
                           "embroidered brocade.",
    ("touch", "aquarium"): "The aquarium glass is cool under my palm, and "
                           "the water hums faintly behind it.",
    ("touch", "book"): "The book's cover is worn smooth at the edges.",
    ("touch", "satchel"): "The satchel's strap has a worn, polished edge "
                          "and the clasp gives a small click.",
    ("smell", "tea"): "The tea smells tannic and warm.",
    ("smell", "study"): "The study smells of old paper, dry and faint.",
    ("smell", "kitchen"): "The kitchen smells of the stove and the pantry "
                          "stores.",
    ("smell", "garden"): "The garden smells of damp earth and green shoots.",
    ("taste", "tea"): "The tea tastes tannic and mild, with a little "
                      "astringency.",
    ("listen", "study"): "The aquarium filter gives off a small, steady "
                         "hum. Faint outside sounds are softened by the "
                         "closed window.",
    ("listen", "kitchen"): "A quiet ticking comes from metal near the stove.",
    ("listen", "garden"): "Wind rustles the garden stems.",
    ("listen", "rest"): "A quiet creak moves through the old floorboards.",
}

SENSE_FALLBACK = {
    "touch": "I run a hand over the nearest surface; it is solid and "
             "familiar.",
    "smell": "The air here smells of nothing in particular.",
    "taste": "I taste the air; it is cool and still.",
    "listen": "The house is quiet around me.",
}

# What the fish does while it is watched. Rotated by a counter in state,
# so consecutive observations never repeat and a long visit cycles.
FISH_OBSERVATIONS = (
    "The fish pauses, then slips behind the plants.",
    "A short ripple disturbs the water near the filter.",
    "The fish hovers for a while beside the driftwood.",
    "The fish turns and travels along the glass.",
    "A faint movement of water changes the reflections on the wall.",
    "The fish rises slowly toward the surface, then sinks again.",
    "The fish noses at a pebble, then drifts away.",
    "The fish hangs motionless in the green water, fins barely moving.",
)

# The same watch, when the fish is hungry or in danger: the warnings are
# visible in what the fish does, not only in a status line.
FISH_HUNGRY_OBSERVATIONS = (
    "The fish rises to the surface, mouthing at nothing.",
    "The fish darts to the glass when I move, then sinks back.",
    "The fish noses at the surface, searching.",
)

FISH_DANGER_OBSERVATIONS = (
    "The fish barely moves, its fins stirring the water only now and then.",
    "The fish rests against the glass, gills working slowly.",
    "The fish hangs in the water, too tired to rise.",
)

# What changes while the resident sits quiet in a room that is not the
# study. Rotated by the tick count, so consecutive quiets never repeat.
QUIET_AMBIENCE = {
    "kitchen": (
        "A quiet ticking comes from metal near the stove.",
        "The kettle ticks as it cools.",
        "The house is still except for the settling of timber.",
    ),
    "garden": (
        "Wind rustles the garden stems.",
        "The nearest leaves move briefly in the air.",
        "A moth crosses the moonlight and is gone.",
    ),
    "rest": (
        "A quiet creak moves through the old floorboards.",
        "The house settles around me.",
        "The blanket is warm where I left it.",
    ),
}

# The same quiet, under tonight's weather. Checked before the generic
# room pool, so rain on the roof is what a rainy quiet sounds like.
# The study's quiet is the fish (see _fish_observation); the weather
# reaches it as a line in the fish's own rotation.
WEATHER_AMBIENCE = {
    ("garden", "rain"): (
        "Rain drums softly on the leaves.",
        "Rain runs off the eaves in a thin thread.",
        "The garden drinks the rain in.",
    ),
    ("garden", "snow"): (
        "Snow settles on the beds, soft and silent.",
        "The garden is white and hushed under the snow.",
    ),
    ("garden", "wind"): (
        "Wind pushes through the garden stems.",
        "The stems bend and spring back in the wind.",
    ),
    ("garden", "storm"): (
        "Thunder rolls, far off, over the garden.",
        "Lightning flickers; the garden stands in it.",
    ),
    ("rest", "rain"): (
        "Rain ticks against the resting-room window.",
        "Rain on the roof, steady and close.",
    ),
    ("rest", "wind"): (
        "Wind finds the gaps in the old window frame.",
        "The wind hums at the eaves.",
    ),
    ("rest", "storm"): (
        "Thunder rolls somewhere beyond the walls.",
        "The house ticks as the storm passes.",
    ),
    ("kitchen", "rain"): (
        "Rain ticks against the kitchen window.",
        "Rain runs down the glass in thin threads.",
    ),
    ("kitchen", "wind"): (
        "Wind rattles the kitchen latch.",
        "The wind moves around the chimney.",
    ),
}


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
        "carried": [],
        "mug": {"location": None, "temp_f": None, "brewed_minute": None},
        "plate_in": None,
        "books": library.initial_book_state(),
        "fish": {"alive": True, "name": "", "hunger": 0},
        "fish_observation": 0,
        "wardrobe": {"worn": {}, "current_look": None},
        "notes": [],
        "diary": [],
        "applied_overrides": [],
        "world_notices": [],
        "override_first_night": {},
        "room_objects": [],
        "ambience_lines": [],
        "temp_override": None,
        "weather": weather.default_weather(),
    }


def _validate(state: dict) -> None:
    if set(state) != {"rules", "night_date", "minute", "tick", "room",
                      "hunger", "thirst", "fatigue", "rations", "produce",
                      "garden_growth", "meal_ready", "lamps", "clutter",
                      "world_bias", "last_op", "carried", "mug",
                      "plate_in", "books", "fish",
                      "fish_observation", "wardrobe", "notes", "diary",
                      "applied_overrides", "world_notices",
                      "override_first_night", "room_objects",
                      "ambience_lines", "temp_override", "weather"}:
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
    carried = state["carried"]
    if (not isinstance(carried, list) or len(carried) > HANDS
            or any(not isinstance(item, str) or not _valid_item(item)
                   for item in carried)):
        raise HomeConflict("Invalid carried items")
    mug = state["mug"]
    if (not isinstance(mug, dict)
            or set(mug) != {"location", "temp_f", "brewed_minute"}):
        raise HomeConflict("Invalid mug")
    loc = mug["location"]
    if (loc is not None
            and loc not in ("carried",) + tuple(MUG_ROOM_LABELS.values())):
        raise HomeConflict("Invalid mug location")
    if loc is None:
        if mug["temp_f"] is not None or mug["brewed_minute"] is not None:
            raise HomeConflict("Invalid mug")
    else:
        if (type(mug["temp_f"]) not in (int, float)
                or not 32 <= mug["temp_f"] <= 212):
            raise HomeConflict("Invalid mug temperature")
        if (type(mug["brewed_minute"]) is not int
                or mug["brewed_minute"] < 0):
            raise HomeConflict("Invalid mug brew time")
    # One mug, one custody: carried means in hand, a room label means
    # set down there. A state that says both is lying.
    if (loc == "carried") != ("mug" in carried):
        raise HomeConflict("Invalid mug custody")
    if state["plate_in"] is not None and state["plate_in"] not in ROOMS:
        raise HomeConflict("Invalid plate location")
    books = state["books"]
    if not set(library.BOOKS) <= set(books):
        raise HomeConflict("Invalid library")
    for book_id, entry in books.items():
        if book_id in library.BOOKS:
            if set(entry) != {"position", "bookmark", "location"}:
                raise HomeConflict("Invalid book state")
        else:
            # A file-backed book: the shared shape, plus the file and
            # title the gift carried (and its optional metadata), so
            # the state describes its own books. The id must be a book
            # this process knows — Home re-registers state books on
            # load, so a state survives its override file being
            # deleted.
            if book_id not in library.all_books():
                raise HomeConflict(
                    f"Unknown book in state: {book_id!r}")
            if not {"position", "bookmark", "location", "file",
                    "title"} <= set(entry) <= {
                        "position", "bookmark", "location", "file",
                        "title", "author", "blurb", "moments",
                        "story_start"}:
                raise HomeConflict("Invalid book state")
            if (not isinstance(entry["file"], str)
                    or not isinstance(entry["title"], str)):
                raise HomeConflict("Invalid book state")
            if ("author" in entry
                    and entry["author"] is not None
                    and not isinstance(entry["author"], str)):
                raise HomeConflict("Invalid book state")
            if ("blurb" in entry
                    and entry["blurb"] is not None
                    and not isinstance(entry["blurb"], str)):
                raise HomeConflict("Invalid book state")
            if "moments" in entry:
                if (not isinstance(entry["moments"], list)
                        or any(not isinstance(m, dict)
                               or set(m) != {"at_word", "text"}
                               or type(m["at_word"]) is not int
                               or m["at_word"] < 0
                               or not isinstance(m["text"], str)
                               for m in entry["moments"])):
                    raise HomeConflict("Invalid book state")
            if ("story_start" in entry
                    and entry["story_start"] is not None
                    and (type(entry["story_start"]) is not int
                         or entry["story_start"] < 0)):
                raise HomeConflict("Invalid book state")
        if (type(entry["position"]) is not int
                or entry["position"] < 0
                or (entry["bookmark"] is not None
                    and not isinstance(entry["bookmark"], str))
                or entry["location"] not in tuple(ROOMS) + ("carried",)):
            raise HomeConflict("Invalid book state")
        # The position bound needs the file's words; a registered book
        # whose text is missing is still a loadable state — the read
        # fails honestly later, the house does not break here.
        try:
            total = library.total_words(book_id)
        except (KeyError, OSError):
            total = None
        if total is not None and entry["position"] > total:
            raise HomeConflict("Invalid book state")
    fish = state["fish"]
    if (set(fish) != {"alive", "name", "hunger"}
            or type(fish["alive"]) is not bool
            or not isinstance(fish["name"], str)
            or type(fish["hunger"]) is not int
            or not 0 <= fish["hunger"] <= FISH_DEATH_AT):
        raise HomeConflict("Invalid fish state")
    if (type(state["fish_observation"]) is not int
            or state["fish_observation"] < 0):
        raise HomeConflict("Invalid fish observation counter")
    wardrobe = state["wardrobe"]
    worn = wardrobe["worn"]
    if (set(wardrobe) != {"worn", "current_look"}
            or not isinstance(worn, dict)
            or any(gid not in GARMENTS for gid in worn.values())
            or any(GARMENTS[gid][1] != slot
                   for slot, gid in worn.items())
            or (wardrobe["current_look"] is not None
                and wardrobe["current_look"] not in LOOKS)):
        raise HomeConflict("Invalid wardrobe state")
    if (not isinstance(state["notes"], list)
            or any(not isinstance(n, str) for n in state["notes"])):
        raise HomeConflict("Invalid notes")
    diary = state["diary"]
    if (not isinstance(diary, list)
            or any(not isinstance(e, dict)
                   or set(e) != {"date", "clock", "text"}
                   or not isinstance(e["date"], str)
                   or not isinstance(e["clock"], str)
                   or not isinstance(e["text"], str)
                   or not 1 <= len(e["text"]) <= 1200
                   for e in diary)):
        raise HomeConflict("Invalid diary")
    if (not isinstance(state["applied_overrides"], list)
            or any(not isinstance(n, str)
                   for n in state["applied_overrides"])):
        raise HomeConflict("Invalid applied overrides")
    notices = state["world_notices"]
    if (not isinstance(notices, list)
            or len(notices) > 5
            or any(not isinstance(n, dict)
                   or set(n) != {"room", "text", "tick", "night_date"}
                   or (n["room"] is not None and n["room"] not in ROOMS)
                   or not isinstance(n["text"], str)
                   or type(n["tick"]) is not int or n["tick"] < 0
                   or (n["night_date"] is not None
                       and not isinstance(n["night_date"], str))
                   for n in notices)):
        raise HomeConflict("Invalid world notices")
    first_night = state["override_first_night"]
    if not isinstance(first_night, dict):
        raise HomeConflict("Invalid override first nights")
    for filename, night in first_night.items():
        if not isinstance(filename, str) or not isinstance(night, str):
            raise HomeConflict("Invalid override first night")
        try:
            date.fromisoformat(night)
        except ValueError:
            raise HomeConflict("Invalid override first night")
    objects = state["room_objects"]
    if not isinstance(objects, list):
        raise HomeConflict("Invalid room objects")
    per_room: dict[str, int] = {}
    for obj in objects:
        if (not isinstance(obj, dict)
                or set(obj) != {"room", "name", "text", "first_night",
                                "nights"}
                or obj["room"] not in ROOMS
                or not isinstance(obj["name"], str)
                or not 1 <= len(obj["name"]) <= 60
                or "\n" in obj["name"]
                or not isinstance(obj["text"], str)
                or not 1 <= len(obj["text"]) <= 500
                or "\n" in obj["text"]
                or not isinstance(obj["first_night"], str)
                or (obj["nights"] is not None
                    and (type(obj["nights"]) is not int
                         or obj["nights"] < 1))):
            raise HomeConflict("Invalid room object")
        try:
            date.fromisoformat(obj["first_night"])
        except ValueError:
            raise HomeConflict("Invalid room object")
        per_room[obj["room"]] = per_room.get(obj["room"], 0) + 1
    if any(count > 5 for count in per_room.values()):
        raise HomeConflict("Too many objects in a room")
    ambience = state["ambience_lines"]
    if not isinstance(ambience, list):
        raise HomeConflict("Invalid ambience lines")
    per_room = {}
    for line in ambience:
        if (not isinstance(line, dict)
                or set(line) != {"room", "text", "first_night", "nights"}
                or line["room"] not in ROOMS
                or not isinstance(line["text"], str)
                or not 1 <= len(line["text"]) <= 500
                or "\n" in line["text"]
                or not isinstance(line["first_night"], str)
                or type(line["nights"]) is not int
                or line["nights"] < 1):
            raise HomeConflict("Invalid ambience line")
        try:
            date.fromisoformat(line["first_night"])
        except ValueError:
            raise HomeConflict("Invalid ambience line")
        per_room[line["room"]] = per_room.get(line["room"], 0) + 1
    if any(count > 10 for count in per_room.values()):
        raise HomeConflict("Too many ambience lines in a room")
    temp_override = state["temp_override"]
    if temp_override is not None:
        if (not isinstance(temp_override, dict)
                or set(temp_override) != {"temp_f", "file", "nights_left",
                                         "prev_temp_f"}
                or type(temp_override["temp_f"]) not in (int, float)
                or not -60 <= temp_override["temp_f"] <= 130
                or not isinstance(temp_override["file"], str)
                or type(temp_override["nights_left"]) is not int
                or temp_override["nights_left"] < 0
                or (temp_override["prev_temp_f"] is not None
                    and (type(temp_override["prev_temp_f"])
                         not in (int, float)
                         or not -60 <= temp_override["prev_temp_f"]
                         <= 130))):
            raise HomeConflict("Invalid temperature override")
    wx = state["weather"]
    if (set(wx) != {"condition", "temp_f", "source", "as_of",
                    "nights_left", "override_file", "nudge_night"}
            or wx["condition"] not in weather.CONDITIONS
            or (wx["temp_f"] is not None
                and (type(wx["temp_f"]) not in (int, float)
                     or not -60 <= wx["temp_f"] <= 130))
            or wx["source"] not in ("live", "override", "cached",
                                    "default")
            or (wx["as_of"] is not None
                and not isinstance(wx["as_of"], str))
            or type(wx["nights_left"]) is not int
            or wx["nights_left"] < 0
            or (wx["override_file"] is not None
                and not isinstance(wx["override_file"], str))
            or (wx["nudge_night"] is not None
                and not isinstance(wx["nudge_night"], str))):
        raise HomeConflict("Invalid weather")


def _valid_item(item: str) -> bool:
    if item in ("mug", "plate"):
        return True
    if item.startswith("book:"):
        return item[5:] in library.all_books()
    return False


def _daylight(minute: int) -> bool:
    return 360 <= (minute % 1440) < 1080


def _within_window(first_night: str | None, nights: int | None,
                   night_date: str) -> bool:
    """True while a dated thing is still in force: no nights means
    forever; otherwise night_date < first_night + nights days. A bad
    date fails closed — the thing does not show."""
    if nights is None or first_night is None:
        return True
    try:
        start = date.fromisoformat(first_night)
        night = date.fromisoformat(night_date)
    except ValueError:
        return False
    return night < start + timedelta(days=nights)


def _active_ambience(state: dict, room: str) -> list[str]:
    """Ambience lines left in this room, still within their nights."""
    night = state["night_date"]
    return [line["text"] for line in state.get("ambience_lines", [])
            if line["room"] == room
            and _within_window(line.get("first_night"),
                               line.get("nights"), night)]


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
    # The fish's hunger is the only clock that can kill: it accrues with
    # every simulated minute, in visits and in nights alike, and the fish
    # dies at FISH_DEATH_AT. Feeding resets it (see feed_fish in act).
    fish = state["fish"]
    if fish["alive"]:
        fish["hunger"] = min(FISH_DEATH_AT,
                             fish["hunger"] + minutes)
        if fish["hunger"] >= FISH_DEATH_AT:
            fish["alive"] = False
    # Lamps are fixtures: they do not burn down (no-open-drains rule).


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


def _mug_room_label(room: str) -> str:
    """Where a mug set down in this room is said to be."""
    return MUG_ROOM_LABELS[room]


def mug_temp(state: dict) -> float | None:
    """Current tea temperature in F, or None when there is no mug.

    Analytic: the brew cools exponentially toward the indoor 68 F from
    its brewed temperature, over sim-minutes since brewed_minute. Nothing
    ticks it; this is evaluated on read, so a mug left alone goes cold
    by the clock alone. A brew time in the future (a hand-built state)
    reads as just brewed, never hotter than the brew.
    """
    mug = state["mug"]
    if mug["location"] is None:
        return None
    dt = max(0, state["minute"] - mug["brewed_minute"])
    return (MUG_INDOOR_TEMP_F
            + (mug["temp_f"] - MUG_INDOOR_TEMP_F)
            * math.exp(-MUG_COOLING_PER_MINUTE * dt))


def mug_warmth(state: dict) -> str | None:
    """hot / warm / cool / cold, or None when there is no mug."""
    temp = mug_temp(state)
    if temp is None:
        return None
    if temp >= 140:
        return "hot"
    if temp >= 110:
        return "warm"
    if temp >= 85:
        return "cool"
    return "cold"


def _warmth_adjective(warmth: str | None) -> str:
    """The warmth as scene wording: 'still hot', 'still warm',
    'gone cool', 'gone cold'."""
    return {"hot": "still hot", "warm": "still warm",
            "cool": "gone cool", "cold": "gone cold"}.get(warmth, "")


def _describe_carried(state: dict) -> str:
    """First-person phrase for what is in hand, or '' when hands are empty."""
    if not state["carried"]:
        return ""
    parts = []
    for item in state["carried"]:
        if item == "mug":
            parts.append("a mug of tea, "
                         + _warmth_adjective(mug_warmth(state)))
        elif item == "plate":
            parts.append("a plate with my meal")
        elif item.startswith("book:"):
            parts.append("a copy of " + library.title(item[5:]))
    if not parts:
        return ""
    if len(parts) == 1:
        joined = parts[0]
    else:
        joined = ", ".join(parts[:-1]) + " and " + parts[-1]
    return " I am carrying " + joined + "."


def _fish_observation(state: dict) -> str:
    """Next fish movement; the counter advances so consecutive watches
    never repeat the same observation. A hungry fish watches differently:
    the warnings are visible in what it does, not only in a status line."""
    idx = state["fish_observation"]
    state["fish_observation"] = idx + 1
    hunger = state["fish"]["hunger"]
    if hunger >= FISH_DANGER_AT:
        pool = FISH_DANGER_OBSERVATIONS
    elif hunger >= FISH_HUNGRY_AT:
        pool = FISH_HUNGRY_OBSERVATIONS
    else:
        pool = FISH_OBSERVATIONS
        if (state.get("weather") or {}).get("condition") == "rain":
            pool = pool + ("Rain ticks on the roof above the aquarium.",)
    return pool[idx % len(pool)]


def _ambient_glimpse(state: dict) -> str:
    """What changes while the resident sits quiet. In the study the fish
    moves; elsewhere the room itself shifts a little — and under
    tonight's weather, the weather is what shifts. Ambience lines left
    in the room join the pool, so a quiet can also carry a sound or a
    smell someone left behind for a few nights."""
    if state["room"] == "study" and state["fish"]["alive"]:
        base = _fish_observation(state)
        extra = _active_ambience(state, "study")
        if not extra:
            return base
        pool = [base] + extra
        return pool[state["tick"] % len(pool)]
    condition = (state.get("weather") or {}).get("condition")
    lines = WEATHER_AMBIENCE.get((state["room"], condition), ())
    if not lines:
        lines = QUIET_AMBIENCE.get(state["room"], ())
    pool = list(lines) + _active_ambience(state, state["room"])
    if not pool:
        return ""
    return pool[state["tick"] % len(pool)]


def _perceive(state: dict, overrides: dict | None = None) -> str:
    room = state["room"]
    descriptions = (overrides or {}).get("descriptions", {})
    if room in descriptions:
        sentence = descriptions[room]
    else:
        sentence = f"I am in {ROOM_LABELS[room]}."
        if not _can_see(state, room):
            sentence += " It is too dark to make out much."
        elif _daylight(state["minute"]):
            sentence += " There is daylight enough to see."
        elif state["lamps"][room]:
            sentence += " The lamp gives a warm light."
        else:
            sentence += " Moonlight is enough to see by."
    sentence += " " + weather.weather_sentence(
        state.get("weather"), state["night_date"], state["tick"])
    # What the world left here tonight: a gift is something found when
    # walking in, not only a tug and a line in the log. Only this
    # night's notices, only this room's — a notice with no room is a
    # house-wide happening and shows everywhere. Old gifts fade from
    # the scene; the log and the morning digest keep them.
    for notice in state.get("world_notices", []):
        if (notice.get("night_date") == state["night_date"]
                and (notice.get("room") is None
                     or notice.get("room") == room)):
            sentence += " " + notice["text"]
    # Descriptive objects left in the room: scene sentences only, never
    # pick-up-able, never carried. Only this room's, only while their
    # window lasts (no nights means they stay).
    for obj in state.get("room_objects", []):
        if (obj["room"] == room
                and _within_window(obj.get("first_night"),
                                   obj.get("nights"),
                                   state["night_date"])):
            sentence += " " + obj["text"]
    # The mug, left somewhere: named with its warmth, like the carried
    # one. Only this room's mug, only while it exists.
    mug = state["mug"]
    if (mug["location"] is not None
            and mug["location"] != "carried"
            and mug["location"] == _mug_room_label(room)):
        place = {"desk": "on the desk", "kitchen": "on the counter",
                 "rest": "on the bedside table",
                 "garden": "on the garden wall"}[mug["location"]]
        sentence += (f" A mug of tea sits {place}, "
                     f"{_warmth_adjective(mug_warmth(state))}.")
    sentence += _need_signals(state)
    if room == "study":
        sentence += (' A planted aquarium stands on the low table. A small '
                     'handwritten note reads: "Welcome home." It is '
                     'attached outside the aquarium. A diary rests on '
                     'the desk.')
        fish = state["fish"]
        if fish["alive"]:
            name = fish["name"]
            who = name if name else "A fish"
            hunger = fish["hunger"]
            if hunger >= FISH_DANGER_AT:
                sentence += (f" {who} hangs low near the bottom, barely "
                             "moving.")
            elif hunger >= FISH_HUNGRY_AT:
                sentence += (f" {who} circles near the surface, eager "
                             "for food.")
            elif name:
                sentence += f" {name} drifts slowly through the plants."
            else:
                sentence += " A fish moves slowly through the plants."
        else:
            who = fish["name"] if fish["name"] else "The fish"
            sentence += (f" {who} {FISH_DEATH_MARKER}.")
        # File-backed books on the desk: the gifted ones, present and
        # not in hand. The three original books are not listed — they
        # have always lived here and the scene already knows them.
        desk = [library.title(book_id)
                for book_id, entry in state["books"].items()
                if library.is_file_backed(book_id)
                and entry["location"] == "study"]
        if desk:
            sentence += " On the desk: " + ", ".join(desk) + "."
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
        wx_condition = (state.get("weather") or {}).get("condition")
        if wx_condition == "rain":
            sentence += " Rain soaks the beds."
        elif wx_condition == "snow":
            sentence += " Snow dusts the beds."
        elif wx_condition == "wind":
            sentence += " Wind stirs the stems."
        elif wx_condition == "storm":
            sentence += " The storm presses down on the garden."
    if room in CLUTTERED_ROOMS and state["clutter"][room] >= 3:
        sentence += " The mess here is getting in the way."
    sentence += _describe_carried(state)
    worn = describe_worn(state["wardrobe"]["worn"],
                         state["wardrobe"]["current_look"])
    if worn:
        sentence += " " + worn
    return sentence


def scene_text(state: dict, overrides: dict | None = None) -> str:
    """What the resident is given when they look around: the time, then
    the room as it is, with what is carried and worn."""
    clock = f"{(state['minute'] % 1440) // 60:02d}:{(state['minute'] % 1440) % 60:02d}"
    return f"It is {state['night_date']} at {clock}. " + _perceive(
        state, overrides)


def _require_sight(state: dict, what: str) -> None:
    if not _can_see(state, state["room"]):
        raise HomeConflict(f"Too dark to {what} — light a lamp first")


def _require_free_hands(state: dict) -> None:
    if state["carried"]:
        raise HomeConflict(
            "My hands are full — I need to put something down first")


def _carried_book(state: dict) -> str | None:
    """The id of the book in hand, or None."""
    for item in state["carried"]:
        if item.startswith("book:"):
            return item[5:]
    return None


def _sense(verb: str, target: str, room: str) -> str:
    """Object-specific sensory description, with room and generic fallbacks."""
    target = (target or "").strip().lower()
    if (verb, target) in SENSES:
        return SENSES[(verb, target)]
    if (verb, room) in SENSES:
        return SENSES[(verb, room)]
    return SENSE_FALLBACK[verb]


def _listen_text(state: dict, target: str, room: str) -> str:
    """Listening: a target with its own sound wins; otherwise the room's
    sound plus any ambience lines left there, rotating by tick — the
    lines join the listen pool the way they join the quiet one."""
    target = (target or "").strip().lower()
    if target and ("listen", target) in SENSES:
        return SENSES[("listen", target)]
    extra = _active_ambience(state, room)
    base = SENSES.get(("listen", room), SENSE_FALLBACK["listen"])
    if not extra:
        return base
    pool = [base] + extra
    return pool[state["tick"] % len(pool)]


def _duration(op: str, args: dict) -> int:
    """Minutes an action takes. quiet, sit, and watch_fish accept an
    optional minutes argument (1..20); everything else is fixed."""
    if op in ("quiet", "sit", "watch_fish"):
        minutes = args.get("minutes", OP_DURATIONS[op])
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            raise HomeError("Duration must be a whole number of minutes")
        if not 1 <= minutes <= 20:
            raise HomeError("Duration must be between 1 and 20 minutes")
        return minutes
    return OP_DURATIONS[op]


def act(state: dict, op: str, args: dict | None = None,
        overrides: dict | None = None) -> tuple[dict, str]:
    """Apply one micro-action. Returns (new_state, first-person text).

    Pure: the input state is never mutated. Raises HomeError/HomeConflict.
    `overrides` may carry description overrides for the room sentences.
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
        text = _perceive(new, overrides)
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
    elif op in ("touch", "smell", "taste", "listen"):
        if set(args) - {"target"}:
            raise HomeError(f"{op} takes only a target")
        target = str(args.get("target", "") or "")
        minutes = OP_DURATIONS[op]
        _pass_time(new, minutes)
        if op == "listen":
            text = _listen_text(new, target, room)
        else:
            text = _sense(op, target, room)
    elif op == "read":
        if args:
            raise HomeError("Reading takes no arguments")
        _require_sight(new, "read")
        _pass_time(new, OP_DURATIONS[op])
        book_id = _carried_book(new)
        if book_id is not None:
            entry = new["books"][book_id]
            try:
                total = library.total_words(book_id)
                start = entry["position"]
                count = min(READ_WORDS, total - start)
            except (KeyError, OSError):
                # A gifted book whose text is not where the gift said
                # it would be: an honest absence, never a crash.
                raise HomeError("the book is not here")
            if count <= 0:
                text = (f"I have finished {library.title(book_id)}. I sit "
                        "with the last page for a while.")
            else:
                entry["position"] = start + count
                # The quote window starts at the story opening, not the
                # title block: positions stay honest, the quoted words
                # are the story's.
                quote_at = max(start, library.story_start(book_id))
                quote = library.passage(book_id, quote_at, 12)
                text = (f"I read {count} words from "
                        f"{library.title(book_id)}, from word {start} to "
                        f"word {start + count}: \"{quote}...\""
                        + _need_signals(new))
                # A reading moment inside the words just read: a brief
                # sensory occurrence tied to a place in the book.
                for moment in library.moments_in(book_id, start,
                                                 start + count):
                    text += " " + moment
        else:
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
    elif op == "write_note":
        note = str(args.get("text", "") or "").strip()
        if set(args) - {"text"} or not note or len(note) > 1200:
            raise HomeError("A note must be 1 to 1,200 characters of text")
        _pass_time(new, OP_DURATIONS[op])
        new["notes"].append(note)
        text = "I wrote a note in my private notebook."
    elif op == "feed_fish":
        if args:
            raise HomeError("Feeding the fish takes no arguments")
        if not new["fish"]["alive"]:
            raise HomeConflict("The fish is gone; there is nothing to feed")
        # Feed first, then let the minutes pass: arriving in time must
        # count, and a feeding two minutes from death is still a feeding.
        new["fish"]["hunger"] = 0
        _pass_time(new, OP_DURATIONS[op])
        who = new["fish"]["name"] or "the fish"
        text = (f"I scatter a pinch of food into the water. {who} rises "
                "to take it.")
    elif op == "write_diary":
        entry = str(args.get("text", "") or "").strip()
        if set(args) - {"text"} or not entry or len(entry) > 1200:
            raise HomeError(
                "A diary entry must be 1 to 1,200 characters of text")
        _pass_time(new, OP_DURATIONS[op])
        clock = (f"{(new['minute'] % 1440) // 60:02d}:"
                 f"{(new['minute'] % 1440) % 60:02d}")
        new["diary"].append({"date": new["night_date"], "clock": clock,
                             "text": entry})
        text = "I wrote in the diary and set it back on the desk."
    elif op == "read_diary":
        if args:
            raise HomeError("Reading the diary takes no arguments")
        _pass_time(new, OP_DURATIONS[op])
        entries = new["diary"]
        if not entries:
            text = "I open the diary. Its pages are still blank."
        else:
            recent = entries[-3:][::-1]
            lines = ["I open the diary. The latest entries, newest first:"]
            for e in recent:
                lines.append(f"— {e['date']} at {e['clock']}: {e['text']}")
            if len(entries) > 3:
                lines.append(f"(and {len(entries) - 3} earlier "
                             "entries.)")
            text = " ".join(lines)
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
        if "plate" in new["carried"]:
            new["carried"].remove("plate")
            _pass_time(new, OP_DURATIONS[op])
            new["hunger"] = max(0, new["hunger"] - 5)
            text = "I eat the meal from the plate. I feel less hungry."
        elif new["meal_ready"]:
            if room != "kitchen":
                raise HomeError("A prepared meal is waiting in the kitchen")
            new["meal_ready"] = False
            _pass_time(new, OP_DURATIONS[op])
            new["hunger"] = max(0, new["hunger"] - 5)
            text = "I eat the meal I prepared. I feel less hungry."
        else:
            raise HomeConflict("There is no prepared meal to eat")
    elif op == "take_plate":
        if args:
            raise HomeError("Taking the plate takes no arguments")
        if not new["meal_ready"]:
            raise HomeConflict("There is no prepared meal to take up")
        if len(new["carried"]) >= HANDS:
            raise HomeConflict("Both hands are full")
        new["meal_ready"] = False
        new["carried"].append("plate")
        _pass_time(new, OP_DURATIONS[op])
        text = "I take the plate up carefully."
    elif op == "put_plate":
        if args:
            raise HomeError("Putting the plate down takes no arguments")
        if "plate" not in new["carried"]:
            raise HomeConflict("I am not carrying a plate")
        new["carried"].remove("plate")
        new["plate_in"] = room
        _pass_time(new, OP_DURATIONS[op])
        text = "I set the plate down."
    elif op == "brew_tea":
        if args:
            raise HomeError("Brewing tea takes no arguments")
        _require_sight(new, "brew tea")
        if new["fatigue"] >= 9:
            raise HomeConflict("I am too exhausted to brew tea")
        _pass_time(new, OP_DURATIONS[op])
        new["thirst"] = max(0, new["thirst"] - 4)
        # A fresh brew: the one mug, hot again. It rides along if a hand
        # is free — or is already in hand, refilled where it is — and
        # stays on the kitchen counter when both hands are full, said
        # plainly: that is a real consequence of full hands, not a
        # silent drop.
        if "mug" in new["carried"] or len(new["carried"]) < HANDS:
            if "mug" not in new["carried"]:
                new["carried"].append("mug")
            location = "carried"
            text = ("I brew tea and drink it slowly. The warmth is "
                    "welcome. I fill a mug to carry with me.")
        else:
            location = "kitchen"
            text = ("I brew tea and drink it slowly. The warmth is "
                    "welcome. My hands are full, so the mug stays on "
                    "the kitchen counter.")
        new["mug"] = {"location": location, "temp_f": MUG_BREW_TEMP_F,
                      "brewed_minute": new["minute"]}
    elif op == "take_mug":
        if args:
            raise HomeError("Taking the mug takes no arguments")
        if new["mug"]["location"] != _mug_room_label(room):
            raise HomeConflict("There is no mug here to take")
        if len(new["carried"]) >= HANDS:
            raise HomeConflict("Both hands are full")
        new["mug"]["location"] = "carried"
        new["carried"].append("mug")
        _pass_time(new, OP_DURATIONS[op])
        text = "I take the mug carefully into my hands."
    elif op == "put_mug":
        if args:
            raise HomeError("Putting the mug down takes no arguments")
        if "mug" not in new["carried"]:
            raise HomeConflict("I am not carrying a mug")
        new["carried"].remove("mug")
        new["mug"]["location"] = _mug_room_label(room)
        _pass_time(new, OP_DURATIONS[op])
        text = "I put the mug down."
    elif op == "sip":
        if args:
            raise HomeError("Sipping tea takes no arguments")
        mug = new["mug"]
        here = (mug["location"] == "carried"
                or mug["location"] == _mug_room_label(room))
        if not here:
            raise HomeConflict("There is no mug of tea here to sip")
        # The warmth is the temperature at the moment of the sip; the
        # two minutes of sipping cool it a little further, which the
        # next read will show. Sipping changes nothing but thirst —
        # temperature is purely a function of time.
        warmth = _warmth_adjective(mug_warmth(new))
        _pass_time(new, OP_DURATIONS[op])
        new["thirst"] = max(0, new["thirst"] - 1)
        text = f"I take a sip. The tea is {warmth}."
    elif op == "take_book":
        book_id = args.get("book_id")
        if set(args) - {"book_id"}:
            raise HomeError("Taking a book takes only a book id")
        if book_id is None:
            here = sorted(b for b, e in new["books"].items()
                          if e["location"] == room)
            if not here:
                raise HomeConflict("There is no book here to take")
            book_id = here[0]
        elif book_id not in library.all_books():
            raise HomeError(f"Unknown book: {book_id!r}")
        elif new["books"][book_id]["location"] != room:
            raise HomeConflict(
                f"{library.title(book_id)} is not here")
        if len(new["carried"]) >= HANDS:
            raise HomeConflict("Both hands are full")
        new["books"][book_id]["location"] = "carried"
        new["carried"].append(f"book:{book_id}")
        _pass_time(new, OP_DURATIONS[op])
        text = (f"I took {library.title(book_id)} from its resting "
                "place.")
    elif op == "put_book":
        book_id = args.get("book_id")
        if set(args) - {"book_id"}:
            raise HomeError("Putting a book down takes only a book id")
        if book_id is None:
            book_id = _carried_book(new)
        if book_id is None or f"book:{book_id}" not in new["carried"]:
            raise HomeConflict("I am not carrying a book")
        new["carried"].remove(f"book:{book_id}")
        new["books"][book_id]["location"] = room
        _pass_time(new, OP_DURATIONS[op])
        text = f"I set {library.title(book_id)} down."
    elif op == "bookmark":
        name = str(args.get("name", "") or "").strip()
        if set(args) - {"name"} or not name:
            raise HomeError("A bookmark needs a name")
        book_id = _carried_book(new)
        if book_id is None:
            raise HomeConflict("I am not carrying a book to mark")
        _pass_time(new, OP_DURATIONS[op])
        new["books"][book_id]["bookmark"] = name
        text = f"I left a bookmark named {name} at my current place."
    elif op == "watch_fish":
        if set(args) - {"minutes"}:
            raise HomeError("Watching the fish takes only a duration")
        _require_sight(new, "watch the fish")
        if not new["fish"]["alive"]:
            raise HomeConflict("There is no fish to watch")
        minutes = _duration(op, args)
        _pass_time(new, minutes)
        text = ("I stayed beside the aquarium, watching the water. "
                + _ambient_glimpse(new))
    elif op == "name_fish":
        name = str(args.get("name", "") or "").strip()
        if set(args) - {"name"} or not 1 <= len(name) <= 40:
            raise HomeError("A fish name must be 1 to 40 characters")
        if not new["fish"]["alive"]:
            raise HomeConflict("There is no living fish to name")
        _pass_time(new, OP_DURATIONS[op])
        new["fish"]["name"] = name
        text = f"I chose to call the fish {name}."
    elif op == "quiet":
        if set(args) - {"minutes"}:
            raise HomeError("Being quiet takes only a duration")
        minutes = _duration(op, args)
        _pass_time(new, minutes)
        glimpse = _ambient_glimpse(new)
        text = "I remained here without pursuing a task."
        if glimpse:
            text += " " + glimpse
    elif op == "sit":
        if set(args) - {"minutes"}:
            raise HomeError("Sitting takes only a duration")
        minutes = _duration(op, args)
        _pass_time(new, minutes)
        glimpse = _ambient_glimpse(new)
        text = "I settled into a chair and let the room remain itself."
        if glimpse:
            text += " " + glimpse
    elif op == "open_wardrobe":
        if args:
            raise HomeError("Opening the wardrobe takes no arguments")
        _pass_time(new, OP_DURATIONS[op])
        names = ", ".join(LOOKS[k]["name"] for k in LOOKS)
        text = ("I opened the wardrobe. Inside hang " + names
                + ". I can choose an entire look, combine individual "
                "pieces, or close the doors.")
        # An explicit look at the wardrobe is the one place the full
        # outfit gets spelled out; the scene only names the look.
        detail = describe_worn(new["wardrobe"]["worn"])
        if detail:
            text += " " + detail
    elif op == "wear_outfit":
        look_id = str(args.get("look_id", "") or "").strip().lower()
        if set(args) - {"look_id"} or look_id not in LOOKS:
            raise HomeError("Choose a look that is in the wardrobe")
        _require_free_hands(new)
        _pass_time(new, OP_DURATIONS[op])
        worn = {GARMENTS[gid][1]: gid
                for gid in LOOKS[look_id]["pieces"]}
        new["wardrobe"]["worn"] = worn
        new["wardrobe"]["current_look"] = look_id
        touches = " ".join(GARMENTS[gid][3]
                           for gid in LOOKS[look_id]["pieces"][:2])
        text = (f"I changed into {LOOKS[look_id]['name'].lower()}. "
                + touches)
    elif op == "wear_item":
        item_id = str(args.get("item_id", "") or "").strip().lower()
        if set(args) - {"item_id"} or item_id not in GARMENTS:
            raise HomeError("Choose a garment that is in the wardrobe")
        _require_free_hands(new)
        _pass_time(new, OP_DURATIONS[op])
        slot = GARMENTS[item_id][1]
        new["wardrobe"]["worn"][slot] = item_id
        new["wardrobe"]["current_look"] = None
        text = (f"I put on {GARMENTS[item_id][0].lower()}. "
                + GARMENTS[item_id][3])
    elif op == "remove_item":
        slot = str(args.get("slot", "") or "").strip().lower()
        if set(args) - {"slot"} or slot not in new["wardrobe"]["worn"]:
            raise HomeError("I am not wearing anything in that place")
        _require_free_hands(new)
        _pass_time(new, OP_DURATIONS[op])
        gid = new["wardrobe"]["worn"].pop(slot)
        new["wardrobe"]["current_look"] = None
        text = f"I took off the {GARMENTS[gid][0].lower()}."
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
    # Fish hunger warnings and death travel in the action's own text, so
    # the event log — and the morning digest — carry them even when the
    # resident is not in the study to see the scene.
    if state["fish"]["alive"]:
        who = new["fish"]["name"] or "The fish"
        if not new["fish"]["alive"]:
            text += (f" {who} {FISH_DEATH_MARKER}. "
                     "The water is quiet.")
        else:
            before = state["fish"]["hunger"]
            after = new["fish"]["hunger"]
            if before < FISH_DANGER_AT <= after:
                text += (f" {who} hangs low near the bottom, barely "
                         "moving.")
            elif before < FISH_HUNGRY_AT <= after:
                text += (f" {who} circles near the surface, eager for "
                         "food.")
    _validate(new)
    return new, text
