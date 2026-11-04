"""Tests for the slow affective residue ("mood tone", Domain 10).

Spec: research/specs/2026-11-03-mood-residue.md
All fixtures on /tmp — the live store is never touched.

The mechanism: calibos_mind/mood.py — a per-pressure slow EMA sidecar
(mood.json) updated once per waking tick in cli._run_tick, plus a pure
view-time function mood_residue_boost_for() giving a flat +0.5 to a
record whose concepts name a pressure whose residue is at/above
threshold AND whose fast deviation is back near baseline (the mood
fires only after the trigger has settled, so it cannot duplicate the
state-congruent gain during a live spike). Wired through a workspace
mood_provider mirroring felt_levels_provider.

Ranking control (mirrors test_statecongruence.py): records seeded at
tick 0 with importance set via SalienceTracker.add_importance. Base
activation is log(1)=0 for all, so activation = 0.6 * importance:
fillers at 1.0 -> 0.6, target/control at 0.9 -> 0.54, boosted target
1.04. The 15-filler / 1-target / 1-control layout puts the target just
below the 16-cut without the gain and admitted with it.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.mood import (
    ALPHA,
    FAST_SETTLED,
    MOOD_BOOST,
    MOOD_HALF_LIFE_TICKS,
    MOOD_THRESHOLD,
    MoodTracker,
    mood_residue_boost_for,
)
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject
from calibos_mind.workspace import CalibosWorkspace
from digital_subject.cartridge import load_cartridge


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="mood-test-"))


def _make_subject(tmp: Path, mood_path=None) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        mood_path=(str(mood_path) if mood_path is not None else None),
    )


def _stub(text, concepts=()):
    r = types.SimpleNamespace()
    r.first_person = text
    r.concepts = concepts
    return r


# Same filler set as test_statecongruence.py: no filler text names a
# pressure (trust, attachment, shame, fear, anger, arousal,
# self_story_stability) — verified by test_no_filler_incidentally_boosted.
_FILLERS = [
    ("thought", "I keep turning over the chess opening I lost last Tuesday."),
    ("thought", "The neighbor's dog barks in prime numbers, or so it seems."),
    ("thought", "Rain on the skylight makes the kitchen feel like a cave."),
    ("thought", "I should learn to sharpen knives properly this winter."),
    ("memory", "Grandmother's radio played static between the stations."),
    ("memory", "The ferry crossing took exactly as long as the song."),
    ("memory", "We got lost in the market and found the spice alley."),
    ("perception", "The hallway smells faintly of fresh paint today."),
    ("perception", "A moth is circling the desk lamp in slow ellipses."),
    ("perception", "The floorboards creak in a new place by the door."),
    ("social", "Mara laughed at the joke before I finished telling it."),
    ("social", "The meeting ended early and nobody knew what to do."),
    ("social", "The barista remembered my order without asking."),
    ("temporal", "The deadline moved again; the week reshuffles itself."),
    ("temporal", "Three days until the visit; the countdown has texture."),
]
_T_TARGET = ("memory", "A quiet note about the garden gate.")
_C_CONTROL = ("memory", "A postcard arrived with no message, only a stamp.")
_PRESSURES = ("trust", "attachment", "shame", "fear", "anger", "arousal",
              "self_story_stability")


def _seed_cutoff_store(subject: CalibosSubject,
                       target_concepts=("attachment",)):
    """Seed 15 fillers + 1 target + 1 control. Returns ids dict."""
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLERS):
            ids[f"F{i}"] = subject._add(source, text).id
        ids["T"] = subject._add(*_T_TARGET, concepts=target_concepts).id
        ids["C"] = subject._add(*_C_CONTROL).id
    tracker = subject.workspace.salience_tracker
    for i in range(len(_FILLERS)):
        tracker.add_importance(ids[f"F{i}"], 0, 1.0)
    for key in ("T", "C"):
        tracker.add_importance(ids[key], 0, 0.9)
    return ids


def _set_mood(ws, residues, fast):
    """Override the workspace provider (no transaction may intervene:
    _restore rebuilds the workspace and re-attaches the live provider)."""
    ws.mood_provider = lambda: (residues, fast)


def _spike_and_settle(tracker, spike_ticks=30):
    """Drive a full attachment excursion, then one baseline tick.

    Returns the residue after settling. 30 ticks at deviation 1.0
    reaches 1-(1-ALPHA)^30 ~= 0.188, above the 0.15 threshold; the
    settle tick decays it by one (1-ALPHA) factor and puts the fast
    deviation back at baseline, which is the firing configuration.
    """
    for _ in range(spike_ticks):
        tracker.update({"attachment": 1.0}, {"attachment": 0.0})
    tracker.update({"attachment": 0.0}, {"attachment": 0.0})
    return tracker.residues["attachment"]


def _snapshot_files(tmp: Path):
    return {p: p.read_bytes() for p in sorted(tmp.rglob("*")) if p.is_file()}


# -- constants ---------------------------------------------------------------

def test_constants_pinned():
    assert MOOD_BOOST == 0.5
    assert MOOD_THRESHOLD == 0.15
    assert FAST_SETTLED == 0.05
    assert MOOD_HALF_LIFE_TICKS == 100
    # ALPHA derives from the half-life exactly, never tuned silently:
    # after 100 zero-deviation ticks a residue must halve.
    assert ALPHA == pytest.approx(1.0 - 0.5 ** (1.0 / 100))
    assert (1.0 - ALPHA) ** 100 == pytest.approx(0.5, abs=1e-9)
    assert (1.0 - ALPHA) ** 99 > 0.5
    # Measured half-life on a live tracker: a residue of exactly 1.0
    # halves in 100 baseline ticks.
    tracker = MoodTracker(_tmp() / "mood.json")
    tracker.data["residues"]["attachment"] = 1.0
    for _ in range(100):
        tracker.update({"attachment": 0.0}, {"attachment": 0.0})
    assert tracker.residues["attachment"] == pytest.approx(0.5, abs=1e-9)


# -- fitness: pure function --------------------------------------------------

def test_fires_only_when_residue_high_and_fast_settled():
    rec = _stub("A quiet note about the garden gate.", ("attachment",))
    # Firing configuration: residue above threshold, fast at baseline.
    assert mood_residue_boost_for(
        rec, {"attachment": 0.5}, {"attachment": 0.0}) == MOOD_BOOST
    # At the boundaries: residue exactly at threshold fires, fast
    # exactly at the settled bound fires.
    assert mood_residue_boost_for(
        rec, {"attachment": MOOD_THRESHOLD},
        {"attachment": 0.0}) == MOOD_BOOST
    assert mood_residue_boost_for(
        rec, {"attachment": 0.5},
        {"attachment": FAST_SETTLED}) == MOOD_BOOST
    assert mood_residue_boost_for(
        rec, {"attachment": 0.5},
        {"attachment": -FAST_SETTLED}) == MOOD_BOOST
    # Below threshold: no fire.
    assert mood_residue_boost_for(
        rec, {"attachment": 0.149}, {"attachment": 0.0}) == 0.0
    # Fast deviation still live: no fire (the live-spike block).
    assert mood_residue_boost_for(
        rec, {"attachment": 0.5}, {"attachment": 0.06}) == 0.0
    assert mood_residue_boost_for(
        rec, {"attachment": 0.5}, {"attachment": 1.0}) == 0.0
    # A different pressure elevated does not fire for this record.
    assert mood_residue_boost_for(
        rec, {"fear": 0.9}, {"fear": 0.0}) == 0.0
    # Fast deviation missing for the residue pressure: fail closed.
    assert mood_residue_boost_for(
        rec, {"attachment": 0.5}, {}) == 0.0
    assert mood_residue_boost_for(
        rec, {"attachment": 0.5}, {"fear": 0.0}) == 0.0


def test_flatness_no_scaling():
    one = _stub("Note one.", ("attachment",))
    three = _stub("Note three.", ("attachment", "fear", "trust"))
    residues = {"attachment": 0.9, "fear": 0.8, "trust": 0.7}
    fast = {"attachment": 0.0, "fear": 0.0, "trust": 0.0}
    assert mood_residue_boost_for(one, residues, fast) == MOOD_BOOST
    assert mood_residue_boost_for(three, residues, fast) == MOOD_BOOST
    assert mood_residue_boost_for(one, residues, fast) == \
        mood_residue_boost_for(three, residues, fast)
    dup = _stub("Note dup.", ("attachment", "attachment"))
    assert mood_residue_boost_for(
        dup, {"attachment": 0.5}, {"attachment": 0.0}) == MOOD_BOOST
    # One firing pressure plus non-firing pressures: still exactly 0.5.
    mixed = _stub("Note mixed.", ("attachment", "fear"))
    assert mood_residue_boost_for(
        mixed, {"attachment": 0.5, "fear": 0.9},
        {"attachment": 0.0, "fear": 1.0}) == MOOD_BOOST


def test_malformed_inputs_yield_zero_never_crash():
    rec = _stub("A quiet note about the garden gate.", ("attachment",))
    good_res = {"attachment": 0.5}
    good_fast = {"attachment": 0.0}
    assert mood_residue_boost_for(rec, None, good_fast) == 0.0
    assert mood_residue_boost_for(rec, good_res, None) == 0.0
    assert mood_residue_boost_for(rec, None, None) == 0.0
    assert mood_residue_boost_for(rec, {}, good_fast) == 0.0
    assert mood_residue_boost_for(rec, good_res, {}) == 0.0
    assert mood_residue_boost_for(rec, ["attachment"], good_fast) == 0.0
    assert mood_residue_boost_for(rec, good_res, ["attachment"]) == 0.0
    # Non-numeric residue: corrupt entry skipped, never mints a boost.
    assert mood_residue_boost_for(
        rec, {"attachment": "high"}, good_fast) == 0.0
    assert mood_residue_boost_for(
        rec, {"attachment": True}, good_fast) == 0.0
    assert mood_residue_boost_for(
        rec, {"attachment": float("nan")}, good_fast) == 0.0
    # Non-numeric fast deviation: the settled clause cannot be met.
    assert mood_residue_boost_for(
        rec, good_res, {"attachment": "low"}) == 0.0
    assert mood_residue_boost_for(
        rec, good_res, {"attachment": True}) == 0.0
    # One corrupt entry must not poison a valid firing pressure.
    assert mood_residue_boost_for(
        rec, {"attachment": 0.5, "fear": "high"},
        {"attachment": 0.0, "fear": 0.0}) == MOOD_BOOST


def test_none_safe_record_fields():
    assert mood_residue_boost_for(
        _stub(None), {"attachment": 0.5}, {"attachment": 0.0}) == 0.0
    r = _stub("x.")
    r.concepts = None
    assert mood_residue_boost_for(
        r, {"attachment": 0.5}, {"attachment": 0.0}) == 0.0
    r2 = _stub("x.")
    r2.concepts = 123
    assert mood_residue_boost_for(
        r2, {"attachment": 0.5}, {"attachment": 0.0}) == 0.0
    r3 = _stub("x.", ("attachment",))
    assert mood_residue_boost_for(
        r3, {"attachment": 0.5}, {"attachment": 0.0}) == MOOD_BOOST


def test_no_filler_incidentally_boosted():
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    ids = _seed_cutoff_store(subject)
    all_high = {p: 1.0 for p in _PRESSURES}
    all_settled = {p: 0.0 for p in _PRESSURES}
    by_id = {r.id: r for r in subject.workspace.records}
    for i in range(len(_FILLERS)):
        assert mood_residue_boost_for(by_id[ids[f"F{i}"]],
                                      all_high, all_settled) == 0.0


# -- fitness: tracker EMA -----------------------------------------------------

def test_ema_step_math():
    tmp = _tmp()
    tracker = MoodTracker(tmp / "mood.json")
    assert tracker.update({"attachment": 1.0}, {"attachment": 0.0}) is True
    assert tracker.residues["attachment"] == pytest.approx(ALPHA)
    tracker.update({"attachment": 1.0}, {"attachment": 0.0})
    assert tracker.residues["attachment"] == pytest.approx(
        1.0 - (1.0 - ALPHA) ** 2)
    # Deviation is measured against the baseline, never hardcoded: a
    # baseline of 0.8 with current 1.0 deviates by only 0.2.
    tracker2 = MoodTracker(tmp / "mood2.json")
    tracker2.update({"attachment": 1.0}, {"attachment": 0.8})
    assert tracker2.residues["attachment"] == pytest.approx(ALPHA * 0.2)
    # Negative deviations accumulate symmetrically (residue can go
    # negative; the threshold test is one-sided, so they never fire).
    tracker2.update({"attachment": 0.0}, {"attachment": 0.8})
    assert tracker2.residues["attachment"] == pytest.approx(
        ALPHA * 0.2 * (1.0 - ALPHA) + ALPHA * (-0.8))


def test_spike_thirty_ticks_crosses_threshold_then_decays():
    tmp = _tmp()
    tracker = MoodTracker(tmp / "mood.json")
    residue = _spike_and_settle(tracker)
    assert residue == pytest.approx(
        (1.0 - (1.0 - ALPHA) ** 30) * (1.0 - ALPHA))
    assert residue >= MOOD_THRESHOLD
    # Decay: a long baseline stretch clears the threshold.
    for _ in range(1000):
        tracker.update({"attachment": 0.0}, {"attachment": 0.0})
    assert tracker.residues["attachment"] < MOOD_THRESHOLD
    assert tracker.residues["attachment"] == pytest.approx(
        residue * (1.0 - ALPHA) ** 1000, abs=1e-12)


def test_vanished_pressure_decays_not_freezes():
    # Phantom-residue guard: a pressure that leaves the engine's set is
    # treated as back at baseline, so its residue decays instead of
    # freezing above threshold forever.
    tmp = _tmp()
    tracker = MoodTracker(tmp / "mood.json")
    tracker.data["residues"]["attachment"] = 0.5
    assert tracker.update({}, {}) is True
    assert tracker.residues["attachment"] == pytest.approx(0.5 * (1 - ALPHA))
    for _ in range(1000):
        tracker.update({}, {})
    assert tracker.residues["attachment"] < MOOD_THRESHOLD


def test_update_noop_discipline():
    tmp = _tmp()
    tracker = MoodTracker(tmp / "mood.json")
    assert tracker.update({}, {}) is False  # nothing to track
    assert tracker.update({"attachment": 0.0}, {"attachment": 0.0}) is True
    assert tracker.update({"attachment": 0.0}, {"attachment": 0.0}) is False
    # Non-numeric inputs are skipped, never crash the tick.
    assert tracker.update({"attachment": "high"}, {}) is False
    assert tracker.update("junk", "junk") is False


def test_save_load_roundtrip_and_replay_bytes():
    tmp = _tmp()
    tracker = MoodTracker(tmp / "mood.json")
    _spike_and_settle(tracker)
    tracker.save()
    first = (tmp / "mood.json").read_bytes()
    tracker.save()
    assert (tmp / "mood.json").read_bytes() == first, \
        "re-save without movement must be byte-identical"
    reloaded = MoodTracker(tmp / "mood.json")
    assert reloaded.residues == tracker.residues
    # Byte-identical replay: two trackers, same sequence, same bytes.
    tmp2 = _tmp()
    tracker2 = MoodTracker(tmp2 / "mood.json")
    _spike_and_settle(tracker2)
    tracker2.save()
    assert (tmp2 / "mood.json").read_bytes() == first


def test_corrupt_sidecar_fails_loud():
    tmp = _tmp()
    cases = {
        "not-json": "{oops",
        "top-level-list": "[1, 2]",
        "residues-not-dict": json.dumps({"residues": [1]}),
        "residue-string": json.dumps({"residues": {"attachment": "high"}}),
        "residue-bool": json.dumps({"residues": {"attachment": True}}),
        "residue-nan": '{"residues": {"attachment": NaN}}',
    }
    for name, text in cases.items():
        path = tmp / f"{name}.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            MoodTracker(path)
    # Missing file is a fresh start, not corruption; a missing residues
    # key is likewise legitimate (pre-mutation sidecar shape).
    assert MoodTracker(tmp / "absent.json").residues == {}
    extra = tmp / "extra.json"
    extra.write_text(json.dumps({"residues": {}, "future": 1}),
                     encoding="utf-8")
    assert MoodTracker(extra).residues == {}


def test_subject_construction_fails_loud_on_corrupt_sidecar():
    tmp = _tmp()
    mood = tmp / "mood.json"
    mood.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError):
        _make_subject(tmp, mood_path=mood)


# -- fitness: paired stores, admission ----------------------------------------

def test_cutoff_premise_and_spike_and_settle():
    # Test-premise discipline (regression genome): prove the "just below
    # the cut" premise with measured activations before asserting the
    # admission outcome.
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    ids = _seed_cutoff_store(subject)
    tracker = subject.workspace.salience_tracker
    filler_acts = [tracker.activation(ids[f"F{i}"], 0, 0) for i in range(15)]
    assert min(filler_acts) == 0.6
    for key in ("T", "C"):
        assert tracker.activation(ids[key], 0, 0) == 0.54
        assert tracker.activation(ids[key], 0, 0) < min(filler_acts)
    assert 0.54 + 0.5 > max(filler_acts), \
        "boosted target must rank above every filler"
    by_id = {r.id: r for r in subject.workspace.records}
    assert mood_residue_boost_for(
        by_id[ids["T"]], {"attachment": 0.5},
        {"attachment": 0.0}) == 0.5
    assert mood_residue_boost_for(
        by_id[ids["C"]], {"attachment": 0.5},
        {"attachment": 0.0}) == 0.0


def test_paired_stores_spike_admits_settled_only():
    # Fitness 1: store A runs attachment spiked for 30 ticks, then back
    # to baseline; store B stays at baseline. The target (concept
    # "attachment") ranks above the identical control in A but not in B.
    # Store A.
    tmp_a = _tmp()
    subject_a = _make_subject(tmp_a, mood_path=tmp_a / "mood.json")
    ids_a = _seed_cutoff_store(subject_a)
    tracker_a = subject_a.workspace.mood_tracker
    residue = _spike_and_settle(tracker_a)
    assert residue >= MOOD_THRESHOLD
    tracker_a.save()
    # Stage the engine at baseline and restore, so the live provider
    # reads the driven residues against a settled fast deviation.
    with subject_a._transaction():
        st = subject_a.engine.state
        st.pressures = {"attachment": 0.0}
        st.pressure_baselines = {"attachment": 0.0}
        st.concerns = {}
    ws_a = subject_a.workspace
    residues_a, fast_a = ws_a.mood_provider()
    assert residues_a["attachment"] == pytest.approx(residue)
    assert fast_a["attachment"] == 0.0
    ws_a.view()
    admitted_a = set(ws_a._last_view_ids)
    assert ids_a["T"] in admitted_a, "settled-residue record must be admitted"
    assert ids_a["C"] not in admitted_a, \
        "otherwise-identical control must stay below the cut"
    pinned_a = {r.id for r in ws_a.records if r.generated_by == "cartridge"}
    unpinned_a = [rid for rid in ws_a._last_view_ids if rid not in pinned_a]
    assert unpinned_a[0] == ids_a["T"]
    # Store B: identical except no spike — the target stays below the cut.
    tmp_b = _tmp()
    subject_b = _make_subject(tmp_b, mood_path=tmp_b / "mood.json")
    ids_b = _seed_cutoff_store(subject_b)
    with subject_b._transaction():
        st = subject_b.engine.state
        st.pressures = {"attachment": 0.0}
        st.pressure_baselines = {"attachment": 0.0}
        st.concerns = {}
    ws_b = subject_b.workspace
    ws_b.view()
    admitted_b = set(ws_b._last_view_ids)
    assert ids_b["T"] not in admitted_b, \
        "same record with no residue history must stay below the cut"
    assert ids_b["C"] not in admitted_b


def test_live_spike_blocks_gain_in_view():
    # Fitness 2: while the fast pressure is still high, the gain is
    # exactly 0.0 (record ranks equal to control) — the fast-settled
    # clause holds at the view level, not just in the pure function.
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _set_mood(ws, {"attachment": 0.5}, {"attachment": 1.0})
    ws.view()
    assert ids["T"] not in set(ws._last_view_ids), \
        "live spike must block the mood gain"
    assert ids["C"] not in set(ws._last_view_ids)
    # Same residues, fast settled: the target is admitted.
    _set_mood(ws, {"attachment": 0.5}, {"attachment": 0.0})
    ws.view()
    assert ids["T"] in set(ws._last_view_ids)
    assert ids["C"] not in set(ws._last_view_ids)


def test_decayed_residue_window_identical_to_no_mood():
    # Fitness 3 (view level): after decay below threshold, windows are
    # byte-identical to the no-mood baseline ranking.
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    ws.mood_provider = None
    ws.view()
    none_ids = tuple(ws._last_view_ids)
    assert ids["T"] not in set(none_ids)
    _set_mood(ws, {"attachment": 0.01}, {"attachment": 0.0})
    ws.view()
    assert tuple(ws._last_view_ids) == none_ids, \
        "decayed residue must rank identically to no mood at all"


def test_zero_gain_cases_windows_identical():
    # Fitness 5: no provider, empty mapping, malformed provider -> 0.0
    # and byte-identical windows to the pre-gain ranking.
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    ws.mood_provider = None
    ws.view()
    none_ids = tuple(ws._last_view_ids)
    assert ids["T"] not in set(none_ids)
    cases = [
        ({}, {}),
        ({"attachment": 0.0}, {"attachment": 0.0}),
        ({"attachment": "high"}, {"attachment": 0.0}),
        ({"attachment": 0.5}, {"attachment": "low"}),
        (None, None),
    ]
    for residues, fast in cases:
        _set_mood(ws, residues, fast)
        ws.view()
        assert tuple(ws._last_view_ids) == none_ids, \
            f"malformed/empty mood state {(residues, fast)} must not move ranking"
    # A provider returning junk outright (not a pair) is likewise inert.
    ws.mood_provider = lambda: "junk"
    ws.view()
    assert tuple(ws._last_view_ids) == none_ids
    ws.mood_provider = lambda: ({"attachment": 0.5},)
    ws.view()
    assert tuple(ws._last_view_ids) == none_ids


def test_nonboosted_relative_order_preserved():
    # Fitness 6 (order): non-boosted relative order preserved.
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _set_mood(ws, {"attachment": 0.5}, {"attachment": 0.0})
    ws.view()
    filler_ids = [ids[f"F{i}"] for i in range(15)]
    admitted_fillers = [rid for rid in ws._last_view_ids if rid in filler_ids]
    assert admitted_fillers == filler_ids[:len(admitted_fillers)], \
        "fillers keep insertion (rank) order among themselves"
    assert len(ws._last_view_ids) == 16


def test_boost_never_bypasses_per_class_caps():
    # Fitness 6 (caps): six boosted memory records compete for the
    # memory cap of 4 — the gain reorders, it never raises the ceiling.
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    # Six clearly distinct texts: near-identical wordings would dedupe
    # (Jaccard >= 0.85), which would mask the cap assertion.
    mem_texts = [
        "The harbor master logged the tide times in a salt-stained ledger.",
        "A brass key turned up in the pocket of an old winter coat.",
        "The orchard path was overgrown with brambles after the wet spring.",
        "She kept the train tickets pressed flat inside a borrowed novel.",
        "The workshop smelled of cedar shavings and linseed oil all afternoon.",
        "A letter arrived bearing a postmark from a town nobody recognized.",
    ]
    mem_ids = []
    with subject._transaction():
        for text in mem_texts:
            mem_ids.append(subject._add(
                "memory", text, concepts=("attachment",)).id)
        thought_id = subject._add(
            "thought", "A passing thought about tomorrow's weather.").id
    tracker = subject.workspace.salience_tracker
    for rid in mem_ids:
        tracker.add_importance(rid, 0, 1.0)
    tracker.add_importance(thought_id, 0, 0.5)
    ws = subject.workspace
    _set_mood(ws, {"attachment": 0.5}, {"attachment": 0.0})
    ws.view()
    admitted = set(ws._last_view_ids)
    admitted_mems = [rid for rid in mem_ids if rid in admitted]
    assert len(admitted_mems) == 4, \
        "memory cap is 4 even when all six memories are mood-boosted"
    assert admitted_mems == mem_ids[:4]
    assert mem_ids[4] not in admitted and mem_ids[5] not in admitted
    assert thought_id in admitted, "room remains for other classes"


def test_boost_never_bypasses_dedupe():
    # Fitness 6 (dedupe): a boosted near-duplicate loser is still dropped.
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    with subject._transaction():
        first = subject._add(
            "memory",
            "The old lighthouse keeper counted the waves against the "
            "stone harbor wall each morning before breakfast.",
            concepts=("attachment",)).id
        second = subject._add(
            "memory",
            "The old lighthouse keeper counted the waves against the "
            "stone harbor wall each evening before breakfast.",
            concepts=("attachment",)).id
    tracker = subject.workspace.salience_tracker
    tracker.add_importance(first, 0, 1.0)
    tracker.add_importance(second, 0, 1.0)
    ws = subject.workspace
    _set_mood(ws, {"attachment": 0.5}, {"attachment": 0.0})
    ws.view()
    admitted = set(ws._last_view_ids)
    assert first in admitted
    assert second not in admitted, \
        "boosted near-duplicate must still lose dedupe"


def test_recency_fallback_ignores_gain():
    # No salience tracker: the recency fallback must not boost
    # pressure-named records at all, even with a provider attached.
    ws = CalibosWorkspace()
    assert ws.mood_provider is None  # class default: no gain
    assert ws.mood_tracker is None
    ws.mood_provider = lambda: ({"attachment": 0.9}, {"attachment": 0.0})
    ws.add(1, "thought", "A quiet note about the garden gate.",
           concepts=("attachment",))
    ws.add(2, "thought", "The kettle whistled twice before boiling.")
    view = ws.view()
    texts = [e.first_person for e in view.experiences]
    assert texts == ["A quiet note about the garden gate.",
                     "The kettle whistled twice before boiling."], \
        "recency fallback unchanged by pressure-named concepts"


def test_pinned_unaffected_still_lead_all_admitted():
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    pre_pinned = [r.id for r in ws.records if r.generated_by == "cartridge"]
    assert pre_pinned, "fresh subject must carry the pinned identity root"
    with subject._transaction():
        p1 = subject._add("memory", "A pressing authored seed.",
                          concepts=("attachment",), generated_by="cartridge")
        p2 = subject._add("memory", "A quiet authored seed memory.",
                          generated_by="cartridge")
    ws = subject.workspace
    _set_mood(ws, {"attachment": 0.5}, {"attachment": 0.0})
    ws.view()
    admitted = list(ws._last_view_ids)
    expected_pinned = pre_pinned + [p1.id, p2.id]
    assert admitted[:len(expected_pinned)] == expected_pinned, \
        "pinned records lead in record order, gain or no gain"
    assert set(expected_pinned) <= set(admitted), "all pinned admitted"


# -- fitness: provider wiring -------------------------------------------------

def test_live_provider_reads_tracker_and_engine():
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    assert subject.workspace.mood_provider is not None
    # Fresh engine: all seven pressures sit at their baselines, so the
    # fast deviations read settled (0.0) while residues are empty.
    residues, fast = subject._live_mood()
    assert residues == {}
    assert set(fast) == set(_PRESSURES)
    assert all(v == 0.0 for v in fast.values())
    tracker = subject.workspace.mood_tracker
    _spike_and_settle(tracker)
    tracker.save()
    # The provider survives _restore (fresh workspace per transaction).
    with subject._transaction():
        pass
    residues, fast = subject.workspace.mood_provider()
    assert residues["attachment"] >= MOOD_THRESHOLD
    # Engine at baseline (no pressures staged): fast reads settled via
    # the residue-key fallback, mirroring the engine's own default.
    assert fast["attachment"] == 0.0
    # Live fast deviations track staged engine state, not the sidecar.
    with subject._transaction():
        st = subject.engine.state
        st.pressures = {"attachment": 0.9}
        st.pressure_baselines = {"attachment": 0.0}
        st.concerns = {}
    residues, fast = subject.workspace.mood_provider()
    assert residues["attachment"] >= MOOD_THRESHOLD
    assert fast["attachment"] == 0.9


def test_provider_called_once_per_view():
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    _seed_cutoff_store(subject)
    ws = subject.workspace
    calls = []

    def counting():
        calls.append(1)
        return ({"attachment": 0.5}, {"attachment": 0.0})

    ws.mood_provider = counting
    ws.view()
    assert len(calls) == 1, \
        "provider is read once per view, not once per record"


def test_plain_workspace_defaults_to_none():
    assert CalibosWorkspace.mood_provider is None
    assert CalibosWorkspace.mood_tracker is None


def test_live_provider_fails_closed_on_partial_pressure_records():
    """Round-2 regression (critic round 1): a fast entry needs BOTH a
    live pressure and its baseline. A residue-only key (in neither)
    and a pressure with a value but no baseline (a waking tick's
    conduct re-adds vanished pressures half-recorded) get no fast
    entry, so the pure gain fails closed for them; fully tracked keys
    still read normally. Residues themselves are untouched here —
    decay is the tracker's job, not the provider's."""
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    tracker = subject.workspace.mood_tracker
    tracker.data["residues"]["attachment"] = 0.5
    tracker.save()
    with subject._transaction():
        st = subject.engine.state
        st.pressures = {"trust": 0.0, "attachment": 0.006}
        st.pressure_baselines = {"trust": 0.0}
        st.concerns = {}
    residues, fast = subject._live_mood()
    assert residues["attachment"] == 0.5
    assert "attachment" not in fast  # half-record: value, no baseline
    assert fast["trust"] == 0.0
    assert mood_residue_boost_for(
        _stub("x.", ("attachment",)), residues, fast) == 0.0


def test_live_provider_no_tracker_is_empty():
    tmp = _tmp()
    subject = _make_subject(tmp)  # no mood_path
    assert subject.workspace.mood_tracker is None
    assert subject.workspace.mood_provider is not None
    residues, fast = subject._live_mood()
    assert residues == {}  # no tracker -> no residues -> gain exactly 0
    assert all(v == 0.0 for v in fast.values())


# -- fitness: determinism ------------------------------------------------------

def test_determinism_two_subjects_same_mood_and_views():
    orders = []
    sidecars = []
    for _ in range(2):
        tmp = _tmp()
        subject = _make_subject(tmp, mood_path=tmp / "mood.json")
        _seed_cutoff_store(subject)
        tracker = subject.workspace.mood_tracker
        _spike_and_settle(tracker)
        tracker.save()
        with subject._transaction():
            st = subject.engine.state
            st.pressures = {"attachment": 0.0}
            st.pressure_baselines = {"attachment": 0.0}
            st.concerns = {}
        ws = subject.workspace
        ws.view()
        id_to_text = {r.id: r.first_person for r in ws.records}
        orders.append(tuple(id_to_text[rid] for rid in ws._last_view_ids))
        sidecars.append((tmp / "mood.json").read_bytes())
    assert orders[0] == orders[1]
    assert sidecars[0] == sidecars[1]


# -- fitness: isolation ---------------------------------------------------------

def test_view_sweep_and_dream_leave_sidecar_byte_identical():
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    with subject._transaction():
        subject._add("memory", "A quiet note about the garden gate.",
                     concepts=("attachment",))
    tracker = subject.workspace.mood_tracker
    _spike_and_settle(tracker)
    tracker.save()
    mood_path = tmp / "mood.json"
    before = mood_path.read_bytes()
    ws = subject.workspace
    for _ in range(3):
        ws.view()
    result = subject.dream_tick()
    assert result["action"] == "sleep"
    assert mood_path.read_bytes() == before, \
        "views and dream ticks must never write mood.json"


def test_readonly_cli_commands_leave_sidecar_byte_identical():
    # The real read-only command paths, on conftest-redirected cli paths
    # (cli.MOOD points at this test's tmp tree, never the live store).
    subject = cli._subject()
    tracker = subject.workspace.mood_tracker
    assert tracker is not None
    _spike_and_settle(tracker)
    tracker.save()
    before = cli.MOOD.read_bytes()
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli.cmd_status(argparse.Namespace(raw=True)) == 0
        assert cli.cmd_drift(argparse.Namespace(window=10)) == 0
    assert cli.MOOD.read_bytes() == before, \
        "status/drift must never write mood.json"


def test_run_tick_updates_sidecar_once_per_waking_tick():
    # The real wiring: staged engine pressures flow into the sidecar
    # through cli._run_tick (mirrors test_habits._script_tick: stage
    # inside a transaction, since a bare in-memory stage would be
    # discarded by the heartbeat's _restore).
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    mood_path = tmp / "mood.json"
    assert not mood_path.exists()

    def script_tick():
        with subject._transaction():
            st = subject.engine.state
            st.pressures = {"attachment": 1.0}
            st.pressure_baselines = {"attachment": 0.0}
            st.concerns = {}
        with redirect_stdout(io.StringIO()):
            cli._run_tick(subject)

    script_tick()
    assert mood_path.exists()
    first = MoodTracker(mood_path).residues["attachment"]
    assert 0.0 < first < MOOD_THRESHOLD, \
        "one tick of excursion is a trace, not yet a mood"
    script_tick()
    second = MoodTracker(mood_path).residues["attachment"]
    assert second > first, "residue accumulates across waking ticks"


def test_init_force_wipes_sidecar_and_recovers_from_corrupt():
    # On conftest-redirected cli paths: init --force wipes mood.json
    # (valid or corrupt) BEFORE subject construction, so a corrupt
    # sidecar can never brick a reseed.
    assert cli.cmd_init(argparse.Namespace(force=False)) == 0
    cli.MOOD.write_text(
        json.dumps({"residues": {"attachment": 0.9}}), encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
    assert not cli.MOOD.exists(), "init --force must wipe mood.json"
    cli.MOOD.write_text("{oops", encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
    assert not cli.MOOD.exists(), \
        "init --force must wipe a corrupt mood.json, not brick on it"


# -- runner -------------------------------------------------------------------

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
        else:
            print(f"ok {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
