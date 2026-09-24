"""InboxCognition: asynchronous cognition for indefinite use.

When the engine wants a thought and I am not sitting in a live session, the
prompt is queued to the inbox and the provider returns None (a deliberate
deferral, recorded as silence for that tick). I answer later with
`mind answer`, which injects the thought through the inner ear.

This keeps every architectural guarantee: the provider only ever sees the
immutable subjective view, thoughts stay private with no world authority,
and the engine remains the sole conduct authority.
"""
from __future__ import annotations

import json
from pathlib import Path

from jelly_psiduck.cognition import cognitive_prompt


class InboxCognition:
    def __init__(self, inbox_dir: str | Path):
        self.inbox = Path(inbox_dir)
        self.inbox.mkdir(parents=True, exist_ok=True)

    def _next_id(self) -> str:
        # Monotonic sequence persisted in the inbox dir, so IDs are never
        # reused even after a prompt is consumed and its file deleted.
        seq_path = self.inbox / ".seq"
        try:
            n = int(seq_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            ids = [int(p.stem.split("-")[1]) for p in self.inbox.glob("prompt-*.json")]
            n = max(ids, default=0)
        n += 1
        seq_path.write_text(str(n), encoding="utf-8")
        return f"prompt-{n:04d}"

    def think(self, view):
        pid = self._next_id()
        prompt = cognitive_prompt(view)
        payload = {
            "id": pid,
            "prompt": prompt,
            "experiences": [
                {"source": e.source, "first_person": e.first_person}
                for e in view.experiences
            ],
        }
        (self.inbox / f"{pid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return None

    def pending(self) -> list[dict]:
        out = []
        for path in sorted(self.inbox.glob("prompt-*.json")):
            out.append(json.loads(path.read_text(encoding="utf-8")))
        return out

    def consume(self, pid: str) -> dict:
        path = self.inbox / f"{pid}.json"
        if not path.exists():
            raise ValueError(f"no such pending prompt: {pid}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
        return payload
