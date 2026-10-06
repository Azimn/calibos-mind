"""Interoceptive gap: a felt body that chases the actual body with lag.

Real interoception is laggy and noisy. The engine's graded interoception
(``EndogenousSubject._project_body``) computes level 0-3 text from EXACT need
floats, so the thinker never gets to be wrong about its own body. This module
inserts the epistemic gap the taxonomy's Domain 1 demands: a felt float per
need that chases the actual value with asymmetric lag (onset fast, offset
slow) plus seeded deterministic noise.

Design rules:
- Update: once per waking tick, after ``subject.heartbeat()`` (see
  ``cli._run_tick``). Never on dream ticks — the body is frozen in sleep,
  so the felt body freezes with it. The felt machine therefore trails the
  engine by one tick inside prompt views: the view built during tick ``t``
  carries felt values from ``t-1``. That is lag, not staleness — the update
  is strictly post-tick, and the gap is the feature.
- Direction: onset when felt moves away from the 0.5 baseline toward the
  actual (``(a - f) * (f - 0.5) > 0``), offset otherwise. A swing *through*
  baseline first un-feels the old state slowly, then feels the new one
  fast — an interoceptive aftereffect, not a bug. From exact baseline any
  movement is onset (there is nothing to return from).
- Noise: ``noise_scale * (h - 0.5) * 2`` with
  ``h = sha256(f"{seed}:{tick}:{key}")`` as a float in [0, 1). No ``random``
  module state, no wall clock, no UUIDs. Same tick/need sequence ->
  byte-identical sidecar (exact replay is a standing invariant). The
  effective scale is strain-scaled per tick (domain 11, 2026-10-05):
  ``noise_scale * (1 + STRAIN_NOISE_K * weariness(actuals))`` — a rested
  body (weariness 0) sees exactly the pristine scale; an exhausted one
  sees up to 3x.
- View substitution: ``CalibosWorkspace.view()`` re-renders body-derived
  interoception records (``source == "interoception"`` with a need key in
  ``concepts``) from FELT urgency using the engine's graded vocabulary and
  hysteresis thresholds (.45/.65/.85) — same language, fallible source.
  Interoception records without a need key (recall unease, concern
  influence, prospective uncertainty) and every other source pass through
  byte-identical: there is no felt value for them, and inventing one would
  be a silent default.
- The tracker is a sidecar (``interoception.json``, local-only, gitignored).
  Engine records are never mutated; the workspace view just re-renders.
- Cognition never sees the machinery: only the re-rendered (source,
  first_person) view leaves this module. No floats or ticks cross into the
  rendered prompt; record ids travel in the prompt payload's private
  provenance only (for the --silent join), never in the prompt text the
  thinker reads.
- Delayed emotional realization (domain 10, 2026-10-04): when a real
  felt/actual gap opens (|felt - actual| > 0.25) and later converges
  (|felt - actual| <= 0.05), update() returns one realization event per
  closed swing→convergence episode. cli._run_tick mints it as a
  ``temporal`` record through the subject's normal record-append path
  (generated_by="cognition") — the memory of having misread oneself, not
  installed emotion. Episodes are per-need sidecar state ("realization"
  section), so a swing survives until it converges; needs that never
  diverge mint nothing. Minting is a write inside the existing tick, never
  a new tick, and never fires on dream ticks or from read-only commands
  (they never call update()).
- Crash safety (2026-10-04): the close is write-ahead durable — update()
  marks the episode closed_tick and saves the sidecar BEFORE the event
  leaves, so a kill between the mint and the cli's save() can neither
  double-mint (mint_realization skips when a realization for the same
  need+swing_tick already exists) nor lose the record (the next tick
  re-takes the mark via take_pending_closings() and re-mints
  idempotently). Exactly one record per closed episode.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from jelly_psiduck.firewall import LOW_IS_BAD, NEED_LANGUAGE

from calibos_mind.friction import weariness

BASELINE = 0.5            # physiological neutral; engine needs default here too
RATE_ONSET = 0.35         # felt chases a rising signal fast
RATE_OFFSET = 0.12        # a fading signal lags, like real interoception
NOISE_SCALE = 0.01        # seeded deterministic jitter per (tick, key)
DEFAULT_SEED = 0          # fixed: identical tick/need sequences replay byte-identical

# Strain-scaled noise (domain 11, 2026-10-05): the felt-body noise grows
# with bodily weariness (friction.weariness over the same tick's
# actuals). Per-tick effective scale = NOISE_SCALE * (1 + K * weariness),
# K = 2: up to 3x at full exhaustion; weariness 0 -> scale exactly 1, so
# rested runs replay byte-identical to the pristine code. Tired organisms
# misread their bodies; the thinker is untouched — only the interoceptive
# signal gets noisier under strain. weariness is a pure function of the
# actuals, so replay stays deterministic.
STRAIN_NOISE_K = 2.0

# The engine's graded thresholds, reused — never forked.
THRESHOLDS = (0.45, 0.65, 0.85)
HYSTERESIS_MARGIN = 0.03  # engine's downward-flutter guard, same value

# Felt bands for `mind status` (exact floats only under --raw).
FELT_BANDS = ("settled", "stirring", "pressing", "urgent")
# Noise floor for the band display: felt within ±this of baseline is
# indistinguishable from seeded jitter — not a genuine departure, so no
# band renders.
#
# Derivation (deterministic; default seed 0; hunger/thirst/energy pinned
# at the 0.5 baseline, fatigue 0.0 / focus 1.0 so bodily weariness is 0 —
# a rested body, i.e. the pristine noise scale). At pinned baseline the
# update is an AR(1) in the felt value with phi = 1 - RATE_OFFSET = 0.88:
# move*displaced > 0 is false exactly at baseline, so the offset rate
# (0.12) always applies and the per-tick noise is uniform in
# [-NOISE_SCALE, +NOISE_SCALE]. Stationary sigma = (NOISE_SCALE/sqrt(3))
# / sqrt(1 - 0.88^2) ≈ 0.0122. Measured maxima on the default seed:
# 0.0297 over the 25-tick fitness horizon and 0.0451 over 5000 ticks
# (steady state). The earlier 2*NOISE_SCALE = 0.02 floor was ~1.64 sigma
# and did not bound this wander: it was breached on 11.35% of steady-state
# samples and rendered spurious bands on 236/500 ticks of a pinned-baseline
# calm run. The floor is 5*NOISE_SCALE = 0.05 (~4.1 sigma), comfortably
# above the measured long-horizon maximum; the same 500-tick calm run
# renders zero bands.
#
# What the floor provides (under the pinned default seed and validated
# horizons): on a pinned-baseline body, felt_bands() is empty — "all
# settled" means genuinely settled, not quiet-by-luck. It does not bound
# the jitter itself (an AR(1) is unbounded in principle — a sufficiently
# long same-sign noise run could theoretically exceed any fixed floor);
# it bounds the *displayed* flicker, so seeded noise never reads as a felt
# movement in `mind status` until a departure exceeds ~4 sigma of the
# stationary jitter.
#
# Strain interaction (domain 11, 2026-10-05): the derivation above assumes
# the pristine noise scale, i.e. weariness 0 (a rested body). Under strain
# the effective scale grows (up to 3x), so a tired body pinned at baseline
# can wander past the floor and render bands — that is the mutation
# working (tired organisms misread their bodies), not a defect. The floor
# is deliberately NOT strain-scaled: scaling the display threshold up with
# weariness would hide exactly the misreading the taxonomy checks demand.
BAND_NOISE_FLOOR = 5 * NOISE_SCALE

# Realization episodes (domain 10: delayed emotional realization).
# A swing starts when a real felt/actual gap first exceeds SWING_GAP; the
# episode closes — one realization record minted — at the first tick where
# the gap returns to CONVERGE_GAP or below. Gaps in between are ordinary
# lag, not misreadings worth remembering. Both thresholds are module
# constants (not sidecar params): they define the phenomenon, not its
# tuning.
SWING_GAP = 0.25
CONVERGE_GAP = 0.05

DEFAULT_PARAMS = {
    "rate_onset": RATE_ONSET,
    "rate_offset": RATE_OFFSET,
    "noise_scale": NOISE_SCALE,
}


def _noise(seed: int, tick: int, key: str, scale: float) -> float:
    """Deterministic jitter in [-scale, +scale] for one (tick, key)."""
    digest = hashlib.sha256(f"{seed}:{tick}:{key}".encode("utf-8")).digest()
    u = int.from_bytes(digest[:8], "big") / 2**64
    return scale * (u - 0.5) * 2


def urgency(key: str, value: float) -> float:
    """Engine's urgency mapping, reused: 1 - value for LOW_IS_BAD keys."""
    return 1 - value if key in LOW_IS_BAD else value


