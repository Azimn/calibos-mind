"""Tests for familiarity traces (near-miss retrieval streaks).

Spec: research/spec-familiarity-trace-2026-09-28.md
All fixtures live in /tmp — the live store is never touched.

The core mechanism is exercised through CalibosWorkspace.view() +
FamiliarityTracker.observe(), exactly the pair cli._run_tick wires
together: a view builds _last_view_ids / _last_near_miss_ids, observe folds
them into the streaks.

Ranking control: every record is seeded at tick 0 with zero importance, so
activation ties on (0, 0) and the stable sort keeps insertion order — the
test author places the target record exactly. Filler sources are spread
across the per-class caps so exactly VIEW_LIMIT (16) records admit and the
target sits at ranked position 17, just below the cut, inside the
FAMILIARITY_WINDOW (32).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.familiarity import (
    FAMILIARITY_BOOST,
    FAMILIARITY_THRESHOLD,
    FAMILIARITY_WINDOW,
    FamiliarityTracker,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="fam-test-"))


def _make_subject(tmp: Path, with_familiarity: bool = True) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        interoception_path=None,
        familiarity_path=(str(tmp / "familiarity.json")
                          if with_familiarity else None),
    )


_FILLER_TOPICS = [
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
    ("interoception", "My shoulders hold yesterday's tension like a debt."),
    ("interoception", "There is a lightness behind my eyes this morning."),
    ("temporal", "The deadline moved again; the week reshuffles itself."),
    ("temporal", "Three days until the visit; the countdown has texture."),
    ("social", "Mara laughed at the joke before I finished telling it."),
    ("social", "The meeting ended early and nobody knew what to do."),
]
_T_TARGET = ("memory",
             "The lighthouse keeper's log mentioned a storm I never witnessed.")
_FILLER_TAIL = [
    ("memory", "A postcard arrived with no message, only a stamp."),
    ("social", "The barista remembered my order without asking."),
    ("action_consequence", "Watering the fern revived it within a day."),
    ("action_consequence", "The letter I mailed came back unopened."),
    ("imagination", "A city built entirely of staircases and bells."),
    ("imagination", "The moon as a coin dropped in a dark well."),
]


def _seed(subject: CalibosSubject):
    """Seed head fillers, the target T, then tail fillers. Returns ids."""
    ids = {}
    with subject._transaction():
        for i, (source, text) in enumerate(_FILLER_TOPICS):
            r = subject._add(source, text)
            ids[f"F{i}"] = r.id
        t = subject._add(*_T_TARGET)
        ids["T"] = t.id
        for i, (source, text) in enumerate(_FILLER_TAIL):
            r = subject._add(source, text)
            ids[f"tail{i}"] = r.id
    return ids


def _cycle(subject: CalibosSubject, save: bool = True):
    """One waking view+observe cycle, mirroring cli._run_tick's core."""
    ws = subject.workspace
    ws.view()
    tracker = ws.familiarity_tracker
    changed = tracker.observe(ws._last_view_ids, ws._last_near_miss_ids)
    if changed and save:
        tracker.save()
    return changed


def _patched_cli(tmp: Path):
    """Point the CLI at synthetic paths (mirrors tests/test_init.py's
    fixture, plus the familiarity sidecar). Returns (make_subject, restore)."""
    db = tmp / "mind.db"
    salience = tmp / "salience.json"
    interoception = tmp / "interoception.json"
    familiarity = tmp / "familiarity.json"
    habits = tmp / "habits-formed.json"
    inbox = tmp / "inbox"
    proposals = tmp / "proposals"
    archive = tmp / "archive"
    ambivalence = tmp / "ambivalence.json"
    provenance = tmp / "provenance.json"
    saved = (cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION,
             cli.FAMILIARITY, cli.HABITS, cli.AMBIVALENCE, cli.PROVENANCE, cli.PROPOSALS,
             cli.ARCHIVE, cli._subject)
    cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY, cli.HABITS, \
        cli.AMBIVALENCE, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE = (
            db, inbox, salience, interoception, familiarity, habits,
            ambivalence, provenance, proposals, archive)
    inbox.mkdir(exist_ok=True)
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(salience),
                              interoception_path=str(interoception),
                              familiarity_path=str(familiarity))

    cli._subject = make_subject
    return make_subject, saved


def _restore_cli(saved):
    (cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY,
     cli.HABITS, cli.AMBIVALENCE, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE,
     cli._subject) = saved


# -- fitness: streak builds to threshold, boost engages -----------------------

