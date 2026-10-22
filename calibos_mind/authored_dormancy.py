"""Authored-habit dormancy demotion — pruning without theater.

Domain 27 (Lack of developmental history), 2026-10-21: authored habits
enter at their toml strength as an *authored baseline*, and the frozen
engine fires any habit inside the already-chosen intention channel when
``habit.strength >= 0.65`` — forever. ``verify_before_claiming`` (0.68)
has never fired in 730 live ticks, yet keeps the bypass privilege on its
next trigger with zero lived grounding. Installed automaticity without
practice is the "born fully formed" symptom with a live behavioral
consequence.

Mechanism (pruning analog — childhood reflexes persist as tendencies but
lose automaticity without practice):

- On waking ticks only, for each authored habit (keys and baselines from
  the cartridge ``[habits]`` entries — the toml is the single source of
  truth): if ``tick - last_fired_tick > DORMANCY_TICKS`` and live
  strength >= the engine bypass threshold, demote live strength to
  exactly 0.64 (one notch below the threshold), set the ``demoted`` flag,
  and mint exactly one temporal record with a written reason naming the
  habit key, the dormancy span in ticks, and the authored baseline. (The
  demotion edge: demote once more than 300 ticks have elapsed since the
  last firing tick — a firing at tick 250 leaves tick 550 undemoted;
  demotion lands at tick 551.)
- ``last_fired_tick`` is proven the same way the stale-note tracker
  proves a bypass firing (see habits.py): the engine's ``last_used_tick``
  stamp on the live habit entry equals the current tick. The clock only
  matters pre-demotion: a proven firing resets it — silently (the
  demotion record already explains).
- Demotion is a one-way ratchet (pruning analog — synaptic atrophy is not
  reversed). The frozen engine stamps ``last_used_tick`` only inside the
  bypass branch (strength >= 0.65), so a habit demoted to 0.64 can never
  produce the stamp that would restore it; the restore path is not
  implemented, by design — dead code that can never execute in vivo is a
  liability. The ``demoted`` flag latches permanently: no code path may
  restore a demoted habit's strength, and the demotion note never
  promises restoration ("loses its automatic edge", never "for now").
- Floor: strength never demoted below 0.64. Identity presence is
  preserved — the habit remains deliberation-eligible; it only loses the
  bypass privilege.

What this module never does:

- The ``formed:`` namespace is never touched — the existing
  HabitFormationTracker owns that namespace (its disuse/decay path); no
  double jeopardy. Authored keys are filtered at construction, and both
  ``observe_tick`` and ``reconcile_state_habits`` skip any key starting
  with the formed prefix.
- Read-only commands and dream ticks never demote and never mint: the
  tracker only runs from ``cli._run_tick`` (the waking-tick path); the
  constructor only reads the sidecar.
- ``mind init --force`` wipes the sidecar (see cli.py), like the other
  tracker sidecars.

Crash discipline mirrors the stale-note write-ahead: the demotion sets
the ``owed`` mark on the sidecar entry and the sidecar saves BEFORE the
mint transaction commits; ``mint_demotion_note`` is idempotent on
(habit_key, demoted_tick); ``ack_demotion_notes`` clears the mark only
after the mint commits. A kill between mint and ack re-mints nothing
(the idempotent mint returns the existing record's id) and the next tick
recovers the owed episode.

Determinism: no wall clock, no RNG; sorted keys, rounded floats.
Identical tick sequences produce byte-identical sidecars.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

# Imported, not redefined: the bypass-strength definition must stay a
# single source of truth with the frozen engine's branch (engine.py:
# ``habit.strength >= 0.65``) and counterfactual._fired_habit (regression
# genome: shadowing definitions).
from .counterfactual import HABIT_FIRE_STRENGTH
from .habits import FORMED_PREFIX

# Ticks of no firing after which an authored habit loses the bypass
# privilege. The live store's never-fired authored habits sit at 700+
# dormant ticks; the bound is deliberately generous so only genuinely
# unpracticed tendencies demote.
DORMANCY_TICKS = 300
# Demoted strength: one notch below the engine's 0.65 bypass threshold.
# The habit stays deliberation-eligible; only the bypass is gated.
DEMOTED_STRENGTH = 0.64
# The engine stamps last_used_tick with this when a habit entry has never
# fired (digital_subject.models.Habit default). A fresh sidecar seeds
# last_fired_tick from the live stamp, so a never-fired habit starts
# dormant-by-construction rather than by clock accident.
NEVER_FIRED = -10_000


def _finite_number(value) -> bool:
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value))


def _sane_tick(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class AuthoredDormancyTracker:
    """Dormancy demotion for authored habits, backed by a local-only sidecar.

    ``data`` is ``{"habits": {key: entry}}`` where each entry is
    ``{"baseline", "last_fired_tick", "demoted", "demoted_tick", "owed"}``:
    - ``baseline``: the authored strength from the toml (single source of
      truth; re-synced from the cartridge map on every observed tick).
    - ``last_fired_tick``: the last tick the engine's ``last_used_tick``
      stamp proved a bypass firing (seeded from the live stamp on a fresh
      sidecar — a never-fired habit starts at -10000).
    - ``demoted``: whether the bypass privilege is currently demoted.
    - ``demoted_tick``: the tick of the current demotion episode (the
      mint identity), or None.
    - ``owed``: None, or the demotion mint event awaiting its record
      (the write-ahead mark: set before the sidecar save, cleared by
      ``ack_demotion_notes`` after the mint transaction commits).

    The constructor only reads the sidecar. One waking tick folds in via
    ``observe_tick()``; the caller saves only when it reports a change,
    then applies the strength projection through ``reconcile_state_habits``
    inside a subject transaction (the pre-transaction live state is
    discarded by ``_restore`` — the sidecar is the bridge, and the
    firing stamp survives the payload round-trip so the projection
    re-derives identically on the fresh state).

    Load contract — fail loud, never silent zero (same philosophy as
    habits.py): a missing sidecar is a legitimate fresh start; a
    present-but-unparseable or malformed sidecar raises ValueError.
    """

    def __init__(self, path: str | Path, authored: dict):
        self.path = Path(path)
        # The toml is the single source of truth; the formed: namespace
        # is owned by the formation tracker and filtered here even if a
        # pathological cartridge ever named one.
        self.authored = {
            str(k): round(float(v), 6)
            for k, v in dict(authored).items()
            if isinstance(k, str) and k
            and not k.startswith(FORMED_PREFIX)
            and _finite_number(v)
        }
        self.data: dict = {"habits": {}}
        if self.path.exists():
            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ValueError(
                    f"dormancy sidecar {self.path} exists but cannot be "
                    f"read ({exc}); refusing to run on fabricated continuity"
                ) from exc
            try:
                loaded = json.loads(raw)
            except ValueError as exc:
                raise ValueError(
                    f"dormancy sidecar {self.path} is not parseable JSON "
                    f"({exc}); refusing to run on fabricated continuity"
                ) from exc
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"dormancy sidecar {self.path} must hold a JSON object, "
                    f"got {type(loaded).__name__}; refusing to run on "
                    f"fabricated continuity")
            self.data["habits"] = self._validated_habits(
                loaded.get("habits"))

    @staticmethod
    def _validated_habits(raw) -> dict:
        """Strict-load the per-habit map; any malformed entry raises."""
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(
                f"dormancy sidecar 'habits' must be an object, got "
                f"{type(raw).__name__}; refusing to run on fabricated "
                f"continuity")
        out = {}
        for key, entry in raw.items():
            problem = None
            if not (isinstance(key, str) and key
                    and not key.startswith(FORMED_PREFIX)):
                problem = (f"key {key!r} is not an authored habit key "
                           f"(formed: namespace is never tracked here)")
            elif not isinstance(entry, dict):
                problem = f"entry {key!r} is not an object"
            elif not _finite_number(entry.get("baseline")):
                problem = (f"entry {key!r} has non-finite baseline "
                           f"{entry.get('baseline')!r}")
            elif not _sane_tick(entry.get("last_fired_tick")):
                problem = (f"entry {key!r} has non-int last_fired_tick "
                           f"{entry.get('last_fired_tick')!r}")
            elif not isinstance(entry.get("demoted"), bool):
                problem = (f"entry {key!r} has non-bool demoted "
                           f"{entry.get('demoted')!r}")
            else:
                dt = entry.get("demoted_tick")
                if dt is not None and not _sane_tick(dt):
                    problem = (f"entry {key!r} has non-int demoted_tick "
                               f"{dt!r}")
                else:
                    owed = entry.get("owed")
                    if owed is not None:
                        problem = AuthoredDormancyTracker._owed_problem(
                            key, owed)
            if problem is not None:
                raise ValueError(
                    f"dormancy sidecar 'habits' malformed: {problem}; "
                    f"refusing to run on fabricated continuity")
            out[key] = entry
        return out

    @staticmethod
    def _owed_problem(key: str, owed) -> str | None:
        """Shape-check one owed demotion event; None when sound."""
        if not isinstance(owed, dict):
            return f"entry {key!r} has non-object owed {owed!r}"
        if owed.get("habit_key") != key:
            return (f"entry {key!r} owed names a different habit "
                    f"({owed.get('habit_key')!r})")
        span = owed.get("dormancy_ticks")
        if not _sane_tick(span) or span <= 0:
            return (f"entry {key!r} owed has non-positive-int "
                    f"dormancy_ticks {span!r}")
        if not _finite_number(owed.get("baseline")):
            return (f"entry {key!r} owed has non-finite baseline "
                    f"{owed.get('baseline')!r}")
        if not _sane_tick(owed.get("demoted_tick")):
            return (f"entry {key!r} owed has non-int demoted_tick "
                    f"{owed.get('demoted_tick')!r}")
        return None

    # -- per-tick observation ----------------------------------------------

    def _seed_last_fired(self, live) -> int:
        """The engine's own stamp is the truthful last firing.

        A fresh sidecar entry seeds ``last_fired_tick`` from the live
        habit's ``last_used_tick`` — a never-fired habit starts at
        -10000 (dormant by construction), a genuinely exercised one keeps
        its real clock. Anything unparseable falls back to -10000, the
        safe direction (a phantom recent firing would wrongly shield a
        dormant habit; a phantom old firing only risks an early demotion
        of a habit the engine itself never stamped).
        """
        stamp = getattr(live, "last_used_tick", NEVER_FIRED)
        return stamp if _sane_tick(stamp) else NEVER_FIRED

    def observe_tick(self, *, tick: int, state_habits: dict
                     ) -> tuple[list, bool, bool]:
        """Fold one waking tick into the dormancy clock.

        Returns ``(events, sidecar_changed, state_changed)``. ``events``
        holds at most one demotion mint event per habit: the newly
        demoted episode and/or a recovered ``owed`` episode from a tick
        whose mint never committed. ``state_changed`` is True when the
        strength projection differs from live strengths (the caller then
        opens a transaction and runs ``reconcile_state_habits``) —
        including the crash-recovery case where the sidecar holds
        demoted=True (+owed) but the store never received the projection
        (a kill between the sidecar save and the reconcile commit): the
        projection re-derives so the owed note stays truthful.

        A firing (the engine's ``last_used_tick`` stamp equals this tick),
        pre-demotion only, resets the dormancy clock — silently. The
        ratchet: once demoted, the flag latches permanently, and a stamp
        (in vivo impossible at 0.64 — the engine stamps only the bypass
        branch) is ignored; no code path restores strength. An owed
        episode from a crashed tick is re-emitted untouched: the closed
        episode's note still needs its mint (the idempotent mint skips it
        if it already landed). The ``formed:`` namespace is never read or
        written.
        """
        tick = int(tick)
        events: list = []
        sidecar_changed = False
        state_changed = False
        if state_habits is None:
            state_habits = {}
        for key in sorted(self.authored):
            baseline = self.authored[key]
            if baseline < HABIT_FIRE_STRENGTH:
                # Never held the bypass privilege; nothing to demote.
                continue
            entry = self.data["habits"].get(key)
            live = state_habits.get(key)
            if entry is None:
                entry = {"baseline": baseline,
                         "last_fired_tick": self._seed_last_fired(live),
                         "demoted": False, "demoted_tick": None,
                         "owed": None}
                self.data["habits"][key] = entry
                sidecar_changed = True
            elif entry["baseline"] != baseline:
                # The toml is the single source of truth (a cartridge
                # change ships with a store migration, so this is
                # defensive only).
                entry["baseline"] = baseline
                sidecar_changed = True
            if live is None:
                # No live entry: nothing to prove or project, but an owed
                # episode still needs its mint.
                pass
            elif (getattr(live, "last_used_tick", None) == tick
                    and not entry["demoted"]):
                # Proven firing, pre-demotion only: the engine's own stamp
                # resets the dormancy clock — silently (the demotion record
                # already explains). Post-demotion the flag latches
                # permanently: a stamp at 0.64 is in vivo impossible (the
                # engine stamps only the bypass branch at >= 0.65), and the
                # ratchet ignores it — no restore path exists, by design.
                if entry["last_fired_tick"] != tick:
                    entry["last_fired_tick"] = tick
                    sidecar_changed = True
                if live.strength != baseline:
                    state_changed = True
            elif (not entry["demoted"]
                    and live.strength >= HABIT_FIRE_STRENGTH
                    and tick - entry["last_fired_tick"] > DORMANCY_TICKS):
                # Demotion edge: one notch below the bypass threshold,
                # exactly one note per episode. Demote once more than 300
                # ticks have elapsed since the last firing tick (firing at
                # tick 250 -> no demotion at 550, demotion at 551). The
                # owed dict is the
                # write-ahead mark — the caller saves BEFORE the mint
                # transaction commits.
                span = tick - entry["last_fired_tick"]
                entry["demoted"] = True
                entry["demoted_tick"] = tick
                entry["owed"] = {
                    "habit_key": key,
                    "dormancy_ticks": span,
                    "baseline": baseline,
                    "demoted_tick": tick,
                }
                sidecar_changed = True
                state_changed = True
            elif entry["demoted"] and live.strength != DEMOTED_STRENGTH:
                # Crash recovery (critic round 2, RED 1): a kill between
                # the dormancy sidecar save() and the reconcile
                # transaction's commit leaves demoted=True (+owed) in the
                # file while the store still holds the baseline strength.
                # The owed mark re-mints the note below, but without this
                # branch state_changed stays False (the demotion edge
                # requires not demoted) and the strength projection is
                # never re-derived — the minted note would claim a
                # demotion that never behaviorally happened. Re-derive
                # here so the caller's reconcile converges the store to
                # 0.64 and the note stays truthful.
                state_changed = True
            # Recovery: an owed episode from a tick whose mint never
            # committed re-emits here. mint_demotion_note is idempotent on
            # (habit_key, demoted_tick), so a post-mint crash re-mints
            # nothing.
            if entry["owed"] is not None:
                events.append(dict(entry["owed"]))
        return events, sidecar_changed, state_changed

    def ack_demotion_notes(self, events: list) -> None:
        """Clear owed marks after their mint transactions commit.

        Until this runs the file still holds the owed marks, so a kill
        before the ack recovers them from the file on the next tick. If
        the mint raised, this never runs and the marks stay owed.
        """
        for event in events:
            entry = self.data["habits"].get(event["habit_key"])
            owed = entry["owed"] if entry is not None else None
            if (owed is not None
                    and owed["demoted_tick"] == event["demoted_tick"]):
                entry["owed"] = None

    # -- state projection ---------------------------------------------------

    def reconcile_state_habits(self, habits: dict, *, tick: int) -> None:
        """Project the sidecar's demotion decisions onto live authored entries.

        Only authored keys from the toml are ever written — the
        ``formed:`` namespace is never touched. The target re-derives
        from durable sidecar state plus the live firing stamp (which
        survives the payload round-trip), so this is safe to run on the
        freshly restored state inside the subject transaction: the
        ratchet — a demoted entry always targets 0.64, even if a stamp
        claims a firing this tick (in vivo impossible at 0.64; a hand-set
        stamp must not restore either). A pre-demotion firing this tick
        targets the exact authored baseline; anything else is left alone.
        Floor: the projection never writes below 0.64.
        """
        tick = int(tick)
        for key in sorted(self.data["habits"]):
            if key.startswith(FORMED_PREFIX):
                continue
            entry = self.data["habits"][key]
            live = habits.get(key)
            if live is None:
                continue
            # Ratchet: the restore-on-firing path was removed entirely in
            # round 2 — dead code that can never execute in vivo (the
            # engine stamps only the bypass branch at >= 0.65) is a
            # liability. A demoted entry always targets 0.64.
            if entry["demoted"]:
                target = DEMOTED_STRENGTH
            elif getattr(live, "last_used_tick", None) == tick:
                target = entry["baseline"]
            else:
                continue
            if target < DEMOTED_STRENGTH:
                target = DEMOTED_STRENGTH  # belt-and-braces: the floor
            if live.strength != target:
                live.strength = target

    # -- persistence --------------------------------------------------------

    # Note: there is deliberately no reset() method. On reseed
    # (mind init --force) cli.py unlinks the sidecar file directly, like
    # the other tracker sidecars — a reseed starts with no dormancy
    # history at all, and the constructed tracker on a missing file is
    # the clean empty state.

    def save(self) -> None:
        """Atomically persist the sidecar (temp file + os.replace + fsync).

        A crash mid-write leaves either the complete old body or the
        complete new body — never a truncated shell. The temp file lives
        in the same directory so the replace is an atomic rename.
        """
        ordered = {
            "habits": {k: self.data["habits"][k]
                       for k in sorted(self.data["habits"])},
        }
        body = json.dumps(ordered, ensure_ascii=False, indent=1)
        tmp = self.path.parent / (self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


# -- demotion notes: canonical text, parsing, minting -----------------------
# Follows the stale-note precedent in habits.py: the record text is the
# canonical serialization — the only writer is demotion_note_text(), the
# only reader is the anchored fullmatch below, so the identifier cannot
# drift from the writer.

def demotion_note_text(event: dict) -> str:
    """First-person text for one demotion episode.

    Names the habit key, the dormancy span in ticks, and the authored
    baseline, then a machine sentence carrying everything the fitness
    function needs to recover the episode from the record alone: the
    habit key, the span, the baseline in a fixed format, the demoted
    strength, and the demotion tick.

    No contractions (the similarity vetoes tokenize on raw text —
    regression genome), no wall clock, rounded fixed-format floats:
    identical episodes mint byte-identical text.
    """
    return (
        f"I have not used my {event['habit_key']} habit in "
        f"{event['dormancy_ticks']} ticks, so it loses its automatic edge. "
        f"It arrived installed at {event['baseline']:.6f}, not "
        f"earned by practice. "
        f"That is authored habit {event['habit_key']} dormant "
        f"{event['dormancy_ticks']} ticks, baseline "
        f"{event['baseline']:.6f}, demoted to "
        f"{DEMOTED_STRENGTH:.6f} at tick {event['demoted_tick']}."
    )


_DEMOTION_NOTE_RE = re.compile(
    r"I have not used my (\S+) habit in (\d+) ticks, so it loses its "
    r"automatic edge\. It arrived installed at (\d+\.\d{6}), not "
    r"earned by practice\. "
    r"That is authored habit (\S+) dormant (\d+) ticks, baseline "
    r"(\d+\.\d{6}), demoted to (\d+\.\d{6}) at tick (\d+)\.")


def parse_demotion_note_text(text) -> dict | None:
    """Parse a demotion note's canonical text, or None.

    Returns the episode identity (habit_key, demoted_tick) plus the
    rendered fields. None means "not a demotion note" — never a partial
    match: the fullmatch anchors both ends, the prose and machine
    sentences must agree on key/span/baseline, the demoted strength must
    be exactly the tracker's floor, and the key must not name the
    formed: namespace.
    """
    if not isinstance(text, str):
        return None
    m = _DEMOTION_NOTE_RE.fullmatch(text)
    if m is None:
        return None
    key1, key2 = m.group(1), m.group(4)
    span1, span2 = m.group(2), m.group(5)
    base1, base2 = m.group(3), m.group(6)
    if key1 != key2 or span1 != span2 or base1 != base2:
        return None
    if key1.startswith(FORMED_PREFIX):
        return None
    if float(m.group(7)) != DEMOTED_STRENGTH:
        return None
    return {"habit_key": key1,
            "dormancy_ticks": int(span1),
            "baseline": float(base1),
            "demoted_tick": int(m.group(8))}


def is_demotion_note_text(text) -> bool:
    """Whether a record text is a domain-27 demotion note."""
    return parse_demotion_note_text(text) is not None


def mint_demotion_note(subject, event: dict) -> str:
    """Append one demotion note through the subject's normal record path.

    Waking-tick path only (cli._run_tick); the caller owns the
    transaction so the record persists. Minting never advances the store
    tick and never touches the trace: it is a write inside the existing
    tick, not a new tick. Stamped generated_by="cognition"
    (engine-originated, not authored), and enters view eligibility,
    salience, and dedupe like any temporal record. Returns the record id.

    Idempotent (write-ahead crash discipline, cf. mint_stale_note): when
    a note for the same (habit_key, demoted_tick) is already in the store
    — e.g. an owed mark re-minted after a kill between the mint and the
    ack — the existing record's id is returned and nothing is appended:
    exactly one record per demotion episode. The check reads
    subject.workspace.records, the in-memory list _add appends to, so
    mints earlier in the SAME transaction are visible.
    """
    for record in subject.workspace.records:
        if record.source != "temporal" or record.generated_by != "cognition":
            continue
        parsed = parse_demotion_note_text(record.first_person)
        if (parsed is not None
                and parsed["habit_key"] == event["habit_key"]
                and parsed["demoted_tick"] == event["demoted_tick"]):
            return record.id
    item = subject._add("temporal", demotion_note_text(event),
                        concepts=(event["habit_key"],),
                        generated_by="cognition",
                        available_to_cognition=True,
                        salience=1.0, intensity=1.0)
    return item.id
