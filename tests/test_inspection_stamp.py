"""Tests for `mind think --inspection` (inspection-provenance stamp).

Spec: research/specs/2026-10-17-inspection-provenance-stamp.md
All fixtures live in /tmp — the live store is never touched.

The stamp "voluntary:inspection" is a colon-subkind of "voluntary"
(precedent: "answered:prompt-0008@66"). It exists because the thinker
composing an instrument-reading report ("I inspected") is psychologically
distinct from the thinker composing a lived reflection ("I thought"),
and the distinction must be first-class, never reconstructed.
"""
from __future__ import annotations

import io
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind import cli
from calibos_mind.attribution import is_external_origin


def _fresh_cli_paths(tmp):
    """Point every store/sidecar path the think path can touch at a tmp dir."""
    tmp = Path(tmp)
    cli.DB = tmp / "mind.db"
    cli.INBOX = tmp / "inbox"
    cli.DREAMS = tmp / "dreams"
    cli.SALIENCE = tmp / "salience.json"
    cli.PROVENANCE = tmp / "provenance.json"
    cli.INTEROCEPTION = tmp / "interoception.json"
    cli.FAMILIARITY = tmp / "familiarity.json"
    cli.AMBIVALENCE = tmp / "ambivalence.json"
    cli.COUNTERFACTUAL = tmp / "counterfactuals.json"
    cli.HABITS = tmp / "habits-formed.json"
    cli.INBOX.mkdir(parents=True, exist_ok=True)
    cli.DREAMS.mkdir(parents=True, exist_ok=True)


def _records():
    return cli._subject().inspect()["workspace"]["records"]


