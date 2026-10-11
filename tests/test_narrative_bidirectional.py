"""Tests for bidirectional narrative-claim confidence (calibos_mind/narrative.py).

Contract: proposals/2026-10-10-reflect-bidirectional-confidence.md.
Overlay patch on SubjectEngine._maybe_reflect; the snapshot venv is untouched.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python -m pytest tests/test_narrative_bidirectional.py -q
All fixtures are synthetic and live in /tmp — the live store is never touched.

Fitness function (contract):
 1. A claim at 1.0 subjected to contradicting evidence decreases within <=5 updates.
 2. Absent rehearsal, confidence decays toward 0.5 over a bounded tick horizon.
 3. No new ratchet: mixed-evidence soak — no claim reaches 1.0 and remains
    there despite contradictory inputs.
 4. Full existing suite stays green (run separately: whole tests/ directory).
 5. Blinded-region check: carrying-list / host-feeder / merger-write fixtures
    behave identically with the patch installed vs uninstalled.

Regression genome:
 - dream/conduct isolation: narrative-claim state identical before/after
   dream ticks (scoped to narrative state only).
 - no read-only command gains a write path: debug_snapshot() leaves narrative
   state untouched.
 - decay floor is 0.5 explicitly, never a quiet zero (also: below-0.5
   confidences decay *up* toward 0.5).
"""
from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind import narrative as narrative_overlay
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject
from digital_subject.cartridge import load_cartridge
from digital_subject.engine import SubjectEngine
from digital_subject.models import Memory, NarrativeClaim

BASE = Path(__file__).resolve().parents[1]
CARTRIDGE = load_cartridge(BASE / "calibos.toml")
INTERVAL = CARTRIDGE.reflection_interval

POS_TAGS = ("kindness", "praise", "promise", "repair", "success", "comfort")
NEG_TAGS = ("betrayal", "accusation", "ignored", "failure", "pain", "threat")


def _engine():
    return SubjectEngine.from_cartridge(CARTRIDGE, subject_id="synth-narrative-001")


def _mem(tag, tick, valence_tag=None, charge=0.8, summary="synthetic event"):
    tags = (tag,) if valence_tag is None else (tag, valence_tag)
    return Memory(str(uuid.uuid4()), summary, summary, tags, 0.5, charge, tick, tick)


def _pos_mems(tag, tick, n=3, charge=0.8):
    return [_mem(tag, tick, POS_TAGS[i % len(POS_TAGS)], charge) for i in range(n)]


def _neg_mems(tag, tick, n=3, charge=0.8):
    return [_mem(tag, tick, NEG_TAGS[i % len(NEG_TAGS)], charge) for i in range(n)]


def _gate(engine, tick):
    """Advance to a reflection-gate tick and run the (patched) reflection."""
    engine.state.tick = tick
    assert tick % INTERVAL == 0
    engine._maybe_reflect()


def _claim(engine, tag):
    return engine.state.narrative[f"pattern:{tag}"]


# -- fitness 1: contradicting evidence decreases a 1.0 claim -----------------

def test_contradiction_decreases_fossil_claim_within_five_updates():
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0)
    _gate(engine, INTERVAL)  # mint
    claim = _claim(engine, "comfort")
    # Precondition: the fossil — maxed confidence, settled-positive valence.
    claim.confidence = 1.0
    claim.valence = 0.85
    claim.updated_tick = INTERVAL

    decreased_at = None
    for i in range(1, 6):
        tick = INTERVAL * (i + 1)
        engine.state.memories = _neg_mems("comfort", tick)
        _gate(engine, tick)
        if _claim(engine, "comfort").confidence < 1.0:
            decreased_at = i
            break

    assert decreased_at is not None and decreased_at <= 5, "no decrease within 5 updates"
    claim = _claim(engine, "comfort")
    # First contradicting update cuts halfway toward uncertainty: 1.0 -> 0.75.
    assert claim.confidence == pytest.approx(0.75)
    # Contradiction trigger rewrote the proposition from the live sentence.
    assert "risk or hurt" in claim.proposition
    assert claim.valence < -0.15
    assert claim.updated_tick == INTERVAL * (decreased_at + 1)


def test_sustained_contradiction_keeps_cutting():
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0)
    _gate(engine, INTERVAL)
    claim = _claim(engine, "comfort")
    claim.confidence = 1.0
    claim.valence = 0.85
    claim.updated_tick = INTERVAL

    confidences = []
    for i in range(1, 7):
        tick = INTERVAL * (i + 1)
        engine.state.memories = _neg_mems("comfort", tick)
        _gate(engine, tick)
        confidences.append(_claim(engine, "comfort").confidence)
    # Monotone decrease while the evidence basis stays contradicting.
    assert all(b < a for a, b in zip(confidences, confidences[1:])), confidences
    assert confidences[-1] < 0.75


# -- fitness 2: decay toward 0.5 absent rehearsal ------------------------------

