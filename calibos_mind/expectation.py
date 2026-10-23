"""Learned event expectations — trajectory-shaped surprise (engine overlay).

Domain 28 (Lack of path dependence), check (4): "Early experiences do not
alter interpretation of later ones." The frozen engine's
``SubjectEngine._own_event`` (``digital_subject/engine.py:217-228``) derives
``surprise`` from ``event.expected_valence``/``event.actual_valence``, but
both are ``None`` for every event except the feeder's (which sets both to
0.0 explicitly) — so surprise always falls back to
``clamp(event.intensity * 0.35)``, a stateless function of the current
event. There is no learned expectation anywhere: a mild event after a month
of intense events is interpreted exactly as it would be on day one.

This module patches ``SubjectEngine._own_event`` at import time — the same
overlay precedent as ``calibos_mind/narrative.py``. The venv stays
byte-identical; revert = delete this module + its import line in
``calibos_mind/__init__.py``.

What changes (contract: research/specs/2026-10-22-learned-expectations.md):

1. Predict. If ``event.expected_valence`` is None AND the local sidecar
   (``expectations.json``, local-only, gitignored) holds an EMA entry for
   this event's key, the engine sees ``expected_valence = ema`` and
   ``actual_valence = event.actual_valence if not None else event.valence``,
   so surprise becomes ``|actual - ema|`` — a function of lived history.
   Keys are ``kind + "|" + ",".join(sorted(tags))``.
2. Learn. After the engine's own body runs (use-then-update ordering: the
   current event's valence never informs its own expectation), the EMA for
   the key moves ``ema := ema + 0.3 * (actual - ema)`` with
   ``actual = event.actual_valence if not None else event.valence``,
   storing ``{ema, count, last_tick}``. The sidecar is capped at 256
   entries, evicting stalest ``last_tick`` first (ties broken by key, so
   replay stays byte-identical).
3. Never override. Events carrying an explicit ``expected_valence``
   (feeder's 0.0/0.0 today) are never touched and never learn into the
   sidecar — the machinery observes only the organism's interpreted world,
   never the host's scheduled feedings.
4. Hygiene. Cold start (no sidecar entry for the key): nothing is set, the
   engine's pristine ``intensity * 0.35`` fallback applies byte-identically.
   Read-only commands and dream ticks never write the sidecar (dream ticks
   never call ``step()`` at all; a suspension flag is set as belt-and-braces
   during ``dream_tick``). ``mind init --force`` wipes the sidecar.

Implementation notes:

- The patch passes the engine a *shadow copy* of the event
  (``dataclasses.replace``) carrying the predicted valences, rather than
  mutating the caller's event in place. Same observable contract — surprise
  is computed from the ema — but a re-fed event object can never be
  misread as "explicit" on a second pass (the never-override rule would
  misfire on a mutated event). ``owned.event`` is the shadow; the only
  downstream reader of its valence fields is ``_learn_preference``, whose
  own ``actual_valence if not None else valence`` expression equals the
  shadow's fields by construction.
- The tracker is resolved per call from the engine instance attribute
  ``_expectation_tracker``. The frozen engine is rebuilt from the payload
  on every transaction, so ``CalibosSubject`` re-attaches the tracker in
  its ``_restore`` override (pure pointer move, never a write). An engine
  with no tracker (e.g. a bare ``SubjectEngine`` in tests, or
  ``expectation_path=None``) takes the pristine path untouched.
- Writes happen only in ``learn`` (one deterministic JSON write per waking
  event — required, since each CLI command is a fresh process). ``predict``
  and construction never write; a missing sidecar is an empty map, never a
  created file. Corrupt sidecars fail loud (ValueError), like the other
  trackers.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from digital_subject.engine import SubjectEngine
from digital_subject.models import Event

# -- tuning constants (documented; the fitness tests pin their behavior) -----

#: EMA learning rate for the per-key expected valence.
LEARNING_RATE = 0.3

#: Maximum sidecar entries; beyond this the stalest last_tick evicts first.
SIDECAR_CAP = 256


def _key_for(event: Event) -> str:
    return f"{event.kind}|{','.join(sorted(event.tags))}"


class ExpectationTracker:
    """Per-(kind, tags) exponential moving average of actual event valence.

    The constructor only reads the sidecar. Mutations move exclusively
    through ``learn()`` on the waking-event path (the ``_own_event``
    overlay below). Read-only commands and dream ticks never write.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: dict[str, dict] = {}
        if self.path.exists():
            self.entries = self._load(self.path)

    @staticmethod
    def _load(path: Path) -> dict[str, dict]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupt expectations sidecar at {path}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("entries"), dict):
            raise ValueError(
                f"corrupt expectations sidecar at {path}: "
                "expected {'entries': {key: {'ema', 'count', 'last_tick'}}}"
            )
        entries: dict[str, dict] = {}
        for key, rec in raw["entries"].items():
            if not isinstance(rec, dict):
                raise ValueError(f"corrupt expectations sidecar at {path}: bad record for {key!r}")
            ema, count, last_tick = rec.get("ema"), rec.get("count"), rec.get("last_tick")
            if isinstance(ema, bool) or not isinstance(ema, (int, float)):
                raise ValueError(f"corrupt expectations sidecar at {path}: bad ema for {key!r}")
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError(f"corrupt expectations sidecar at {path}: bad count for {key!r}")
            if isinstance(last_tick, bool) or not isinstance(last_tick, int):
                raise ValueError(f"corrupt expectations sidecar at {path}: bad last_tick for {key!r}")
            entries[str(key)] = {
                "ema": float(ema),
                "count": count,
                "last_tick": last_tick,
            }
        return entries

    def predict(self, event: Event) -> float | None:
        """Expected valence for this event's key, or None when unknown.

        Read-only: never writes the sidecar.
        """
        rec = self.entries.get(_key_for(event))
        return rec["ema"] if rec is not None else None

    def learn(self, event: Event, tick: int) -> None:
        """Fold this event's actual valence into the key's EMA (then save).

        Use-then-update: call only after the engine's own ``_own_event``
        body has consumed the prediction, so the current event never
        informs its own expectation.
        """
        key = _key_for(event)
        actual = event.actual_valence
        actual = float(actual) if actual is not None else float(event.valence)
        rec = self.entries.get(key)
        if rec is None:
            self.entries[key] = {"ema": actual, "count": 1, "last_tick": int(tick)}
            while len(self.entries) > SIDECAR_CAP:
                # Stalest last_tick evicts first; the key tie-break keeps
                # replay byte-identical (dict iteration order would not).
                victim = min(
                    self.entries,
                    key=lambda k: (self.entries[k]["last_tick"], k),
                )
                del self.entries[victim]
        else:
            rec["ema"] = rec["ema"] + LEARNING_RATE * (actual - rec["ema"])
            rec["count"] += 1
            rec["last_tick"] = int(tick)
        self._save()

    def _save(self) -> None:
        # sort_keys makes the bytes deterministic: identical event
        # sequences replay to a byte-identical sidecar.
        self.path.write_text(
            json.dumps({"entries": self.entries}, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _own_event_with_expectations(self, event: Event):
    tracker = getattr(self, "_expectation_tracker", None)
    suspended = getattr(self, "_expectation_suspended", False)
    explicit = event.expected_valence is not None
    shadow = event
    if tracker is not None and not suspended and not explicit:
        ema = tracker.predict(event)
        if ema is not None:
            # Shadow copy: the caller's event object is never mutated, so a
            # re-fed event is never misread as carrying an explicit value.
            shadow = dataclasses.replace(
                event,
                expected_valence=ema,
                actual_valence=(
                    event.actual_valence
                    if event.actual_valence is not None
                    else event.valence
                ),
            )
    owned = _ORIGINAL_OWN_EVENT(self, shadow)
    if tracker is not None and not suspended and not explicit:
        tracker.learn(event, self.state.tick)
    return owned


_ORIGINAL_OWN_EVENT = SubjectEngine._own_event


def install() -> None:
    """Activate the overlay (idempotent). Called at package import."""
    SubjectEngine._own_event = _own_event_with_expectations


def uninstall() -> None:
    """Restore the frozen engine's original (for before/after checks)."""
    SubjectEngine._own_event = _ORIGINAL_OWN_EVENT


install()
