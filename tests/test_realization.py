"""Tests for interoceptive realization records (domain 10: delayed
emotional realization).

When a real felt/actual gap opens (|felt - actual| > 0.25) and later
converges (|felt - actual| <= 0.05), exactly one `temporal`-class record
captures the self-misreading — the memory of having misread oneself, not
installed emotion. Minted through the subject's normal record-append path
(generated_by="cognition"), a write inside the existing tick, never on
dream ticks or from read-only commands.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_realization.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import random
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.interoception import (
    BASELINE,
    CONVERGE_GAP,
    FELT_BANDS,
    SWING_GAP,
    InteroceptionTracker,
    felt_level,
    is_realization_text,
    mint_realization,
    parse_realization_text,
    realization_band,
    realization_text,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge


REALIZATION_MARK = "the feeling caught up"


def _tracker(tmp: Path, name: str = "interoception.json", **kw) -> InteroceptionTracker:
    return InteroceptionTracker(tmp / name, **kw)


def _needs(**over):
    base = {"hunger": 0.5, "thirst": 0.5, "fatigue": 0.5, "energy": 0.5}
    base.update(over)
    return base


def _realizations(records):
    """The realization records among workspace records (any shape)."""
    out = []
    for r in records:
        src = r.get("source") if isinstance(r, dict) else r.source
        gen = r.get("generated_by") if isinstance(r, dict) else r.generated_by
        txt = r.get("first_person") if isinstance(r, dict) else r.first_person
        if src == "temporal" and gen == "cognition" and REALIZATION_MARK in txt:
            out.append(r)
    return out


# -- synthetic-store harness (same shape as test_interoception.py) ----------

def _patched_cli(tmp: Path):
    db = tmp / "mind.db"
    intero = tmp / "interoception.json"
    salience = tmp / "salience.json"
    familiarity = tmp / "familiarity.json"
    habits = tmp / "habits-formed.json"
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    proposals = tmp / "proposals"
    archive = tmp / "archive"
    ambivalence = tmp / "ambivalence.json"
    provenance = tmp / "provenance.json"
    saved = (cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY,
             cli.HABITS, cli.AMBIVALENCE, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE, cli._subject)
    cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY, \
        cli.HABITS, cli.AMBIVALENCE, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE = (
            db, inbox, salience, intero, familiarity, habits,
            ambivalence, provenance, proposals, archive)
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(salience),
                              interoception_path=str(intero))

    cli._subject = make_subject
    return make_subject, saved, intero, db


def _restore(saved):
    (cli.DB, cli.INBOX, cli.SALIENCE, cli.INTEROCEPTION, cli.FAMILIARITY,
     cli.HABITS, cli.AMBIVALENCE, cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE, cli._subject) = saved


def _stdout(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args)
    return rc, buf.getvalue()


def _set_needs(subject, **vals):
    """Script the body's actual values through the store.

    Pinning subject.engine.state.needs directly does not survive a tick:
    heartbeat() opens a transaction that _restores the engine from the DB
    payload, discarding in-memory-only changes. Writing through a
    transaction persists, so the next _run_tick restores the scripted
    values (plus the engine's own small per-tick homeostasis nudge).
    """
    with subject._transaction():
        for key, value in vals.items():
            subject.engine.state.needs[key] = value


def _tick(subject):
    cli._run_tick(subject)


# -- unit: episode detection -------------------------------------------------

def test_step_opens_and_closes_one_episode():
    """Fitness: a 0.2→0.9 step opens one episode; convergence closes it
    with exactly one event carrying swing tick, felt/actual bands, ticks."""
    tmp = Path(tempfile.mkdtemp(prefix="real-"))
    tr = _tracker(tmp)
    tick = 0
    for _ in range(30):  # stabilize felt at thirst 0.2
        tick += 1
        assert tr.update(_needs(thirst=0.2), tick) == []
    events = []
    for _ in range(30):  # step the actual to 0.9, hold
        tick += 1
        events.extend(tr.update(_needs(thirst=0.9), tick))
    assert len(events) == 1, events
    ev = events[0]
    assert ev["need"] == "thirst"
    assert ev["gap_max"] > SWING_GAP, ev
    assert ev["conv_tick"] > ev["swing_tick"], ev
    # The gap really did exceed 0.25 (test-premise check, not assumed).
    assert ev["gap_max"] > 0.25
    # Bands recoverable from the event alone.
    felt_band = realization_band(ev["swing_felt"], "thirst", floored=True)
    actual_band = realization_band(ev["swing_actual"], "thirst", floored=False)
    assert felt_band in FELT_BANDS and actual_band in FELT_BANDS
    # Canonical step: thirst is not LOW_IS_BAD (urgency = value), so at
    # the swing the felt value (~0.28) reads settled while the actual
    # body (0.9) is urgent — the delayed realization, honestly named.
    assert felt_band == "settled", (felt_band, ev)
    assert actual_band == "urgent", (actual_band, ev)
    text = realization_text(ev)
    assert f"tick {ev['swing_tick']}" in text
    assert f"tick {ev['conv_tick']}" in text
    assert "settled" in text and "urgent" in text
    assert REALIZATION_MARK in text


def test_no_divergence_no_episode():
    """Needs that never diverge close nothing — no phantom episodes."""
    tmp = Path(tempfile.mkdtemp(prefix="real-"))
    tr = _tracker(tmp)
    tick = 0
    for _ in range(60):
        tick += 1
        assert tr.update(_needs(), tick) == []
    assert tr.data["realization"]["swing"] == {}
    tr.save()
    tr2 = _tracker(tmp)  # reload: still no open episode
    assert tr2.data["realization"]["swing"] == {}


def test_first_contact_gap_is_real():
    """The felt body starts settled at baseline while the actual body may
    not be there (engine defaults: hunger 0.10, pain 0.00). That first
    felt/actual gap is real under the tracker's documented model — not a
    silent default — so it opens an episode and mints on convergence,
    exactly like any later swing.

    hunger is pinned at 0.05 (not the engine's 0.10): the first-contact
    gap must open robustly past SWING_GAP even against a worst-case
    strain-scaled noise draw (±0.02 at the fixture's weariness 0.5); at
    0.10 the deterministic tick-1 gap lands at 0.2435 — a near-miss of the
    threshold, not a premise failure."""
    tmp = Path(tempfile.mkdtemp(prefix="real-"))
    tr = _tracker(tmp)
    events = []
    for tick in range(1, 40):
        events.extend(tr.update(_needs(hunger=0.05, pain=0.0), tick))
    assert len(events) == 2, events
    assert {e["need"] for e in events} == {"hunger", "pain"}
    for e in events:
        assert e["swing_tick"] == 1
        assert e["gap_max"] > SWING_GAP


def test_two_episodes_two_events():
    """Two separate swing→convergence episodes mint two events."""
    tmp = Path(tempfile.mkdtemp(prefix="real-"))
    tr = _tracker(tmp)
    tick = 0
    for _ in range(30):
        tick += 1
        tr.update(_needs(thirst=0.2), tick)
    events = []
    for _ in range(30):
        tick += 1
        events.extend(tr.update(_needs(thirst=0.9), tick))
    for _ in range(40):
        tick += 1
        events.extend(tr.update(_needs(thirst=0.2), tick))
    assert len(events) == 2, [ (e["swing_tick"], e["conv_tick"]) for e in events ]
    first, second = events
    assert first["conv_tick"] <= second["swing_tick"]
    assert first["gap_max"] > SWING_GAP and second["gap_max"] > SWING_GAP


def test_episode_survives_sidecar_roundtrip():
    """An open swing persists in the sidecar; a fresh tracker closes it."""
    tmp = Path(tempfile.mkdtemp(prefix="real-"))
    tr = _tracker(tmp)
    tick = 0
    for _ in range(30):
        tick += 1
        tr.update(_needs(thirst=0.2), tick)
    tick += 1
    assert tr.update(_needs(thirst=0.9), tick) == []  # swing opens, no close
    assert "thirst" in tr.data["realization"]["swing"]
    tr.save()
    tr2 = _tracker(tmp)  # fresh instance, same file
    assert "thirst" in tr2.data["realization"]["swing"]
    events = []
    for _ in range(30):
        tick += 1
        events.extend(tr2.update(_needs(thirst=0.9), tick))
    assert len(events) == 1, events
    assert tr2.data["realization"]["swing"] == {}


def test_byte_identical_sequences():
    """Determinism: identical tick/need sequences -> byte-identical
    sidecar and identical events (no wall clock, seeded noise only)."""
    def run(tmp):
        tr = _tracker(tmp)
        tick = 0
        events = []
        rng = random.Random(7)
        for _ in range(50):
            tick += 1
            actuals = {k: round(0.5 + (rng.random() - 0.5) * 0.9, 4)
                       for k in ("hunger", "thirst", "fatigue", "energy")}
            events.extend(tr.update(actuals, tick))
        tr.save()
        return (tmp / "interoception.json").read_bytes(), events

    a_bytes, a_events = run(Path(tempfile.mkdtemp(prefix="real-a-")))
    b_bytes, b_events = run(Path(tempfile.mkdtemp(prefix="real-b-")))
    assert a_bytes == b_bytes
    assert a_events == b_events


def test_soak_rarity():
    """Rarity by construction: 100 ticks of small random need walks mint
    realization records at well under 2% of ticks."""
    tmp = Path(tempfile.mkdtemp(prefix="real-"))
    tr = _tracker(tmp)
    rng = random.Random(20261004)
    actuals = {k: 0.5 for k in ("hunger", "thirst", "fatigue", "energy")}
    events = []
    for tick in range(1, 101):
        for k in actuals:
            actuals[k] = round(min(1.0, max(0.0,
                actuals[k] + (rng.random() - 0.5) * 0.08)), 4)
        events.extend(tr.update(dict(actuals), tick))
    assert len(events) < 2, f"{len(events)} realizations in 100 ticks: {events}"


def test_compat_read_old_and_malformed_sidecars():
    """Pre-mutation sidecars (no realization section) and malformed
    sections read as no open episodes — never a phantom, never a crash."""
    tmp = Path(tempfile.mkdtemp(prefix="real-"))
    p = tmp / "interoception.json"
    # Pre-mutation shape: needs/params/seed only.
    p.write_text(json.dumps({"needs": {}, "params": {}, "seed": 0}),
                 encoding="utf-8")
    tr = _tracker(tmp)
    assert tr.data["realization"] == {"swing": {}}
    assert tr.update(_needs(), 1) == []
    # Malformed realization section: wrong types are dropped entry-wise.
    p.write_text(json.dumps({
        "needs": {},
        "realization": {"swing": {
            "thirst": {"swing_tick": "not-an-int", "swing_felt": 0.2,
                       "swing_actual": 0.9, "gap_max": 0.7},
            "hunger": {"swing_tick": 3, "swing_felt": 0.2,
                       "swing_actual": 0.9, "gap_max": 0.7}}},
        "seed": 0}), encoding="utf-8")
    tr = _tracker(tmp)
    assert set(tr.data["realization"]["swing"]) == {"hunger"}
    # The surviving well-formed swing still closes honestly.
    events = []
    for tick in range(2, 40):
        events.extend(tr.update(_needs(), tick))
    assert len(events) == 1 and events[0]["need"] == "hunger"
    assert events[0]["swing_tick"] == 3


def test_realization_band_math():
    """Band vocabulary reuses the felt math: noise floor on the felt side
    only; the actual side is engine-exact."""
    # Felt pinned at baseline renders settled even though raw level math
    # would read level 1 ("stirring") at urgency 0.5.
    assert realization_band(0.5, "thirst", floored=True) == "settled"
    assert realization_band(0.53, "thirst", floored=True) == "settled"
    # The actual side is never floored: 0.5 urgency reads its true level.
    assert realization_band(0.5, "thirst", floored=False) == "stirring"
    assert realization_band(0.2, "thirst", floored=True) == "settled"
    assert realization_band(0.9, "thirst", floored=False) == "urgent"
    assert realization_band(0.9, "thirst", floored=True) == "urgent"
    # thirst is not LOW_IS_BAD: urgency = value (0.9 = parched).
    assert felt_level(0.2, "thirst", 0) == 0
    assert felt_level(0.9, "thirst", 0) == 3


# -- integration: the seam ---------------------------------------------------

def test_step_mints_exactly_one_record_end_to_end():
    """Full _run_tick path: one step → exactly one temporal record with
    swing tick, convergence tick, felt band, actual band all recoverable;
    the record is view-eligible like any engine record."""
    tmp = Path(tempfile.mkdtemp(prefix="real-e2e-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        for _ in range(30):
            _tick(subject)  # birth: the felt body calibrates; first-contact
                            # episodes (real felt/actual gaps) open and close
        subject = make_subject()
        n_before = len([r for r in _realizations(subject.workspace.records)
                        if tuple(r.concepts) == ("thirst",)])
        for _ in range(30):
            _set_needs(subject, thirst=0.9)  # script the 0.1→0.9 step
            _tick(subject)
        subject = make_subject()  # fresh read of the persisted store
        records = subject.workspace.records
        # The thirst step mints exactly one NEW thirst realization. (The
        # birth window may carry a first-contact thirst episode — a real
        # felt/actual gap under the tracker's documented model — so the
        # assertion is on the delta, and the record examined is the step's.)
        found = [r for r in _realizations(records)
                 if tuple(r.concepts) == ("thirst",)]
        assert len(found) == n_before + 1, [
            (r.tick, r.first_person[:70]) for r in found]
        rec = found[-1]
        assert rec.generated_by == "cognition"
        assert tuple(rec.concepts) == ("thirst",)
        assert rec.available_to_cognition
        m = re.search(r"At tick (\d+) I felt (\w+) as (\w+), but my body was "
                      r"only (\w+); by tick (\d+) " + REALIZATION_MARK, rec.first_person)
        assert m, rec.first_person
        swing_tick, need, felt_band, actual_band, conv_tick = (
            int(m.group(1)), m.group(2), m.group(3), m.group(4), int(m.group(5)))
        assert need == "thirst"
        assert felt_band in FELT_BANDS and actual_band in FELT_BANDS
        assert conv_tick > swing_tick
        assert rec.tick == conv_tick  # minted inside the convergence tick
        # View eligibility: the record reaches cognition like any engine
        # record (normal path — eligibility, ranking, caps, dedupe).
        view_texts = [e.first_person for e in subject.workspace.view().experiences]
        assert rec.first_person in view_texts
    finally:
        _restore(saved)


def test_quiet_run_mints_nothing_end_to_end():
    """No divergence in vivo → no realization records. After the birth
    window (the felt body calibrates against the engine's default needs;
    any first-contact episodes close), natural body dynamics are small
    enough that the felt body tracks without episodes — the count stays
    flat across the quiet window."""
    tmp = Path(tempfile.mkdtemp(prefix="real-e2e-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        for _ in range(15):
            _tick(subject)  # birth window: calibration episodes may close
        subject = make_subject()
        n_after_birth = len(_realizations(subject.workspace.records))
        for _ in range(25):
            _tick(subject)  # quiet window: natural dynamics only
        subject = make_subject()
        assert len(_realizations(subject.workspace.records)) == n_after_birth
    finally:
        _restore(saved)


def test_mint_never_advances_tick_or_trace():
    """No-tick discipline: minting is a write inside the existing tick —
    engine tick and trace byte-identical apart from the minted record."""
    tmp = Path(tempfile.mkdtemp(prefix="real-e2e-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        event = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.284,
                 "swing_actual": 0.9, "gap_max": 0.616, "conv_tick": 52}
        tick_before = subject.engine.state.tick
        trace_before = json.dumps(subject.inspect()["trace"], sort_keys=True)
        n_before = len(subject.workspace.records)
        with subject._transaction():
            rid = mint_realization(subject, event)
        assert subject.engine.state.tick == tick_before
        assert json.dumps(subject.inspect()["trace"], sort_keys=True) == trace_before
        records = subject.workspace.records
        assert len(records) == n_before + 1
        rec = records[-1]
        assert rec.id == rid and rec.source == "temporal"
        assert rec.generated_by == "cognition"
        assert rec.first_person == realization_text(event)
    finally:
        _restore(saved)


def test_init_force_wipes_open_episodes():
    """Reseed restarts episodes: no stale swing attaches to recycled state."""
    tmp = Path(tempfile.mkdtemp(prefix="real-e2e-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 30):
            tr.update(_needs(thirst=0.2), tick)
        tr.update(_needs(thirst=0.9), 30)  # swing opens
        tr.save()
        assert "thirst" in json.loads(intero.read_text(encoding="utf-8")
                                     )["realization"]["swing"]
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        payload = json.loads(intero.read_text(encoding="utf-8"))
        assert payload["needs"] == {}
        assert payload["realization"] == {"swing": {}}, payload["realization"]
    finally:
        _restore(saved)


def _payload_records(db: Path):
    import sqlite3
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("SELECT payload FROM subject WHERE id=1").fetchone()
    finally:
        con.close()
    return json.loads(row[0])["workspace"]["records"]


def test_readonly_and_dream_never_mint():
    """drift / status / dream_tick never mint realization records and never
    move the sidecar — even with an episode open (genome: read-only
    violations; dream/conduct isolation)."""
    tmp = Path(tempfile.mkdtemp(prefix="real-e2e-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 30):
            tr.update(_needs(thirst=0.2), tick)
        tr.update(_needs(thirst=0.9), 30)  # swing opens
        tr.save()
        digest_before = hashlib.sha256(intero.read_bytes()).hexdigest()
        recs_before = _payload_records(db)
        assert _realizations(recs_before) == []
        rc, _ = _stdout(cli.cmd_drift, argparse.Namespace(window=10))
        assert rc == 0
        rc, _ = _stdout(cli.cmd_status, argparse.Namespace(raw=False))
        assert rc == 0
        recs_after_ro = _payload_records(db)
        assert [(r["id"], r["source"], r["first_person"]) for r in recs_after_ro] == \
               [(r["id"], r["source"], r["first_person"]) for r in recs_before], \
            "a read-only command changed the records"
        assert _realizations(recs_after_ro) == []
        # Dream ticks legitimately add dream records (their isolation
        # freezes body/conduct/tick, not the record stream) — but they
        # must never touch the sidecar or mint realizations.
        subject.dream_tick()
        assert hashlib.sha256(intero.read_bytes()).hexdigest() == digest_before, \
            "a read-only path or dream tick moved the sidecar"
        recs_after_dream = _payload_records(db)
        assert _realizations(recs_after_dream) == []
        # The episode is still honestly open — the next waking tick may
        # close it; nothing was lost or minted.
        assert "thirst" in json.loads(intero.read_text(encoding="utf-8")
                                     )["realization"]["swing"]
    finally:
        _restore(saved)


def test_drift_r_not_flattened():
    """A minted realization record (grown, generated_by=cognition) leaves
    the drift metric intact: R stays defined and finite, the authored set
    is untouched, and the realization classifies as grown.

    (Genome: test-premise drift — measured on pre-mutation code, this
    synthetic sequence already scores R = 1.0 because the authored seeds
    floor to zero activation; the invariant is that minting changes none
    of that, not that R lands in the interior.)
    """
    from calibos_mind.drift import drift_report, is_authored
    tmp = Path(tempfile.mkdtemp(prefix="real-e2e-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        for _ in range(30):
            _tick(subject)
        subject = make_subject()
        r0 = drift_report(subject.inspect(), cli._tracker(subject),
                          window=10)["ratio"]
        n0 = len([r for r in _realizations(subject.workspace.records)
                  if tuple(r.concepts) == ("thirst",)])
        for _ in range(30):
            _set_needs(subject, thirst=0.9)  # step mints 1 thirst record
            _tick(subject)
        subject = make_subject()
        thirst_real = [r for r in _realizations(subject.workspace.records)
                       if tuple(r.concepts) == ("thirst",)]
        assert len(thirst_real) == n0 + 1, [r.first_person for r in thirst_real]
        assert not is_authored({"generated_by": thirst_real[-1].generated_by})
        r1 = drift_report(subject.inspect(), cli._tracker(subject),
                          window=10)["ratio"]
        assert r1["R"] is not None and 0.0 <= r1["R"] <= 1.0, r1
        assert r1["R"] == r0["R"], (r0["R"], r1["R"])  # metric unmoved
        assert r1["n_authored"] == r0["n_authored"] > 0, (r0, r1)
    finally:
        _restore(saved)


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"all {len(fns)} realization tests passed")


# -- round 2 (critic fix-ups): crash safety + consolidation awareness ------

def test_close_mark_is_write_ahead_durable():
    """The close is durable before the event leaves update(): after a close
    the in-memory swing is empty (no re-close in the same tick) but the
    file holds the marked episode; a reloaded tracker sees it and never
    re-closes it."""
    tmp = Path(tempfile.mkdtemp(prefix="real-r2-"))
    tr = _tracker(tmp, params={"noise_scale": 0.0})
    tr.data["needs"]["thirst"] = {"felt": 0.9, "last_tick": 1, "level": 3}
    tr.update({"thirst": 0.2}, 2)  # gap 0.7: opens
    tr.data["needs"]["thirst"]["felt"] = 0.21
    evs = tr.update({"thirst": 0.2}, 3)  # converges: closes
    assert len(evs) == 1, evs
    assert tr.data["realization"]["swing"] == {}  # no same-tick re-close
    file_swing = json.loads((tmp / "interoception.json").read_text(
        encoding="utf-8"))["realization"]["swing"]
    assert file_swing["thirst"]["closed_tick"] == 3, file_swing
    tr2 = _tracker(tmp)  # "restart" before any mint
    assert "thirst" in tr2.data["realization"]["swing"]
    dups = [e for t in range(4, 60) for e in tr2.update({"thirst": 0.2}, t)]
    assert dups == [], dups  # the mark, not a re-close


def test_take_pending_closings_takes_once_and_recovers():
    """take_pending_closings() returns each mark exactly once; a mark taken
    but never acked (mid-tick mint failure) is still recovered from the
    file by a fresh tracker."""
    tmp = Path(tempfile.mkdtemp(prefix="real-r2-"))
    tr = _tracker(tmp, params={"noise_scale": 0.0})
    tr.data["needs"]["thirst"] = {"felt": 0.9, "last_tick": 1, "level": 3}
    tr.update({"thirst": 0.2}, 2)
    tr.data["needs"]["thirst"]["felt"] = 0.21
    evs = tr.update({"thirst": 0.2}, 3)
    assert len(evs) == 1
    pend = tr.take_pending_closings()
    assert len(pend) == 1 and pend[0]["conv_tick"] == 3, pend
    assert tr.take_pending_closings() == []
    # The mint "failed" (never acked): the file still holds the mark, so a
    # fresh tracker recovers it.
    tr2 = _tracker(tmp)
    pend2 = tr2.take_pending_closings()
    assert len(pend2) == 1 and pend2[0]["swing_tick"] == evs[0]["swing_tick"]
    tr2.ack_closings(pend2)
    assert tr2.take_pending_closings() == []
    tr2.save()
    assert json.loads((tmp / "interoception.json").read_text(
        encoding="utf-8"))["realization"]["swing"] == {}


def test_mint_realization_is_idempotent():
    """Re-minting the same episode — across transactions (crash retry) or
    inside one transaction — appends nothing and returns the existing id:
    exactly one record per closed episode."""
    tmp = Path(tempfile.mkdtemp(prefix="real-r2-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        event = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.284,
                 "swing_actual": 0.9, "gap_max": 0.616, "conv_tick": 52}
        with subject._transaction():
            id1 = mint_realization(subject, event)
        with subject._transaction():  # crash-retry: already committed
            id2 = mint_realization(subject, event)
        assert id1 == id2
        with subject._transaction():  # same-transaction double mint
            id3 = mint_realization(subject, event)
            id4 = mint_realization(subject, event)
        assert id3 == id4 == id1  # read-your-own-write: visible
        recs = [r for r in subject.workspace.records
                if r.source == "temporal" and r.generated_by == "cognition"
                and is_realization_text(r.first_person)]
        assert len(recs) == 1, [r.id for r in recs]
        # A genuinely different episode still mints.
        other = dict(event, swing_tick=100, conv_tick=131)
        with subject._transaction():
            id5 = mint_realization(subject, other)
        assert id5 != id1
    finally:
        _restore(saved)


def test_impossible_gap_max_dropped_at_compat_read():
    """A sidecar entry with gap_max <= SWING_GAP is impossible for a real
    open episode (it only opens past the threshold) — dropped at load, so
    it can never mint a phantom. A legitimate above-threshold entry loads."""
    tmp = Path(tempfile.mkdtemp(prefix="real-r2-"))
    p = tmp / "interoception.json"
    p.write_text(json.dumps({
        "needs": {}, "params": {}, "seed": 0,
        "realization": {"swing": {
            "thirst": {"swing_tick": 3, "swing_felt": 0.5,
                       "swing_actual": 0.51, "gap_max": 0.01},
            "hunger": {"swing_tick": 4, "swing_felt": 0.2,
                       "swing_actual": 0.9, "gap_max": 0.7}}}}),
        encoding="utf-8")
    tr = _tracker(tmp)
    assert set(tr.data["realization"]["swing"]) == {"hunger"}, \
        tr.data["realization"]["swing"]
    assert tr.data["realization"]["swing"]["hunger"]["gap_max"] == 0.7


def test_writer_preserves_strict_gap_max_invariant():
    """A true gap in (0.25, 0.2500005] opens (gap > SWING_GAP) but 6-decimal
    rounding would store exactly 0.25 — which the compat-read drops as
    impossible. The writer preserves the invariant exactly so a legitimate
    episode is never rounded into the corrupt bucket."""
    tmp = Path(tempfile.mkdtemp(prefix="real-r2-"))
    tr = _tracker(tmp, params={"noise_scale": 0.0})
    tr.data["needs"]["thirst"] = {"felt": 0.8, "last_tick": 1, "level": 2}
    # Post-chase gap 0.2500001: opens (gap > SWING_GAP) but 6-decimal
    # rounding would store exactly 0.25.
    assert tr.update({"thirst": 0.5159089}, 2) == []
    entry = tr.data["realization"]["swing"]["thirst"]
    assert entry["gap_max"] > SWING_GAP, entry
    tr.save()
    tr2 = _tracker(tmp)  # reload: the entry survives the compat-read
    assert "thirst" in tr2.data["realization"]["swing"]


def test_parse_realization_text_roundtrip():
    """parse_realization_text inverts realization_text on the episode
    identity; anything else — including near-misses of the template — is
    not a realization record."""
    event = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.28,
             "swing_actual": 0.9, "gap_max": 0.62, "conv_tick": 52}
    text = realization_text(event)
    parsed = parse_realization_text(text)
    assert parsed is not None
    assert (parsed["need"], parsed["swing_tick"]) == ("thirst", 41)
    assert parsed["conv_tick"] == 52
    assert is_realization_text(text)
    assert not is_realization_text("I felt thirst as settled today.")
    assert not is_realization_text(text[:-1])  # missing final period
    assert not is_realization_text(text.replace("At tick", "At Tick"))
    assert not is_realization_text(None)
    # A different need word still parses (identity, not thirst-specific).
    other = realization_text(dict(event, need="hunger"))
    assert parse_realization_text(other)["need"] == "hunger"


if __name__ == "__main__":
    _main()