def _think(text, inspection=False):
    buf = io.StringIO()
    argv = ["think"] + (["--inspection"] if inspection else []) + [text]
    with redirect_stdout(buf), redirect_stderr(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


# 1. Stamp contract ------------------------------------------------------

def test_inspection_stamp_recorded_exactly_once():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        text = "Drift R = 0.980 at tick 103 — an inspection report"
        rc, _ = _think(text, inspection=True)
        assert rc == 0, "stamped think must succeed"
        recs = [r for r in _records() if r["source"] == "thought"]
        matches = [r for r in recs if r["first_person"] == text]
        assert len(matches) == 1, f"expected exactly one record, got {len(matches)}"
        r = matches[0]
        assert r["generated_by"] == "voluntary:inspection"
        assert r["first_person"] == text, "text must be byte-intact"
        assert bytes(r["first_person"], "utf-8") == bytes(text, "utf-8")


def test_plain_think_keeps_voluntary_stamp():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        rc, _ = _think("a plain voluntary thought")
        assert rc == 0
        recs = [r for r in _records()
                if r["first_person"] == "a plain voluntary thought"]
        assert len(recs) == 1
        assert recs[0]["generated_by"] == "voluntary"


def test_stamped_think_matches_voluntary_sidecars():
    """+0.3 salience importance and provenance-tracker entry, identical
    to the plain think path: entry mints only when --weighed/--discarded/
    --unsure are supplied (provenance parity in both directions)."""
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        rc, _ = _think("an inspected reading", inspection=True)
        assert rc == 0
        tid = next(r["id"] for r in _records()
                   if r["first_person"] == "an inspected reading")
        tracker = cli._tracker(cli._subject())
        entry = tracker.data["records"].get(tid)
        assert entry is not None and abs(entry.get("importance", 0.0) - 0.3) < 1e-9, \
            "stamped think must add +0.3 importance like plain think"
        from calibos_mind.provenance import ProvenanceTracker
        # Without --weighed: no entry on either path (identical treatment).
        assert ProvenanceTracker(cli.PROVENANCE).get(tid) is None
        # With --weighed: entry mints identically on both paths.
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = cli.main(["think", "--inspection", "weighed stamped thought",
                           "--weighed", "option a"])
        assert rc == 0
        # plain path gets --weighed via the same arg parser:
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = cli.main(["think", "weighed plain thought 2",
                           "--weighed", "option a"])
        assert rc == 0
        recs = _records()
        t_stamp = next(r["id"] for r in recs
                       if r["first_person"] == "weighed stamped thought")
        t_plain = next(r["id"] for r in recs
                       if r["first_person"] == "weighed plain thought 2")
        prov = ProvenanceTracker(cli.PROVENANCE)
        e_stamp, e_plain = prov.get(t_stamp), prov.get(t_plain)
        assert e_stamp is not None and e_plain is not None, \
            "both paths must mint provenance entries when --weighed given"
        e_stamp = {k: v for k, v in e_stamp.items() if k != "tick"}
        e_plain = {k: v for k, v in e_plain.items() if k != "tick"}
        assert e_stamp == e_plain, "provenance entries must match"


# 2. Stale-view parity ---------------------------------------------------

def _queue_think_answer(tmp, inspection):
    _fresh_cli_paths(tmp)
    cli._subject()
    assert cli.main(["queue", "an invitation"]) == 0
    think_argv = ["think"] + (["--inspection"] if inspection else []) \
        + ["an intervening inspection of the instrument panel"]
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        think_rc = cli.main(think_argv)
    assert think_rc == 0
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        answer_rc = cli.main(["answer", "prompt-0001", "a considered answer"])
    return answer_rc, buf.getvalue()


def test_inspection_thought_does_not_stale_queued_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        rc, out = _queue_think_answer(tmp, inspection=True)
        assert rc == 0, f"answer after --inspection think must succeed: {out}"
        assert "refused" not in out.lower()


def test_plain_think_control_behaves_identically():
    with tempfile.TemporaryDirectory() as tmp:
        rc, out = _queue_think_answer(tmp, inspection=False)
        assert rc == 0, f"control answer after plain think must succeed: {out}"
        assert "refused" not in out.lower()


# 3. No behavioral delta -------------------------------------------------

def _capture(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


def _paired_stores(tmp, stamp_argv):
    """Two synthetic stores differing ONLY in stamp choice."""
    outs = []
    for half in ("a", "b"):
        _fresh_cli_paths(Path(tmp) / half)
        cli._subject()
        stamp = stamp_argv[half]
        rc, _ = _capture("think", *stamp, "the lamp hums in the dark")
        assert rc == 0
        rc, _ = _capture("think", *stamp, "hunger rises +0.002/tick")
        assert rc == 0
        drift_rc, drift = _capture("drift")
        assert drift_rc == 0
        status_rc, status = _capture("status")
        assert status_rc == 0
        review_rc, review = _capture("review")
        assert review_rc == 0
        outs.append({
            "drift": drift, "status": status, "review": review,
            "salience": (Path(tmp) / half / "salience.json").read_bytes(),
        })
    return outs


def test_paired_stores_byte_identical_surfaces():
    with tempfile.TemporaryDirectory() as tmp:
        outs = _paired_stores(tmp, {"a": [], "b": ["--inspection"]})
        a, b = outs
        assert a["drift"] == b["drift"], "drift output must be byte-identical"
        assert a["status"] == b["status"], "status output must be byte-identical"
        assert a["review"] == b["review"], "review output must be byte-identical"
        assert a["salience"] == b["salience"], \
            "salience sidecar must be byte-identical"


# 4. Attribution ----------------------------------------------------------

def test_inspection_stamp_is_not_external_origin():
    assert is_external_origin("voluntary:inspection") is False
    assert is_external_origin("voluntary") is False


# 5. Caps and duplicate refusal -------------------------------------------

def test_inspection_think_applies_length_cap():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        before = len(_records())
        rc, out = _think("x" * 601, inspection=True)
        assert rc == 1, "601-char stamped thought must be refused"
        assert "601" in out
        assert "nothing recorded" in out
        assert len(_records()) == before, "refused thought must not be recorded"


def test_inspection_think_refuses_recent_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        rc, _ = _think("the same instrument reading twice", inspection=True)
        assert rc == 0
        before = len(_records())
        rc, out = _think("the same instrument reading twice", inspection=True)
        assert rc == 1, "duplicate within 6 ticks must be refused"
        assert "nothing recorded" in out
        assert len(_records()) == before


# 6. Refusal paths --------------------------------------------------------

def test_inspection_think_refuses_empty_text_loudly():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        before = len(_records())
        rc, out = _think("", inspection=True)
        assert rc != 0, "empty stamped think must fail nonzero"
        assert "refused" in out.lower(), "empty stamped think must be loud"
        assert len(_records()) == before, "ledger must be untouched"


def test_inspection_think_refuses_whitespace_text():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        rc, out = _think("   ", inspection=True)
        assert rc != 0
        assert "refused" in out.lower()


# 7. Stamp containment -----------------------------------------------------

def test_dream_never_mints_inspection_stamp():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        cli._subject()
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            cli.main(["dream", "--ticks", "2"])
        stamps = [r["generated_by"] for r in _records()]
        assert "voluntary:inspection" not in stamps, \
            "dream ticks must never mint the inspection stamp"


def test_readonly_commands_never_mint_inspection_stamp():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        cli._subject()
        before = len(_records())
        for argv in (["status"], ["review"], ["drift"], ["inbox"]):
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                cli.main(argv)
        assert len(_records()) == before, "read-only commands must add nothing"
        stamps = [r["generated_by"] for r in _records()]
        assert "voluntary:inspection" not in stamps


def test_init_force_wipes_stamped_records():
    with tempfile.TemporaryDirectory() as tmp:
        _fresh_cli_paths(tmp)
        cli._subject()
        rc, _ = _think("a stamped record to be wiped", inspection=True)
        assert rc == 0
        stamped = [r for r in _records()
                   if r["generated_by"] == "voluntary:inspection"]
        assert stamped, "precondition: a stamped record exists"
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = cli.main(["init", "--force"])
        assert rc == 0
        remaining = [r for r in _records()
                     if r["generated_by"] == "voluntary:inspection"]
        assert not remaining, "init --force must wipe stamped records"
