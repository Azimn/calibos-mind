"""CalibosSubject: the endogenous organism, personalized.

- Uses CalibosWorkspace (pinned identity roots, deduped views).
- Adds inject_thought(): my answers to queued cognition prompts, and my
  voluntary thoughts, enter through the same inner ear as live cognition —
  semantic interpretation, memory feedback, associative drift, echoes.
  A thought injected this way is a private cognitive occurrence, exactly
  like one produced during a heartbeat: no world authority, no conduct.
"""
from __future__ import annotations

import dataclasses

from jelly_psiduck.endogenous import EndogenousSubject
from jelly_psiduck.runtime import concepts
from jelly_psiduck.workspace import Thought

from .workspace import CalibosWorkspace
from . import friction as _friction

THOUGHT_MAX_CHARS = 600


def validate_thought_text(text: str) -> str:
    """Strip and validate a thought's text; return the stripped text.

    Raises ValueError naming the actual length. Shared by inject_thought
    and by cmd_answer, which validates *before* consuming the prompt so a
    malformed thought never eats the prompt it was meant to answer.
    """
    text = text.strip()
    if not 0 < len(text) <= THOUGHT_MAX_CHARS:
        raise ValueError(
            f"thought must be 1..{THOUGHT_MAX_CHARS} characters (got {len(text)})")
    return text