def test_decay_toward_uncertainty_matches_documented_rate():
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0)
    _gate(engine, INTERVAL)
    claim = _claim(engine, "comfort")
    claim.confidence = 1.0
    claim.updated_tick = 0

    # No repeated tag anywhere: decay runs, no rehearsal.
    engine.state.memories = [
        _mem(f"unrelated-{i}", 0, charge=0.5) for i in range(3)
    ]
    half_life = 693  # ~ln(0.5)/ln(0.999); pinned to DECAY_PER_TICK, not guessed
    _gate(engine, half_life - half_life % INTERVAL + INTERVAL)
    tick = engine.state.tick
    expected = 0.5 + 0.5 * (narrative_overlay.DECAY_PER_TICK ** tick)
    assert _claim(engine, "comfort").confidence == pytest.approx(expected, abs=1e-9)
    # Rehearsal marker untouched by decay.
    assert _claim(engine, "comfort").updated_tick == 0


def test_decay_is_monotone_and_bounded():
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0)
    _gate(engine, INTERVAL)
    claim = _claim(engine, "comfort")
    claim.confidence = 1.0
    claim.updated_tick = 0
    engine.state.memories = [_mem(f"unrelated-{i}", 0, charge=0.5) for i in range(3)]

    seen = []
    for step in range(1, 6):
        _gate(engine, step * 2016)  # 2016 = 168 * INTERVAL, gate-aligned
        seen.append(_claim(engine, "comfort").confidence)
    assert all(b < a for a, b in zip(seen, seen[1:])), seen
    assert all(0.5 <= c <= 1.0 for c in seen), seen
    # Long horizon: converges to the floor, never through it.
    assert seen[-1] == pytest.approx(0.5, abs=1e-3)


def test_decay_floor_is_explicit_not_zero():
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0)
    _gate(engine, INTERVAL)
    claim = _claim(engine, "comfort")
    claim.confidence = 0.3  # below uncertainty: decay pulls *up* toward 0.5
    claim.updated_tick = 0
    engine.state.memories = [_mem(f"unrelated-{i}", 0, charge=0.5) for i in range(3)]
    _gate(engine, 2016)  # gate-aligned
    expected = 0.5 + (0.3 - 0.5) * (narrative_overlay.DECAY_PER_TICK ** 2016)
    assert _claim(engine, "comfort").confidence == pytest.approx(expected, abs=1e-9)
    assert _claim(engine, "comfort").confidence > 0.3


def test_rehearsed_claim_does_not_decay():
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0, charge=0.9)
    _gate(engine, INTERVAL)
    before = _claim(engine, "comfort").confidence
    # Same agreeing evidence next gate: rehearsed, so no decay; agreement
    # moves confidence toward the evidence mass instead.
    engine.state.memories = _pos_mems("comfort", INTERVAL, charge=0.9)
    _gate(engine, 2 * INTERVAL)
    claim = _claim(engine, "comfort")
    assert claim.updated_tick == 2 * INTERVAL
    assert claim.confidence >= before  # agreement: toward mass, not down


# -- fitness 3: no new ratchet -------------------------------------------------

def test_sixty_agreeing_updates_never_exceed_evidence_mass():
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0, n=5, charge=0.9)
    _gate(engine, INTERVAL)
    mass = min(1.0, 0.35 + 5 * 0.08)  # 0.75
    peak = 0.0
    for i in range(2, 62):
        tick = INTERVAL * i
        engine.state.memories = _pos_mems("comfort", tick, n=5, charge=0.9)
        _gate(engine, tick)
        conf = _claim(engine, "comfort").confidence
        # Anti-ratchet invariant: agreement approaches the evidence mass
        # asymptotically and can never overshoot it (the old max()+0.02
        # ratchet climbed past the mass to exactly 1.0).
        assert conf <= mass + 1e-9, (i, conf)
        peak = max(peak, conf)
    assert peak < 1.0
    assert peak > 0.7  # ...but it does converge toward the mass, not stall
    # ...and a contradiction still cuts from wherever it got to.
    tick = INTERVAL * 62
    engine.state.memories = _neg_mems("comfort", tick, n=5)
    before = _claim(engine, "comfort").confidence
    _gate(engine, tick)
    assert _claim(engine, "comfort").confidence < before


def test_minted_at_mass_does_not_survive_contradiction():
    # The kept mint path can still open at 1.0 (clamp(0.35 + 10*0.08));
    # the no-ratchet guarantee is that it does not REMAIN there once the
    # evidence basis contradicts it.
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0, n=10, charge=0.9)
    _gate(engine, INTERVAL)
    assert _claim(engine, "comfort").confidence == 1.0
    tick = 2 * INTERVAL
    engine.state.memories = _neg_mems("comfort", tick, n=10)
    _gate(engine, tick)
    claim = _claim(engine, "comfort")
    assert claim.confidence == pytest.approx(0.75)
    assert "risk or hurt" in claim.proposition


