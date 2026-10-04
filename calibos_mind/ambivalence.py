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

Standing-tie consolidation (2026-10-03): a near-tie that persists across
ticks (e.g. one attachment-vs-thirst tie held for 28 ticks) used to emit
one marker per tick — 28 near-identical events for a single standing
condition, tripping the mutation's own ">50% of ticks" noise revert
signal. Consecutive per-tick markers for the same standing near-tie are
now consolidated into runs:
- ``contested_marker`` stays pure and per-tick: it is the ground truth
  for contested/not-contested, and the consolidation must never
  contradict it at any tick.
- The wrapper keeps an in-memory open run per subject: the
  (contenders, channels) pair, onset tick, last tick, margin min/max.
  A tick that would emit a marker with a matching pair and a still-
  contested margin extends the run instead of emitting.
- The first tick of a run emits an onset marker: today's seven-field
  marker schema is unmodified; the eighth field, ``"run": "onset"``, is
  additive and documented (the ``margin`` field is the run's initial
  margin). When the run ends — clear margin, contender/channel change,
  or channel-rule bypass — a multi-tick run emits an offset marker
  naming onset tick, offset tick (the first tick that was not a
  continuation of the run), last contested tick, and margin min/max.
  A one-tick run emits nothing further: the onset marker stands alone
  as the run's exactly-one marker ("onset immediately followed by
  nothing"), so isolated near-ties keep today's seven fields intact.
- The full drift signal (onset tick, offset tick, margin min/max) is
  recoverable from the marker stream alone; the stream stays a faithful
  event log. A multi-tick run's offset never names a lost onset: the
  staged pending buffer survives the per-transaction tracker swap
  (``CalibosSubject._restore`` carries it onto the fresh tracker), so
  an onset staged mid-tick is flushed by the tick's own flush and the
  stream never carries an orphan offset.
- Run state is in-memory on the subject (NOT the tracker or the wrapper
  closure: ``_restore`` swaps in a fresh engine and tracker on every
  transaction, so subject-level state is the only surface that survives
  a tick boundary). On process restart mid-tie the next contested tick
  starts a fresh run — the earlier onset marker is already in the
  sidecar, so the contest is never lost, only its continuity across the
  restart. Read-only commands and dream ticks never create, extend, or
  close runs.

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


class _OpenRun:
    """In-memory consolidation state for one standing near-tie.

    A run opens on the first contested tick for a (contenders, channels)
    pair and absorbs every following contested tick with the same pair
    while the margin stays inside the deadband. It closes on the first
    tick that is not a continuation: clear margin, contender/channel
    change, or channel-rule bypass. Deliberately never persisted — see
    the module docstring for the cross-restart caveat.
    """

    __slots__ = ("contenders", "channels", "onset_tick", "last_tick",
                 "margin_min", "margin_max", "absorbed")

    def __init__(self, *, contenders, channels, tick, margin):
        self.contenders = tuple(contenders)
        self.channels = tuple(channels)
        self.onset_tick = int(tick)
        self.last_tick = int(tick)
        self.margin_min = float(margin)
        self.margin_max = float(margin)
        self.absorbed = 1

    @property
    def key(self):
        return (self.contenders, self.channels)

    def extend(self, tick, margin):
        # Ticks advance monotonically (engine._advance_time only moves
        # forward), so last_tick is simply the latest absorbed tick.
        self.last_tick = int(tick)
        self.margin_min = min(self.margin_min, float(margin))
        self.margin_max = max(self.margin_max, float(margin))
        self.absorbed += 1


# Subject attribute holding the open run. Stored on the subject — not on
# the tracker and not in the wrapper closure — because _restore swaps in
# a fresh engine instance and a fresh AmbivalenceTracker on every
# transaction: subject-level state is the only in-memory surface that
# survives a tick boundary. install_observer must never reset it.
_RUN_ATTR = "_ambivalence_open_run"


def open_run(subject):
    """Return the subject's current open contested run, or None.

    Read-only accessor for tests and auditing; the wrapper mutates run
    state only through ``_absorb_contested`` / ``_close_open_run``.
    """
    return getattr(subject, _RUN_ATTR, None)