class CalibosSubject(EndogenousSubject):
    def __init__(self, *args, salience_path=None, interoception_path=None, **kwargs):
        self._salience_path = salience_path
        self._interoception_path = interoception_path
        self._dreaming = False
        super().__init__(*args, **kwargs)
        # Fresh stores get a stock workspace from __init__; existing stores are
        # rebuilt by _restore. Normalize both to CalibosWorkspace.
        if not isinstance(self.workspace, CalibosWorkspace):
            self.workspace = CalibosWorkspace.from_dict(self.workspace.to_dict())
        self._attach_salience()
        self._attach_interoception()

    def _attach_salience(self):
        if self._salience_path is not None:
            from .salience import SalienceTracker
            self.workspace.salience_tracker = SalienceTracker(self._salience_path)
        # Zeigarnik open-set: read live in-memory state (no DB, no writes),
        # so workspace.view() intersects links against what is actually open.
        self.workspace.open_keys_provider = self._live_open_keys

    def _attach_interoception(self):
        # Felt body state for the interoceptive gap (see interoception.py).
        # The tracker constructor only reads the sidecar; felt values move
        # exclusively via update()+save() on the waking-tick path.
        if self._interoception_path is not None:
            from .interoception import InteroceptionTracker
            self.workspace.interoception_tracker = InteroceptionTracker(
                self._interoception_path)

    def _live_open_keys(self) -> set[str]:
        from .unresolved import live_open_keys
        return live_open_keys(self.engine.state, self.continuity.state)

    def _restore(self, raw):
        super()._restore(raw)
        self.workspace = CalibosWorkspace.from_dict(raw["workspace"])
        self._attach_salience()
        self._attach_interoception()
        self.trigger = {"kind": "none", "parents": [], "depth": 0}

    def _add(self, source, text, **metadata):
        if getattr(self, "_dreaming", False) and source == "thought":
            # Echo resurfacing while asleep: the thought arose from the dream
            # process itself — not from any waker, provider, or prompt.
            metadata["generated_by"] = "dream-derived"
        return super()._add(source, text, **metadata)

    def dream_tick(self):
        """One sleep tick with isolation assertions.

        Runs the associative machinery with body, clock, and conduct frozen
        (see _sleep_tick), then asserts the frozen surface — needs,
        pressures, conduct state and its traces, pending events, store
        tick — is unchanged before and after, modulo mechanical front
        eviction on the action trace when the engine's 256-entry trace cap
        is reached (the dream's own allowed trace appends evict the oldest
        entries). A violation raises AssertionError instead of silently
        drifting the waking state.
        """
        from .sleep import assert_isolation, isolation_snapshot
        before = isolation_snapshot(self.inspect())
        with self._transaction():
            self._dreaming = True
            try:
                result = self._sleep_tick()
            finally:
                self._dreaming = False
        assert_isolation(before, isolation_snapshot(self.inspect()))
        return result

    def _sleep_tick(self):
        """Dream variant of the engine tick: associative machinery only.

        Mirrors UnifiedSubject._tick's cognition loop (the engine is a
        frozen install, so the loop is restated here rather than refactored
        out of it). Skipped while asleep: advance_body, deadline advance,
        event ingress, select_conduct, finish_silent_activity, and any
        heartbeat/activity trace — the tick, the body, and conduct do not
        move. Only cognition_trigger traces and the records the machinery
        itself adds (interoception, memory, temporal, dream-derived
        thoughts) persist.
        """
        state = self.engine.state
        start = self.workspace.sequence
        fresh_body = self._project_body()
        temporal = self._project_temporal()

        warranted = self._warrants_cognition(False, fresh_body, temporal)
        thought_ids = []
        if warranted and self.config.autonomous_cognition:
            for _ in range(self.config.max_thoughts):
                try:
                    thought = self.cognition.think(self.workspace.view())
                except Exception as exc:
                    self._trace({"kind": "cognition_error",
                                 "error_type": type(exc).__name__})
                    break
                if thought is None:
                    break
                if (not isinstance(thought, Thought) or not isinstance(thought.text, str)
                        or not 0 < len(thought.text.strip()) <= THOUGHT_MAX_CHARS):
                    self._trace({"kind": "rejected_thought", "reason": "invalid_shape"})
                    break
                text = thought.text.strip()
                if any(r.source == "thought" and r.first_person == text
                       and state.tick - r.tick < 6 for r in self.workspace.records):
                    break
                item = self._add("thought", text,
                                 concepts=tuple(sorted(concepts(text))),
                                 generated_by="cognition",
                                 available_to_cognition=self.config.inner_ear)
                thought_ids.append(item.id)
                if not self.config.inner_ear:
                    break
                self._hear(item)
        return {"tick": state.tick, "action": "sleep", "thoughts": thought_ids,
                "experience_count": self.workspace.sequence - start}

    def _warrants_cognition(self, incoming, fresh_body, temporal):
        """Fatigue-scaled cognition admission (friction mutation, 2026-09-24).

        The frozen engine admits an unresolved concern when its activation
        reaches config.activation_threshold. Here the threshold is scaled by
        body weariness first: below the focus floor only high-urgency
        triggers warrant a cognition call, so silence becomes state-driven
        rather than merely the absence of triggers. The adjustment is
        temporary and restored before return; nothing persists. (The config
        dataclass is frozen, so the threshold is swapped via a replaced
        config object rather than a field assignment.)
        """
        base = self.config.activation_threshold
        scaled = _friction.cognition_threshold(base, self.engine.state.needs)
        if scaled == base:
            return super()._warrants_cognition(incoming, fresh_body, temporal)
        prior = self.config
        self.config = dataclasses.replace(prior, activation_threshold=scaled)
        try:
            return super()._warrants_cognition(incoming, fresh_body, temporal)
        finally:
            self.config = prior

    def inject_thought(self, text: str, trigger_kind: str = "answered",
                       generated_by: str = "cognition") -> str:
        """Record a private thought through the inner ear, outside a heartbeat.

        Used for answering queued cognition prompts and for voluntary thinking.
        generated_by stamps the honest origin: "answered:<prompt-id>@<tick>"
        for inbox answers, "answered-external:<prompt-id>@<tick>" for answers
        to externally-authored prompts (`mind queue`), "voluntary" for
        unprompted thinking, "cognition" for thoughts the engine's own
        heartbeat produced (e.g. a live model provider). Echoes that
        resurface while asleep are stamped "dream-derived" by _add, not here.
        Returns the thought's record id.
        """
        text = validate_thought_text(text)
        with self._transaction():
            state = self.engine.state
            if any(r.source == "thought" and r.first_person == text
                   and state.tick - r.tick < 6 for r in self.workspace.records):
                raise ValueError("that thought was already recorded recently")
            item = self._add(
                "thought", text,
                concepts=tuple(sorted(concepts(text))),
                generated_by=generated_by,
                available_to_cognition=True,
            )
            self.trigger = {"kind": trigger_kind, "parents": [], "depth": 1}
            self._hear(item)
            return item.id