def test_mixed_evidence_soak_no_stale_maxima():
    engine = _engine()
    peak_by_key: dict[str, float] = {}
    prev: dict[str, tuple] = {}
    for i in range(1, 41):
        tick = INTERVAL * i
        # Alternate evidence polarity every 5 gates; 4 memories per gate.
        makers = _pos_mems if (i // 5) % 2 == 0 else _neg_mems
        engine.state.memories = makers("comfort", tick, n=4, charge=0.7)
        _gate(engine, tick)
        for key, claim in engine.state.narrative.items():
            peak_by_key[key] = max(peak_by_key.get(key, 0.0), claim.confidence)
            state = (claim.proposition, claim.confidence, claim.valence)
            if key in prev and prev[key][0] != state[0]:
                # Proposition rewritten => contradiction trigger fired =>
                # confidence must have been cut, not raised.
                assert state[1] < prev[key][1], (key, prev[key], state)
            prev[key] = state
        # No claim may sit at >=0.999 while its live evidence basis disagrees
        # with its valence class (the "remains at 1.0 despite contradiction"
        # failure mode).
        recent = engine.state.memories[-24:]
        for key, claim in engine.state.narrative.items():
            if claim.confidence >= 0.999:
                tag = key.split("pattern:", 1)[1]
                basis = [m for m in recent if tag in m.tags]
                avg = sum(engine._memory_valence(m) for m in basis) / len(basis)
                assert narrative_overlay._valence_class(avg) == narrative_overlay._valence_class(
                    claim.valence
                ), (key, claim.confidence, avg, claim.valence)
    assert peak_by_key, "soak formed no claims at all"


def test_mint_path_unchanged():
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0, charge=0.9)
    before = len(engine.state.memories)
    _gate(engine, INTERVAL)
    claim = _claim(engine, "comfort")
    assert claim.confidence == pytest.approx(min(1.0, 0.35 + 3 * 0.08))
    assert claim.valence > 0.15
    assert "safer or more worthwhile" in claim.proposition
    assert len(claim.evidence_memory_ids) == 3
    # Mint still records the "I formed a conclusion" narrative memory.
    minted = [m for m in engine.state.memories[before:] if m.kind == "narrative"]
    assert len(minted) == 1
    assert minted[0].summary == claim.proposition


# -- regression genome ----------------------------------------------------------

def _narrative_fingerprint(engine):
    return {
        k: (c.proposition, round(c.confidence, 6), round(c.valence, 6), c.updated_tick)
        for k, c in engine.state.narrative.items()
    }


def test_dream_ticks_leave_narrative_claims_untouched():
    tmp = Path(tempfile.mkdtemp(prefix="narrative-dream-"))
    sub = CalibosSubject(
        tmp / "test.db",
        CARTRIDGE,
        cognition=InboxCognition(tmp / "inbox"),
        salience_path=tmp / "salience.json",
    )
    # Seed inside a transaction: _transaction() restores engine state from the
    # DB on entry, so a claim set on the bare in-memory state would vanish.
    with sub._transaction():
        sub.engine.state.narrative["pattern:comfort"] = NarrativeClaim(
            "pattern:comfort",
            "Experiences involving comfort have generally made my life feel safer.",
            0.9,
            0.8,
            ("mem-1",),
            sub.engine.state.tick,
        )
    before = _narrative_fingerprint(sub.engine)
    assert before, "seed claim did not survive the transaction round-trip"
    for _ in range(3):
        sub.dream_tick()
    # Scoped to narrative-claim state only (the operation under test).
    assert _narrative_fingerprint(sub.engine) == before


def test_read_only_snapshot_writes_nothing():
    engine = _engine()
    engine.state.memories = _pos_mems("comfort", 0)
    _gate(engine, INTERVAL)
    before = _narrative_fingerprint(engine)
    engine.debug_snapshot()
    assert _narrative_fingerprint(engine) == before


# -- fitness 5: blinded-region identical behavior ---------------------------------
#
# carrying-list (test_ablation.py), host-feeder (test_feeder.py), merger-write
# (test_remember.py) fixtures must behave identically with the overlay
# installed vs uninstalled. The diff touches only SubjectEngine._maybe_reflect,
# which none of these paths depend on; this runs them both ways to prove it.

BLINDED_FILES = ["tests/test_feeder.py", "tests/test_ablation.py", "tests/test_remember.py"]


def _run_blinded():
    import pytest as _pytest

    return _pytest.main(
        [str(BASE / f) for f in BLINDED_FILES] + ["-q", "--tb=short", "-p", "no:cacheprovider"]
    )


def test_blinded_fixtures_identical_before_after():
    narrative_overlay.uninstall()
    try:
        before = _run_blinded()
    finally:
        narrative_overlay.install()
    after = _run_blinded()
    assert before == after == pytest.ExitCode.OK
    # The overlay is active again for every test after this one.
    assert SubjectEngine._maybe_reflect is narrative_overlay.maybe_reflect_bidirectional
