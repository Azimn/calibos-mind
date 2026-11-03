"""Tests for the state-congruent retrieval gain (Domain 9).

Spec: research/specs/2026-11-02-state-congruent-retrieval-gain.md
All fixtures on /tmp — the live store is never touched.

The mechanism: CalibosWorkspace.view()'s salience-ranked sort key adds
statecongruence.state_congruent_boost_for(r, felt_levels) — a flat 0.5
for a record whose concepts name a need at felt level exactly 2
('pressing') — alongside the familiarity and self gains. The felt
levels arrive via workspace.felt_levels_provider, following the
open_keys_provider pattern; None provider means exactly 0 gain.

Ranking control (mirrors test_selfgain.py): records seeded at tick 0
with importance set via SalienceTracker.add_importance. Base activation
is log(1)=0 for all, so activation = 0.6 * importance: fillers at 1.0
-> 0.6, target/control at 0.9 -> 0.54, boosted target 1.04. The
15-filler / 1-target / 1-control layout puts the target just below
the 16-cut without the gain and admitted with it.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.drift import drift_report
from calibos_mind.provider import InboxCognition
from calibos_mind.statecongruence import (
    PRESSING_LEVEL,
    STATE_CONGRUENT_BOOST,
    state_congruent_boost_for,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.workspace import CalibosWorkspace
from digital_subject.cartridge import load_cartridge


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="statecongruence-test-"))


def _make_subject(tmp: Path, interoception_path=None) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        interoception_path=(str(interoception_path)
                            if interoception_path is not None else None),
    )


def _stub(text, concepts=()):
    r = types.SimpleNamespace()
    r.first_person = text
    r.concepts = concepts
    return r


# 15 fillers spread across the per-class caps (thought 4, memory 3,
# perception 3, social 3, temporal 2). Texts contain no need-name word
# (hunger, thirst, curiosity, comfort, energy, fatigue, focus,
# loneliness, pain, restlessness, safety, satisfaction, warmth), so no
# filler can fire the gain under any felt-level mapping — verified by
# test_no_filler_incidentally_boosted.
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


def _seed_cutoff_store(subject: CalibosSubject,
                       target_concepts=("hunger",)):
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


def _snapshot_files(tmp: Path):
    return {p: p.read_bytes() for p in sorted(tmp.rglob("*")) if p.is_file()}


def _set_pressing(ws, mapping):
    """Override the workspace provider (no transaction may intervene:
    _restore rebuilds the workspace and re-attaches the live provider)."""
    ws.felt_levels_provider = lambda: dict(mapping)


# -- fitness: pure function -------------------------------------------------

def test_state_congruent_boost_constant_and_pressing_level():
    assert STATE_CONGRUENT_BOOST == 0.5
    # FELT_BANDS = (settled, stirring, pressing, urgent): pressing is 2.
    assert PRESSING_LEVEL == 2


def test_pressing_level_fires_only():
    rec = _stub("A quiet note about the garden gate.", ("hunger",))
    assert state_congruent_boost_for(rec, {"hunger": 2}) == STATE_CONGRUENT_BOOST
    # Settled (0) and stirring (1) do not fire.
    assert state_congruent_boost_for(rec, {"hunger": 0}) == 0.0
    assert state_congruent_boost_for(rec, {"hunger": 1}) == 0.0
    # Urgent (3) is a distinct band, not 'pressing': it does not fire.
    # This is the spec's level-2-only reading, tested explicitly so a
    # future >=2 relaxation cannot slip in unnoticed.
    assert state_congruent_boost_for(rec, {"hunger": 3}) == 0.0
    # A different need pressing does not fire for this record.
    assert state_congruent_boost_for(rec, {"thirst": 2}) == 0.0
    # No felt state at all: empty mapping, None provider, malformed input.
    assert state_congruent_boost_for(rec, {}) == 0.0
    assert state_congruent_boost_for(rec, None) == 0.0
    assert state_congruent_boost_for(rec, ["hunger"]) == 0.0
    # Corrupt level values never mint a boost (bool is an int subclass).
    assert state_congruent_boost_for(rec, {"hunger": True}) == 0.0
    assert state_congruent_boost_for(rec, {"hunger": "2"}) == 0.0
    assert state_congruent_boost_for(rec, {"hunger": 2.0}) == 0.0


def test_flatness_no_scaling():
    # One pressing need vs three pressing needs: identical gain.
    one = _stub("Note one.", ("hunger",))
    three = _stub("Note three.", ("hunger", "thirst", "curiosity"))
    levels = {"hunger": 2, "thirst": 2, "curiosity": 2}
    assert state_congruent_boost_for(one, levels) == STATE_CONGRUENT_BOOST
    assert state_congruent_boost_for(three, levels) == STATE_CONGRUENT_BOOST
    assert state_congruent_boost_for(one, levels) == \
        state_congruent_boost_for(three, levels)
    # The same need listed twice in concepts: still exactly 0.5.
    dup = _stub("Note dup.", ("hunger", "hunger"))
    assert state_congruent_boost_for(dup, {"hunger": 2}) == STATE_CONGRUENT_BOOST
    # A pressing need plus non-pressing needs: still exactly 0.5.
    mixed = _stub("Note mixed.", ("hunger", "thirst"))
    assert state_congruent_boost_for(
        mixed, {"hunger": 2, "thirst": 1}) == STATE_CONGRUENT_BOOST


def test_none_safe_record_fields():
    assert state_congruent_boost_for(_stub(None), {"hunger": 2}) == 0.0
    r = _stub("x.")
    r.concepts = None
    assert state_congruent_boost_for(r, {"hunger": 2}) == 0.0
    r2 = _stub("x.")
    r2.concepts = 123
    assert state_congruent_boost_for(r2, {"hunger": 2}) == 0.0
    r3 = _stub("x.", ("hunger",))
    assert state_congruent_boost_for(r3, {"hunger": 2}) == STATE_CONGRUENT_BOOST


def test_no_filler_incidentally_boosted():
    # Every filler record, under every need pressing at once, gains 0:
    # the cutoff layout's premise does not leak through filler concepts.
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    all_pressing = {need: 2 for need in
                    ("comfort", "curiosity", "energy", "fatigue", "focus",
                     "hunger", "loneliness", "pain", "restlessness",
                     "safety", "satisfaction", "thirst", "warmth")}
    by_id = {r.id: r for r in subject.workspace.records}
    for i in range(len(_FILLERS)):
        assert state_congruent_boost_for(by_id[ids[f"F{i}"]],
                                         all_pressing) == 0.0


# -- fitness: paired stores, admission --------------------------------------

def test_cutoff_premise_target_below_fillers_unboosted():
    # Test-premise discipline (regression genome): prove the "just below
    # the cut" premise with measured activations before asserting the
    # admission outcome.
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    tracker = subject.workspace.salience_tracker
    filler_acts = [tracker.activation(ids[f"F{i}"], 0, 0) for i in range(15)]
    assert min(filler_acts) == 0.6
    for key in ("T", "C"):
        assert tracker.activation(ids[key], 0, 0) == 0.54
        assert tracker.activation(ids[key], 0, 0) < min(filler_acts)
    by_id = {r.id: r for r in subject.workspace.records}
    assert state_congruent_boost_for(by_id[ids["T"]], {"hunger": 2}) == 0.5
    assert state_congruent_boost_for(by_id[ids["C"]], {"hunger": 2}) == 0.0
    assert 0.54 + 0.5 > max(filler_acts), \
        "boosted target must rank above every filler"


def test_paired_stores_target_admitted_control_not():
    # Fitness 1, paired stores identical except the target's concepts.
    # Store A: target concepts name the pressing need.
    tmp_a = _tmp()
    subject_a = _make_subject(tmp_a)
    ids_a = _seed_cutoff_store(subject_a, target_concepts=("hunger",))
    ws_a = subject_a.workspace
    _set_pressing(ws_a, {"hunger": 2})
    ws_a.view()
    admitted_a = set(ws_a._last_view_ids)
    assert ids_a["T"] in admitted_a, "need-matching record must be admitted"
    assert ids_a["C"] not in admitted_a, \
        "otherwise-identical control must stay below the cut"
    # The boosted target leads the ranked (unpinned) window.
    pinned_a = {r.id for r in ws_a.records if r.generated_by == "cartridge"}
    unpinned_a = [rid for rid in ws_a._last_view_ids if rid not in pinned_a]
    assert unpinned_a[0] == ids_a["T"]
    # Store B: identical except the target's concepts name a neutral word.
    tmp_b = _tmp()
    subject_b = _make_subject(tmp_b)
    ids_b = _seed_cutoff_store(subject_b, target_concepts=("garden",))
    ws_b = subject_b.workspace
    _set_pressing(ws_b, {"hunger": 2})
    ws_b.view()
    admitted_b = set(ws_b._last_view_ids)
    assert ids_b["T"] not in admitted_b, \
        "same record with neutral concepts must stay below the cut"
    assert ids_b["C"] not in admitted_b


def test_non_pressing_level_no_admission():
    for level in (0, 1, 3):
        tmp = _tmp()
        subject = _make_subject(tmp)
        ids = _seed_cutoff_store(subject)
        ws = subject.workspace
        _set_pressing(ws, {"hunger": level})
        ws.view()
        assert ids["T"] not in set(ws._last_view_ids), \
            f"felt level {level} must not admit the target"
        assert ids["C"] not in set(ws._last_view_ids)


def test_no_provider_zero_gain_windows_identical():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    # Plain-workspace default: no provider attached by _make_subject's
    # subject? The subject DOES attach its live provider (which reads
    # no tracker here, hence {}). So first prove the live-provider
    # empty case, then the explicit None case, and compare all three.
    ws.felt_levels_provider = None
    ws.view()
    none_ids = tuple(ws._last_view_ids)
    assert ids["T"] not in set(none_ids)
    _set_pressing(ws, {})
    ws.view()
    empty_ids = tuple(ws._last_view_ids)
    assert none_ids == empty_ids, \
        "None provider and empty mapping must rank identically"
    # A provider naming a non-pressing level also ranks identically.
    _set_pressing(ws, {"hunger": 1})
    ws.view()
    assert tuple(ws._last_view_ids) == none_ids
    assert ids["T"] not in set(ws._last_view_ids)


def test_nonboosted_relative_order_preserved():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _set_pressing(ws, {"hunger": 2})
    ws.view()
    filler_ids = [ids[f"F{i}"] for i in range(15)]
    admitted_fillers = [rid for rid in ws._last_view_ids if rid in filler_ids]
    assert admitted_fillers == filler_ids[:len(admitted_fillers)], \
        "fillers keep insertion (rank) order among themselves"
    assert len(ws._last_view_ids) == 16


def test_recency_fallback_ignores_gain():
    # No salience tracker: the recency fallback must not boost
    # need-named records at all, even with a provider attached.
    ws = CalibosWorkspace()
    assert ws.felt_levels_provider is None  # class default: no gain
    ws.felt_levels_provider = lambda: {"hunger": 2}
    ws.add(1, "thought", "A quiet note about the garden gate.",
           concepts=("hunger",))
    ws.add(2, "thought", "The kettle whistled twice before boiling.")
    view = ws.view()
    texts = [e.first_person for e in view.experiences]
    assert texts == ["A quiet note about the garden gate.",
                     "The kettle whistled twice before boiling."], \
        "recency fallback unchanged by need-named concepts"


def test_pinned_unaffected_still_lead_all_admitted():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    pre_pinned = [r.id for r in ws.records if r.generated_by == "cartridge"]
    assert pre_pinned, "fresh subject must carry the pinned identity root"
    with subject._transaction():
        p1 = subject._add("memory", "A pressing authored seed.",
                          concepts=("hunger",), generated_by="cartridge")
        p2 = subject._add("memory", "A quiet authored seed memory.",
                          generated_by="cartridge")
    # The transaction rebuilds the workspace via _restore — re-fetch it
    # and re-attach the pressing provider (the live provider reads no
    # tracker here, so it would report {}).
    ws = subject.workspace
    _set_pressing(ws, {"hunger": 2})
    ws.view()
    admitted = list(ws._last_view_ids)
    expected_pinned = pre_pinned + [p1.id, p2.id]
    assert admitted[:len(expected_pinned)] == expected_pinned, \
        "pinned records lead in record order, gain or no gain"
    assert set(expected_pinned) <= set(admitted), "all pinned admitted"


# -- fitness: provider wiring ------------------------------------------------

def test_live_provider_reads_interoception_sidecar():
    tmp = _tmp()
    intero = tmp / "interoception.json"
    intero.write_text(json.dumps({
        "needs": {"hunger": {"felt": 0.85, "last_tick": 0, "level": 2},
                  "thirst": {"felt": 0.55, "last_tick": 0, "level": 1}},
        "params": {"rate_onset": 0.35, "rate_offset": 0.12,
                   "noise_scale": 0.01},
        "seed": 0,
        "realization": {"swing": {}},
    }), encoding="utf-8")
    subject = _make_subject(tmp, interoception_path=intero)
    assert subject.workspace.felt_levels_provider is not None
    assert subject._live_felt_levels() == {"hunger": 2, "thirst": 1}
    # The provider survives _restore (fresh workspace per transaction).
    with subject._transaction():
        pass
    assert subject.workspace.felt_levels_provider is not None
    assert subject._live_felt_levels() == {"hunger": 2, "thirst": 1}


def test_live_provider_with_pressing_need_admits_target():
    tmp = _tmp()
    intero = tmp / "interoception.json"
    intero.write_text(json.dumps({
        "needs": {"hunger": {"felt": 0.85, "last_tick": 0, "level": 2}},
        "params": {"rate_onset": 0.35, "rate_offset": 0.12,
                   "noise_scale": 0.01},
        "seed": 0,
        "realization": {"swing": {}},
    }), encoding="utf-8")
    subject = _make_subject(tmp, interoception_path=intero)
    ids = _seed_cutoff_store(subject)
    # Seeding crossed a transaction (_restore re-attached the provider
    # from the sidecar file); no override: the live path must fire.
    ws = subject.workspace
    assert ws.felt_levels_provider() == {"hunger": 2}
    ws.view()
    assert ids["T"] in set(ws._last_view_ids)
    assert ids["C"] not in set(ws._last_view_ids)


def test_live_provider_no_tracker_is_empty():
    tmp = _tmp()
    subject = _make_subject(tmp)
    assert subject.workspace.interoception_tracker is None
    assert subject.workspace.felt_levels_provider is not None
    assert subject._live_felt_levels() == {}
    assert subject.workspace.felt_levels_provider() == {}


def test_provider_called_once_per_view():
    tmp = _tmp()
    subject = _make_subject(tmp)
    _seed_cutoff_store(subject)
    ws = subject.workspace
    calls = []

    def counting():
        calls.append(1)
        return {"hunger": 2}

    ws.felt_levels_provider = counting
    ws.view()
    assert len(calls) == 1, \
        "provider is read once per view, not once per record"


def test_plain_workspace_defaults_to_none_provider():
    assert CalibosWorkspace.felt_levels_provider is None


# -- fitness: determinism -----------------------------------------------------

def test_determinism_identical_views_identical_admission_order():
    tmp = _tmp()
    subject = _make_subject(tmp)
    _seed_cutoff_store(subject)
    ws = subject.workspace
    _set_pressing(ws, {"hunger": 2})
    ws.view()
    first = tuple(ws._last_view_ids)
    ws.view()
    second = tuple(ws._last_view_ids)
    assert first == second


def test_determinism_two_stores_same_admission_text_order():
    orders = []
    for _ in range(2):
        tmp = _tmp()
        subject = _make_subject(tmp)
        _seed_cutoff_store(subject)
        ws = subject.workspace
        _set_pressing(ws, {"hunger": 2})
        ws.view()
        id_to_text = {r.id: r.first_person for r in ws.records}
        orders.append(tuple(id_to_text[rid] for rid in ws._last_view_ids))
    assert orders[0] == orders[1]


# -- fitness: no writes, read-only paths clean --------------------------------

def test_view_construction_writes_no_sidecars():
    tmp = _tmp()
    subject = _make_subject(tmp)
    _seed_cutoff_store(subject)
    ws = subject.workspace
    _set_pressing(ws, {"hunger": 2})
    ws.view()  # warm up: tracker construction reads are done by now
    before = _snapshot_files(tmp)
    for _ in range(3):
        ws.view()
    after = _snapshot_files(tmp)
    assert after == before, \
        "view construction must create/modify no files (pure gain)"


def test_drift_report_unchanged_by_views():
    tmp = _tmp()
    subject = _make_subject(tmp)
    _seed_cutoff_store(subject)
    ws = subject.workspace
    _set_pressing(ws, {"hunger": 2})
    tracker = subject.workspace.salience_tracker
    before = drift_report(subject.inspect(), tracker, window=10)
    for _ in range(3):
        ws.view()
    after = drift_report(subject.inspect(), tracker, window=10)
    assert after == before, "views (and the gain) must not move drift"


def test_status_output_unchanged_by_views():
    # The real read-only command path: patch the CLI at synthetic paths,
    # capture `mind status` output before and after views.
    tmp = _tmp()
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    salience = tmp / "salience.json"
    saved = (cli.DB, cli.INBOX, cli.SALIENCE, cli._subject)
    cli.DB, cli.INBOX, cli.SALIENCE = db, inbox, salience
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)
    cli._subject = lambda provider=None: CalibosSubject(
        str(db), cartridge,
        cognition=InboxCognition(inbox) if provider is None else provider,
        salience_path=str(salience))
    try:
        subject = cli._subject()
        _seed_cutoff_store(subject)
        ws = subject.workspace
        _set_pressing(ws, {"hunger": 2})
        import io
        from contextlib import redirect_stdout
        args = argparse.Namespace(raw=True)
        buf1, buf2 = io.StringIO(), io.StringIO()
        with redirect_stdout(buf1):
            cli.cmd_status(args)
        for _ in range(3):
            ws.view()
        with redirect_stdout(buf2):
            cli.cmd_status(args)
        assert buf1.getvalue() == buf2.getvalue(), \
            "status output must be byte-identical across views"
    finally:
        (cli.DB, cli.INBOX, cli.SALIENCE, cli._subject) = saved


def test_dream_tick_isolation_holds_with_gain():
    # Interoception sidecar with hunger pressing, so the dream views run
    # with a live firing gain (not a zero-gain provider): isolation must
    # still hold. dream_tick's own _restore re-attaches the live provider
    # from the sidecar file, so no manual override is needed or kept.
    tmp = _tmp()
    intero = tmp / "interoception.json"
    intero.write_text(json.dumps({
        "needs": {"hunger": {"felt": 0.85, "last_tick": 0, "level": 2}},
        "params": {"rate_onset": 0.35, "rate_offset": 0.12,
                   "noise_scale": 0.01},
        "seed": 0,
        "realization": {"swing": {}},
    }), encoding="utf-8")
    subject = _make_subject(tmp, interoception_path=intero)
    with subject._transaction():
        subject._add("memory", "A quiet note about the garden gate.",
                     concepts=("hunger",))
    assert subject.workspace.felt_levels_provider() == {"hunger": 2}
    # dream_tick asserts body/conduct/tick isolation itself; a raise here
    # means the gain broke the dream path.
    result = subject.dream_tick()
    assert result["action"] == "sleep"


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
