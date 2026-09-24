"""CalibosSubject: the endogenous organism, personalized.

- Uses CalibosWorkspace (pinned identity roots, deduped views).
- Adds inject_thought(): my answers to queued cognition prompts, and my
  voluntary thoughts, enter through the same inner ear as live cognition —
  semantic interpretation, memory feedback, associative drift, echoes.
  A thought injected this way is a private cognitive occurrence, exactly
  like one produced during a heartbeat: no world authority, no conduct.
"""
from __future__ import annotations

from jelly_psiduck.endogenous import EndogenousSubject
from jelly_psiduck.runtime import concepts

from .workspace import CalibosWorkspace


class CalibosSubject(EndogenousSubject):
    def __init__(self, *args, salience_path=None, **kwargs):
        self._salience_path = salience_path
        super().__init__(*args, **kwargs)
        # Fresh stores get a stock workspace from __init__; existing stores are
        # rebuilt by _restore. Normalize both to CalibosWorkspace.
        if not isinstance(self.workspace, CalibosWorkspace):
            self.workspace = CalibosWorkspace.from_dict(self.workspace.to_dict())
        self._attach_salience()

    def _attach_salience(self):
        if self._salience_path is not None:
            from .salience import SalienceTracker
            self.workspace.salience_tracker = SalienceTracker(self._salience_path)

    def _restore(self, raw):
        super()._restore(raw)
        self.workspace = CalibosWorkspace.from_dict(raw["workspace"])
        self._attach_salience()
        self.trigger = {"kind": "none", "parents": [], "depth": 0}

    def inject_thought(self, text: str, trigger_kind: str = "answered") -> str:
        """Record a private thought through the inner ear, outside a heartbeat.

        Used for answering queued cognition prompts and for voluntary thinking.
        Returns the thought's record id.
        """
        text = text.strip()
        if not 0 < len(text) <= 600:
            raise ValueError("thought must be 1..600 characters")
        with self._transaction():
            state = self.engine.state
            if any(r.source == "thought" and r.first_person == text
                   and state.tick - r.tick < 6 for r in self.workspace.records):
                raise ValueError("that thought was already recorded recently")
            item = self._add(
                "thought", text,
                concepts=tuple(sorted(concepts(text))),
                generated_by="cognition",
                available_to_cognition=True,
            )
            self.trigger = {"kind": trigger_kind, "parents": [], "depth": 1}
            self._hear(item)
            return item.id
