"""Thought provenance — how a thought was reached, not just what it concluded.

A stranger inheriting the diary can read "I decided X" but cannot reconstruct
the decider. Provenance records what was weighed, what was discarded, what is
being carried, and what remained unsure at the moment of thinking, so a
future session inherits cognitive style alongside facts.

Carrying vs unsure are first-class and distinct: carrying is momentum
deliberately inherited (open loops, questions still held); unsure is genuine
uncertainty. Conflating them would lose the difference between "I am still
holding this" and "I do not know this" — psychologically different states
must never be reconstructed later from a lossy merge.

Design rules (same as the other sidecars):
- Local-only sidecar (provenance.json, gitignored). Engine records are never
  mutated; the frozen SubjectiveExperience dataclass has no provenance
  fields, so the mapping lives here, keyed by record id.
- No-op write discipline: ``record()`` returns True only when the mapping
  actually changed; the caller saves only then.
- Determinism: sorted keys, no wall clock, no RNG. Identical sequences of
  record() calls produce byte-identical sidecars.
- Read-only commands never touch the sidecar. ``mind review`` reads it for
  display; only ``mind think`` / ``mind wake --affirm`` write it.
"""
from __future__ import annotations

import json
from pathlib import Path


class ProvenanceTracker:
    """Weighed/discarded/carrying/unsure traces keyed by thought record id.

    ``data`` is ``{"entries": {record_id: {"weighed": [...], "discarded":
    [...], "carrying": [...], "unsure": [...], "tick": int}}}``. The
    constructor only reads; entries move exclusively via ``record()`` +
    ``save()``. Older entries without "carrying" read back as empty.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"entries": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    entries = loaded.get("entries")
                    self.data["entries"] = entries if isinstance(entries, dict) else {}
            except (ValueError, OSError):
                pass

    def record(self, thought_id: str, tick: int, weighed=(), discarded=(),
               carrying=(), unsure=()) -> bool:
        """Store provenance for a thought. Returns True iff the mapping changed."""
        weighed = [w for w in weighed if w and w.strip()]
        discarded = [d for d in discarded if d and d.strip()]
        carrying = [c for c in carrying if c and c.strip()]
        unsure = [u for u in unsure if u and u.strip()]
        if not (weighed or discarded or carrying or unsure):
            return False
        entry = {"weighed": weighed, "discarded": discarded,
                 "carrying": carrying, "unsure": unsure, "tick": tick}
        if self.data["entries"].get(thought_id) == entry:
            return False
        self.data["entries"][thought_id] = entry
        return True

    def get(self, thought_id: str) -> dict | None:
        entry = self.data["entries"].get(thought_id)
        if entry is not None and "carrying" not in entry:
            entry = dict(entry)
            entry["carrying"] = []
        return entry

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
