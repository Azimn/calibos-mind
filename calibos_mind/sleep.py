"""Sleep isolation: what a dream may and may not change.

A dream tick runs the engine's associative machinery — body and temporal
projections, cognition admission, echo/association resurfacing, dream
fragments — with the body, the clock, and conduct frozen. Skipped while
asleep: advance_body (needs/pressures), deadline advance, event ingress,
select_conduct, finish_silent_activity, and heartbeat/activity traces.

The snapshot/assertion below run around every dream tick (see
CalibosSubject.dream_tick). Anything outside the dream's remit that moves
— body needs/pressures, conduct state and its trace history, pending
events, or the store tick — fails the tick loudly instead of silently
drifting the waking state.

One subtlety: the engine caps the *full* trace at 256 entries
(runtime._trace truncates the front on every append). A dream legitimately
appends in-remit traces (cognition_trigger, inner_ear, cognition/rejected
errors) — never activity/heartbeat — so on a full trace each such append
mechanically evicts the oldest entry, which may be a heartbeat. That
eviction is not a remit violation, so the action-trace compare is
eviction-aware: after must be a suffix of before (see
_conduct_trace_legally_evolved), not list-identical.
"""
from __future__ import annotations

# Trace kinds that belong to conduct, not cognition. A dream may append
# cognition_trigger (and error) traces; it must never append these.
ACTION_TRACE_KINDS = ("activity", "heartbeat")


def isolation_snapshot(payload: dict) -> dict:
    """The dream-frozen surface of a store payload, for before/after compare."""
    eng = payload["engine"]
    return {
        "tick": eng["tick"],
        "needs": dict(eng["needs"]),
        "pressures": dict(eng.get("pressures", {})),
        "current_activity": eng.get("current_activity"),
        "last_intention": eng.get("last_intention"),
        "action_trace": [t for t in payload.get("trace", [])
                         if t.get("kind") in ACTION_TRACE_KINDS],
        "pending": payload.get("pending", []),
    }


def _conduct_trace_legally_evolved(before_trace: list, after_trace: list) -> bool:
    """Eviction-aware compare for the conduct-kind (activity/heartbeat) trace.

    The dream never appends activity/heartbeat traces, so the only legal
    evolution of this list across a dream tick is shrinkage from the front:
    the dream's own in-remit trace appends push the full 256-entry trace
    over the cap and the oldest entries — possibly heartbeats — are
    mechanically evicted. ``after`` must therefore be a suffix of
    ``before``: same length or shorter, retained entries identical and in
    order. An appended conduct entry, a mutated entry, or a reordered entry
    is a real violation and fails.
    """
    if len(after_trace) > len(before_trace):
        return False
    return before_trace[len(before_trace) - len(after_trace):] == after_trace


def assert_isolation(before: dict, after: dict) -> None:
    """Fail loudly if a dream tick moved anything outside its remit.

    Everything except the conduct-kind action trace must be identical
    across the tick. The action trace is compared eviction-aware (see
    _conduct_trace_legally_evolved): on a store whose trace has reached the
    engine's 256-entry cap, the dream's own allowed trace appends evict
    the oldest heartbeat entries without anything moving outside the
    dream's remit.
    """
    moved = [k for k in before
             if k != "action_trace" and before[k] != after.get(k)]
    if ("action_trace" not in after
            or not _conduct_trace_legally_evolved(before["action_trace"],
                                                  after["action_trace"])):
        moved.append("action_trace")
    if moved:
        raise AssertionError(
            "dream isolation violated — changed across a sleep tick: "
            + ", ".join(moved)
        )
