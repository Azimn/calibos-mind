"""Rooms of the home and what each one affords.

The dwelling loop (spec v0.2): study, kitchen, garden, rest. The garden is
load-bearing (provisions), not decorative. Cottage skin, not dungeon skin:
affordances are ordinary domestic micro-actions, never grind quests.

This module is pure data: no imports, no I/O. rhythm.py validates against it.
"""
from __future__ import annotations

# Canonical room ids. Order is presentation order only — the putz proposer
# must never treat it as a visit sequence (see tick.py; tested).
ROOMS = ("study", "kitchen", "garden", "rest")

ROOM_LABELS = {
    "study": "the study",
    "kitchen": "the kitchen",
    "garden": "the garden",
    "rest": "the resting room",
}

# Affordances: op names valid in each room. "move" targets are handled
# separately (any room -> any other room). Lamp ops are universal.
# Sensory verbs, quiet, sit, and note-writing are universal too; the
# proposer only ever offers them where the rhythm itself allows them.
AFFORDANCES = {
    "study": ("read", "write", "tidy", "take_book", "put_book", "bookmark",
              "watch_fish", "name_fish", "listen", "touch", "smell",
              "taste", "quiet", "sit", "write_note", "read_diary"),
    "kitchen": ("prepare_meal", "eat", "brew_tea", "store_produce", "tidy",
                "take_mug", "put_mug", "take_plate", "put_plate", "sip_tea",
                "listen", "touch", "smell", "taste", "quiet", "sit",
                "write_note"),
    "garden": ("tend_garden", "harvest", "listen", "touch", "smell",
               "quiet", "sit", "write_note"),
    "rest": ("sleep", "rest", "open_wardrobe", "wear_outfit", "wear_item",
             "remove_item", "listen", "touch", "smell", "quiet", "sit",
             "write_note"),
}

UNIVERSAL_OPS = ("look", "move", "light_lamp", "douse_lamp")

# Rooms whose clutter the inhabitant tracks. The kitchen gate is ported from
# the lab: a filthy kitchen blocks meal preparation. The study's clutter is
# flavor with teeth — a messy study makes reading/writing less appealing to
# the proposer (weight, never a gate).
CLUTTERED_ROOMS = ("kitchen", "study")

# Night lamp state at the start of each off-hours run. The cottage is lived
# in: lamps are lit where the inhabitant expects to be. Unlike the lab, lamps
# burn no oil — they are fixtures, not a supply drain (no-open-drains rule).
LAMPS_AT_NIGHTFALL = {
    "study": True,
    "kitchen": True,
    "garden": False,   # moonlight; the lamp is lit only if the inhabitant
    "rest": True,      # chooses to tend the garden after dark
}

# One-line activity phrases for the traversal snapshot. Keyed by op; "move"
# is formatted with the destination label.
ACTIVITY_PHRASES = {
    "read": "reading in the study",
    "write": "writing",
    "tidy": "tidying up",
    "prepare_meal": "cooking a meal",
    "eat": "eating",
    "brew_tea": "brewing tea",
    "store_produce": "putting up produce",
    "tend_garden": "tending the garden",
    "harvest": "gathering the harvest",
    "sleep": "sleeping",
    "rest": "resting",
    "look": "looking around",
    "light_lamp": "lighting a lamp",
    "douse_lamp": "dousing a lamp",
    "touch": "feeling the room",
    "smell": "smelling the air",
    "taste": "tasting",
    "listen": "listening",
    "take_mug": "taking up the mug",
    "put_mug": "setting the mug down",
    "sip_tea": "sipping tea",
    "take_plate": "taking up the plate",
    "put_plate": "setting the plate down",
    "take_book": "taking up a book",
    "put_book": "setting a book down",
    "bookmark": "marking a page",
    "quiet": "sitting quiet",
    "sit": "sitting",
    "watch_fish": "watching the fish",
    "name_fish": "naming the fish",
    "feed_fish": "feeding the fish",
    "open_wardrobe": "opening the wardrobe",
    "wear_outfit": "changing clothes",
    "wear_item": "changing clothes",
    "remove_item": "changing clothes",
    "write_note": "writing a note",
    "write_diary": "writing in the diary",
    "read_diary": "reading the diary",
}


def activity_phrase(op: str, room: str) -> str:
    """One-line present-tense activity for a snapshot. Never leaks numbers."""
    if op == "move":
        return f"settling into {ROOM_LABELS.get(room, 'the house')}"
    return ACTIVITY_PHRASES.get(op, "puttering about")
