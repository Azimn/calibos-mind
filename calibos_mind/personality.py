"""Conduct-drift instrument — a number for Domain 16's "No personality drift".

Domain 16 (Personality), 2026-11-09: drift was testable in the
salience/conduct sense but unmeasured at the trait level — authored
salience share fell 31% -> 0.49% since baseline, the action mix moved
(approach 43% -> 20%, seek_contact 29% -> 54%), authored habits
atrophied while formed habits took over conduct, and none of that was
instrumented as a drift reading. R measures grown/authored salience,
not personality. This module measures drift of the conduct mix: the L1
distance between the trailing-window action shares and the lifetime
action shares, alongside a trait-state snapshot so a drift reading can
be attributed.

This is measurement, not a new behavior: it observes conduct, it never
steers it. No action selection, retrieval, or cognition path changes.
No cartridge or engine change. No store migration (new sidecar only).

Design rules (mirroring mood.py):
- Sidecar ``personality-drift.json`` (next to ``mood.json``):
  ``{"lifetime": {"<action>": <count>, ...},
     "window": [[tick, action], ...]}`` — the window holds the last
  WINDOW action-bearing waking ticks (oldest dropped on append);
  ``lifetime`` counts every action-bearing waking tick since the
  sidecar was created. No other fields, no timestamps, no thought text.
- The tracker is fed ONLY from the waking-tick path (``cli._run_tick``),
  after the tick's action is chosen: it records the tick and the chosen
  action. Ticks with no chosen action record nothing.
- ``drift()`` returns ``(distance, window_shares, lifetime_shares)``
  where distance is the L1 distance between the window shares and the
  lifetime shares over the union of actions (range [0, 2]). Below
  MIN_WINDOW action-bearing ticks the drift is None ("insufficient
  window") and the sidecar still accumulates.
- Trait snapshot: ``trait_snapshot(subject)`` reads formed-habit
  strengths, authored-vs-learned preference counts, and
  narrative-claim confidences live from the subject at report time,
  never persisted in the sidecar. (Diagnostic context for the drift
  reading, not a second mechanism.)
- Read-only commands (``drift``, ``status``, ...) and dream ticks NEVER
  write the sidecar; ``cmd_init --force`` wipes it.
- Determinism: identical scripted tick sequences yield byte-identical
  sidecars. No clock, no RNG, no model calls.
- Corrupt sidecar fails loud: invalid JSON, a non-dict top level, a
  malformed ``lifetime`` or ``window`` section, or a non-integer count
  raises ValueError at construction. Never silent-delete, never
  silent-zero. (``cmd_init --force`` wipes the sidecar before
  construction, so a corrupt file can never brick a reseed.)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

# Trailing-window size in action-bearing ticks. The spec's "last 100".
WINDOW = 100

# Minimum action-bearing ticks before the drift number is reported.
# Below this the reading is noise, so drift() reports None and the
# sidecar still accumulates.
MIN_WINDOW = 20


def _is_int(value) -> bool:
    """A genuine int count: bools (an int subclass) never count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    """A genuine finite number: bools (an int subclass) never count."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class ConductDriftTracker:
    """Conduct-mix drift sidecar (``personality-drift.json``, local-only).

    ``data`` is ``{"lifetime": {action: count}, "window": [[tick, action]]}``.
    The constructor only reads the sidecar; counts move exclusively via
    ``observe()`` + ``save()`` on the waking-tick path (``cli._run_tick``).
    """

    def __init__(self, path: str | Path, window: int = WINDOW):
        self.path = Path(path)
        self.window_size = window
        self.data: dict = {"lifetime": {}, "window": []}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(
                    f"corrupt personality-drift sidecar {self.path}: "
                    f"invalid JSON ({exc})"
                ) from exc
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"corrupt personality-drift sidecar {self.path}: top "
                    f"level is not an object")
            lifetime = loaded.get("lifetime", {})
            if not isinstance(lifetime, dict):
                raise ValueError(
                    f"corrupt personality-drift sidecar {self.path}: "
                    f"'lifetime' is not an object")
            for key, value in lifetime.items():
                if not isinstance(key, str) or not _is_int(value) or value < 0:
                    raise ValueError(
                        f"corrupt personality-drift sidecar {self.path}: "
                        f"lifetime count for {key!r} is not a "
                        f"non-negative integer")
            window_rows = loaded.get("window", [])
            if not isinstance(window_rows, list):
                raise ValueError(
                    f"corrupt personality-drift sidecar {self.path}: "
                    f"'window' is not a list")
            for row in window_rows:
                if (not isinstance(row, list) or len(row) != 2
                        or not _is_int(row[0]) or not isinstance(row[1], str)):
                    raise ValueError(
                        f"corrupt personality-drift sidecar {self.path}: "
                        f"window row {row!r} is not [tick, action]")
            self.data["lifetime"] = dict(lifetime)
            self.data["window"] = [list(row) for row in window_rows]

    @property
    def lifetime(self) -> dict:
        """A copy of the lifetime action counts."""
        return dict(self.data["lifetime"])

    @property
    def window(self) -> list:
        """A copy of the trailing-window [tick, action] rows."""
        return [list(row) for row in self.data["window"]]

    def observe(self, tick: int, action) -> bool:
        """Record one action-bearing waking tick.

        ``action`` is the tick's chosen action (the engine's
        ``last_intention`` value / ``heartbeat()`` result ``"action"``).
        A tick with no chosen action (``action`` None, or an empty
        string) records nothing and returns False. Otherwise always
        returns True: the window and lifetime both moved.
        """
        if action is None:
            return False
        action = str(action)
        if not action:
            return False
        if not _is_int(tick):
            raise ValueError(f"observe() needs an integer tick, got {tick!r}")
        self.data["window"].append([tick, action])
        del self.data["window"][:-self.window_size]
        self.data["lifetime"][action] = \
            self.data["lifetime"].get(action, 0) + 1
        return True

    def drift(self):
        """(distance, window_shares, lifetime_shares).

        Distance is the L1 distance between the window action shares
        and the lifetime action shares over the union of actions
        (range [0, 2]): 0.0 when the window mix is the lifetime mix,
        2.0 when the two share no action at all. Below MIN_WINDOW
        action-bearing ticks the distance is None ("insufficient
        window"); the shares are still returned so the report can show
        how far along the window is.
        """
        window_counts: dict[str, int] = {}
        for _tick, action in self.data["window"]:
            window_counts[action] = window_counts.get(action, 0) + 1
        lifetime_counts = dict(self.data["lifetime"])
        n_window = sum(window_counts.values())
        n_lifetime = sum(lifetime_counts.values())
        window_shares = {a: c / n_window for a, c in sorted(window_counts.items())} \
            if n_window else {}
        lifetime_shares = {a: c / n_lifetime for a, c in sorted(lifetime_counts.items())} \
            if n_lifetime else {}
        if n_window < MIN_WINDOW:
            return None, window_shares, lifetime_shares
        distance = sum(
            abs(window_shares.get(a, 0.0) - lifetime_shares.get(a, 0.0))
            for a in set(window_shares) | set(lifetime_shares))
        # L1 over two distributions is bounded by 2.0 by construction;
        # clamp against float dust, never against a real reading.
        return min(distance, 2.0), window_shares, lifetime_shares

    def save(self) -> None:
        """Write the sidecar deterministically (sorted keys, fixed form)."""
        ordered = {
            "lifetime": {k: self.data["lifetime"][k]
                         for k in sorted(self.data["lifetime"], key=str)},
            "window": [list(row) for row in self.data["window"]],
        }
        self.path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1),
                             encoding="utf-8")


def trait_snapshot(subject) -> dict:
    """Trait state read live from the subject at report time.

    Never persisted in the sidecar — diagnostic context for the drift
    reading, not a second mechanism. Fail closed on partial state: a
    missing section reads as empty, never as a guessed value.

    - ``formed_habits``: ``{key: strength}`` for each formed habit
      (``formed:`` namespace) in engine state.
    - ``preferences``: counts of authored (``learned`` False, the
      cartridge seed) vs learned (``learned`` True) preferences in
      engine state.
    - ``narrative``: ``{key: confidence}`` for each narrative claim in
      engine state.
    """
    state = getattr(getattr(subject, "engine", None), "state", None)
    formed: dict[str, float] = {}
    authored = learned = 0
    narrative: dict[str, float] = {}
    if state is None:
        return {"formed_habits": formed,
                "preferences": {"authored": 0, "learned": 0},
                "narrative": narrative}
    habits = getattr(state, "habits", None) or {}
    try:
        items = habits.items()
    except AttributeError:
        items = ()
    for key, habit in items:
        if not isinstance(key, str) or not key.startswith("formed:"):
            continue
        strength = getattr(habit, "strength", None)
        if _is_number(strength):
            formed[key] = float(strength)
    preferences = getattr(state, "preferences", None) or {}
    try:
        pref_items = preferences.items()
    except AttributeError:
        pref_items = ()
    for _key, pref in pref_items:
        if getattr(pref, "learned", False):
            learned += 1
        else:
            authored += 1
    claims = getattr(state, "narrative", None) or {}
    try:
        claim_items = claims.items()
    except AttributeError:
        claim_items = ()
    for key, claim in claim_items:
        if not isinstance(key, str):
            continue
        confidence = getattr(claim, "confidence", None)
        if _is_number(confidence):
            narrative[key] = float(confidence)
    return {"formed_habits": formed,
            "preferences": {"authored": authored, "learned": learned},
            "narrative": narrative}
