"""Access report — the Article 32 (Opacity and Cognitive Privacy) state,
in one read-only command.

The Article 32 verdict turns on access facts — what the thinker can
actually see, what is genuinely unavailable, where the view boundary
sits — but those facts were scattered across the store, the workspace
code, and the engine config, and establishing them took four ad-hoc
probes. This module measures them all from one loaded store and reports
them with every number labelled by what it counts.

It is a reporter, not a claim: it never presents a number it did not
measure, and it never writes anything. It owns no sidecar and no state
of its own. A quantity that has no defined value (e.g. a view share
over zero records) reports "undefined", never a quiet zero; a store
missing its workspace/records section is malformed and raises, never
a quiet zero either.

View composition mirrors ``calibos_mind.workspace.CalibosWorkspace.view``:
pinned roots are the cartridge-authored eligible records, capped at
``MAX_PINNED``; the ranked room is ``VIEW_LIMIT - pinned`` (floored at
zero the same way ``view()`` floors it). Cartridge records beyond the
pin cap are not pinned, but they stay eligible for the ranked room —
``view()`` excludes only the capped pinned ids from its ranked pool —
so the admitted count is measured, never assumed to equal the room:
dedupe and per-class caps can also leave the room unfilled. The view
*size* — how many records the thinker is actually shown — is taken
from the live view when the caller passes it (the CLI does); otherwise
it is measured by rebuilding the view from the snapshot, which is
exact for the live ranking too except for pathological chained
near-duplicate clusters, where admission order can change the dedupe
survivor count. The CLI path is always exact.
"""
from __future__ import annotations

from pathlib import Path

from .consolidate import excluded_ids
from .workspace import MAX_PINNED, VIEW_LIMIT, CalibosWorkspace

# Pinned roots are the cartridge-authored records (identity root, seed
# memories) — the same stamp drift.py reads as "authored".
AUTHORED_GENERATOR = "cartridge"


def _measure_view_size(workspace_section: dict,
                       availability_path) -> int:
    """View size from the snapshot alone: rebuild and count.

    No tracker is attached, so ranking falls back to recency; the
    admitted *count* does not depend on rank order (dedupe keeps one
    survivor per duplicate cluster, caps admit a fixed total), so this
    measures the same size the live view reports.
    """
    ws = CalibosWorkspace.from_dict(workspace_section)
    ws.availability_path = availability_path
    return len(ws.view().experiences)


def access_report(state: dict, availability_path=None,
                  view_size: int | None = None) -> dict:
    """Access facts for one loaded store. Read-only (no save, no sidecar).

    ``state`` is an ``inspect()`` snapshot. ``availability_path`` is the
    archive availability journal (``archive/availability.json``); when
    it is absent the archived-excluded count is 0 and the report says
    so — never a quiet zero. ``view_size`` is the live view's size
    when the caller has it; otherwise it is measured from the snapshot.
    """
    try:
        records = state["workspace"]["records"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "malformed store: missing workspace/records section — "
            "the engine always writes workspace.records, so a missing "
            "section is corruption, not an empty store") from exc
    if not isinstance(records, list):
        raise ValueError(
            "malformed store: workspace.records is not a list")

    total = len(records)

    # Availability journal: present or absent is itself a fact.
    journal_present = (availability_path is not None
                       and Path(availability_path).exists())
    excluded = (excluded_ids(availability_path)
                if journal_present else frozenset())
    flag_false = {r["id"] for r in records
                  if r.get("available_to_cognition") is False}
    archived = {r["id"] for r in records if r["id"] in excluded}
    unavailable = flag_false | archived
    available = total - len(unavailable)

    pinned_eligible = [r for r in records
                       if r["id"] not in unavailable
                       and r.get("generated_by") == AUTHORED_GENERATOR]
    pinned = min(len(pinned_eligible), MAX_PINNED)
    room = max(VIEW_LIMIT - pinned, 0)

    if view_size is None:
        view_size = _measure_view_size(state["workspace"],
                                       availability_path)
    view_share = (view_size / total) if total > 0 else None
    # Cartridge records beyond the pin cap are not pinned, but they
    # stay eligible for the ranked room — exactly as view() treats
    # them (it excludes only the capped pinned ids from `rest`). So
    # the admitted count is measured, never assumed to equal the room:
    # dedupe and per-class caps can also leave the room unfilled.
    ranked_admitted = view_size - min(pinned, VIEW_LIMIT)

    config = state.get("config") if isinstance(state, dict) else None
    inner_ear = (config.get("inner_ear")
                 if isinstance(config, dict) else None)
    engine = state.get("engine") if isinstance(state, dict) else None
    tick = engine.get("tick") if isinstance(engine, dict) else None

    return {
        "tick": tick,
        "view_limit": VIEW_LIMIT,
        "max_pinned": MAX_PINNED,
        "total": total,
        "available": available,
        "unavailable": len(unavailable),
        "unavailable_flag_false": len(flag_false),
        "unavailable_archived": len(archived),
        "availability_file_present": journal_present,
        "availability_path": (str(availability_path)
                              if availability_path is not None else None),
        "pinned": pinned,
        "pinned_eligible": len(pinned_eligible),
        "ranked_room": room,
        "ranked_admitted": ranked_admitted,
        "view_size": view_size,
        "view_share": view_share,
        "inner_ear": inner_ear,
    }


def format_access_report(rep: dict) -> str:
    """Deterministic rendering: every number labelled by what it counts."""
    tick = rep["tick"] if rep["tick"] is not None else "?"
    if rep["inner_ear"] is True:
        ear = "on (authored thoughts are available to cognition)"
    elif rep["inner_ear"] is False:
        ear = "off (authored thoughts are NOT available to cognition)"
    else:
        ear = "undefined (no inner_ear setting in the store config)"
    if rep["availability_file_present"]:
        journal = f"present ({rep['availability_path']})"
    else:
        journal = "absent — no availability file"
    if rep["view_share"] is None:
        share = "share undefined — no records"
    else:
        share = f"{rep['view_share'] * 100:.1f}% of the store"
    lines = [
        f"access (tick {tick}, {rep['total']} records) — read-only",
        f"  view limit {rep['view_limit']}, pinned cap {rep['max_pinned']}",
        f"  inner ear: {ear}",
        f"  records: {rep['total']} total, {rep['available']} available "
        f"to cognition, {rep['unavailable']} unavailable",
        f"    unavailable by cause: {rep['unavailable_flag_false']} "
        f"flagged unavailable (available_to_cognition False), "
        f"{rep['unavailable_archived']} archived-excluded",
        f"    availability file: {journal}",
        f"  pinned (cartridge-authored, eligible): {rep['pinned']} shown "
        f"(of {rep['pinned_eligible']} eligible)",
        f"  view composition: {rep['pinned']} pinned + "
        f"{rep['ranked_room']} ranked room "
        f"({rep['ranked_admitted']} admitted)",
        f"  view shows {rep['view_size']} of {rep['total']} records "
        f"({share})",
    ]
    return "\n".join(lines)
