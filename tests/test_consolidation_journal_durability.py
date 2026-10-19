"""Tests for consolidation-journal durability (2026-10-18).

The Oct-17 incident: the proposal journal (proposals/proposals.json,
gitignored, single copy) was silently lost — 943 pending proposals incl. 55
contradiction-flags — and `mind consolidate --list` just reported "no pending
proposals". An empty journal is indistinguishable from a lost one without
integrity armor.

Mechanism under test:
- _save_journal rotates a one-deep backup (proposals.json.bak1) and writes
  an integrity sentinel (journal.seq) — same gitignored dir, nothing tracked.
- load_journal falls back to the backup (loud stderr notice) when the primary
  is missing or corrupt, restoring the primary on write paths only.
- load_journal diagnoses loudly (never silently empty) when no backup exists;
  the "journal lost" variant fires when the sentinel says the journal existed.
- `mind init --force` wipes primary + backup + sentinel (genome: sidecar
  reset on reseed — stale proposal ids must never attach to recycled
  experience ids).
- Read-only paths (drift, status, consolidate --list, recall) never write
  backup/sentinel files.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_consolidation_journal_durability.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched. The CLI's
module-global paths are redirected to a tmp dir for the duration of each
CLI test and restored afterwards.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind import consolidate as C

BASE = Path(__file__).resolve().parents[1]


def _prop(pid: int, kind: str = "contradiction-flag") -> dict:
    return {"id": pid, "kind": kind, "a": "experience-1", "b": "experience-2",
            "winner": None, "loser": None, "rationale": "r",
            "reason": "", "confidence": 0.3, "a_hash": "h1", "b_hash": "h2",
            "a_tick": 1, "b_tick": 2, "status": "pending",
            "created_tick": 1, "resolved_tick": None, "rejected_reason": None}


def _journal(n: int = 3, seq: int | None = None) -> dict:
    props = {str(i): _prop(i) for i in range(1, n + 1)}
    return {"seq": n if seq is None else seq, "proposals": props}


def _load_capture_err(base: Path, **kw) -> tuple[dict, str]:
    err = io.StringIO()
    with redirect_stderr(err):
        journal = C.load_journal(base, **kw)
    return journal, err.getvalue()


# -- backup rotation -----------------------------------------------------------

def test_first_save_writes_primary_and_sentinel_but_no_backup():
    tmp = Path(tempfile.mkdtemp(prefix="jdur-"))
    C._save_journal(tmp, _journal(3))
    assert (tmp / "proposals.json").exists()
    assert (tmp / "journal.seq").exists()
    assert not (tmp / "proposals.json.bak1").exists(), \
        "no previous primary exists on first save — nothing to back up"
    sentinel = json.loads((tmp / "journal.seq").read_text(encoding="utf-8"))
    assert sentinel["seq"] == 3
    assert sentinel["mtime"] > 0


def test_backup_rotates_one_deep_byte_identical():
    tmp = Path(tempfile.mkdtemp(prefix="jdur-"))
    j1 = _journal(1)
    C._save_journal(tmp, j1)
    b1 = (tmp / "proposals.json").read_bytes()
    j2 = _journal(2)
    C._save_journal(tmp, j2)
    b2 = (tmp / "proposals.json").read_bytes()
    assert b1 != b2
    assert (tmp / "proposals.json.bak1").read_bytes() == b1, \
        "backup must be byte-identical to the previous primary"
    j3 = _journal(3)
    C._save_journal(tmp, j3)
    assert (tmp / "proposals.json.bak1").read_bytes() == b2, \
        "one-deep rotation: only the latest prior journal survives"
    extras = [p.name for p in tmp.iterdir()
              if p.name.startswith("proposals.json.bak")]
    assert extras == ["proposals.json.bak1"], f"exactly one backup file: {extras}"
    assert {p.name for p in tmp.iterdir()} == {"proposals.json",
                                               "proposals.json.bak1",
                                               "journal.seq",
                                               ".journal.lock"}, \
        "bounded: no stray files in the proposals dir beyond the journal, " \
        "its one-deep backup, the sentinel, and the lockfile"


def test_sentinel_tracks_seq():
    tmp = Path(tempfile.mkdtemp(prefix="jdur-"))
    C._save_journal(tmp, _journal(3))
    C._save_journal(tmp, _journal(5))
    sentinel = json.loads((tmp / "journal.seq").read_text(encoding="utf-8"))
    assert sentinel["seq"] == 5


# -- load recovery --------------------------------------------------------------

def test_save_delete_primary_restores_from_backup_with_stderr_notice():
    tmp = Path(tempfile.mkdtemp(prefix="jdur-"))
    C._save_journal(tmp, _journal(3))   # no backup yet
    C._save_journal(tmp, _journal(4))  # backup <- the 3-proposal journal
    (tmp / "proposals.json").unlink()  # the incident, reproduced
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        journal = C.load_journal(tmp)
    got = C.pending_proposals(journal)
    assert len(got) == 3, f"backup held 3 proposals, got {len(got)}"
    assert out.getvalue() == "", "the recovery notice must not go to stdout"
    assert ("consolidation journal recovered from backup: "
            "3 proposals restored") in err.getvalue()
    # The restore path rewrote the primary so the caller can continue on it.
    assert (tmp / "proposals.json").exists()
    assert C.load_journal(tmp)["seq"] == 3


def test_corrupt_primary_valid_backup_falls_back():
    tmp = Path(tempfile.mkdtemp(prefix="jdur-"))
    C._save_journal(tmp, _journal(2))
    C._save_journal(tmp, _journal(2))  # now a backup exists
    (tmp / "proposals.json").write_text('{"seq": 2, "proposals": {"1": tru',
                                        encoding="utf-8")  # truncated JSON
    journal, stderr = _load_capture_err(tmp)
    assert len(journal["proposals"]) == 2
    assert "consolidation journal recovered from backup: 2 proposals restored" in stderr


def test_corrupt_backup_is_absence_not_recovery():
    tmp = Path(tempfile.mkdtemp(prefix="jdur-"))
    C._save_journal(tmp, _journal(2))
    C._save_journal(tmp, _journal(2))
    (tmp / "proposals.json").write_text("truncated{", encoding="utf-8")
    (tmp / "proposals.json.bak1").write_text("also truncated{", encoding="utf-8")
    # Sentinel seq>0 from the saves -> the LOST variant, not the backup one.
    journal, stderr = _load_capture_err(tmp)
    assert journal == {"seq": 0, "proposals": {}}
    assert "consolidation journal LOST" in stderr
    assert "no usable backup" in stderr
    assert "re-run `mind consolidate` scan" in stderr


def test_no_backup_sentinel_seq_positive_is_journal_lost():
    tmp = Path(tempfile.mkdtemp(prefix="jdur-"))
    C._save_journal(tmp, _journal(3))
    (tmp / "proposals.json").unlink()   # primary gone, backup never existed
    journal, stderr = _load_capture_err(tmp)
    assert journal == {"seq": 0, "proposals": {}}, "never crash the exit path"
    assert "consolidation journal LOST" in stderr, \
        "sentinel seq>0 with no journal and no backup must be named as loss"
    assert "store records intact" in stderr
    assert "re-run `mind consolidate` scan" in stderr


def test_no_journal_at_all_is_plain_diagnostic():
    tmp = Path(tempfile.mkdtemp(prefix="jdur-"))  # never saved: first run
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        journal = C.load_journal(tmp)
    assert journal == {"seq": 0, "proposals": {}}
    assert "no consolidation journal found" in err.getvalue()
    assert "re-run `mind consolidate` scan" in err.getvalue()
    assert out.getvalue() == ""


def test_missing_primary_no_sentinel_reports_absence_not_loss():
    tmp = Path(tempfile.mkdtemp(prefix="jdur-"))
    # A sentinel that claims nothing (seq=0) is the fresh state, not a loss.
    (tmp / "journal.seq").write_text('{"seq": 0, "mtime": 1.0}\n',
                                     encoding="utf-8")
    _, stderr = _load_capture_err(tmp)
    assert "consolidation journal LOST" not in stderr
    assert "no consolidation journal found" in stderr


# -- CLI: read-only discipline ----------------------------------------------------

class CliOnTmp:
    """Redirect the CLI's paths at a synthetic tree (test_consolidate.py parity)."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jdur-cli-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json",
                   "HABITS": self.tmp / "habits-formed.json",
                   "AMBIVALENCE": self.tmp / "ambivalence.json",
                   "FAMILIARITY": self.tmp / "familiarity.json",
                   "PROVENANCE": self.tmp / "provenance.json",
                   "ARCHIVE": self.tmp / "archive",
                   "PROPOSALS": self.tmp / "proposals"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        with redirect_stdout(io.StringIO()):
            cli.main(["init"])
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False

    def run(self, *argv):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