def test_streak_builds_to_threshold_then_boost_engages():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed(subject)
    t, f0 = ids["T"], ids["F0"]
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    for cycle in range(1, 4):
        _cycle(subject)
        assert t in ws._last_near_miss_ids, f"cycle {cycle}: T must near-miss"
        assert t not in ws._last_view_ids, f"cycle {cycle}: T not admitted yet"
        assert tracker.data["streaks"].get(t) == cycle
        if cycle < FAMILIARITY_THRESHOLD:
            assert tracker.boost_for(t) == 0.0
    assert tracker.data["streaks"][t] == FAMILIARITY_THRESHOLD == 3
    assert tracker.boost_for(t) == FAMILIARITY_BOOST == 0.5


def test_boost_admits_then_streak_resets():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed(subject)
    t = ids["T"]
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    for _ in range(3):
        _cycle(subject)
    assert tracker.boost_for(t) == 0.5
    # 4th view: the nudge lifts T above the cut.
    _cycle(subject)
    assert t in ws._last_view_ids, "boosted T must be admitted on the 4th view"
    assert t not in ws._last_near_miss_ids
    assert t not in tracker.data["streaks"], "admission resets the streak"
    assert tracker.boost_for(t) == 0.0


def test_boost_bounded_flat():
    tmp = _tmp()
    tracker = FamiliarityTracker(tmp / "familiarity.json")
    tracker.data["streaks"]["experience-9"] = 100
    assert tracker.boost_for("experience-9") == 0.5, \
        "streak 100 still yields exactly the flat boost"


def test_never_near_miss_means_no_streak():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed(subject)
    f0 = ids["F0"]
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    for _ in range(5):
        _cycle(subject)
    # F0 is always admitted (ranked first), so it never near-misses.
    assert f0 in ws._last_view_ids
    assert f0 not in tracker.data["streaks"]
    assert tracker.boost_for(f0) == 0.0


# -- fitness: archived records never near-miss --------------------------------

def test_archived_record_never_near_misses():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed(subject)
    t = ids["T"]
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    # Archive T while giving it the highest activation in the store: it
    # would rank #1 if it ever reached `ranked`.
    tracker2 = subject.workspace.salience_tracker
    tracker2.add_importance(t, 0, 5.0)
    archive = tmp / "archive"
    archive.mkdir(exist_ok=True)
    (archive / "availability.json").write_text(
        json.dumps({"excluded": {t: {"reason": "test archive"}}}),
        encoding="utf-8")
    ws.availability_path = archive / "availability.json"
    for _ in range(4):
        _cycle(subject)
        assert t not in ws._last_view_ids
        assert t not in ws._last_near_miss_ids, \
            "archived records never reach ranked, so they can never near-miss"
    assert t not in tracker.data["streaks"]
    assert tracker.boost_for(t) == 0.0


# -- fitness: no-op write discipline ------------------------------------------

def test_observe_noop_absent_stays_absent():
    tmp = _tmp()
    subject = _make_subject(tmp)
    # Fewer records than the view cut: everything admits, nothing near-misses.
    with subject._transaction():
        subject._add("thought", "A quiet synthetic sentence for the no-op test.")
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    sidecar = tmp / "familiarity.json"
    assert not sidecar.exists()
    changed = _cycle(subject, save=False)
    assert changed is False
    assert not sidecar.exists(), "absent stays absent when nothing changed"


def test_observe_noop_present_stays_byte_identical():
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        subject._add("thought", "A quiet synthetic sentence for the no-op test.")
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    sidecar = tmp / "familiarity.json"
    tracker.save()  # present but empty streaks
    before = sidecar.read_bytes()
    changed = _cycle(subject)
    assert changed is False
    # The caller saves only when observe returns True.
    assert sidecar.read_bytes() == before, "present stays byte-identical"


def test_observe_returns_true_only_on_change():
    tmp = _tmp()
    tracker = FamiliarityTracker(tmp / "familiarity.json")
    assert tracker.observe(["a"], []) is False
    assert tracker.observe(["a"], ["b"]) is True       # streak increment
    assert tracker.observe(["a", "b"], ["b"]) is True  # admission reset
    assert tracker.observe(["a"], []) is False
    tracker.data["streaks"]["zz"] = 2
    assert tracker.observe(["a"], []) is True          # pruning
    assert "zz" not in tracker.data["streaks"]
    assert tracker.observe(["a"], []) is False


# -- fitness: determinism -----------------------------------------------------

def test_determinism_byte_identical_sidecars():
    paths = []
    for _ in range(2):
        tmp = _tmp()
        subject = _make_subject(tmp)
        _seed(subject)
        for _ in range(4):
            _cycle(subject)
        paths.append(tmp / "familiarity.json")
    assert paths[0].read_bytes() == paths[1].read_bytes()
    assert len(paths[0].read_bytes()) > 0