def _absorb_contested(subject, tracker, marker):
    """Fold one contested marker into the open run, or open a new one.

    Emits the onset marker (today's seven fields plus the additive
    ``"run": "onset"``) on the first tick of a run and suppresses the
    per-tick marker while the run continues. When the (contenders,
    channels) pair changes, the old run is closed first (offset marker)
    and the new run opens immediately after, so the marker stream never
    drops a contested tick.

    Staging order is deliberate: the onset is noted on the tracker
    BEFORE the run is recorded on the subject. If ``note()`` fails (the
    observer wrapper swallows the exception), no run may survive —
    otherwise the next contested tick would silently extend a run whose
    onset never reached the stream.
    """
    key = (tuple(marker["contenders"]), tuple(marker["channels"]))
    margin = marker["margin"]
    run = getattr(subject, _RUN_ATTR, None)
    if (run is not None and run.key == key
            # The marker was just minted by contested_marker, so it is
            # contested by construction; re-check on the rounded margin
            # with <= because a raw margin in [0.11995, 0.12) rounds to
            # exactly 0.12 while still being inside the deadband — a <
            # check would spuriously close the run there.
            and abs(margin) <= CONTESTED_MARGIN):
        run.extend(marker["tick"], margin)
        return
    if run is not None:
        _close_open_run(subject, tracker, tick=marker["tick"],
                        event_kind=marker["event"], reason="changed")
    onset = dict(marker)
    onset["run"] = "onset"
    tracker.note(onset)
    setattr(subject, _RUN_ATTR, _OpenRun(
        contenders=marker["contenders"], channels=marker["channels"],
        tick=marker["tick"], margin=margin))


def _close_open_run(subject, tracker, *, tick, event_kind, reason):
    """Close the open run, emitting the offset marker when due.

    A one-tick run emits nothing further: its onset marker stands alone
    as the run's exactly-one marker. A multi-tick run emits an offset
    marker naming onset tick, offset tick (the first tick that was not a
    continuation of the run — for a contender change this coincides with
    the new run's onset tick), last contested tick, and margin min/max.
    ``reason`` is "clear", "changed", or "bypass", naming what ended it.

    Staging order is deliberate, mirroring ``_absorb_contested``: the
    offset is noted on the tracker BEFORE the run is cleared on the
    subject. If ``note()`` fails, the run stays open and a later close
    emits one offset for the whole span — never a dropped run silently
    replaced by a fresh one.
    """
    run = getattr(subject, _RUN_ATTR, None)
    if run is None:
        return
    if tracker is None:
        # No sidecar target: nothing can be staged — clear the run so no
        # state lingers, exactly as before.
        setattr(subject, _RUN_ATTR, None)
        return
    if run.last_tick > run.onset_tick:
        tracker.note({
            "tick": int(tick),
            "event": str(event_kind),
            "contenders": list(run.contenders),
            "channels": list(run.channels),
            "run": "offset",
            "reason": reason,
            "onset_tick": run.onset_tick,
            "offset_tick": int(tick),
            "last_tick": run.last_tick,
            "margin_min": round(run.margin_min, 4),
            "margin_max": round(run.margin_max, 4),
        })
    setattr(subject, _RUN_ATTR, None)


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
    action or any engine state. Per-tick markers for a standing near-tie
    are consolidated into runs (see the module docstring); the run state
    lives on the subject so it survives the per-transaction _restore.

    Idempotent: safe to call again after ``_restore`` swaps in a fresh
    engine instance (every ``_transaction`` does), and a no-op if the
    current instance is already observed. Re-installation never touches
    the open run — only the wrapper's per-tick decisions do.
    """
    engine = subject.engine
    original = engine._choose_intention
    if getattr(original, "_ambivalence_wrapped", False):
        return

    @functools.wraps(original)
    def _observed_choose_intention(event, needs, pressures):
        action = original(event, needs, pressures)
        try:
            event_kind = getattr(event, "kind", "?")
            # Mirror the frozen engine's channel-rule bypass
            # (engine.py:337-338): for these event kinds the engine returned
            # without running the channel rule, so no channel selection
            # was contested — noting a marker would fabricate a "winning
            # channel" the engine never computed. A bypass tick also ends
            # any open run: the standing tie was not observed this tick.
            if event_kind in CHANNEL_RULE_BYPASS_KINDS:
                _close_open_run(
                    subject,
                    getattr(subject.workspace, "ambivalence_tracker", None),
                    tick=engine.state.tick, event_kind=event_kind,
                    reason="bypass")
                return action
            if not getattr(subject, "_dreaming", False):
                tracker = getattr(subject.workspace, "ambivalence_tracker", None)
                if tracker is not None:
                    marker = contested_marker(
                        tick=engine.state.tick,
                        event_kind=event_kind,
                        needs=needs,
                        pressures=pressures,
                    )
                    if marker is not None:
                        _absorb_contested(subject, tracker, marker)
                    else:
                        _close_open_run(subject, tracker,
                                        tick=engine.state.tick,
                                        event_kind=event_kind, reason="clear")
        except Exception:
            # Observation is strictly subordinate to the tick: a marker
            # computation failure must never break a tick the pristine
            # engine would have completed. A missed marker is always the
            # safe direction.
            pass
        return action

    _observed_choose_intention._ambivalence_wrapped = True
    engine._choose_intention = _observed_choose_intention
