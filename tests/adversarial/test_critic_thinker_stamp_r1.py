"""Critic round 1 attack tests for the thinker-identity stamp (Domain 23).

Spec: research/specs/2026-11-16-thinker-identity-stamp.md
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

import pytest

from calibos_mind import cli
from calibos_mind.subject import validate_thinker_label
from calibos_mind.thinkers import is_self_authored, thinkers_report
from calibos_mind.workspace import CalibosWorkspace


def _capture(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


def _records():
    return cli._subject().inspect()["workspace"]["records"]


def _think(text, *extra):
    return _capture("think", *extra, text)


# --- Attack 1: label validation misses Unicode newlines and C1 controls --
#
# Spec: "Label validation: stripped, non-empty, max 80 chars, no newline
# or control characters". The validator checks only ord(c) < 32 or == 127.
# U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR are newline
# characters (Unicode categories Zl/Zp; str.splitlines() breaks on them),
# and U+0080..U+009F are C1 control characters (category Cc). All pass.
# A label carrying one breaks the one-label-per-line `mind thinkers`
# report: the label itself injects a line break into the output.

@pytest.mark.parametrize("bad", [
    "model\u2028A",   # LINE SEPARATOR: a newline by Unicode definition
    "model\u2029A",   # PARAGRAPH SEPARATOR: likewise a line boundary
    "model\x85A",     # NEL (U+0085): a C1 control character
    "model\x80A",     # PAD (U+0080): a C1 control character
    "model\x9bA",     # CSI (U+009B): a C1 control character
])
def test_validate_rejects_unicode_newlines_and_c1_controls(bad):
    with pytest.raises(ValueError):
        validate_thinker_label(bad)


def test_cli_refuses_unicode_line_separator_label():
    # End-to-end: the CLI must refuse the label (rc 1, nothing recorded),
    # exactly as it refuses an ASCII newline label today.
    before = len(_records())
    rc, out = _think("a thought with a broken-line label",
                     "--thinker", "model\u2028A")
    assert rc == 1, f"CLI accepted a label containing U+2028: {out!r}"
    assert "refused" in out.lower()
    assert len(_records()) == before


# --- Attack 2: the free-text label "unstamped" collides with the report's
# reserved unstamped line --------------------------------------------------
#
# `mind thinkers` prints one line per label ("  <label>: <n>") and then a
# reserved line "  unstamped: <n>". A thinker labelled "unstamped" makes
# the output carry two indistinguishable "unstamped:" lines, so the counts
# can no longer be read honestly: which line is the label and which is the
# legacy count is ambiguous to any reader or parser.

def test_label_unstamped_does_not_collide_with_reserved_line():
    rc, _ = _think("a thought from a host literally called unstamped",
                   "--thinker", "unstamped")
    assert rc == 0
    rc, out = _capture("thinkers")
    assert rc == 0
    assert out.count("unstamped:") == 1, (
        "report carries two 'unstamped:' lines (label + reserved count) "
        f"and cannot be read honestly:\n{out}")


# --- Attack 3: `mind thinkers` crashes on a malformed generated_by -------
#
# thinkers_report / is_self_authored assume generated_by is a str (or
# None). A persisted record whose generated_by is not a string loads
# cleanly — the frozen dataclass performs no type check — but the
# read-only report then dies: `123 in frozenset` is False, then
# `123.startswith(...)` raises AttributeError; a list raises TypeError
# on the `in` check itself (unhashable). A read-only report must never
# crash the command on a malformed record; it must count it as
# not-self-authored (or skip it) and keep going.

@pytest.mark.parametrize("gb", [123, ["answered:x"], {"label": "x"}])
def test_is_self_authored_survives_non_string_generated_by(gb):
    assert is_self_authored(gb) is False


def test_thinkers_report_survives_non_string_generated_by():
    records = [
        {"id": "experience-1", "tick": 0, "source": "thought",
         "first_person": "a malformed-origin thought", "generated_by": 123,
         "thinker": "model-A"},
        {"id": "experience-2", "tick": 1, "source": "thought",
         "first_person": "a clean thought", "generated_by": "voluntary",
         "thinker": "model-A"},
    ]
    rep = thinkers_report(records)
    assert rep["n"] == 1
    assert rep["counts"] == {"model-A": 1}


# --- Attack 4: the validator does not honour its own contract on
# non-string input ---------------------------------------------------------
#
# validate_thinker_label's contract is "Raises ValueError on ...".
# A non-string label raises AttributeError ('int' object has no
# attribute 'strip') instead, and inject_thought passes it straight
# through — cmd_think catches only ValueError, so this surfaces as a
# raw traceback, not a clean refusal.

def test_validate_non_string_label_raises_value_error():
    with pytest.raises(ValueError):
        validate_thinker_label(123)


def test_inject_thought_non_string_thinker_raises_value_error():
    subject = cli._subject()
    with pytest.raises(ValueError):
        subject.inject_thought("a thought with a non-string label",
                               thinker=123)


# --- Attack 5: CalibosWorkspace.add stamps any source, and stores
# unvalidated labels ------------------------------------------------------
#
# The stamp map is the single funnel through which stamps enter the
# store, and it enforces nothing: add() stamps a "memory" record (the
# spec's revert signal is absolute — "A stamp appears on any record not
# authored through inject_thought"), and it stores an empty-string label
# verbatim, so to_dict() writes "thinker": "" into the persisted
# payload — a record that carries a thinker key yet counts as unstamped,
# which then silently vanishes on the next load (from_dict drops empty
# labels). In-memory state, persisted payload, and loaded state disagree
# about the same record.

def test_workspace_add_never_stamps_non_thought_source():
    ws = CalibosWorkspace(limit=64)
    ws.add(0, "memory", "a cartridge memory", generated_by="cartridge",
           thinker="model-A")
    ws.add(1, "temporal", "a temporal note", thinker="model-A")
    ws.add(2, "thought", "a genuine thought", generated_by="voluntary",
           thinker="model-A")
    stamped = [r for r in ws.to_dict()["records"] if "thinker" in r]
    assert [r["source"] for r in stamped] == ["thought"], (
        f"stamp landed on non-thinker-authored records: "
        f"{[(r['source'], r['first_person']) for r in stamped]}")


def test_workspace_add_drops_empty_or_non_string_labels():
    ws = CalibosWorkspace(limit=64)
    ws.add(0, "thought", "an empty-label thought",
           generated_by="voluntary", thinker="")
    ws.add(1, "thought", "a non-string-label thought",
           generated_by="voluntary", thinker=123)
    recs = ws.to_dict()["records"]
    assert all("thinker" not in r for r in recs), (
        f"unvalidated labels persisted: "
        f"{[(r['first_person'], r.get('thinker')) for r in recs]}")
    # And the round-trip must agree with the in-memory state.
    ws2 = CalibosWorkspace.from_dict(ws.to_dict())
    assert ws2._thinker_stamps == {}
    assert ws.to_dict() == ws2.to_dict()


# --- Verification (should pass; recorded so the check is explicit) -------
#
# Eviction must prune stamps with their records, so the map never
# outlives the store.

def test_eviction_prunes_stamps_with_records():
    ws = CalibosWorkspace(limit=3)
    for i in range(5):
        ws.add(i, "thought", f"thought {i}", generated_by="voluntary",
               thinker="model-A")
    assert len(ws.records) == 3
    assert set(ws._thinker_stamps) == {r.id for r in ws.records}
    assert all(r.get("thinker") == "model-A"
               for r in ws.to_dict()["records"])
