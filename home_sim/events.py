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

from .rhythm import FISH_DEATH_MARKER
from .weather import weather_adjective

PROVENANCE_SIMULATED = "simulated"
PROVENANCE_WORLD = "world"

# The need-status sentences rhythm._need_signals appends to action texts.
# The digest dedups on the base text (below), so "I read for a while. I
# am getting tired." and "I read for a while. I feel worn out..." count
# as one moment, not two — otherwise the narrative is a tally wearing
# narrative clothes. Mirrors rhythm._need_signals; keep them in step.
NEED_SIGNAL_SENTENCES = (
    " I feel worn out and find it hard to concentrate.",
    " I am getting tired.",
    " I am painfully hungry.",
    " I could do with something to eat.",
    " My mouth is dry.",
)


def _base_text(text: str) -> str:
    """An event's text with trailing need-status sentences stripped, so
    repeats of the same moment collapse however the body felt at the
    time."""
    stripped = text
    changed = True
    while changed:
        changed = False
        for sentence in NEED_SIGNAL_SENTENCES:
            if stripped.endswith(sentence):
                stripped = stripped[:-len(sentence)]
                changed = True
    return stripped


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


def summarize_night(records: list[dict], weather: dict | None = None
                    ) -> str:
    """Morning "while you were out" digest, as a short first-person
    narrative of the night — not a tally. Marked simulated; the wake agent
    may note reactions but must never import these as memories.

    Up to eight distinctive moments, in the order they happened, exact
    repeats dropped, then a closing line giving the night's date and the
    count of moments. World events are quoted separately. When weather
    is given, the header names the night's condition — the sky the
    resident actually lived under.
    """
    sim = [r for r in records if r.get("provenance") == PROVENANCE_SIMULATED]
    world = [r for r in records if r.get("provenance") == PROVENANCE_WORLD]
    if not records:
        return "(no off-hours activity recorded — simulated)"
    night = records[0].get("night_date") or "that night"
    preferred = ("watch_fish", "name_fish", "feed_fish", "sip",
                 "brew_tea", "take_mug", "read", "write", "eat", "harvest",
                 "sleep", "wear_outfit", "wear_item", "quiet", "sit",
                 "tend_garden", "store_produce", "take_book", "bookmark",
                 "listen", "touch", "smell", "taste", "write_note",
                 "write_diary", "read_diary")
    moments: list[str] = []
    seen: set[str] = set()
    for r in sim:
        base = _base_text(r["text"])
        if r.get("op") in preferred and base not in seen:
            moments.append(base)
            seen.add(base)
        if len(moments) >= 8:
            break
    if len(moments) < 3:
        for r in sim:
            base = _base_text(r["text"])
            if base not in seen:
                moments.append(base)
                seen.add(base)
            if len(moments) >= 8:
                break
    # A dead fish is the one moment the morning must not miss: if any
    # event of the night carries the death sentence, it leads the digest
    # even when its action is not one of the preferred kinds.
    for r in sim:
        if FISH_DEATH_MARKER in r["text"]:
            death = _base_text(r["text"])
            if death not in seen:
                moments.insert(0, death)
                seen.add(death)
            break
    moments = moments[:8]
    header = f"While you were out (simulated) — the night of {night}"
    if weather and weather.get("condition"):
        header += f", a {weather_adjective(weather['condition'])} night"
        temp = weather.get("temp_f")
        if temp is not None:
            header += f", {temp:g}°F"
    header += ":"
    lines = [header]
    if moments:
        lines.append(" ".join(moments))
    else:
        lines.append("I puttered about the house, and nothing in particular "
                     "asked for my attention.")
    for r in world:
        lines.append(f"(from the world: {r.get('text', '')})")
    lines.append(f"— {len(sim)} moments in all, all simulated —")
    return "\n".join(lines)
