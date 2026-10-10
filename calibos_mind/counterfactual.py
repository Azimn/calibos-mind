"""Foregone-option trace for habit bypasses.

Domain 14 (Decision making) audit: the frozen engine's ``_choose_intention``
resolves conduct selection with a within-channel habit bypass — a formed
habit with strength >= 0.65 may preempt the deliberated choice, and the
deliberated alternative is discarded silently. This module records the
foregone option: what the argmax would have chosen without the habit.

This is honest bookkeeping in the ambivalence family: a trace of a real
selection the engine genuinely made, never installed behavior. Nothing here
makes the organism hesitate, second-guess, or narrate counterfactuals —
that would be bias-as-theater, forbidden by the emerge-vs-install
discipline. The record exists so future falsification passes can see what
each bypass displaced.

Design rules (mirroring ambivalence.py, same bug family):
- Zero behavior change: the observer wraps ``engine._choose_intention`` on
  the composed instance (never the frozen class or its source), delegates
  to the pristine bound method FIRST, and only then recomputes the
  deliberated path read-only. The recomputation NEVER calls
  ``_choose_intention`` (it has side effects: ``habit.last_used_tick``)
  and mirrors the engine's logic exactly — apology-event bypass, the 0.12
  pressure-vs-need deadband, the real NEED_ACTIONS / PRESSURE_ACTIONS
  tables (imported from digital_subject.engine, never copied), the
  trust<0.25 -> CONCEAL branch, else options[0]. The wrapper never alters
  the returned action or any engine state.
- Records ONLY genuine bypasses: a habit in ``engine.state.habits`` with
  ``last_used_tick == current tick``, ``strength >= 0.65``, and
  ``action ==`` the tick's selected action, AND a recomputed deliberated
  choice that differs from the habit action. The stamp proves the bypass
  fired — the deliberated path never writes ``last_used_tick``, and the
  bypass branch is its only writer (engine.py:350) — so nothing else can
  have set it on this tick. Cooldown and options-membership are proven by
  the stamp too: the branch only fires when they hold.
- No record on apology ticks (the engine never ran the channel rule —
  there is no deliberated alternative to forgo), on CONCEAL ticks (no
  habit fired), on ticks where the habit action equals options[0] (the
  bypass changed nothing — no foregone option), or on any tick where no
  formed habit fired.
- Local-only sidecar (counterfactuals.json, gitignored), capped at the
  last 128 records. Never touches mind.db: records accumulate in memory
  during the tick and flush on the waking-tick path (cli._run_tick).
- ``mind init --force`` wipes the sidecar; read-only commands and dream
  ticks never write.
- Determinism: records carry rounded floats and no wall clock; identical
  sequences produce byte-identical sidecars (insertion order preserved,
  same no-op write discipline as ambivalence.json).

Formulas mirrored from the frozen engine (digital_subject/engine.py,
jelly_psiduck-0.2.0a2 snapshot, non-editable .venv install),
``_choose_intention`` (lines 336-359), MINUS the habit branch:
- apology bypass (lines 337-338): ``if event.kind in {"apology"}:
  return Action.REPAIR`` — no channel selection ran, so there is no
  deliberated alternative; the observer notes nothing.
- dominant_pressure = pressures[0] else ("trust", 0.0);
  dominant_need = needs[0] else ("curiosity", 0.0).
- pressure channel wins iff dominant_pressure[1] >=
  dominant_need[1] + 0.12 (line 341); the pressure key is stripped of the
  "concern:" prefix for the options lookup.
- options = PRESSURE_ACTIONS.get(key, (ANSWER, ASK, DEFLECT)) /
  NEED_ACTIONS.get(key, (ANSWER, ASK, OBSERVE)).
- trust override (lines 354-355): ``if self.state.pressures.get("trust",
  0.5) < 0.25 and Action.CONCEAL in options: return Action.CONCEAL`` —
  the trust read is the RAW pressure value, not the triage magnitude, so
  the recomputation takes it as a separate argument.
- else options[0].
"""
from __future__ import annotations

import functools
import json
import math
from pathlib import Path

from digital_subject.engine import NEED_ACTIONS, PRESSURE_ACTIONS
from digital_subject.models import Action

# The frozen engine's own channel deadband (engine.py:341:
# ``if dominant_pressure[1] >= dominant_need[1] + 0.12``). Reused verbatim
# here: the deliberated path IS the engine's channel rule.
CHANNEL_DEADBAND = 0.12

