"""Tests for the host-layer feeder: slow tick-count rhythm of food/drink events.

Spec: proposals/2026-10-08-host-feeder-food-drink.md (filed by the 02:06
wake after the seek_contact streak was adjudicated genuine). Hunger
(+0.0020/tick) and thirst (+0.0025/tick) rise forever; nothing in
calibos_mind/ emits the frozen engine's food/drink events, so conduct
degenerated to a seek_contact constant with forming habits hardening past
the 0.65 fast-fire threshold. The feeder emits the engine's OWN events
(it invents no satiation math) on a slow deterministic tick-count rhythm,
need-gated — waking ticks only.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_feeder.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched. Every harness
redirects the complete cli path set (DB, INBOX, DREAMS, SALIENCE,
INTEROCEPTION, FAMILIARITY, AMBIVALENCE, HABITS, PROVENANCE, ARCHIVE,
PROPOSALS): incomplete redirect sets let cmd_init delete live sidecars
(defect fixed 2026-10-08).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind import feeder
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge

BASE = Path(__file__).resolve().parents[1]
CARTRIDGE = load_cartridge(BASE / "calibos.toml")

FED_MARK = "I have been fed."
EASED_MARK = "My thirst has eased."


# -- synthetic-store harness (full cli path redirect set) -------------------

def _patched_cli(tmp: Path):
    paths = {}
    for name, fname in [("DB", "mind.db"), ("INBOX", "inbox"), ("DREAMS", "dreams"),
                        ("SALIENCE", "salience.json"),
                        ("INTEROCEPTION", "interoception.json"),
                        ("FAMILIARITY", "familiarity.json"),
                        ("AMBIVALENCE", "ambivalence.json"),
                        ("HABITS", "habits-formed.json"),
                        ("PROVENANCE", "provenance.json"),
                        ("ARCHIVE", "archive"), ("PROPOSALS", "proposals")]:
        p = tmp / fname
        if fname in ("inbox", "dreams", "archive", "proposals"):
            p.mkdir(exist_ok=True)
        paths[name] = p
    saved = (cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE, cli.INTEROCEPTION,
             cli.FAMILIARITY, cli.HABITS, cli.AMBIVALENCE, cli.PROVENANCE,
             cli.PROPOSALS, cli.ARCHIVE, cli._subject)
    (cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE, cli.INTEROCEPTION,
     cli.FAMILIARITY, cli.HABITS, cli.AMBIVALENCE, cli.PROVENANCE,
     cli.PROPOSALS, cli.ARCHIVE) = (
        paths["DB"], paths["INBOX"], paths["DREAMS"], paths["SALIENCE"],
        paths["INTEROCEPTION"], paths["FAMILIARITY"], paths["HABITS"],
        paths["AMBIVALENCE"], paths["PROVENANCE"], paths["PROPOSALS"],
        paths["ARCHIVE"])
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(paths["INBOX"])
        return CalibosSubject(str(paths["DB"]), cartridge, cognition=provider,
                              salience_path=str(paths["SALIENCE"]),
                              interoception_path=str(paths["INTEROCEPTION"]))

    cli._subject = make_subject
    return make_subject, saved, paths


def _restore(saved):
    (cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE, cli.INTEROCEPTION,
     cli.FAMILIARITY, cli.HABITS, cli.AMBIVALENCE, cli.PROVENANCE,
     cli.PROPOSALS, cli.ARCHIVE, cli._subject) = saved


def _quiet(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*args)


def _set_state(subject, **vals):
    """Script engine state through a transaction so it persists."""
    with subject._transaction():
        for key, value in vals.items():
            if key == "tick":
                subject.engine.state.tick = value
            else:
                subject.engine.state.needs[key] = value


def _feed_records(state):
    return [r for r in state["workspace"]["records"]
            if r["source"] == "perception" and
            (FED_MARK in r["first_person"] or EASED_MARK in r["first_person"])]


# -- pure schedule -----------------------------------------------------------

def test_due_kinds_rhythm_and_gating():
    # Food rhythm: every 48 ticks, gated on hunger >= 0.60.
    assert feeder.due_kinds(48, {"hunger": 0.60, "thirst": 0.0}) == ["food"]
    assert feeder.due_kinds(48, {"hunger": 0.59, "thirst": 0.0}) == []
    assert feeder.due_kinds(96, {"hunger": 1.0, "thirst": 0.0}) == ["food"]
    assert feeder.due_kinds(47, {"hunger": 1.0, "thirst": 1.0}) == []
    # Drink rhythm: offset 24, gated on thirst >= 0.60.
    assert feeder.due_kinds(24, {"hunger": 0.0, "thirst": 0.60}) == ["drink"]
    assert feeder.due_kinds(72, {"hunger": 0.0, "thirst": 0.59}) == []
    assert feeder.due_kinds(48, {"hunger": 0.0, "thirst": 1.0}) == [], \
        "drink must not fire on the food rhythm tick (offset)"
    # The two rhythms never coincide (48k = 24 + 48m has no solution).
    both = [t for t in range(1, 2000)
            if "food" in feeder.due_kinds(t, {"hunger": 1.0, "thirst": 1.0})
            and "drink" in feeder.due_kinds(t, {"hunger": 1.0, "thirst": 1.0})]
    assert both == []


def test_due_kinds_tick_zero_and_missing_keys():
    # Tick 0 is the origin, not a rhythm tick — never feed on unknown state.
    assert feeder.due_kinds(0, {"hunger": 1.0, "thirst": 1.0}) == []
    assert feeder.due_kinds(48, {}) == []
    assert feeder.due_kinds(48, {"hunger": 1.0}) == ["food"]
    assert feeder.due_kinds(24, {"thirst": 1.0}) == ["drink"]


def test_event_for_fields():
    ev = feeder.event_for("food")
    assert ev.kind == "food" and ev.source == "world"
    assert ev.intensity == 1.0 and ev.valence == 0.0
    # Fully expected (expected == actual == 0.0) -> surprise 0 -> the
    # engine scales EVENT_RULES by exactly 1.0. A scheduled meal is
    # routine, not a surprise.
    assert ev.expected_valence == 0.0 and ev.actual_valence == 0.0
    assert ev.tags == ("food",) and ev.description.strip()
    ev2 = feeder.event_for("drink")
    assert ev2.kind == "drink" and ev2.tags == ("drink",)


def test_event_for_unknown_kind_raises():
    try:
        feeder.event_for("feast")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown feeder kind must raise")


# -- the feeder emits engine events: exact EVENT_RULES deltas ----------------

def test_food_event_applies_exact_engine_delta():
    """food -> hunger -0.35 exactly; drink -> thirst -0.40 exactly, via the
    engine's own _apply_event (advance_time=False isolates the event from
    per-tick homeostasis). The feeder invents no satiation math."""
    tmp = Path(tempfile.mkdtemp(prefix="feed-"))
    make_subject, saved, _ = _patched_cli(tmp)
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        with subject._transaction():
            subject.engine.state.needs["hunger"] = 0.8
            subject.engine.state.needs["thirst"] = 0.8
            h0 = subject.engine.state.needs["hunger"]
            t0 = subject.engine.state.needs["thirst"]
            subject.engine.step(feeder.event_for("food"),
                                advance_time=False, choose_conduct=False)
            dh = h0 - subject.engine.state.needs["hunger"]
            subject.engine.step(feeder.event_for("drink"),
                                advance_time=False, choose_conduct=False)
            dt = t0 - subject.engine.state.needs["thirst"]
        assert abs(dh - 0.35) < 1e-9, dh
        assert abs(dt - 0.40) < 1e-9, dt
    finally:
        _restore(saved)


# -- maybe_feed: enqueue discipline ------------------------------------------

def test_maybe_feed_enqueues_due_kinds():
    tmp = Path(tempfile.mkdtemp(prefix="feed-"))
    make_subject, saved, _ = _patched_cli(tmp)
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        _set_state(subject, tick=48, hunger=0.9, thirst=0.1)
        kinds = feeder.maybe_feed(subject)
        assert kinds == ["food"], kinds
        assert [e["kind"] for e in subject.inspect()["pending"]] == ["food"]
        _set_state(subject, tick=24, hunger=0.1, thirst=0.9)
        kinds = feeder.maybe_feed(subject)
        assert kinds == ["drink"], kinds
        # Below threshold at a rhythm tick: nothing enqueued.
        _set_state(subject, tick=96, hunger=0.5, thirst=0.5)
        n_before = len(subject.inspect()["pending"])
        assert feeder.maybe_feed(subject) == []
        assert len(subject.inspect()["pending"]) == n_before
    finally:
        _restore(saved)


def test_maybe_feed_dream_guard():
    """Defense in depth for the revert signal: even if called while
    dreaming, the feeder emits nothing."""
    tmp = Path(tempfile.mkdtemp(prefix="feed-"))
    make_subject, saved, _ = _patched_cli(tmp)
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        _set_state(subject, tick=48, hunger=0.9, thirst=0.9)
        subject._dreaming = True
        try:
            assert feeder.maybe_feed(subject) == []
        finally:
            subject._dreaming = False
        assert subject.inspect()["pending"] == []
    finally:
        _restore(saved)


def test_maybe_feed_creates_no_files():
    """No sidecar for this mutation: maybe_feed touches no files."""
    tmp = Path(tempfile.mkdtemp(prefix="feed-"))
    make_subject, saved, _ = _patched_cli(tmp)
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        before = sorted(p.name for p in tmp.iterdir())
        _set_state(subject, tick=48, hunger=0.9, thirst=0.9)
        feeder.maybe_feed(subject)
        after = sorted(p.name for p in tmp.iterdir())
        assert before == after, (before, after)
    finally:
        _restore(saved)


# -- end to end: bounded band, diversified conduct, determinism ---------------

def _trajectory(ticks, feed):
    """Per-tick (hunger, thirst, action); feed=False monkeypatches the
    feeder off (the pristine control)."""
    tmp = Path(tempfile.mkdtemp(prefix="feed-"))
    make_subject, saved, _ = _patched_cli(tmp)
    real = feeder.maybe_feed
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        if not feed:
            feeder.maybe_feed = lambda s: []
        hs, ts, acts = [], [], []
        for _ in range(ticks):
            result = _quiet(cli._run_tick, subject)
            hs.append(round(subject.engine.state.needs["hunger"], 4))
            ts.append(round(subject.engine.state.needs["thirst"], 4))
            acts.append(result["action"])
        subject = make_subject()
        return hs, ts, acts, subject.inspect()
    finally:
        feeder.maybe_feed = real
        _restore(saved)


def test_long_run_band_bounded_never_pins():
    hs, ts, acts, state = _trajectory(600, feed=True)
    # Bounded: never pins at 1.0 (the pre-feeder degeneracy).
    assert max(hs) < 0.85, max(hs)
    assert max(ts) < 0.85, max(ts)
    # No over-satiation (revert signal): after warm-up the needs stay
    # well clear of 0 — the creature still hungers.
    assert min(hs[300:]) > 0.15, min(hs[300:])
    assert min(ts[300:]) > 0.15, min(ts[300:])
    # Feeding actually happened through the real tick path.
    assert len(_feed_records(state)) >= 2, "no feedings fired in 600 ticks"


def test_pristine_control_pins_at_one():
    """The control the feeder fixes: without it, hunger and thirst pin at
    1.0 over a long run (measured 2026-10-08 in vivo)."""
    hs, ts, acts, _ = _trajectory(600, feed=False)
    assert max(hs) == 1.0, max(hs)
    assert max(ts) == 1.0, max(ts)


def test_conduct_diversifies():
    """Pre-feeder, seek_contact dominated (134/156 action-bearing trace
    entries in vivo; 0.92 in the pinned regime here). With the feeder no
    single action may dominate to that degree. Measured on the last 200
    ticks (the steady-state window — the early ramp-up is pre-feeding in
    both runs)."""
    _, _, acts, _ = _trajectory(600, feed=True)
    _, _, acts0, _ = _trajectory(600, feed=False)
    top = Counter(acts[-200:]).most_common(1)[0][1] / 200
    top0 = Counter(acts0[-200:]).most_common(1)[0][1] / 200
    assert top0 > 0.8, top0  # control really is degenerate
    assert top < 0.6, (top, Counter(acts[-200:]).most_common(3))
    assert top < top0


def test_determinism():
    """Identical tick sequences -> identical feeding schedules and need
    trajectories. Engine memory ids are uuid4 (pre-existing engine
    behavior), so the store-JSON comparison normalizes them."""
    h1, t1, a1, s1 = _trajectory(300, feed=True)
    h2, t2, a2, s2 = _trajectory(300, feed=True)
    assert h1 == h2 and t1 == t2, "need trajectories diverged"
    assert a1 == a2, "action sequences diverged"
    f1 = sorted(r["tick"] for r in _feed_records(s1))
    f2 = sorted(r["tick"] for r in _feed_records(s2))
    assert f1 == f2 and len(f1) > 0, (f1, f2)
    uuid_re = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    n1 = uuid_re.sub("UUID", json.dumps(s1, sort_keys=True))
    n2 = uuid_re.sub("UUID", json.dumps(s2, sort_keys=True))
    assert n1 == n2, "uuid-normalized store state diverged"


# -- isolation: dream ticks and read-only commands never feed -----------------

def test_dream_tick_never_feeds():
    """At a rhythm tick with high hunger, a dream tick must not feed:
    dream isolation (body/tick/trace) is asserted inside dream_tick()."""
    tmp = Path(tempfile.mkdtemp(prefix="feed-"))
    make_subject, saved, _ = _patched_cli(tmp)
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        for _ in range(48):
            _quiet(cli._run_tick, subject)  # tick == 48: food rhythm tick
        assert subject.engine.state.tick == 48
        _set_state(subject, hunger=0.9, thirst=0.9)
        n_before = len(subject.workspace.records)
        subject.dream_tick()
        assert subject.inspect()["pending"] == []
        assert len(subject.workspace.records) == n_before or \
            all(FED_MARK not in r.first_person and EASED_MARK not in r.first_person
                for r in subject.workspace.records[n_before:]), \
            "a dream tick fed the body"
        assert _feed_records(subject.inspect()) == []
    finally:
        _restore(saved)


def test_readonly_commands_never_feed():
    """drift / status never call _run_tick, so they can never feed — even
    at a rhythm tick with the body hungry."""
    tmp = Path(tempfile.mkdtemp(prefix="feed-"))
    make_subject, saved, _ = _patched_cli(tmp)
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        for _ in range(48):
            _quiet(cli._run_tick, subject)
        _set_state(subject, hunger=0.9, thirst=0.9)
        recs_before = [(r["id"], r["source"], r["first_person"])
                       for r in subject.inspect()["workspace"]["records"]]
        assert _quiet(cli.cmd_drift, argparse.Namespace(window=10)) == 0
        assert _quiet(cli.cmd_status, argparse.Namespace(raw=False)) == 0
        after = subject.inspect()
        assert [(r["id"], r["source"], r["first_person"])
                for r in after["workspace"]["records"]] == recs_before, \
            "a read-only command changed the records"
        assert after["engine"]["needs"]["hunger"] == 0.9
        assert after["pending"] == []
        assert _feed_records(after) == []
    finally:
        _restore(saved)


def test_init_force_unaffected():
    """No sidecar exists for this mutation: reseed wipes nothing
    feeder-specific, and the stateless schedule works on a fresh store."""
    tmp = Path(tempfile.mkdtemp(prefix="feed-"))
    make_subject, saved, paths = _patched_cli(tmp)
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        for _ in range(60):
            _quiet(cli._run_tick, subject)
        names = sorted(p.name for p in tmp.iterdir())
        assert not any("feed" in n for n in names), names
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        _set_state(subject, tick=48, hunger=0.9)
        assert feeder.maybe_feed(subject) == ["food"]
    finally:
        _restore(saved)


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}", flush=True)
    print(f"all {len(fns)} feeder tests passed")


if __name__ == "__main__":
    _main()
