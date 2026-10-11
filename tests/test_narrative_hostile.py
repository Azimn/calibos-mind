"""Critic's hostile cases for calibos_mind/narrative.py (round 1).

Each test is an attack: it fails if the overlay violates the contract or the
regression genome. Synthetic /tmp-free engines only; no live store touched.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind import narrative as nov
from digital_subject.cartridge import load_cartridge
from digital_subject.engine import SubjectEngine
from digital_subject.models import Memory

BASE = Path("/home/hatch/workspace/calibos-mind")
CARTRIDGE = load_cartridge(BASE / "calibos.toml")
INTERVAL = CARTRIDGE.reflection_interval

POS = ("kindness", "praise", "promise", "repair", "success", "comfort")
NEG = ("betrayal", "accusation", "ignored", "failure", "pain", "threat")


def _engine():
    return SubjectEngine.from_cartridge(CARTRIDGE, subject_id="synth-critic-001")


def _mem(tag, tick, vtag=None, charge=0.8):
    tags = (tag,) if vtag is None else (tag, vtag)
    return Memory(str(uuid.uuid4()), "synthetic event", "synthetic event",
                  tags, 0.5, charge, tick, tick)


def _pos(tag, tick, n=3, charge=0.8):
    return [_mem(tag, tick, POS[i % len(POS)], charge) for i in range(n)]


def _neg(tag, tick, n=3, charge=0.8):
    return [_mem(tag, tick, NEG[i % len(NEG)], charge) for i in range(n)]


def _gate(engine, tick):
    engine.state.tick = tick
    assert tick % INTERVAL == 0
    engine._maybe_reflect()


def _claim(engine, tag):
    return engine.state.narrative[f"pattern:{tag}"]


def _valence_of_sentence(tag, avg):
    # Ground truth: the engine's own sentence thresholds.
    e = _engine()
    s = e._narrative_sentence(tag, avg)
    if avg > 0.15:
        assert "safer or more worthwhile" in s
    elif avg < -0.15:
        assert "risk or hurt" in s
    else:
        assert "not settled" in s
    return s


def test_h1_oscillating_evidence_converges_not_thrashes():
    """Alternate polarity every gate for 40 gates. Confidence must not
    oscillate wildly (contract revert signal: instability under the regime);
    ambiguous evidence should pin confidence near uncertainty, monotonically
    approaching it, never below 0.5 or above the pre-oscillation peak."""
    engine = _engine()
    engine.state.memories = _pos("comfort", 0, n=4, charge=0.9)
    _gate(engine, INTERVAL)
    peak = _claim(engine, "comfort").confidence
    confs = []
    for i in range(2, 42):
        tick = INTERVAL * i
        makers = _pos if i % 2 == 0 else _neg
        engine.state.memories = makers("comfort", tick, n=4, charge=0.9)
        _gate(engine, tick)
        c = _claim(engine, "comfort").confidence
        confs.append(c)
        assert 0.5 - 1e-9 <= c <= peak + 1e-9, (i, c)
    # End state: pinned near uncertainty, not thrashing at the extremes.
    assert confs[-1] < 0.6, confs[-5:]
    # No wild single-gate swings late in the oscillation.
    late_swings = [abs(b - a) for a, b in zip(confs[-6:], confs[-5:])]
    assert all(s < 0.05 for s in late_swings), late_swings


def test_h2_single_contradictory_memory_cannot_nuke_claim():
    """One bad memory among agreeing ones must dilute, not detonate: the
    ≥3-tag gate and net-valence basis should absorb it without firing the
    contradiction trigger."""
    engine = _engine()
    engine.state.memories = _pos("comfort", 0, n=5, charge=0.9)
    _gate(engine, INTERVAL)
    claim = _claim(engine, "comfort")
    claim.confidence = 0.9
    claim.valence = 0.8
    before_prop = claim.proposition
    # 4 agreeing + 1 strongly contradicting memory, same tag.
    mems = _pos("comfort", INTERVAL, n=4, charge=0.9)
    mems.append(_mem("comfort", INTERVAL, NEG[0], charge=1.0))
    engine.state.memories = mems
    _gate(engine, 2 * INTERVAL)
    claim = _claim(engine, "comfort")
    # Net basis still positive: no trigger, proposition intact.
    assert claim.proposition == before_prop
    assert claim.confidence > 0.5
    assert claim.valence > 0.15


def test_h3_paraphrase_without_meaning_change_never_triggers_cut():
    """The trigger must compare valence classes, not sentence strings.
    Feed the claim its own sentence's paraphrase-equivalent evidence
    repeatedly: identical meaning must never cause a proposition rewrite
    or a confidence cut."""
    engine = _engine()
    engine.state.memories = _pos("comfort", 0, n=4, charge=0.85)
    _gate(engine, INTERVAL)
    claim = _claim(engine, "comfort")
    claim.confidence = 0.9
    prop0 = claim.proposition
    rewrites = 0
    for i in range(2, 12):
        tick = INTERVAL * i
        # Same tag, same polarity class, slightly different charge mix —
        # the live sentence is always the same "safer" sentence.
        engine.state.memories = _pos("comfort", tick, n=4,
                                     charge=0.7 + 0.05 * (i % 3))
        _gate(engine, tick)
        claim = _claim(engine, "comfort")
        if claim.proposition != prop0:
            rewrites += 1
    assert rewrites == 0, "paraphrase-equivalent evidence rewrote the proposition"
    # The manually inflated 0.9 correctly converges DOWN toward the evidence
    # mass (0.67) — agreement tracks evidence in both directions. It must
    # approach the mass asymptotically, never overshoot below it.
    mass = min(1.0, 0.35 + 4 * 0.08)
    assert mass - 1e-9 <= claim.confidence <= 0.9, claim.confidence


def test_h4_ten_k_tick_consistent_soak_is_stable():
    """10k ticks of consistent agreeing evidence: the contract's revert
    signal is 'claims unstable under consistent evidence'. Confidence must
    converge to the evidence mass and stay there — no drift, no decay
    fighting the rehearsal, updated_tick tracking the last gate."""
    engine = _engine()
    engine.state.memories = _pos("comfort", 0, n=5, charge=0.9)
    _gate(engine, INTERVAL)
    mass = min(1.0, 0.35 + 5 * 0.08)
    last = None
    for i in range(2, 834):  # ~10k ticks at INTERVAL=12
        tick = INTERVAL * i
        engine.state.memories = _pos("comfort", tick, n=5, charge=0.9)
        _gate(engine, tick)
        c = _claim(engine, "comfort").confidence
        assert 0.5 <= c <= mass + 1e-9, (i, c)
        last = c
    assert abs(last - mass) < 1e-6, (last, mass)
    assert _claim(engine, "comfort").updated_tick == INTERVAL * 833


def test_h5_boundary_straddle_does_not_corrupt():
    """Evidence oscillating exactly across the ±0.15 class boundary is the
    pathological case for a class-based trigger. It must degrade gracefully
    (confidence → 0.5) without corrupting the proposition or valence, and
    the trigger must never 'revise' the proposition to identical text."""
    engine = _engine()
    # avg valence +0.16 vs +0.14 alternate: charge 0.16/0.14 via POS tag
    # with tuned charge. _memory_valence returns ±charge for POS/NEG tags.
    engine.state.memories = _pos("comfort", 0, n=4, charge=0.8)
    _gate(engine, INTERVAL)
    claim = _claim(engine, "comfort")
    claim.confidence = 0.9
    for i in range(2, 22):
        tick = INTERVAL * i
        charge = 0.16 if i % 2 == 0 else 0.14
        engine.state.memories = _pos("comfort", tick, n=4, charge=charge)
        _gate(engine, tick)
        claim = _claim(engine, "comfort")
        assert 0.5 - 1e-9 <= claim.confidence <= 0.9 + 1e-9
        assert -1.0 <= claim.valence <= 1.0
    # Graceful degradation: ambiguity drives confidence toward uncertainty.
    assert _claim(engine, "comfort").confidence < 0.75


def test_h6_trigger_rewrite_always_changes_sentence_class():
    """Lockstep invariant: whenever the contradiction trigger fires (valence
    rebased + confidence cut), the stored proposition's sentence class must
    actually change. A 'revision' to identical text would be a spurious cut."""
    engine = _engine()
    engine.state.memories = _pos("comfort", 0, n=4, charge=0.9)
    _gate(engine, INTERVAL)

    def sclass(prop):
        if "safer or more worthwhile" in prop:
            return "safer"
        if "risk or hurt" in prop:
            return "risk"
        return "unsettled"

    violations = []
    # Drive through all six class transitions.
    seq = [(_neg, "risk"), (_pos, "safer"), (_neg, "risk")]
    for makers, _ in seq:
        for i in range(3):
            tick = engine.state.tick + INTERVAL
            engine.state.memories = makers("comfort", tick, n=4, charge=0.9)
            claim = _claim(engine, "comfort")
            before = (sclass(claim.proposition), claim.confidence)
            _gate(engine, tick)
            claim = _claim(engine, "comfort")
            after = (sclass(claim.proposition), claim.confidence)
            # Detect a trigger firing: confidence cut toward 0.5 while the
            # claim was rehearsed (updated_tick == tick).
            if claim.updated_tick == tick and after[1] < before[1] - 1e-9:
                # A cut happened — was it the contradiction cut? Check the
                # proposition class actually changed.
                if after[0] == before[0]:
                    violations.append((tick, before, after))
    assert not violations, violations


def test_h7_decay_never_crosses_floor_from_above_or_below():
    """Direct unit attack on _decay: for a sweep of confidences and large
    elapsed spans, 0.5 is an attractor and is never crossed in either
    direction; huge elapsed underflows gracefully to exactly 0.5."""
    for conf in (1.0, 0.99, 0.75, 0.51, 0.5, 0.49, 0.25, 0.01, 0.0):
        for elapsed in (1, 12, 693, 10000, 10**7):
            out = nov._decay(conf, elapsed)
            if conf > 0.5:
                assert 0.5 <= out <= conf, (conf, elapsed, out)
            elif conf < 0.5:
                assert conf <= out <= 0.5, (conf, elapsed, out)
            else:
                assert out == 0.5
    assert nov._decay(1.0, 10**9) == 0.5
    assert nov._decay(0.0, 10**9) == 0.5
    assert nov._decay(0.8, 0) == 0.8
    assert nov._decay(0.8, -5) == 0.8


def test_h8_forced_gate_does_not_forgive_decay_debt_silently():
    """A forced gate (high-intensity event) rehearses the winner — its
    updated_tick legitimately advances. But non-winner claims must NOT lose
    their accrued decay: the next interval gate must apply the full elapsed
    span from their (old) updated_tick."""
    engine = _engine()
    engine.state.memories = _pos("comfort", 0, n=4, charge=0.9)
    _gate(engine, INTERVAL)
    engine.state.memories = _pos("safety", 0, n=4, charge=0.9)
    _gate(engine, 2 * INTERVAL)
    # Both claims minted; comfort's updated_tick is INTERVAL (old).
    comfort = _claim(engine, "comfort")
    comfort.confidence = 0.9
    comfort_tick = comfort.updated_tick
    # Forced gate at a non-interval tick rehearsing a DIFFERENT tag.
    engine.state.tick = 2 * INTERVAL + 5
    engine.state.memories = _pos("safety", engine.state.tick, n=4, charge=0.9)
    engine._maybe_reflect(force=True)
    # comfort was not rehearsed: its updated_tick must be untouched...
    assert _claim(engine, "comfort").updated_tick == comfort_tick
    # ...and the next interval gate applies decay over the FULL elapsed span.
    _gate(engine, 3 * INTERVAL)
    expected = 0.5 + (0.9 - 0.5) * (nov.DECAY_PER_TICK ** (3 * INTERVAL - comfort_tick))
    assert _claim(engine, "comfort").confidence == expected


def test_h9_uninstall_fully_restores_original_ratchet():
    """Revert path: uninstall() must restore the exact original behavior —
    the old max()+0.02 ratchet — proving the overlay is cleanly removable
    and the venv was never the thing that changed."""
    engine = _engine()
    engine.state.memories = _pos("comfort", 0, n=4, charge=0.9)
    _gate(engine, INTERVAL)
    claim = _claim(engine, "comfort")
    claim.confidence = 0.9
    claim.valence = 0.8
    claim.updated_tick = INTERVAL

    nov.uninstall()
    try:
        assert SubjectEngine._maybe_reflect is nov._ORIGINAL_MAYBE_REFLECT
        engine.state.memories = _pos("comfort", INTERVAL, n=4, charge=0.9)
        _gate(engine, 2 * INTERVAL)
        claim = _claim(engine, "comfort")
        # Original ratchet: max(0.9, mass) + 0.02, clamped.
        mass = min(1.0, 0.35 + 4 * 0.08)
        assert claim.confidence == min(1.0, max(0.9, mass) + 0.02)
    finally:
        nov.install()
    assert SubjectEngine._maybe_reflect is nov.maybe_reflect_bidirectional
