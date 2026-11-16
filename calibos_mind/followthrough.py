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
"""
from __future__ import annotations

import json
from pathlib import Path

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
    LOG_CAP rows. The constructor only reads the sidecar; rows move
    exclusively via ``observe()`` + ``save()`` on the waking-tick path
    (``cli._run_tick``).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: list = []
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(
                    f"corrupt followthrough sidecar {self.path}: "
                    f"invalid JSON ({exc})"
                ) from exc
            if not isinstance(loaded, list):
                raise ValueError(
                    f"corrupt followthrough sidecar {self.path}: top "
                    f"level is not a list")
            prev_tick = None
            for row in loaded:
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
            self.data = [list(row) for row in loaded]

    @property
    def rows(self) -> list:
        """A copy of the [tick, action] log rows."""
        return [list(row) for row in self.data]

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
        """Write the sidecar deterministically (fixed form)."""
        self.path.write_text(
            json.dumps([list(row) for row in self.data],
                       ensure_ascii=False, indent=1),
            encoding="utf-8")


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
