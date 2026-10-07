"""Tests for the self-relevance retrieval gain (calibos_mind/selfgain.py).

Spec: workspace/goals/calibos-autonomous-mind-operation/hidden_files/
      spec-selfgain-2026-10-06.md
All fixtures on /tmp — the live store is never touched.

The mechanism: CalibosWorkspace.view()'s salience-ranked sort key adds
selfgain.self_boost_for(r, self.display_name) — a flat 0.5 for
self-referential records — alongside the familiarity nudge. Three
self-referential signals: the frozen engine's "This event concerns me: "
self-relevance stamp, the 'identity' concept, and a whole-word
case-insensitive display-name mention (None name disables only that
signal).

Ranking control: records are seeded at tick 0 with importance set via
SalienceTracker.add_importance. Base activation is log(1)=0 for all, so
activation = 0.6 * importance: fillers at 1.0 -> 0.6, targets at 0.9 ->
0.54, boosted targets at 1.04. The 16-filler / 2-target / 1-control
layout puts each target at ranked position 17..18 without the gain —
just below the 16-cut — and admitted with it.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.drift import drift_report
from calibos_mind.provider import InboxCognition
from calibos_mind.selfgain import OWN_EVENT_PREFIX, SELF_BOOST, self_boost_for
from calibos_mind.subject import CalibosSubject
from calibos_mind.workspace import CalibosWorkspace
from digital_subject.cartridge import load_cartridge


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="selfgain-test-"))


def _make_subject(tmp: Path) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
    )


def _stub(text, concepts=()):
    r = types.SimpleNamespace()
    r.first_person = text
    r.concepts = concepts
    return r


# 15 fillers spread across the per-class caps (thought 4, memory 3,
# perception 3, social 3, temporal 2). Texts avoid the name, the prefix,
# and the 'identity' concept.
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
_T_NAME = ("imagination",
           "Calibos wondered whether the lighthouse keeper was still awake.")
_T_PREFIX = ("imagination",
             "This event concerns me: the tide schedule changed overnight.")
_T_IDENTITY = ("memory",
               "I remember the garden gate being open this morning.")
_C_CONTROL = ("memory",
              "A postcard arrived with no message, only a stamp.")


def _seed_cutoff_store(subject: CalibosSubject):
    """Seed 15 fillers + 3 self targets + 1 control. Returns ids dict."""
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLERS):
            ids[f"F{i}"] = subject._add(source, text).id
        ids["T_name"] = subject._add(*_T_NAME).id
        ids["T_prefix"] = subject._add(*_T_PREFIX).id
        ids["T_identity"] = subject._add(*_T_IDENTITY,
                                         concepts=("identity", "garden")).id
        ids["C"] = subject._add(*_C_CONTROL).id
    tracker = subject.workspace.salience_tracker
    for i in range(len(_FILLERS)):
        tracker.add_importance(ids[f"F{i}"], 0, 1.0)
    for key in ("T_name", "T_prefix", "T_identity", "C"):
        tracker.add_importance(ids[key], 0, 0.9)
    return ids


def _snapshot_files(tmp: Path):
    return {p: p.read_bytes() for p in sorted(tmp.rglob("*")) if p.is_file()}


# -- fitness: pure function, per-signal -------------------------------------

def test_self_boost_constant():
    assert SELF_BOOST == 0.5


def test_each_signal_independently_triggers_full_boost():
    prefix = _stub(OWN_EVENT_PREFIX + "the bell rang twice today.")
    assert self_boost_for(prefix, None) == SELF_BOOST
    identity = _stub("A quiet sentence about the garden.", ("identity",))
    assert self_boost_for(identity, None) == SELF_BOOST
    name = _stub("Calibos walked home in the rain.")
    assert self_boost_for(name, "Calibos") == SELF_BOOST
    plain = _stub("A quiet sentence about the garden.")
    assert self_boost_for(plain, "Calibos") == 0.0
    assert self_boost_for(plain, None) == 0.0


def test_flatness_no_scaling():
    # Five mentions: still exactly 0.5.
    many = _stub("Calibos met Calibos; calibos waved at CALIBOS and Calibos.")
    assert self_boost_for(many, "Calibos") == SELF_BOOST
    # All three signals at once: still exactly 0.5.
    all_three = _stub(OWN_EVENT_PREFIX + "Calibos heard the bell.",
                      ("identity",))
    assert self_boost_for(all_three, "Calibos") == SELF_BOOST


def test_name_match_is_whole_word_case_insensitive():
    assert self_boost_for(_stub("calibos slept late."), "Calibos") == SELF_BOOST
    assert self_boost_for(_stub("CALIBOS slept late."), "Calibos") == SELF_BOOST
    assert self_boost_for(_stub("(Calibos) slept late."), "Calibos") == SELF_BOOST
    # Partial-word matches must not fire (dead-regex-branch discipline:
    # \b must actually hold on both sides).
    assert self_boost_for(_stub("Caliboses slept late."), "Calibos") == 0.0
    assert self_boost_for(_stub("XCalibos slept late."), "Calibos") == 0.0
    assert self_boost_for(_stub("Calibosian slept late."), "Calibos") == 0.0


def test_name_ending_in_punctuation_matches_whole_word():
    # Regression genome "dead regex branches": \b can never hold adjacent
    # to a non-word char, so a punctuation-edged name must use lookarounds.
    assert self_boost_for(_stub("DJ! woke early."), "DJ!") == SELF_BOOST
    assert self_boost_for(_stub("XDJ! woke early."), "DJ!") == 0.0


def test_name_starting_with_punctuation_matches_whole_word():
    assert self_boost_for(_stub("(Calibos) slept late."),
                          "(Calibos)") == SELF_BOOST
    assert self_boost_for(_stub("X(Calibos) slept late."),
                          "(Calibos)") == 0.0


def test_name_regex_is_escaped():
    # A display name carrying regex metacharacters matches literally.
    assert self_boost_for(_stub("A.C woke early."), "A.C") == SELF_BOOST
    assert self_boost_for(_stub("ABC woke early."), "A.C") == 0.0, \
        "unescaped '.' would match ABC"


def test_none_display_name_disables_only_name_signal():
    named = _stub("Calibos walked home.")
    assert self_boost_for(named, None) == 0.0
    assert self_boost_for(named, "") == 0.0
    assert self_boost_for(named, "   ") == 0.0
    # The other two signals still fire with a None name.
    assert self_boost_for(_stub(OWN_EVENT_PREFIX + "x."), None) == SELF_BOOST
    assert self_boost_for(_stub("x.", ("identity",)), None) == SELF_BOOST


def test_prefix_is_exact_verbatim_engine_stamp():
    # Verified against digital_subject/engine.py _own_event:
    # f"This event concerns me: {event.description}"
    assert OWN_EVENT_PREFIX == "This event concerns me: "
    assert self_boost_for(_stub("This event concerns me"), "Calibos") == 0.0
    assert self_boost_for(_stub("This event concerns me:"), "Calibos") == 0.0
    assert self_boost_for(
        _stub("Note: This event concerns me: later"), "Calibos") == 0.0, \
        "prefix must be at the start, not merely present"


def test_none_safe_record_fields():
    r = _stub(None)
    assert self_boost_for(r, "Calibos") == 0.0
    r2 = _stub(None, ("identity",))
    assert self_boost_for(r2, None) == SELF_BOOST


# -- fitness: below-cutoff admission, self vs control ------------------------

def test_cutoff_premise_targets_rank_below_fillers_unboosted():
    # Test-premise discipline (regression genome): prove the "just below
    # the cut" premise with measured activations before asserting the
    # admission outcome.
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    tracker = subject.workspace.salience_tracker
    filler_acts = [tracker.activation(ids[f"F{i}"], 0, 0) for i in range(15)]
    for key in ("T_name", "T_prefix", "T_identity", "C"):
        a = tracker.activation(ids[key], 0, 0)
        assert a < min(filler_acts), \
            f"{key} must rank below every filler without the gain"


def test_self_targets_admitted_control_not():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    assert ws.display_name == "Calibos"
    ws.view()
    admitted = set(ws._last_view_ids)
    for key in ("T_name", "T_prefix", "T_identity"):
        assert ids[key] in admitted, f"{key} must be admitted with the gain"
    assert ids["C"] not in admitted, \
        "otherwise-identical non-self control must stay below the cut"
    # Boosted targets lead the ranked (unpinned) window.
    pinned_ids = {r.id for r in ws.records if r.generated_by == "cartridge"}
    unpinned = [rid for rid in ws._last_view_ids if rid not in pinned_ids]
    assert unpinned[0] == ids["T_name"]
    assert unpinned[1] == ids["T_prefix"]


def test_name_signal_causal_without_display_name_target_stays_out():
    # Same store, name-mention signal disabled: the prefix target (whose
    # signal is name-independent) is still admitted, the name target is not.
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    ws.display_name = None
    try:
        ws.view()
        admitted = set(ws._last_view_ids)
    finally:
        ws.display_name = "Calibos"
    assert ids["T_prefix"] in admitted, "prefix signal is name-independent"
    assert ids["T_identity"] in admitted, "identity signal is name-independent"
    assert ids["T_name"] not in admitted, \
        "name-mention target needs display_name to fire"
    assert ids["C"] not in admitted


def test_pinned_unaffected_still_lead_all_admitted():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    # The fresh subject already carries the cartridge identity root as
    # pinned (experience-1, "I know myself as Calibos...") — itself
    # self-referential, so the pinned path must ignore the gain entirely.
    pre_pinned = [r.id for r in ws.records if r.generated_by == "cartridge"]
    assert pre_pinned, "fresh subject must carry the pinned identity root"
    with subject._transaction():
        p1 = subject._add("memory", "Calibos is the name I answer to.",
                          generated_by="cartridge")
        p2 = subject._add("memory", "A quiet authored seed memory.",
                          generated_by="cartridge")
    # The transaction rebuilds the workspace via _restore — re-fetch it;
    # the pre-transaction object is stale.
    ws = subject.workspace
    ws.view()
    admitted = list(ws._last_view_ids)
    expected_pinned = pre_pinned + [p1.id, p2.id]
    assert admitted[:len(expected_pinned)] == expected_pinned, \
        "pinned records lead in record order, gain or no gain"
    assert set(expected_pinned) <= set(admitted), "all pinned admitted"


def test_nonboosted_relative_order_preserved():
    # The gain is purely additive: non-boosted records keep the relative
    # order the unboosted ranking gave them.
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed_cutoff_store(subject)
    ws = subject.workspace
    ws.view()
    filler_ids = [ids[f"F{i}"] for i in range(15)]
    admitted_fillers = [rid for rid in ws._last_view_ids if rid in filler_ids]
    assert admitted_fillers == filler_ids[:len(admitted_fillers)], \
        "fillers keep insertion (rank) order among themselves"
    # The room-16 cut drops exactly the lowest-ranked records.
    assert len(ws._last_view_ids) == 16


def test_recency_fallback_ignores_gain():
    # No salience tracker: the recency fallback must not boost
    # self-referential records at all. (The fallback re-chronologizes the
    # window oldest-first; the assertion is on that unchanged order.)
    ws = CalibosWorkspace()
    assert ws.display_name is None  # class default: no name, no boost
    ws.add(1, "thought", "Calibos had a strange dream about ladders.")
    ws.add(2, "thought", "The kettle whistled twice before boiling.")
    view = ws.view()
    texts = [e.first_person for e in view.experiences]
    assert texts == ["Calibos had a strange dream about ladders.",
                     "The kettle whistled twice before boiling."], \
        "recency fallback unchanged by self-referential content"


# -- fitness: display_name wiring --------------------------------------------

def test_display_name_wired_on_init_and_survives_restore():
    tmp = _tmp()
    subject = _make_subject(tmp)
    assert subject.engine.state.display_name == "Calibos"
    assert subject.workspace.display_name == "Calibos"
    # A transaction boundary rebuilds the workspace via from_dict (which
    # drops ad-hoc attributes); _restore must re-set the name.
    with subject._transaction():
        pass
    assert subject.workspace.display_name == "Calibos", \
        "display_name must survive _restore"


def test_plain_workspace_defaults_to_none_name():
    assert CalibosWorkspace.display_name is None


# -- fitness: determinism -----------------------------------------------------

def test_determinism_identical_views_identical_admission_order():
    tmp = _tmp()
    subject = _make_subject(tmp)
    _seed_cutoff_store(subject)
    ws = subject.workspace
    first = [ws.view(), ws._last_view_ids][1]
    second = [ws.view(), ws._last_view_ids][1]
    assert first == second


def test_determinism_two_stores_same_admission_text_order():
    orders = []
    for _ in range(2):
        tmp = _tmp()
        subject = _make_subject(tmp)
        _seed_cutoff_store(subject)
        ws = subject.workspace
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
    tracker = subject.workspace.salience_tracker
    before = drift_report(subject.inspect(), tracker, window=10)
    for _ in range(3):
        subject.workspace.view()
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
        import io
        from contextlib import redirect_stdout
        args = argparse.Namespace(raw=True)
        buf1, buf2 = io.StringIO(), io.StringIO()
        with redirect_stdout(buf1):
            cli.cmd_status(args)
        for _ in range(3):
            subject.workspace.view()
        with redirect_stdout(buf2):
            cli.cmd_status(args)
        assert buf1.getvalue() == buf2.getvalue(), \
            "status output must be byte-identical across views"
    finally:
        (cli.DB, cli.INBOX, cli.SALIENCE, cli._subject) = saved


def test_dream_tick_isolation_holds_with_selfgain():
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        subject._add("memory", "Calibos dreamed of a house with no doors.")
        subject._add("memory", "This event concerns me: the wind changed.")
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