def felt_level(felt_value: float, key: str, previous_level: int) -> int:
    """Graded level 0-3 from a felt value, with the engine's hysteresis."""
    u = urgency(key, felt_value)
    level = sum(u >= t for t in THRESHOLDS)
    if (level < previous_level
            and u > THRESHOLDS[previous_level - 1] - HYSTERESIS_MARGIN):
        level = previous_level
    return level


def felt_text(key: str, level: int) -> str:
    """The engine's interoception vocabulary rendered from a felt level.

    Steady-state form: the rising texts double as the current-state
    rendering (level 2 is the bare description), and level 0 keeps the
    engine's own "easing" text. Same words the body would use — driven by
    what is felt, not what is actual.
    """
    description = NEED_LANGUAGE[key]
    if level <= 0:
        return "That feeling is easing."
    if level == 1:
        return f"I am beginning to notice this: {description}"
    if level == 2:
        return description
    return f"It is hard to think past this: {description}"


def _read_swing(raw) -> dict:
    """Compat-read one sidecar's open realization episodes.

    The "realization" section is new (2026-10-04); sidecars written before
    it carry no section and read as no open episodes — the same defensive
    pattern the needs/params/seed reads already use. A malformed section
    (wrong types) is dropped entry-by-entry, never trusted: a stale or
    corrupt swing must never mint a phantom realization or attach to a
    recycled need.

    Two semantic guards beyond the type checks (2026-10-04 critic round 2):

    - ``gap_max <= SWING_GAP`` entries are dropped. An open episode only
      opens past SWING_GAP and gap_max only ever grows, so such an entry
      is impossible — corrupt-but-well-typed, and trusting it would mint a
      phantom realization for a gap that never exceeded 0.25 (the
      mutation's own revert signal). The writer preserves the strict
      ``gap_max > SWING_GAP`` invariant exactly (see update()), so the
      drop cannot eat a legitimate episode.
    - A well-typed ``closed_tick`` marker is preserved: it is update()'s
      write-ahead mark for a close whose mint may not have happened yet
      (crash between mint and save). Dropping it would lose the record;
      re-closing it would double-mint. take_pending_closings() disposes it.
    """
    swing: dict = {}
    if not isinstance(raw, dict):
        return swing
    entries = raw.get("swing")
    if not isinstance(entries, dict):
        return swing
    for key, value in entries.items():
        if not (isinstance(value, dict)
                and isinstance(value.get("swing_tick"), int)
                and not isinstance(value.get("swing_tick"), bool)
                and isinstance(value.get("swing_felt"), (int, float))
                and isinstance(value.get("swing_actual"), (int, float))
                and isinstance(value.get("gap_max"), (int, float))):
            continue
        if float(value["gap_max"]) <= SWING_GAP:
            # Impossible for a real open episode: it only opens past
            # SWING_GAP and gap_max never shrinks. Corrupt entry — drop.
            continue
        entry = {"swing_tick": value["swing_tick"],
                 "swing_felt": float(value["swing_felt"]),
                 "swing_actual": float(value["swing_actual"]),
                 "gap_max": float(value["gap_max"])}
        closed_tick = value.get("closed_tick")
        if isinstance(closed_tick, int) and not isinstance(closed_tick, bool):
            entry["closed_tick"] = closed_tick
        swing[key] = entry
    return swing


