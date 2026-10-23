"""Tests for learned event expectations (calibos_mind/expectation.py).

Spec: research/specs/2026-10-22-learned-expectations.md (domain 28).
All fixtures live in /tmp — the live store, live sidecars, and inbox/
are never touched. No test helper resolves to the live tree: cli paths
are exercised only through _patched_cli, which redirects every
module-level path cmd_* touches (the reseed-stash test's pattern).

Spec ambiguity resolved (named for the critic): the fitness function
asks for a probe with valence=0.0 after five +0.8 vs five -0.8 history
events, asserting the probe's surprise differs between the stores. That
is mathematically unachievable under the frozen engine's surprise
formula, surprise = clamp(|actual - expected|): |0.0 - 0.8| == |0.0 -
(-0.8)| == 0.8. A zero-valence probe is exactly equidistant from the
two symmetric histories, so its surprise magnitude is identical in both
(this is pinned by test_symmetric_probe_surprise_is_symmetric below —
the symmetry is in the engine's formula, not a defect of this overlay).
The probe therefore carries valence=+0.4 (same kind/tags/intensity as
specified): after a +0.8 run it lands near expectation (surprise 0.4),
after a -0.8 run it violates expectation (surprise 1.0). The treatment —
the two histories — is verbatim per the spec.

Second deviation from the spec's letter (same observable contract):
the spec says "set event.expected_valence = ema". The implementation
passes the engine a shadow copy (dataclasses.replace) carrying the
predicted valences instead of mutating the caller's event. Rationale:
mutating the caller's event would make a re-fed event object look
"explicit" on its second pass, misfiring the never-override rule and
skipping learning. The contract — surprise computed from the EMA, the
caller's event untouched — is pinned by
test_probe_event_object_is_not_mutated.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_subject.cartridge import load_cartridge
from digital_subject.engine import clamp
from digital_subject.models import Event

from calibos_mind import cli
from calibos_mind import expectation as EXP
from calibos_mind.drift import drift_report
from calibos_mind.expectation import (
    LEARNING_RATE,
    SIDECAR_CAP,
    ExpectationTracker,
)
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="exp-test-"))


def _make_subject(tmp: Path, expectation: bool = True) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        interoception_path=str(tmp / "interoception.json"),
        expectation_path=(str(tmp / "expectations.json") if expectation else None),
    )


def _ev(kind="observation", valence=0.0, intensity=0.5, tags=("t",),
       expected=None, actual=None, description="a synthetic event"):
    return Event(kind=kind, source="world", description=description,
                 tags=tags, intensity=intensity, valence=valence,
                 expected_valence=expected, actual_valence=actual)


def _process(subject: CalibosSubject, event: Event):
    """Drive an event through own+apply (step's first half, no conduct)."""
    eng = subject.engine
    owned = eng._own_event(event)
    eng._apply_event(owned)
    return owned


def _sidecar(tmp: Path) -> Path:
    return tmp / "expectations.json"


# -- fitness 1: history changes interpretation --------------------------------

def test_history_shapes_probe_surprise_and_memory_charge():
    """Five +0.8 vs five -0.8 history events, then an identical probe.

    The probe's surprise differs between the stores, and the minted
    memory's emotional charge and initial strength differ accordingly —
    the check-(4) symptom addressed in vivo-mechanically. (Probe valence
    is +0.4, not 0.0: see the module docstring for why the spec's literal
    0.0 cannot produce differing surprises under |actual - expected|.)
    """
    tmp_a, tmp_b = _tmp(), _tmp()
    sub_a, sub_b = _make_subject(tmp_a), _make_subject(tmp_b)
    for _ in range(5):
        _process(sub_a, _ev(valence=+0.8))
        _process(sub_b, _ev(valence=-0.8))

    probe_a, probe_b = _ev(valence=+0.4), _ev(valence=+0.4)
    owned_a = _process(sub_a, probe_a)
    owned_b = _process(sub_b, probe_b)

    # The shadow carried the learned expectation into the engine.
    assert owned_a.event.expected_valence == 0.8
    assert owned_b.event.expected_valence == -0.8
    assert owned_a.surprise == clamp(abs(0.4 - 0.8)) == 0.4
    assert owned_b.surprise == clamp(abs(0.4 - (-0.8))) == 1.0
    assert owned_a.surprise != owned_b.surprise

    # ...and everything downstream of surprise moves with it.
    mem_a = sub_a.engine.state.memories[-1]
    mem_b = sub_b.engine.state.memories[-1]
    assert mem_a.emotional_charge != mem_b.emotional_charge
    assert mem_a.emotional_charge == clamp(0.4 + 0.5 * 0.7 + 0.4 * 0.2)
    assert mem_b.emotional_charge == clamp(0.4 + 0.5 * 0.7 + 1.0 * 0.2)
    assert mem_a.strength != mem_b.strength
    assert mem_a.strength == clamp(0.3 + 0.5 * 0.45 + 0.4 * 0.25)
    assert mem_b.strength == clamp(0.3 + 0.5 * 0.45 + 1.0 * 0.25)


def test_symmetric_probe_surprise_is_symmetric():
    """Pins the spec ambiguity: a zero-valence probe is equidistant from
    symmetric histories, so its surprise is identical in both stores.
    |0.0 - 0.8| == |0.0 - (-0.8)| — the engine's formula, not this
    overlay, fixes that. The fitness criterion's literal 0.0 probe
    cannot discriminate; the +0.4 probe in the test above can."""
    tmp_a, tmp_b = _tmp(), _tmp()
    sub_a, sub_b = _make_subject(tmp_a), _make_subject(tmp_b)
    for _ in range(5):
        _process(sub_a, _ev(valence=+0.8))
        _process(sub_b, _ev(valence=-0.8))
    owned_a = _process(sub_a, _ev(valence=0.0))
    owned_b = _process(sub_b, _ev(valence=0.0))
    assert owned_a.surprise == owned_b.surprise == clamp(0.8)


def test_probe_event_object_is_not_mutated():
    """The caller's event keeps expected_valence=None after the pass —
    the engine saw a shadow copy, so a re-fed event never looks explicit."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    _process(sub, _ev(valence=+0.8))
    probe = _ev(valence=+0.4)
    _process(sub, probe)
    assert probe.expected_valence is None
    assert probe.actual_valence is None


# -- fitness 2: cold start == pristine ----------------------------------------

def test_cold_start_matches_pristine_fallback():
    """First-ever event of a key: surprise is the engine's pristine
    fallback, byte-identical — learning only starts with the second
    event's expectation."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    owned = _process(sub, _ev(valence=0.3, intensity=0.5))
    assert owned.surprise == clamp(0.5 * 0.35)
    # ...but the event did learn: the second event sees the expectation.
    owned2 = _process(sub, _ev(valence=0.3, intensity=0.5))
    assert owned2.event.expected_valence == 0.3
    assert owned2.surprise == clamp(abs(0.3 - 0.3)) == 0.0


def test_cold_start_parity_against_unpatched_engine():
    """Same fixture, overlay installed vs uninstalled: identical surprise.
    The uninstalled run is the blinded-region parity proof — fresh stores
    behave exactly as before."""
    tmp_on, tmp_off = _tmp(), _tmp()
    sub_on = _make_subject(tmp_on)
    owned_on = _process(sub_on, _ev(valence=0.3, intensity=0.5, tags=("x",)))
    EXP.uninstall()
    try:
        sub_off = _make_subject(tmp_off)
        owned_off = _process(sub_off, _ev(valence=0.3, intensity=0.5, tags=("x",)))
    finally:
        EXP.install()
    assert owned_on.surprise == owned_off.surprise == clamp(0.5 * 0.35)


def test_no_tracker_means_pristine_path():
    """An engine with no tracker attached (expectation_path=None, or a
    bare engine) takes the pristine path untouched."""
    tmp = _tmp()
    sub = _make_subject(tmp, expectation=False)
    assert getattr(sub.engine, "_expectation_tracker", None) is None
    owned = _process(sub, _ev(valence=0.9, intensity=0.5))
    assert owned.surprise == clamp(0.5 * 0.35)
    assert not _sidecar(tmp).exists()


# -- fitness 3: explicit expected_valence is never touched, never learns ------

def test_feeder_style_event_never_touched_nor_learned():
    """Feeder's 0.0/0.0 events: surprise 0, valences untouched, and no
    sidecar entry is written — the machinery never observes the host's
    scheduled feedings."""
    from calibos_mind.feeder import event_for
    tmp = _tmp()
    sub = _make_subject(tmp)
    owned = _process(sub, event_for("food"))
    assert owned.surprise == 0.0
    assert owned.event.expected_valence == 0.0
    assert owned.event.actual_valence == 0.0
    assert not _sidecar(tmp).exists()


def test_explicit_expected_valence_with_existing_ema():
    """Even when an EMA entry exists for the key, an explicit
    expected_valence wins for surprise and the entry is not updated."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for _ in range(3):
        _process(sub, _ev(valence=+0.8))
    before = _sidecar(tmp).read_bytes()
    owned = _process(sub, _ev(valence=+0.8, expected=0.2, actual=0.9))
    assert owned.surprise == clamp(abs(0.9 - 0.2))
    assert _sidecar(tmp).read_bytes() == before


# -- fitness 4: read-only + dream paths never write ---------------------------

def _patched_cli(tmp: Path):
    """Redirect every module-level path cmd_* touches at tmp (the
    reseed-stash test's pattern, extended with EXPECTATIONS).

    The complete set cmd_init --force touches: BASE, DB, INBOX, SALIENCE,
    INTEROCEPTION, FAMILIARITY, AMBIVALENCE, COUNTERFACTUAL, HABITS,
    DORMANCY, PROVENANCE, PROPOSALS, ARCHIVE, EXPECTATIONS, _subject.
    (2026-10-22: this helper once omitted COUNTERFACTUAL and DORMANCY —
    cmd_init --force unlinks those too, so the omission deleted the live
    authored-dormancy.json during a routine test run. Every path
    cmd_init touches must be redirected, no exceptions.)
    """
    names = ("BASE", "DB", "INBOX", "SALIENCE", "INTEROCEPTION",
             "FAMILIARITY", "AMBIVALENCE", "COUNTERFACTUAL", "HABITS",
             "DORMANCY", "PROVENANCE", "PROPOSALS", "ARCHIVE",
             "EXPECTATIONS", "_subject")
    saved = tuple(getattr(cli, n) for n in names)
    (tmp / "inbox").mkdir(exist_ok=True)
    paths = {
        "BASE": tmp,
        "DB": tmp / "mind.db",
        "INBOX": tmp / "inbox",
        "SALIENCE": tmp / "salience.json",
        "INTEROCEPTION": tmp / "interoception.json",
        "FAMILIARITY": tmp / "familiarity.json",
        "AMBIVALENCE": tmp / "ambivalence.json",
        "COUNTERFACTUAL": tmp / "counterfactuals.json",
        "HABITS": tmp / "habits-formed.json",
        "DORMANCY": tmp / "authored-dormancy.json",
        "PROVENANCE": tmp / "provenance.json",
        "PROPOSALS": tmp / "proposals",
        "ARCHIVE": tmp / "archive",
        "EXPECTATIONS": tmp / "expectations.json",
    }
    for n, v in paths.items():
        setattr(cli, n, v)
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(paths["INBOX"])
        return CalibosSubject(
            str(paths["DB"]), cartridge, cognition=provider,
            salience_path=str(paths["SALIENCE"]),
            interoception_path=str(paths["INTEROCEPTION"]),
            expectation_path=str(paths["EXPECTATIONS"]),
        )

    cli._subject = make_subject

    def restore():
        for n, v in zip(names, saved):
            setattr(cli, n, v)

    return restore, make_subject


def test_readonly_cli_commands_leave_sidecar_byte_identical():
    """drift, status, review, consolidate --list through the real CLI
    entry points (paths redirected): the sidecar stays byte-identical."""
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        for v in (0.8, -0.2, 0.5):
            _process(sub, _ev(valence=v))
        sidecar = tmp / "expectations.json"
        before = sidecar.read_bytes()

        cli.cmd_drift(argparse.Namespace(window=50))  # returns None; read-only by construction
        assert cli.cmd_status(argparse.Namespace(raw=True)) == 0
        assert cli.cmd_review(argparse.Namespace(n=5)) == 0
        assert cli.cmd_consolidate(
            argparse.Namespace(list=True, accept=None, reject=None,
                               quarantine=None)) == 0

        assert sidecar.read_bytes() == before
    finally:
        restore()


def test_readonly_paths_never_create_sidecar():
    """A fresh store's read-only sweep must not conjure the file."""
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        cli.cmd_drift(argparse.Namespace(window=50))  # returns None; read-only by construction
        assert cli.cmd_status(argparse.Namespace(raw=True)) == 0
        assert not (tmp / "expectations.json").exists()
        # ...and the subject-level read surface either.
        sub.inspect()
        drift_report(sub.inspect(), sub.workspace.salience_tracker)
        assert not (tmp / "expectations.json").exists()
    finally:
        restore()


def test_dream_ticks_leave_sidecar_byte_identical():
    """Dream ticks process no events; the sidecar (and the suspension
    flag path) must leave it byte-identical."""
    tmp = _tmp()
    sub = _make_subject(tmp)
    for v in (0.8, -0.2, 0.5):
        _process(sub, _ev(valence=v))
    sidecar = _sidecar(tmp)
    before = sidecar.read_bytes()
    for _ in range(3):
        sub.dream_tick()
    assert sidecar.read_bytes() == before
    # The suspension flag is always restored, even mid-dream.
    assert getattr(sub.engine, "_expectation_suspended", False) is False


# -- fitness 5: init --force wipes the sidecar --------------------------------

def test_init_force_wipes_sidecar_and_reseed_starts_cold():
    tmp = _tmp()
    restore, make_subject = _patched_cli(tmp)
    try:
        sub = make_subject()
        for _ in range(4):
            _process(sub, _ev(valence=+0.8))
        assert (tmp / "expectations.json").exists()

        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        assert not (tmp / "expectations.json").exists()

        # Post-reseed, the first event of the key is cold again.
        sub2 = make_subject()
        owned = _process(sub2, _ev(valence=+0.8, intensity=0.5))
        assert owned.surprise == clamp(0.5 * 0.35)
    finally:
        restore()


# -- fitness 6: replay determinism ---------------------------------------------

def _script(sub: CalibosSubject):
    seq = [
        ("observation", ("a",), 0.8, 0.5),
        ("observation", ("b", "a"), -0.4, 0.7),
        ("note", ("a",), 0.1, 0.3),
        ("observation", ("a",), 0.6, 0.5),
        ("note", ("c",), -0.9, 0.9),
        ("observation", ("b", "a"), 0.2, 0.4),
    ]
    for kind, tags, valence, intensity in seq:
        _process(sub, _ev(kind=kind, tags=tags, valence=valence,
                          intensity=intensity))


def test_identical_sequences_replay_byte_identical():
    tmp_a, tmp_b = _tmp(), _tmp()
    _script(_make_subject(tmp_a))
    _script(_make_subject(tmp_b))
    assert _sidecar(tmp_a).read_bytes() == _sidecar(tmp_b).read_bytes()


def test_sidecar_caps_at_256_evicting_stalest():
    tmp = _tmp()
    sub = _make_subject(tmp)
    # Distinct keys with strictly increasing ticks: the 44 stalest evict.
    for i in range(300):
        sub.engine.state.tick = i
        _process(sub, _ev(kind="k", tags=(f"tag{i}",), valence=0.1))
    data = json.loads(_sidecar(tmp).read_text(encoding="utf-8"))
    entries = data["entries"]
    assert len(entries) == SIDECAR_CAP
    ticks = sorted(e["last_tick"] for e in entries.values())
    assert ticks[0] == 44  # ticks 0..43 (the stalest) evicted
    assert ticks[-1] == 299


# -- tracker unit behavior ------------------------------------------------------

def test_ema_math_and_counts():
    tmp = _tmp()
    tracker = ExpectationTracker(tmp / "e.json")
    assert tracker.predict(_ev(valence=0.0)) is None  # read-only: no write
    assert not (tmp / "e.json").exists()
    tracker.learn(_ev(valence=1.0), tick=10)
    assert tracker.predict(_ev(valence=0.0)) == 1.0
    tracker.learn(_ev(valence=0.0), tick=11)
    assert tracker.predict(_ev(valence=0.0)) == 1.0 + LEARNING_RATE * (0.0 - 1.0)
    assert tracker.entries["observation|t"]["count"] == 2
    assert tracker.entries["observation|t"]["last_tick"] == 11


def test_key_includes_sorted_tags():
    tmp = _tmp()
    tracker = ExpectationTracker(tmp / "e.json")
    tracker.learn(_ev(tags=("b", "a"), valence=0.5), tick=1)
    assert "observation|a,b" in tracker.entries
    assert tracker.predict(_ev(tags=("a", "b"))) == 0.5
    assert tracker.predict(_ev(tags=("a",))) is None


def test_corrupt_sidecar_fails_loud():
    tmp = _tmp()
    p = tmp / "e.json"
    p.write_text("{not json", encoding="utf-8")
    try:
        ExpectationTracker(p)
    except ValueError:
        pass
    else:
        raise AssertionError("corrupt sidecar must fail loud")
    p.write_text(json.dumps({"entries": {"k": {"ema": "x"}}}), encoding="utf-8")
    try:
        ExpectationTracker(p)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed record must fail loud")
