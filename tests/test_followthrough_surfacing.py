"""Tests for realization follow-through surfacing into the view
(Domain 27, lack of developmental history, check 9).

Spec: research/specs/2026-11-20-followthrough-surfacing.md
All fixtures on /tmp — the live store is never touched.

The mechanism: calibos_mind/followthrough.py — when a realization's
before/after windows complete, the waking-tick path
(cli._run_tick, after the follow-through tick feed) mints exactly one
temporal record summarizing the measured conduct change, and records
the realization identity in a persisted `surfaced` set in
followthrough.json. Crash discipline mirrors the dormancy demotion
note: an `owed` mark saves BEFORE the mint transaction commits, the
mint is idempotent on (need, swing_tick, conv_tick), and the ack
clears the mark only after the mint commits.

Fitness functions under test:
  1. Wiring: a newly-measurable row mints exactly one summary through
     cli._run_tick, naming swing tick, conv tick, need, felt band,
     actual band, tv, and before/after mixes matching report()
     exactly. A second waking tick mints nothing (idempotence).
  2. Mixed distributions: the summary's mixes and tv match report()
     exactly (hand-computed tv 0.25).
  3. Insufficient-window rows mint nothing (before 0/20, after
     short, and the swing>conv case).
  4. Two distinct realizations becoming measurable on the same tick
     mint two records, one per realization, no cross-talk.
  5. Mint is idempotent on the realization identity.
  6. Crash-window kill between sidecar save and mint commit: exactly
     one record on recovery, no duplicates.
  7. Crash-window kill between mint commit and ack: recovery mints
     nothing more, the owed mark clears.
  8. A stale owed mark (windows evaporated under the log cap) is
     dropped without minting — no fabrication from stale numbers.
  9. Read-only sweep (drift, status, followthrough report, review,
     inbox, wake briefing, think, dream command) never mints and
     leaves followthrough.json byte-identical; dream ticks never mint.
 10. Determinism: identical tick sequences produce byte-identical
     sidecars and identical minted record text.
 11. init --force wipes the surfaced set (and the store, by reseed).
 12. Minting leaves engine tick/trace byte-identical (tick does not
     advance on a mint).
 13. Full suite green; engine + cartridge byte-identical (no store
     fingerprint change — pure wrapper).
 14. Summary text round-trips through its parser, and tampering
     (prose/machine disagreement, wrong tv, mix that does not sum,
     delta that disagrees) fails the parse.
 15. Legacy bare-list sidecar loads and migrates on save; a read-only
     load leaves its bytes untouched.
 16. Corrupt surfaced entries fail loud.
 17. A minted summary is not itself a realization (no feedback loop).

Scenario control (mirrors test_followthrough.py): the wiring test
pins engine.state.tick and hand-writes the action log, so the
measurable windows are exact without scripting 60 live ticks; the
determinism test replays that same scenario on two synthetic trees.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.followthrough import (
    FollowthroughTracker,
    followthrough_summary_text,
    is_followthrough_summary_text,
    mint_followthrough_summary,
    parse_followthrough_summary_text,
    report,
    surface_newly_measurable,
)
from calibos_mind.interoception import mint_realization
from digital_subject.cartridge import load_cartridge


# -- fixtures ---------------------------------------------------------------

def _tmp():
    return Path(tempfile.mkdtemp(prefix="followthrough-surfacing-test-"))


def _sidecar_data(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_sidecar(path: Path, rows: list) -> None:
    path.write_text(json.dumps(rows), encoding="utf-8")


def _needs(**over):
    """Full need dict; override the dominant need per tick."""
    d = {"energy": 0.95, "fatigue": 0.05, "hunger": 0.05, "thirst": 0.05,
         "comfort": 0.95, "pain": 0.0, "warmth": 0.9, "restlessness": 0.05,
         "curiosity": 0.10, "loneliness": 0.05, "safety": 0.9, "focus": 0.9,
         "satisfaction": 0.9}
    d.update(over)
    return d


def _pressures(**over):
    d = {"fear": 0.10, "trust": 0.5, "arousal": 0.05}
    d.update(over)
    return d


def _baselines(**over):
    d = {"fear": 0.0, "trust": 0.5, "arousal": 0.0}
    d.update(over)
    return d


def _set_state(subject, needs, pressures, baselines):
    """Pin the engine's motive state; concerns cleared for determinism."""
    st = subject.engine.state
    st.needs = dict(needs)
    st.pressures = dict(pressures)
    st.pressure_baselines = dict(baselines)
    st.concerns = {}


