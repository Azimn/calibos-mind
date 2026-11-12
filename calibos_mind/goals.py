"""Goal-structure instrument — a number for Domain 17's "Goal structure".

Domain 17 (Goal structure), 2026-11-10, tick 1056, 1876 records: the
entire goal machinery was unexercised in vivo. Zero open commitments (2
total, both kept CLI test commitments), zero open expectations (21 total,
all confirmed ``inbox:pr``), engine concerns ``{}`` empty, ``unresolved``
empty, zero ``promise_made`` events in 1056 ticks / ~47 days. Behavioural
mechanisms (dormancy, shielding, wish-to-plan staging) would be dead code
against a goal population of zero. Measurement first, following the
personality-drift instrument precedent (2026-11-09): make the gap visible
and tracked, then decide on behaviour from evidence. Nothing here installs
a goal, an aspiration, a quirk, or any goal content as theater.

This is measurement, not a new behavior: it observes the goal population,
it never steers it. No commitment or expectation is created, resolved, or
re-ranked here. No cartridge or engine change. No store migration (new
sidecar only).

Design rules (mirroring personality.py):
- Sidecar ``goals.json`` (next to ``personality-drift.json``)::
    ``{"rows": [row, ...],
       "known": {"<commitment-id>": {"actor": ..., "description": ...,
                                     "kind": "authored"|"candidate",
                                     "first_tick": ...}, ...},
       "tension": {"current": <int>, "longest": <int>}}``
  A row is one observed waking tick::
    ``{"tick": <int>, "open": <int>, "oldest_open_age": <int>|null,
       "commitments": {"open": n, "overdue": n, "kept": n, "broken": n,
                       "released": n},
       "due_soon": <int>,
       "open_expectations": <int>,
       "expectations": {"pending": n, "confirmed": n, "violated": n,
                        "expired": n}}``
  ``rows`` holds the last ROW_CAP observed ticks (oldest dropped on
  append); ``known`` records every commitment id the instrument has ever
  seen, with the tick it first appeared; ``tension`` tracks the longest
  run of consecutive observed ticks with >= 1 open commitment (``open``
  = status ``open`` or ``overdue``, the engine's own ``summary()``
  definition) and the current run. No other fields, no timestamps, no
  thought text. Commitment descriptions ARE recorded in ``known`` — the
  spec requires actor + description for each detected goal — but the
  sidecar is local-only and gitignored like every other sidecar.
- The tracker is fed ONLY from the waking-tick path (``cli._run_tick``),
  right after the tick, at the same hook as the personality instrument's
  ``observe()``. Every waking tick records one row, even a tick with zero
  open commitments: a zero is data (it resets the tension run), not an
  absence. Read-only commands (``drift``, ``status``, ``review``,
  ``inbox``, ``goals``, ``think``, the ``dream`` command) and dream ticks
  NEVER write the sidecar; ``cmd_init --force`` wipes it.
- New-goal detection: the commitment id set is diffed against the ids
  already known to the sidecar. A newly-appeared id whose actor is not
  ``"self"`` is recorded as a ``candidate`` accidentally-formed goal
  (the frozen engine creates such commitments only from ``promise_made``
  events or event metadata, continuity.py:384,413; the CLI ``mind
  commit`` path always sets actor ``"self"``). A new commitment with
  actor ``"self"`` is recorded as ``authored``. Formation is never
  inferred from text content. A commitment that appears and disappears
  between two observations is never seen — sampling, not a defect.
- Tension span counts consecutive *observations*, not consecutive tick
  numbers: an unobserved tick carries no evidence either way, so only an
  observed tick with zero open commitments resets the current run. In
  practice every waking tick is observed, so the two coincide.
- Due-soon: an ``open`` commitment whose ``due_tick`` is within 2 ticks
  (``due_tick - tick <= 2``, no lower bound), exactly the window
  ``derive_continuity_influence`` uses for its ``commitment_due_soon``
  reason. An open commitment already past due still counts — the engine
  counts it too.
- ``report()`` is a pure read of live state + sidecar: counts and
  oldest-open age come from the live store at call time (so a commitment
  registered by ``mind intend`` shows immediately, before any tick),
  tension spans and the formed-goal history come from the sidecar. It
  returns the accumulated formed-goal list with first-seen ticks; there
  is no "reported" watermark, because the ``goals`` command itself must
  leave the sidecar byte-identical and a watermark would be a write.
  ``new_in_latest_observation`` is the delta of the most recent
  observed tick.
- Determinism: identical store evolutions yield byte-identical sidecars.
  No clock, no RNG, no model calls. (Commitment ids are store input: two
  runs that mint different ids legitimately differ.)
- Corrupt sidecar fails loud: invalid JSON, a non-dict top level, a
  malformed ``rows``/``known``/``tension`` section, a non-integer
  count, or a row whose fields contradict each other (``open`` vs
  the status counts, ``due_soon`` vs ``open`` and vs the status-
  ``open`` count, ``oldest_open_age`` vs ``open`` and vs ``tick``,
  ``open_expectations`` vs pending) raises ValueError at
  construction. Never silent-delete, never silent-zero. (``cmd_init --force`` wipes the sidecar before
  construction, so a corrupt file can never brick a reseed.)
- Fail closed on a subject without continuity state: a report that
  silently said "0 commitments" when it could not read the store would
  be the silent-zero genome bug. ``observe()`` and ``report()`` raise
  ValueError instead.
"""
from __future__ import annotations