class InteroceptionTracker:
    """Felt body state. Local-only sidecar; read-only until update()+save()."""

    def __init__(self, path: str | Path, params: dict | None = None):
        self.path = Path(path)
        self.data: dict = {"needs": {}, "params": dict(DEFAULT_PARAMS),
                           "seed": DEFAULT_SEED,
                           "realization": {"swing": {}}}
        # Close-pending write-ahead marks (2026-10-04 critic round 2):
        # episodes whose close was durably marked but whose mint may not
        # have happened (kill between mint and save). Populated from the
        # file at load and by update() at close time; take_pending_closings
        # takes from it, ack_closings clears it after a successful mint.
        self._pending_marks: dict = {}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    needs = loaded.get("needs")
                    self.data["needs"] = needs if isinstance(needs, dict) else {}
                    params = loaded.get("params")
                    if isinstance(params, dict):
                        merged = dict(DEFAULT_PARAMS)
                        merged.update({k: v for k, v in params.items()
                                       if k in DEFAULT_PARAMS})
                        self.data["params"] = merged
                    seed = loaded.get("seed")
                    if isinstance(seed, int):
                        self.data["seed"] = seed
                    self.data["realization"] = {"swing": _read_swing(
                        loaded.get("realization"))}
                    # Mirror file marks into the in-memory swing (so the
                    # loaded state shows them and update() skips them) and
                    # register them pending.
                    for key, entry in self.data["realization"]["swing"].items():
                        if "closed_tick" in entry:
                            self._pending_marks[key] = entry
            except (ValueError, OSError):
                pass
        if params is not None:
            merged = dict(DEFAULT_PARAMS)
            merged.update({k: v for k, v in params.items() if k in DEFAULT_PARAMS})
            self.data["params"] = merged

    # -- update ---------------------------------------------------------

    def update(self, actuals: dict[str, float], tick: int) -> list[dict]:
        """Chase actual need values with asymmetric lag + seeded noise.

        Call once per waking tick, after the engine heartbeat. Never on
        dream ticks. ``actuals`` maps need key -> actual float (e.g. from
        ``subject.engine.state.needs``); ``tick`` is the post-heartbeat tick.

        Returns the realization episodes closed on this tick: one dict per
        need whose felt/actual gap opened past SWING_GAP on an earlier tick
        and has now returned to CONVERGE_GAP — i.e. a real self-misreading
        that has resolved. Each dict carries ``need``, ``swing_tick``,
        ``swing_felt``, ``swing_actual``, ``gap_max``, and ``conv_tick``.
        The caller (cli._run_tick) mints one record per event through the
        subject's normal record-append path. Needs that never diverge close
        nothing; separate swing→convergence episodes for one need each
        close exactly one event.
        """
        needs = self.data["needs"]
        params = self.data["params"]
        seed = self.data["seed"]
        swing = self.data["realization"]["swing"]
        # Strain-scaled noise (domain 11): this tick's effective noise
        # scale grows with bodily weariness (pure function of the same
        # actuals — deterministic, replay-exact). Computed once per tick,
        # not per need: weariness is a body-level property.
        noise_scale_eff = params["noise_scale"] * (
            1.0 + STRAIN_NOISE_K * weariness(actuals))
        closed: list[dict] = []
        for key in sorted(actuals):
            a = float(actuals[key])
            entry = needs.get(key)
            if entry is None:
                # First contact: the felt body starts settled at baseline,
                # then chases from there. Documented, not a silent default —
                # the engine's own needs default to 0.5.
                entry = {"felt": BASELINE, "last_tick": tick, "level": 0}
                needs[key] = entry
            f = float(entry["felt"])
            move = a - f
            displaced = f - BASELINE
            onset = (move * displaced > 0) or (displaced == 0 and move != 0)
            rate = params["rate_onset"] if onset else params["rate_offset"]
            n = _noise(seed, tick, key, noise_scale_eff)
            felt = round(min(1.0, max(0.0, f + move * rate + n)), 6)
            entry["felt"] = felt
            entry["last_tick"] = tick
            entry["level"] = felt_level(felt, key, int(entry.get("level", 0)))
            # -- realization episodes (domain 10) ----------------------
            # The gap is measured on the rounded felt value actually
            # stored, so replay is exact. An open episode persists in the
            # sidecar until it converges — or until `reset()` wipes it
            # (reseed restarts episodes; stale swings never attach to
            # recycled state). A close is write-ahead durable: the episode
            # is marked closed_tick and saved BEFORE the event leaves
            # update(), so a kill between the mint and the cli's save()
            # can neither double-mint (mint_realization is idempotent on
            # need+swing_tick) nor lose the record (take_pending_closings
            # re-mints the mark on the next tick). Reversing the order
            # (save-then-mint) would trade the double-mint for a missed
            # realization and is not used.
            gap = abs(felt - a)
            episode = swing.get(key)
            if episode is None:
                if gap > SWING_GAP:
                    gap_max = round(gap, 6)
                    if gap_max <= SWING_GAP:
                        # 6-decimal rounding can pull a true gap in
                        # (0.25, 0.2500005] down to exactly 0.25; the
                        # compat-read drops gap_max <= SWING_GAP as
                        # impossible, so preserve the open-episode
                        # invariant exactly rather than rounding a real
                        # episode into the corrupt bucket.
                        gap_max = round(SWING_GAP + 1e-6, 6)
                    swing[key] = {"swing_tick": tick,
                                  "swing_felt": felt,
                                  "swing_actual": round(a, 6),
                                  "gap_max": gap_max}
            elif "closed_tick" in episode:
                # Close-pending-ack (loaded from a pre-mint crash): not an
                # open episode — no gap_max tracking, no re-close, no new
                # opening on this slot. The cli re-mints it via
                # take_pending_closings(); until then the slot is held.
                pass
            else:
                if gap > episode["gap_max"]:
                    episode["gap_max"] = round(gap, 6)
                if gap <= CONVERGE_GAP:
                    closed.append({"need": key,
                                   "swing_tick": episode["swing_tick"],
                                   "swing_felt": episode["swing_felt"],
                                   "swing_actual": episode["swing_actual"],
                                   "gap_max": episode["gap_max"],
                                   "conv_tick": tick})
                    episode["closed_tick"] = tick
                    self.save()
                    # Dropped from the in-memory swing only: the file keeps
                    # the mark until the cli's post-mint save() overwrites
                    # it, so a second update() in the same tick cannot
                    # re-close, while a pre-mint crash still recovers the
                    # mark from the file. Registered pending so a mid-tick
                    # mint failure is retried, not lost.
                    self._pending_marks[key] = episode
                    del swing[key]
        return closed

    def take_pending_closings(self) -> list[dict]:
        """Take write-ahead close marks for (re-)minting.

        Returns one event per close-pending episode — same shape as
        update()'s closed events — and forgets them: a second call returns
        []. The marks live in memory (populated from the file at load and
        by update() at close time), so this also covers a mid-tick mint
        failure — update() drops the in-memory swing entry at close, but
        the mark survives here until it is taken. After the caller mints
        the returned events it must call ack_closings(); until then the
        file still holds the marks (update()'s write-ahead save), so a
        kill before the mint loses nothing. A mark whose mint already
        committed is harmless: mint_realization() skips it idempotently.
        """
        swing = self.data["realization"]["swing"]
        events: list[dict] = []
        for key in sorted(self._pending_marks):
            entry = self._pending_marks[key]
            events.append({"need": key,
                           "swing_tick": entry["swing_tick"],
                           "swing_felt": entry["swing_felt"],
                           "swing_actual": entry["swing_actual"],
                           "gap_max": entry["gap_max"],
                           "conv_tick": entry["closed_tick"]})
            swing.pop(key, None)  # drop the load-time mirror, if any
        self._pending_marks.clear()
        return events

    def ack_closings(self, events: list[dict]) -> None:
        """Forget close marks whose events were minted.

        Called after the mint transaction commits: the file still holds
        the write-ahead marks until the caller's save() overwrites it, so
        forgetting them here is safe — a kill before that save() recovers
        the marks from the file, and the re-mint is idempotent. If the
        mint raised, this never runs: the in-memory marks are gone with
        this process, but the file still holds the write-ahead marks, so
        the next process's take_pending_closings() recovers them from the
        file (every CLI command is a fresh process).
        """
        for event in events:
            key = event["need"]
            entry = self._pending_marks.get(key)
            if (entry is not None
                    and entry["swing_tick"] == event["swing_tick"]):
                del self._pending_marks[key]

    def reset(self) -> None:
        """Clear the sidecar (used when the store is reseeded; ids restart).

        Open realization episodes die with the reseed: a swing opened on
        the old body must never converge into a record on the new one.
        """
        self.data = {"needs": {}, "params": dict(DEFAULT_PARAMS),
                     "seed": DEFAULT_SEED,
                     "realization": {"swing": {}}}
        self._pending_marks = {}
        self.save()

    # -- reads (view substitution, status) -------------------------------

    def need_key(self, record) -> str | None:
        """The body need a record speaks for, or None.

        Only engine body interoceptions carry the need key in concepts;
        recall-unease / concern / prospective-uncertainty interoceptions do
        not, and must pass through substitution untouched.
        """
        for concept in getattr(record, "concepts", ()) or ():
            if concept in NEED_LANGUAGE:
                return concept
        return None

    def text_for_record(self, record) -> str | None:
        """Felt-derived rendering for an interoception record, or None.

        None means "no felt state to render from" — the caller passes the
        record through byte-identical. Never a quiet zero or a guess.
        """
        key = self.need_key(record)
        if key is None:
            return None
        entry = self.data["needs"].get(key)
        if entry is None:
            return None
        return felt_text(key, int(entry.get("level", 0)))

    def felt_bands(self) -> dict[str, str]:
        """Felt urgency bands for `mind status`: only needs felt off-baseline.

        "Off-baseline" means beyond the noise floor — not `level > 0`.
        Level 1 begins at urgency 0.45, so an exactly-at-baseline need
        (urgency 0.5) reads level 1 even though nothing is genuinely felt;
        filtering on the level would render a calm body as all-"stirring"
        and `mind status` could never print "all settled". The graded
        level/hysteresis computation itself is untouched — this filter only
        decides display.
        """
        return {key: FELT_BANDS[int(e.get("level", 0))]
                for key, e in sorted(self.data["needs"].items())
                if abs(float(e.get("felt", BASELINE)) - BASELINE) > BAND_NOISE_FLOOR}

    # -- persistence ------------------------------------------------------

    def save(self) -> None:
        swing = self.data["realization"]["swing"]
        ordered = {"needs": {k: self.data["needs"][k]
                             for k in sorted(self.data["needs"])},
                   "params": self.data["params"],
                   "realization": {"swing": {k: swing[k]
                                            for k in sorted(swing)}},
                   "seed": self.data["seed"]}
        self.path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1),
                             encoding="utf-8")


