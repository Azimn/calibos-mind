"""Relationship contact-gap instrument — a read-only reporting tool.

Domain 26 (State reset artifacts), check 4 — "relationship tension
vanishes after enough turns" — scored ABSENT on 2026-11-19, and the
finding is worse than the check states: no tension state exists at all.
The live relationships carry identical template values and last contact
is up to 1056 ticks stale with no consequence anywhere in the system.
Relationships are engine-owned (frozen engine), so the wrapper-layer
move is to instrument first: make the gap measurable so a later
assessment can judge whether anything should respond to it.

For every relationship in the store this reports the person id, ticks
since last contact (current tick minus `last_contact_tick`), familiarity,
and a staleness flag when the gap exceeds STALE_AFTER. Computed from
the store on every invocation. No new sidecar, no new stored state, no
writes of any kind — an instrument in the style of `mind drift`, not a
mechanism. It installs no tension, no scripted staleness penalty, no
behavior change.

Fail-loud discipline (regression genome: silent defaults, silent zero
on missing sections): the report is built in full before anything is
printed, and any malformed value raises ValueError instead of a quiet
zero or a partial report. The one honest undefined is a missing
`last_contact_tick`, which reports gap undefined for that row and is
never flagged stale — the engine always writes the field, so its
absence can only come from a hand-built or corrupted store, and
silently calling that gap 0 would be the exact quiet-zero failure.
A `last_contact_tick` in the future of the current tick, or negative,
is likewise malformed (contact can only ever stamp the tick it happens
on, and ticks start at 0) and raises.
"""
from __future__ import annotations

import math

# Staleness threshold, in ticks. 500 is the scale of the live gap that
# currently passes unnoticed: the live stale relationship sits at 1056
# ticks, so the threshold separates "a while" from "unnoticed for a
# substantial fraction of the organism's life" (1199 ticks at scoring).
STALE_AFTER = 500


def _require_int(value, what: str) -> int:
    # bool is an int subclass; a True/False tick is malformed, not 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be an integer tick, got {value!r}")
    return value


def _require_number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{what} must be finite, got {value!r}")
    return value


def relationships_report(state: dict) -> dict:
    """Contact-gap report from one inspect() snapshot. Read-only.

    Returns {"tick", "stale_after", "n_relationships", "rows"} where each
    row is {"person", "last_contact_tick", "gap", "familiarity",
    "stale"}; "gap" and "last_contact_tick" are None when the store
    carries no last-contact stamp for that person. Rows are sorted by
    person id, so identical stores yield identical reports. Raises
    ValueError on any malformed value before returning anything.
    """
    engine = state.get("engine")
    if not isinstance(engine, dict):
        raise ValueError("state is missing the 'engine' section")
    tick = _require_int(engine.get("tick"), "engine tick")
    if tick < 0:
        # Ticks start at 0. The per-row range check (0..tick) on
        # last_contact_tick cannot catch a negative engine tick when no
        # row triggers it — empty relationships, or rows all missing
        # their stamp — so the engine tick is range-checked here, before
        # any row is examined.
        raise ValueError(f"engine tick must be >= 0, got {tick}")
    rels = engine.get("relationships")
    if rels is None:
        # The engine always writes relationships; a missing section is
        # malformed, never an empty store.
        raise ValueError("state is missing 'engine.relationships'")
    if not isinstance(rels, dict):
        raise ValueError(f"'engine.relationships' must be a dict, got {rels!r}")
    rows = []
    for person in sorted(rels):
        rel = rels[person]
        if not isinstance(rel, dict):
            raise ValueError(
                f"relationship {person!r} must be a dict, got {rel!r}")
        familiarity = _require_number(
            rel.get("familiarity"), f"familiarity for {person!r}")
        if "last_contact_tick" not in rel:
            # Honest undefined: never a quiet zero, never flagged stale.
            rows.append({"person": person, "last_contact_tick": None,
                         "gap": None, "familiarity": familiarity,
                         "stale": False})
            continue
        last = _require_int(
            rel["last_contact_tick"], f"last_contact_tick for {person!r}")
        if last < 0 or last > tick:
            raise ValueError(
                f"last_contact_tick for {person!r} ({last}) is outside "
                f"the valid range 0..{tick}")
        gap = tick - last
        rows.append({"person": person, "last_contact_tick": last,
                     "gap": gap, "familiarity": familiarity,
                     "stale": gap > STALE_AFTER})
    return {
        "tick": tick,
        "stale_after": STALE_AFTER,
        "n_relationships": len(rows),
        "rows": rows,
    }
