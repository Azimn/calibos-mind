"""Critic round 1: host-layer feeder for food/drink channels.

Cold brief: the SPEC (proposals/2026-10-08-host-feeder-food-drink.md) + the
diff only. Every objection below arrives as a failing test or a concretely
violated invariant with measured evidence. Prose-only critique is rejected
by the loop rules (research/builder-critic-loop.md).

What this battery attacks (beyond the builder's 15 tests):
- The exact caller set of maybe_feed: which CLI commands can ever feed?
  "Waking ticks only" must hold for EVERY command, not just drift/status.
- Exact engine semantics through the REAL tick path (surprise, intensity
  scaling, event-field fidelity against EVENT_RULES) — not just the
  isolated engine.step probe the builder ran.
- The fitness claims under LIVE wiring (all five trackers attached, as
  cli._subject does). The builder's synthetic runs attached only
  salience+interoception, omitting the habits mechanism the spec's own
  problem statement centers on.
- The live-like regime: pre-formed hunger/thirst:seek_contact habits at
  0.60 + pinned needs (the actual live-store state the mutation ships
  into). Does conduct still diversify, or do the old habits hold the
  constant?
- Phase-locking (revert signal): is conduct better predicted by feeder
  phase than by need levels?
- Habit-evidence contamination: do the feeder's -0.35/-0.40 drops ever
  enter the habits tracker's formation evidence?
- Interoception episode hygiene: do feeding swings close or accumulate?
- Regression genome items, each explicitly.
- The 2026-10-08 harness lesson: complete redirect set + live-path
  restoration discipline.

Run: cd ~/workspace/calibos-mind && ./.venv/bin/python -m pytest tests/adversarial/test_critic_feeder_r1.py
All fixtures live in /tmp. The live store is never touched.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind import feeder
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from calibos_mind.habits import HabitFormationTracker
from digital_subject.cartridge import load_cartridge
from digital_subject import rules as engine_rules

BASE = Path(__file__).resolve().parents[2]
CARTRIDGE = load_cartridge(BASE / "calibos.toml")

# The canonical redirect set (2026-10-08 harness fix): every module-level
# cli path EXCEPT CARTRIDGE_PATH (read-only input). WAKE_LIVENESS /
# ABLATION_* are written only by cmd_wake / cmd_ablation, which no feeder
# test invokes — the redirect set is complete for the commands exercised.
REDIRECT = [("DB", "mind.db"), ("INBOX", "inbox"), ("DREAMS", "dreams"),
            ("SALIENCE", "salience.json"),
            ("INTEROCEPTION", "interoception.json"),
            ("FAMILIARITY", "familiarity.json"),
            ("AMBIVALENCE", "ambivalence.json"),
            ("HABITS", "habits-formed.json"),
            ("PROVENANCE", "provenance.json"),
            ("ARCHIVE", "archive"), ("PROPOSALS", "proposals")]
DIR_NAMES = {"inbox", "dreams", "archive", "proposals"}


def _patched_cli_full(tmp: Path):
    """Live-wiring harness: all 11 cli paths redirected AND all five
    trackers attached, exactly as cli._subject does in production."""
    paths = {}
    for name, fname in REDIRECT:
        p = tmp / fname
        if fname in DIR_NAMES:
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
                              interoception_path=str(paths["INTEROCEPTION"]),
                              familiarity_path=str(paths["FAMILIARITY"]),
                              ambivalence_path=str(paths["AMBIVALENCE"]),
                              habits_path=str(paths["HABITS"]))

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
    with subject._transaction():
        for key, value in vals.items():
            if key == "tick":
                subject.engine.state.tick = value
            else:
                subject.engine.state.needs[key] = value


def _fresh(tmp_prefix="crit-"):
    tmp = Path(tempfile.mkdtemp(prefix=tmp_prefix))
    return tmp, _patched_cli_full(tmp)


# -- 1. exact caller set: which commands can ever feed? -----------------------

def test_exact_caller_set_of_maybe_feed():
    """'Waking ticks only' must hold for EVERY command. Patch maybe_feed
    with a recorder; the set of commands that trigger it must be exactly
    {note, heartbeat}. Positive controls prove the recorder works."""
    tmp, (make_subject, saved, paths) = _fresh()
    real = feeder.maybe_feed
    calls = []

    def recording(subject):
        kinds = real(subject)
        calls.append(kinds)
        return kinds

    feeder.maybe_feed = recording
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        # Positive control: heartbeat at a rhythm tick with hunger high.
        subject = make_subject()
        _set_state(subject, tick=48, hunger=0.9, thirst=0.1)
        _quiet(cli.cmd_heartbeat, argparse.Namespace(ticks=1))
        assert calls and calls[-1] == ["food"], \
            f"positive control failed: heartbeat did not feed: {calls}"
        # Positive control: note tick at a rhythm tick.
        subject = make_subject()
        _set_state(subject, tick=96, hunger=0.9, thirst=0.1)
        _quiet(cli.cmd_note, argparse.Namespace(
            text="an observation", kind="observation", source="world",
            tags="", valence=0.0))
        assert calls[-1] == ["food"], \
            f"positive control failed: note tick did not feed: {calls[-1]}"
        n_pos = len(calls)
        # The sweep: every other command, at a rhythm tick, body hungry.
        subject = make_subject()
        _set_state(subject, tick=48, hunger=0.9, thirst=0.9)
        _quiet(cli.cmd_drift, argparse.Namespace(window=10))
        _quiet(cli.cmd_status, argparse.Namespace(raw=False))
        _quiet(cli.cmd_think, argparse.Namespace(
            text="a voluntary thought for the sweep", weighed=None,
            discarded=None, unsure=None))
        _quiet(cli.cmd_recall, argparse.Namespace(n=3))
        _quiet(cli.cmd_review, argparse.Namespace(n=3))
        _quiet(cli.cmd_inbox, argparse.Namespace(id=None))
        _quiet(cli.cmd_queue, argparse.Namespace(
            prompt="sweep prompt?", source="invitation", experience=None,
            from_person=None))
        _quiet(cli.cmd_remember, argparse.Namespace(
            text="sweep memory: the sky was blue", concepts=None))
        _quiet(cli.cmd_resolve, argparse.Namespace(
            id="zzz-no-such-commitment", released=False, note=None))
        _quiet(cli.cmd_consolidate, argparse.Namespace(
            list=True, accept=None, reject=None, quarantine=None))
        # answer: queue then answer silently (no tick expected).
        _quiet(cli.cmd_queue, argparse.Namespace(
            prompt="answer me", source="invitation", experience=None,
            from_person=None))
        import glob as _glob
        prompt_files = _glob.glob(str(paths["INBOX"] / "prompt-*.json"))
        assert prompt_files, "queue did not write a prompt"
        pid = json.loads(Path(prompt_files[-1]).read_text())["id"]
        _quiet(cli.cmd_answer, argparse.Namespace(id=pid, text=None, silent=True))
        assert len(calls) == n_pos, \
            f"a non-tick command fed the body: {calls[n_pos:]}"
    finally:
        feeder.maybe_feed = real
        _restore(saved)


# -- 2. exact engine semantics through the REAL tick path ---------------------

def test_real_path_exact_engine_delta():
    """The fitness claim 'food -> hunger -0.35 exactly' must hold through
    cli._run_tick, not just an isolated engine.step probe: surprise must be
    0 after the full own/event-reconstruction path, intensity scaling 1.0,
    and the event consumed in the same tick it was enqueued."""
    tmp, (make_subject, saved, paths) = _fresh()
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        with subject._transaction():
            subject.engine.state.needs["hunger"] = 0.5
            subject.engine.state.needs["thirst"] = 0.1
        h0 = subject.engine.state.needs["hunger"]
        _quiet(cli._run_tick, subject)  # entry tick 0: no feeding
        rise = subject.engine.state.needs["hunger"] - h0
        assert abs(rise - 0.0020) < 1e-9, rise  # homeostatic baseline
        _set_state(subject, tick=48, hunger=0.9, thirst=0.1)
        h1 = subject.engine.state.needs["hunger"]
        _quiet(cli._run_tick, subject)
        net = subject.engine.state.needs["hunger"] - h1
        assert net == -0.35 + rise, \
            f"real-path delta {net} != engine math {-0.35 + rise} " \
            f"(surprise != 0 or intensity scaling through the tick path)"
        assert subject.inspect()["pending"] == [], \
            "feeder event was not consumed in its own tick"
    finally:
        _restore(saved)


def test_event_fields_match_engine_food_semantics():
    """The emitted event must BE the engine's food/drink event, not a
    lookalike: pin the field contract AND the engine's EVENT_RULES side."""
    for kind, need, amount in (("food", "hunger", -0.35),
                               ("drink", "thirst", -0.40)):
        ev = feeder.event_for(kind)
        assert ev.kind == kind
        assert ev.source == "world"  # environmental, not social
        assert ev.tags == (kind,)
        assert ev.intensity == 1.0
        assert ev.valence == 0.0
        assert ev.expected_valence == 0.0 and ev.actual_valence == 0.0
        assert ev.description.strip()
        rule = engine_rules.EVENT_RULES[kind]
        assert rule.need_deltas[need] == amount, \
            f"engine EVENT_RULES[{kind}] changed: {rule.need_deltas}"
        assert not rule.concern and not rule.belief_key, \
            f"engine EVENT_RULES[{kind}] gained concern/belief side effects"
    # scale check: surprise==0 and intensity==1.0 -> scale exactly 1.0
    ev = feeder.event_for("food")
    assert ev.intensity * (1.0 + abs(ev.actual_valence - ev.expected_valence) * 0.4) == 1.0


