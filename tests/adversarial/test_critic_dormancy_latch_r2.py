"""Critic round 2 attacks on lost-latch recovery (domain 28, 2026-11-21).

Spec: research/specs/2026-11-21-dormancy-latch-recovery.md.
Target: calibos_mind/authored_dormancy.py::recover_lost_latches,
wired at the top of calibos_mind/cli.py::_run_tick.

Round 2 claims: (a) a pending owed mark on a candidate entry fails loud
(round-1 FAIL 1 fix); (b) the note trust boundary matches
mint_demotion_note's idempotency check (source temporal +
generated_by cognition); (c) temporal consistency of demoted_tick vs
the entry's last_fired_tick (more than DORMANCY_TICKS after it).

Every objection here is a failing test against a concretely violated
invariant, not prose. All fixtures live in /tmp; the live store is
never touched (conftest autouse fixture redirects every cli path).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind.authored_dormancy import (
    AuthoredDormancyTracker,
    demotion_note_text,
    is_demotion_note_text,
    mint_demotion_note,
)
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject
from digital_subject.cartridge import load_cartridge
from digital_subject.models import Action, Habit

CQ = "curious_question"          # baseline 0.72
VBC = "verify_before_claiming"  # baseline 0.68


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-dormancy-latch-r2-"))


def _seed_sidecar(tmp: Path, **entries):
    path = tmp / "authored-dormancy.json"
    path.write_text(json.dumps({"habits": entries}), encoding="utf-8")
    return path


def _entry(key, baseline, last_fired_tick, demoted=False,
           demoted_tick=None, owed=None):
    return {key: {"baseline": baseline, "last_fired_tick": last_fired_tick,
                  "demoted": demoted, "demoted_tick": demoted_tick,
                  "owed": owed}}


def _note_record(key, baseline, span, tick):
    """A record-shaped object carrying one canonical demotion note with
    the provenance a real minted note carries."""
    return SimpleNamespace(
        source="temporal", generated_by="cognition",
        first_person=demotion_note_text(
            {"habit_key": key, "dormancy_ticks": span, "baseline": baseline,
             "demoted_tick": tick}))


def _make_subject(tmp: Path) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        interoception_path=None,
        familiarity_path=None,
        ambivalence_path=None,
        habits_path=str(tmp / "habits-formed.json"),
        dormancy_path=str(tmp / "authored-dormancy.json"),
    )


def _demotion_notes(subject):
    return [r for r in subject.workspace.records
            if r.source == "temporal" and r.generated_by == "cognition"
            and is_demotion_note_text(r.first_person)]


def _quiet_tick(subject):
    """A waking tick on which no authored habit can fire."""
    with subject._transaction():
        st = subject.engine.state
        st.needs = {"energy": 0.95, "fatigue": 0.05, "hunger": 0.80,
                    "thirst": 0.05, "comfort": 0.95, "pain": 0.0,
                    "warmth": 0.9, "restlessness": 0.05,
                    "curiosity": 0.10, "loneliness": 0.05,
                    "safety": 0.9, "focus": 0.9, "satisfaction": 0.9}
        st.pressures = {"fear": 0.10, "trust": 0.5, "arousal": 0.05}
        st.pressure_baselines = {"fear": 0.0, "trust": 0.5, "arousal": 0.0}
        st.concerns = {}
    return cli._run_tick(subject)


# -- ATTACK 1: fresh sidecar must heal BOTH live latches in one tick ----------
# Invariant (spec fitness: "Fresh sidecar" + "Both live habits"): no
# sidecar file at all, both habits at 0.64, both notes in the store.
# The builder tests fresh-seed recovery for ONE habit and both-habit
# recovery for a SEEDED sidecar, but never the composition: two missing
# entries seeded and latched in the same tick. A per-key seeding bug
# (e.g. the second missing entry judged on the first key's clock, or
# the phase-3 loop short-circuiting after the first seed) would mint a
# spurious second demotion for the unrecovered habit: the normal edge
# would see it at 0.64 with demoted False... no — the edge needs
# strength >= 0.65, so it cannot re-demote. The failure mode instead
# is a fail-loud raise (VBC's note judged on CQ's seeded clock) or a
# silently unlatched VBC left for a later tick. Either breaks the
# in-vivo shape: the live store's next waking tick must heal BOTH
# latches (the sidecar file is absent in the fresh-sidecar reading of
# the incident only if it is lost again; the live file exists but the
# same seeding path runs whenever an entry is missing).
def test_critic_r2_fresh_sidecar_recovers_both_live_latches():
    tmp = _tmp()
    subject = _make_subject(tmp)  # no sidecar file at all
    assert not (tmp / "authored-dormancy.json").exists()
    with subject._transaction():
        st = subject.engine.state
        st.tick = 735
        st.habits[CQ].strength = 0.64
        st.habits[CQ].last_used_tick = 7
        st.habits[VBC].strength = 0.64
        st.habits[VBC].last_used_tick = -10000
        mint_demotion_note(subject, {"habit_key": CQ, "dormancy_ticks": 728,
                                     "baseline": 0.72, "demoted_tick": 735})
        mint_demotion_note(subject, {"habit_key": VBC, "dormancy_ticks": 10735,
                                     "baseline": 0.68, "demoted_tick": 735})
    assert len(_demotion_notes(subject)) == 2
    _quiet_tick(subject)  # tick 736: both seeded entries recover at once
    assert len(_demotion_notes(subject)) == 2  # no new record minted
    assert subject.engine.state.habits[CQ].strength == 0.64
    assert subject.engine.state.habits[VBC].strength == 0.64
    sidecar = json.loads((tmp / "authored-dormancy.json").read_text())
    for key in (CQ, VBC):
        entry = sidecar["habits"][key]
        assert entry["demoted"] is True and entry["demoted_tick"] == 735, (
            f"{key} latch was not restored by the fresh-sidecar tick")
        assert entry["owed"] is None
    # Seeded clocks stand: CQ from its live stamp, VBC never-fired.
    assert sidecar["habits"][CQ]["last_fired_tick"] == 7
    assert sidecar["habits"][VBC]["last_fired_tick"] == -10000
    # Second tick: idempotent, sidecar byte-identical.
    after_first = (tmp / "authored-dormancy.json").read_bytes()
    _quiet_tick(subject)
    assert (tmp / "authored-dormancy.json").read_bytes() == after_first
    assert len(_demotion_notes(subject)) == 2


# -- ATTACK 2: an owed mark on a SIBLING blocks the healthy key too ----------
# Invariant (spec: "every key is validated before any key is mutated"):
# the two-phase guarantee must extend to the owed check. CQ has a valid
# note and would recover; VBC holds a pending owed mark for a different
# episode. Recovery must raise and leave CQ's latch unrestored in
# memory and the sidecar bytes untouched — a half-applied tick (CQ
# latched, VBC refused) would let the operator heal one latch while
# the inconsistent sibling stays behind, and the saved sidecar would
# then disagree with the refused state.
def test_critic_r2_owed_sibling_blocks_healthy_recovery():
    tmp = _tmp()
    owed_400 = {"habit_key": VBC, "dormancy_ticks": 400, "baseline": 0.68,
                "demoted_tick": 400}
    _seed_sidecar(
        tmp, **_entry(CQ, 0.72, 7),
        **_entry(VBC, 0.68, 0, demoted=False, demoted_tick=None,
                 owed=owed_400))
    path = tmp / "authored-dormancy.json"
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72, VBC: 0.68})
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0),
            VBC: Habit(VBC, "uncertainty", Action.OBSERVE, 0.64, 3, 0)}
    records = [_note_record(CQ, 0.72, 728, 735),
               _note_record(VBC, 0.68, 399, 399)]
    before = path.read_bytes()
    data_before = json.loads(json.dumps(tracker.data))
    with pytest.raises(ValueError):
        tracker.recover_lost_latches(
            tick=736, state_habits=live, records=records)
    assert path.read_bytes() == before  # sidecar bytes untouched
    assert tracker.data == data_before  # no in-memory mutation either
    assert tracker.data["habits"][CQ]["demoted"] is False, (
        "the healthy sibling's latch was half-restored before the "
        "owed sibling refused")
    assert tracker.data["habits"][VBC]["owed"] == owed_400
