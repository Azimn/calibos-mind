"""Loss ledger — a record of documented losses (Domain 31, Vulnerability
and Stakes).

Domain 31 (Vulnerability and stakes), 2026-11-24: the mind's only losses
are passive decay (habit disuse, salience sinking), and the one genuine
bypass loss on record — the hunger habit falling 0.70 to 0.45 across
~524 silent ticks — exists only as a number in ``mind drift``, with no
event marking when it lost bypass or what that cost. This instrument
makes loss an event in the mind's history and gives future Article 31
verdicts evidence to score.

This is measurement, not a new behavior: it observes losses, it never
steers them. No conduct change, no gate change, no cartridge change, no
store migration (new sidecar only). It does not manufacture stakes: it
documents the losses that already happen, and surfaces what is currently
at risk of being lost. A breakage-consequence design (what a broken
commitment should cost in later organization) is deliberately out of
scope: it changes conduct and needs Jay's decision on the cost
semantics.

Design rules (mirroring goals.py / followthrough.py):
- Sidecar ``stakes.json`` (next to ``followthrough.json``)::
    ``{"losses": [row, ...],
       "seen_habits": {"<formed-habit-key>": <last observed strength>}}``
  A loss row is one of:
  - ``{"kind": "habit-bypass", "habit": "<formed:...>", "tick": <int>,
    "before": <float>, "after": <float>}`` — a ``formed:`` habit whose
    strength was at or above the bypass threshold (0.65, the threshold
    at which a formed habit fires within-channel bypassing
    deliberation) at the last observation and is now below it.
  - ``{"kind": "broken-commitment", "commitment_id": "<id>",
    "description": "...", "tick": <resolved tick>,
    "outcome": "..."}`` — a commitment resolving to status ``broken``.
  ``seen_habits`` is the per-habit baseline map: the last observed
  strength of every live ``formed:`` habit. The latch is per-episode,
  not per-habit — it IS the stored last strength: a habit that
  recovers to >= 0.65 and later decays below it again mints a second
  row, because the stored baseline went back above the line. The map
  is pruned to habits still present in engine state, so a removed
  habit leaves no stale baseline behind.
- The tracker is fed ONLY from the waking-tick path (``cli._run_tick``),
  after the habits reconcile and the dormancy demotion, so the
  strengths it reads are this tick's reconciled ones. Read-only
  commands (``drift``, ``status``, ``review``, ``inbox``, ``stakes``,
  ``goals``, ``followthrough``, ``predictions``) and dream ticks NEVER
  write the sidecar; ``cmd_init --force`` wipes it.
- Habit bypass loss: a habit already below 0.65 when first seen mints
  nothing (no retroactive fabrication: the ledger never backfills
  history) — only the baseline is recorded. A habit at exactly 0.65
  stays above the loss line (the engine fires at >= 0.65, so 0.65 is
  still bypass, not loss); a crossing needs the previous observation
  >= 0.65 and the current one < 0.65. The row's tick is the
  observation tick; before/after are the exact stored strengths.
- Broken commitment: any commitment with status ``broken`` whose id
  has no loss row yet mints exactly one row — including one first seen
  already broken. That is not backfill: the engine stamps the
  resolution itself (``resolved_tick`` and ``outcome`` are engine
  writes on ``resolve_commitment``), so the row names the engine's own
  resolving tick and outcome text, never an invented one. The latch
  is the row set itself: the same breakage observed twice mints one
  row. Kept and released resolutions mint nothing. A commitment that
  resolves kept or released can never later become broken through the
  engine, so no row is ever owed for it.
- ``report()`` is a pure read of live state + sidecar: formed habits
  at or above 0.65 (bypass active: what disuse would cost), open
  commitments (open/overdue) with due tick where present, and the
  losses to date, most recent first, capped at LOSSES_SHOWN. Counts
  of losses by kind come from the sidecar. Nothing is written:
  calling this twice returns equal dicts and leaves the sidecar
  byte-identical, and on a fresh store it reports empty state and
  mints no sidecar.
- Determinism: identical tick sequences yield byte-identical sidecars.
  No clock, no RNG, no model calls. (Loss rows carry engine-stamped
  ticks and store strengths, both deterministic inputs.)
- Corrupt sidecar fails loud: invalid JSON, a non-dict top level, a
  malformed ``losses``/``seen_habits`` section, a row whose fields
  contradict the crossing invariant (before >= 0.65 > after), a
  duplicate row for one breakage, or duplicate (habit, tick) habit
  rows raises ValueError at construction. Never silent-delete, never
  silent-zero. (``cmd_init --force`` wipes the sidecar before
  construction, so a corrupt file can never brick a reseed.)
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

# Imported, not redefined: the bypass-strength definition must stay a
# single source of truth with counterfactual._fired_habit and the
# habits/dormancy trackers (regression genome: shadowing definitions).
from .counterfactual import HABIT_FIRE_STRENGTH
# The formed-habit namespace; authored habits are never the ledger's
# domain (their bypass loss is the dormancy tracker's demotion).
from .habits import FORMED_PREFIX
# The only commitment statuses the frozen engine produces.
from .goals import COMMITMENT_STATUSES

# Losses shown in the report, most recent first.
LOSSES_SHOWN = 20

HABIT_BYPASS = "habit-bypass"
BROKEN_COMMITMENT = "broken-commitment"
LOSS_KINDS = (HABIT_BYPASS, BROKEN_COMMITMENT)

# Statuses counting as an open commitment (engine summary() definition).
OPEN_COMMITMENT_STATUSES = ("open", "overdue")


def _is_int(value) -> bool:
    """A genuine int count: bools (an int subclass) never count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    """A genuine finite number: bools are not numbers in this domain."""
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value))