def realization_band(value: float, key: str, *, floored: bool) -> str:
    """Felt-band vocabulary for one side of a realization record.

    Reuses the tracker's own math: the graded felt_level() thresholds, and
    the noise floor for the *felt* side (floored=True — seeded jitter is
    not a genuine departure, so a felt value inside ±BAND_NOISE_FLOOR of
    baseline renders "settled", exactly as felt_bands() does). The actual
    side is engine-exact and never floored.
    """
    if floored and abs(value - BASELINE) <= BAND_NOISE_FLOOR:
        return FELT_BANDS[0]
    return FELT_BANDS[felt_level(value, key, 0)]


def realization_text(event: dict) -> str:
    """First-person text for one closed swing→convergence episode.

    Names the need, the swing tick, the felt band at the swing, the actual
    band, and the convergence tick — everything the fitness function needs
    to recover the episode from the record alone. No contractions (the
    similarity vetoes tokenize on raw text), no floats, no wall clock:
    identical episodes mint byte-identical text.
    """
    need = event["need"]
    felt_band = realization_band(event["swing_felt"], need, floored=True)
    actual_band = realization_band(event["swing_actual"], need, floored=False)
    return (f"At tick {event['swing_tick']} I felt {need} as {felt_band}, "
            f"but my body was only {actual_band}; "
            f"by tick {event['conv_tick']} the feeling caught up.")