# The frozen engine's habit-fire threshold (engine.py:349:
# ``habit.strength >= 0.65``).
HABIT_FIRE_STRENGTH = 0.65

# The frozen engine's low-trust override (engine.py:354:
# ``self.state.pressures.get("trust", 0.5) < 0.25``).
TRUST_CONCEAL_THRESHOLD = 0.25

# Bounded sidecar: only the most recent records are kept. A foregone
# option is evidence for a falsification pass, not history worth keeping
# forever — and bypasses are rare (<5% of ticks by the fitness window).
COUNTERFACTUAL_CAP = 128

# Event kinds for which the frozen engine bypasses the channel rule
# entirely (engine.py:337-338: ``if event.kind in {"apology"}:
# return Action.REPAIR``), pinned against jelly_psiduck-0.2.0a2. For these
# kinds the engine never ran the argmax selection — there is no
# deliberated alternative to forgo — so the observer mirrors the bypass
# and notes nothing. A missed record is always the safe direction; a
# fabricated foregone option is the failure mode.
CHANNEL_RULE_BYPASS_KINDS = frozenset({"apology"})


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def deliberated_choice(*, event_kind, needs, pressures, trust_value):
    """Recompute what ``_choose_intention`` returns with no habit.

    Mirrors the frozen engine exactly (engine.py:336-359) MINUS the habit
    branch — the bypass this module traces. Returns
    ``(action, trigger_channel, dominant_need, dominant_pressure, margin)``,
    or None when the engine never ran the channel rule (``event.kind in
    {"apology"}`` -> REPAIR) or the input is malformed. A missed record is
    always the safe direction: this observer must never invent a foregone
    option the engine did not consider.

    ``trust_value`` is the engine's own
    ``state.pressures.get("trust", 0.5)`` — the RAW pressure value, not
    the triage magnitude. The trust<0.25 -> CONCEAL branch reads that raw
    value (engine.py:354), so passing the triage magnitude here would be
    a fidelity break.

    ``margin`` follows the ambivalence sign convention:
    pressure_value - need_value (positive favors the pressure channel).
    """
    if event_kind in CHANNEL_RULE_BYPASS_KINDS:
        return None

    def _head(scored, default):
        if (scored and isinstance(scored[0], (list, tuple))
                and len(scored[0]) == 2):
            key, value = scored[0]
            if isinstance(key, str) and _finite_number(value):
                return key, float(value)
        return default

    p_key, p_val = _head(pressures, ("trust", 0.0))
    n_key, n_val = _head(needs, ("curiosity", 0.0))

    if not _finite_number(trust_value):
        return None
    trust = float(trust_value)

    # Exact engine rule (engine.py:341): the pressure channel wins iff it
    # clears the deadband over the dominant need; otherwise the need
    # channel wins.
    if p_val >= n_val + CHANNEL_DEADBAND:
        key = p_key.replace("concern:", "")
        options = PRESSURE_ACTIONS.get(
            key, (Action.ANSWER, Action.ASK, Action.DEFLECT))
        trigger_channel = "pressure"
    else:
        options = NEED_ACTIONS.get(
            n_key, (Action.ANSWER, Action.ASK, Action.OBSERVE))
        trigger_channel = "need"

    # Exact engine rule (engine.py:354-355): low trust preempts the
    # deliberated option with CONCEAL when it is available.
    if trust < TRUST_CONCEAL_THRESHOLD and Action.CONCEAL in options:
        chosen = Action.CONCEAL
    else:
        chosen = options[0]
    return chosen, trigger_channel, n_key, p_key, p_val - n_val


def _fired_habit(engine, action):
    """Return the (key, habit) whose bypass branch fired, or None.

    Read-only detection: the bypass branch is the only writer of
    ``habit.last_used_tick`` (engine.py:350), so a habit stamped with the
    current tick fired its bypass THIS tick — cooldown and
    options-membership held, because the branch only fires when they do.
    The deliberated path never writes the stamp, so it can never
    masquerade as a bypass.
    """
    tick = engine.state.tick
    for key, habit in engine.state.habits.items():
        if (habit.last_used_tick == tick
                and habit.strength >= HABIT_FIRE_STRENGTH
                and habit.action == action):
            return key, habit
    return None


