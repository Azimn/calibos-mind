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
  byte-identical sidecar (exact replay is a standing invariant).
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
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jelly_psiduck.firewall import LOW_IS_BAD, NEED_LANGUAGE

BASELINE = 0.5            # physiological neutral; engine needs default here too
RATE_ONSET = 0.35         # felt chases a rising signal fast
RATE_OFFSET = 0.12        # a fading signal lags, like real interoception
NOISE_SCALE = 0.01        # seeded deterministic jitter per (tick, key)
DEFAULT_SEED = 0          # fixed: identical tick/need sequences replay byte-identical

# The engine's graded thresholds, reused — never forked.
THRESHOLDS = (0.45, 0.65, 0.85)
HYSTERESIS_MARGIN = 0.03  # engine's downward-flutter guard, same value

# Felt bands for `mind status` (exact floats only under --raw).
FELT_BANDS = ("settled", "stirring", "pressing", "urgent")
# Noise floor for the band display: felt within ±this of baseline is
# indistinguishable from seeded jitter — not a genuine departure, so no
# band renders.
#
# Derivation (deterministic; default seed 0; keys hunger/thirst/fatigue/energy
# pinned at the 0.5 baseline). At pinned baseline the update is an AR(1) in
# the felt value with phi = 1 - RATE_OFFSET = 0.88: move*displaced > 0 is
# false exactly at baseline, so the offset rate (0.12) always applies and
# the per-tick noise is uniform in [-NOISE_SCALE, +NOISE_SCALE]. Stationary
# sigma = (NOISE_SCALE/sqrt(3)) / sqrt(1 - 0.88^2) ≈ 0.0122. Measured
# maxima on the default seed: 0.0297 over the 25-tick fitness horizon and
# 0.0451 over 5000 ticks (steady state). The earlier 2*NOISE_SCALE = 0.02
# floor was ~1.64 sigma and did not bound this wander: it was breached on
# 11.35% of steady-state samples and rendered spurious bands on 236/500
# ticks of a pinned-baseline calm run. The floor is 5*NOISE_SCALE = 0.05
# (~4.1 sigma), comfortably above the measured long-horizon maximum; the
# same 500-tick calm run renders zero bands.
#
# What the floor provides (under the pinned default seed and validated
# horizons): on a pinned-baseline body, felt_bands() is empty — "all
# settled" means genuinely settled, not quiet-by-luck. It does not bound
# the jitter itself (an AR(1) is unbounded in principle — a sufficiently
# long same-sign noise run could theoretically exceed any fixed floor);
# it bounds the *displayed* flicker, so seeded noise never reads as a felt
# movement in `mind status` until a departure exceeds ~4 sigma of the
# stationary jitter.
BAND_NOISE_FLOOR = 5 * NOISE_SCALE

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


class InteroceptionTracker:
    """Felt body state. Local-only sidecar; read-only until update()+save()."""

    def __init__(self, path: str | Path, params: dict | None = None):
        self.path = Path(path)
        self.data: dict = {"needs": {}, "params": dict(DEFAULT_PARAMS),
                           "seed": DEFAULT_SEED}
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
            except (ValueError, OSError):
                pass
        if params is not None:
            merged = dict(DEFAULT_PARAMS)
            merged.update({k: v for k, v in params.items() if k in DEFAULT_PARAMS})
            self.data["params"] = merged

    # -- update ---------------------------------------------------------

    def update(self, actuals: dict[str, float], tick: int) -> None:
        """Chase actual need values with asymmetric lag + seeded noise.

        Call once per waking tick, after the engine heartbeat. Never on
        dream ticks. ``actuals`` maps need key -> actual float (e.g. from
        ``subject.engine.state.needs``); ``tick`` is the post-heartbeat tick.
        """
        needs = self.data["needs"]
        params = self.data["params"]
        seed = self.data["seed"]
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
            n = _noise(seed, tick, key, params["noise_scale"])
            felt = min(1.0, max(0.0, f + move * rate + n))
            entry["felt"] = round(felt, 6)
            entry["last_tick"] = tick
            entry["level"] = felt_level(felt, key, int(entry.get("level", 0)))

    def reset(self) -> None:
        """Clear the sidecar (used when the store is reseeded; ids restart)."""
        self.data = {"needs": {}, "params": dict(DEFAULT_PARAMS),
                     "seed": DEFAULT_SEED}
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
        ordered = {"needs": {k: self.data["needs"][k]
                             for k in sorted(self.data["needs"])},
                   "params": self.data["params"],
                   "seed": self.data["seed"]}
        self.path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1),
                             encoding="utf-8")
