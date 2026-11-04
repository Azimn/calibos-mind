"""Slow affective residue ("mood tone") — a diffuse mood with a cause.

Domain 10 (Affect and emotion), 2026-11-03: "No diffuse mood without
identifiable cause" was absent — this mind had no mood state at all (all
live records carried ``affect=[]``). A mood that is *causeless* would be
theater and must not be installed. A mood as *residue* is not: a
slow-moving background state that outlasts its trigger is exactly what
"diffuse mood" means mechanistically, and it is load-bearing only if it
changes what is recalled, not merely what is labeled. So the residue
changes only the view-time salience ranking (a flat +0.5, same scale as
STATE_CONGRUENT_BOOST), never mints records or writes text.

Design rules (mirroring statecongruence.py):
- Sidecar ``mood.json`` (next to ``interoception.json``): per-pressure
  slow EMA ("residue") over the deviation of each engine pressure from
  its baseline (``engine.pressure_baselines``: trust, attachment, shame,
  fear, anger, arousal, self_story_stability). Baselines are read from
  the engine at update time, never hardcoded here; a pressure with no
  baseline reads against 0.0, the engine's own fallback.
- Update: once per waking tick (``cli._run_tick``), the same waking-only
  seam the feeder, interoception update, and habit machinery use:
  ``residue = residue * (1 - ALPHA) + ALPHA * deviation``. Read-only
  commands and ``dream_tick()`` never call ``update()`` or ``save()``.
- ALPHA is declared from a half-life, never tuned silently:
  MOOD_HALF_LIFE_TICKS = 100 (the fast end of the spec's ~100-200 tick
  range, so a sustained ~30-tick full excursion leaves a detectable
  residue: 1 - (1-ALPHA)^30 = 0.188, above the 0.15 threshold) and
  ALPHA = 1 - 0.5**(1/100). After ~1000 baseline ticks the residue has
  decayed by a factor of ~0.001, back below threshold.
- A pressure that leaves the engine's pressure set is treated as back
  at baseline (deviation 0) and decays like any other settled pressure,
  so a residue can never freeze above threshold for a vanished pressure
  (phantom-residue guard, same fallback the engine itself uses).
- View-time gain: the pure function ``mood_residue_boost_for`` returns a
  flat +0.5 when ANY concept of the record names a pressure whose
  residue is at or above MOOD_THRESHOLD **and** whose fast deviation is
  back near baseline (|dev| <= FAST_SETTLED) — i.e. the mood fires only
  after the trigger has settled, so it cannot duplicate the
  state-congruent gain during a live spike. One pressure or three, one
  concept match or several — exactly +0.5, never scaled.
- Corrupt sidecar fails loud: invalid JSON, a non-dict top level, a
  non-dict residues section, or a non-numeric/non-finite residue raises
  ValueError at construction. Never silent-delete, never silent-zero.
  (``cmd_init --force`` wipes the sidecar before construction, so a
  corrupt file can never brick a reseed.)
- Determinism: identical tick/pressure sequences yield byte-identical
  sidecars and windows. No clock, no RNG in the update or gain path.
- Malformed provider input (None, non-mapping, non-numeric residues or
  deviations) yields 0.0, never a crash inside view construction; a
  pressure with no fast deviation on record cannot satisfy the
  fast-settled clause, so it never fires (fail closed).
- The gain only reorders the salience-ranked sort key, before dedupe
  and per-class caps run — a boosted record can never bypass either.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

# Flat retrieval nudge for mood-residue records. Same scale as
# STATE_CONGRUENT_BOOST: meaningful against live activations (roughly
# -2.7 .. +3.2) without being dominant. Gains may stack per existing
# precedent (self, familiarity, state-congruent, mood).
MOOD_BOOST = 0.5

# Residue at or above this fires (given a settled fast deviation).
# Pinned with the half-life below: a 30-tick full excursion (deviation
# 1.0) reaches 0.188, clearing it with margin, while a brief blip does
# not — the residue must be earned by sustained deviation.
MOOD_THRESHOLD = 0.15

# Fast deviation within this of baseline counts as "settled": the trigger
# has passed and only the residue remains. Above it, the live-spike
# state-congruent machinery owns the moment and the mood stays silent.
FAST_SETTLED = 0.05

# Slow-EMA half-life in ticks. Declared, not tuned: ALPHA derives from
# it exactly, and the derivation is tested (measured half-life == 100
# ticks). 100 is the fast end of the spec's ~100-200 range: slow enough
# to outlast any single spike by an order of magnitude, fast enough
# that a day-scale baseline stretch clears it.
MOOD_HALF_LIFE_TICKS = 100
ALPHA = 1.0 - 0.5 ** (1.0 / MOOD_HALF_LIFE_TICKS)


def _is_number(value) -> bool:
    """A genuine finite number: bools (an int subclass) never count."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def mood_residue_boost_for(record, residues, fast_deviations) -> float:
    """Flat retrieval nudge for a mood-residue record.

    Returns MOOD_BOOST when ANY concept of ``record`` names a pressure
    whose slow residue in ``residues`` is at or above MOOD_THRESHOLD
    **and** whose fast deviation in ``fast_deviations`` is back near
    baseline (|dev| <= FAST_SETTLED), else 0.0. Reads
    ``record.concepts`` only — no sidecar, no state, no writes, no
    clock, no RNG.

    None-safe and malformed-safe: None (no provider), a non-mapping
    input, or a mapping with no usable numeric entries yields 0.0 by
    definition. Non-numeric entries are skipped per pressure (fail
    closed: a corrupt residue can never mint a boost, and a pressure
    with no fast deviation can never satisfy the settled clause). A
    record with no concepts, or concepts of the wrong type, never
    matches.
    """
    if residues is None or fast_deviations is None:
        return 0.0
    try:
        residue_items = residues.items()
    except AttributeError:
        return 0.0
    try:
        fast_items = fast_deviations.items()
    except AttributeError:
        return 0.0
    clean_residues = {}
    for key, value in residue_items:
        if _is_number(value):
            clean_residues[key] = float(value)
    if not clean_residues:
        return 0.0
    clean_fast = {}
    for key, value in fast_items:
        if _is_number(value):
            clean_fast[key] = float(value)
    concepts = getattr(record, "concepts", None)
    if concepts is None:
        return 0.0
    try:
        return MOOD_BOOST if any(
            c in clean_residues
            and clean_residues[c] >= MOOD_THRESHOLD
            and c in clean_fast
            and abs(clean_fast[c]) <= FAST_SETTLED
            for c in concepts) else 0.0
    except TypeError:
        # Concepts of an uniterable type, or unhashable entries: fail
        # closed, never a silent match.
        return 0.0