import json
from pathlib import Path

# Bounded history, like the personality sidecar's window.
ROW_CAP = 100

# Due-soon window in ticks, matching derive_continuity_influence.
DUE_SOON_WINDOW = 2

# The only commitment statuses the frozen engine produces.
COMMITMENT_STATUSES = ("open", "overdue", "kept", "broken", "released")

# The only expectation statuses the frozen engine produces.
EXPECTATION_STATUSES = ("pending", "confirmed", "violated", "expired")

# Statuses counting as an open commitment (engine summary() definition).
OPEN_COMMITMENT_STATUSES = ("open", "overdue")


def _is_int(value) -> bool:
    """A genuine int count: bools (an int subclass) never count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _continuity_state(subject):
    """The subject's continuity state, or a loud failure.

    A missing state is not an empty goal population — it is a subject
    this instrument cannot read, and reporting zero would be the
    silent-zero genome bug.
    """
    state = getattr(getattr(subject, "continuity", None), "state", None)
    if state is None:
        raise ValueError(
            "goals instrument needs a subject with continuity state")
    return state


def _live_counts(state, tick: int) -> dict:
    """Exact goal-population counts from live continuity state.

    Shared by ``observe()`` (snapshot row) and ``report()`` (live
    reading) so the two can never drift apart in definition. Raises
    ValueError on a commitment/expectation status the frozen engine
    cannot produce — a loud failure, never a silently dropped bucket.
    """
    by_status: dict[str, int] = {s: 0 for s in COMMITMENT_STATUSES}
    oldest_created: int | None = None
    due_soon_ids: list[str] = []
    for item in state.commitments.values():
        status = item.status
        if status not in by_status:
            raise ValueError(
                f"unknown commitment status {status!r} for {item.id!r}")
        by_status[status] += 1
        if status in OPEN_COMMITMENT_STATUSES:
            if oldest_created is None or item.created_tick < oldest_created:
                oldest_created = item.created_tick
        if (status == "open" and item.due_tick is not None
                and item.due_tick - tick <= DUE_SOON_WINDOW):
            due_soon_ids.append(item.id)
    by_exp: dict[str, int] = {s: 0 for s in EXPECTATION_STATUSES}
    for item in state.expectations.values():
        status = item.status
        if status not in by_exp:
            raise ValueError(
                f"unknown expectation status {status!r} for {item.id!r}")
        by_exp[status] += 1
    open_count = by_status["open"] + by_status["overdue"]
    return {
        "open": open_count,
        "oldest_open_age": (None if oldest_created is None
                            else tick - oldest_created),
        "commitments": by_status,
        "due_soon": len(due_soon_ids),
        "due_soon_ids": sorted(due_soon_ids),
        "open_expectations": by_exp["pending"],
        "expectations": by_exp,
    }


class GoalTracker:
    """Goal-population sidecar (``goals.json``, local-only).

    ``data`` is ``{"rows": [...], "known": {...}, "tension": {...}}``.
    The constructor only reads the sidecar; rows move exclusively via
    ``observe()`` + ``save()`` on the waking-tick path
    (``cli._run_tick``).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"rows": [], "known": {},
                           "tension": {"current": 0, "longest": 0}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(
                    f"corrupt goals sidecar {self.path}: "
                    f"invalid JSON ({exc})"
                ) from exc
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"corrupt goals sidecar {self.path}: top level is "
                    f"not an object")
            rows = loaded.get("rows", [])
            if not isinstance(rows, list):
                raise ValueError(
                    f"corrupt goals sidecar {self.path}: 'rows' is not "
                    f"a list")
            for row in rows:
                self._validate_row(row)
            known = loaded.get("known", {})
            if not isinstance(known, dict):
                raise ValueError(
                    f"corrupt goals sidecar {self.path}: 'known' is not "
                    f"an object")
            for cid, entry in known.items():
                self._validate_known_entry(cid, entry)
            tension = loaded.get("tension", {"current": 0, "longest": 0})
            if not isinstance(tension, dict):
                raise ValueError(
                    f"corrupt goals sidecar {self.path}: 'tension' is "
                    f"not an object")
            for key in ("current", "longest"):
                value = tension.get(key)
                if not _is_int(value) or value < 0:
                    raise ValueError(
                        f"corrupt goals sidecar {self.path}: tension "
                        f"{key!r} is not a non-negative integer")
            if tension.get("current", 0) > tension.get("longest", 0):
                raise ValueError(
                    f"corrupt goals sidecar {self.path}: tension "
                    f"'current' exceeds 'longest' (observe() maintains "
                    f"longest = max(longest, current) on every tick)")
            self.data["rows"] = [dict(row) for row in rows]
            self.data["known"] = {cid: dict(entry)
                                  for cid, entry in known.items()}
            self.data["tension"] = {"current": tension.get("current", 0),
                                    "longest": tension.get("longest", 0)}

    def _validate_row(self, row) -> None:
        """One snapshot row: exact shape, or a loud ValueError."""
        path = self.path

        def bad(why: str):
            raise ValueError(
                f"corrupt goals sidecar {path}: row {row!r} {why}")

        if not isinstance(row, dict):
            bad("is not an object")
        if not _is_int(row.get("tick")) or row["tick"] < 0:
            bad("has no non-negative integer 'tick'")
        if not _is_int(row.get("open")) or row["open"] < 0:
            bad("has no non-negative integer 'open'")
        age = row.get("oldest_open_age")
        if age is not None and (not _is_int(age) or age < 0):
            bad("has no null-or-non-negative-integer 'oldest_open_age'")
        by_status = row.get("commitments")
        if not isinstance(by_status, dict):
            bad("has no 'commitments' object")
        for status in COMMITMENT_STATUSES:
            value = by_status.get(status)
            if not _is_int(value) or value < 0:
                bad(f"has no non-negative integer commitments[{status!r}]")
        if not _is_int(row.get("due_soon")) or row["due_soon"] < 0:
            bad("has no non-negative integer 'due_soon'")
        if not _is_int(row.get("open_expectations")) \
                or row["open_expectations"] < 0:
            bad("has no non-negative integer 'open_expectations'")
        by_exp = row.get("expectations")
        if not isinstance(by_exp, dict):
            bad("has no 'expectations' object")
        for status in EXPECTATION_STATUSES:
            value = by_exp.get(status)
            if not _is_int(value) or value < 0:
                bad(f"has no non-negative integer expectations[{status!r}]")
        # Cross-field invariants save() always produces (critic round 1):
        # a row violating any of these is a tampered or half-written
        # sidecar, not data — fail closed.
        if row["open"] != by_status["open"] + by_status["overdue"]:
            bad("'open' does not equal commitments['open'] + "
                "commitments['overdue']")
        if row["due_soon"] > row["open"]:
            bad("'due_soon' exceeds 'open'")
        if row["open_expectations"] != by_exp["pending"]:
            bad("'open_expectations' does not equal "
                "expectations['pending']")
        # Stricter invariants of the same class (critic round 2):
        # due_soon counts status "open" commitments ONLY (_live_counts
        # gates the due-soon bucket on status == "open"), so an
        # overdue-only row can never carry due_soon; oldest_open_age
        # is null exactly when no open/overdue commitment exists to
        # age, and an integer whenever one does.
        if row["due_soon"] > by_status["open"]:
            bad("'due_soon' exceeds commitments['open'] (due-soon "
                "counts status 'open' only)")
        if row["open"] == 0 and age is not None:
            bad("'oldest_open_age' is present with zero open "
                "commitments")
        if row["open"] >= 1 and age is None:
            bad("'oldest_open_age' is null with open commitments")
        # Age is tick - oldest_created and a commitment is never
        # created at a negative tick, so an age above the observation
        # tick implies a negative created tick — save() can never
        # produce it.
        if age is not None and age > row["tick"]:
            bad("'oldest_open_age' exceeds 'tick'")

    def _validate_known_entry(self, cid, entry) -> None:
        """One known-goal entry: exact shape, or a loud ValueError."""
        path = self.path

        def bad(why: str):
            raise ValueError(
                f"corrupt goals sidecar {path}: known entry {cid!r} {why}")

        if not isinstance(cid, str):
            bad("has a non-string id")
        if not isinstance(entry, dict):
            bad("is not an object")
        if not isinstance(entry.get("actor"), str):
            bad("has no string 'actor'")
        if not isinstance(entry.get("description"), str):
            bad("has no string 'description'")
        if entry.get("kind") not in ("authored", "candidate"):
            bad("has no 'kind' of 'authored' or 'candidate'")
        if not _is_int(entry.get("first_tick")) or entry["first_tick"] < 0:
            bad("has no non-negative integer 'first_tick'")

    @property
    def rows(self) -> list:
        """A copy of the snapshot rows."""
        return [dict(row) for row in self.data["rows"]]

    @property
    def known(self) -> dict:
        """A copy of the known-goal map."""
        return {cid: dict(entry)
                for cid, entry in self.data["known"].items()}

    @property
    def tension(self) -> dict:
        """A copy of the tension spans."""
        return dict(self.data["tension"])

    def observe(self, subject, tick: int) -> bool:
        """Record one waking tick's goal-population snapshot.

        Reads the live commitment/expectation population, diffs the
        commitment id set against the ids already known (recording
        newly-appeared ids with actor, description, and kind), updates
        the tension run, and appends the snapshot row (oldest dropped
        past ROW_CAP). Always returns True: every waking tick records a
        row, even a tick with zero open commitments — a zero resets the
        tension run and is data, not an absence.
        """
        if not _is_int(tick):
            raise ValueError(f"observe() needs an integer tick, got {tick!r}")
        state = _continuity_state(subject)
        counts = _live_counts(state, tick)
        # New-goal detection: ids the sidecar has never seen. Never infer
        # formation from text content — actor alone decides the kind.
        for cid, item in state.commitments.items():
            if cid not in self.data["known"]:
                self.data["known"][cid] = {
                    "actor": str(item.actor),
                    "description": str(item.description),
                    "kind": ("authored" if item.actor == "self"
                             else "candidate"),
                    "first_tick": tick,
                }
        # Tension span: a run of consecutive observed ticks with >= 1
        # open commitment. An observed zero-open tick resets the run;
        # the longest run is retained.
        if counts["open"] >= 1:
            self.data["tension"]["current"] += 1
            self.data["tension"]["longest"] = max(
                self.data["tension"]["longest"],
                self.data["tension"]["current"])
        else:
            self.data["tension"]["current"] = 0
        self.data["rows"].append({
            "tick": tick,
            "open": counts["open"],
            "oldest_open_age": counts["oldest_open_age"],
            "commitments": dict(counts["commitments"]),
            "due_soon": counts["due_soon"],
            "open_expectations": counts["open_expectations"],
            "expectations": dict(counts["expectations"]),
        })
        del self.data["rows"][:-ROW_CAP]
        return True

    def save(self) -> None:
        """Write the sidecar deterministically (sorted keys, fixed form)."""
        ordered = {
            "known": {cid: self.data["known"][cid]
                      for cid in sorted(self.data["known"], key=str)},
            "rows": [{
                "commitments": {s: row["commitments"][s]
                                for s in COMMITMENT_STATUSES},
                "due_soon": row["due_soon"],
                "expectations": {s: row["expectations"][s]
                                 for s in EXPECTATION_STATUSES},
                "oldest_open_age": row["oldest_open_age"],
                "open": row["open"],
                "open_expectations": row["open_expectations"],
                "tick": row["tick"],
            } for row in self.data["rows"]],
            "tension": {"current": self.data["tension"]["current"],
                        "longest": self.data["tension"]["longest"]},
        }
        self.path.write_text(
            json.dumps(ordered, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8")


def report(subject, sidecar) -> dict:
    """Pure read of live state + sidecar: the Domain 17 report.

    ``sidecar`` is a ``GoalTracker`` or a path to ``goals.json``. Counts,
    oldest-open age, and due-soon ids come from the live store at call
    time; tension spans and the formed-goal history come from the
    sidecar. Nothing is written: calling this twice returns equal dicts
    and leaves the sidecar byte-identical.

    Returns ``{"tick", "commitments": {"by_status", "open",
    "oldest_open_age", "due_soon": [ids]}, "expectations": {"by_status",
    "open", "outcomes": {"confirmed", "violated", "expired"}}, "tension":
    {"current", "longest"}, "formed": [{id, kind, actor, description,
    first_tick}], "new_in_latest_observation": [...], "observed_ticks",
    "latest_observation_tick"}``. ``formed`` is the accumulated detected
    set, ordered by first-seen tick; ``new_in_latest_observation`` is
    the subset first seen at the latest observed tick.
    """
    tracker = (sidecar if isinstance(sidecar, GoalTracker)
               else GoalTracker(sidecar))
    state = _continuity_state(subject)
    tick = subject.engine.state.tick
    counts = _live_counts(state, tick)
    rows = tracker.rows
    latest_tick = rows[-1]["tick"] if rows else None
    formed = [{
        "id": cid,
        "kind": entry["kind"],
        "actor": entry["actor"],
        "description": entry["description"],
        "first_tick": entry["first_tick"],
    } for cid, entry in sorted(tracker.known.items(),
                               key=lambda kv: (kv[1]["first_tick"], kv[0]))]
    return {
        "tick": tick,
        "commitments": {
            "by_status": dict(counts["commitments"]),
            "open": counts["open"],
            "oldest_open_age": counts["oldest_open_age"],
            "due_soon": list(counts["due_soon_ids"]),
        },
        "expectations": {
            "by_status": dict(counts["expectations"]),
            "open": counts["open_expectations"],
            "outcomes": {s: counts["expectations"][s]
                         for s in ("confirmed", "violated", "expired")},
        },
        "tension": dict(tracker.tension),
        "formed": formed,
        "new_in_latest_observation": [
            g for g in formed if g["first_tick"] == latest_tick],
        "observed_ticks": len(rows),
        "latest_observation_tick": latest_tick,
    }
