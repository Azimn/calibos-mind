"""Critic round 1 attacks on lost-latch recovery (domain 28, 2026-11-21).

Spec: research/specs/2026-11-21-dormancy-latch-recovery.md.
Target: calibos_mind/authored_dormancy.py::recover_lost_latches,
wired at the top of calibos_mind/cli.py::_run_tick.

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
    return Path(tempfile.mkdtemp(prefix="critic-dormancy-latch-r1-"))


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
    return SimpleNamespace(first_person=demotion_note_text(
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


# -- FAIL 1: recovery silently discards a pending owed mint ----------------
# Invariant violated (module's own write-ahead discipline, stated in the
# module docstring and in ack_demotion_notes): an owed mark is cleared
# ONLY after its mint commits; observe_tick re-emits an owed episode
# "from a tick whose mint never committed" so the note gets minted.
# recover_lost_latches sets owed=None unconditionally when it latches,
# so a pending owed episode whose note was never minted is silently
# dropped: observe_tick never re-emits it, no code ever mints it, and
# the audit trail permanently loses that episode.
#
# Premise, proved against measured behavior (genome: test-premise drift):
# the strict loader ACCEPTS a sidecar entry with demoted=False and a
# pending owed mark (it validates only the owed shape, never the
# demoted/owed consistency), and observe_tick on such an entry DOES
# re-emit the owed event before recovery runs. The state is reachable
# by any sidecar edit or future writer bug; recovery is the code that
# must not make it worse. Acceptable resolutions: recovery raises
# ValueError on a pending owed mark (fail loud, the module's philosophy),
# or preserves owed so observe_tick can still re-emit it.


def test_critic_recovery_never_discards_pending_owed_mint():
    tmp = _tmp()
    owed_400 = {"habit_key": CQ, "dormancy_ticks": 400, "baseline": 0.72,
                "demoted_tick": 400}
    _seed_sidecar(tmp, **_entry(CQ, 0.72, 0, demoted=False,
                                demoted_tick=None, owed=owed_400))
    path = tmp / "authored-dormancy.json"
    # The store holds a note for a DIFFERENT episode (tick 399), so
    # recovery's note check passes while the tick-400 episode's mint
    # never committed.
    records = [_note_record(CQ, 0.72, 399, 399)]
    live = {CQ: Habit(CQ, "curiosity", Action.EXPLORE, 0.64, 2, 0)}

    # Premise, measured: the loader accepts demoted=False + owed, and
    # observe_tick re-emits the owed episode before any recovery runs.
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72})
    events, _, _ = tracker.observe_tick(tick=401, state_habits=live)
    assert any(e["demoted_tick"] == 400 for e in events), (
        "premise: observe_tick must re-emit the pending owed episode")
    assert tracker.data["habits"][CQ]["owed"] == owed_400

    # The attack: recovery latches from the 399 note. It must either
    # refuse (ValueError) or leave the owed mark intact so the 400
    # episode still gets its mint. Silently clearing it loses the mint.
    tracker = AuthoredDormancyTracker(path, {CQ: 0.72})
    try:
        changed = tracker.recover_lost_latches(
            tick=401, state_habits=live, records=records)
    except ValueError:
        return  # fail-loud is an acceptable resolution
    assert changed is True
    entry = tracker.data["habits"][CQ]
    assert entry["demoted"] is True and entry["demoted_tick"] == 399
    # After recovery, the owed episode must still be recoverable:
    # observe_tick must re-emit it so its note gets minted.
    events, _, _ = tracker.observe_tick(tick=401, state_habits=live)
    assert any(e["demoted_tick"] == 400 for e in events), (
        "recovery cleared the pending owed mark for tick 400 without "
        "its mint having committed; observe_tick can no longer re-emit "
        "it and the episode's note will never be minted")


def test_critic_waking_tick_never_loses_owed_episode():
    # End-to-end form of FAIL 1 through cli._run_tick: a waking tick
    # with a lost latch plus a pending owed episode for a different
    # tick must not silently drop the owed episode's mint.
    tmp = _tmp()
    owed_400 = {"habit_key": CQ, "dormancy_ticks": 400, "baseline": 0.72,
                "demoted_tick": 400}
    _seed_sidecar(tmp, **_entry(CQ, 0.72, 0, demoted=False,
                                demoted_tick=None, owed=owed_400),
                  **_entry(VBC, 0.68, 399))
    subject = _make_subject(tmp)
    with subject._transaction():
        st = subject.engine.state
        st.tick = 400
        st.habits[CQ].strength = 0.64
        st.habits[VBC].last_used_tick = 400
        mint_demotion_note(subject, {"habit_key": CQ, "dormancy_ticks": 399,
                                     "baseline": 0.72, "demoted_tick": 399})
    assert len(_demotion_notes(subject)) == 1
    # A quiet waking tick: needs hunger-dominant so nothing fires.
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
    try:
        cli._run_tick(subject)
    except ValueError:
        return  # fail-loud is an acceptable resolution
    notes = _demotion_notes(subject)
    ticks = sorted(
        int(n.first_person.rsplit("at tick ", 1)[1].rstrip("."))
        for n in notes)
    assert 400 in ticks, (
        "the tick-400 owed episode's note was never minted and the "
        "sidecar no longer carries the owed mark; recovery dropped a "
        "pending mint obligation")
