"""Host-layer feeder: slow tick-count rhythm of food/drink events.

Findings (2026-10-08 probe, 12 heartbeat ticks on the live store): hunger
(+0.0020/tick) and thirst (+0.0025/tick) rise forever — no [[activities]]
entry reduces them, and nothing in calibos_mind/ ever emits the frozen
engine's food/drink events, whose EVENT_RULES would reduce hunger by 0.35
and thirst by 0.40. Conduct selection had degenerated to a seek_contact
constant (NEED_ACTIONS maps hunger, thirst AND loneliness to SEEK_CONTACT
first), and the forming habits were hardening it past the 0.65 fast-fire
threshold — the loop chasing a constant.

The feeder emits the engine's OWN events — it invents no satiation math —
on a slow deterministic tick-count rhythm, need-gated: at rhythm ticks,
food is emitted only if hunger is at/above FEED_THRESHOLD (drink likewise
for thirst). The need gate is load-bearing, not decoration: a fixed cadence
alone would ratchet needs to 0 (drop exceeds rise per cycle) or let them
pin at 1.0 — both opposite degeneracies. Gated, hunger oscillates in
roughly [0.25, 0.70] and thirst in [0.20, 0.72]: bounded, never pinned.

The events are fully expected (expected_valence == actual_valence == 0.0,
surprise 0) at intensity 1.0, so the engine applies exactly its EVENT_RULES
deltas — a scheduled meal is routine, not a surprise. Source "world": the
feeding is an environmental occurrence, not a social communication.

Zero new state, zero new sidecars, fully deterministic: the schedule is a
pure function of (tick, needs). Waking ticks only — maybe_feed is called
from cli._run_tick before heartbeat(); dream_tick() and read-only commands
never call it (plus a defensive _dreaming guard). The frozen engine and
the cartridge are untouched (no fingerprint migration).
"""
from __future__ import annotations

from digital_subject.models import Event

FOOD_CADENCE = 48    # consider food every N waking ticks
DRINK_CADENCE = 48   # consider drink every N waking ticks
DRINK_OFFSET = 24    # drink rhythm offset so both rarely coincide
FEED_THRESHOLD = 0.60  # only feed when the need is at/above this

_DESCRIPTIONS = {
    "food": "A meal was provided.",
    "drink": "Water was provided.",
}


def due_kinds(tick: int, needs: dict) -> list:
    """Pure schedule: which feeder events are due at this waking tick.

    Deterministic function of (tick, needs) — no state, no I/O. tick 0 is
    the origin, not a rhythm tick. Missing need keys default to 0.0 (never
    feed on unknown state).
    """
    kinds = []
    if tick > 0 and tick % FOOD_CADENCE == 0 \
            and needs.get("hunger", 0.0) >= FEED_THRESHOLD:
        kinds.append("food")
    if tick > 0 and (tick - DRINK_OFFSET) % DRINK_CADENCE == 0 \
            and needs.get("thirst", 0.0) >= FEED_THRESHOLD:
        kinds.append("drink")
    return kinds


def event_for(kind: str) -> Event:
    """Build the engine event for one feeding.

    intensity 1.0 with expected == actual valence 0.0 gives surprise 0, so
    the engine's _apply_event scales the EVENT_RULES deltas by exactly 1.0:
    food -> hunger -0.35, drink -> thirst -0.40, the engine's own math.
    """
    if kind not in _DESCRIPTIONS:
        raise ValueError(f"unknown feeder kind: {kind!r}")
    return Event(kind=kind, source="world", description=_DESCRIPTIONS[kind],
                 tags=(kind,), intensity=1.0, valence=0.0,
                 expected_valence=0.0, actual_valence=0.0)


def maybe_feed(subject) -> list:
    """Enqueue due food/drink events; return the kinds emitted.

    Waking-tick path only — call from cli._run_tick before heartbeat() so
    the tick consumes the events as external input. Never call from dream
    ticks or read-only paths (defensive _dreaming guard; the call site is
    the real guarantee).
    """
    if getattr(subject, "_dreaming", False):
        return []
    state = subject.engine.state
    kinds = due_kinds(state.tick, state.needs)
    for kind in kinds:
        subject.enqueue(event_for(kind))
    return kinds
