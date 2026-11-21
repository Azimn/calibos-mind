"""Realization follow-through tracker — a number for Domain 22's
"Behavioral consequences are not traceable across time".

Domain 22 (Internal causality), check 9, 2026-11-15: the causal chain IS
recorded (counterfactual traces, prediction-error traces, realization
records with swing/convergence ticks), but whether a recorded consequence
ever changes later conduct was untested — the 2026-11-14 domain-21 pass
noted no conduct change observably follows a realization record. This
instrument supplies the missing evidence: for each realization record,
does the action mix after its convergence differ from the action mix
before its swing?

This is measurement, not a new behavior: it observes conduct, it never
steers it. No action selection, retrieval, or cognition path changes.
No cartridge or engine change. No store migration (new sidecar only).

Design rules (mirroring personality.py):
- Sidecar ``followthrough.json`` (next to ``personality-drift.json``):
  a bare capped, append-only log ``[[tick, action], ...]`` of
  action-bearing waking ticks (oldest dropped on append past LOG_CAP).
  No other fields, no timestamps, no thought text.
- The tracker is fed ONLY from the waking-tick path (``cli._run_tick``),
  after the tick's action is chosen: it records the tick and the chosen
  action, exactly the source personality.py uses (the engine's
  ``last_intention`` / ``heartbeat()`` result ``"action"``). Ticks with
  no chosen action record nothing.
- The report scans the live store for realization records using the
  canonical parser ``interoception.parse_realization_text`` (never a
  substring heuristic): each yields (need, swing_tick, conv_tick,
  felt_band, actual_band). For each, the action-share distribution in
  the 20 action-bearing ticks BEFORE the swing tick is compared with
  the 20 action-bearing ticks AFTER the convergence tick, as total
  variation distance (half the L1 distance between the two share
  vectors over the union of actions, range [0, 1] — the same math
  family as the personality drift). A realization whose before- or
  after-window is not fully present in the action log reports
  ``insufficient-window`` for that row — never a quiet 0.0 — and so
  does a realization whose swing tick is after its convergence tick,
  where the two windows would overlap and any tv would be vacuous
  (the engine can never mint that shape; only hand-authored record
  text can carry it). The
  aggregate (mean tv over measurable realizations) excludes such rows
  and says so; with zero measurable realizations the mean is None
  ("undefined"), never 0.0.
- Read-only commands (``drift``, ``status``, ``review``, ``inbox``,
  ``followthrough``, ``predictions``, ``goals``) and dream ticks NEVER
  write the sidecar; ``cmd_init --force`` wipes it.
- Determinism: identical tick/action sequences yield byte-identical
  sidecars. No clock, no RNG, no model calls.
- Corrupt sidecar fails loud: invalid JSON, a non-list top level, a
  row that is not [tick, action], a non-integer or negative tick, a
  non-string action, or ticks out of order raises ValueError at
  construction. Never silent-delete, never silent-zero.
  (``cmd_init --force`` wipes the sidecar before construction, so a
  corrupt file can never brick a reseed.)
- Surfacing (Domain 27, 2026-11-20): when a realization's row becomes
  measurable, the waking-tick path mints exactly one temporal record
  summarizing the measured conduct change (see
  ``surface_newly_measurable``), so the thinker can encounter its own
  measured past-vs-present comparison in a later view. The sidecar
  carries a persisted ``surfaced`` set of realization identities
  (need, swing tick, convergence tick) with an ``owed`` write-ahead
  mark per entry, mirroring the dormancy demotion note: the owed mark
  saves BEFORE the mint transaction commits, the mint is idempotent
  on the realization identity, and the ack clears the mark only after
  the mint commits. A kill between sidecar save and mint re-mints
  nothing on recovery. Read-only commands and dream ticks never
  surface and never write the sidecar. The summary text is factual
  only (need, swing tick, convergence tick, felt band, actual band,
  before/after mixes, tv distance) — no interpretation, no promise,
  no judgment. The sidecar format is ``{"log": [...], "surfaced":
  [...]}``; a bare-list file (the pre-2026-11-20 shape) still loads
  as a log with an empty surfaced set and migrates on the next save.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from jelly_psiduck.firewall import NEED_LANGUAGE

from .interoception import FELT_BANDS

# Bounded action log, mirroring the engine trace cap.
LOG_CAP = 256

# Action-bearing ticks per window, before the swing tick and after the
# convergence tick. Both windows must be fully present for a row to be
# measurable.
WINDOW = 20


def _is_int(value) -> bool:
    """A genuine int count: bools (an int subclass) never count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _shares(rows) -> dict:
    """Action-share vector for a list of [tick, action] rows."""
    counts: dict[str, int] = {}
    for _tick, action in rows:
        counts[action] = counts.get(action, 0) + 1
    n = sum(counts.values())
    if n == 0:
        return {}
    return {a: c / n for a, c in sorted(counts.items())}


