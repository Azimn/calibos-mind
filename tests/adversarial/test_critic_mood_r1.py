"""Critic round 1 for slow affective residue (Domain 10, 2026-11-03).

Finding: `_live_mood` (subject.py) synthesizes fast=0.0 ("settled") for
residue keys the engine no longer tracks (keys in neither
`pressures` nor `pressure_baselines`). That defeats the pure function's
own fail-closed rule (mood.py docstring: "a pressure with no fast
deviation on record cannot satisfy the fast-settled clause, so it
never fires") and lets a residue with no live engine corroboration —
including one that never had a genuine spike in this incarnation, the
spec's phantom-residue revert trigger — fire for ~175 ticks while it
decays. Decay itself works (no freeze); the defect is firing during
the decay window on manufactured evidence.

Fix direction (builder's call): `_live_mood` should emit no fast entry
for keys in neither pressures nor baselines, so the pure function fails
closed for them; `_run_tick` decay of such keys is unchanged.

All fixtures on /tmp — the live store is never touched.
"""
from __future__ import annotations

import io
import sys
import tempfile
import types
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind.mood import mood_residue_boost_for
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject
from digital_subject.cartridge import load_cartridge


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-mood-r1-"))


def _make_subject(tmp: Path, mood_path=None) -> CalibosSubject:
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    cart = load_cartridge(cli.CARTRIDGE_PATH)
    return CalibosSubject(
        str(tmp / "mind.db"), cart,
        cognition=InboxCognition(inbox),
        salience_path=str(tmp / "salience.json"),
        mood_path=(str(mood_path) if mood_path is not None else None),
    )


def _stub(concepts):
    r = types.SimpleNamespace()
    r.first_person = "x."
    r.concepts = concepts
    return r


def test_critic_vanished_pressure_must_not_fire():
    """A residue key the engine no longer tracks has no fast deviation
    on record, so the settled clause must fail closed (0.0)."""
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    tracker = subject.workspace.mood_tracker
    tracker.data["residues"]["attachment"] = 0.5
    tracker.save()
    with subject._transaction():
        st = subject.engine.state
        st.pressures = {"trust": 0.0}
        st.pressure_baselines = {"trust": 0.0}
        st.concerns = {}
    residues, fast = subject.workspace.mood_provider()
    assert residues["attachment"] == 0.5
    assert mood_residue_boost_for(
        _stub(("attachment",)), residues, fast) == 0.0


def test_critic_vanished_pressure_record_not_admitted():
    """View level: the vanished-pressure record must stay below the cut.

    CORRECTED in critic round 2 (test-premise drift, genome rule): as
    first filed, this test seeded only 7 view ids (1 pinned + 4 thoughts
    + target + control) against an unfilled window, so the target was
    admitted even with the gain disabled entirely — the test could not
    distinguish the gain, and its failure was a premise error, not a
    code defect (verified by a gain-off baseline reproducing the exact
    same 7-id window). The correction fills the memory class cap (4)
    with competitor memories at importance 1.0, so a +0.5 boost would
    admit the 0.9 target and no boost cuts it: vanished and gain-off
    both cut it, a legitimate settled residue admits it (measured
    2026-11-03 in the round-2 probe)."""
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    with subject._transaction():
        tgt = subject._add("memory", "A quiet note about the garden gate.",
                           concepts=("attachment",)).id
        ctl = subject._add(
            "memory", "A postcard arrived with no message, only a stamp.").id
        fids = [subject._add(
            "thought", f"Filler thought number {i} about chess and rain.").id
            for i in range(15)]
        mems = [subject._add(
            "memory", f"Extra memory number {i} about stones.").id
            for i in range(4)]
    tracker = subject.workspace.mood_tracker
    tracker.data["residues"]["attachment"] = 0.5
    tracker.save()
    with subject._transaction():
        st = subject.engine.state
        st.pressures = {"trust": 0.0}
        st.pressure_baselines = {"trust": 0.0}
        st.concerns = {}
    ws = subject.workspace
    sal = ws.salience_tracker
    for fid in fids:
        sal.add_importance(fid, 0, 1.0)
    for mem in mems:
        sal.add_importance(mem, 0, 1.0)
    sal.add_importance(tgt, 0, 0.9)
    sal.add_importance(ctl, 0, 0.9)
    ws.view()
    assert tgt not in set(ws._last_view_ids)


def test_critic_vanished_pressure_never_fires_while_decaying():
    """Across 200 waking ticks after the pressure vanishes, the gain
    must never fire for it, even though the residue is still decaying."""
    tmp = _tmp()
    subject = _make_subject(tmp, mood_path=tmp / "mood.json")
    tracker = subject.workspace.mood_tracker
    tracker.data["residues"]["attachment"] = 0.5
    tracker.save()
    fired = 0
    for _ in range(200):
        with subject._transaction():
            st = subject.engine.state
            st.pressures = {"trust": 0.0}
            st.pressure_baselines = {"trust": 0.0}
            st.concerns = {}
        with redirect_stdout(io.StringIO()):
            cli._run_tick(subject)
        residues, fast = subject.workspace.mood_provider()
        if mood_residue_boost_for(
                _stub(("attachment",)), residues, fast) > 0:
            fired += 1
    assert fired == 0, f"vanished pressure fired on {fired}/200 ticks"