class CounterfactualTracker:
    """Bounded local sidecar for foregone-option records.

    ``data`` is ``{"records": [...]}``, oldest first, capped at
    COUNTERFACTUAL_CAP. The constructor only reads the sidecar; records
    move exclusively via ``note()`` + ``flush()`` on the waking-tick path
    (``cli._run_tick``). Read-only commands never call either.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.records: list = []
        self._pending: list = []
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                raw = loaded.get("records")
                if isinstance(raw, list):
                    self.records = [r for r in raw if isinstance(r, dict)][-COUNTERFACTUAL_CAP:]
            except (ValueError, OSError):
                pass

    def note(self, record: dict) -> None:
        """Stage one foregone-option record in memory. Flushed by ``flush()``."""
        self._pending.append(record)

    @property
    def pending(self) -> int:
        return len(self._pending)

    def flush(self) -> bool:
        """Append staged records to the sidecar, trimmed to the cap.

        Returns True iff anything was staged, so the caller saves only
        then — a tick with no bypass writes nothing: absent stays absent,
        present stays byte-identical (no-op write discipline).
        """
        if not self._pending:
            return False
        self.records.extend(self._pending)
        self.records = self.records[-COUNTERFACTUAL_CAP:]
        self._pending = []
        self.save()
        return True

    def reset(self) -> None:
        """Clear the sidecar (used when the store is reseeded).

        Stale foregone options must never attach to a new incarnation's
        ticks — same bug class as the ambivalence/salience resets.
        """
        self.records = []
        self._pending = []
        self.save()

    def save(self) -> None:
        self.path.write_text(
            json.dumps({"records": self.records}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


def install_observer(subject) -> None:
    """Wrap the subject's engine ``_choose_intention`` with a pure observer.

    The wrapper is installed on the composed engine *instance* — the
    frozen class and its source are never modified. It delegates to the
    pristine bound method first, then checks (read-only) whether a habit
    bypass fired and recomputes the deliberated alternative the bypass
    displaced. It never alters the returned action or any engine state.

    Idempotent: safe to call again after ``_restore`` swaps in a fresh
    engine instance (every ``_transaction`` does), and a no-op if the
    current instance is already observed. Stacks cleanly with the
    ambivalence observer (each wrapper carries its own idempotence flag).
    """
    engine = subject.engine
    original = engine._choose_intention
    if getattr(original, "_counterfactual_wrapped", False):
        return

    @functools.wraps(original)
    def _observed_choose_intention(event, needs, pressures):
        action = original(event, needs, pressures)
        try:
            event_kind = getattr(event, "kind", "?")
            # Mirror the frozen engine's channel-rule bypass
            # (engine.py:337-338): for these event kinds the engine
            # returned without running the channel rule, so there is no
            # deliberated alternative to forgo — noting a record would
            # fabricate a foregone option the engine never considered.
            if event_kind in CHANNEL_RULE_BYPASS_KINDS:
                return action
            # Dream ticks never trace: the dream path freezes conduct,
            # and any selection there is not a waking decision.
            if getattr(subject, "_dreaming", False):
                return action
            tracker = getattr(subject.workspace, "counterfactual_tracker", None)
            if tracker is None:
                return action
            # Genuine bypass? The stamp is read-only proof (see
            # _fired_habit): the deliberated path never writes it.
            fired = _fired_habit(engine, action)
            if fired is None:
                return action
            # Recompute the deliberated path WITHOUT calling
            # _choose_intention (it has side effects) — mirror its logic
            # exactly. The trust read is the raw pressure value, matching
            # the engine's own ``state.pressures.get("trust", 0.5)``.
            recomputed = deliberated_choice(
                event_kind=event_kind,
                needs=needs,
                pressures=pressures,
                trust_value=engine.state.pressures.get("trust", 0.5),
            )
            if recomputed is None:
                return action
            foregone, trigger_channel, n_key, p_key, margin = recomputed
            if foregone == action:
                # The habit action equals the deliberated alternative: the
                # bypass changed nothing, so there is no foregone option
                # to trace. (Clear-margin ticks land here.)
                return action
            habit_key, _habit = fired
            tracker.note({
                "tick": int(engine.state.tick),
                "habit_key": str(habit_key),
                "trigger_channel": str(trigger_channel),
                "chosen_action": action.value,
                "foregone_action": foregone.value,
                "dominant_need": str(n_key),
                "dominant_pressure": str(p_key),
                "margin": round(float(margin), 4),
            })
        except Exception:
            # Observation is strictly subordinate to the tick: a record
            # computation failure must never break a tick the pristine
            # engine would have completed. A missed record is always the
            # safe direction.
            pass
        return action

    _observed_choose_intention._counterfactual_wrapped = True
    engine._choose_intention = _observed_choose_intention
