"""Tests for deterministic consolidation (dedup + supersession + flags).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_consolidate.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched. The CLI's
module-global paths (DB/INBOX/DREAMS/SALIENCE/ARCHIVE/PROPOSALS) are
redirected to a tmp dir for the duration of each CLI test and restored
afterwards.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind import consolidate as C

BASE = Path(__file__).resolve().parents[1]
LIVE_DB = BASE / "mind.db"


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cons-cli-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json",
                   "ARCHIVE": self.tmp / "archive",
                   "PROPOSALS": self.tmp / "proposals"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        cli.main(["init"])
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False

    def plant(self, text, generated_by="note"):
        subject = cli._subject()
        with subject._transaction():
            rec = subject._add("memory", text, generated_by=generated_by)
        return rec.id

    def journal(self):
        p = Path(cli.PROPOSALS) / "proposals.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def proposals(self):
        j = self.journal()
        return list(j["proposals"].values()) if j else []

    def run(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(argv))
        return rc, buf.getvalue()

    def archive_entries(self):
        p = Path(cli.ARCHIVE) / "memories.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
                if l.strip()]

    def excluded(self):
        p = Path(cli.ARCHIVE) / "availability.json"
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))["excluded"]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# -- dry-run read-only guarantee -------------------------------------------

def test_dry_run_changes_nothing_but_journal():
    """The regression-genome case: a read-only path must provably touch no
    write path. mind.db is opened mode=ro, so writes are impossible, not
    merely avoided — and the test pins every other file too."""
    with CliOnTmp() as ctx:
        a = ctx.plant("The garden gate was left open again this morning.")
        b = ctx.plant("The garden gate was left open again this morning!")
        db_before = sha(Path(cli.DB).read_bytes())
        sal_before = sha(Path(cli.SALIENCE).read_bytes()) \
            if Path(cli.SALIENCE).exists() else None
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        assert sha(Path(cli.DB).read_bytes()) == db_before, \
            "mind.db changed during dry-run"
        if sal_before is not None:
            assert sha(Path(cli.SALIENCE).read_bytes()) == sal_before
        # No archive writes, no availability changes on the dry-run path.
        assert not (Path(cli.ARCHIVE) / "memories.jsonl").exists()
        assert not (Path(cli.ARCHIVE) / "availability.json").exists()
        # The journal IS the dry-run's structured emission.
        props = ctx.proposals()
        assert len(props) == 1, props
        assert props[0]["kind"] == "dedup" and props[0]["status"] == "pending"
        assert {props[0]["a"], props[0]["b"]} == {a, b}


def test_dry_run_empty_store_reports_no_proposals():
    """Silent-defaults genome: zero proposals must say so, never pass quiet."""
    with CliOnTmp() as ctx:
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        assert "no proposals" in out, out
        assert ctx.journal() is None or not ctx.proposals()
        rc, out = ctx.run("consolidate", "--list")
        assert rc == 0 and "no pending proposals" in out, out


def test_dry_run_suppresses_existing_proposals():
    """Re-running the scan never re-mints or reuses proposal ids."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        _, out1 = ctx.run("consolidate")
        j1 = ctx.journal()
        _, out2 = ctx.run("consolidate")
        j2 = ctx.journal()
        assert j1["seq"] == j2["seq"], (j1["seq"], j2["seq"])
        assert "no proposals" in out2, out2  # nothing NEW; the queue stands


# -- exact dedup ------------------------------------------------------------

def test_exact_dedup_proposal():
    with CliOnTmp() as ctx:
        a = ctx.plant("The garden gate was left open again this morning.")
        b = ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        props = [p for p in ctx.proposals() if p["kind"] == "dedup"]
        assert len(props) == 1, props
        p = props[0]
        assert p["loser"] == b and p["winner"] == a, p  # earliest wins
        assert p["reason"] == "identical after normalization; one copy is enough"
        assert p["confidence"] == 1.0
        assert p["status"] == "pending" and p["created_tick"] == 0