# Canonical serialization identity for realization records.
# realization_text() is the only writer; this anchored fullmatch is the
# only reader. It is built from this module's own template constants
# (FELT_BANDS and the NEED_LANGUAGE keys), so the identifier cannot drift
# from the writer — a serialization-identity check, not a substring
# heuristic. Used by consolidation (realization-aware supersede guard) and
# by mint_realization's idempotency check.
_REALIZATION_RE = re.compile(
    r"At tick (\d+) I felt (" + "|".join(sorted(NEED_LANGUAGE)) + r") as "
    r"(" + "|".join(FELT_BANDS) + r"), but my body was only "
    r"(" + "|".join(FELT_BANDS) + r"); by tick (\d+) the feeling caught up\.")


def parse_realization_text(text) -> dict | None:
    """Parse a realization record's canonical text, or None.

    Returns the episode identity (need, swing_tick) plus the rendered
    bands and convergence tick. None means "not a realization record" —
    never a partial match: the fullmatch anchors both ends.
    """
    if not isinstance(text, str):
        return None
    m = _REALIZATION_RE.fullmatch(text)
    if m is None:
        return None
    return {"need": m.group(2), "swing_tick": int(m.group(1)),
            "felt_band": m.group(3), "actual_band": m.group(4),
            "conv_tick": int(m.group(5))}