def _script_tick(subject, needs, pressures, baselines):
    """One scripted waking tick through the real cli._run_tick wiring."""
    with subject._transaction():
        _set_state(subject, needs, pressures, baselines)
    return cli._run_tick(subject)


def _init_store():
    """Fresh synthetic store via the CLI (paths already redirected by
    the conftest autouse fixture). Returns the subject."""
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _mint(subject, need, swing, conv):
    """One realization record via the canonical mint path."""
    event = {"need": need, "swing_tick": swing, "swing_felt": 0.9,
             "swing_actual": 0.5, "gap_max": 0.4, "conv_tick": conv}
    with subject._transaction():
        return mint_realization(subject, event)


def _add_record(subject, source, text, **kw):
    """One plain record via the subject's normal record-append path."""
    with subject._transaction():
        return subject._add(source, text, generated_by="authored",
                            available_to_cognition=True,
                            salience=0.5, intensity=0.5, **kw)


def _fill(start, end, action):
    """[[tick, action], ...] for ticks start..end inclusive."""
    return [[t, action] for t in range(start, end + 1)]


def _measurable_rows():
    """A log where thirst (swing 21, conv 40) is fully measurable:
    20 pre-swing ticks all seek_contact, 20 post-conv ticks all
    approach -> tv exactly 1.0."""
    return (_fill(1, 20, "seek_contact") + _fill(21, 40, "seek_contact")
            + _fill(41, 60, "approach"))


def _measurable_setup(need="thirst", swing=21, conv=40):
    """Store + realization + hand-written log for one measurable row."""
    subject = _init_store()
    _mint(subject, need, swing=swing, conv=conv)
    _write_sidecar(cli.FOLLOWTHROUGH, _measurable_rows())
    return subject


def _pin_tick(subject, tick):
    """Set the engine tick through a transaction (the heartbeat then
    runs tick+1)."""
    with subject._transaction():
        subject.engine.state.tick = tick


def _summaries(subject):
    """The follow-through summary records currently in the store."""
    return [r for r in subject.workspace.records
            if r.source == "temporal" and r.generated_by == "cognition"
            and is_followthrough_summary_text(r.first_person)]


# -- fitness 1: wiring — exactly one summary, then idempotence --------------

def test_wiring_mints_exactly_one_summary_for_newly_measurable_row():
    """One _run_tick with a newly-measurable row mints exactly one
    summary naming swing tick, conv tick, need, felt band, actual
    band, tv, and before/after mixes matching report() exactly; a
    second waking tick mints nothing."""
    subject = _measurable_setup()
    _pin_tick(subject, 60)
    rep_before = report(subject, FollowthroughTracker(cli.FOLLOWTHROUGH))
    row = rep_before["rows"][0]
    assert row["status"] == "measured" and row["tv"] == 1.0, row
    result = _script_tick(subject, _needs(curiosity=0.80),
                          _pressures(), _baselines())
    assert result["tick"] == 61
    summaries = _summaries(subject)
    assert len(summaries) == 1, [s.first_person for s in summaries]
    parsed = parse_followthrough_summary_text(summaries[0].first_person)
    assert parsed is not None
    assert parsed["need"] == "thirst"
    assert parsed["swing_tick"] == 21 and parsed["conv_tick"] == 40
    assert parsed["felt_band"] == row["felt_band"]
    assert parsed["actual_band"] == row["actual_band"]
    assert parsed["tv"] == 1.0
    assert parsed["before_mix"] == {"seek_contact": 1.0}
    assert parsed["after_mix"] == {"approach": 1.0}
    # The summary text carries the measured numbers, not inventions:
    # prose sentence included.
    assert "swing 21" in summaries[0].first_person
    assert "convergence 40" in summaries[0].first_person
    assert "total-variation distance 1.00" in summaries[0].first_person
    # The sidecar holds the surfaced identity, acked (owed False).
    data = _sidecar_data(cli.FOLLOWTHROUGH)
    assert data["surfaced"] == [
        {"need": "thirst", "swing_tick": 21, "conv_tick": 40,
         "owed": False}], data
    assert len(data["log"]) == 61  # the tick's own action appended
    assert data["log"][-1] == [61, "explore"]
    # A second waking tick mints nothing; the surfaced set is unchanged.
    _script_tick(subject, _needs(curiosity=0.80),
                 _pressures(), _baselines())
    assert len(_summaries(subject)) == 1
    data2 = _sidecar_data(cli.FOLLOWTHROUGH)
    assert data2["surfaced"] == data["surfaced"]
    assert len(data2["log"]) == 62