# -- near-dup + the _same_sequence guard ------------------------------------

def test_near_dup_richer_survives():
    with CliOnTmp() as ctx:
        short = ctx.plant("Mara left the garden gate open after the morning walk")
        long = ctx.plant("Mara left the garden gate open after the morning walk yesterday")
        ctx.run("consolidate")
        props = [p for p in ctx.proposals()
                 if {p["a"], p["b"]} == {short, long}]
        assert len(props) == 1, props
        p = props[0]
        assert p["kind"] == "dedup", p
        assert p["winner"] == long and p["loser"] == short, p  # richer survives
        assert "kept the richer wording" in p["reason"], p


def test_same_sequence_rejects_reversed_pair():
    """'A calls B' vs 'B calls A' are opposites, not duplicates — the da7
    anti-footgun. Jaccard alone would call these identical."""
    with CliOnTmp() as ctx:
        a = ctx.plant("Mara left the garden gate open after the morning walk")
        b = ctx.plant("The garden gate left Mara open after the morning walk")
        # Sanity: the similarity layer alone WOULD fire without the guard.
        ta, tb = C.content_tokens(
            "Mara left the garden gate open after the morning walk"), \
            C.content_tokens("The garden gate left Mara open after the morning walk")
        assert C.jaccard(set(ta), set(tb)) == 1.0
        assert not C._same_sequence(ta, tb)
        ctx.run("consolidate")
        hits = [p for p in ctx.proposals() if {p["a"], p["b"]} == {a, b}]
        assert hits == [], hits


# -- supersession ------------------------------------------------------------

SUP_OLD = "I keep a notebook by the bed; ideas that arrive at night go into it"
SUP_NEW = ("I keep a notebook by the bed because the ideas that arrive "
           "at night vanish by morning")


def test_supersession_newer_wins_tick_chain():
    with CliOnTmp() as ctx:
        old = ctx.plant(SUP_OLD)
        ctx.run("heartbeat", "--ticks", "2")  # the newer record is newer
        new = ctx.plant(SUP_NEW)
        ctx.run("consolidate")
        props = [p for p in ctx.proposals()
                 if {p["a"], p["b"]} == {old, new}]
        assert len(props) == 1, props
        p = props[0]
        assert p["kind"] == "supersede", p
        assert p["winner"] == new and p["loser"] == old, p
        assert p["a_tick"] < p["b_tick"] or p["b_tick"] > p["a_tick"]
        assert "newer record" in p["reason"] and old in p["reason"], p
        assert 0.40 <= p["confidence"] < 0.85, p


def test_supersession_tick_tie_later_index_wins():
    """Same tick -> store order breaks the tie deterministically."""
    with CliOnTmp() as ctx:
        old = ctx.plant(SUP_OLD)
        new = ctx.plant(SUP_NEW)  # same tick 0, later index
        ctx.run("consolidate")
        props = [p for p in ctx.proposals()
                 if {p["a"], p["b"]} == {old, new}]
        assert len(props) == 1 and props[0]["kind"] == "supersede"
        assert props[0]["winner"] == new and props[0]["loser"] == old


def test_identity_root_never_loser():
    """Automatic passes may retire seeds, but never the identity root —
    even when a newer record is a textbook near-duplicate of it."""
    with CliOnTmp() as ctx:
        subject = cli._subject()
        root = next(r for r in subject.inspect()["workspace"]["records"]
                    if r["id"] == "experience-1")
        dup = ctx.plant(root["first_person"] + " Always.")
        # The pair genuinely qualifies as a near-dup without the guard.
        assert C.containment(set(C.content_tokens(root["first_person"])),
                             set(C.content_tokens(root["first_person"] + " Always."))) >= 0.92
        ctx.run("consolidate")
        for p in ctx.proposals():
            assert p["loser"] != "experience-1", p
        hits = [p for p in ctx.proposals() if {p["a"], p["b"]} == {"experience-1", dup}]
        assert hits == [], hits


# -- contradiction flags: zero mutation -------------------------------------