def _engine_habits(subject):
    """The subject's live formed-habit population, or a loud failure.

    A missing engine state is not an empty habit population — it is a
    subject this instrument cannot read, and reporting zero losses
    would be the silent-zero genome bug.
    """
    state = getattr(getattr(subject, "engine", None), "state", None)
    if state is None:
        raise ValueError(
            "stakes instrument needs a subject with engine state")
    habits = getattr(state, "habits", None)
    if not isinstance(habits, dict):
        raise ValueError(
            "stakes instrument needs engine state.habits to be a dict")
    return habits


def _continuity_state(subject):
    """The subject's continuity state, or a loud failure.

    Mirrors goals._continuity_state: a missing state is not an empty
    commitment population.
    """
    state = getattr(getattr(subject, "continuity", None), "state", None)
    if state is None:
        raise ValueError(
            "stakes instrument needs a subject with continuity state")
    return state


def _habit_strength(habit, key: str) -> float:
    """One habit's live strength, validated: a finite number in [0, 1].

    The engine clamps strengths into [0, 1]; a bool, a NaN, or an
    out-of-range value can only come from a hand-built or corrupted
    store, and silently comparing it against the threshold would be
    the exact quiet-misreading failure.
    """
    strength = getattr(habit, "strength", None)
    if not _is_number(strength) or not 0.0 <= strength <= 1.0:
        raise ValueError(
            f"stakes instrument: habit {key!r} has malformed strength "
            f"{strength!r} (expected a finite number in [0, 1])")
    return float(strength)


