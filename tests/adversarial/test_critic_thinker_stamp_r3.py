"""Critic round 3 attack tests for the thinker-identity stamp (Domain 23).

Spec: research/specs/2026-11-16-thinker-identity-stamp.md
Round-2 FAIL was two defects; the builder claims both fixed (all 19
round-1 + round-2 attack tests pass). This file attacks the FIXES for
new holes. Every test here is an objection stated as a failing test or
a concretely violated invariant. All fixtures live in /tmp — the live
store is never touched (the autouse conftest fixture redirects every
cli store path at a fresh tmp tree).
"""
from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from calibos_mind import cli
from calibos_mind.workspace import CalibosWorkspace


def _capture(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


def _think(text, *extra):
    return _capture("think", *extra, text)


# --- Attack 1: JSON-quoting must be injective for EVERY valid label -----
#
# Round 2, attack 1: the disambiguator "unstamped (thinker label)" was
# itself a valid label, so two labels printed byte-identical lines. The
# round-3 fix prints every label JSON-quoted. json.dumps is injective on
# strings, so distinct labels must print distinct lines — including
# labels that contain quotes, backslashes, the reserved word, or the
# reserved word followed by a colon and a count. The reserved line must
# appear exactly once as a FULL line (substring counting is not the
# contract: a label may legitimately contain the text "unstamped:").

ADVERSARIAL_LABELS = [
    "unstamped",
    '"unstamped"',              # the reserved word, quoted
    "unstamped (thinker label)",
    "a\\b",                     # backslash
    'x": 5',                    # quote-colon-count forgery attempt
    "unstamped: 99",           # reserved word + colon + count, as text
    "foo unstamped: 3",
    '"',                        # a lone quote
    'a"b\\c',
]


def test_json_quoted_labels_are_injective_for_adversarial_labels():
    for i, label in enumerate(ADVERSARIAL_LABELS):
        rc, out = _think(f"a thought from host {i}", "--thinker", label)
        assert rc == 0, f"label {label!r} refused: {out!r}"
    rc, out = _capture("thinkers")
    assert rc == 0
    label_lines = [l for l in out.splitlines()
                   if l.startswith('  "')]
    assert len(label_lines) == len(set(label_lines)), (
        "two distinct labels printed identical report lines:\n" + out)
    assert len(label_lines) == len(ADVERSARIAL_LABELS), (
        f"expected one quoted line per label, got {len(label_lines)}:\n"
        + out)
    for label in ADVERSARIAL_LABELS:
        assert f"  {json.dumps(label)}: 1" in out, (
            f"label {label!r} did not print as its JSON-quoted line:\n"
            + out)
    reserved = [l for l in out.splitlines()
                if re.fullmatch(r"  unstamped: \d+", l)]
    assert len(reserved) == 1, (
        "reserved legacy-count line must appear exactly once as a full "
        f"line, got {len(reserved)}:\n" + out)
    assert reserved[0] == "  unstamped: 0"


# --- Attack 2: from_dict re-admits stamps on non-thinker-authored
# THOUGHT records ---------------------------------------------------------
#
# Round 2, attack 2: from_dict re-admitted stamps on non-thought
# records. The round-3 fix enforces source == "thought" at the load
# boundary. But source is not the whole authorship test: the frozen
# engine itself authors source "thought" records that never pass
# through inject_thought — dream-derived echoes (subject._add stamps
# them generated_by="dream-derived" and pops any thinker label
# fail-closed) and echo resurfacing (endogenous.py adds the thought
# with generated_by=<parent record id>). A persisted payload carrying
# a stamp on such a record (foreign payload, or a hand-edited store)
# loads with the stamp in the map and to_dict() writes it straight
# back out. The spec's revert signal is absolute — "A stamp appears
# on any record not authored through inject_thought" — and its fitness
# item is explicit: "Dream ticks never stamp: no dream-path record
# carries a thinker field." The write boundary already expresses the
# rule (_add pops the stamp on the dream path); the load boundary
# contradicts it, so in-memory, persisted, and loaded state disagree
# about the same record — the same defect class as round 1, attack 5.
# No legitimate stamped record can carry these generated_by values:
# inject_thought's callers in this codebase pass only "cognition"
# (the default), "voluntary", "voluntary:inspection", "wake", or
# "answered:*" / "answered-external:*".

def test_from_dict_drops_stamp_on_non_inject_thought_records():
    data = {"records": [
        {"id": "experience-1", "tick": 0, "source": "thought",
         "first_person": "a dream echo", "generated_by": "dream-derived",
         "thinker": "model-A"},
        {"id": "experience-2", "tick": 1, "source": "thought",
         "first_person": "a resurfaced echo",
         "generated_by": "experience-0", "thinker": "model-A"},
        {"id": "experience-3", "tick": 2, "source": "thought",
         "first_person": "an engine thought with no origin",
         "generated_by": None, "thinker": "model-A"},
        {"id": "experience-4", "tick": 3, "source": "thought",
         "first_person": "a genuine thought", "generated_by": "voluntary",
         "thinker": "model-A"},
    ], "sequence": 4}
    ws = CalibosWorkspace.from_dict(data)
    assert ws._thinker_stamps == {"experience-4": "model-A"}, (
        f"stamp re-entered the map on a non-thinker-authored thought "
        f"record: {ws._thinker_stamps}")
    stamped = [r for r in ws.to_dict()["records"] if "thinker" in r]
    assert [r["id"] for r in stamped] == ["experience-4"], (
        f"stamp persisted on non-thinker-authored thought records after "
        f"load: {[(r['id'], r.get('generated_by')) for r in stamped]}")
    # And the round-trip must agree with the loaded state.
    ws2 = CalibosWorkspace.from_dict(ws.to_dict())
    assert ws2._thinker_stamps == ws._thinker_stamps
    assert ws.to_dict() == ws2.to_dict()


# --- Verification (should pass; recorded so the check is explicit) -------
#
# The round-3 source check covers every source the frozen engine can
# produce (SOURCES in jelly_psiduck/workspace.py: perception,
# interoception, memory, thought, imagination, social, temporal,
# action_consequence). A stamp on each non-thought source is dropped
# at the load boundary.

def test_from_dict_drops_stamp_on_every_non_thought_engine_source():
    from jelly_psiduck.workspace import SOURCES
    records = []
    for i, source in enumerate(sorted(SOURCES)):
        if source == "thought":
            continue
        records.append({"id": f"experience-{i}", "tick": i,
                        "source": source, "first_person": f"a {source}",
                        "generated_by": "cartridge", "thinker": "model-A"})
    records.append({"id": "experience-99", "tick": 99, "source": "thought",
                    "first_person": "a genuine thought",
                    "generated_by": "voluntary", "thinker": "model-A"})
    ws = CalibosWorkspace.from_dict({"records": records, "sequence": 100})
    assert ws._thinker_stamps == {"experience-99": "model-A"}, (
        f"stamp survived on a non-thought engine source: "
        f"{ws._thinker_stamps}")
    stamped = [r for r in ws.to_dict()["records"] if "thinker" in r]
    assert [r["source"] for r in stamped] == ["thought"]


# A stamp on a thought with generated_by="cognition" must still load:
# that is inject_thought's default, so the load boundary cannot tell it
# apart from a heartbeat cognition thought and must admit it — dropping
# it here would over-enforce and break legitimate API-authored stamps.

def test_from_dict_keeps_stamp_on_cognition_generated_by_thought():
    subject = cli._subject()
    tid = subject.inject_thought("a thought via the default origin",
                                 thinker="model-A")
    recs = cli._subject().inspect()["workspace"]["records"]
    match = next(r for r in recs if r["id"] == tid)
    assert match["generated_by"] == "cognition"
    assert match["thinker"] == "model-A"