FLAG_A = ("The old lighthouse keeper still walks the cliff path every evening, "
           "greeting the gulls and watching the fishing boats come home")
FLAG_B = ("The old lighthouse keeper still walks the cliff path every evening, "
          "but he stopped tending the garden years ago and the roses went wild")


def test_contradiction_flag_proposal():
    with CliOnTmp() as ctx:
        a = ctx.plant(FLAG_A)
        b = ctx.plant(FLAG_B)
        ctx.run("consolidate")
        props = [p for p in ctx.proposals()
                 if {p["a"], p["b"]} == {a, b}]
        assert len(props) == 1, props
        p = props[0]
        assert p["kind"] == "contradiction-flag", p
        assert p["winner"] is None and p["loser"] is None and p["reason"] == ""
        assert 0.25 <= p["confidence"] < 0.40, p
        assert "not auto-resolved" in p["rationale"], p


def test_contradiction_flag_accept_mutates_nothing():
    with CliOnTmp() as ctx:
        ctx.plant(FLAG_A)
        ctx.plant(FLAG_B)
        ctx.run("consolidate")
        (p,) = [p for p in ctx.proposals() if p["kind"] == "contradiction-flag"]
        db_before = sha(Path(cli.DB).read_bytes())
        rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
        assert rc == 0, out
        assert "nothing archived" in out, out
        assert sha(Path(cli.DB).read_bytes()) == db_before
        assert ctx.archive_entries() == []
        assert ctx.excluded() == {}
        j = ctx.journal()
        assert j["proposals"][str(p["id"])]["status"] == "accepted"


# -- accept / reject ----------------------------------------------------------

def test_accept_archives_with_reason_and_full_text():
    with CliOnTmp() as ctx:
        a = ctx.plant("The garden gate was left open again this morning.")
        b = ctx.plant("The garden gate was left open again this morning!")
        loser_text = "The garden gate was left open again this morning!"
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
        assert rc == 0, out
        assert f"archived {b}" in out, out
        # Archive-never-delete: full text + written reason, every time.
        entries = ctx.archive_entries()
        assert len(entries) == 1, entries
        e = entries[0]
        assert e["record_id"] == b
        assert e["op"] == "dedup"
        assert e["reason"] == "identical after normalization; one copy is enough"
        assert e["original_text"] == loser_text, e
        assert e["proposal"] == p["id"]
        assert e["archived_tick"] == 0
        # Availability journal excludes it; the store record itself remains.
        assert b in ctx.excluded()
        assert ctx.excluded()[b]["reason"] == e["reason"]
        j = ctx.journal()
        assert j["proposals"][str(p["id"])]["status"] == "accepted"
        recs = cli._subject().inspect()["workspace"]["records"]
        assert any(r["id"] == b for r in recs), "archived record deleted from store"
        assert any(r["id"] == a for r in recs)
        # And the view no longer surfaces it (availability consulted by views).
        view_texts = [x.first_person for x in cli._subject().workspace.view().experiences]
        assert loser_text not in view_texts
        assert "The garden gate was left open again this morning." in view_texts


def test_accept_is_idempotent_on_retry():
    """Accepting twice: the second is refused as not-pending, never double-archived."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        b = ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        assert ctx.run("consolidate", "--accept", str(p["id"]))[0] == 0
        rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
        assert rc == 1 and "not pending" in out, out
        assert len(ctx.archive_entries()) == 1


def test_accept_never_burns_proposal_on_hash_mismatch():
    """Retry-safe accept: if the record no longer matches the proposal's
    hash, the proposal is left pending — never burned by a failure."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        # Tamper with the journal's stored hash (simulates a changed record).
        jp = Path(cli.PROPOSALS) / "proposals.json"
        j = json.loads(jp.read_text(encoding="utf-8"))
        j["proposals"][str(p["id"])]["a_hash"] = "0" * 32
        jp.write_text(json.dumps(j), encoding="utf-8")
        rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
        assert rc == 1, out
        assert "no longer matches the proposal hash" in out, out
        assert "left pending" in out, out
        j = ctx.journal()
        assert j["proposals"][str(p["id"])]["status"] == "pending"
        assert ctx.archive_entries() == []


