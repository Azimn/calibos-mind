"""Habit formation — conduct chasing, not conduct authoring.

Domain 8 (Habits and procedural continuity) audit: the frozen engine fires
cartridge habits inside the already-chosen intention channel when
``habit.strength >= 0.65``, but strength is a fixed cartridge constant and
the habit set is closed at two authored entries (``curious_question``
0.72, ``verify_before_claiming`` 0.68). Repetition never makes behavior
more automatic, and there are no bad habits — the two diagnostic gaps this
module addresses.

This installs the *mechanism* (formation, growth, disuse decay, removal),
never the *symptom*: no habit is authored here. Habits crystallize from
lived (trigger, action) co-firing, strengthen with repetition up to a cap
deliberately below the strongest authored habit (formed habits modulate,
authored habits define identity), decay through disuse, and are removed
with a written archival note — history archived with a reason, never
silently deleted. A formed habit can conflict with goals: formation is
pure repetition-based, which the taxonomy lists as a symptom of
authenticity, not a bug.

Design rules:
- The frozen engine is never modified. Formed habits are real
  ``digital_subject.models.Habit`` entries in ``engine.state.habits``,
  written by the wrapper layer through the normal ``_transaction``
  payload path (INSERT OR REPLACE). The wrapper owns state — the engine
  only fires what it finds there.
- Observation is pure. A wrapper on the composed engine *instance's*
  ``select_conduct`` — called exactly once per waking tick, never on the
  dream path — delegates to the pristine bound method FIRST, then notes
  the (trigger, action) from the exact triage lists the engine used. It
  never alters the returned action or any engine state. The trigger is
  the channel winner by the engine's own rule (pressure wins iff
  p >= n + 0.12, else the dominant need), replicated not inferred, with
  the engine's apology bypass mirrored (no channel selection ran, so no
  trigger is recorded — a missed observation is safe, a fabricated one
  is the hazard).
- Local-only sidecar (``habits-formed.json``, gitignored). The sidecar
  is the tracker's continuity: the rolling window, formed-habit metadata
  (strength, peak, creation tick), and the archive of removals.
  ``state.habits`` is a projection of it, reconciled after every change.
  Writes are atomic (temp file + ``os.replace`` + fsync) so a crash
  mid-write cannot truncate the file into an unparseable shell.
- Observe + save-only-when-changed: the window advances on every
  observed waking tick (a rolling window is a tick log); the DB
  transaction for ``state.habits`` opens only when formed-habit state
  actually changed. Read-only commands never tick, so they never write.
- Determinism: no wall clock, no RNG; rounded floats, sorted keys.
  Identical tick sequences produce byte-identical sidecars.
- Waking ticks only: ``dream_tick()`` never calls ``select_conduct``,
  and the ``_dreaming`` guard drops any note if it ever did.

Formation rule (the documented resolution of one ambiguity): the rolling
window holds the last 8 observed ticks. A (trigger, action) pair
co-firing >= 3 times within the window crystallizes when the mean
per-tick delta of the trigger need across those co-fire ticks is <= 0 —
the behavior isn't making its driving need worse. When the trigger is a
pressure key it has no need-delta of its own; the check then uses the
tick's dominant need (the need under most demand while the pressure
drove) — documented here, not hidden, because pressure-driven repetition
(fear -> withdraw) is exactly the material bad habits are made of.
"""
from __future__ import annotations

import functools
import json
import math
import os
from pathlib import Path

from digital_subject.models import Action, Habit

# Rolling window: the last N observed waking ticks considered for formation.
FORMATION_WINDOW = 8
# Co-fires of one (trigger, action) pair within the window that crystallize it.
FORMATION_COFIRES = 3
# Strength a habit is born with: below the engine's 0.65 fire threshold, so
# a newborn habit modulates nothing until repetition earns automaticity.
CRYSTALLIZE_STRENGTH = 0.45
# Per co-fire growth; deliberately capped below the strongest authored
# habit (curious_question, 0.72): formed habits modulate, authored habits
# define identity.
GROWTH_STEP = 0.02
GROWTH_CAP = 0.70
# Per-tick decay when the trigger dominates but a different action is chosen.
DECAY_STEP = 0.05
# Below this strength the habit is removed (with a written archival note).
REMOVAL_FLOOR = 0.30
# Namespace for formed habits; authored habits are never touched.
FORMED_PREFIX = "formed:"
# Bounded archive: removals are history worth keeping, but the sidecar must
# never grow unbounded (a revert signal for this mutation).
ARCHIVE_CAP = 64
# The engine's own channel deadband (digital_subject/engine.py,
# ``_choose_intention``: pressure wins iff p >= n + 0.12). Replicated, not
# imported, so this module stays standalone like the other trackers.
CHANNEL_DEADBAND = 0.12
# Event kinds for which the frozen engine bypasses the channel rule entirely
# (engine.py: ``if event.kind in {"apology"}: return Action.REPAIR``).
# Mirrored from ambivalence.py: no channel selection ran, so there is no
# driving trigger to record.
CHANNEL_RULE_BYPASS_KINDS = frozenset({"apology"})
# Written reason on every removal: history archived with a reason, never
# silently deleted.
REMOVAL_REASON = "decayed below formation floor through disuse"