def total_variation(before_shares: dict, after_shares: dict) -> float:
    """Total variation distance between two action-share vectors.

    Half the L1 distance over the union of actions, range [0, 1]:
    0.0 when the mixes are identical, 1.0 when they share no action
    at all. Clamped against float dust, never against a real reading.
    """
    distance = sum(
        abs(before_shares.get(a, 0.0) - after_shares.get(a, 0.0))
        for a in set(before_shares) | set(after_shares))
    return min(distance / 2.0, 1.0)


class FollowthroughTracker:
    """Action-log sidecar (``followthrough.json``, local-only).

    ``data`` is ``[[tick, action], ...]``, oldest first, capped at
    LOG_CAP rows. ``surfaced`` is the persisted set of realization
    identities (need, swing tick, convergence tick) whose measured
    conduct change has already been surfaced as a summary record, each
    entry ``{"need", "swing_tick", "conv_tick", "owed"}`` — ``owed``
    True is the write-ahead mark for a summary whose mint has not yet
    been acked (see ``due_summaries`` / ``ack_summaries``). The file
    holds ``{"log": data, "surfaced": surfaced}``; a bare-list file
    (the pre-2026-11-20 shape) still loads as a log with an empty
    surfaced set and migrates on the next save. The constructor only
    reads the sidecar; rows move exclusively via ``observe()`` + ``save()``
    on the waking-tick path (``cli._run_tick``), and surfaced marks move
    via ``due_summaries()`` / ``ack_summaries()`` + ``save()`` on the
    same path.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: list = []
        self.surfaced: list = []
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(
                    f"corrupt followthrough sidecar {self.path}: "
                    f"invalid JSON ({exc})"
                ) from exc
            if isinstance(loaded, list):
                # Legacy bare-list shape (pre-2026-11-20): the log only,
                # surfaced set empty. Migrates to the dict shape on the
                # next save; a read-only load leaves the bytes untouched.
                self.data = self._validated_log(loaded)
            elif isinstance(loaded, dict):
                if "log" not in loaded:
                    raise ValueError(
                        f"corrupt followthrough sidecar {self.path}: "
                        f"object has no 'log' key")
                self.data = self._validated_log(loaded.get("log"))
                self.surfaced = self._validated_surfaced(
                    loaded.get("surfaced", []))
            else:
                raise ValueError(
                    f"corrupt followthrough sidecar {self.path}: top "
                    f"level is neither a list nor an object")

    def _validated_log(self, raw) -> list:
        """Strict-load the action log; any malformed row raises.

        An explicit JSON null is corruption, not an empty log — the
        "log" key is required, so a null here can only come from an
        explicit null, and silently emptying the log would discard
        recorded conduct."""
        if raw is None:
            raise ValueError(
                f"corrupt followthrough sidecar {self.path}: "
                f"log is null")
        if not isinstance(raw, list):
            raise ValueError(
                f"corrupt followthrough sidecar {self.path}: "
                f"log is not a list")
        prev_tick = None
        for row in raw:
            if (not isinstance(row, list) or len(row) != 2
                    or not _is_int(row[0]) or row[0] < 0
                    or not isinstance(row[1], str)):
                raise ValueError(
                    f"corrupt followthrough sidecar {self.path}: "
                    f"log row {row!r} is not [tick, action]")
            if prev_tick is not None and row[0] <= prev_tick:
                raise ValueError(
                    f"corrupt followthrough sidecar {self.path}: "
                    f"log ticks out of order at {row!r} "
                    f"(previous tick {prev_tick})")
            prev_tick = row[0]
        return [list(row) for row in raw]

    def _validated_surfaced(self, raw) -> list:
        """Strict-load the surfaced set; any malformed entry raises.

        An entry is ``{"need", "swing_tick", "conv_tick", "owed"}``:
        a non-empty need name, non-negative int ticks, a bool owed
        mark. A duplicate identity is malformed — the set holds one
        entry per realization, and a duplicate means the writer (this
        module) or a hand edit broke the invariant; fail loud rather
        than silently dedupe. An explicit JSON null is likewise
        corruption, not an empty set — only a *missing* "surfaced"
        key defaults to empty (the constructor passes [] then), and
        silently forgetting which realizations were surfaced would
        re-mint summaries for every measurable row.
        """
        if raw is None:
            raise ValueError(
                f"corrupt followthrough sidecar {self.path}: "
                f"surfaced is null")
        if not isinstance(raw, list):
            raise ValueError(
                f"corrupt followthrough sidecar {self.path}: "
                f"surfaced is not a list")
        seen = set()
        out = []
        for entry in raw:
            problem = None
            if not isinstance(entry, dict):
                problem = f"surfaced entry {entry!r} is not an object"
            elif (not isinstance(entry.get("need"), str)
                    or not entry.get("need")):
                problem = (f"surfaced entry {entry!r} has no need name")
            elif (not _is_int(entry.get("swing_tick"))
                    or entry["swing_tick"] < 0):
                problem = (f"surfaced entry {entry!r} has non-int or "
                           f"negative swing_tick")
            elif (not _is_int(entry.get("conv_tick"))
                    or entry["conv_tick"] < 0):
                problem = (f"surfaced entry {entry!r} has non-int or "
                           f"negative conv_tick")
            elif not isinstance(entry.get("owed"), bool):
                problem = (f"surfaced entry {entry!r} has non-bool owed")
            else:
                key = (entry["need"], entry["swing_tick"],
                       entry["conv_tick"])
                if key in seen:
                    problem = (f"surfaced entry {entry!r} duplicates "
                               f"identity {key!r}")
                seen.add(key)
            if problem is not None:
                raise ValueError(
                    f"corrupt followthrough sidecar {self.path}: "
                    f"{problem}; refusing to run on fabricated continuity")
            out.append({"need": entry["need"],
                        "swing_tick": entry["swing_tick"],
                        "conv_tick": entry["conv_tick"],
                        "owed": entry["owed"]})
        return out

    @property
    def rows(self) -> list:
        """A copy of the [tick, action] log rows."""
        return [list(row) for row in self.data]

    @staticmethod
    def _identity(row: dict) -> tuple:
        """A measured row's realization identity."""
        return (row["need"], row["swing_tick"], row["conv_tick"])

    def _entry(self, key: tuple) -> dict | None:
        """The surfaced entry for an identity, or None."""
        for entry in self.surfaced:
            if (entry["need"], entry["swing_tick"],
                    entry["conv_tick"]) == key:
                return entry
        return None

    def due_summaries(self, rep: dict) -> tuple[list, bool]:
        """Which measured rows still need a summary record.

        Returns ``(events, sidecar_changed)``. ``events`` holds one
        report row per newly-measurable realization — measured and
        neither surfaced nor already owed (an owed entry re-emits its
        row: the crash-recovery case, where the mint never committed
        or the ack never landed). A newly due row is marked owed here;
        the caller saves BEFORE the mint transaction commits, so the
        mark is durable before any record exists. An owed entry whose
        row is no longer measurable — the log's 256-row cap evicted
        its windows before recovery ran — is dropped without minting:
        there is no measurement left to truthfully summarize, and
        minting from stale numbers would be fabrication. The caller
        saves whenever ``sidecar_changed`` is True, even with no
        events (that save is what drops a stale owed mark).
        """
        measured: dict = {}
        for row in rep.get("rows", []):
            if row.get("status") != "measured":
                continue
            key = self._identity(row)
            if key not in measured:
                measured[key] = row
        events: list = []
        sidecar_changed = False
        for entry in list(self.surfaced):
            key = (entry["need"], entry["swing_tick"],
                   entry["conv_tick"])
            if entry["owed"] and key not in measured:
                # Stale owed: the windows evaporated under the log cap
                # before recovery ran. Drop the mark; nothing truthful
                # remains to mint.
                self.surfaced.remove(entry)
                sidecar_changed = True
        for key, row in measured.items():
            entry = self._entry(key)
            if entry is not None and not entry["owed"]:
                continue  # already surfaced
            if entry is None:
                self.surfaced.append(
                    {"need": key[0], "swing_tick": key[1],
                     "conv_tick": key[2], "owed": True})
                sidecar_changed = True
            events.append(row)
        return events, sidecar_changed

    def ack_summaries(self, events: list) -> None:
        """Clear owed marks after their mint transactions commit.

        Until this runs the file still holds the owed marks, so a kill
        before the ack recovers them from the file on the next tick.
        If the mint raised, this never runs and the marks stay owed.
        The caller saves after acking.
        """
        for event in events:
            entry = self._entry(self._identity(event))
            if entry is not None and entry["owed"]:
                entry["owed"] = False

    def observe(self, tick: int, action) -> bool:
        """Record one action-bearing waking tick.

        ``action`` is the tick's chosen action (the engine's
        ``last_intention`` value / ``heartbeat()`` result ``"action"``).
        A tick with no chosen action (``action`` None, or an empty
        string) records nothing and returns False. Otherwise always
        returns True: the log moved.
        """
        if action is None:
            return False
        action = str(action)
        if not action:
            return False
        if not _is_int(tick):
            raise ValueError(f"observe() needs an integer tick, got {tick!r}")
        if tick < 0:
            raise ValueError(f"observe() needs a non-negative tick, got {tick!r}")
        # No monotonicity guard here: the tick number comes from the
        # engine and strictly increases per heartbeat, and an observation
        # must never break a tick the pristine engine would complete
        # (personality.py discipline). Out-of-order rows in a loaded
        # file still fail loud at construction, above.
        self.data.append([tick, action])
        del self.data[:-LOG_CAP]
        return True

    def save(self) -> None:
        """Atomically persist the sidecar (temp file + os.replace + fsync).

        A crash mid-write leaves either the complete old body or the
        complete new body — never a truncated shell. The temp file lives
        in the same directory so the replace is an atomic rename. The
        body is deterministic: the log in tick order, the surfaced set
        sorted by (swing_tick, conv_tick, need), fixed form. A legacy
        bare-list file migrates to the dict shape on this save.
        """
        body = json.dumps(
            {"log": [list(row) for row in self.data],
             "surfaced": sorted(
                 ({"need": e["need"], "swing_tick": e["swing_tick"],
                   "conv_tick": e["conv_tick"], "owed": e["owed"]}
                  for e in self.surfaced),
                 key=lambda e: (e["swing_tick"], e["conv_tick"],
                                e["need"]))},
            ensure_ascii=False, indent=1)
        tmp = self.path.parent / (self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


def _record_text(record):
    """A record's first-person text, object or dict shaped."""
    if isinstance(record, dict):
        return record.get("first_person")
    return getattr(record, "first_person", None)


def _window(rows: list, *, before_tick=None, after_tick=None) -> list:
    """The WINDOW action-bearing rows nearest a boundary tick.

    ``before_tick``: the last WINDOW rows with tick < before_tick.
    ``after_tick``: the first WINDOW rows with tick > after_tick.
    Exactly one boundary is given.
    """
    if before_tick is not None:
        return [r for r in rows if r[0] < before_tick][-WINDOW:]
    return [r for r in rows if r[0] > after_tick][:WINDOW]


def report(subject, sidecar) -> dict:
    """Pure read of live store + sidecar: the Domain 22 report.

    ``sidecar`` is a ``FollowthroughTracker`` or a path to
    ``followthrough.json``. Realization records are found by scanning
    the live store with the canonical
    ``interoception.parse_realization_text`` parser; each yields one
    row. Nothing is written: calling this twice returns equal dicts
    and leaves the sidecar byte-identical.

    Returns ``{"tick", "rows": [...], "mean_tv", "measurable",
    "insufficient", "realizations", "observed_ticks"}``. A row is
    ``{"need", "swing_tick", "conv_tick", "felt_band", "actual_band",
    "status": "measured" | "insufficient-window", "before_mix",
    "after_mix", "tv", "before_count", "after_count"}``; for an
    insufficient-window row the mixes and tv are None and the counts
    say how much of each window the log actually held. ``mean_tv`` is
    None when no realization is measurable.
    """
    from .interoception import parse_realization_text

    tracker = (sidecar if isinstance(sidecar, FollowthroughTracker)
               else FollowthroughTracker(sidecar))
    rows = tracker.rows
    tick = subject.engine.state.tick
    realizations = []
    for record in subject.workspace.records:
        parsed = parse_realization_text(_record_text(record))
        if parsed is not None:
            realizations.append(parsed)
    realizations.sort(key=lambda p: (p["swing_tick"], p["conv_tick"],
                                     p["need"]))
    out_rows = []
    tvs = []
    for parsed in realizations:
        before = _window(rows, before_tick=parsed["swing_tick"])
        after = _window(rows, after_tick=parsed["conv_tick"])
        row = {
            "need": parsed["need"],
            "swing_tick": parsed["swing_tick"],
            "conv_tick": parsed["conv_tick"],
            "felt_band": parsed["felt_band"],
            "actual_band": parsed["actual_band"],
            "before_count": len(before),
            "after_count": len(after),
        }
        # A swing tick after the convergence tick can never come from the
        # engine (an episode opens in one iteration and can only close
        # in a later one), but hand-authored record text can carry it —
        # and then the before window (last 20 rows < swing) and the
        # after window (first 20 rows > conv) overlap, even coincide,
        # so any tv would be vacuous. Never certify that as measured:
        # swing == conv is fine (the windows are disjoint there), but
        # swing > conv is insufficient-window by construction.
        if (parsed["swing_tick"] > parsed["conv_tick"]
                or len(before) < WINDOW or len(after) < WINDOW):
            row.update({"status": "insufficient-window",
                        "before_mix": None, "after_mix": None, "tv": None})
        else:
            before_mix = _shares(before)
            after_mix = _shares(after)
            tv = total_variation(before_mix, after_mix)
            row.update({"status": "measured",
                        "before_mix": before_mix, "after_mix": after_mix,
                        "tv": tv})
            tvs.append(tv)
        out_rows.append(row)
    return {
        "tick": tick,
        "rows": out_rows,
        "mean_tv": (sum(tvs) / len(tvs) if tvs else None),
        "measurable": len(tvs),
        "insufficient": len(out_rows) - len(tvs),
        "realizations": len(out_rows),
        "observed_ticks": len(rows),
    }


# -- follow-through summaries: canonical text, parsing, minting ----------
# Follows the demotion-note precedent in authored_dormancy.py: the record
# text is the canonical serialization — the only writer is
# followthrough_summary_text(), the only reader is the anchored
# fullmatch below, so the identifier cannot drift from the writer.

def _fmt_mix(mix: dict) -> str:
    """An action-share vector in fixed form, sorted by action."""
    return "{" + ", ".join(f"{a}: {s:.6f}"
                           for a, s in sorted(mix.items())) + "}"


def followthrough_summary_text(row: dict) -> str:
    """First-person text for one newly-measurable realization.

    Factual only: the need, the swing and convergence ticks, the felt
    and actual bands, the before and after action mixes, and the
    total-variation distance — no interpretation, no promise, no
    judgment about whether the change is good. Two sentences: a prose
    sentence carrying the per-action deltas at two decimals, then a
    machine sentence carrying everything the fitness function needs to
    recover the measurement from the record alone, at six decimals.

    No contractions (the similarity vetoes tokenize on raw text —
    regression genome), no wall clock, rounded fixed-format floats:
    identical rows mint byte-identical text. A measured row always
    has non-empty mixes (both windows hold WINDOW rows).
    """
    before = row["before_mix"]
    after = row["after_mix"]
    clauses = []
    for action in sorted(set(before) | set(after)):
        b = before.get(action, 0.0)
        a = after.get(action, 0.0)
        if a > b:
            clauses.append(f"{action} rose from {b:.2f} to {a:.2f}")
        elif a < b:
            clauses.append(f"{action} fell from {b:.2f} to {a:.2f}")
        else:
            clauses.append(f"{action} stayed at {b:.2f}")
    return (
        f"After the {row['need']} realization (swing "
        f"{row['swing_tick']}, convergence {row['conv_tick']}), "
        f"{'; '.join(clauses)}; total-variation distance "
        f"{row['tv']:.2f}. "
        f"That is follow-through for {row['need']}, felt "
        f"{row['felt_band']}, actual {row['actual_band']}, swing "
        f"{row['swing_tick']}, convergence {row['conv_tick']}, before "
        f"{_fmt_mix(before)}, after {_fmt_mix(after)}, total-variation "
        f"distance {row['tv']:.6f}."
    )


# Canonical serialization identity for follow-through summaries.
# followthrough_summary_text() is the only writer; this anchored
# fullmatch is the only reader. It is built from this module's own
# template constants (NEED_LANGUAGE keys and FELT_BANDS), so the
# identifier cannot drift from the writer — a serialization-identity
# check, not a substring heuristic.
_SUMMARY_RE = re.compile(
    r"After the (" + "|".join(sorted(NEED_LANGUAGE)) + r") realization "
    r"\(swing (\d+), convergence (\d+)\), (.+?); total-variation distance "
    r"(\d+\.\d{2})\. "
    r"That is follow-through for (" + "|".join(sorted(NEED_LANGUAGE))
    + r"), felt (" + "|".join(FELT_BANDS) + r"), actual "
    r"(" + "|".join(FELT_BANDS) + r"), swing (\d+), convergence (\d+), "
    r"before \{(.*?)\}, after \{(.*?)\}, total-variation distance "
    r"(\d+\.\d{6})\.")

_MIX_PAIR_RE = re.compile(r"([\w-]+): (\d+\.\d{6})")
_CHANGE_RE = re.compile(
    r"([\w-]+) (rose|fell) from (\d+\.\d{2}) to (\d+\.\d{2})")
_STAY_RE = re.compile(r"([\w-]+) stayed at (\d+\.\d{2})")


def _deltas_agree(section: str, before_mix: dict, after_mix: dict
                  ) -> bool:
    """Whether the prose delta clauses match the machine mixes.

    Every action in the union of the mixes must appear exactly once,
    as a rose/fell clause whose two-decimal endpoints agree with the
    mixes within rounding, or a stayed-at clause when the shares are
    equal. A prose sentence that disagrees with its own machine
    sentence is not a summary of the measurement it claims.
    """
    actions = set(before_mix) | set(after_mix)
    seen = set()
    for clause in section.split("; "):
        change = _CHANGE_RE.fullmatch(clause)
        stay = _STAY_RE.fullmatch(clause) if change is None else None
        if change is not None:
            action = change.group(1)
            if action in seen or action not in actions:
                return False
            seen.add(action)
            b, a = float(change.group(3)), float(change.group(4))
            if change.group(2) == "rose" and not a > b:
                return False
            if change.group(2) == "fell" and not a < b:
                return False
            if (abs(before_mix.get(action, 0.0) - b) > 0.0051
                    or abs(after_mix.get(action, 0.0) - a) > 0.0051):
                return False
        elif stay is not None:
            action = stay.group(1)
            if action in seen or action not in actions:
                return False
            seen.add(action)
            x = float(stay.group(2))
            if abs(before_mix.get(action, 0.0)
                   - after_mix.get(action, 0.0)) > 1e-6:
                return False
            if (abs(before_mix.get(action, 0.0) - x) > 0.0051
                    or abs(after_mix.get(action, 0.0) - x) > 0.0051):
                return False
        else:
            return False
    return seen == actions


def _parse_mix(block: str) -> dict | None:
    """Parse a ``{action: share, ...}`` block, or None if malformed.

    Every pair must be ``name: 0.123456`` (six decimals, the writer's
    fixed form), each share in [0, 1], no duplicate names, and the
    shares must sum to 1.0 within rounding — a mix is a distribution,
    and a block that does not sum is not one.
    """
    if not block:
        return None
    pairs = _MIX_PAIR_RE.findall(block)
    if not pairs:
        return None
    # The whole block must be exactly the pairs, comma-separated —
    # nothing else may ride along.
    if ", ".join(f"{a}: {s}" for a, s in pairs) != block:
        return None
    mix = {}
    for action, share in pairs:
        if action in mix:
            return None
        value = float(share)
        if not 0.0 <= value <= 1.0:
            return None
        mix[action] = value
    if abs(sum(mix.values()) - 1.0) > 1e-4:
        return None
    return mix


def parse_followthrough_summary_text(text) -> dict | None:
    """Parse a follow-through summary's canonical text, or None.

    Returns the realization identity (need, swing_tick, conv_tick)
    plus the rendered bands, mixes, and tv. None means "not a
    follow-through summary" — never a partial match: the fullmatch
    anchors both ends, the prose and machine sentences must agree on
    need/swing/convergence and on the tv (two decimals vs six), the
    mixes must parse as distributions, and the tv must agree with the
    distance recomputed from the parsed mixes. The prose delta clauses
    must agree with the mixes too — a prose sentence that disagrees
    with its own machine sentence is not a summary of the measurement
    it claims.
    """
    if not isinstance(text, str):
        return None
    m = _SUMMARY_RE.fullmatch(text)
    if m is None:
        return None
    need1, need2 = m.group(1), m.group(6)
    swing1, swing2 = int(m.group(2)), int(m.group(9))
    conv1, conv2 = int(m.group(3)), int(m.group(10))
    if need1 != need2 or swing1 != swing2 or conv1 != conv2:
        return None
    tv_prose = float(m.group(5))
    tv = float(m.group(13))
    if abs(tv_prose - tv) > 0.0051:
        return None
    if not 0.0 <= tv <= 1.0:
        return None
    before_mix = _parse_mix(m.group(11))
    after_mix = _parse_mix(m.group(12))
    if before_mix is None or after_mix is None:
        return None
    if abs(total_variation(before_mix, after_mix) - tv) > 1e-5:
        return None
    if not _deltas_agree(m.group(4), before_mix, after_mix):
        return None
    return {"need": need1, "swing_tick": swing1, "conv_tick": conv1,
            "felt_band": m.group(7), "actual_band": m.group(8),
            "before_mix": before_mix, "after_mix": after_mix,
            "tv": tv}


def is_followthrough_summary_text(text) -> bool:
    """Whether a record text is a domain-27 follow-through summary."""
    return parse_followthrough_summary_text(text) is not None


def mint_followthrough_summary(subject, row: dict) -> str:
    """Append one follow-through summary through the subject's normal
    record path.

    Waking-tick path only (cli._run_tick); the caller owns the
    transaction so the record persists. Minting never advances the
    store tick and never touches the trace: it is a write inside the
    existing tick, not a new tick. Stamped generated_by="cognition"
    (engine-originated, not authored), and enters view eligibility,
    salience, and dedupe like any temporal record. Returns the record
    id.

    Idempotent (write-ahead crash discipline, cf. mint_demotion_note):
    when a summary for the same (need, swing_tick, conv_tick) is
    already in the store — e.g. an owed mark re-minted after a kill
    between the mint and the ack — the existing record's id is
    returned and nothing is appended: exactly one record per
    newly-measurable realization. The check reads
    subject.workspace.records, the in-memory list _add appends to, so
    mints earlier in the SAME transaction are visible.
    """
    for record in subject.workspace.records:
        if record.source != "temporal" or record.generated_by != "cognition":
            continue
        parsed = parse_followthrough_summary_text(record.first_person)
        if (parsed is not None
                and parsed["need"] == row["need"]
                and parsed["swing_tick"] == row["swing_tick"]
                and parsed["conv_tick"] == row["conv_tick"]):
            return record.id
    item = subject._add("temporal", followthrough_summary_text(row),
                        concepts=(row["need"],),
                        generated_by="cognition",
                        available_to_cognition=True,
                        salience=1.0, intensity=1.0)
    return item.id


def surface_newly_measurable(subject, tracker, rep=None) -> list:
    """Mint one summary record per newly-measurable realization.

    ``tracker`` is the FollowthroughTracker for the live sidecar.
    ``rep`` is a ``report()`` dict, computed here when omitted. The
    crash discipline mirrors the dormancy demotion note: the owed
    marks save BEFORE the mint transaction commits; the mint is
    idempotent on (need, swing_tick, conv_tick); the ack clears the
    marks only after the mint commits. A kill anywhere in the window
    recovers on the next waking tick with exactly one record per
    realization. Returns the list of summary events processed (each
    a report row); an empty list means nothing was due. Read-only
    commands and dream ticks never call this.
    """
    if rep is None:
        rep = report(subject, tracker)
    events, changed = tracker.due_summaries(rep)
    if changed:
        tracker.save()
    if events:
        with subject._transaction():
            for event in events:
                mint_followthrough_summary(subject, event)
        tracker.ack_summaries(events)
        tracker.save()
    return events