# -- fitness: init --force deletes the sidecar --------------------------------

def test_init_force_deletes_familiarity_sidecar():
    tmp = _tmp()
    make_subject, saved = _patched_cli(tmp)
    try:
        make_subject()  # creates the DB
        fam = tmp / "familiarity.json"
        tracker = FamiliarityTracker(fam)
        tracker.data["streaks"] = {"experience-1": 3, "experience-2": 1}
        tracker.save()
        assert fam.exists()
        rc = cli.cmd_init(argparse.Namespace(force=True))
        assert rc == 0
        assert not fam.exists(), "init --force must delete the sidecar"
        # The store itself reseeded cleanly.
        ids = sorted(r["id"] for r in make_subject().inspect()["workspace"]["records"])
        assert ids, "reseed must still seed the store"
    finally:
        _restore_cli(saved)


# -- fitness: dream-path isolation --------------------------------------------

def test_dream_tick_accumulates_no_streaks():
    tmp = _tmp()
    subject = _make_subject(tmp)
    _seed(subject)
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    for _ in range(3):
        _cycle(subject)
    assert tracker.data["streaks"], "precondition: streaks exist before sleep"
    sidecar = tmp / "familiarity.json"
    before = sidecar.read_bytes()
    subject.dream_tick()
    assert sidecar.read_bytes() == before, \
        "dream ticks must not touch the familiarity sidecar"
    assert tracker.data["streaks"] == json.loads(before.decode())["streaks"]


def test_view_construction_alone_never_writes():
    tmp = _tmp()
    subject = _make_subject(tmp)
    _seed(subject)
    ws = subject.workspace
    sidecar = tmp / "familiarity.json"
    for _ in range(5):
        ws.view()  # read-only commands construct views; they must not write
    assert not sidecar.exists()


# -- regression genome: the boost must not leak into the substrate -------------

def test_boost_does_not_touch_activation_or_salience_sidecar():
    tmp = _tmp()
    subject = _make_subject(tmp)
    ids = _seed(subject)
    t = ids["T"]
    ws = subject.workspace
    stracker = ws.salience_tracker
    ftracker = ws.familiarity_tracker
    sal_sidecar = tmp / "salience.json"
    stracker.save()
    sal_before = sal_sidecar.read_bytes()
    act_before = stracker.activation(t, 0, 0)
    for _ in range(3):
        _cycle(subject)  # builds streaks; 3rd engages the boost
    assert ftracker.boost_for(t) == 0.5
    assert stracker.activation(t, 0, 0) == act_before, \
        "activation() itself is untouched by the familiarity machinery"
    assert sal_sidecar.read_bytes() == sal_before, \
        "views must not write the salience sidecar"


def test_pinned_records_never_near_miss():
    tmp = _tmp()
    subject = _make_subject(tmp)
    with subject._transaction():
        pinned = subject._add("memory", "A cartridge root that pins the view.",
                              generated_by="cartridge")
    ids = _seed(subject)
    ws = subject.workspace
    tracker = ws.familiarity_tracker
    for _ in range(4):
        _cycle(subject)
        assert pinned.id in ws._last_view_ids, "pinned records always admit"
        assert pinned.id not in ws._last_near_miss_ids
    assert pinned.id not in tracker.data["streaks"]


def test_no_familiarity_tracker_no_boost_no_crash():
    tmp = _tmp()
    subject = _make_subject(tmp, with_familiarity=False)
    ids = _seed(subject)
    ws = subject.workspace
    assert ws.familiarity_tracker is None
    for _ in range(2):
        ws.view()
        assert ws._last_near_miss_ids, "side-channel is still captured"
    assert not (tmp / "familiarity.json").exists()


# -- integration: _run_tick wires observe once per waking tick -----------------

def test_run_tick_accumulates_streaks_and_saves():
    tmp = _tmp()
    make_subject, saved = _patched_cli(tmp)
    try:
        subject = make_subject()
        _seed(subject)
        sidecar = tmp / "familiarity.json"
        for _ in range(3):
            cli._run_tick(subject)
        assert sidecar.exists(), "_run_tick must persist streak changes"
        streaks = json.loads(sidecar.read_text(encoding="utf-8"))["streaks"]
        assert streaks, "three waking ticks must accumulate near-miss streaks"
        assert all(isinstance(v, int) and v >= 1 for v in streaks.values())
        # And the dream path leaves it alone afterwards.
        before = sidecar.read_bytes()
        subject.dream_tick()
        assert sidecar.read_bytes() == before
    finally:
        _restore_cli(saved)


if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
