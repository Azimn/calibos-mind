"""Critic attack tests for the contradiction-flag surfacing mutation,
adopted into the suite in builder round 2 (2026-11-17) after the
availability_path fix. Attacks A2 / A7-boost originally proved the
_restore leak; they now wire the exclusion the way cli._subject does
(availability_path threaded onto the subject) and assert it survives
_restore. All fixtures on /tmp — the live store is never touched.

Spec: research/specs/2026-11-17-contradiction-surfacing.md
Each test is an attack: it FAILS iff the mutation violates the spec's
hard constraints or the regression genome. Passing tests are evidence
the attack found nothing; they are not builder tests.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import calibos_mind.cli as cli
import calibos_mind.consolidate as C
from calibos_mind.contradiction import flagged_record_ids
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject
from digital_subject.cartridge import load_cartridge


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-attack-"))


def _make_subject(tmp: Path, proposals_path=None,
                  availability_path=None) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        proposals_path=(str(proposals_path)
                        if proposals_path is not None else None),
        availability_path=(str(availability_path)
                           if availability_path is not None else None),
    )


def _write_journal(proposals_dir: Path, props: dict):
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


# Cutoff layout (same as the builder's test fixtures): 15 fillers at
# importance 1.0 -> activation 0.6, targets at 0.9 -> 0.54, +0.5 -> 1.04.
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
_T1_TEXT = ("I love the morning walk through the old garden when the "
            "light is soft.")
_T2_TEXT = ("I do not love the morning walk through the old garden when "
            "the rain is falling.")


def _snapshot_files(root: Path):
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# -- Attack A1: provider on a missing journal must not create files -------
def test_attack_provider_missing_journal_creates_nothing():
    # Read-only genome: a view on a fresh store (no proposals dir at all)
    # must not create the directory, the journal, a backup, or a sentinel.
    # load_journal(restore=False) must be a pure read.
    tmp = _tmp()
    pdir = tmp / "proposals"
    assert not pdir.exists()
    assert flagged_record_ids(pdir) == set()
    assert not pdir.exists(), \
        "provider must not create the proposals directory on a missing journal"
    assert list(tmp.rglob("*")) == [], \
        "provider must not create any file on a missing journal"
    # Same via a live subject's provider + view on a fresh store.
    subject = _make_subject(tmp, proposals_path=pdir)
    with subject._transaction():
        subject._add("thought", "A quiet note about the garden gate.")
    assert subject.workspace.contradiction_provider() == set()
    subject.workspace.view()
    assert not pdir.exists(), \
        "view on a fresh store must not create the proposals directory"


# -- Attack A2: flagged + archived record must never be admitted -----------
def test_attack_flagged_archived_record_never_admitted():
    # The boost must never admit a record the availability exclusion
    # removed: seed a store, archive one record via the real quarantine
    # path, flag it via a pending contradiction-flag, boost it hard, and
    # demand it stay out of the view — before AND after a transaction
    # (which rebuilds the workspace via _restore).
    tmp = _tmp()
    pdir = tmp / "proposals"
    adir = tmp / "archive"
    adir.mkdir(parents=True, exist_ok=True)
    # Wire the availability exclusion exactly as cli._subject does:
    # threaded onto the subject, so it survives every _restore
    # (round-2 fix for the leak these attacks originally proved).
    subject = _make_subject(tmp, proposals_path=pdir,
                            availability_path=adir / "availability.json")
    ids = {}
    with subject._transaction():
        for i, text in enumerate([
            "The blue bicycle leans against the fence by the library steps.",
            "A brass key turned in a lock I had never seen before.",
            "The attic window showed a field I did not recognize.",
            "Someone left a pair of gloves on the bench by the pond.",
        ]):
            ids[f"R{i}"] = subject._add("memory", text).id
    tracker = subject.workspace.salience_tracker
    for i in range(4):
        tracker.add_importance(ids[f"R{i}"], 0, 1.0)
    # Archive R0 through the real quarantine path (writes availability.json).
    C.quarantine(str(tmp / "mind.db"), str(adir), ids["R0"],
                 "critic attack: archived record")
    # Flag the archived record (and R1, as its live pair) and boost it.
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["R0"], ids["R1"])})
    ws = subject.workspace
    assert ws.contradiction_provider() == {ids["R0"], ids["R1"]}
    ws.view()
    assert ids["R0"] not in set(ws._last_view_ids), \
        "flagged ARCHIVED record admitted before any transaction"
    # Now force a _restore (fresh workspace per transaction) and re-check.
    with subject._transaction():
        pass
    ws = subject.workspace
    assert ws.contradiction_provider() == {ids["R0"], ids["R1"]}
    ws.view()
    assert ids["R0"] not in set(ws._last_view_ids), \
        "flagged ARCHIVED record admitted after _restore"
    assert ids["R1"] in set(ws._last_view_ids), \
        "the live side of the flagged pair must still admit"


def test_attack_boost_decides_archived_admission():
    # Sharpening of A2: the boost must not be the DECIDING factor in
    # admitting an excluded record. Cutoff layout: the archived record
    # sits at 0.54, below the 0.6 fillers, so unflagged it loses the
    # admission race; flagged (+0.5 -> 1.04) it must still stay out —
    # the exclusion is a gate, not a ranking input. If the flagged
    # version admits, the gain puts an archived record in front of the
    # thinker.
    tmp = _tmp()
    pdir = tmp / "proposals"
    adir = tmp / "archive"
    adir.mkdir(parents=True, exist_ok=True)
    subject = _make_subject(tmp, proposals_path=pdir,
                            availability_path=adir / "availability.json")
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLERS):
            ids[f"F{i}"] = subject._add(source, text).id
        ids["A"] = subject._add("memory", _T1_TEXT).id
        ids["B"] = subject._add("memory", _T2_TEXT).id
    tracker = subject.workspace.salience_tracker
    for i in range(len(_FILLERS)):
        tracker.add_importance(ids[f"F{i}"], 0, 1.0)
    tracker.add_importance(ids["A"], 0, 0.9)
    tracker.add_importance(ids["B"], 0, 0.9)
    C.quarantine(str(tmp / "mind.db"), str(adir), ids["A"],
                 "critic attack: archived record at the cutoff")
    # Force the _restore first: the exclusion must survive it.
    with subject._transaction():
        pass
    ws = subject.workspace
    assert ws.availability_path == str(adir / "availability.json"), \
        "premise: _restore must preserve availability_path (round-2 fix)"
    # Control: no journal yet, so no flags. The archived record sits at
    # 0.54, below the 0.6 fillers — it loses the admission race, and the
    # exclusion gate keeps it out regardless.
    assert ws.contradiction_provider() == set()
    ws.view()
    assert ids["A"] not in set(ws._last_view_ids), \
        "control: unflagged archived record must lose the race"
    assert ids["B"] not in set(ws._last_view_ids), \
        "control: unflagged target must lose the race"
    # Now flag the pair. The live side admits (correct); the archived
    # side must still stay out — the exclusion is a gate, not a ranking
    # input. If the flagged version admits, the gain (not just the
    # pre-existing leak) puts an archived record in front of the thinker.
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["A"], ids["B"])})
    assert ws.contradiction_provider() == {ids["A"], ids["B"]}
    ws.view()
    assert ids["B"] in set(ws._last_view_ids), \
        "live side of the flagged pair must admit"
    assert ids["A"] not in set(ws._last_view_ids), \
        "flagged ARCHIVED record admitted: the +0.5 boost decided it"


# -- Attack A3: accept() on a hand-built flag must clear the boost ---------
def test_attack_accept_handbuilt_flag_clears_boost():
    # Fitness item 2 says "resolve (or reject) the flag". The builder
    # tests reject on a hand-built flag and accept on a real-scan flag.
    # Accept on a WELL-FORMED hand-built flag (correct a/b hashes, as
    # the real scan mints) must also clear the boost.
    # NOTE (test-premise correction, 2026-11-17): the first version of
    # this attack used the dummy hashes "x"/"y" from the builder's
    # _flag_prop helper; accept() correctly refuses those because
    # _verify_pair's hash check is a deliberate safety invariant for
    # every kind. That red was a test-side error, not a code defect:
    # a flag with fake hashes is not a realistic flag. This version
    # uses the real hashes, as the scan mints them.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ta = "I love the morning walk through the old garden."
    tb = "I do not love the morning walk in the rain."
    ids = {}
    with subject._transaction():
        ids["A"] = subject._add("memory", ta).id
        ids["B"] = subject._add("memory", tb).id
    prop = _flag_prop(1, ids["A"], ids["B"])
    prop["a_hash"] = C.exact_hash(ta)
    prop["b_hash"] = C.exact_hash(tb)
    _write_journal(pdir, {"1": prop})
    assert subject.workspace.contradiction_provider() == {ids["A"], ids["B"]}
    outcomes = C.accept(str(tmp / "mind.db"), str(pdir),
                        str(tmp / "archive"), [1])
    assert outcomes[0]["ok"] is True, \
        f"accept on a well-formed hand-built flag must succeed, got {outcomes}"
    assert subject.workspace.contradiction_provider() == set(), \
        "accepted flag must drop the boost"


# -- Attack A4: provider cost per view at live-journal scale ---------------
def test_attack_provider_cost_per_view():
    # Brief (f): the provider re-reads + re-parses the whole journal on
    # every view. Measure at live scale (~438 proposals) and demand the
    # per-view provider cost stay negligible against the view itself;
    # anything above 5 ms per view is a hot-path regression.
    tmp = _tmp()
    pdir = tmp / "proposals"
    props = {}
    for i in range(1, 439):
        props[str(i)] = _flag_prop(
            i, f"experience-{2*i}", f"experience-{2*i+1}")
    _write_journal(pdir, props)
    t0 = time.perf_counter()
    for _ in range(200):
        flagged = flagged_record_ids(pdir)
    dt = (time.perf_counter() - t0) / 200
    assert flagged == {f"experience-{i}" for i in range(2, 878)}
    assert dt < 0.005, f"provider costs {dt*1000:.2f} ms/view at live scale"


# -- Attack A5: journal-LOST diagnostic must survive the provider ---------
def test_attack_journal_loss_signal_not_swallowed():
    # consolidate.load_journal's design (Oct-17 incident): a lost journal
    # (sentinel seq > 0, primary missing, no backup) is "never a quiet
    # empty". The provider suppresses load_journal's stderr wholesale.
    # If the waker's only other channel is `consolidate --list`, prove
    # the loss is still diagnosable there — and that the provider path
    # at least does not claim a healthy empty.
    import io
    from contextlib import redirect_stderr
    tmp = _tmp()
    pdir = tmp / "proposals"
    pdir.mkdir(parents=True, exist_ok=True)
    # Sentinel claiming a journal existed (seq > 0); no primary, no backup.
    (pdir / "journal.seq").write_text(
        json.dumps({"seq": 42, "mtime": 0.0}) + "\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stderr(buf):
        assert flagged_record_ids(pdir) == set()
    provider_diag = buf.getvalue()
    # The provider path itself stays silent (stderr-flood discipline);
    # the loss must still be diagnosable through the consolidate path.
    buf2 = io.StringIO()
    with redirect_stderr(buf2):
        journal = C.load_journal(pdir, restore=False)
    assert journal == {"seq": 0, "proposals": {}}
    assert "LOST" in buf2.getvalue(), \
        "consolidate --list path must still diagnose a lost journal"
    assert provider_diag == "", \
        "provider must not flood stderr (its own discipline)"


# -- Attack A6: near-dup twin of a PINNED record ---------------------------
def test_attack_flagged_near_dup_of_pinned_record():
    # _dedupe never dedupes against pinned roots ("handled before this").
    # A flagged record that near-duplicates the pinned identity root
    # must not ride in alongside it on the boost alone — or, if the
    # pre-existing pinned exemption admits it, the boost must not be the
    # cause: the same record unflagged must admit identically.
    tmp = _tmp()
    pdir = tmp / "proposals"
    subject = _make_subject(tmp, proposals_path=pdir)
    ws = subject.workspace
    pinned = [r for r in ws.records if r.generated_by == "cartridge"]
    assert pinned, "fresh subject must carry the pinned identity root"
    root_text = pinned[0].first_person
    ids = {}
    with subject._transaction():
        ids["TWIN"] = subject._add("thought", root_text).id
    tracker = subject.workspace.salience_tracker
    tracker.add_importance(ids["TWIN"], 0, 1.0)
    # Unflagged baseline: is the exact twin of the pinned root admitted?
    ws.view()
    baseline_admitted = ids["TWIN"] in set(ws._last_view_ids)
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["TWIN"], ids["TWIN"])})
    ws.view()
    flagged_admitted = ids["TWIN"] in set(ws._last_view_ids)
    assert flagged_admitted == baseline_admitted, \
        "boost must not change admission of a pinned-root twin"


# -- Attack A7: provider return re-read per view (cutoff layout) ----------
def test_attack_provider_return_reread_per_view():
    # The gain must treat each provider return as fresh data: a provider
    # returning a set the caller then mutates must be re-read on the
    # next view (no aliasing of a cached set, no stale flagged set).
    # NOTE (test-premise correction, 2026-11-17): the first version of
    # this attack seeded only two records, so the 16-slot window
    # admitted both with or without the boost — the premise could not
    # isolate the gain. This version uses the cutoff layout (fillers at
    # 1.0 -> 0.6, targets at 0.9 -> 0.54, +0.5 -> 1.04) so admission
    # is decided by the flag alone.
    tmp = _tmp()
    subject = _make_subject(tmp, proposals_path=tmp / "proposals")
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLERS):
            ids[f"F{i}"] = subject._add(source, text).id
        ids["A"] = subject._add("memory", _T1_TEXT).id
        ids["B"] = subject._add("memory", _T2_TEXT).id
    tracker = subject.workspace.salience_tracker
    for i in range(len(_FILLERS)):
        tracker.add_importance(ids[f"F{i}"], 0, 1.0)
    tracker.add_importance(ids["A"], 0, 0.9)
    tracker.add_importance(ids["B"], 0, 0.9)
    ws = subject.workspace
    shared = {ids["A"]}
    ws.contradiction_provider = lambda: shared
    ws.view()
    assert ids["A"] in set(ws._last_view_ids)
    assert ids["B"] not in set(ws._last_view_ids)
    shared.clear()
    shared.add(ids["B"])
    ws.view()
    assert ids["B"] in set(ws._last_view_ids)
    assert ids["A"] not in set(ws._last_view_ids), \
        "stale flagged set must not keep A admitted after the flag moved"


# -- Regression: the exclusion must survive MANY transactions -------------
def test_attack_availability_survives_multiple_transactions():
    # Round-2 fix check: the availability path is threaded onto the
    # subject and re-set on every _restore, so the exclusion must hold
    # across an arbitrary run of transactions, not just the first.
    tmp = _tmp()
    pdir = tmp / "proposals"
    adir = tmp / "archive"
    adir.mkdir(parents=True, exist_ok=True)
    subject = _make_subject(tmp, proposals_path=pdir,
                            availability_path=adir / "availability.json")
    ids = {}
    with subject._transaction():
        for i, text in enumerate([
            "The blue bicycle leans against the fence by the library steps.",
            "A brass key turned in a lock I had never seen before.",
            "The attic window showed a field I did not recognize.",
            "Someone left a pair of gloves on the bench by the pond.",
        ]):
            ids[f"R{i}"] = subject._add("memory", text).id
    tracker = subject.workspace.salience_tracker
    for i in range(4):
        tracker.add_importance(ids[f"R{i}"], 0, 1.0)
    C.quarantine(str(tmp / "mind.db"), str(adir), ids["R0"],
                 "critic attack: archived record, many transactions")
    _write_journal(pdir, {
        "1": _flag_prop(1, ids["R0"], ids["R1"])})
    for n in range(5):
        with subject._transaction():
            pass
        ws = subject.workspace
        assert ws.availability_path == str(adir / "availability.json"), \
            f"availability_path lost after transaction {n + 1}"
        assert ws.contradiction_provider() == {ids["R0"], ids["R1"]}
        ws.view()
        assert ids["R0"] not in set(ws._last_view_ids), \
            f"flagged ARCHIVED record admitted after transaction {n + 1}"
        assert ids["R1"] in set(ws._last_view_ids), \
            f"live side of the flagged pair must admit after transaction {n + 1}"
