"""Critic round 2 — attack tests for the round-1 fix (fail-loud loader).

Spec: research/specs/2026-11-20-followthrough-surfacing.md
Brief: only the spec + the diff. Round 1 FAILED on two counts: an
explicit JSON null for "log" and for "surfaced" loaded silently. The
builder claims to have fixed both in calibos_mind/followthrough.py
without touching the round-1 tests.

This file attacks the FIX itself:
  A. Missing-vs-null distinction, pinned both ways: a missing "log"
     key must still raise (it is required), a missing "surfaced" key
     is the one sanctioned default (empty set, migrates on save).
  B. Every other wrong-typed shape the validators might still
     swallow: top-level null/number/string, non-list "log"/"surfaced",
     and each malformed surfaced-entry field (missing need/ticks/owed,
     non-bool owed, duplicate identity, non-dict entry, negative /
     float / bool ticks, empty need, null log row, bool/float log tick).
  C. Crash window 2, end to end through the real path: owed mark
     durable, mint commits, ack never lands (kill simulated between
     mint and ack) — recovery must mint nothing new and clear the mark.

All fixtures on /tmp — the live store is never touched.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from calibos_mind.followthrough import (
    FollowthroughTracker,
    mint_followthrough_summary,
    parse_followthrough_summary_text,
    report,
    surface_newly_measurable,
)
from calibos_mind.interoception import mint_realization

import calibos_mind.cli as cli


def _tmp():
    return Path(tempfile.mkdtemp(prefix="critic-followthrough-surfacing-r2-"))


def _write(path, body):
    path.write_text(json.dumps(body), encoding="utf-8")


def _good_entry(**over):
    e = {"need": "thirst", "swing_tick": 21, "conv_tick": 40, "owed": False}
    e.update(over)
    return e


# -- A. missing-vs-null, pinned both ways -----------------------------------

def test_critic_missing_log_key_still_raises():
    """The "log" key is required: a dict object without it is
    corrupt, not an empty log. Only a *missing* "surfaced" key may
    default — the missing/null line must not be drawn the other way
    round for "log"."""
    tmp = _tmp()
    path = tmp / "followthrough.json"
    _write(path, {"surfaced": []})
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)


def test_critic_missing_surfaced_key_defaults_to_empty_and_migrates():
    """The one sanctioned default: a dict object with no "surfaced"
    key loads with an empty surfaced set (the pre-surfacing dict
    shape), and the next save migrates it to the full shape."""
    tmp = _tmp()
    path = tmp / "followthrough.json"
    _write(path, {"log": [[1, "approach"]]})
    tracker = FollowthroughTracker(path)
    assert tracker.surfaced == []
    assert tracker.rows == [[1, "approach"]]
    tracker.save()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == {"log": [[1, "approach"]], "surfaced": []}


def test_critic_top_level_null_number_string_raise():
    """A top-level JSON null, number, or string is corrupt — the
    loader must not coerce any of them into an empty log."""
    tmp = _tmp()
    for body in ("null", "42", '"thirst"'):
        path = tmp / "followthrough.json"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt followthrough"):
            FollowthroughTracker(path)


# -- B. every other wrong-typed shape ---------------------------------------

@pytest.mark.parametrize("bad_log", [
    {}, "approach", 42, True,
])
def test_critic_non_list_log_raises(bad_log):
    tmp = _tmp()
    path = tmp / "followthrough.json"
    _write(path, {"log": bad_log, "surfaced": []})
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)


@pytest.mark.parametrize("bad_surfaced", [
    {}, "thirst", 42, True,
])
def test_critic_non_list_surfaced_raises(bad_surfaced):
    tmp = _tmp()
    path = tmp / "followthrough.json"
    _write(path, {"log": [], "surfaced": bad_surfaced})
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)


@pytest.mark.parametrize("bad_row", [
    None, [True, "approach"], [1.5, "approach"], ["1", "approach"],
    [1], [1, "approach", "extra"], "approach", 42,
])
def test_critic_malformed_log_row_raises(bad_row):
    tmp = _tmp()
    path = tmp / "followthrough.json"
    _write(path, {"log": [bad_row], "surfaced": []})
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)


@pytest.mark.parametrize("bad_entry", [
    "thirst",
    42,
    None,
    {"swing_tick": 21, "conv_tick": 40, "owed": False},  # no need
    {"need": "", "swing_tick": 21, "conv_tick": 40, "owed": False},
    {"need": 42, "swing_tick": 21, "conv_tick": 40, "owed": False},
    {"need": "thirst", "conv_tick": 40, "owed": False},  # no swing
    {"need": "thirst", "swing_tick": 21, "owed": False},  # no conv
    {"need": "thirst", "swing_tick": 21, "conv_tick": 40},  # no owed
    {"need": "thirst", "swing_tick": 21, "conv_tick": 40, "owed": 1},
    {"need": "thirst", "swing_tick": 21, "conv_tick": 40, "owed": "true"},
    {"need": "thirst", "swing_tick": 21, "conv_tick": 40, "owed": None},
    {"need": "thirst", "swing_tick": -1, "conv_tick": 40, "owed": False},
    {"need": "thirst", "swing_tick": 21, "conv_tick": -2, "owed": False},
    {"need": "thirst", "swing_tick": 21.5, "conv_tick": 40, "owed": False},
    {"need": "thirst", "swing_tick": True, "conv_tick": 40, "owed": False},
    {"need": "thirst", "swing_tick": 21, "conv_tick": 40.0, "owed": False},
])
def test_critic_malformed_surfaced_entry_raises(bad_entry):
    tmp = _tmp()
    path = tmp / "followthrough.json"
    _write(path, {"log": [], "surfaced": [bad_entry]})
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)


def test_critic_duplicate_surfaced_identity_raises():
    """Two entries for one (need, swing, conv) identity mean the
    writer or a hand edit broke the set invariant; the loader must
    fail loud rather than silently dedupe."""
    tmp = _tmp()
    path = tmp / "followthrough.json"
    _write(path, {"log": [], "surfaced": [_good_entry(),
                                           _good_entry(owed=True)]})
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)


def test_critic_nulls_inside_log_and_surfaced_lists_raise():
    """Nulls nested inside the lists are corruption too, not empty
    rows/entries."""
    tmp = _tmp()
    path = tmp / "followthrough.json"
    _write(path, {"log": [None], "surfaced": []})
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)
    _write(path, {"log": [], "surfaced": [None]})
    with pytest.raises(ValueError, match="corrupt followthrough"):
        FollowthroughTracker(path)


# -- C. crash window 2 through the real path --------------------------------

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
    import argparse
    cli.cmd_init(argparse.Namespace(force=False))
    return cli._subject()


def _measurable_setup():
    subject = _init_store()
    event = {"need": "thirst", "swing_tick": 21, "swing_felt": 0.9,
             "swing_actual": 0.5, "gap_max": 0.4, "conv_tick": 40}
    with subject._transaction():
        mint_realization(subject, event)
    rows = ([[t, "seek_contact"] for t in range(1, 41)]
            + [[t, "approach"] for t in range(41, 61)])
    cli.FOLLOWTHROUGH.write_text(json.dumps(rows), encoding="utf-8")
    return subject


def _summaries(subject):
    from calibos_mind.followthrough import is_followthrough_summary_text
    return [r for r in subject.workspace.records
            if r.source == "temporal" and r.generated_by == "cognition"
            and is_followthrough_summary_text(r.first_person)]


def test_critic_crash_between_mint_and_ack_recovers_with_no_duplicate():
    """Kill simulated between the mint commit and the ack save: the
    owed mark is durable, the summary record is durable, the ack
    never landed. Recovery (fresh tracker, fresh surface) must mint
    nothing new, return the existing record, and clear the mark —
    exactly one summary in the store afterwards."""
    subject = _measurable_setup()
    tracker = FollowthroughTracker(cli.FOLLOWTHROUGH)
    rep = report(subject, tracker)
    events, changed = tracker.due_summaries(rep)
    assert len(events) == 1 and changed
    tracker.save()  # write-ahead mark durable; mint never ran yet
    # The mint runs and commits (its own transaction), then the
    # process dies before ack_summaries + save.
    with subject._transaction():
        minted_id = mint_followthrough_summary(subject, events[0])
    assert len(_summaries(subject)) == 1
    # Recovery: a fresh tracker reloads the owed mark from disk.
    recovered = FollowthroughTracker(cli.FOLLOWTHROUGH)
    assert recovered.surfaced[0]["owed"] is True
    # The owed mark re-emits its row, the mint finds the existing
    # record and appends nothing.
    assert len(surface_newly_measurable(subject, recovered)) == 1
    assert len(_summaries(subject)) == 1, \
        "recovery minted a duplicate summary"
    assert _summaries(subject)[0].id == minted_id
    assert recovered.surfaced[0]["owed"] is False
    assert surface_newly_measurable(subject, recovered) == []
    assert len(_summaries(subject)) == 1
