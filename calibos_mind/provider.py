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


class StalePromptError(ValueError):
    """A queued prompt whose view has been superseded. Never answer it."""


def check_prompt_fresh(payload: dict, sequence: int) -> None:
    """Refuse a prompt queued from a view the store has moved past.

    Raises StalePromptError when records were added after the prompt was
    queued (the workspace sequence moved past its queue-time value), or
    when the prompt predates queue-time provenance and its view cannot be
    verified. Answering from a stale view would let drift accounting move
    on thoughts the thinker never actually saw.
    """
    queued = payload.get("view_sequence")
    if queued is None:
        raise StalePromptError("prompt predates queue-time provenance; "
                               "its view cannot be verified")
    if sequence != queued:
        raise StalePromptError(
            f"view superseded: queued at tick {payload.get('view_tick')} "
            f"(sequence {queued}), store is now at sequence {sequence}"
        )


class InboxCognition:
    def __init__(self, inbox_dir: str | Path, clock=None):
        self.inbox = Path(inbox_dir)
        self.inbox.mkdir(parents=True, exist_ok=True)
        # clock() -> (store tick, workspace sequence), sampled at queue time.
        # Wired by the CLI once the subject exists; absent in bare use.
        self._clock = clock

    def track_queue_time(self, clock):
        """clock() -> (store tick, workspace sequence); sampled per queue."""
        self._clock = clock

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
        view_tick = view_sequence = None
        if self._clock is not None:
            view_tick, view_sequence = self._clock()
        payload = {
            "id": pid,
            "prompt": prompt,
            "view_tick": view_tick,
            "view_sequence": view_sequence,
            "experiences": [
                {"source": e.source, "first_person": e.first_person}
                for e in view.experiences
            ],
        }
        (self.inbox / f"{pid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return None

    def queue_external(self, prompt_text, source="invitation", first_person=None):
        """Queue an externally-authored prompt (invitation, relay message...).

        Unlike think(), the prompt text is authored outside a cognition
        view, so there is no cognitive_prompt() rendering. Queue-time
        provenance is stamped identically from the wired clock, so
        `mind answer` can verify the store has not moved past queue time.
        Without a wired clock the payload carries no provenance and
        answering it is refused (fail closed), same as a legacy prompt.
        Returns the prompt id.
        """
        pid = self._next_id()
        view_tick = view_sequence = None
        if self._clock is not None:
            view_tick, view_sequence = self._clock()
        payload = {
            "id": pid,
            "prompt": prompt_text,
            "view_tick": view_tick,
            "view_sequence": view_sequence,
            "experiences": [
                {"source": source,
                 "first_person": first_person if first_person is not None
                 else prompt_text}
            ],
        }
        (self.inbox / f"{pid}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return pid

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


class DreamCognition:
    """Cognition provider for dream ticks.

    When the sleeping engine wants a thought, there is nobody home to think
    it. Instead the cognitive view — the immutable subjective experiences the
    engine's own association machinery surfaced together under this trigger —
    is recorded as a dream fragment, and the provider returns None (silence).

    Nothing is queued to the inbox, no thought is injected, and no conduct
    can follow from a dream: dreams propose, the waker disposes. Zero LLM
    calls; the dreaming is done entirely by the engine's native triggers,
    echoes, and memory resurfacing running with no outside world.
    """

    def __init__(self, dream_dir: str | Path):
        self.dream_dir = Path(dream_dir)
        self.dream_dir.mkdir(parents=True, exist_ok=True)
        self.fragments: list[list[dict]] = []

    def think(self, view):
        self.fragments.append(
            [{"source": e.source, "first_person": e.first_person}
             for e in view.experiences]
        )
        return None
