"""Critic round 2 attack tests for the thinker-identity stamp (Domain 23).

Spec: research/specs/2026-11-16-thinker-identity-stamp.md
Round 1 FAIL was five defects; the builder claims all five fixed (all 16
round-1 attack tests pass). This file attacks the FIXES for new holes.
Every test here is an objection stated as a failing test or a concretely
violated invariant. All fixtures live in /tmp — the live store is never
touched (the autouse conftest fixture redirects every cli store path at
a fresh tmp tree).
"""
from __future__ import annotations

import io
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


# --- Attack 1: the round-1 fix's disambiguator collides with itself -----
#
# Round 1, attack 2: the free-text label "unstamped" collided with the
# report's reserved "  unstamped: N" line. The fix prints that label as
# "  unstamped (thinker label): N". But the disambiguator string is
# itself a valid free-text label: a host labelled "unstamped (thinker
# label)" is accepted by validate_thinker_label (no control characters,
# under 80 chars) and prints a byte-identical line. With both labels
# present the report carries two indistinguishable
# "  unstamped (thinker label): 1" lines, so the counts can no longer be
# read honestly — the same defect class the fix was supposed to close,
# reintroduced by the fix itself. A disambiguation that a valid label
# can reproduce is not a disambiguation.

def test_disambiguator_label_does_not_collide_with_disambiguated_line():
    rc, _ = _think("a thought from a host literally called unstamped",
                   "--thinker", "unstamped")
    assert rc == 0
    rc, _ = _think("a thought from a host called unstamped (thinker label)",
                   "--thinker", "unstamped (thinker label)")
    assert rc == 0
    rc, out = _capture("thinkers")
    assert rc == 0
    lines = [l for l in out.splitlines()
             if "unstamped (thinker label)" in l]
    assert len(lines) == len(set(lines)), (
        "report prints the same 'unstamped (thinker label):' line for "
        "two different labels and cannot be read honestly:\n" + out)


# --- Attack 2: from_dict re-admits stamps on non-thought records --------
#
# Round 1, attack 5: CalibosWorkspace.add is now the enforcement point —
# a stamp lands only on source "thought" with a validated label, and the
# builder's own comment claims "no stamp can ever land on an
# engine/dream/interoception or any other non-thinker-authored record".
# But from_dict() is a second landing path and it enforces only the
# LABEL (re-validation), never the SOURCE: a persisted payload carrying
# a stamp on a "memory" record (e.g. written by the round-1 build, which
# stamped any source, or any foreign payload) loads with the stamp in
# the map and to_dict() writes it straight back out. The spec's revert
# signal is absolute — "A stamp appears on any record not authored
# through inject_thought" — and the load boundary is where a bad payload
# must die, not where it is laundered back into the store.

def test_from_dict_never_stamps_non_thought_source():
    data = {"records": [
        {"id": "experience-1", "tick": 0, "source": "memory",
         "first_person": "a cartridge memory", "generated_by": "cartridge",
         "thinker": "model-A"},
        {"id": "experience-2", "tick": 1, "source": "thought",
         "first_person": "a genuine thought", "generated_by": "voluntary",
         "thinker": "model-A"},
    ], "sequence": 2}
    ws = CalibosWorkspace.from_dict(data)
    assert ws._thinker_stamps == {"experience-2": "model-A"}, (
        f"stamp re-entered the map on a non-thinker-authored record: "
        f"{ws._thinker_stamps}")
    stamped = [r for r in ws.to_dict()["records"] if "thinker" in r]
    assert [r["source"] for r in stamped] == ["thought"], (
        f"stamp persisted on non-thinker-authored records after load: "
        f"{[(r['source'], r['first_person']) for r in stamped]}")
    # And the round-trip must agree with the loaded state.
    ws2 = CalibosWorkspace.from_dict(ws.to_dict())
    assert ws2._thinker_stamps == ws._thinker_stamps
    assert ws.to_dict() == ws2.to_dict()


# --- Verification (should pass; recorded so the check is explicit) -------
#
# from_dict drops a persisted stamp that fails the round-2 Unicode-aware
# validator (e.g. written before the validator went Unicode-aware): it
# loads as unstamped, never crashes, and the round-trip is stable.

def test_from_dict_drops_unicode_invalid_persisted_stamp():
    data = {"records": [
        {"id": "experience-1", "tick": 0, "source": "thought",
         "first_person": "a thought with a broken-line stamp",
         "generated_by": "voluntary", "thinker": "model\u2028A"},
        {"id": "experience-2", "tick": 1, "source": "thought",
         "first_person": "a clean stamped thought",
         "generated_by": "voluntary", "thinker": "model-A"},
    ], "sequence": 2}
    ws = CalibosWorkspace.from_dict(data)
    assert ws._thinker_stamps == {"experience-2": "model-A"}
    recs = ws.to_dict()["records"]
    assert "thinker" not in recs[0]
    assert recs[1]["thinker"] == "model-A"
    ws2 = CalibosWorkspace.from_dict(ws.to_dict())
    assert ws2.to_dict() == ws.to_dict()
