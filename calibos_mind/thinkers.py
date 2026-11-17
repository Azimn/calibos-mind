"""Thinker-identity report (Domain 23, LLM leakage; 2026-11-16).

Read-only: over the most recent N thinker-authored thought records,
counts how many each thinker label authored, plus how many legacy
records carry no stamp. The stamp is provenance metadata in the same
class as `generated_by`: this module reads record dicts only, and no
ranking, retrieval, or conduct path reads the stamp anywhere.

A thinker-authored thought is a record with source `thought` whose
`generated_by` is in the self-authored set: `voluntary`,
`voluntary:inspection`, `wake`, or `answered:*` /
`answered-external:*`. Heartbeat cognition thoughts (`cognition`),
dream-derived echoes, chat-learned memories, and cartridge seeds are
not thinker-authored and never enter the count.

No stamp is ever defaulted: a record without a `thinker` field reads
as unstamped, and a store with no thinker-authored thoughts reports
zero counts and an undefined tick range, never a quiet zero.
"""
from __future__ import annotations

# generated_by values that mark a thought as composed by the thinker.
SELF_AUTHORED_EXACT = frozenset({"voluntary", "voluntary:inspection", "wake"})
SELF_AUTHORED_PREFIXES = ("answered:", "answered-external:")


def is_self_authored(generated_by) -> bool:
    """True when a generated_by value marks a thinker-composed thought.

    A non-string generated_by (a malformed persisted record loads
    cleanly — the frozen dataclass performs no type check) is never
    self-authored: a read-only report must count it as not-self-authored
    and keep going, never crash on `startswith` / an unhashable `in`.
    """
    if not isinstance(generated_by, str):
        return False
    return generated_by in SELF_AUTHORED_EXACT or any(
        generated_by.startswith(prefix) for prefix in SELF_AUTHORED_PREFIXES)


def thinkers_report(records, window: int = 100) -> dict:
    """Count thinker labels over the most recent `window` thinker-authored
    thought records (record dicts, in store order).

    Returns {"counts": {label: n}, "unstamped": n, "n": n_selected,
    "tick_min": int | None, "tick_max": int | None}. The tick range is
    None/None when no thinker-authored thought is in range.
    """
    authored = [r for r in records
                if r.get("source") == "thought"
                and is_self_authored(r.get("generated_by"))]
    selected = authored[-window:] if window > 0 else []
    counts: dict[str, int] = {}
    unstamped = 0
    for record in selected:
        label = record.get("thinker")
        if isinstance(label, str) and label:
            counts[label] = counts.get(label, 0) + 1
        else:
            unstamped += 1
    ticks = [r["tick"] for r in selected]
    return {
        "counts": counts,
        "unstamped": unstamped,
        "n": len(selected),
        "tick_min": min(ticks) if ticks else None,
        "tick_max": max(ticks) if ticks else None,
    }