def test_accept_after_quarantine_leaves_proposal_pending():
    """The loser was already archived by quarantine: accept must fail safe
    and leave the proposal pending, not burn it or double-archive."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        b = ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        assert ctx.run("consolidate", "--quarantine", b,
                       "--reason", "synthetic probe residue")[0] == 0
        rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
        assert rc == 1 and "already archived" in out, out
        j = ctx.journal()
        assert j["proposals"][str(p["id"])]["status"] == "pending"
        assert len(ctx.archive_entries()) == 1  # the quarantine one only


def test_reject_keeps_pending_with_reason():
    with CliOnTmp() as ctx:
        a = ctx.plant("The garden gate was left open again this morning.")
        b = ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        rc, out = ctx.run("consolidate", "--reject", str(p["id"]),
                          "--reason", "waker disagrees: keep both wordings")
        assert rc == 0, out
        j = ctx.journal()
        q = j["proposals"][str(p["id"])]
        assert q["status"] == "rejected"
        assert q["rejected_reason"] == "waker disagrees: keep both wordings"
        # Nothing archived; both records still available to views.
        assert ctx.archive_entries() == [] and ctx.excluded() == {}
        texts = [x.first_person for x in cli._subject().workspace.view().experiences]
        assert any(a in t or t.startswith("The garden gate") for t in texts)


def test_reject_requires_reason_fail_closed():
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        rc, out = ctx.run("consolidate", "--reject", str(p["id"]))
        assert rc == 1, out
        j = ctx.journal()
        assert j["proposals"][str(p["id"])]["status"] == "pending"


def test_monotonic_ids_never_reused():
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        ids1 = sorted(p["id"] for p in ctx.proposals())
        # New duplicates arrive later; their proposals continue the sequence.
        ctx.plant("Mara left the garden gate open after the morning walk")
        ctx.plant("Mara left the garden gate open after the morning walk yesterday")
        ctx.run("consolidate")
        ids2 = sorted(p["id"] for p in ctx.proposals())
        assert len(ids2) > len(ids1)
        assert min(i for i in ids2 if i not in ids1) > max(ids1), (ids1, ids2)
        assert len(set(ids2)) == len(ids2), "proposal id reused"


# -- quarantine -----------------------------------------------------------------

def test_quarantine_archives_with_written_reason():
    with CliOnTmp() as ctx:
        rid = ctx.plant("synthetic prompt-9999 residue: the moon is a test")
        db_before = sha(Path(cli.DB).read_bytes())
        rc, out = ctx.run("consolidate", "--quarantine", rid,
                          "--reason", "synthetic test residue")
        assert rc == 0, out
        assert f"quarantined {rid}" in out, out
        entries = ctx.archive_entries()
        assert len(entries) == 1, entries
        e = entries[0]
        assert e["record_id"] == rid and e["op"] == "quarantine"
        assert e["reason"] == "synthetic test residue"
        assert e["original_text"] == "synthetic prompt-9999 residue: the moon is a test"
        assert rid in ctx.excluded()
        assert sha(Path(cli.DB).read_bytes()) == db_before, \
            "quarantine wrote to mind.db"
        # No proposal minted for a quarantine; the store record remains.
        assert ctx.proposals() == []
        recs = cli._subject().inspect()["workspace"]["records"]
        assert any(r["id"] == rid for r in recs)
        assert "synthetic prompt-9999 residue" not in [
            x.first_person for x in cli._subject().workspace.view().experiences]


def test_quarantine_requires_reason_and_existing_record():
    with CliOnTmp() as ctx:
        rid = ctx.plant("some residue")
        rc, _ = ctx.run("consolidate", "--quarantine", rid)
        assert rc == 1
        rc, out = ctx.run("consolidate", "--quarantine", "experience-4242",
                          "--reason", "nope")
        assert rc == 1 and "no such record" in out, out
        assert ctx.archive_entries() == []


# -- scale guard ------------------------------------------------------------------

def test_comparison_cap_is_reported_not_silent():
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        recs, tick = C.load_records(Path(cli.DB))
        journal = C.load_journal(Path(cli.PROPOSALS))
        report = C.scan(recs, tick, journal, max_pairs=1)
        assert report["stats"]["capped"] is True, report["stats"]
        assert report["stats"]["pairs"] >= 1


# -- misc --------------------------------------------------------------------------

def test_mutually_exclusive_actions():
    with CliOnTmp() as ctx:
        rc, out = ctx.run("consolidate", "--list", "--accept", "1")
        assert rc == 1 and "pick one action" in out, out


def test_accept_unknown_id():
    with CliOnTmp() as ctx:
        rc, out = ctx.run("consolidate", "--accept", "4242")
        assert rc == 1 and "no such proposal" in out, out
        rc, out = ctx.run("consolidate", "--accept", "notanid")
        assert rc == 1 and "not a proposal id" in out, out


def test_same_sequence_unit():
    assert C._same_sequence(["a", "call", "b"], ["a", "call", "b"])
    assert not C._same_sequence(["a", "call", "b"], ["b", "call", "a"])
    assert not C._same_sequence(["x"], ["y"])   # no shared tokens
    assert C._same_sequence(["a", "b", "a"], ["a", "b", "a"])
    assert not C._same_sequence(["a", "b", "a"], ["a", "a", "b"])


def test_accept_idempotent_retry_after_partial_apply():
    """A crash between the archive write and the journal flip must not
    wedge the proposal: retry finishes the flip, archives nothing twice."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        b = ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        # Simulate the crash: loser archived under this proposal, journal
        # flip never happened.
        C._archive_entry(Path(cli.ARCHIVE), b, 0, "dedup", p["reason"],
                         "The garden gate was left open again this morning!",
                         p["id"])
        assert ctx.journal()["proposals"][str(p["id"])]["status"] == "pending"
        rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
        assert rc == 0, out
        j = ctx.journal()
        assert j["proposals"][str(p["id"])]["status"] == "accepted"
        assert len(ctx.archive_entries()) == 1, "double-archived on retry"


