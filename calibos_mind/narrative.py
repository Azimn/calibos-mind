"""Bidirectional confidence revision for narrative claims (engine overlay).

The snapshot engine's ``SubjectEngine._maybe_reflect``
(``digital_subject/engine.py``) ratchets narrative-claim confidence
monotonically: ``max(old, new) + 0.02``, strictly non-decreasing, saturating
at 1.0. No decay, no revision path. Observed live: ``pattern:comfort`` at 1.0
since tick 144; ``pattern:social`` at 1.0 while the propositional state is
"unsettled" about it — narrative state contradicting the live reading with no
mechanism to ever correct itself.

The engine lives in the snapshot venv (untracked, rebuildable, deliberately
immune to repo branch moves), so it is not edited. Instead this module
patches ``SubjectEngine._maybe_reflect`` at import time — the same overlay
precedent as ``calibos_mind/projection.py``. It is imported by
``calibos_mind/__init__.py`` on package load, so every engine consumer
(``Organism`` subclasses ``SubjectEngine`` without overriding
``_maybe_reflect``) picks up the fixed version. Revertible by deleting this
module and its import line; the venv stays byte-identical.

What changes (contract: proposals/2026-10-10-reflect-bidirectional-confidence.md):

1. Signed updates. The update branch no longer does ``max(old, new) + 0.02``.
   When the fresh evidence basis (average valence of the repeated-tag
   memories) agrees in valence class with the stored claim, confidence moves
   *toward* the evidence mass ``clamp(0.35 + n * 0.08)`` — an asymptote, never
   an overshoot, so agreement alone cannot ratchet past the evidence. When the
   basis disagrees in class, the contradiction trigger fires instead (below).
2. Slow decay toward uncertainty. At each reflection-interval gate, every
   claim *not* rehearsed at that gate drifts toward ``UNCERTAINTY`` (0.5).
   Decay is time-based from ``claim.updated_tick`` (``DECAY_PER_TICK **
   elapsed``), so skipped or forced gates neither lose nor double-count decay.
   The decay floor is 0.5 explicitly: the formula is symmetric, pulling
   below-0.5 confidences *up* toward 0.5, never down through it.
3. Contradiction trigger. When the live ``_narrative_sentence(tag, avg)``
   output materially disagrees with the stored proposition — i.e. the
   evidence-basis valence class (positive / negative / unsettled, using the
   sentence's own ±0.15 thresholds) differs from the claim's class — the claim
   is *revised*, not left to coexist with its contradiction: the proposition
   is rewritten from the live sentence, the valence is set to the new basis
   average, and confidence is cut halfway toward 0.5.

What is kept byte-for-byte in behavior: the mint path for new claims
(including the "I formed a conclusion about my life" memory), the
>=3-repeated-tag gate, the reflection-interval gate, and valence averaging
for agreeing evidence.

Deploy-day note: claims that have not been rehearsed for a long time
(the fossils) will decay toward 0.5 on the first reflection gate after this
lands — that is the specified behavior, not a bug. If that proves too abrupt
in practice, the revert signal ("decay untunable") covers it.
"""
from __future__ import annotations

import uuid

from digital_subject.engine import SubjectEngine, clamp, clamp_signed
from digital_subject.models import Memory, NarrativeClaim

# -- tuning constants (documented; the fitness tests pin their behavior) -----

#: Uncertainty attractor and explicit decay floor for claim confidence.
UNCERTAINTY = 0.5

#: Per-tick retention applied to (confidence - UNCERTAINTY) for unrehearsed
#: claims. 0.999 -> half-life ~693 ticks: slow against the reflection
#: interval (12 ticks), bounded well under the narrative-memory strength
#: half-life (~3466 ticks), not geological time.
DECAY_PER_TICK = 0.999

#: Fraction of the remaining distance to the evidence mass closed per
#: agreeing update. Agreement approaches the mass asymptotically; it can
#: never overshoot it, so there is no ratchet above the evidence.
AGREE_PULL = 0.10

#: Retention of (confidence - UNCERTAINTY) when the contradiction trigger
#: fires: confidence is cut halfway toward 0.5 (1.0 -> 0.75 in one update).
CONTRADICTION_CUT = 0.5

#: Valence-class thresholds, mirroring _narrative_sentence exactly
#: (> 0.15 positive, < -0.15 negative, else unsettled).
SENTENCE_THRESHOLD = 0.15


