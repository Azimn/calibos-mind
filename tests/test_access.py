"""Tests for the access report (Domain 32, Opacity and Cognitive Privacy).

Spec: research/specs/2026-11-25-access-report.md
All fixtures on /tmp — the live store is never touched.

The mechanism: calibos_mind/access.py — a pure report function over a
loaded store (no sidecar, no state of its own), plus a `mind access`
command wired like `mind drift` / `mind relationships` / `mind stakes`.

Fitness functions under test:
  1. Seeded store with a known mix (available / available_to_cognition
     False / archived-excluded / pinned cartridge / unpinned) reports
     every count exactly, including the by-cause unavailable breakdown
     and the view composition (pinned + room) matching
     workspace.view() semantics.
  2. Fresh store (0 records): defined output, view 0 of 0, no crash,
     no sidecar, "undefined" where a ratio would divide by zero.
  3. Missing workspace / records section raises (malformed store),
     never a quiet zero.
  4. Read-only sweep: payload content byte-identical, no sidecar
     minted, no file created (scoped to payload content, per the
     CLI-construction genome item — cli._subject() construction
     rewrites the row, so mtime is out of scope).
  5. Dream ticks never touch the report path: the command works
     identically before and after a dream tick — measured honestly,
     a dream tick on a fresh store mints dream-derived records
     (probed 2026-11-25: 6 -> 8 records), so the post-dream report
     must track the new store exactly, not repeat the old numbers.
  6. Determinism: identical store -> byte-identical report text.
  7. `--help` lists `access`.
  8. Full suite green. Engine + cartridge byte-identical
     (run separately).
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.access import access_report, format_access_report
from calibos_mind.consolidate import _write_availability


# -- helpers ----------------------------------------------------------------

def _args(force=False):
    return argparse.Namespace(force=force)


def _run(cmd, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd(args)
    return rc, buf.getvalue()


def _rec(rid, tick, source, text, generated_by=None,
         available_to_cognition=True):
    """A full SubjectiveExperience dict, as inspect() returns it."""
    return {
        "id": rid, "tick": tick, "source": source,
        "first_person": text, "salience": 0.5, "intensity": 0.5,
        "concepts": (), "affect": (), "memory_links": (),
        "concern_links": (), "expectation_links": (),
        "generated_by": generated_by, "private": True,
        "available_to_cognition": available_to_cognition,
    }


def _mixed_state(archive_dir):
    """Known mix, hand-built: 7 cartridge-eligible, 3 grown available,
    2 flagged unavailable, 1 archived-excluded, 1 both."""
    recs = [_rec(f"cart-{i}", 0, "memory",
                 f"synthetic cartridge root number {i}",
                 generated_by="cartridge") for i in range(7)]
    recs += [
        _rec("grown-mem", 1, "memory", "synthetic grown memory one"),
        _rec("grown-perc", 2, "perception",
             "synthetic grown perception one"),
        _rec("grown-thought", 3, "thought",
             "synthetic grown thought one"),
        _rec("flag-1", 4, "memory", "synthetic flagged one",
             available_to_cognition=False),
        _rec("flag-2", 5, "memory", "synthetic flagged two",
             available_to_cognition=False),
        _rec("arch-1", 6, "memory", "synthetic archived one"),
        _rec("both-1", 7, "memory", "synthetic both causes one",
             available_to_cognition=False),
    ]
    _write_availability(archive_dir, {"excluded": {
        "arch-1": {"reason": "synthetic test exclusion", "tick": 6},
        "both-1": {"reason": "synthetic test exclusion", "tick": 7},
    }})
    return {
        "workspace": {"records": recs, "sequence": len(recs)},
        "engine": {"tick": 10},
        "config": {"inner_ear": True},
    }


def _seed_cli_store():
    """cmd_init store + the same known mix, added through the real CLI
    subject. Returns the ids of the archived records."""
    assert cli.cmd_init(_args(force=False)) == 0
    subject = cli._subject()
    with subject._transaction():
        for i in range(3):
            subject._add("memory",
                         f"synthetic cartridge seed number {i}",
                         concepts=("seed",), generated_by="cartridge")
        subject._add("memory", "synthetic grown memory one",
                     concepts=("seed",))
        subject._add("perception", "synthetic grown perception one",
                     concepts=("seed",))
        subject._add("thought", "synthetic grown thought one",
                     concepts=("seed",))
        subject._add("social", "synthetic grown social one",
                     concepts=("seed",))
        subject._add("memory", "synthetic flagged one",
                     concepts=("seed",), available_to_cognition=False)
        subject._add("memory", "synthetic flagged two",
                     concepts=("seed",), available_to_cognition=False)
        subject._add("memory", "synthetic archived one",
                     concepts=("seed",))
        subject._add("memory", "synthetic both causes one",
                     concepts=("seed",), available_to_cognition=False)
    by_text = {r["first_person"]: r
               for r in subject.inspect()["workspace"]["records"]}
    arch_ids = [by_text["synthetic archived one"]["id"],
                by_text["synthetic both causes one"]["id"]]
    _write_availability(cli.ARCHIVE, {"excluded": {
        rid: {"reason": "synthetic test exclusion", "tick": 0}
        for rid in arch_ids}})
    return arch_ids


def _payload_bytes():
    subject = cli._subject()
    return json.dumps(subject.inspect(), sort_keys=True).encode("utf-8")


def _tree_snapshot(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file())


# -- pure-function tests ----------------------------------------------------

def test_seeded_mix_exact_counts(tmp_path):
    """Every count exact on a known mix, including the by-cause
    breakdown and the view composition."""
    archive_dir = tmp_path / "archive"
    state = _mixed_state(archive_dir)
    avail_path = archive_dir / "availability.json"
    rep = access_report(state, availability_path=avail_path)
    # 7 cartridge + 3 grown + 2 flagged + 1 archived + 1 both = 14
    assert rep["total"] == 14, rep
    assert rep["unavailable_flag_false"] == 3, rep  # flag-1, flag-2, both-1
    assert rep["unavailable_archived"] == 2, rep    # arch-1, both-1
    assert rep["unavailable"] == 4, rep             # union, both-1 once
    assert rep["available"] == 10, rep
    assert rep["availability_file_present"] is True, rep
    # 7 cartridge-eligible, pinned capped at MAX_PINNED (6)
    assert rep["pinned_eligible"] == 7, rep
    assert rep["pinned"] == 6, rep
    assert rep["max_pinned"] == 6, rep
    # View: 6 pinned + 4 admitted. The ranked pool is the 1 cartridge
    # record past the pin cap plus the 3 grown records (distinct
    # classes, distinct texts: no dedupe, no cap bite) = 4 of 10 room.
    assert rep["ranked_room"] == 10, rep            # 16 - 6
    assert rep["ranked_admitted"] == 4, rep
    assert rep["view_size"] == 10, rep
    assert rep["view_share"] == 10 / 14, rep
    assert rep["inner_ear"] is True, rep
    assert rep["tick"] == 10, rep
    # An explicitly passed live view size wins over the rebuild.
    rep2 = access_report(state, availability_path=avail_path, view_size=10)
    assert rep2 == rep, (rep, rep2)


def test_zero_record_store_defined_output(tmp_path):
    """0 records: defined output, view 0 of 0, 'undefined' for the
    share — never a crash, never a quiet zero."""
    state = {"workspace": {"records": [], "sequence": 0},
             "engine": {"tick": 0}, "config": {"inner_ear": True}}
    before = sorted(p.name for p in tmp_path.iterdir())
    rep = access_report(state,
                        availability_path=tmp_path / "availability.json")
    assert rep["total"] == 0, rep
    assert rep["available"] == 0, rep
    assert rep["unavailable"] == 0, rep
    assert rep["pinned"] == 0, rep
    assert rep["ranked_room"] == 16, rep
    assert rep["view_size"] == 0, rep
    assert rep["view_share"] is None, rep
    assert rep["availability_file_present"] is False, rep
    text = format_access_report(rep)
    assert "view shows 0 of 0 records" in text, text
    assert "undefined" in text, text
    assert "no availability file" in text, text
    # No sidecar minted by the pure report: the tmp tree is unchanged.
    assert not (tmp_path / "availability.json").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_missing_workspace_raises():
    """Missing workspace/records section is malformed, never a
    quiet zero — the silent-zero genome item."""
    with pytest.raises(ValueError, match="malformed store"):
        access_report({"engine": {"tick": 0}})
    with pytest.raises(ValueError, match="malformed store"):
        access_report({"workspace": {}, "engine": {"tick": 0}})
    with pytest.raises(ValueError, match="malformed store"):
        access_report({"workspace": {"records": "not-a-list"}})


def test_inner_ear_off_and_undefined():
    """The inner_ear setting is reported as measured, including off
    and absent-from-config."""
    base = {"workspace": {"records": [], "sequence": 0},
            "engine": {"tick": 0}}
    rep = access_report({**base, "config": {"inner_ear": False}})
    assert rep["inner_ear"] is False, rep
    assert "off" in format_access_report(rep)
    rep2 = access_report(base)
    assert rep2["inner_ear"] is None, rep2
    assert "undefined" in format_access_report(rep2)


def test_format_deterministic(tmp_path):
    """Identical report -> byte-identical text on replay."""
    archive_dir = tmp_path / "archive"
    state = _mixed_state(archive_dir)
    rep = access_report(state,
                        availability_path=archive_dir / "availability.json")
    assert format_access_report(rep) == format_access_report(rep)


# -- CLI tests (synthetic /tmp stores via the conftest fixture) -------------

def test_cli_fresh_store_report(tmp_path):
    """cmd_init store: 6 cartridge records, all available, view 6 of 6."""
    assert cli.cmd_init(_args(force=False)) == 0
    rc, out = _run(cli.cmd_access, argparse.Namespace())
    assert rc == 0, out
    assert "access (tick 0, 6 records) — read-only" in out, out
    assert "6 available to cognition, 0 unavailable" in out, out
    assert "0 flagged unavailable" in out, out
    assert "0 archived-excluded" in out, out
    assert "no availability file" in out, out
    assert "pinned (cartridge-authored, eligible): 6 shown (of 6 eligible)" in out, out
    assert "6 pinned + 10 ranked room" in out, out
    assert "view shows 6 of 6 records (100.0% of the store)" in out, out
    assert "inner ear: on" in out, out


def test_cli_seeded_mix_matches_live_view(tmp_path):
    """CLI report on the seeded mix: every count exact, and the
    snapshot-rebuild view size equals the live salience-ranked view."""
    _seed_cli_store()
    rc, out = _run(cli.cmd_access, argparse.Namespace())
    assert rc == 0, out
    # 6 init seeds + 3 cartridge + 4 grown + 2 flagged + 1 archived
    # + 1 both = 17 records.
    assert "17 records" in out, out
    assert "13 available to cognition, 4 unavailable" in out, out
    assert "3 flagged unavailable" in out, out
    assert "2 archived-excluded" in out, out
    assert "availability file: present" in out, out
    # 6 init + 3 added cartridge-eligible = 9, capped at 6.
    assert "pinned (cartridge-authored, eligible): 6 shown (of 9 eligible)" in out, out
    assert "6 pinned + 10 ranked room" in out, out
    # 6 pinned + 4 grown unpinned = 10 shown of 17 — plus the 3
    # cartridge records past the pin cap, which stay eligible for the
    # ranked room (view() excludes only the capped pinned ids from
    # its ranked pool): 6 pinned + 7 admitted = 13 shown.
    assert "view shows 13 of 17 records" in out, out
    assert "6 pinned + 10 ranked room (7 admitted)" in out, out

    subject = cli._subject()
    state = subject.inspect()
    live_size = len(subject.workspace.view().experiences)
    assert live_size == 13, live_size
    rebuilt = access_report(state,
                            availability_path=cli.ARCHIVE / "availability.json")
    assert rebuilt["view_size"] == live_size, (rebuilt, live_size)
    assert rebuilt["total"] == 17, rebuilt


def test_cli_view_size_with_class_caps(tmp_path):
    """Caps bite: 12 unpinned thoughts, room 10, thought cap 4 — the
    view is smaller than pinned + room, and the report says so."""
    assert cli.cmd_init(_args(force=False)) == 0
    subject = cli._subject()
    with subject._transaction():
        for i in range(12):
            subject._add("thought",
                         f"synthetic looping thought number {i}",
                         concepts=("loop",))
    rc, out = _run(cli.cmd_access, argparse.Namespace())
    assert rc == 0, out
    # 6 pinned + 4 admitted (thought cap) = 10 shown of 18.
    assert "view shows 10 of 18 records" in out, out
    assert "6 pinned + 10 ranked room (4 admitted)" in out, out


def test_cli_read_only_sweep(tmp_path):
    """Payload content byte-identical, no file created, no sidecar
    minted — scoped to payload content, per the CLI-construction
    genome item (construction rewrites the row, so mtime is out of
    scope)."""
    _seed_cli_store()
    root = Path(cli.DB).parent
    before_payload = _payload_bytes()
    before_tree = _tree_snapshot(root)
    rc1, out1 = _run(cli.cmd_access, argparse.Namespace())
    rc2, out2 = _run(cli.cmd_access, argparse.Namespace())
    assert rc1 == 0 and rc2 == 0
    assert out1 == out2, "identical store must give byte-identical text"
    assert _payload_bytes() == before_payload, "report wrote the store!"
    assert _tree_snapshot(root) == before_tree, "report created a file!"
    # The availability journal the test itself wrote is untouched.
    assert (cli.ARCHIVE / "availability.json").exists()


def test_cli_dream_tick_leaves_report_path_untouched(tmp_path):
    """Dream ticks never touch the report path: the command still
    works after a dream tick and tracks the store exactly. A dream
    tick on a fresh store mints dream-derived records (6 -> 8), so
    the post-dream report must show the new store, not repeat the
    old numbers — the dream changes the store, not the reporter."""
    assert cli.cmd_init(_args(force=False)) == 0
    rc1, out1 = _run(cli.cmd_access, argparse.Namespace())
    assert rc1 == 0, out1
    assert "6 records" in out1, out1
    rc_d, _ = _run(cli.cmd_dream, argparse.Namespace(ticks=1))
    assert rc_d == 0
    rc2, out2 = _run(cli.cmd_access, argparse.Namespace())
    assert rc2 == 0, out2
    n_after = len(cli._subject().inspect()["workspace"]["records"])
    assert f"{n_after} records" in out2, out2
    assert f"view shows {n_after} of {n_after} records" in out2, out2
    # And the report is still deterministic across the dream.
    rc3, out3 = _run(cli.cmd_access, argparse.Namespace())
    assert rc3 == 0 and out3 == out2


def test_help_lists_access(capsys):
    """`mind --help` lists the access command."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "access" in capsys.readouterr().out