def is_realization_text(text) -> bool:
    """Whether a record text is a domain-10 realization record."""
    return parse_realization_text(text) is not None


def mint_realization(subject, event: dict) -> str:
    """Append one realization record through the subject's normal record path.

    Waking-tick path only (cli._run_tick); the caller owns the transaction
    so the record persists. Minting never advances the store tick and never
    touches the trace: it is a write inside the existing tick, not a new
    tick. Stamped generated_by="cognition" (engine-originated, not
    authored), and enters view eligibility, salience, and dedupe like any
    temporal record. Returns the record id.

    Idempotent (2026-10-04 critic round 2): when a realization for the same
    (need, swing_tick) is already in the store — e.g. a write-ahead close
    mark re-minted after a kill between the mint and the sidecar save —
    the existing record's id is returned and nothing is appended: exactly
    one record per closed episode. The check reads
    subject.workspace.records, the in-memory list _add appends to, so mints
    earlier in the SAME transaction are visible — no read-your-own-write
    hazard.
    """
    for record in subject.workspace.records:
        if record.source != "temporal" or record.generated_by != "cognition":
            continue
        parsed = parse_realization_text(record.first_person)
        if (parsed is not None and parsed["need"] == event["need"]
                and parsed["swing_tick"] == event["swing_tick"]):
            return record.id
    weight = min(1.0, max(0.0, float(event["gap_max"])))
    item = subject._add("temporal", realization_text(event),
                        concepts=(event["need"],),
                        generated_by="cognition",
                        available_to_cognition=True,
                        salience=weight, intensity=weight)
    return item.id