def _finite_number(value) -> bool:
    # bool is an int subclass; True/False are not numbers in this
    # tracker's domain (a strength of True would fire: True >= 0.65).
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value))


def resolve_trigger(needs, pressures):
    """Replicate the frozen engine's channel rule on triage lists.

    ``needs``/``pressures`` are (key, urgency) lists, descending, as the
    engine's ``_triage_needs()`` / ``_triage_pressures()`` return them.
    Returns ``(trigger, dominant_need)``: the trigger is the pressure key
    (``concern:`` prefix stripped, the spelling the engine itself uses for
    its option lookup) when the pressure channel wins the 0.12 deadband,
    else the dominant need key. ``dominant_need`` is always the head of
    the need triage — the formation delta proxy when the trigger is a
    pressure (see module docstring).
    """
    def _head(scored, default):
        if scored and isinstance(scored[0], (list, tuple)) and len(scored[0]) == 2:
            key, value = scored[0]
            if isinstance(key, str) and _finite_number(value):
                return key, float(value)
        return default

    p_key, p_val = _head(pressures, ("trust", 0.0))
    n_key, n_val = _head(needs, ("curiosity", 0.0))
    if p_val >= n_val + CHANNEL_DEADBAND:
        return p_key.replace("concern:", ""), n_key
    return n_key, n_key