def test_init_force_resets_consolidation_sidecars():
    """Reseed recycles record ids: stale proposals, archive reasons, and
    availability exclusions must not attach to the new incarnation."""
    with CliOnTmp() as ctx:
        b = ctx.plant("The garden gate was left open again this morning!")
        ctx.plant("The garden gate was left open again this morning.")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        assert ctx.run("consolidate", "--accept", str(p["id"]))[0] == 0
        assert (Path(cli.ARCHIVE) / "availability.json").exists()
        rc, _ = ctx.run("init", "--force")
        assert rc == 0
        assert ctx.journal() is None or ctx.journal()["proposals"] == {}
        assert not (Path(cli.ARCHIVE) / "availability.json").exists()
        assert not (Path(cli.ARCHIVE) / "memories.jsonl").exists()
        # The recycled ids are fully visible again in the new incarnation:
        # re-planting to the previously-archived id must surface in views.
        ctx.plant("a fresh record one")
        recycled = ctx.plant("a fresh record two")  # reuses the archived id
        texts = [x.first_person for x in cli._subject().workspace.view().experiences]
        assert "a fresh record two" in texts, texts


def test_live_store_untouched():
    if not LIVE_DB.exists():
        return
    digest = sha(LIVE_DB.read_bytes())
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        assert ctx.run("consolidate")[0] == 0
        (p,) = ctx.proposals()
        assert ctx.run("consolidate", "--accept", str(p["id"]))[0] == 0
        assert ctx.run("consolidate", "--quarantine",
                       ctx.plant("residue"), "--reason", "r")[0] == 0
    assert sha(LIVE_DB.read_bytes()) == digest, \
        "live mind.db changed during synthetic consolidation tests"


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