# -- 3. schedule purity / boundaries ------------------------------------------

def test_due_kinds_pure_no_hidden_state():
    needs = {"hunger": 0.9, "thirst": 0.9}
    first = feeder.due_kinds(48, needs)
    for _ in range(1000):
        assert feeder.due_kinds(48, needs) == first
    assert needs == {"hunger": 0.9, "thirst": 0.9}, "due_kinds mutated its input"
    assert feeder.due_kinds(0, {"hunger": 1.0, "thirst": 1.0}) == []
    import math
    assert feeder.due_kinds(48, {"hunger": math.nextafter(0.6, 0.0),
                                 "thirst": 0.0}) == [], "gate must be >= 0.60"
    assert feeder.due_kinds(48, {"hunger": 0.6, "thirst": 0.0}) == ["food"]


def test_feeding_tick_alignment_no_off_by_one():
    """Feeding entry-ticks must sit exactly on the 48 / 24+48 rhythm the
    spec names — the hook reads state.tick BEFORE heartbeat advances it."""
    tmp, (make_subject, saved, paths) = _fresh()
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        entry_feed = []
        for _ in range(700):
            entry = subject.engine.state.tick
            kinds = feeder.due_kinds(entry, dict(subject.engine.state.needs))
            _quiet(cli._run_tick, subject)
            if kinds:
                entry_feed.append(entry)
        assert entry_feed, "no feedings in 700 ticks"
        for t in entry_feed:
            assert t % 48 == 0 or (t - 24) % 48 == 0, \
                f"feeding at entry tick {t} is off the 48/24+48 rhythm"
    finally:
        _restore(saved)