def _valence_class(valence: float) -> str:
    if valence > SENTENCE_THRESHOLD:
        return "safer"
    if valence < -SENTENCE_THRESHOLD:
        return "risk"
    return "unsettled"


def _decay(confidence: float, elapsed_ticks: int) -> float:
    """Drift confidence toward UNCERTAINTY over elapsed_ticks.

    Symmetric: above-0.5 confidences fall toward 0.5, below-0.5 confidences
    rise toward it. 0.5 is an attractor and an explicit floor, never crossed.
    """
    if elapsed_ticks <= 0:
        return confidence
    return UNCERTAINTY + (confidence - UNCERTAINTY) * (DECAY_PER_TICK ** elapsed_ticks)


def maybe_reflect_bidirectional(self, force: bool = False) -> None:
    # Interval gate and minimum-evidence gate: unchanged from the engine.
    if not force and self.state.tick % self.cartridge.reflection_interval != 0:
        return
    recent = self.state.memories[-24:]
    if len(recent) < 3:
        return
    tag_scores: dict[str, list] = {}
    for memory in recent:
        for tag in memory.tags:
            tag_scores.setdefault(tag, []).append(memory)
    repeated = [
        (tag, memories) for tag, memories in tag_scores.items() if len(memories) >= 3
    ]

    winner_key: str | None = None
    winner: tuple[str, list] | None = None
    if repeated:
        tag, memories = max(
            repeated,
            key=lambda item: (len(item[1]), sum(m.emotional_charge for m in item[1])),
        )
        winner = (tag, memories)
        winner_key = f"pattern:{tag}"

    # Decay (interval gates only): every claim not rehearsed at this gate
    # drifts toward uncertainty. Time-based from updated_tick, so a forced
    # gate that skips decay loses nothing — the next interval gate applies
    # the full elapsed span.
    if not force:
        for key, claim in self.state.narrative.items():
            if key == winner_key:
                continue
            claim.confidence = _decay(claim.confidence, self.state.tick - claim.updated_tick)

    if winner is None:
        return
    tag, memories = winner
    # Basis valence derived from the claim's evidential basis: the memories
    # carrying the repeated tag in the current evidence window.
    avg = sum(self._memory_valence(m) for m in memories) / len(memories)
    proposition = self._narrative_sentence(tag, avg)
    key = f"pattern:{tag}"
    evidence = tuple(m.id for m in memories[-5:])
    current = self.state.narrative.get(key)
    evidence_mass = clamp(0.35 + len(memories) * 0.08)
    if current:
        if _valence_class(avg) != _valence_class(current.valence):
            # Contradiction trigger: the live reading materially disagrees
            # with the stored proposition (polarity flip, settling, or
            # unsettling). Revise the claim — rewrite the proposition from
            # the live sentence, rebase the valence on the new evidence,
            # cut confidence toward uncertainty.
            current.proposition = proposition
            current.valence = clamp_signed(avg)
            current.confidence = (
                UNCERTAINTY + (current.confidence - UNCERTAINTY) * CONTRADICTION_CUT
            )
        else:
            # Agreeing evidence: confidence approaches the evidence mass
            # asymptotically (no overshoot, no ratchet); valence averaged
            # as in the engine.
            current.confidence = clamp(
                current.confidence + AGREE_PULL * (evidence_mass - current.confidence)
            )
            current.valence = clamp_signed((current.valence + avg) / 2)
        current.evidence_memory_ids = evidence
        current.updated_tick = self.state.tick
    else:
        # Mint path: unchanged from the engine.
        self.state.narrative[key] = NarrativeClaim(
            key, proposition, evidence_mass, clamp_signed(avg), evidence, self.state.tick
        )
        self._store_memory(
            Memory(
                str(uuid.uuid4()),
                proposition,
                f"I formed a conclusion about my life: {proposition}",
                ("narrative", tag),
                0.75,
                abs(avg),
                self.state.tick,
                self.state.tick,
                kind="narrative",
            )
        )


_ORIGINAL_MAYBE_REFLECT = SubjectEngine._maybe_reflect


def install() -> None:
    """Activate the overlay (idempotent). Called at package import."""
    SubjectEngine._maybe_reflect = maybe_reflect_bidirectional


def uninstall() -> None:
    """Restore the frozen engine's original (for before/after checks)."""
    SubjectEngine._maybe_reflect = _ORIGINAL_MAYBE_REFLECT


install()
