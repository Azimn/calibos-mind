"""Append-only JSONL event log for the home simulation.

Every line is one event with a provenance label written at write time:
- "simulated": the sim's own off-hours life. Events, never thoughts.
- "world": imported through the traversal interface (traversal.py) —
  a visitor arrived, a gift was left, the village bell rang.

The log is the sim's memory of the night and the source of the morning
"while you were out" digest. It never touches the thought database, the
prompt inbox, dream fragments, salience sidecars, or ablation records —
separate state dir, separate file, by construction (this module has no
path to any of them).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROVENANCE_SIMULATED = "simulated"
PROVENANCE_WORLD = "world"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EventLog:
    """Append-only JSONL log living under a state directory."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "events.jsonl"

    def append(self, *, tick: int, minute: int, room: str, op: str,
               text: str, provenance: str, night_date: str | None = None,
               considered: list[str] | None = None,
               event_id: str | None = None) -> dict:
        """Append one event; returns the record written."""
        if provenance not in (PROVENANCE_SIMULATED, PROVENANCE_WORLD):
            raise ValueError(f"unknown provenance: {provenance!r}")
        record = {
            "event_id": event_id or uuid.uuid4().hex,
            "night_date": night_date,
            "tick": tick,
            "minute": minute,
            "clock": f"{(minute % 1440) // 60:02d}:{(minute % 1440) % 60:02d}",
            "room": room,
            "op": op,
            "text": text,
            "provenance": provenance,
            "utc": _utc_now(),
        }
        if considered:
            record["considered"] = list(considered)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def read_all(self) -> list[dict]:
        """Read every event in order. Tolerates a missing log (no night yet)."""
        if not self.path.exists():
            return []
        records = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def read_night(self, night_date: str) -> list[dict]:
        """Events for one night only — the morning digest's source. The spec
        requires 'the night's digest', not a whole-history summary."""
        return [r for r in self.read_all()
                if r.get("night_date") == night_date]


def summarize_night(records: list[dict]) -> str:
    """Morning "while you were out" digest. Marked simulated; the wake agent
    may note reactions but must never import these as memories."""
    sim = [r for r in records if r.get("provenance") == PROVENANCE_SIMULATED]
    world = [r for r in records if r.get("provenance") == PROVENANCE_WORLD]
    if not records:
        return "(no off-hours activity recorded — simulated)"
    lines = ["While you were out (simulated):"]
    counts: dict[str, int] = {}
    for r in sim:
        counts[r.get("op", "?")] = counts.get(r.get("op", "?"), 0) + 1
    done = ", ".join(f"{op} x{n}" for op, n in sorted(counts.items()))
    lines.append(f"  did: {done}" if done else "  did: nothing recorded")
    # A few first-person moments, for texture — the surprise the spec wants.
    moments = [r["text"] for r in sim if r.get("op") in
               ("eat", "harvest", "store_produce", "write", "sleep")][:3]
    for m in moments:
        lines.append(f"  — {m}")
    for r in world:
        lines.append(f"  (from the world: {r.get('text', '')})")
    return "\n".join(lines)
