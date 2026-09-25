"""Salience, lazy decay, and rehearsal — the retrieval substrate.

Lineage: ACT-R-style activation computed fresh at retrieval time, ported from
Azimn/persona_engine_PYTHONX core/memory.py (deterministic, zero models), with
importance signals drawn from our own engagement instead of simulated affect:

- answered a prompt   -> the thought mattered (+importance)
- let a prompt pass   -> its material surfaced without engagement (mild penalty,
                       the "- repetition" term of the egg-events attention formula)
- valence-tagged note -> |valence| marks importance
- voluntary thought   -> revealed preference (+importance)
- dream rehearsal     -> a memory resurfacing asleep counts as a recall

Design rules:
- Lazy: nothing is decayed eagerly and nothing is ever deleted. Scores are
  computed at view time from (created_tick, recall_ticks, importance,
  unengaged_surfacings). Low scores only sink records in the ranking.
- The tracker is a sidecar (salience.json, local-only, gitignored). Engine
  records are never mutated; the workspace view just ranks by this score.
- Cognition never sees the machinery: only the ranked (source, first_person)
  view leaves this module. No scores, ticks, or ids cross the boundary.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

DECAY = 0.5               # recency decay exponent (ACT-R default)
IMPORTANCE_WEIGHT = 0.6   # how much engaged importance lifts a record
UNENGAGED_PENALTY = 0.25  # per surfacing let pass without a thought
UNRESOLVED_BOOST = 1.0    # record linked to an open concern/expectation (Zeigarnik)

# Sentinel distinguishing a missing "tick" key from an explicit null.
_MISSING = object()

# Diagnostic reasons emitted when a dream fragment's tick fails validation.
# Each entry appended to SalienceTracker.diagnostics is
# {"reason": <one of these>, "raw": <the offending value or None>,
#  "log": <filename>, "line": <1-based line number>}.
TICK_REJECT_MISSING = "missing_tick"
TICK_REJECT_NULL = "null_tick"
TICK_REJECT_NON_INTEGER = "non_integer_tick"
TICK_REJECT_NEGATIVE = "negative_tick"
TICK_REJECT_FUTURE = "future_tick"


def _validate_fragment_tick(raw, now_tick: int | None):
    """Fail-closed validation of a dream fragment's temporal provenance.

    Returns ``(tick, None)`` when the tick is a legitimate engine tick, or
    ``(None, reason)`` when it must not feed rehearsal. Tick 0 is
    legitimate engine time, never an error sentinel — it rehearses
    normally. ``bool`` is rejected explicitly (it is an ``int`` subclass
    but never a real tick); floats, strings, and every other non-int type
    are rejected too. The future check runs only when ``now_tick`` is
    provided; when it is None the check is skipped (documented degraded
    validation) and every otherwise-valid tick passes.
    """
    if raw is _MISSING:
        return None, TICK_REJECT_MISSING
    if raw is None:
        return None, TICK_REJECT_NULL
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, TICK_REJECT_NON_INTEGER
    if raw < 0:
        return None, TICK_REJECT_NEGATIVE
    if now_tick is not None and raw > now_tick:
        return None, TICK_REJECT_FUTURE
    return raw, None


class SalienceTracker:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"records": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data = loaded
                    self.data.setdefault("records", {})
            except (ValueError, OSError):
                pass
        # Explicit per-call diagnostics from rehearse_from_dreams: a list of
        # dicts, refreshed on every call (not accumulated), in-memory only —
        # never persisted to the sidecar file. Each entry names the rejection
        # reason, the offending value, and the log/line the fragment came
        # from, so a malformed fragment is observable and testable rather
        # than a silent skip.
        self.diagnostics: list[dict] = []

    # -- events -----------------------------------------------------------

    def _entry(self, rid: str, created_tick: int) -> dict:
        recs = self.data["records"]
        e = recs.get(rid)
        if e is None:
            e = {"created": created_tick, "recalls": [], "importance": 0.0,
                 "unengaged": 0}
            recs[rid] = e
        return e

    def note_recall(self, rid: str, created_tick: int, tick: int) -> bool:
        """Record a recall of record ``rid`` at ``tick``.

        Returns True when the tick was newly added to the record's recall set,
        False when it was already present (no change).
        """
        e = self._entry(rid, created_tick)
        if tick not in e["recalls"]:
            e["recalls"].append(tick)
            return True
        return False

    def add_importance(self, rid: str, created_tick: int, delta: float) -> None:
        e = self._entry(rid, created_tick)
        e["importance"] = round(e["importance"] + delta, 3)

    def note_unengaged(self, rid: str, created_tick: int) -> None:
        e = self._entry(rid, created_tick)
        e["unengaged"] += 1

    def reset(self) -> None:
        """Clear the sidecar (used when the store is reseeded; ids restart)."""
        self.data = {"records": {}}
        self.save()

    def prune(self, live_ids: set[str]) -> None:
        self.data["records"] = {rid: e for rid, e in self.data["records"].items()
                                if rid in live_ids}

    # -- scoring ----------------------------------------------------------

    def activation(self, rid: str, created_tick: int, now_tick: int,
                   unresolved: bool = False) -> float:
        e = self._entry(rid, created_tick)
        times = [max(now_tick - created_tick, 1)]
        # Defense in depth: sidecars written before fail-closed temporal
        # validation (missing keys collapsed to 0, explicit nulls passed
        # through) may carry malformed recall ticks. They must never crash
        # view construction: non-integer entries are skipped here, adding
        # no information rather than raising TypeError on ``now_tick - t``.
        times += [max(now_tick - t, 1) for t in e["recalls"]
                  if isinstance(t, int) and not isinstance(t, bool)]
        base = math.log(sum(t ** -DECAY for t in times))
        return (base
                + IMPORTANCE_WEIGHT * e["importance"]
                - UNENGAGED_PENALTY * e["unengaged"]
                + (UNRESOLVED_BOOST if unresolved else 0.0))

    # -- rehearsal --------------------------------------------------------

    def rehearse_from_dreams(self, dream_dir: str | Path, records: list[dict],
                               now_tick: int | None = None) -> int:
        """Fold dream-fragment memory surfacings into recall counts.

        Provenance: a memory experience carrying ``record_id`` is matched by
        id only. An id that names no record in the current workspace is
        skipped — falling back to text there would credit the wrong record,
        which is exactly the hazard this fixes. Only id-less fragments
        (written before record_id existed) fall back to exact
        (source, first_person) text matching.

        Temporal provenance fails closed, like identity provenance: a
        fragment whose ``tick`` is missing, null, non-integer (including
        bool — an int subclass, rejected explicitly), negative, or
        future-relative-to-engine yields NO rehearsal mutation for any of
        its experiences and records an explicit diagnostic on
        ``self.diagnostics`` (refreshed per call, in-memory only). Tick 0
        is legitimate engine time, never an error sentinel — it rehearses
        normally. There is no reinterpretation as tick 0, ever.

        ``now_tick`` is the now-reference for the future check: fragment
        ticks above it are causally impossible (fragments are written with
        the frozen dream tick, so anything above the current engine tick
        cannot have happened) and are rejected. When ``now_tick`` is None
        the future check is skipped — documented degraded validation.
        The sole CLI caller (``cmd_dream``) passes
        ``subject.engine.state.tick``.

        Idempotent: reprocessing a log re-adds nothing (recalls are a set of
        ticks), and the returned count is idempotent too — it counts each
        distinct (record, tick) pair exactly once across reprocessings. Only
        memories currently in the workspace can be rehearsed.
        """
        by_id = {r["id"]: r for r in records}
        by_text = {(r["source"], r["first_person"]): r for r in records}
        self.diagnostics = []
        n = 0
        for path in sorted(Path(dream_dir).glob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    frag = json.loads(line)
                except ValueError:
                    continue
                raw_tick = frag.get("tick", _MISSING)
                tick, reason = _validate_fragment_tick(raw_tick, now_tick)
                if reason is not None:
                    # Fail closed: no rehearsal mutation for this fragment's
                    # experiences, and an explicit, testable diagnostic —
                    # never a silent skip, never a reinterpreted tick 0.
                    self.diagnostics.append({
                        "reason": reason,
                        "raw": None if raw_tick is _MISSING else raw_tick,
                        "log": path.name,
                        "line": lineno,
                    })
                    continue
                for e in frag.get("experiences", []):
                    if e.get("source") != "memory":
                        continue
                    rid = e.get("record_id")
                    if rid is None:
                        # Legacy fragment: text is all we have.
                        r = by_text.get((e.get("source"), e.get("first_person")))
                    else:
                        r = by_id.get(rid)
                        if r is None:
                            # The record has left the workspace (reseed,
                            # archive). Skipped: no text fallback, no credit.
                            continue
                    if r is not None:
                        if self.note_recall(r["id"], r["tick"], tick):
                            n += 1
        return n

    # -- persistence ------------------------------------------------------

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1),
                             encoding="utf-8")