class HabitFormationTracker:
    """Rolling-window habit formation backed by a local-only sidecar.

    ``data`` is ``{"window": [...], "formed": {...}, "archived": [...]}``:
    - ``window``: the last ``FORMATION_WINDOW`` observed ticks, each
      ``{"tick", "trigger", "action", "dominant_need", "delta"}`` where
      ``delta`` is the rounded per-tick delta of the trigger-relevant
      need (the trigger's own delta for need triggers, the dominant
      need's delta for pressure triggers).
    - ``formed``: ``{"formed:<trigger>:<action>": {"trigger", "action",
      "strength", "peak", "created_tick", "cofires", "last_tick"}}``.
    - ``archived``: removal notes ``{"key", "peak", "lifespan_ticks",
      "reason", "archived_tick"}``, newest last, capped at
      ``ARCHIVE_CAP``.

    The constructor only reads the sidecar. One waking tick folds in via
    ``observe_tick()``; the caller saves only when it reports a change
    and opens a state transaction only when formed-habit state changed.

    Load contract — fail loud, never silent zero. A missing sidecar is a
    legitimate fresh start (empty state). A sidecar that exists but is
    unparseable, has the wrong top-level shape, or holds a malformed
    ``formed`` entry raises ``ValueError``: the stale-key deletion in
    ``reconcile_state_habits`` treats the sidecar's formed map as ground
    truth, so a silently-emptied map would delete live habits without a
    written archival note — the silent-zero genome class. This is the
    codebase's established philosophy (cf. sleep.py's isolation
    assertions; ``load_records`` raises on a missing section the engine
    always writes). The rejected alternative was a degraded flag that
    suppresses stale-key deletion: it would fork every downstream path
    (reconcile, save, the pending-slot fold) and let the mind run on
    fabricated continuity — a phantom-history hazard. A present-but-
    unreadable sidecar, with writes now atomic, is anomalous enough to
    halt at the next transaction (``_restore`` rebuilds the tracker from
    disk, so the raise surfaces before any reconcile runs) instead of
    quietly rebuilding the self from nothing.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"window": [], "formed": {}, "archived": []}
        self._pending: dict | None = None
        if self.path.exists():
            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ValueError(
                    f"habits sidecar {self.path} exists but cannot be read "
                    f"({exc}); refusing to run on fabricated continuity"
                ) from exc
            try:
                loaded = json.loads(raw)
            except ValueError as exc:
                raise ValueError(
                    f"habits sidecar {self.path} is not parseable JSON "
                    f"({exc}); refusing to run on fabricated continuity"
                ) from exc
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"habits sidecar {self.path} must hold a JSON object, "
                    f"got {type(loaded).__name__}; refusing to run on "
                    f"fabricated continuity")
            window = loaded.get("window")
            if isinstance(window, list):
                self.data["window"] = [
                    w for w in window if self._valid_window_row(w)
                ][-FORMATION_WINDOW:]
            self.data["formed"] = self._validated_formed(loaded.get("formed"))
            archived = loaded.get("archived")
            if isinstance(archived, list):
                self.data["archived"] = [
                    a for a in archived if isinstance(a, dict)
                ][-ARCHIVE_CAP:]

    @staticmethod
    def _valid_window_row(row) -> bool:
        """Lenient-but-sound check for one window row.

        A dropped row only misses a future formation — the safe
        direction — but a *kept* row is indexed by ``observe_tick()`` on
        every waking tick (``e["trigger"]``, ``e["delta"]`` ...), so a
        kept row must carry every field observe_tick reads, in the type
        it reads it as. A row missing a field or holding a wrong-typed
        one is dropped here rather than kept to crash every tick.
        """
        if not isinstance(row, dict):
            return False
        tick = row.get("tick")
        if not isinstance(tick, int) or isinstance(tick, bool):
            return False
        for field in ("trigger", "action", "dominant_need"):
            value = row.get(field)
            if not (isinstance(value, str) and value):
                return False
        return _finite_number(row.get("delta"))

    @staticmethod
    def _validated_formed(formed) -> dict:
        """Strict-load the formed map; any malformed entry raises.

        The window list filters malformed rows leniently at load (a
        dropped row only misses a future formation — the safe
        direction), but ``formed`` is strict: it is the ground truth
        behind ``reconcile_state_habits``' stale-key deletion, and a
        silently filtered entry there would delete the live habit with
        no written note.

        Shape checks are not enough: a parseable-but-out-of-domain
        entry (strength above the growth cap, a bool strength, negative
        ticks, a key that names a different habit than its meta) would
        reconcile unclamped into ``state.habits``, where the frozen
        engine's max-strength ``_matching_habit`` rule could let a
        phantom formed habit outrank an authored identity habit. Every
        numeric field therefore rejects bools (``True`` is an int
        subclass), strengths are confined to the tracker's written
        domain ``[REMOVAL_FLOOR, GROWTH_CAP]``, peaks to
        ``[strength, GROWTH_CAP]``, ticks are non-negative non-bool ints
        with ``last_tick >= created_tick``, and the key must name
        exactly ``formed:<trigger>:<action>``.
        """
        if formed is None:
            return {}
        if not isinstance(formed, dict):
            raise ValueError(
                f"habits sidecar 'formed' must be an object, got "
                f"{type(formed).__name__}; refusing to run on fabricated "
                f"continuity")
        out = {}
        for key, meta in formed.items():
            problem = None
            if not (isinstance(key, str) and key.startswith(FORMED_PREFIX)):
                problem = f"key {key!r} is not a {FORMED_PREFIX!r} string"
            elif not isinstance(meta, dict):
                problem = f"entry {key!r} is not an object"
            elif not (isinstance(meta.get("trigger"), str)
                      and meta["trigger"]):
                problem = f"entry {key!r} has no string trigger"
            else:
                try:
                    Action(meta.get("action"))
                except (ValueError, TypeError):
                    problem = (f"entry {key!r} has no real Action "
                               f"(got {meta.get('action')!r})")
            if problem is None:
                expected = (f"{FORMED_PREFIX}{meta['trigger']}:"
                            f"{meta['action']}")
                if key != expected:
                    problem = (f"entry {key!r} key does not match its "
                               f"trigger/action (expected {expected!r})")
            if problem is None:
                strength = meta.get("strength")
                if not _finite_number(strength):
                    problem = (f"entry {key!r} has non-finite strength "
                               f"{strength!r}")
                elif not (REMOVAL_FLOOR <= strength <= GROWTH_CAP):
                    problem = (f"entry {key!r} strength {strength!r} "
                               f"outside tracker domain "
                               f"[{REMOVAL_FLOOR}, {GROWTH_CAP}]")
            if problem is None:
                peak = meta.get("peak")
                strength = meta.get("strength")
                if not _finite_number(peak):
                    problem = (f"entry {key!r} has non-finite peak "
                               f"{peak!r}")
                elif not (strength <= peak <= GROWTH_CAP):
                    problem = (f"entry {key!r} peak {peak!r} outside "
                               f"[{strength!r}, {GROWTH_CAP}]")
            if problem is None:
                for field in ("created_tick", "cofires", "last_tick"):
                    value = meta.get(field)
                    if not isinstance(value, int) or isinstance(value, bool):
                        problem = (f"entry {key!r} has non-int "
                                   f"{field} {value!r}")
                        break
                    if field != "cofires" and value < 0:
                        problem = (f"entry {key!r} has negative "
                                   f"{field} {value!r}")
                        break
            if problem is None:
                created = meta["created_tick"]
                last = meta["last_tick"]
                if last < created:
                    problem = (f"entry {key!r} last_tick {last} "
                               f"< created_tick {created}")
            if problem is not None:
                raise ValueError(
                    f"habits sidecar 'formed' malformed: {problem}; "
                    f"refusing to run on fabricated continuity")
            out[key] = meta
        return out

    # -- observation slot (written by the select_conduct wrapper) ---------

    def note_tick(self, tick: int, trigger: str, action: str, dominant_need: str) -> None:
        """Stage one intention selection. Single slot: the latest note wins.

        Called by the ``select_conduct`` observer during the heartbeat;
        consumed-or-dropped by ``take_pending()`` in ``cli._run_tick``.
        """
        self._pending = {
            "tick": int(tick),
            "trigger": str(trigger),
            "action": str(action),
            "dominant_need": str(dominant_need),
        }

    def take_pending(self) -> dict | None:
        """Return the staged note and clear the slot (consume-or-drop).

        A stale note — e.g. from a direct ``select_conduct`` call outside
        a tick — is discarded here, never applied to a later tick. A
        missed observation is safe; a misattributed one is the hazard.
        """
        pending, self._pending = self._pending, None
        return pending

    # -- formation ---------------------------------------------------------

    def observe_tick(self, *, tick: int, trigger: str, action: str,
                     dominant_need: str, need_deltas: dict) -> tuple[bool, bool]:
        """Fold one waking tick into the window; maybe crystallize/grow/decay/remove.

        Returns ``(sidecar_changed, state_changed)``. The window always
        advances on an observed tick, so ``sidecar_changed`` is True
        whenever the inputs are well-formed; ``state_changed`` is True
        only when ``state.habits`` needs reconciling (crystallization,
        growth, decay, or removal touched a formed habit).
        """
        if not trigger or not action:
            return False, False
        before = {k: v["strength"] for k, v in self.data["formed"].items()}

        delta_key = trigger if trigger in need_deltas else dominant_need
        delta = round(float(need_deltas.get(delta_key, 0.0)), 6)
        self.data["window"].append({
            "tick": int(tick),
            "trigger": str(trigger),
            "action": str(action),
            "dominant_need": str(dominant_need),
            "delta": delta,
        })
        self.data["window"] = self.data["window"][-FORMATION_WINDOW:]

        key = f"{FORMED_PREFIX}{trigger}:{action}"
        pair_entries = [e for e in self.data["window"]
                        if e["trigger"] == trigger and e["action"] == action]
        if key in self.data["formed"]:
            # Growth: +0.02 per subsequent co-fire, hard cap 0.70.
            meta = self.data["formed"][key]
            if meta["strength"] < GROWTH_CAP:
                meta["strength"] = round(
                    min(GROWTH_CAP, meta["strength"] + GROWTH_STEP), 6)
                meta["peak"] = max(meta["peak"], meta["strength"])
            meta["cofires"] += 1
            meta["last_tick"] = int(tick)
        elif len(pair_entries) >= FORMATION_COFIRES:
            mean_delta = sum(e["delta"] for e in pair_entries) / len(pair_entries)
            if mean_delta <= 0:
                # Crystallize: a real Habit, born below the fire threshold.
                self.data["formed"][key] = {
                    "trigger": str(trigger),
                    "action": str(action),
                    "strength": CRYSTALLIZE_STRENGTH,
                    "peak": CRYSTALLIZE_STRENGTH,
                    "created_tick": int(tick),
                    "cofires": len(pair_entries),
                    "last_tick": int(tick),
                }

        # Disuse decay: the trigger drove this tick but a different action
        # was chosen. A habit that never gets used in its own context fades.
        for fkey, meta in list(self.data["formed"].items()):
            if meta["trigger"] == trigger and meta["action"] != action:
                meta["strength"] = round(meta["strength"] - DECAY_STEP, 6)
                if meta["strength"] < REMOVAL_FLOOR:
                    self.data["archived"].append({
                        "key": fkey,
                        "peak": meta["peak"],
                        "lifespan_ticks": int(tick) - meta["created_tick"],
                        "reason": REMOVAL_REASON,
                        "archived_tick": int(tick),
                    })
                    self.data["archived"] = self.data["archived"][-ARCHIVE_CAP:]
                    del self.data["formed"][fkey]

        after = {k: v["strength"] for k, v in self.data["formed"].items()}
        state_changed = (set(before) != set(after) or
                         any(s != after.get(k) for k, s in before.items()))
        return True, state_changed

    # -- state projection ---------------------------------------------------

    def reconcile_state_habits(self, habits: dict) -> None:
        """Project the sidecar's formed habits into a live ``state.habits`` dict.

        Only the ``formed:`` namespace is touched — authored habits are
        never modified. Strength comes from the sidecar (the tracker's
        domain); ``last_used_tick`` is preserved from the live entry when
        one exists, because the engine writes it when a habit fires.
        Stale ``formed:`` keys with no sidecar record are deleted
        (self-healing: no phantom habits).
        """
        for key, meta in self.data["formed"].items():
            existing = habits.get(key)
            if existing is None:
                habits[key] = Habit(
                    key=key,
                    trigger=meta["trigger"],
                    action=Action(meta["action"]),
                    strength=meta["strength"],
                    cooldown=2,
                )
            else:
                existing.strength = meta["strength"]
        for key in [k for k in habits
                    if k.startswith(FORMED_PREFIX) and k not in self.data["formed"]]:
            del habits[key]

    # -- persistence --------------------------------------------------------

    # Note: there is deliberately no reset() method. On reseed
    # (mind init --force) cli.py unlinks the sidecar file directly, like
    # the familiarity and ambivalence sidecars — a reseed starts with no
    # formation history at all, and the constructed tracker on a missing
    # file is the clean empty state.

    def save(self) -> None:
        """Atomically persist the sidecar (temp file + os.replace + fsync).

        A crash mid-write leaves either the complete old body or the
        complete new body — never a truncated shell. The temp file lives
        in the same directory so the replace is an atomic rename.
        """
        ordered = {
            "window": self.data["window"],
            "formed": {k: self.data["formed"][k]
                       for k in sorted(self.data["formed"])},
            "archived": self.data["archived"],
        }
        body = json.dumps(ordered, ensure_ascii=False, indent=1)
        tmp = self.path.parent / (self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


def install_habit_observer(subject) -> None:
    """Wrap the subject's engine ``select_conduct`` with a pure observer.

    The wrapper is installed on the composed engine *instance* — the
    frozen class and its source are never modified. It delegates to the
    pristine bound method FIRST, then notes ``(tick, trigger, action,
    dominant_need)`` on the tracker's pending slot, computed from the
    exact triage lists the engine used at selection time. It never alters
    the returned action or any engine state.

    Idempotent: safe to call again after ``_restore`` swaps in a fresh
    engine instance (every ``_transaction`` does), and a no-op if the
    current instance is already observed. ``select_conduct`` is called
    exactly once per waking tick and never on the dream path; the
    ``_dreaming`` guard is belt-and-braces.
    """
    engine = subject.engine
    original = engine.select_conduct
    if getattr(original, "_habits_wrapped", False):
        return

    @functools.wraps(original)
    def _observed_select_conduct(event=None):
        action = original(event)
        try:
            if getattr(subject, "_dreaming", False):
                return action
            # Mirror the frozen engine's channel-rule bypass: for these
            # event kinds the engine returned without running the channel
            # rule, so no trigger drove the tick — noting one would
            # fabricate a (trigger, action) pair the engine never co-fired.
            kind = event.kind if event is not None else "temporal"
            if kind in CHANNEL_RULE_BYPASS_KINDS:
                return action
            tracker = getattr(subject.workspace, "habits_tracker", None)
            if tracker is not None:
                trigger, dominant_need = resolve_trigger(
                    engine._triage_needs(), engine._triage_pressures())
                tracker.note_tick(engine.state.tick, trigger,
                                  action.value, dominant_need)
        except Exception:
            # Observation is strictly subordinate to the tick: a note
            # computation failure must never break a tick the pristine
            # engine would have completed. A missed note is always the
            # safe direction.
            pass
        return action

    _observed_select_conduct._habits_wrapped = True
    engine.select_conduct = _observed_select_conduct