# -- 4. long-run band: no pinning, no over-satiation ---------------------------

def _band(ticks, feed):
    tmp, (make_subject, saved, paths) = _fresh("crit-band-")
    real = feeder.maybe_feed
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        if not feed:
            feeder.maybe_feed = lambda s: []
        hs, ts = [], []
        for _ in range(ticks):
            _quiet(cli._run_tick, subject)
            hs.append(subject.engine.state.needs["hunger"])
            ts.append(subject.engine.state.needs["thirst"])
        return hs, ts
    finally:
        feeder.maybe_feed = real
        _restore(saved)


def test_long_run_band_bounded_never_pins_no_oversatiation():
    hs, ts = _band(1500, feed=True)
    assert max(hs) < 0.85, max(hs)
    assert max(ts) < 0.85, max(ts)
    # Revert signal: over-satiation. The 0.60 gate makes hunger>=0.25 and
    # thirst>=0.20 after any feeding by construction; the long run must
    # show the creature still hungers.
    assert min(hs[500:]) > 0.15, min(hs[500:])
    assert min(ts[500:]) > 0.15, min(ts[500:])
    hs0, ts0 = _band(1500, feed=False)
    assert max(hs0) == 1.0 and max(ts0) == 1.0  # control still degenerate


# -- 5. diversification under LIVE wiring + live-like regime ------------------

