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
        times += [max(now_tick - t, 1) for t in e["recalls"]]
        base = math.log(sum(t ** -DECAY for t in times))
        return (base
                + IMPORTANCE_WEIGHT * e["importance"]
                - UNENGAGED_PENALTY * e["unengaged"]
                + (UNRESOLVED_BOOST if unresolved else 0.0))

    # -- rehearsal --------------------------------------------------------

    def rehearse_from_dreams(self, dream_dir: str | Path, records: list[dict]) -> int:
        """Fold dream-fragment memory surfacings into recall counts.

        Provenance: a memory experience carrying ``record_id`` is matched by
        id only. An id that names no record in the current workspace is
        skipped — falling back to text there would credit the wrong record,
        which is exactly the hazard this fixes. Only id-less fragments
        (written before record_id existed) fall back to exact
        (source, first_person) text matching.

        Idempotent: reprocessing a log re-adds nothing (recalls are a set of
        ticks), and the returned count is idempotent too — it counts each
        distinct (record, tick) pair exactly once across reprocessings. Only
        memories currently in the workspace can be rehearsed.
        """
        by_id = {r["id"]: r for r in records}
        by_text = {(r["source"], r["first_person"]): r for r in records}
        n = 0
        for path in sorted(Path(dream_dir).glob("*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    frag = json.loads(line)
                except ValueError:
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
                        if self.note_recall(r["id"], r["tick"], frag.get("tick", 0)):
                            n += 1
        return n

    # -- persistence ------------------------------------------------------

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1),
                             encoding="utf-8")