class MoodTracker:
    """Slow affective residue sidecar (``mood.json``, local-only).

    ``data`` is ``{"residues": {pressure: float}}``. The constructor
    only reads the sidecar; residues move exclusively via ``update()``
    + ``save()`` on the waking-tick path (``cli._run_tick``).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"residues": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(
                    f"corrupt mood sidecar {self.path}: invalid JSON ({exc})"
                ) from exc
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"corrupt mood sidecar {self.path}: top level is not "
                    f"an object")
            residues = loaded.get("residues", {})
            if not isinstance(residues, dict):
                raise ValueError(
                    f"corrupt mood sidecar {self.path}: 'residues' is not "
                    f"an object")
            for key, value in residues.items():
                if not _is_number(value):
                    raise ValueError(
                        f"corrupt mood sidecar {self.path}: residue for "
                        f"{key!r} is not a finite number")
            self.data["residues"] = {k: float(v)
                                     for k, v in residues.items()}

    @property
    def residues(self) -> dict:
        """A copy of the current residues (callers never mutate state)."""
        return dict(self.data["residues"])

    def update(self, pressures, baselines) -> bool:
        """Fold one waking tick's pressure deviations into the residues.

        ``pressures`` are the engine's current pressures,
        ``baselines`` its ``pressure_baselines`` — both read from the
        engine by the caller, never hardcoded here. A key missing from
        ``pressures`` reads as its baseline (deviation 0), so residues
        for vanished pressures decay rather than freeze. Non-dict
        inputs read as empty; non-numeric values are skipped, never
        defaulted. Returns True when any residue moved (the caller's
        no-op write discipline: save only on change).
        """
        if not isinstance(pressures, dict):
            pressures = {}
        if not isinstance(baselines, dict):
            baselines = {}
        changed = False
        keys = (set(pressures) | set(baselines)
                | set(self.data["residues"]))
        for key in sorted(keys, key=str):
            base = baselines.get(key, 0.0)
            current = pressures.get(key, base)
            if not _is_number(current) or not _is_number(base):
                continue
            deviation = float(current) - float(base)
            old = self.data["residues"].get(key)
            new = (old * (1.0 - ALPHA) + ALPHA * deviation
                   if old is not None else ALPHA * deviation)
            if old is None or new != old:
                changed = True
            self.data["residues"][key] = new
        return changed

    def save(self) -> None:
        """Write the sidecar deterministically (sorted keys, fixed form)."""
        ordered = {"residues": {k: self.data["residues"][k]
                                for k in sorted(self.data["residues"],
                                                key=str)}}
        self.path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1),
                             encoding="utf-8")
