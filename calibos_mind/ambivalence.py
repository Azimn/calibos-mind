"""Contested-margin ambivalence trace markers.

Domain 7 (Motivation) audit: the frozen engine's ``_choose_intention``
resolves motive competition by argmax with a 0.12 deadband — the dominant
pressure wins iff it beats the dominant need by 0.12 or more, otherwise the
need channel wins. A near-tie (a pressure at 0.78 against a need at 0.72)
is a real motive conflict, but the argmax leaves no trace of it: later
cognition cannot see that the choice was contested.

This module installs the *mechanism* (a trace of real near-ties), never the
*symptom*: nothing here makes the organism dither, hedge, or narrate
conflict. Ambivalence as scripted behavior is forbidden by the
emerge-vs-install discipline; a trace of an actual thin margin is
legitimate instrumentation of a phenomenon the engine genuinely produced.

Design rules:
- Zero behavior change: the observer wraps ``engine._choose_intention`` on
  the composed instance (never the frozen class or its source), delegates
  to the pristine bound method FIRST, and only then computes a marker from
  the exact triage lists the engine used. The wrapper never alters the
  returned action or any engine state.
- One parameter: CONTESTED_MARGIN = 0.12, the engine's own deadband,
  reused for within-channel near-ties so the whole module has a single
  documented scale.
- Local-only sidecar (ambivalence.json, gitignored), capped at the last
  64 markers. Never touches mind.db: the constructor only reads, markers
  accumulate in memory during the tick and flush on the waking-tick path.
- ``mind init --force`` wipes the sidecar; read-only commands
  (``drift``, ``status``) never tick, so they never write it.
- Determinism: markers carry rounded floats and no wall clock; identical
  sequences produce byte-identical sidecars (sorted JSON, insertion order
  preserved).

Formulas copied from the frozen engine (digital_subject/engine.py,
jelly_psiduck-0.2.0a2 snapshot, non-editable .venv install):
- ``_choose_intention`` (lines 336-344): dominant_pressure =
  pressures[0] else ("trust", 0.0); dominant_need = needs[0] else
  ("curiosity", 0.0); pressure channel wins iff
  dominant_pressure[1] >= dominant_need[1] + 0.12.
- The contested region is therefore |p - n| < 0.12 — the band where the
  outcome flips on a hair. The winning channel is replicated from the
  exact rule above, not inferred from the returned action (habits and the
  low-trust CONCEAL override pick *within* the winning channel).
- The triage lists themselves are NOT replicated: the wrapper receives
  the exact lists the engine computed via its own ``_triage_needs()`` /
  ``_triage_pressures()`` (including the endogenous concern: renaming),
  so no urgency formula is duplicated here.
- Channel-rule bypass (lines 337-338): ``if event.kind in {"apology"}:
  return Action.REPAIR`` — for apology events the engine never runs the
  channel rule at all, so no channel selection was contested and the
  observer must stay silent. ``CHANNEL_RULE_BYPASS_KINDS`` mirrors this
  set in the wrapper: a marker there would fabricate a "winning channel"
  for a selection the engine never made.
"""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path

# The frozen engine's own deadband (engine.py:341:
# ``if dominant_pressure[1] >= dominant_need[1] + 0.12``). The single
# documented scale for this module: a selection is contested when the
# cross-channel margin |p - n| falls inside the deadband, or when the top
# two contenders *within* one channel fall inside it.
CONTESTED_MARGIN = 0.12

# Bounded sidecar: only the most recent markers are kept. A near-tie is a
# momentary state of the competition, not history worth keeping forever.
AMBIVALENCE_CAP = 64