def _actions(ticks, feed, preseed=False):
    tmp, (make_subject, saved, paths) = _fresh("crit-div-")
    real = feeder.maybe_feed
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        if preseed:
            # The live store's actual pathology: formed hunger/thirst
            # seek_contact habits at 0.60 (below the 0.65 fire threshold).
            sidecar = {"formed": {
                "formed:hunger:seek_contact": {
                    "trigger": "hunger", "action": "seek_contact",
                    "strength": 0.60, "peak": 0.60, "created_tick": 0,
                    "cofires": 20, "last_tick": 0},
                "formed:thirst:seek_contact": {
                    "trigger": "thirst", "action": "seek_contact",
                    "strength": 0.60, "peak": 0.60, "created_tick": 0,
                    "cofires": 20, "last_tick": 0}},
                "window": [], "archived": []}
            paths["HABITS"].write_text(json.dumps(sidecar))
        subject = make_subject()
        if not feed:
            feeder.maybe_feed = lambda s: []
        if preseed:
            with subject._transaction():
                subject.engine.state.needs["hunger"] = 1.0
                subject.engine.state.needs["thirst"] = 1.0
        acts = []
        for _ in range(ticks):
            acts.append(_quiet(cli._run_tick, subject)["action"])
        return acts
    finally:
        feeder.maybe_feed = real
        _restore(saved)


def test_conduct_diversifies_with_live_wiring():
    """The builder measured diversification WITHOUT the habits tracker
    attached. The spec's problem statement centers on habit hardening, so
    the claim must hold with the full live wiring."""
    fed = _actions(600, feed=True)
    pristine = _actions(600, feed=False)
    top = Counter(fed[-200:]).most_common(1)[0][1] / 200
    top0 = Counter(pristine[-200:]).most_common(1)[0][1] / 200
    assert top0 > 0.8, f"control not degenerate: {top0}"
    assert top < 0.6, f"fed run still dominated: {top}"
    assert top < top0


def test_conduct_diversifies_in_live_like_regime():
    """Pre-seeded formed seek_contact habits (the live store's state) must
    not hold the constant: the feeder starves them of trigger
    opportunities faster than they can fire."""
    fed = _actions(600, feed=True, preseed=True)
    pristine = _actions(600, feed=False, preseed=True)
    top = Counter(fed[-200:]).most_common(1)[0][1] / 200
    top0 = Counter(pristine[-200:]).most_common(1)[0][1] / 200
    assert top0 > 0.85, f"preseeded control not degenerate: {top0}"
    assert top < 0.6, \
        f"pre-seeded habits held the constant despite the feeder: {top} " \
        f"{Counter(fed[-200:]).most_common(3)}"
    assert top < top0


# -- 6. phase-locking (revert signal) ------------------------------------------

