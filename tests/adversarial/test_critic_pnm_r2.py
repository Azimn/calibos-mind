"""CRITIC tests, round 2, for the persistent-near-miss-note mutation
(spec: research/specs/2026-10-28-persistent-near-miss-note.md).

Round-2 scope: the view() near-miss exclusion in workspace.py, and the
re-verification of the two round-1 failures. All fixtures live in /tmp —
the live store is never touched.

Round-2 finding (see test_long_run_corrected_premise): the round-1
long-run test's global <=5-notes-per-100-ticks cap contradicts the
spec's per-record-per-episode mechanism when the fixture synchronizes
~10 genuine episodes at run start (measured round 2: 12 notes, 0
cascade, every record noted exactly once at streak 10). The corrected
premise asserted here is per-episode uniqueness + zero cascade +
steady-state rate after the seeding transient, not a global cap over a
synchronized burst. The round-1 test file itself is NOT edited by this
file; correcting or retiring that assertion is the design lead's call.
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind.familiarity import (
    is_near_miss_note_text,
    near_miss_note_text,
    parse_near_miss_note_text,
)
from calibos_mind.subject import CalibosSubject
from calibos_mind.provider import InboxCognition
from digital_subject.cartridge import load_cartridge

# Fixture mirrors the round-1 battery (same seed/boost shape) so the
# numbers are comparable across rounds.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adversarial"))
from test_critic_pnm_r1 import (  # noqa: E402
    _boost,
    _full_subject,
    _lean_subject,
    _notes,
    _seed,
    _tmp,
)


def _max_window_from(mint_ticks, lo, hi, width=100):
    """Worst width-tick window fully inside [lo, hi)."""
    return max(
        sum(1 for tk in mint_ticks if s <= tk < s + width)
        for s in range(lo, hi - width + 1)
    ) if hi - lo >= width else max(
        sum(1 for tk in mint_ticks if lo <= tk < hi), default=0)


def test_long_run_corrected_premise():
    """200-tick full run, corrected premise: zero cascade, exactly one
    note per record episode at streak 10, and a steady-state rate (after
    the synchronized seeding transient) within signal level."""
    tmp = _tmp()
    saved_inbox = cli.INBOX
    cli.INBOX = tmp / "inbox"
    try:
        subject = _full_subject(tmp)
        ids = _seed(subject)
        _boost(subject, ids)
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(200):
                cli._run_tick(subject)
    finally:
        cli.INBOX = saved_inbox
    notes = _notes(subject)
    note_ids = {r.id for r in notes}
    cascade = [r for r in notes
               if parse_near_miss_note_text(r.first_person)["record_id"]
               in note_ids]
    assert cascade == [], [r.first_person for r in cascade]
    per_record = {}
    for r in notes:
        parsed = parse_near_miss_note_text(r.first_person)
        per_record.setdefault(parsed["record_id"], []).append(parsed)
    for rid, ps in per_record.items():
        assert len(ps) == 1, (rid, ps)
        assert ps[0]["streak"] == 10, (rid, ps)
        assert rid not in note_ids, rid
    # Genuine target still notes exactly once (round-1 requirement).
    assert len(per_record.get(ids["T0"], [])) == 1
    # Steady state: worst 100-tick window inside ticks 50-200.
    ticks = [p["tick"] for ps in per_record.values() for p in ps]
    steady = _max_window_from(ticks, 50, 200)
    assert steady <= 5, f"steady-state worst window {steady}"


def test_legacy_note_text_record_never_builds_a_streak():
    """Round-2 claim: identification is by canonical pattern, so a note
    minted BEFORE the fix is excluded too. Hand-place a canonical note
    text as an ordinary record, pin it below the view cut, run 15 waking
    ticks: it must hold no streak and mint no note about itself."""
    tmp = _tmp()
    subject = _lean_subject(tmp)
    ids = _seed(subject)
    legacy_text = near_miss_note_text(
        {"record_id": "experience-999", "streak": 10, "tick": 3})
    with subject._transaction():
        legacy_id = subject._add("temporal", legacy_text,
                                 generated_by="cognition").id
    assert is_near_miss_note_text(legacy_text)
    _boost(subject, ids)
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(15):
            cli._run_tick(subject)
    import json
    sidecar = json.loads((tmp / "familiarity.json").read_text())
    assert legacy_id not in sidecar["streaks"], sidecar["streaks"]
    notes = _notes(subject)
    about_legacy = [r for r in notes
                    if parse_near_miss_note_text(r.first_person)["record_id"]
                    == legacy_id]
    assert about_legacy == [], [r.first_person for r in about_legacy]
    # And the genuine target still notes exactly once in the same run.
    target_notes = [r for r in notes
                    if parse_near_miss_note_text(r.first_person)["record_id"]
                    == ids["T0"]]
    assert len(target_notes) == 1, [r.first_person for r in target_notes]