class StakesTracker:
    """Loss-ledger sidecar (``stakes.json``, local-only).

    ``data`` is ``{"losses": [...], "seen_habits": {...}}``. The
    constructor only reads the sidecar; rows move exclusively via
    ``observe()`` + ``save()`` on the waking-tick path
    (``cli._run_tick``).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"losses": [], "seen_habits": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(
                    f"corrupt stakes sidecar {self.path}: "
                    f"invalid JSON ({exc})"
                ) from exc
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"corrupt stakes sidecar {self.path}: top level is "
                    f"not an object")
            losses = loaded.get("losses", [])
            if not isinstance(losses, list):
                raise ValueError(
                    f"corrupt stakes sidecar {self.path}: 'losses' is "
                    f"not a list")
            for row in losses:
                self._validate_loss_row(row)
            self._validate_no_duplicate_rows(losses)
            seen = loaded.get("seen_habits", {})
            if not isinstance(seen, dict):
                raise ValueError(
                    f"corrupt stakes sidecar {self.path}: 'seen_habits' "
                    f"is not an object")
            for key, strength in seen.items():
                if (not isinstance(key, str)
                        or not key.startswith(FORMED_PREFIX)):
                    raise ValueError(
                        f"corrupt stakes sidecar {self.path}: "
                        f"seen_habits key {key!r} is not a formed-habit "
                        f"key")
                if not _is_number(strength) or not 0.0 <= strength <= 1.0:
                    raise ValueError(
                        f"corrupt stakes sidecar {self.path}: "
                        f"seen_habits[{key!r}] is not a finite number in "
                        f"[0, 1]: {strength!r}")
            self.data["losses"] = [dict(row) for row in losses]
            self.data["seen_habits"] = {key: float(strength)
                                        for key, strength in seen.items()}

    def _validate_loss_row(self, row) -> None:
        """One loss row: exact shape and crossing invariant, or loud."""
        path = self.path

        def bad(why: str):
            raise ValueError(
                f"corrupt stakes sidecar {path}: loss row {row!r} {why}")

        if not isinstance(row, dict):
            bad("is not an object")
        kind = row.get("kind")
        if kind not in LOSS_KINDS:
            bad(f"has unknown kind {kind!r}")
        if not _is_int(row.get("tick")) or row["tick"] < 0:
            bad("has no non-negative integer 'tick'")
        if kind == HABIT_BYPASS:
            habit = row.get("habit")
            if (not isinstance(habit, str)
                    or not habit.startswith(FORMED_PREFIX)):
                bad("has no formed-habit 'habit'")
            before = row.get("before")
            after = row.get("after")
            if not _is_number(before) or not 0.0 <= before <= 1.0:
                bad("has no finite 'before' strength in [0, 1]")
            if not _is_number(after) or not 0.0 <= after <= 1.0:
                bad("has no finite 'after' strength in [0, 1]")
            # The crossing invariant observe() always produces: the
            # previous observation was at or above the bypass
            # threshold and the current one below it. A row violating
            # it is a tampered or half-written sidecar, not data.
            if before < HABIT_FIRE_STRENGTH:
                bad(f"'before' {before!r} is below the bypass threshold "
                    f"{HABIT_FIRE_STRENGTH} — no crossing happened")
            if after >= HABIT_FIRE_STRENGTH:
                bad(f"'after' {after!r} is at or above the bypass "
                    f"threshold {HABIT_FIRE_STRENGTH} — no crossing "
                    f"happened")
        else:  # BROKEN_COMMITMENT
            cid = row.get("commitment_id")
            if not isinstance(cid, str) or not cid:
                bad("has no non-empty string 'commitment_id'")
            if not isinstance(row.get("description"), str):
                bad("has no string 'description'")
            if not isinstance(row.get("outcome"), str):
                bad("has no string 'outcome'")

    def _validate_no_duplicate_rows(self, losses: list) -> None:
        """One row per breakage, one row per (habit, tick) crossing.

        observe() latches broken ids and observes each tick once, so
        a duplicate is a tampered sidecar — fail closed rather than
        silently dedupe. Habit rows must also arrive in
        non-decreasing tick order (observations happen in tick order);
        commitment rows carry engine-stamped resolving ticks and may
        legitimately arrive out of order, so no ordering check applies
        to them.
        """
        broken_ids = set()
        habit_ticks = set()
        prev_habit_tick = None
        for row in losses:
            if not isinstance(row, dict):
                continue  # _validate_loss_row already raised for this.
            if row.get("kind") == BROKEN_COMMITMENT:
                cid = row.get("commitment_id")
                if cid in broken_ids:
                    raise ValueError(
                        f"corrupt stakes sidecar {self.path}: duplicate "
                        f"loss row for broken commitment {cid!r}")
                broken_ids.add(cid)
            elif row.get("kind") == HABIT_BYPASS:
                tick = row.get("tick")
                key = (row.get("habit"), tick)
                if key in habit_ticks:
                    raise ValueError(
                        f"corrupt stakes sidecar {self.path}: duplicate "
                        f"loss row for habit {key[0]!r} at tick {tick}")
                habit_ticks.add(key)
                tick = row.get("tick")
                if (prev_habit_tick is not None
                        and _is_int(tick) and tick < prev_habit_tick):
                    raise ValueError(
                        f"corrupt stakes sidecar {self.path}: habit "
                        f"loss rows out of tick order at {row!r} "
                        f"(previous habit tick {prev_habit_tick})")
                if _is_int(tick):
                    prev_habit_tick = tick

    @property
    def losses(self) -> list:
        """A copy of the loss rows, in mint order."""
        return [dict(row) for row in self.data["losses"]]

    @property
    def seen_habits(self) -> dict:
        """A copy of the per-habit baseline map."""
        return dict(self.data["seen_habits"])

    @property
    def counts(self) -> dict:
        """Loss counts by kind, plus the total."""
        by_kind = {kind: 0 for kind in LOSS_KINDS}
        for row in self.data["losses"]:
            by_kind[row["kind"]] += 1
        return {**by_kind, "total": len(self.data["losses"])}

    def _broken_ids(self) -> set:
        """Commitment ids with a breakage row already minted."""
        return {row["commitment_id"] for row in self.data["losses"]
                if row["kind"] == BROKEN_COMMITMENT}

    def observe(self, subject, tick: int) -> bool:
        """Record one waking tick's losses.

        Reads the live formed-habit strengths and commitment
        resolutions, mints at most one row per crossing episode and
        one row per breakage, and refreshes the baseline map.
        Returns True when the sidecar moved (a row minted, a baseline
        recorded or refreshed, or a stale baseline pruned) — the
        caller saves only then (no-op write discipline).
        """
        if not _is_int(tick):
            raise ValueError(f"observe() needs an integer tick, got {tick!r}")
        if tick < 0:
            raise ValueError(
                f"observe() needs a non-negative tick, got {tick!r}")
        changed = False
        # Habit bypass losses: compare each live formed habit against
        # its stored baseline, in sorted key order for determinism.
        habits = _engine_habits(subject)
        current: dict[str, float] = {}
        for key in sorted(habits):
            if not key.startswith(FORMED_PREFIX):
                continue
            current[key] = _habit_strength(habits[key], key)
        for key, strength in current.items():
            prev = self.data["seen_habits"].get(key)
            if prev is None:
                # First sight: baseline only, never a row — the ledger
                # never backfills history it did not observe.
                self.data["seen_habits"][key] = strength
                changed = True
            else:
                if (prev >= HABIT_FIRE_STRENGTH
                        and strength < HABIT_FIRE_STRENGTH):
                    self.data["losses"].append({
                        "kind": HABIT_BYPASS, "habit": key, "tick": tick,
                        "before": prev, "after": strength})
                    changed = True
                if self.data["seen_habits"][key] != strength:
                    self.data["seen_habits"][key] = strength
                    changed = True
        # Prune baselines for habits no longer in engine state (a
        # removed habit leaves no stale baseline for a later
        # re-formation to inherit).
        for key in list(self.data["seen_habits"]):
            if key not in current:
                del self.data["seen_habits"][key]
                changed = True
        # Broken commitments: one row per breakage, latched by id.
        # The row names the engine's own resolving tick and outcome —
        # a first-seen-broken commitment is a store-documented event,
        # not backfill.
        state = _continuity_state(subject)
        commitments = state.commitments
        if not isinstance(commitments, dict):
            raise ValueError(
                "stakes instrument needs continuity commitments to be "
                "a dict")
        broken_ids = self._broken_ids()
        for cid in sorted(commitments):
            item = commitments[cid]
            status = getattr(item, "status", None)
            if status not in COMMITMENT_STATUSES:
                raise ValueError(
                    f"stakes instrument: commitment {cid!r} has unknown "
                    f"status {status!r}")
            if status != "broken" or cid in broken_ids:
                continue
            resolved_tick = getattr(item, "resolved_tick", None)
            if not _is_int(resolved_tick) or resolved_tick < 0:
                raise ValueError(
                    f"stakes instrument: broken commitment {cid!r} has "
                    f"no engine-stamped resolving tick "
                    f"({resolved_tick!r}) — the engine always writes one")
            outcome = getattr(item, "outcome", None)
            if not isinstance(outcome, str):
                raise ValueError(
                    f"stakes instrument: broken commitment {cid!r} has "
                    f"no outcome text ({outcome!r}) — the engine always "
                    f"writes one")
            description = getattr(item, "description", None)
            if not isinstance(description, str):
                raise ValueError(
                    f"stakes instrument: broken commitment {cid!r} has "
                    f"no string description ({description!r})")
            self.data["losses"].append({
                "kind": BROKEN_COMMITMENT, "commitment_id": cid,
                "description": description, "tick": resolved_tick,
                "outcome": outcome})
            broken_ids.add(cid)
            changed = True
        return changed

    def save(self) -> None:
        """Atomically persist the sidecar (temp file + os.replace + fsync).

        A crash mid-write leaves either the complete old body or the
        complete new body — never a truncated shell. The temp file lives
        in the same directory so the replace is an atomic rename. The
        body is deterministic: losses in mint order, baselines sorted
        by habit key, fixed form.
        """
        body = json.dumps(
            {"losses": [{
                "after": row["after"],
                "before": row["before"],
                "habit": row["habit"],
                "kind": row["kind"],
                "tick": row["tick"],
            } if row["kind"] == HABIT_BYPASS else {
                "commitment_id": row["commitment_id"],
                "description": row["description"],
                "kind": row["kind"],
                "outcome": row["outcome"],
                "tick": row["tick"],
            } for row in self.data["losses"]],
             "seen_habits": {key: self.data["seen_habits"][key]
                             for key in sorted(self.data["seen_habits"])}},
            ensure_ascii=False, indent=1, sort_keys=True)
        tmp = self.path.parent / (self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


def report(subject, sidecar) -> dict:
    """Pure read of live state + sidecar: the Domain 31 report.

    ``sidecar`` is a ``StakesTracker`` or a path to ``stakes.json``.
    Current stakes are derived live at call time, never stored:
    formed habits at or above the bypass threshold, and open
    commitments (open/overdue) with due tick where present. Losses
    come from the sidecar, most recent first, capped at
    LOSSES_SHOWN. Nothing is written: calling this twice returns
    equal dicts and leaves the sidecar byte-identical, and a fresh
    store reports empty state without minting a sidecar.

    Returns ``{"tick", "at_risk_habits": [{"habit", "strength"}],
    "open_commitments": [{"id", "description", "status", "due_tick",
    "created_tick"}], "losses": [...], "losses_total", "losses_shown",
    "losses_limit", "counts": {"habit-bypass", "broken-commitment",
    "total"}, "observed_habits"}``. Losses are most recent first
    (highest tick first; ties keep later-minted first).
    """
    tracker = (sidecar if isinstance(sidecar, StakesTracker)
               else StakesTracker(sidecar))
    tick = subject.engine.state.tick
    if not _is_int(tick) or tick < 0:
        raise ValueError(
            f"stakes instrument: engine tick must be a non-negative "
            f"integer, got {tick!r}")
    habits = _engine_habits(subject)
    at_risk = []
    for key in sorted(habits):
        if not key.startswith(FORMED_PREFIX):
            continue
        strength = _habit_strength(habits[key], key)
        if strength >= HABIT_FIRE_STRENGTH:
            at_risk.append({"habit": key, "strength": strength})
    state = _continuity_state(subject)
    commitments = state.commitments
    if not isinstance(commitments, dict):
        raise ValueError(
            "stakes instrument needs continuity commitments to be a dict")
    open_commitments = []
    for cid in sorted(commitments):
        item = commitments[cid]
        status = getattr(item, "status", None)
        if status not in COMMITMENT_STATUSES:
            raise ValueError(
                f"stakes instrument: commitment {cid!r} has unknown "
                f"status {status!r}")
        if status not in OPEN_COMMITMENT_STATUSES:
            continue
        due_tick = getattr(item, "due_tick", None)
        if due_tick is not None and (not _is_int(due_tick)
                                     or due_tick < 0):
            raise ValueError(
                f"stakes instrument: commitment {cid!r} has malformed "
                f"due_tick {due_tick!r}")
        created_tick = getattr(item, "created_tick", None)
        if not _is_int(created_tick) or created_tick < 0:
            raise ValueError(
                f"stakes instrument: commitment {cid!r} has malformed "
                f"created_tick {created_tick!r}")
        description = getattr(item, "description", None)
        if not isinstance(description, str):
            raise ValueError(
                f"stakes instrument: commitment {cid!r} has no string "
                f"description ({description!r})")
        open_commitments.append({
            "id": cid, "description": description, "status": status,
            "due_tick": due_tick, "created_tick": created_tick})
    losses = tracker.losses
    ordered = [row for _, row in sorted(
        enumerate(losses), key=lambda p: (p[1]["tick"], p[0]),
        reverse=True)]
    return {
        "tick": tick,
        "at_risk_habits": at_risk,
        "open_commitments": open_commitments,
        "losses": ordered[:LOSSES_SHOWN],
        "losses_total": len(losses),
        "losses_shown": min(len(losses), LOSSES_SHOWN),
        "losses_limit": LOSSES_SHOWN,
        "counts": dict(tracker.counts),
        "observed_habits": len(tracker.seen_habits),
    }