def test_conduct_responds_to_needs_not_feeder_phase():
    """Revert signal: 'conduct phase-locks to the feeder instead of
    responding to needs'. Operationalized as nearest-neighbor agreement:
    a feeding tick's action must match the action of the rest tick with
    the closest POST-tick need vector at least as often as rest ticks
    match theirs. (An earlier formulation of this test matched on
    PRE-tick vectors and failed spuriously -- test-premise drift, caught
    by measurement: the selection sees post-drop needs. The corrected
    formulation below is the honest one.)

    Structural note: no architecture component can learn tick-phase
    (habits are trigger-keyed, expectations prompt-keyed, interoception
    need-keyed); this test is the behavioral guard."""
    import math
    import random
    NEEDS = ["hunger", "thirst", "energy", "fatigue", "loneliness",
             "curiosity", "comfort", "focus", "restlessness",
             "satisfaction", "safety", "warmth", "pain"]
    tmp, (make_subject, saved, paths) = _fresh("crit-phase-")
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        data = []
        feed_obs = set()
        for _ in range(1500):
            entry = subject.engine.state.tick
            kinds = feeder.due_kinds(entry, dict(subject.engine.state.needs))
            if kinds:
                feed_obs.add(entry + 1)  # note tick = entry tick + 1
            result = _quiet(cli._run_tick, subject)
            tick = subject.engine.state.tick
            # POST-tick needs == what select_conduct saw: nothing mutates
            # needs after selection within the tick.
            vec = tuple(round(subject.engine.state.needs.get(k, 0.0), 6)
                        for k in NEEDS)
            data.append((vec, result["action"], tick in feed_obs))
        feeds = [(v, a) for v, a, f in data if f]
        rests = [(v, a) for v, a, f in data if not f]
        assert len(feeds) >= 8, f"too few feedings for the guard: {len(feeds)}"

        def dist(u, v):
            return math.sqrt(sum((x - y) ** 2 for x, y in zip(u, v)))

        agree_feed = 0
        for v, a in feeds:
            _, ba = min(rests, key=lambda r: dist(v, r[0]))
            agree_feed += (ba == a)
        random.seed(1)
        sample = random.sample(rests, 300)
        agree_base = 0
        for v, a in sample:
            cands = [r for r in rests if not (r[0] == v and r[1] == a)]
            _, ba = min(cands, key=lambda r: dist(v, r[0]))
            agree_base += (ba == a)
        rate_feed = agree_feed / len(feeds)
        rate_base = agree_base / len(sample)
        assert rate_feed >= rate_base - 0.30, \
            f"feeding ticks choose differently than need-matched rest ticks: " \
            f"{rate_feed:.2f} vs baseline {rate_base:.2f} -- phase-locking"
    finally:
        _restore(saved)


# -- 7. habit-evidence contamination -------------------------------------------

def test_feeder_drops_never_enter_habit_evidence():
    """The habits tracker credits each tick's need delta to that tick's
    (trigger, action). The feeder's -0.35/-0.40 drops must not become
    habit evidence: conduct is selected AFTER the satiation lands, so the
    fed need is never the tick's trigger/dominant need. Measured, not
    assumed: log every observation across 600 fed ticks."""
    tmp, (make_subject, saved, paths) = _fresh("crit-hab-")
    real_obs = HabitFormationTracker.observe_tick
    log = []

    def wrapped(self, *, tick, trigger, action, dominant_need, need_deltas):
        log.append({"tick": tick, "trigger": trigger, "action": action,
                    "dominant_need": dominant_need,
                    "deltas": dict(need_deltas)})
        return real_obs(self, tick=tick, trigger=trigger, action=action,
                        dominant_need=dominant_need, need_deltas=need_deltas)

    HabitFormationTracker.observe_tick = wrapped
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        feed_obs_ticks = set()
        for _ in range(600):
            entry = subject.engine.state.tick
            kinds = feeder.due_kinds(entry, dict(subject.engine.state.needs))
            _quiet(cli._run_tick, subject)
            if kinds:
                feed_obs_ticks.add(entry + 1)  # note tick = entry+1
        assert feed_obs_ticks, "no feedings fired; test premise broken"
        for e in log:
            if e["tick"] in feed_obs_ticks:
                dk = e["trigger"] if e["trigger"] in e["deltas"] \
                    else e["dominant_need"]
                credited = e["deltas"].get(dk, 0.0)
                assert credited > -0.1, \
                    f"feeder drop entered habit evidence at tick {e['tick']}: " \
                    f"({e['trigger']}, {e['action']}) credited {credited:.4f}"
    finally:
        HabitFormationTracker.observe_tick = real_obs
        _restore(saved)


# -- 8. interoception hygiene ---------------------------------------------------

def test_interoception_episodes_close_no_accumulation():
    """Feeding swings (-0.35 in one tick) open interoceptive episodes; they
    must close (minting honest realizations), not accumulate as open
    swings in the sidecar."""
    tmp, (make_subject, saved, paths) = _fresh("crit-intero-")
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        for _ in range(600):
            _quiet(cli._run_tick, subject)
        sidecar = json.loads(paths["INTEROCEPTION"].read_text())
        open_swings = [need for need, rec in sidecar["needs"].items()
                       if rec.get("swing")]
        assert len(open_swings) <= 2, \
            f"unclosed interoceptive swings accumulate: {open_swings}"
    finally:
        _restore(saved)


