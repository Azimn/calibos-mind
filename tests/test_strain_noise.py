"""Tests for strain-scaled interoceptive noise (domain 11, 2026-10-05).

The felt-body noise grows with bodily weariness: per tick,
noise_scale_eff = NOISE_SCALE * (1 + STRAIN_NOISE_K * weariness(actuals)),
K = 2 — up to 3x at full exhaustion, exactly the pristine scale at
weariness 0. Tired organisms misread their bodies; the thinker is
untouched.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_strain_noise.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind.friction import weariness
from calibos_mind.interoception import (
    BASELINE,
    NOISE_SCALE,
    STRAIN_NOISE_K,
    SWING_GAP,
    InteroceptionTracker,
    _noise,
)


def _tracker(tmp: Path, name: str = "strain.json", **kw) -> InteroceptionTracker:
    return InteroceptionTracker(tmp / name, **kw)


def _needs(**over):
    base = {"hunger": 0.5, "thirst": 0.5, "fatigue": 0.5, "energy": 0.5,
            "focus": 0.5}
    base.update(over)
    return base


def _rested(**over):
    """A rested body: weariness exactly 0."""
    d = _needs(focus=1.0, fatigue=0.0)
    d.update(over)
    assert weariness(d) == 0.0
    return d


def _exhausted(**over):
    """An exhausted body: weariness exactly 1."""
    d = _needs(focus=0.0, fatigue=1.0)
    d.update(over)
    assert weariness(d) == 1.0
    return d


# -- fitness (a): strain scales the misreading --------------------------------

def test_strain_scales_misreading():
    """Identical need sequences: a high-weariness body shows larger mean
    |felt - actual| than the same sequence at low weariness. The hunger
    sequence is identical in both runs — only focus/fatigue (the weariness
    inputs) differ, so any difference is the noise scaling, not the chase."""
    def mean_dev(focus, fatigue, ticks=200):
        tmp = Path(tempfile.mkdtemp(prefix="strain-a-"))
        tr = _tracker(tmp)
        devs = []
        for tick in range(1, ticks + 1):
            actuals = _needs(hunger=0.5, focus=focus, fatigue=fatigue)
            tr.update(dict(actuals), tick)
            devs.append(abs(tr.data["needs"]["hunger"]["felt"] - 0.5))
        return statistics.mean(devs)

    low = mean_dev(1.0, 0.0)    # weariness 0 -> pristine scale
    high = mean_dev(0.0, 1.0)   # weariness 1 -> 3x scale
    # Measured ratio is 3.0 (the scale ratio); assert with margin.
    assert high > 2.0 * low, (low, high)


def test_strain_constant_is_two():
    """K = 2: up to 3x at full exhaustion, exactly 1x at weariness 0."""
    assert STRAIN_NOISE_K == 2.0
    assert NOISE_SCALE * (1.0 + STRAIN_NOISE_K * 0.0) == NOISE_SCALE
    assert NOISE_SCALE * (1.0 + STRAIN_NOISE_K * 1.0) == 3 * NOISE_SCALE


# -- fitness (b): weariness 0 is pristine -------------------------------------

def _pristine_step(prev_felt, actual, seed, tick, key, params):
    """The pre-mutation update math: pristine noise scale, no strain term."""
    move = actual - prev_felt
    displaced = prev_felt - BASELINE
    onset = (move * displaced > 0) or (displaced == 0 and move != 0)
    rate = params["rate_onset"] if onset else params["rate_offset"]
    n = _noise(seed, tick, key, params["noise_scale"])
    return round(min(1.0, max(0.0, prev_felt + move * rate + n)), 6)


def test_weariness_zero_matches_pristine_tick_by_tick():
    """At weariness 0 the felt values equal the pristine formula exactly,
    tick by tick — the strain term multiplies by exactly 1.0."""
    tmp = Path(tempfile.mkdtemp(prefix="strain-b-"))
    tr = _tracker(tmp)
    params = tr.data["params"]
    seed = tr.data["seed"]
    felt = dict.fromkeys(("hunger", "thirst", "energy"), BASELINE)
    seq = [0.2, 0.9, 0.5, 0.7, 0.1, 0.55, 0.95, 0.4]
    tick = 0
    for i in range(60):
        tick += 1
        actuals = _rested(hunger=seq[i % len(seq)],
                          thirst=seq[(i + 3) % len(seq)],
                          energy=seq[(i + 5) % len(seq)])
        tr.update(dict(actuals), tick)
        for key in felt:
            felt[key] = _pristine_step(felt[key], actuals[key],
                                       seed, tick, key, params)
            assert tr.data["needs"][key]["felt"] == felt[key], (tick, key)


def test_weariness_zero_sidecar_replays_byte_identical():
    """Two weariness-0 runs of an identical sequence -> byte-identical
    sidecar (determinism holds with the strain term in the path)."""
    seq = []
    for i in range(50):
        seq.append((i + 1, _rested(hunger=0.3 + 0.01 * (i % 11),
                                   thirst=0.7 - 0.005 * i)))
    paths = []
    for name in ("w0a.json", "w0b.json"):
        tmp = Path(tempfile.mkdtemp(prefix="strain-b2-"))
        tr = _tracker(tmp, name)
        for tick, actuals in seq:
            tr.update(dict(actuals), tick)
        tr.save()
        paths.append(tmp / name)
    assert paths[0].read_bytes() == paths[1].read_bytes()


# -- fitness (c): determinism with varying weariness --------------------------

def test_varying_weariness_replays_byte_identical():
    """Weariness varying tick to tick (fatigue ramps 0 -> 1) is still a
    pure function of the actuals: identical sequences -> byte-identical
    sidecar AND identical realization events."""
    def run(tmp):
        tr = _tracker(tmp)
        events = []
        for tick in range(1, 81):
            fatigue = round(tick / 80, 4)  # weariness ramps with the tick
            actuals = _needs(hunger=0.2 if tick < 40 else 0.9,
                             focus=1.0 - fatigue, fatigue=fatigue)
            events.extend(tr.update(dict(actuals), tick))
        tr.save()
        return (tmp / "strain.json").read_bytes(), events

    a_bytes, a_events = run(Path(tempfile.mkdtemp(prefix="strain-c1-")))
    b_bytes, b_events = run(Path(tempfile.mkdtemp(prefix="strain-c2-")))
    assert a_bytes == b_bytes
    assert a_events == b_events


# -- fitness (d): realization episodes under strain ---------------------------

def test_realization_still_mints_exactly_one_under_strain():
    """A 0.2 -> 0.9 step at full weariness opens and closes exactly one
    episode — strain changes the noise, not the episode discipline."""
    tmp = Path(tempfile.mkdtemp(prefix="strain-d-"))
    tr = _tracker(tmp)
    tick = 0
    for _ in range(30):
        # Stabilize felt at hunger 0.2. (focus/fatigue first-contact gaps
        # open and close here — genuine episodes under the model, not
        # noise; the step-phase assertion counts hunger events only.)
        tick += 1
        tr.update(_exhausted(hunger=0.2), tick)
    events = []
    for _ in range(40):
        tick += 1
        events.extend(tr.update(_exhausted(hunger=0.9), tick))
    hunger_ev = [e for e in events if e["need"] == "hunger"]
    assert len(hunger_ev) == 1, hunger_ev
    assert hunger_ev[0]["gap_max"] > SWING_GAP
    assert hunger_ev[0]["conv_tick"] > hunger_ev[0]["swing_tick"]


def test_no_noise_only_episodes_at_full_weariness():
    """Revert-signal guard: at full weariness (3x noise) a pinned body
    mints no realization episodes from jitter alone — episodes still
    require a genuine >0.25 swing."""
    tmp = Path(tempfile.mkdtemp(prefix="strain-d2-"))
    tr = _tracker(tmp)
    events = []
    for tick in range(1, 231):
        events.extend(tr.update(_exhausted(), tick))
    hunger_ev = [e for e in events if e["need"] == "hunger"]
    assert hunger_ev == [], hunger_ev


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"all {len(fns)} strain-noise tests passed")


if __name__ == "__main__":
    _main()