def test_read_only_sweep_never_writes_backup_or_sentinel():
    with CliOnTmp() as fx:
        proposals = Path(cli.PROPOSALS)
        C._save_journal(proposals, _journal(2))
        C._save_journal(proposals, _journal(2))  # ensure .bak1 + journal.seq exist
        bak1_before = (proposals / "proposals.json.bak1").read_bytes()
        seq_before = (proposals / "journal.seq").read_bytes()
        files_before = sorted(p.name for p in proposals.iterdir())
        for argv in (["drift"], ["status"], ["consolidate", "--list"],
                     ["recall"]):
            rc, out, err = fx.run(*argv)
            assert rc == 0, f"{argv} failed: {err}"
        assert (proposals / "proposals.json.bak1").read_bytes() == bak1_before
        assert (proposals / "journal.seq").read_bytes() == seq_before
        assert sorted(p.name for p in proposals.iterdir()) == files_before, \
            "no new files may appear in the proposals dir on read-only paths"


def test_list_shows_backup_without_rewriting_primary():
    with CliOnTmp() as fx:
        proposals = Path(cli.PROPOSALS)
        C._save_journal(proposals, _journal(2))
        C._save_journal(proposals, _journal(2))  # backup now valid
        (proposals / "proposals.json").write_text("truncated{", encoding="utf-8")
        bak1_before = (proposals / "proposals.json.bak1").read_bytes()
        rc, out, err = fx.run("consolidate", "--list")
        assert rc == 0
        assert "#1 [contradiction-flag]" in out and "#2 [contradiction-flag]" in out
        assert "consolidation journal recovered from backup: 2 proposals restored" in err
        assert "read-only path" in err
        # The read-only path never rewrote anything in the proposals dir.
        assert (proposals / "proposals.json").read_text(encoding="utf-8") == "truncated{"
        assert (proposals / "proposals.json.bak1").read_bytes() == bak1_before


def test_init_force_wipes_primary_backup_and_sentinel():
    with CliOnTmp() as fx:
        proposals = Path(cli.PROPOSALS)
        C._save_journal(proposals, _journal(2))
        C._save_journal(proposals, _journal(2))  # primary + .bak1 + journal.seq
        assert (proposals / "proposals.json").exists()
        assert (proposals / "proposals.json.bak1").exists()
        assert (proposals / "journal.seq").exists()
        rc, out, err = fx.run("init", "--force")
        assert rc == 0, err
        assert not (proposals / "proposals.json").exists()
        assert not (proposals / "proposals.json.bak1").exists(), \
            "stale backup must not survive reseed"
        assert not (proposals / "journal.seq").exists(), \
            "stale sentinel would masquerade as a lost journal after reseed"
        # The fresh incarnation loads a clean empty journal, silently — no
        # journal existed, so no diagnostic belongs here.
        err2 = io.StringIO()
        with redirect_stderr(err2):
            journal = C.load_journal(proposals)
        assert journal == {"seq": 0, "proposals": {}}
        assert "LOST" not in err2.getvalue()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"ok - {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL - {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