# -- 9. determinism under live wiring -------------------------------------------

def test_determinism_full_wiring():
    runs = []
    for _ in range(2):
        tmp, (make_subject, saved, paths) = _fresh("crit-det-")
        try:
            _quiet(cli.cmd_init, argparse.Namespace(force=True))
            subject = make_subject()
            hs, ts, feeds = [], [], []
            for _ in range(300):
                entry = subject.engine.state.tick
                kinds = feeder.due_kinds(entry, dict(subject.engine.state.needs))
                if kinds:
                    feeds.append((entry, tuple(kinds)))
                _quiet(cli._run_tick, subject)
                hs.append(round(subject.engine.state.needs["hunger"], 6))
                ts.append(round(subject.engine.state.needs["thirst"], 6))
            runs.append((hs, ts, feeds))
        finally:
            _restore(saved)
    assert runs[0][2] == runs[1][2] and runs[0][2], "feeding schedules diverged"
    assert runs[0][0] == runs[1][0] and runs[0][1] == runs[1][1], \
        "need trajectories diverged under live wiring"


# -- 10. dream isolation at the rhythm tick -------------------------------------

def test_dream_tick_at_rhythm_tick_never_feeds():
    tmp, (make_subject, saved, paths) = _fresh()
    try:
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        _set_state(subject, tick=48, hunger=0.9, thirst=0.9)
        assert feeder.due_kinds(48, {"hunger": 0.9, "thirst": 0.9}) != [], \
            "premise broken: nothing due at tick 48"
        subject.dream_tick()  # isolation assertions run inside
        assert subject.inspect()["pending"] == []
        recs = subject.inspect()["workspace"]["records"]
        assert not [r for r in recs
                    if r["source"] == "perception" and
                    ("I have been fed." in r["first_person"] or
                     "My thirst has eased." in r["first_person"])], \
            "a dream tick fed the body"
        # The defensive guard, directly:
        subject._dreaming = True
        try:
            assert feeder.maybe_feed(subject) == []
        finally:
            subject._dreaming = False
    finally:
        _restore(saved)


# -- 11. engine + cartridge untouched --------------------------------------------

def test_engine_and_cartridge_untouched():
    out = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "calibos.toml"],
                         cwd=str(BASE))
    assert out.returncode == 0, "calibos.toml modified: fingerprint migration needed"
    src = (BASE / "calibos_mind" / "feeder.py").read_text()
    tree = ast.parse(src)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
    assert imports <= {"__future__", "digital_subject"}, \
        f"feeder reaches beyond the engine's Event model: {imports}"
    assert "engine" not in src or "digital_subject.models" in src or \
        "_apply_event" not in src, "feeder touches engine internals"


# -- 12. harness discipline (the 2026-10-08 lesson) -------------------------------

def test_harness_redirects_complete_path_set_and_restores():
    """The critic's own harness must redirect the complete 11-path set and
    leave the live module state exactly as found (the defect class that
    deleted live sidecars on 2026-10-08)."""
    live_before = {name: getattr(cli, name) for name, _ in REDIRECT}
    assert live_before["DB"] == BASE / "mind.db", "premise: live DB path"
    tmp, (make_subject, saved, paths) = _fresh("crit-meta-")
    try:
        for name, _ in REDIRECT:
            target = getattr(cli, name)
            assert str(target).startswith(str(tmp)), \
                f"cli.{name} not redirected: {target}"
        assert len({n for n, _ in REDIRECT}) == 11
        _quiet(cli.cmd_init, argparse.Namespace(force=True))
        subject = make_subject()
        for _ in range(5):
            _quiet(cli._run_tick, subject)
        # writes landed in tmp: the synthetic store exists only there
        assert (tmp / "mind.db").exists()
    finally:
        _restore(saved)
    for name, _ in REDIRECT:
        assert getattr(cli, name) == live_before[name], \
            f"cli.{name} not restored after harness (live-path leak)"
