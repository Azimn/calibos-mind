"""Critic round 1 — attack tests for realization follow-through surfacing.

Spec: research/specs/2026-11-20-followthrough-surfacing.md
Brief: only the spec + the diff. Every objection below arrives as a
failing test (or a test that documents the behaviour it probes).
All fixtures on /tmp — the live store is never touched.

Attack angles:
  A. Fail-loud loader: an explicit JSON null for "log" or "surfaced"
     must raise like every other wrong-typed field (personality.py
     precedent: an explicit null lifetime/window raises; a missing key
     may default). The new loader silently empties both.
  B. Full action vocabulary: the summary parser's [\\w-]+ action
     patterns must round-trip a row whose mixes span all 15 engine
     actions — the writer emits whatever the log holds, and a minted
     record the parser cannot read breaks mint idempotence on crash
     recovery (the idempotence check scans by parse).
  C. The 256-row log-cap evaporation case, driven through the real
     observe() eviction (not a hand-shortened log): an owed mark whose
     windows evict must be dropped without minting, and the drop must
     persist to disk; conversely, an owed mark whose windows survive
     intervening ticks must still mint exactly once on recovery.
  D. Read-only completeness: predictions and goals (named read-only
     in the module docstring alongside drift/status/review/inbox/
     followthrough) must also leave followthrough.json byte-identical
     with a measurable row waiting.
  E. Probe (spec-ambiguous, xfail, does not drive the verdict): two
     hand-authored realization records sharing one (need, swing,
     conv) identity but different bands — report() lists two rows;
     the identity-keyed surfacing collapses them to one summary, so
     the second row's bands are never rendered. Engine-unreachable
     (mint_realization is idempotent on (need, swing_tick)), and the
     spec itself defines identity as (need, swing, conv), so this is
     flagged for design-lead adjudication, not as a builder defect.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.followthrough import (
    FollowthroughTracker,
    followthrough_summary_text,
    is_followthrough_summary_text,
    parse_followthrough_summary_text,
    report,
    surface_newly_measurable,
)
from calibos_mind.interoception import mint_realization, realization_text
from digital_subject.models import Action

ALL_ACTIONS = [a.value for a in Action]


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-followthrough-surfacing-r1-"))


def _needs(**over):
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


def _init_store():
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _mint(subject, need, swing, conv):
    event = {"need": need, "swing_tick": swing, "swing_felt": 0.9,
             "swing_actual": 0.5, "gap_max": 0.4, "conv_tick": conv}
    with subject._transaction():
        return mint_realization(subject, event)


def _fill(start, end, action):
    return [[t, action] for t in range(start, end + 1)]


def _measurable_rows():
    return (_fill(1, 20, "seek_contact") + _fill(21, 40, "seek_contact")
            + _fill(41, 60, "approach"))


def _measurable_setup(need="thirst", swing=21, conv=40):
    subject = _init_store()
    _mint(subject, need, swing=swing, conv=conv)
    cli.FOLLOWTHROUGH.write_text(json.dumps(_measurable_rows()),
                                 encoding="utf-8")
    return subject


def _summaries(subject):
    return [r for r in subject.workspace.records
            if r.source == "temporal" and r.generated_by == "cognition"
            and is_followthrough_summary_text(r.first_person)]


# -- A. fail-loud loader: explicit null is corruption, not absence ----------

def test_critic_null_log_field_must_fail_loud():
    """{"log": null} is a corrupt sidecar, not an empty log: the
    loader must raise, never silently discard 81 rows of conduct.
    personality.py raises on an explicit null lifetime/window; the
    follow-through loader must match — only a *missing* key may
    default, an explicit null is a wrong type."""
    tmp = _tmp()
    path = tmp / "followthrough.json"
    path.write_text(json.dumps({"log": None, "surfaced": []}),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)


def test_critic_null_surfaced_field_must_fail_loud():
    """{"surfaced": null} is a corrupt sidecar, not an empty surfaced
    set: the loader must raise, never silently forget which
    realizations were already surfaced (a silent empty set would
    re-mint summaries for every measurable row)."""
    tmp = _tmp()
    path = tmp / "followthrough.json"
    path.write_text(json.dumps({"log": [], "surfaced": None}),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)


# -- B. full action vocabulary round-trips -----------------------------------

def test_critic_all_engine_actions_round_trip():
    """A row whose mixes span all 15 engine actions round-trips
    through the canonical text and its parser, with tv matching
    report() exactly. If any engine action could not be written and
    re-read, a minted summary would be invisible to the idempotent
    mint check and would duplicate on crash recovery."""
    assert len(ALL_ACTIONS) == 15, ALL_ACTIONS
    subject = _init_store()
    _mint(subject, "thirst", swing=21, conv=40)
    # Before window (ticks 1-20): 15 actions, 5 of them twice.
    # After window (ticks 41-60): a different 15-action spread.
    before_actions = (ALL_ACTIONS + ALL_ACTIONS[:5])
    after_actions = (ALL_ACTIONS[5:] + ALL_ACTIONS[:5]
                     + ALL_ACTIONS[10:15])
    assert len(before_actions) == 20 and len(after_actions) == 20
    rows = ([[t, a] for t, a in zip(range(1, 21), before_actions)]
            + _fill(21, 40, "explore")
            + [[t, a] for t, a in zip(range(41, 61), after_actions)])
    cli.FOLLOWTHROUGH.write_text(json.dumps(rows), encoding="utf-8")
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    row = rep["rows"][0]
    assert row["status"] == "measured", row
    assert set(row["before_mix"]) | set(row["after_mix"]) == set(ALL_ACTIONS)
    text = followthrough_summary_text(row)
    parsed = parse_followthrough_summary_text(text)
    assert parsed is not None, text
    assert parsed["before_mix"] == pytest.approx(row["before_mix"])
    assert parsed["after_mix"] == pytest.approx(row["after_mix"])
    assert parsed["tv"] == pytest.approx(row["tv"])
    assert followthrough_summary_text(parsed) == text
    events = surface_newly_measurable(subject, tracker, rep)
    assert len(events) == 1
    assert len(_summaries(subject)) == 1
    # Crash recovery still finds it: the mint check parses the record.
    assert surface_newly_measurable(subject, tracker) == []
    assert len(_summaries(subject)) == 1


# -- C. 256-row cap evaporation, driven through real observe() ---------------

def test_critic_stale_owed_dropped_after_real_log_cap_eviction():
    """The flagged evaporation case, end to end: an owed mark is
    written for a measurable row; 240 further waking observations
    push the log past its 256-row cap and evict the row's before
    window; the next surface drops the owed mark WITHOUT minting —
    no fabrication from stale numbers — and the drop persists to
    disk (a second surface still mints nothing)."""
    subject = _measurable_setup()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    events, changed = tracker.due_summaries(rep)
    assert len(events) == 1 and changed
    tracker.save()  # write-ahead mark durable; the mint never ran
    assert tracker.surfaced[0]["owed"] is True
    # 240 more observations: log grows 60 -> 300 rows, capped at 256,
    # so the surviving log starts at tick 45 and the before window
    # (last 20 rows < swing 21) is gone.
    for tick in range(61, 301):
        assert tracker.observe(tick, "approach") is True
    tracker.save()
    assert len(tracker.rows) == 256
    assert tracker.rows[0][0] == 45, tracker.rows[0]
    rep2 = report(subject, tracker)
    assert rep2["rows"][0]["status"] == "insufficient-window", rep2
    assert surface_newly_measurable(subject, tracker, rep2) == []
    assert _summaries(subject) == []
    assert tracker.surfaced == []
    on_disk = json.loads(cli.FOLLOWTHROUGH.read_text(encoding="utf-8"))
    assert on_disk["surfaced"] == [], on_disk["surfaced"]
    # The drop is durable: a second surface still mints nothing.
    assert surface_newly_measurable(subject, tracker) == []
    assert _summaries(subject) == []


def test_critic_owed_survives_intervening_ticks_then_mints_once():
    """The complement: an owed mark with ten intervening observations
    (windows intact — appending after conv cannot move the first-20
    after window or the last-20 before window) still mints exactly
    one summary on recovery, with the measured numbers."""
    subject = _measurable_setup()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    events, changed = tracker.due_summaries(rep)
    assert len(events) == 1 and changed
    tracker.save()
    for tick in range(61, 71):
        tracker.observe(tick, "approach")
    tracker.save()
    recovered = FollowthroughTracker(cli.FOLLOWTHROUGH)
    assert recovered.surfaced[0]["owed"] is True
    minted = surface_newly_measurable(subject, recovered)
    assert len(minted) == 1
    assert len(_summaries(subject)) == 1
    parsed = parse_followthrough_summary_text(
        _summaries(subject)[0].first_person)
    assert parsed is not None
    assert (parsed["swing_tick"], parsed["conv_tick"],
            parsed["tv"]) == (21, 40, 1.0)
    assert recovered.surfaced[0]["owed"] is False
    assert surface_newly_measurable(subject, recovered) == []
    assert len(_summaries(subject)) == 1


# -- D. read-only completeness: predictions + goals ----------------------------

def test_critic_readonly_predictions_and_goals_leave_sidecar_byte_identical():
    """predictions and goals are named read-only in the module
    docstring alongside the swept commands; with a measurable row
    waiting, neither may mint nor touch followthrough.json."""
    subject = _measurable_setup()
    sidecar = cli.FOLLOWTHROUGH
    before = sidecar.read_bytes()
    cli.cmd_predictions(argparse.Namespace())
    cli.cmd_goals(argparse.Namespace())
    assert sidecar.read_bytes() == before, \
        "a read-only command wrote the sidecar"
    fresh = cli._subject()
    assert _summaries(fresh) == [], \
        "a read-only command minted a summary"


# -- E. probe: duplicate-identity realizations (spec-ambiguous) ----------------

@pytest.mark.xfail(strict=False, reason=(
    "spec-ambiguous probe: report() lists two rows for two records "
    "sharing one (need, swing, conv) identity, but the spec defines "
    "identity as that triple and the mint as idempotent on it, so the "
    "builder's collapse to one summary follows the spec's own "
    "definition; engine-unreachable (mint_realization is idempotent "
    "on (need, swing_tick)). For design-lead adjudication, not a "
    "builder defect."))
def test_critic_probe_duplicate_identity_realizations_mint_two():
    """Two hand-authored realization records, same (need, swing,
    conv), different bands: report() lists two rows. Spec-literal
    reading ('one summary per newly-measurable row') expects two
    summaries; the identity-keyed design mints one."""
    subject = _init_store()
    for felt, actual in ((0.9, 0.5), (0.3, 0.1)):
        event = {"need": "thirst", "swing_tick": 21, "swing_felt": felt,
                 "swing_actual": actual, "gap_max": 0.4, "conv_tick": 40}
        with subject._transaction():
            subject._add("temporal", realization_text(event),
                         concepts=("thirst",), generated_by="cognition",
                         available_to_cognition=True,
                         salience=0.5, intensity=0.5)
    cli.FOLLOWTHROUGH.write_text(json.dumps(_measurable_rows()),
                                 encoding="utf-8")
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    assert len(rep["rows"]) == 2, rep
    assert all(r["status"] == "measured" for r in rep["rows"])
    events = surface_newly_measurable(subject, tracker, rep)
    assert len(events) == 2, events
    assert len(_summaries(subject)) == 2