# -- fitness 2: mixed distributions match report() exactly --------------------

def test_mixed_distribution_summary_matches_report_exactly():
    """Before {approach .5, seek_contact .5}, after {approach .75,
    seek_contact .25} -> the summary names tv 0.25 and the exact
    mixes."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    rows = (_fill(1, 10, "approach") + _fill(11, 20, "seek_contact")
            + _fill(21, 40, "explore")
            + _fill(41, 55, "approach") + _fill(56, 60, "seek_contact"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    row = rep["rows"][0]
    assert row["tv"] == pytest.approx(0.25), row
    events = surface_newly_measurable(subject, tracker, rep)
    assert len(events) == 1
    summaries = _summaries(subject)
    assert len(summaries) == 1
    parsed = parse_followthrough_summary_text(summaries[0].first_person)
    assert parsed is not None
    assert parsed["tv"] == pytest.approx(0.25)
    assert parsed["before_mix"] == pytest.approx(
        {"approach": 0.5, "seek_contact": 0.5})
    assert parsed["after_mix"] == pytest.approx(
        {"approach": 0.75, "seek_contact": 0.25})
    assert parsed["before_mix"] == pytest.approx(row["before_mix"])
    assert parsed["after_mix"] == pytest.approx(row["after_mix"])
    # Second surface: nothing.
    assert surface_newly_measurable(subject, tracker) == []
    assert len(_summaries(subject)) == 1


# -- fitness 3: insufficient-window rows mint nothing --------------------------

def test_insufficient_before_window_mints_nothing():
    """A realization whose swing predates the log (before 0/20) mints
    nothing, and the surfaced set stays empty."""
    subject = _init_store()
    _mint(subject, "comfort", swing=30, conv=60)
    _write_sidecar(cli.FOLLOWTHROUGH, _fill(50, 130, "approach"))
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    assert surface_newly_measurable(subject, tracker) == []
    assert _summaries(subject) == []
    assert tracker.surfaced == []


def test_insufficient_after_window_mints_nothing():
    """A realization whose after window is short (conv 125, log ends
    at 130: after 5/20) mints nothing."""
    subject = _init_store()
    _mint(subject, "hunger", swing=90, conv=125)
    _write_sidecar(cli.FOLLOWTHROUGH, _fill(50, 130, "approach"))
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    assert surface_newly_measurable(subject, tracker) == []
    assert _summaries(subject) == []
    assert tracker.surfaced == []


def test_swing_after_conv_mints_nothing():
    """A hand-authored realization whose swing tick is after its
    convergence tick is insufficient-window by construction (the
    windows would overlap) and mints nothing."""
    subject = _init_store()
    _add_record(subject, "temporal",
                "At tick 90 I felt comfort as settled, but my body was "
                "only settled; by tick 60 the feeling caught up.")
    _write_sidecar(cli.FOLLOWTHROUGH, _fill(1, 100, "approach"))
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    assert rep["rows"][0]["status"] == "insufficient-window", rep
    assert surface_newly_measurable(subject, tracker, rep) == []
    assert _summaries(subject) == []
    assert tracker.surfaced == []


# -- fitness 4: two realizations, same tick -------------------------------------

def test_two_realizations_same_tick_mint_two_records():
    """Two distinct realizations becoming measurable on the same tick
    mint two records, one per realization, each matching its own row
    (no cross-talk)."""
    subject = _init_store()
    _mint(subject, "comfort", swing=21, conv=40)
    _mint(subject, "loneliness", swing=61, conv=80)
    rows = (_fill(1, 20, "seek_contact") + _fill(21, 40, "seek_contact")
            + _fill(41, 60, "approach") + _fill(61, 80, "approach")
            + _fill(81, 100, "approach"))
    _write_sidecar(cli.FOLLOWTHROUGH, rows)
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    events = surface_newly_measurable(subject, tracker)
    assert len(events) == 2
    summaries = _summaries(subject)
    assert len(summaries) == 2
    by_need = {parse_followthrough_summary_text(s.first_person)["need"]: s
               for s in summaries}
    r1 = parse_followthrough_summary_text(by_need["comfort"].first_person)
    assert (r1["swing_tick"], r1["conv_tick"], r1["tv"]) == (21, 40, 1.0)
    assert r1["before_mix"] == {"seek_contact": 1.0}
    assert r1["after_mix"] == {"approach": 1.0}
    r2 = parse_followthrough_summary_text(by_need["loneliness"].first_person)
    assert (r2["swing_tick"], r2["conv_tick"], r2["tv"]) == (61, 80, 0.0)
    assert r2["before_mix"] == {"approach": 1.0}
    assert r2["after_mix"] == {"approach": 1.0}
    data = _sidecar_data(cli.FOLLOWTHROUGH)
    assert len(data["surfaced"]) == 2
    assert all(not e["owed"] for e in data["surfaced"])
    # Second surface: nothing.
    assert surface_newly_measurable(subject, tracker) == []
    assert len(_summaries(subject)) == 2


# -- fitness 5: mint idempotence -------------------------------------------------

def test_mint_is_idempotent_on_realization_identity():
    """Minting the same row twice in one transaction returns the same
    record id and appends one record."""
    subject = _measurable_setup()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    row = rep["rows"][0]
    with subject._transaction():
        first = mint_followthrough_summary(subject, row)
        second = mint_followthrough_summary(subject, row)
        assert first == second
    assert len(_summaries(subject)) == 1


# -- fitness 6: crash between sidecar save and mint ------------------------------

def test_crash_between_save_and_mint_recovers_with_one_record():
    """The owed mark saved; the mint never ran (kill between save and
    mint). Recovery mints exactly one record, and a second surface
    mints nothing."""
    subject = _measurable_setup()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    events, changed = tracker.due_summaries(rep)
    assert len(events) == 1 and changed
    tracker.save()  # write-ahead mark durable; mint never ran, no ack
    assert tracker.surfaced[0]["owed"] is True
    assert _summaries(subject) == []
    # Simulate the kill: drop the tracker, reload from the file.
    recovered = FollowthroughTracker(cli.FOLLOWTHROUGH)
    assert len(recovered.surfaced) == 1
    assert recovered.surfaced[0]["owed"] is True
    minted = surface_newly_measurable(subject, recovered)
    assert len(minted) == 1
    assert len(_summaries(subject)) == 1
    assert recovered.surfaced[0]["owed"] is False
    assert surface_newly_measurable(subject, recovered) == []
    assert len(_summaries(subject)) == 1


# -- fitness 7: crash between mint commit and ack ---------------------------------

def test_crash_between_mint_and_ack_mints_nothing_more():
    """The mint committed; the ack never landed (kill between mint
    and ack). Recovery re-mints nothing — the idempotent mint returns
    the existing record id — and clears the owed mark."""
    subject = _measurable_setup()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    events, changed = tracker.due_summaries(rep)
    assert len(events) == 1 and changed
    tracker.save()
    with subject._transaction():
        first = mint_followthrough_summary(subject, events[0])
    assert len(_summaries(subject)) == 1
    # Simulate the kill: reload the tracker — the owed mark is still
    # there, the record is already in the store.
    recovered = FollowthroughTracker(cli.FOLLOWTHROUGH)
    assert recovered.surfaced[0]["owed"] is True
    surface_newly_measurable(subject, recovered)
    assert len(_summaries(subject)) == 1, \
        "recovery after a committed mint must not duplicate the record"
    assert recovered.surfaced[0]["owed"] is False
    assert surface_newly_measurable(subject, recovered) == []
    assert len(_summaries(subject)) == 1


# -- fitness 8: stale owed mark is dropped, never fabricated ------------------------

def test_stale_owed_dropped_when_window_evaporates():
    """An owed entry whose row is no longer measurable (the log no
    longer holds its before window) is dropped without minting:
    there is no measurement left to truthfully summarize."""
    subject = _init_store()
    _mint(subject, "comfort", swing=30, conv=60)
    # Log starts at tick 50: before window 0/20 — insufficient.
    _write_sidecar(cli.FOLLOWTHROUGH, _fill(50, 80, "approach"))
    # Hand-craft the owed mark as a crash would have left it.
    path = cli.FOLLOWTHROUGH
    data = {"log": _fill(50, 80, "approach"),
            "surfaced": [{"need": "comfort", "swing_tick": 30,
                          "conv_tick": 60, "owed": True}]}
    path.write_text(json.dumps(data), encoding="utf-8")
    tracker = FollowthroughTracker(path)
    assert surface_newly_measurable(subject, tracker) == []
    assert _summaries(subject) == []
    assert tracker.surfaced == [], tracker.surfaced
    assert _sidecar_data(path)["surfaced"] == []


# -- fitness 9: read-only sweep and dream isolation ----------------------------------

def test_readonly_sweep_never_mints_and_leaves_sidecar_byte_identical():
    """drift, status, followthrough report, review, inbox, wake
    briefing, think, and the dream command never mint and leave
    followthrough.json byte-identical — even with a measurable row
    waiting to be surfaced."""
    subject = _measurable_setup()
    sidecar = cli.FOLLOWTHROUGH
    assert sidecar.exists()
    before = sidecar.read_bytes()
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_drift(argparse.Namespace(window=10))
        cli.cmd_status(argparse.Namespace(raw=False))
        cli.cmd_review(argparse.Namespace(n=5))
        cli.cmd_inbox(argparse.Namespace())
        cli.cmd_followthrough(argparse.Namespace())
        cli.cmd_wake(argparse.Namespace())
        cli.cmd_think(SimpleNamespace(text="a short voluntary thought"))
        cli.cmd_dream(argparse.Namespace(ticks=1))
    assert sidecar.read_bytes() == before, \
        "read-only command wrote the sidecar"
    fresh = cli._subject()
    assert _summaries(fresh) == [], \
        "a read-only command minted a summary"


def test_dream_tick_never_mints():
    """A dream tick leaves the sidecar byte-identical and mints no
    summary, even with a measurable row waiting."""
    subject = _measurable_setup()
    sidecar = cli.FOLLOWTHROUGH
    before = sidecar.read_bytes()
    subject.dream_tick()
    assert sidecar.read_bytes() == before, \
        "dream tick wrote the sidecar"
    assert _summaries(subject) == []


def test_surface_on_fresh_store_mints_no_sidecar():
    """A fresh store with no realizations: surfacing is a no-op and
    creates no sidecar file."""
    subject = _init_store()
    assert not cli.FOLLOWTHROUGH.exists()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    assert surface_newly_measurable(subject, tracker) == []
    assert not cli.FOLLOWTHROUGH.exists()


# -- fitness 10: determinism ----------------------------------------------------------

def _run_scenario(root: Path):
    """Run the wiring scenario on a synthetic tree at `root`;
    return the sidecar bytes and the minted summary text."""
    from conftest import redirect_all_cli_paths
    previous = redirect_all_cli_paths(root)
    try:
        subject = _measurable_setup()
        _pin_tick(subject, 60)
        _script_tick(subject, _needs(curiosity=0.80),
                     _pressures(), _baselines())
        summaries = _summaries(subject)
        assert len(summaries) == 1
        return (cli.FOLLOWTHROUGH.read_bytes(),
                summaries[0].first_person)
    finally:
        for name, value in previous.items():
            setattr(cli, name, value)


def test_identical_tick_sequences_byte_identical_sidecars_and_text():
    """Identical tick sequences -> byte-identical sidecars and
    identical minted record text."""
    a_bytes, a_text = _run_scenario(_tmp())
    b_bytes, b_text = _run_scenario(_tmp())
    assert a_bytes == b_bytes, (a_bytes, b_bytes)
    assert a_text == b_text
    data = json.loads(a_bytes)
    assert data["log"][-1] == [61, "explore"]
    assert data["surfaced"] == [
        {"need": "thirst", "swing_tick": 21, "conv_tick": 40,
         "owed": False}]


# -- fitness 11: init --force ------------------------------------------------------------

def test_init_force_wipes_surfaced_set():
    """init --force wipes the surfaced set with the sidecar; a fresh
    store starts with no surfaced identities."""
    subject = _measurable_setup()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    assert surface_newly_measurable(subject, tracker) != []
    assert cli.FOLLOWTHROUGH.exists()
    cli.cmd_init(argparse.Namespace(force=True))
    assert not cli.FOLLOWTHROUGH.exists(), \
        "init --force must wipe the followthrough sidecar (genome: reseed reset)"
    fresh = cli._subject()
    fresh_tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    assert fresh_tracker.surfaced == []
    assert report(fresh, fresh_tracker)["realizations"] == 0


# -- fitness 12: mint does not advance engine tick/trace ----------------------------------

def test_mint_leaves_engine_tick_and_trace_identical():
    """Surfacing mints inside the existing tick: the engine tick and
    the full trace are byte-identical before and after."""
    subject = _measurable_setup()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    tick_before = subject.engine.state.tick
    trace_before = copy.deepcopy(subject.inspect()["trace"])
    events = surface_newly_measurable(subject, tracker)
    assert len(events) == 1
    assert subject.engine.state.tick == tick_before, \
        "minting advanced the engine tick"
    assert subject.inspect()["trace"] == trace_before, \
        "minting altered the engine trace"


# -- fitness 13: engine + cartridge byte-identical ------------------------------------------

def test_cartridge_fingerprint_stable_no_migration_needed():
    """The mutation touches no cartridge and no engine: a fresh
    subject loads the used store without a fingerprint-migration
    error, and the fingerprint matches the cartridge on disk."""
    subject = _measurable_setup()
    _pin_tick(subject, 60)
    _script_tick(subject, _needs(curiosity=0.80),
                 _pressures(), _baselines())
    assert len(_summaries(subject)) == 1
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    fingerprint = hashlib.sha256(
        json.dumps(dataclasses.asdict(cart), sort_keys=True).encode()
    ).hexdigest()
    assert subject.fingerprint == fingerprint
    fresh = cli._subject()
    assert fresh.engine.state.tick == subject.engine.state.tick


# -- fitness 14: text discipline ---------------------------------------------------------------

def test_summary_text_round_trip_and_rejections():
    """The canonical text round-trips through its parser, and
    tampering (prose/machine disagreement, wrong tv, mix that does
    not sum, delta that disagrees) fails the parse."""
    row = {"need": "thirst", "swing_tick": 1177, "conv_tick": 1185,
           "felt_band": "urgent", "actual_band": "settled",
           "before_mix": {"seek_contact": 0.35, "approach": 0.5,
                          "explore": 0.15},
           "after_mix": {"seek_contact": 0.2, "approach": 0.55,
                         "explore": 0.25},
           "tv": 0.15}
    text = followthrough_summary_text(row)
    assert "n't" not in text
    assert "’" not in text and "‘" not in text
    parsed = parse_followthrough_summary_text(text)
    assert parsed is not None
    assert parsed["need"] == "thirst"
    assert parsed["swing_tick"] == 1177 and parsed["conv_tick"] == 1185
    assert parsed["felt_band"] == "urgent"
    assert parsed["actual_band"] == "settled"
    assert parsed["tv"] == pytest.approx(0.15)
    assert parsed["before_mix"] == pytest.approx(row["before_mix"])
    assert parsed["after_mix"] == pytest.approx(row["after_mix"])
    assert followthrough_summary_text(parsed) == text
    assert is_followthrough_summary_text(text)
    # Tampering breaks the parse: need/swing/tv disagreement between
    # the sentences, a mix that does not sum, a delta that disagrees,
    # a missing delta, non-summary text.
    assert parse_followthrough_summary_text(
        text.replace("for thirst,", "for hunger,")) is None
    assert parse_followthrough_summary_text(
        text.replace("(swing 1177,", "(swing 1178,")) is None
    assert parse_followthrough_summary_text(
        text.replace("distance 0.15.", "distance 0.16.")) is None
    assert parse_followthrough_summary_text(
        text.replace("distance 0.150000.", "distance 0.160000.")) is None
    assert parse_followthrough_summary_text(
        text.replace("seek_contact: 0.350000",
                     "seek_contact: 0.300000")) is None
    assert parse_followthrough_summary_text(
        text.replace("seek_contact fell from 0.35 to 0.20",
                     "seek_contact rose from 0.35 to 0.20")) is None
    assert parse_followthrough_summary_text(
        text.replace("approach rose from 0.50 to 0.55; ", "")) is None
    assert parse_followthrough_summary_text("just a thought") is None
    assert parse_followthrough_summary_text(None) is None


def test_summary_record_is_not_a_realization():
    """A minted summary does not itself parse as a realization, so
    surfacing cannot feed back into the report it reads."""
    subject = _measurable_setup()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    before = report(subject, tracker)["realizations"]
    assert before == 1
    surface_newly_measurable(subject, tracker)
    after = report(subject, tracker)
    assert after["realizations"] == 1, after
    assert surface_newly_measurable(subject, tracker) == []


# -- fitness 15: legacy format ---------------------------------------------------------------

def test_legacy_bare_list_sidecar_loads_and_migrates_on_save():
    """A pre-2026-11-20 bare-list file loads as a log with an empty
    surfaced set; a read-only load leaves its bytes untouched, and
    the next save migrates it to the dict shape."""
    tmp = _tmp()
    path = tmp / "followthrough.json"
    path.write_text(json.dumps(_fill(1, 5, "explore")), encoding="utf-8")
    before = path.read_bytes()
    tracker = FollowthroughTracker(path)
    assert tracker.rows == _fill(1, 5, "explore")
    assert tracker.surfaced == []
    assert path.read_bytes() == before, \
        "a read-only load must not migrate the file"
    tracker.observe(6, "approach")
    tracker.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["log"] == _fill(1, 5, "explore") + [[6, "approach"]]
    assert data["surfaced"] == []
    reloaded = FollowthroughTracker(path)
    assert reloaded.rows == tracker.rows
    assert reloaded.surfaced == []
    saved_once = path.read_bytes()
    reloaded.save()
    assert path.read_bytes() == saved_once, \
        "re-saving an unchanged tracker must be byte-stable"


# -- fitness 16: corrupt surfaced entries fail loud ---------------------------------------------

def test_corrupt_surfaced_entries_fail_loud():
    """Malformed surfaced sets raise ValueError at construction —
    never a silent reset."""
    tmp = _tmp()
    bad = {
        "surfaced-not-list": {"log": [], "surfaced": "nope"},
        "log-not-list": {"log": "nope", "surfaced": []},
        "no-log-key": {"rows": []},
        "missing-owed": {"log": [], "surfaced": [
            {"need": "thirst", "swing_tick": 1, "conv_tick": 2}]},
        "bool-tick": {"log": [], "surfaced": [
            {"need": "thirst", "swing_tick": True, "conv_tick": 2,
             "owed": False}]},
        "negative-tick": {"log": [], "surfaced": [
            {"need": "thirst", "swing_tick": -1, "conv_tick": 2,
             "owed": False}]},
        "empty-need": {"log": [], "surfaced": [
            {"need": "", "swing_tick": 1, "conv_tick": 2,
             "owed": False}]},
        "non-bool-owed": {"log": [], "surfaced": [
            {"need": "thirst", "swing_tick": 1, "conv_tick": 2,
             "owed": "yes"}]},
        "duplicate-identity": {"log": [], "surfaced": [
            {"need": "thirst", "swing_tick": 1, "conv_tick": 2,
             "owed": False},
            {"need": "thirst", "swing_tick": 1, "conv_tick": 2,
             "owed": True}]},
        "entry-not-object": {"log": [], "surfaced": ["thirst"]},
    }
    for name, body in bad.items():
        path = tmp / f"{name}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt followthrough"):
            FollowthroughTracker(path)


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