# Event kinds for which the frozen engine bypasses the channel rule entirely
# (engine.py:337-338: ``if event.kind in {"apology"}: return Action.REPAIR``),
# pinned against jelly_psiduck-0.2.0a2. For these kinds the engine never
# ran the argmax selection — there is no contested margin to trace — so
# the observer mirrors the bypass and notes nothing. A missed marker is
# always the safe direction; a fabricated one is the failure mode.
CHANNEL_RULE_BYPASS_KINDS = frozenset({"apology"})


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def contested_marker(*, tick, event_kind, needs, pressures):
    """Compute the contested-margin marker for one intention selection.

    ``needs``/``pressures`` are the exact triage lists the frozen engine
    passed to ``_choose_intention`` (already sorted descending,
    top_k-capped). Returns a marker dict, or None when the margin is
    clear on every axis. A missed marker (malformed input) is always the
    safe direction: this is an observer, and it must never invent a
    contest the engine did not have.
    """
    needs = list(needs or [])
    pressures = list(pressures or [])

    def _head(scored, default):
        if scored and isinstance(scored[0], (list, tuple)) and len(scored[0]) == 2:
            key, value = scored[0]
            if isinstance(key, str) and _finite_number(value):
                return key, float(value)
        return default

    p_key, p_val = _head(pressures, ("trust", 0.0))
    n_key, n_val = _head(needs, ("curiosity", 0.0))

    def _second_gap(scored, head_val):
        if len(scored) >= 2 and isinstance(scored[1], (list, tuple)) \
                and len(scored[1]) == 2 and isinstance(scored[1][0], str) \
                and _finite_number(scored[1][1]):
            return head_val - float(scored[1][1]), scored[1][0]
        return None, None

    need_gap, need_runner = _second_gap(needs, n_val)
    pressure_gap, pressure_runner = _second_gap(pressures, p_val)

    margin = p_val - n_val  # > 0 favors the pressure channel
    # Exact engine rule (engine.py:341): pressure wins iff it clears the
    # deadband over the need; otherwise the need channel wins.
    pressure_wins = p_val >= n_val + CONTESTED_MARGIN
    winning_channel = "pressure" if pressure_wins else "need"

    cross_contested = abs(margin) < CONTESTED_MARGIN
    need_tie = need_gap is not None and need_gap < CONTESTED_MARGIN
    pressure_tie = pressure_gap is not None and pressure_gap < CONTESTED_MARGIN
    if not (cross_contested or need_tie or pressure_tie):
        return None

    if cross_contested:
        contenders = [p_key, n_key]
        channels = ["pressure", "need"]
        pair_margin = margin
        winner = p_key if pressure_wins else n_key
    elif need_tie and (not pressure_tie or need_gap <= pressure_gap):
        contenders = [n_key, need_runner]
        channels = ["need", "need"]
        pair_margin = need_gap
        winner = n_key
    else:
        contenders = [p_key, pressure_runner]
        channels = ["pressure", "pressure"]
        pair_margin = pressure_gap
        winner = p_key

    return {
        "tick": int(tick),
        "event": str(event_kind),
        "contenders": contenders,
        "channels": channels,
        # margin sign convention: pressure_value - need_value for
        # cross-channel pairs (positive favors pressure); top - runner-up
        # for within-channel pairs (always >= 0).
        "margin": round(pair_margin, 4),
        "winner": winner,
        "winning_channel": winning_channel,
    }


class AmbivalenceTracker:
    """Bounded local sidecar for contested-margin markers.

    ``data`` is ``{"markers": [...]}``, newest last, capped at
    AMBIVALENCE_CAP. The constructor only reads the sidecar; markers move
    exclusively via ``note()`` + ``flush()`` on the waking-tick path
    (``cli._run_tick``). Read-only commands never call either.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.markers: list = []
        self._pending: list = []
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                raw = loaded.get("markers")
                if isinstance(raw, list):
                    self.markers = [m for m in raw if isinstance(m, dict)][-AMBIVALENCE_CAP:]
            except (ValueError, OSError):
                pass

    def note(self, marker: dict) -> None:
        """Stage one contested marker in memory. Flushed by ``flush()``."""
        self._pending.append(marker)

    @property
    def pending(self) -> int:
        return len(self._pending)

    def flush(self) -> bool:
        """Append staged markers to the sidecar, trimmed to the cap.

        Returns True iff anything was staged, so the caller saves only
        then — a tick with no near-ties writes nothing: absent stays
        absent, present stays byte-identical (no-op write discipline).
        """
        if not self._pending:
            return False
        self.markers.extend(self._pending)
        self.markers = self.markers[-AMBIVALENCE_CAP:]
        self._pending = []
        self.save()
        return True

    def reset(self) -> None:
        """Clear the sidecar (used when the store is reseeded).

        Stale contested margins must never attach to a new incarnation's
        ticks — same bug class as the salience/familiarity resets.
        """
        self.markers = []
        self._pending = []
        self.save()

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"markers": self.markers}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


def install_observer(subject) -> None:
    """Wrap the subject's engine ``_choose_intention`` with a pure observer.

    The wrapper is installed on the composed engine *instance* — the
    frozen class and its source are never modified. It delegates to the
    pristine bound method first, then records a contested-margin marker
    when the selection was a near-tie. It never alters the returned
    action or any engine state.

    Idempotent: safe to call again after ``_restore`` swaps in a fresh
    engine instance (every ``_transaction`` does), and a no-op if the
    current instance is already observed.
    """
    engine = subject.engine
    original = engine._choose_intention
    if getattr(original, "_ambivalence_wrapped", False):
        return

    @functools.wraps(original)
    def _observed_choose_intention(event, needs, pressures):
        action = original(event, needs, pressures)
        try:
            # Mirror the frozen engine's channel-rule bypass
            # (engine.py:337-338): for these event kinds the engine returned
            # without running the channel rule, so no channel selection
            # was contested — noting a marker would fabricate a "winning
            # channel" the engine never computed.
            if getattr(event, "kind", "?") in CHANNEL_RULE_BYPASS_KINDS:
                return action
            if not getattr(subject, "_dreaming", False):
                tracker = getattr(subject.workspace, "ambivalence_tracker", None)
                if tracker is not None:
                    marker = contested_marker(
                        tick=engine.state.tick,
                        event_kind=getattr(event, "kind", "?"),
                        needs=needs,
                        pressures=pressures,
                    )
                    if marker is not None:
                        tracker.note(marker)
        except Exception:
            # Observation is strictly subordinate to the tick: a marker
            # computation failure must never break a tick the pristine
            # engine would have completed. A missed marker is always the
            # safe direction.
            pass
        return action

    _observed_choose_intention._ambivalence_wrapped = True
    engine._choose_intention = _observed_choose_intention
