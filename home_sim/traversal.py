"""Traversal interface: the boundary between the high-fidelity home sim and
any simpler game world (the prototype for Pretorius's lab<->Village crossing).

Two directions, both lossy-by-design in the safe direction:

- Export (sim -> world): `snapshot(state, ...)` emits a WorldSnapshot dict —
  coarse presence (room id or "away"), coarse need states (words, never
  numbers), a one-line activity phrase, and a stable identity handle. This is
  what a MUD room would render. No internal state leaks: the snapshot carries
  no floats, no ints for needs, no provisions counts, no bias, no minute.
- Import (world -> sim): `import_world_event(...)` records a WorldEvent in
  the event log with provenance "world" and installs a *weighting* bias for
  the next few ticks. Weighting never dictates: it multiplies proposal
  weights for matching rooms/actions and then decays. It never sets needs,
  rooms, or resources. The sim is never reconstructed from a snapshot.

Contract notes for future implementers (including the lab):
- snapshot() must be a pure function of state + wall-clock context.
- import_world_event() must leave needs/room/resources untouched; the only
  state it may write is the bias envelope and the log record.
- Round-trip fidelity: sim -> snapshot -> event -> sim must not corrupt sim
  state. Tested: 100 round-trips, zero corruption, zero leaked numbers.
"""
from __future__ import annotations

from typing import Any

from .events import EventLog, PROVENANCE_WORLD
from .rooms import ROOMS, activity_phrase

IDENTITY_HANDLE = "calibos"

# Coarse need vocabulary. Bands are uniform and documented; coarse is lossy
# by design, and the lab's own signal thresholds (>=4 peckish, >=7 hungry)
# sit inside these bands rather than defining them.
HUNGER_WORDS = ("comfortable", "peckish", "hungry")
THIRST_WORDS = ("comfortable", "dry", "parched")
FATIGUE_WORDS = ("rested", "tired", "worn-out")


def _coarsen(value: int, words: tuple[str, str, str]) -> str:
    if value <= 3:
        return words[0]
    if value <= 6:
        return words[1]
    return words[2]


def snapshot(state: dict, *, away: bool = False) -> dict:
    """Export a WorldSnapshot. Pure; leaks no internal numbers.

    `away` marks the inhabitant as out of the sim (daytime): presence
    becomes "away" and activity "out in the day". The sim never renders
    itself from this — it is write-only across the boundary.
    """
    if away:
        return {
            "identity": IDENTITY_HANDLE,
            "presence": "away",
            "needs": {
                "hunger": _coarsen(state["hunger"], HUNGER_WORDS),
                "thirst": _coarsen(state["thirst"], THIRST_WORDS),
                "fatigue": _coarsen(state["fatigue"], FATIGUE_WORDS),
            },
            "activity": "out in the day",
            "provenance": "simulated",
        }
    room = state["room"] if state["room"] in ROOMS else "away"
    op = state.get("last_op")
    return {
        "identity": IDENTITY_HANDLE,
        "presence": room,
        "needs": {
            "hunger": _coarsen(state["hunger"], HUNGER_WORDS),
            "thirst": _coarsen(state["thirst"], THIRST_WORDS),
            "fatigue": _coarsen(state["fatigue"], FATIGUE_WORDS),
        },
        "activity": activity_phrase(op, room) if op else "settling in",
        "provenance": "simulated",
    }


def import_world_event(state: dict, log: EventLog, event: dict) -> dict:
    """Import a WorldEvent: record it (provenance "world") and install a
    short-lived weighting bias. Returns the updated state.

    Accepted event shape: {"kind": str, "text": str, "room": <room|None>}.
    `room`, when given, biases the proposer's room weights for the next few
    ticks — a visitor in the garden makes the garden more likely, never
    mandatory. Needs, room, and resources are never touched here.
    """
    kind = event.get("kind", "notice")
    text = event.get("text", "Something happened in the world.")
    room_hint = event.get("room")
    if room_hint is not None and room_hint not in ROOMS:
        raise ValueError(f"unknown room in world event: {room_hint!r}")

    new_state = dict(state)
    # Shallow-copy the bias envelope so the caller's state is never mutated.
    new_state["world_bias"] = {"room": room_hint, "ticks_left": 6}

    log.append(tick=state.get("tick", 0), minute=state.get("minute", 0),
               room=state.get("room", "rest"), op=f"world:{kind}",
               text=text, provenance=PROVENANCE_WORLD,
               night_date=state.get("night_date"))
    return new_state


def assert_no_leakage(snapshot_dict: dict) -> None:
    """Test helper: fail if a snapshot leaks raw internal numbers.

    Walks the snapshot; floats are forbidden anywhere, and the `needs`
    mapping may contain only the coarse vocabulary words.
    """
    coarse_vocab = set(HUNGER_WORDS) | set(THIRST_WORDS) | set(FATIGUE_WORDS)

    def walk(node: Any, path: str) -> None:
        if isinstance(node, float):
            raise AssertionError(f"float leaked at {path}: {node!r}")
        if isinstance(node, dict):
            for k, v in node.items():
                if path == "needs" or path.endswith(".needs"):
                    if v not in coarse_vocab:
                        raise AssertionError(
                            f"non-coarse need value at needs.{k}: {v!r}")
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(snapshot_dict, "")
