"""Tests for contradiction-flag surfacing into the view (Domain 24).

Spec: research/specs/2026-11-17-contradiction-surfacing.md
All fixtures on /tmp — the live store is never touched.

The mechanism: CalibosWorkspace.view()'s salience-ranked sort key adds
contradiction.contradiction_boost_for(r, flagged_ids) — a flat 0.5 for a
record whose id is party to a PENDING contradiction-flag proposal in the
proposals journal — alongside the familiarity, self, state-congruent, and
mood gains. The flagged set arrives via workspace.contradiction_provider,
following the open_keys_provider / mood_provider pattern; None provider
means exactly 0 gain.

Ranking control (mirrors test_statecongruence.py): records seeded at tick 0
with importance set via SalienceTracker.add_importance. Base activation
is log(1)=0 for all, so activation = 0.6 * importance: fillers at 1.0
-> 0.6, target/control at 0.9 -> 0.54, boosted target 1.04. The
15-filler / 2-target / 2-control layout puts each target just below the
16-cut without the gain and admitted with it; both sides of the flagged
pair can be admitted together (memory cap is 4: the two flagged memory
records plus two memory fillers).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
import calibos_mind.consolidate as C
from calibos_mind.contradiction import (
    CONTRADICTION_BOOST,
    contradiction_boost_for,
    flagged_record_ids,
)
from calibos_mind.drift import drift_report
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject
from calibos_mind.workspace import CalibosWorkspace
from digital_subject.cartridge import load_cartridge


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="contradiction-test-"))


def _make_subject(tmp: Path, proposals_path=None) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        proposals_path=(str(proposals_path)
                        if proposals_path is not None else None),
    )


def _stub(record_id, text="A quiet note about the garden gate."):
    r = types.SimpleNamespace()
    r.id = record_id
    r.first_person = text
    return r


# 15 fillers spread across the per-class caps (thought 4, memory 3,
# perception 3, social 3, temporal 2). Texts name no record id and share
# no content tokens with the target pair, so no filler can be flagged
# with them and no filler can be near-duped against them — verified by
# test_no_filler_incidentally_flagged.
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
# The flagged pair: same subject (overlap 1.0) and body Jaccard 0.6
# (supersede band), but opposite in stopword negation polarity, so the
# real consolidation scan mints a contradiction-flag for them
# (test_real_scan_mints_flag) instead of a supersede. Raw-token Jaccard
# is 0.625, below the workspace dedupe threshold — both sides can be
# admitted together, which a near-dup veto pair (identical content
# tokens, raw Jaccard ~0.93) could never do: dedupe drops the twin, by
# the spec's own hard constraint that the boost never bypasses dedupe.
_T1_TEXT = ("I love the morning walk through the old garden when the "
            "light is soft.")
_T2_TEXT = ("I do not love the morning walk through the old garden when "
            "the rain is falling.")
_C1_TEXT = "A postcard arrived with no message, only a stamp."
_C2_TEXT = "A ticket stub from a train I never rode."


def _seed_cutoff_store(subject: CalibosSubject):
    """Seed 15 fillers + flagged pair + 2 controls. Returns ids dict."""
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLERS):
            ids[f"F{i}"] = subject._add(source, text).id
        ids["T1"] = subject._add("memory", _T1_TEXT).id
        ids["T2"] = subject._add("memory", _T2_TEXT).id
        ids["C1"] = subject._add("memory", _C1_TEXT).id
        ids["C2"] = subject._add("memory", _C2_TEXT).id
    tracker = subject.workspace.salience_tracker
    for i in range(len(_FILLERS)):
        tracker.add_importance(ids[f"F{i}"], 0, 1.0)
    for key in ("T1", "T2", "C1", "C2"):
        tracker.add_importance(ids[key], 0, 0.9)
    return ids


def _write_journal(proposals_dir: Path, props: dict):
    """Write a hand-built proposals journal (dict pid -> proposal)."""
    proposals_dir.mkdir(parents=True, exist_ok=True)
    journal = {"seq": max((int(k) for k in props), default=0),
               "proposals": props}
    (proposals_dir / "proposals.json").write_text(
        json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")


def _flag_prop(pid, a, b, status="pending", kind="contradiction-flag"):
    return {"id": pid, "kind": kind, "a": a, "b": b,
            "winner": None, "loser": None, "rationale": "test flag",
            "reason": "", "confidence": 0.5, "a_hash": "x", "b_hash": "y",
            "a_tick": 0, "b_tick": 0, "status": status,
            "created_tick": 0, "resolved_tick": None,
            "rejected_reason": None}


def _snapshot_files(root: Path):
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _set_flagged(ws, flagged):
    """Override the workspace provider (no transaction may intervene:
    _restore rebuilds the workspace and re-attaches the live provider)."""
    ws.contradiction_provider = lambda: set(flagged)


# -- fitness: pure function -------------------------------------------------

def test_contradiction_boost_constant():
    assert CONTRADICTION_BOOST == 0.5


def test_boost_fires_only_for_flagged_id():
    rec = _stub("experience-17")
    assert contradiction_boost_for(rec, {"experience-17"}) == \
        CONTRADICTION_BOOST
    assert contradiction_boost_for(rec, {"experience-99"}) == 0.0
    assert contradiction_boost_for(rec, set()) == 0.0
    # Malformed provider input yields 0.0, never a crash.
    assert contradiction_boost_for(rec, None) == 0.0
    assert contradiction_boost_for(rec, ["experience-17"]) == 0.0
    assert contradiction_boost_for(rec, ("experience-17",)) == 0.0
    assert contradiction_boost_for(rec, {"experience-17": True}) == 0.0
    assert contradiction_boost_for(rec, 123) == 0.0
    # Non-string ids in the set can never match a real record id.
    assert contradiction_boost_for(rec, {123, None}) == 0.0
    assert contradiction_boost_for(rec, {"experience-17", 123}) == \
        CONTRADICTION_BOOST


def test_boost_none_safe_record_fields():
    rec = _stub(None)
    assert contradiction_boost_for(rec, {"experience-17"}) == 0.0
    no_id = types.SimpleNamespace(first_person="x.")
    assert contradiction_boost_for(no_id, {"experience-17"}) == 0.0
    bad_id = types.SimpleNamespace(id=123, first_person="x.")
    assert contradiction_boost_for(bad_id, {123}) == 0.0
    empty_id = _stub("")
    assert contradiction_boost_for(empty_id, {""}) == 0.0


def test_flatness_membership_is_flat():
    # A record in the flagged set gains exactly 0.5 however many flags
    # name it: the provider returns a set, so one flag or three collapse
    # to one membership test. Proved at the provider level below and at
    # the view level in test_triple_flag_still_flat_gain.
    rec = _stub("experience-17")
    assert contradiction_boost_for(rec, {"experience-17"}) == 0.5


# -- fitness: provider ------------------------------------------------------

def test_provider_pending_flag_ids():
    tmp = _tmp()
    pdir = tmp / "proposals"
    _write_journal(pdir, {
        "1": _flag_prop(1, "experience-17", "experience-18"),
        "2": _flag_prop(2, "experience-19", "experience-20",
                        status="accepted"),
        "3": _flag_prop(3, "experience-21", "experience-22",
                        status="rejected"),
        "4": _flag_prop(4, "experience-23", "experience-24",
                        kind="dedup"),
        "5": _flag_prop(5, "experience-25", "experience-26",
                        kind="supersede"),
        "6": _flag_prop(6, 123, None),
        "7": {"kind": "contradiction-flag", "status": "pending"},
        "8": "garbage",
    })
    assert flagged_record_ids(pdir) == {"experience-17", "experience-18"}


def test_provider_missing_and_malformed():
    tmp = _tmp()
    # Missing proposals directory.
    assert flagged_record_ids(tmp / "proposals") == set()
    # Missing journal (directory exists, no file).
    (tmp / "proposals").mkdir()
    assert flagged_record_ids(tmp / "proposals") == set()
    # Empty journal.
    (tmp / "proposals" / "proposals.json").write_text(
        json.dumps({"seq": 0, "proposals": {}}))
    assert flagged_record_ids(tmp / "proposals") == set()
    # Invalid JSON.
    (tmp / "proposals" / "proposals.json").write_text("{not json")
    assert flagged_record_ids(tmp / "proposals") == set()
    # Wrong shape: proposals is not a dict.
    (tmp / "proposals" / "proposals.json").write_text(
        json.dumps({"seq": 0, "proposals": [1, 2]}))
    assert flagged_record_ids(tmp / "proposals") == set()
    # Top level is not a dict.
    (tmp / "proposals" / "proposals.json").write_text(json.dumps([1, 2]))
    assert flagged_record_ids(tmp / "proposals") == set()
    # None base.
    assert flagged_record_ids(None) == set()
    # A non-path base never raises.
    assert flagged_record_ids(123) == set()


def test_provider_silent_on_missing_journal():
    # View construction runs every tick: a store with no journal yet
    # must not flood stderr on every view (the load_journal diagnostic
    # is suppressed in the provider path; consolidate --list still
    # reports it loudly through its own call).
    tmp = _tmp()
    buf = io.StringIO()
    with redirect_stderr(buf):
        assert flagged_record_ids(tmp / "proposals") == set()
    assert buf.getvalue() == "", \
        "provider must not emit diagnostics on a missing journal"


def test_provider_triple_flag_still_one_id():
    tmp = _tmp()
    pdir = tmp / "proposals"
    _write_journal(pdir, {
        "1": _flag_prop(1, "experience-17", "experience-18"),
        "2": _flag_prop(2, "experience-17", "experience-19"),
        "3": _flag_prop(3, "experience-20", "experience-17"),
    })
    flagged = flagged_record_ids(pdir)
    assert flagged == {"experience-17", "experience-18",
                       "experience-19", "experience-20"}
    # The shared record is in the set once: still exactly +0.5.
    assert contradiction_boost_for(_stub("experience-17"), flagged) == 0.5


def test_provider_reads_only():
    tmp = _tmp()
    pdir = tmp / "proposals"
    _write_journal(pdir, {
        "1": _flag_prop(1, "experience-17", "experience-18")})
    before = _snapshot_files(pdir)
    for _ in range(5):
        assert flagged_record_ids(pdir) == {"experience-17",
                                            "experience-18"}
    assert _snapshot_files(pdir) == before, \
        "provider reads must not write the journal, backup, or sentinel"


def test_provider_called_once_per_view():
    tmp = _tmp()
    subject = _make_subject(tmp, proposals_path=tmp / "proposals")
    _seed_cutoff_store(subject)
    ws = subject.workspace
    calls = []

    def counting():
        calls.append(1)
        return set()

    ws.contradiction_provider = counting
    ws.view()
    assert len(calls) == 1, \
        "provider is read once per view, not once per record"


def test_plain_workspace_defaults_to_none_provider():
    assert CalibosWorkspace.contradiction_provider is None


def test_live_provider_wiring_and_restore():
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    assert subject.workspace.contradiction_provider is not None
    # No journal yet: empty set, never a crash.
    assert subject.workspace.contradiction_provider() == set()
    _write_journal(pdir, {
        "1": _flag_prop(1, "experience-17", "experience-18")})
    assert subject.workspace.contradiction_provider() == \
        {"experience-17", "experience-18"}
    # The provider survives _restore (fresh workspace per transaction).
    with subject._transaction():
        pass
    assert subject.workspace.contradiction_provider is not None
    assert subject.workspace.contradiction_provider() == \
        {"experience-17", "experience-18"}


def test_live_provider_no_proposals_path_is_empty():
    tmp = _tmp()
    subject = _make_subject(tmp)
    assert subject.workspace.contradiction_provider is not None
    assert subject.workspace.contradiction_provider() == set()


# -- fitness: paired stores, admission --------------------------------------

def test_cutoff_premise_targets_below_fillers_unboosted():
    # Test-premise discipline (regression genome): prove the "just below
    # the cut" premise with measured activations before asserting the
    # admission outcome.
    tmp = _tmp()
    subject = _make_subject(tmp, proposals_path=tmp / "proposals")
    ids = _seed_cutoff_store(subject)
    tracker = subject.workspace.salience_tracker
    filler_acts = [tracker.activation(ids[f"F{i}"], 0, 0) for i in range(15)]
    assert min(filler_acts) == 0.6
    for key in ("T1", "T2", "C1", "C2"):
        assert tracker.activation(ids[key], 0, 0) == 0.54
        assert tracker.activation(ids[key], 0, 0) < min(filler_acts)
    assert 0.54 + CONTRADICTION_BOOST > max(filler_acts), \
        "boosted target must rank above every filler"


def test_no_filler_incidentally_flagged():
    # Every filler record, under a journal flagging the pair, gains 0:
    # the cutoff layout's premise does not leak through filler ids.
    tmp = _tmp()
    subject = _make_subject(tmp, proposals_path=tmp / "proposals")
    ids = _seed_cutoff_store(subject)
    flagged = {ids["T1"], ids["T2"]}
    by_id = {r.id: r for r in subject.workspace.records}
    for i in range(len(_FILLERS)):
        assert contradiction_boost_for(by_id[ids[f"F{i}"]], flagged) == 0.0


def test_flagged_pair_surfaces_both_sides():
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
    ws.view()
    admitted = set(ws._last_view_ids)
    assert ids["T1"] in admitted, "flagged record must be admitted"
    assert ids["T2"] in admitted, "both sides of the pair admit together"
    assert ids["C1"] not in admitted, \
        "otherwise-identical control must stay below the cut"
    assert ids["C2"] not in admitted
    # The boosted pair leads the ranked (unpinned) window.
    pinned = {r.id for r in ws.records if r.generated_by == "cartridge"}
    unpinned = [rid for rid in ws._last_view_ids if rid not in pinned]
    assert unpinned[0] == ids["T1"]
    assert unpinned[1] == ids["T2"]


def test_flag_drives_admission_not_text():
    # Same store, same texts: swap which pair the journal flags and the
    # admitted pair swaps with it. The flag — not the wording — admits.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
    ws.view()
    assert ids["T1"] in set(ws._last_view_ids)
    assert ids["C1"] not in set(ws._last_view_ids)
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["C1"], ids["C2"])})
    ws.view()
    assert ids["C1"] in set(ws._last_view_ids)
    assert ids["C2"] in set(ws._last_view_ids)
    assert ids["T1"] not in set(ws._last_view_ids)
    assert ids["T2"] not in set(ws._last_view_ids)


def test_resolution_removes_boost_byte_identical():
    # Baseline (no journal) -> flag -> reject -> window byte-identical
    # to the no-flag baseline: a resolved/rejected flag leaves no stale
    # boost behind.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    ws.view()
    baseline = tuple(ws._last_view_ids)
    assert ids["T1"] not in set(baseline)
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
    ws.view()
    flagged_window = tuple(ws._last_view_ids)
    assert flagged_window != baseline
    assert ids["T1"] in set(flagged_window)
    C.reject(pdir, 1, "waker reviewed: not a real conflict", store_tick=0)
    assert subject.workspace.contradiction_provider() == set()
    ws.view()
    assert tuple(ws._last_view_ids) == baseline, \
        "rejected flag must restore the no-flag window exactly"


def test_real_scan_mints_flag_and_accept_removes_boost():
    # End to end through the real consolidation pass: the scan mints
    # exactly one contradiction-flag for the pair (no dedup, no
    # supersede, no filler flags), the view admits both sides, and
    # accepting the flag restores the no-flag window exactly.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    ws.view()
    baseline = tuple(ws._last_view_ids)
    assert ids["T1"] not in set(baseline)
    report = C.dry_run(str(tmp / "mind.db"), str(pdir),
                       str(tmp / "archive"))
    assert len(report["proposals"]) == 1, \
        f"scan must mint exactly the pair flag, got {report['proposals']}"
    prop = report["proposals"][0]
    assert prop["kind"] == "contradiction-flag"
    assert prop["status"] == "pending"
    assert {prop["a"], prop["b"]} == {ids["T1"], ids["T2"]}
    assert subject.workspace.contradiction_provider() == \
        {ids["T1"], ids["T2"]}
    ws.view()
    assert ids["T1"] in set(ws._last_view_ids)
    assert ids["T2"] in set(ws._last_view_ids)
    outcomes = C.accept(str(tmp / "mind.db"), str(pdir),
                        str(tmp / "archive"), [prop["id"]])
    assert outcomes[0]["ok"] is True
    assert subject.workspace.contradiction_provider() == set()
    ws.view()
    assert tuple(ws._last_view_ids) == baseline, \
        "accepted flag must restore the no-flag window exactly"


def test_triple_flag_still_flat_gain():
    # A record party to three pending flags gains exactly +0.5 in the
    # live sort key, never scaled: measured activation, not just the
    # pure function's membership test.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"]),
        "2": _flag_prop(2, ids["T1"], ids["C1"]),
        "3": _flag_prop(3, ids["C2"], ids["T1"]),
    })
    ws = subject.workspace
    tracker = subject.workspace.salience_tracker
    by_id = {r.id: r for r in ws.records}
    flagged = ws.contradiction_provider()
    assert flagged == {ids["T1"], ids["T2"], ids["C1"], ids["C2"]}
    base = tracker.activation(ids["T1"], 0, 0)
    assert base == 0.54
    assert contradiction_boost_for(by_id[ids["T1"]], flagged) == 0.5
    assert base + contradiction_boost_for(by_id[ids["T1"]], flagged) == 1.04


def test_zero_gain_windows_identical():
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    # No provider at all.
    ws.contradiction_provider = None
    ws.view()
    none_ids = tuple(ws._last_view_ids)
    assert ids["T1"] not in set(none_ids)
    # Empty flagged set.
    _set_flagged(ws, set())
    ws.view()
    assert tuple(ws._last_view_ids) == none_ids
    # Missing proposals directory.
    ws.contradiction_provider = lambda: flagged_record_ids(pdir)
    ws.view()
    assert tuple(ws._last_view_ids) == none_ids
    # Empty journal.
    _write_journal(pdir, {})
    ws.view()
    assert tuple(ws._last_view_ids) == none_ids
    # Malformed journal.
    (pdir / "proposals.json").write_text("{not json")
    ws.view()
    assert tuple(ws._last_view_ids) == none_ids
    # Malformed provider returns.
    for bad in (None, ["x"], {"x": True}, {123}):
        ws.contradiction_provider = lambda bad=bad: bad
        ws.view()
        assert tuple(ws._last_view_ids) == none_ids, \
            f"provider returning {bad!r} must rank like no provider"


def test_nonboosted_relative_order_preserved():
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
    ws.view()
    filler_ids = [ids[f"F{i}"] for i in range(15)]
    admitted_fillers = [rid for rid in ws._last_view_ids if rid in filler_ids]
    admitted_set = set(admitted_fillers)
    assert admitted_fillers == [f for f in filler_ids if f in admitted_set], \
        "fillers keep insertion (rank) order among themselves"
    # The two flagged memory records take two memory-cap slots, so the
    # third memory filler (F6) loses to the cap while the others admit.
    assert ids["F6"] not in admitted_set
    assert len(ws._last_view_ids) == 16


def test_boost_never_bypasses_per_class_cap():
    # Five flagged memory records, all boosted above the fillers: the
    # memory cap (4) still admits at most four of them. The boost
    # reorders the ranking; it never widens the cap.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    texts = [
        "The blue bicycle leans against the fence by the library steps.",
        "A brass key turned in a lock I had never seen before.",
        "The attic window showed a field I did not recognize.",
        "Someone left a pair of gloves on the bench by the pond.",
        "The lighthouse keeper waved from the cliff path at dusk.",
    ]
    mem_fillers = [
        "The cellar stairs creak under a weight that is not mine.",
        "A moth landed on the map and stayed for the whole evening.",
        "The kettle sang a low note before the water boiled.",
        "Rain wrote brief rivers across the dusty windshield.",
    ]
    thought_fillers = [
        "I keep turning over the chess opening I lost last Tuesday.",
        "The neighbor's dog barks in prime numbers, or so it seems.",
        "Rain on the skylight makes the kitchen feel like a cave.",
        "I should learn to sharpen knives properly this winter.",
        "The harbor bell rings twice when the fog comes in.",
        "A comet crossed the yard while the kettle boiled.",
    ]
    ids = {}
    with subject._transaction():
        for i, t in enumerate(texts):
            ids[f"G{i}"] = subject._add("memory", t).id
        for i, t in enumerate(mem_fillers):
            ids[f"M{i}"] = subject._add("memory", t).id
        for i, t in enumerate(thought_fillers):
            ids[f"H{i}"] = subject._add("thought", t).id
    tracker = subject.workspace.salience_tracker
    for i in range(5):
        tracker.add_importance(ids[f"G{i}"], 0, 0.9)
    for i in range(4):
        tracker.add_importance(ids[f"M{i}"], 0, 1.0)
    for i in range(6):
        tracker.add_importance(ids[f"H{i}"], 0, 1.0)
    _write_journal(pdir, {
        str(i + 1): _flag_prop(i + 1, ids[f"G{i}"],
                               ids[f"G{(i + 1) % 5}"])
        for i in range(5)})
    ws = subject.workspace
    ws.view()
    admitted = set(ws._last_view_ids)
    by_id = {r.id: r for r in ws.records}
    pinned = {r.id for r in ws.records if r.generated_by == "cartridge"}
    # Pinned roots are exempt from caps by design (workspace.py), so
    # count only the unpinned memory records against the cap of 4: the
    # pinned identity root is itself a memory record.
    mem_admitted = [rid for rid in ws._last_view_ids
                    if rid not in pinned and by_id[rid].source == "memory"]
    assert len(mem_admitted) == 4, \
        f"memory cap is 4 even with five boosted memory records, got {len(mem_admitted)}"
    assert ids["G4"] not in admitted, \
        "the fifth flagged memory record must lose to the cap"
    for i in range(4):
        assert ids[f"G{i}"] in admitted
    # The thought cap (4) holds too: only the first four thought
    # fillers admit, even though the window has room.
    for i in range(4):
        assert ids[f"H{i}"] in admitted
    assert ids["H4"] not in admitted
    assert ids["H5"] not in admitted


def test_boost_never_bypasses_dedupe():
    # A flagged record that near-duplicates an admitted record must not
    # be admitted alongside it: the boost reorders the ranking, dedupe
    # still keeps exactly one representative per duplicate set.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLERS):
            ids[f"F{i}"] = subject._add(source, text).id
        ids["A"] = subject._add("memory", _T1_TEXT).id
        ids["B"] = subject._add(
            "memory", _T1_TEXT.replace("clear.", "clear!")).id
    tracker = subject.workspace.salience_tracker
    for i in range(len(_FILLERS)):
        tracker.add_importance(ids[f"F{i}"], 0, 1.0)
    tracker.add_importance(ids["A"], 0, 1.0)
    tracker.add_importance(ids["B"], 0, 0.9)
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["B"], ids["F0"])})
    ws = subject.workspace
    ws.view()
    admitted = set(ws._last_view_ids)
    assert ids["B"] in admitted, "flagged near-dup leads the ranking"
    assert ids["A"] not in admitted, \
        "the near-duplicate twin must not ride in alongside it"


def test_recency_fallback_ignores_gain():
    # No salience tracker: the recency fallback must not boost flagged
    # records at all, even with a provider attached.
    ws = CalibosWorkspace()
    assert ws.contradiction_provider is None  # class default: no gain
    ws.contradiction_provider = lambda: {"experience-1"}
    ws.add(1, "thought", "A quiet note about the garden gate.")
    ws.add(2, "thought", "The kettle whistled twice before boiling.")
    view = ws.view()
    texts = [e.first_person for e in view.experiences]
    assert texts == ["A quiet note about the garden gate.",
                     "The kettle whistled twice before boiling."], \
        "recency fallback unchanged by flagged ids"


def test_pinned_unaffected_still_lead_all_admitted():
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    pre_pinned = [r.id for r in ws.records if r.generated_by == "cartridge"]
    assert pre_pinned, "fresh subject must carry the pinned identity root"
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
    ws.view()
    admitted = list(ws._last_view_ids)
    assert admitted[:len(pre_pinned)] == pre_pinned, \
        "pinned records lead in record order, gain or no gain"
    assert set(pre_pinned) <= set(admitted), "all pinned admitted"


# -- fitness: determinism -----------------------------------------------------

def test_determinism_identical_views_identical_admission_order():
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
    ws.view()
    first = tuple(ws._last_view_ids)
    ws.view()
    second = tuple(ws._last_view_ids)
    assert first == second


def test_determinism_two_stores_same_admission_text_order():
    orders = []
    for _ in range(2):
        tmp = _tmp()
        pdir = tmp / "proposals"
        subject = _make_subject(tmp, proposals_path=pdir)
        ids = _seed_cutoff_store(subject)
        ws = subject.workspace
        _write_journal(pdir, {
            "1": _flag_prop(1, ids["T1"], ids["T2"])})
        ws.view()
        id_to_text = {r.id: r.first_person for r in ws.records}
        orders.append(tuple(id_to_text[rid] for rid in ws._last_view_ids))
    assert orders[0] == orders[1]


# -- fitness: no writes, read-only paths clean --------------------------------

def test_view_construction_writes_no_sidecars():
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
    ws.view()  # warm up: tracker construction reads are done by now
    before = _snapshot_files(tmp)
    for _ in range(3):
        ws.view()
    after = _snapshot_files(tmp)
    assert after == before, \
        "view construction must create/modify no files (pure gain)"


def test_drift_report_unchanged_by_views():
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
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
    saved = (cli.DB, cli.INBOX, cli.SALIENCE, cli.PROPOSALS, cli._subject)
    cli.DB, cli.INBOX, cli.SALIENCE = db, inbox, salience
    cli.PROPOSALS = tmp / "proposals"
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)
    cli._subject = lambda provider=None: CalibosSubject(
        str(db), cartridge,
        cognition=InboxCognition(inbox) if provider is None else provider,
        salience_path=str(salience),
        proposals_path=str(tmp / "proposals"))
    try:
        subject = cli._subject()
        ids = _seed_cutoff_store(subject)
        ws = subject.workspace
        _write_journal(tmp / "proposals", {
            "1": _flag_prop(1, ids["T1"], ids["T2"])})
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
        (cli.DB, cli.INBOX, cli.SALIENCE, cli.PROPOSALS,
         cli._subject) = saved


def test_isolation_journal_byte_identical():
    # Read-only sweep + dream ticks with a LIVE firing flag: the
    # proposals journal (primary, backup, sentinel) stays byte-identical,
    # and the windows replay byte-identically.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
    assert ws.contradiction_provider() == {ids["T1"], ids["T2"]}
    ws.view()
    before = _snapshot_files(pdir)
    first_window = tuple(ws._last_view_ids)
    tracker = subject.workspace.salience_tracker
    for _ in range(3):
        ws.view()
        drift_report(subject.inspect(), tracker, window=10)
    for _ in range(2):
        result = subject.dream_tick()
        assert result["action"] == "sleep"
    after = _snapshot_files(pdir)
    assert after == before, \
        "views, drift, and dream ticks must never write the journal"
    # The flag still fires after the dream ticks (no stale state either
    # way), and the window replays exactly.
    assert subject.workspace.contradiction_provider() == \
        {ids["T1"], ids["T2"]}
    subject.workspace.view()
    assert tuple(subject.workspace._last_view_ids) == first_window


def test_dream_tick_isolation_holds_with_gain():
    # A journal with a live firing flag, so the dream views run with a
    # firing gain (not a zero-gain provider): isolation must still hold.
    # dream_tick's own _restore re-attaches the live provider from the
    # proposals path, so no manual override is needed or kept.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ids = _seed_cutoff_store(subject)
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["T1"], ids["T2"])})
    assert subject.workspace.contradiction_provider() == \
        {ids["T1"], ids["T2"]}
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
