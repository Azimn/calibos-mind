"""Critic battery, round 1: deterministic consolidation (calibos_mind/consolidate.py).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/adversarial/test_critic_consolidate_r1.py
Plain asserts, no test runner needed (also pytest-compatible).

Brief: cold critic for the builder/critic loop. The builder's own suite is
tests/test_consolidate.py (27 tests, all passing at time of writing). This
battery attacks what that suite does not pin:

  A. Read-only violations (regression genome): prove mode=ro raises, prove the
     dry-run's ONLY filesystem delta is proposals/proposals.json, prove --list
     creates nothing, prove no wal/shm artifacts.
  B. Silent defaults (genome): true zero-record store, missing snapshot row.
  C. Semantic attacks: negation-blind near-dup (FAILING), non-reversal
     _same_sequence veto, containment tiny-set guard + positive control,
     short-record subject==body, supersede chain semantics, older-never-wins,
     scan determinism, confidence bounds.
  D. Archive honesty: restore path via public machinery, entry completeness,
     quarantine-of-identity-root audit pin.
  E. Accept retry-safety: real store-text mutation, two proposals one loser.
  F. Genome mechanics: no shadowed defs, init --force cache coherence,
     reject-path id monotonicity, malformed journal honest error.
  G. Cross-mutation: dream isolation with archive present, drift sees
     archived as history, provenance stamps intact.
  H. CLI coupling wart (FAILING): --reject must not require the store.
  I. Robustness (FAILING): malformed record must raise ConsolidationError,
     not raw KeyError.

FAILING tests (expected red on the builder's code as briefed):
  - test_critic_negation_pair_never_proposed_same_fact
  - test_critic_reject_does_not_require_store
  - test_critic_malformed_record_raises_consolidation_error
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind import consolidate as C


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="critic-cons-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "ARCHIVE": self.tmp / "archive",
                   "PROPOSALS": self.tmp / "proposals"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        buf = io.StringIO()
        with redirect_stdout(buf):
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

    def tree(self):
        return {str(p.relative_to(self.tmp)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.tmp.rglob("*") if p.is_file()}


# =====================================================================
# A. Read-only violations (regression genome)
# =====================================================================

def test_critic_ro_mode_actually_raises_on_write():
    """The dry-run guarantee rests on mode=ro. Prove a write raises instead
    of merely being avoided."""
    with CliOnTmp() as ctx:
        con = sqlite3.connect(f"file:{ctx.tmp / 't.db'}?mode=ro", uri=True)
        try:
            con.execute("INSERT INTO subject(id, payload) VALUES (999, 'x')")
            raise AssertionError("write succeeded under mode=ro")
        except sqlite3.OperationalError:
            pass
        finally:
            con.close()


def test_critic_dry_run_filesystem_delta_is_journal_only():
    """Snapshot the whole tree: the dry-run's ONLY delta must be
    proposals/proposals.json. No db change, no archive dir, no wal/shm."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        before = ctx.tree()
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        after = ctx.tree()
        new = sorted(set(after) - set(before))
        changed = sorted(k for k in after if k in before and after[k] != before[k])
        assert new == ["proposals/proposals.json"], (new, changed)
        assert changed == [], changed
        assert not (ctx.tmp / "archive").exists(), "archive/ created on dry-run"
        arts = [k for k in after if k.endswith(("-wal", "-shm", "-journal"))]
        assert arts == [], arts


def test_critic_dry_run_with_no_proposals_writes_nothing():
    """Seeds only: nothing meets thresholds -> zero filesystem delta."""
    with CliOnTmp() as ctx:
        before = ctx.tree()
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        assert ctx.tree() == before, "dry-run wrote files with no proposals"


def test_critic_list_creates_nothing():
    """--list is read-only: it must not create the journal file or dir."""
    with CliOnTmp() as ctx:
        rc, out = ctx.run("consolidate", "--list")
        assert rc == 0 and "no pending proposals" in out, out
        assert not (Path(cli.PROPOSALS) / "proposals.json").exists()
        assert not Path(cli.PROPOSALS).exists(), "--list created proposals/"


# =====================================================================
# B. Silent defaults (regression genome)
# =====================================================================

def _zero_record_store(ctx):
    con = sqlite3.connect(str(Path(cli.DB)))
    payload = {"engine": {"tick": 0}, "workspace": {"records": []}}
    con.execute("UPDATE subject SET payload=? WHERE id=1", (json.dumps(payload),))
    con.commit()
    con.close()


def test_critic_zero_record_store_reports_honestly():
    """A store with truly zero records (not just zero pairs) reports
    0 records / no proposals — never a quiet pass, never a crash."""
    with CliOnTmp() as ctx:
        _zero_record_store(ctx)
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        assert "0 records" in out and "no proposals" in out, out
        assert ctx.journal() is None or not ctx.proposals()


def test_critic_missing_snapshot_row_is_honest():
    """No subject snapshot row -> clean ConsolidationError, not a traceback."""
    with CliOnTmp() as ctx:
        con = sqlite3.connect(str(Path(cli.DB)))
        con.execute("DELETE FROM subject WHERE id=1")
        con.commit()
        con.close()
        rc, out = ctx.run("consolidate")
        assert rc == 1 and "no subject snapshot" in out, out


# =====================================================================
# C. Semantic attacks
# =====================================================================

def test_critic_negation_pair_never_proposed_same_fact():
    """FAILING (spec-level hazard the thresholds permit).

    'not'/'no' are stopwords, so the affirmative and the negated record have
    IDENTICAL content-token sets: jaccard 1.0, same order, union 6 >= 6.
    The scan mints a dedup proposal whose rationale claims 'same fact worded
    twice' — for opposite facts. Worse, the LONGER (negated) text wins as
    'richer', so accepting would archive the affirmative and keep the
    negation. The contradiction-flag op cannot catch it (body jaccard 1.0 is
    outside its band). Proposal-first mitigates; the threshold is still
    negation-blind.
    """
    with CliOnTmp() as ctx:
        a = ctx.plant("the quick brown fox is friendly and playful today")
        b = ctx.plant("the quick brown fox is not friendly and not playful today")
        c = ctx.plant("there are no fresh red apples left in the basket today")
        d = ctx.plant("there are fresh red apples left in the basket today")
        ctx.run("consolidate")
        for x, y in ((a, b), (c, d)):
            hits = [p for p in ctx.proposals()
                    if {p["a"], p["b"]} == {x, y} and p["kind"] == "dedup"]
            assert hits == [], (
                f"contradictory pair proposed as the same fact: {hits}")


def test_critic_same_sequence_vetoes_non_reversal_reordering():
    """The guard vetoes ANY order change, not just full reversals (spec:
    shared tokens must appear in the same relative order). Pin the
    conservative behavior."""
    with CliOnTmp() as ctx:
        a = ctx.plant("the red fox jumps high daily in the meadow")
        b = ctx.plant("the red fox daily jumps high in the meadow")
        ta, tb = C.content_tokens("the red fox jumps high daily in the meadow"), \
                 C.content_tokens("the red fox daily jumps high in the meadow")
        assert C.jaccard(set(ta), set(tb)) == 1.0  # similarity alone fires
        assert not C._same_sequence(ta, tb)        # order vetoes
        ctx.run("consolidate")
        hits = [p for p in ctx.proposals() if {p["a"], p["b"]} == {a, b}]
        assert hits == [], hits


def test_critic_containment_tiny_sets_stay_guarded():
    """containment 1.0 on a 5-token union must NOT yield a *dedup* proposal
    (the MIN_DUP_UNION noise guard); the pair may still supersede, which is
    spec-correct. The 6-token positive control must fire a dedup."""
    with CliOnTmp() as ctx:
        s1 = ctx.plant("cats chase birds daily")
        s2 = ctx.plant("cats chase birds daily now")   # containment 1.0, union 5
        l1 = ctx.plant("bright quick brown fox jumps daily")
        l2 = ctx.plant("bright quick brown fox jumps daily again")  # union 7
        ctx.run("consolidate")
        tiny = [p for p in ctx.proposals() if {p["a"], p["b"]} == {s1, s2}]
        assert tiny, "expected the tiny pair to be evaluated"
        assert all(p["kind"] != "dedup" for p in tiny), \
            f"noise guard bypassed: {tiny}"
        big = [p for p in ctx.proposals() if {p["a"], p["b"]} == {l1, l2}]
        assert len(big) == 1 and big[0]["kind"] == "dedup", big


def test_critic_short_record_subject_is_body():
    """At 4 content tokens the 'first 6' subject IS the body. Document the
    behavior: same-subject restatement still supersedes, newer wins."""
    with CliOnTmp() as ctx:
        old = ctx.plant("I love quiet morning walks")
        new = ctx.plant("love quiet evening walks")
        ctx.run("consolidate")
        hits = [p for p in ctx.proposals() if {p["a"], p["b"]} == {old, new}]
        assert len(hits) == 1 and hits[0]["kind"] == "supersede", hits
        assert hits[0]["winner"] == new and hits[0]["loser"] == old, hits


def test_critic_supersede_chain_newest_never_loser():
    """Three-way restatement chain: (old,mid) and (mid,new) proposed, the
    (old,new) pair skipped via the slated guard, the newest never a loser."""
    with CliOnTmp() as ctx:
        u1 = ctx.plant("I keep a notebook by the bed; ideas that arrive at night go into it")
        u2 = ctx.plant("I keep a notebook by the bed because the ideas that arrive "
                       "at night vanish by morning")
        u3 = ctx.plant("I keep a notebook by the bed since the ideas that arrive "
                       "at night fade by dawn")
        ctx.run("consolidate")
        mine = [p for p in ctx.proposals()
                if {p["a"], p["b"]} <= {u1, u2, u3} and p["kind"] == "supersede"]
        assert len(mine) == 2, mine
        losers = {p["loser"] for p in mine}
        assert losers == {u1, u2}, mine
        assert all(p["winner"] != u1 for p in mine)
        # the newest record is never named a loser anywhere
        assert all(p["loser"] != u3 for p in ctx.proposals())


def test_critic_older_record_never_wins_supersede():
    """Even when the older record is the richer text, the NEWER created_tick
    wins supersession — no path lets an older record supersede a newer one."""
    with CliOnTmp() as ctx:
        old = ctx.plant("I keep a notebook by the bed because the ideas that arrive "
                        "at night vanish by morning, and I annotate them at dawn")
        new = ctx.plant("I keep a notebook by the bed; ideas arrive nightly")
        ctx.run("consolidate")
        hits = [p for p in ctx.proposals()
                if {p["a"], p["b"]} == {old, new} and p["kind"] == "supersede"]
        assert len(hits) == 1, hits
        assert hits[0]["winner"] == new and hits[0]["loser"] == old, hits


def test_critic_scan_is_deterministic():
    """Two scans over identical records mint identical proposal streams
    (modulo the monotonic ids)."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.plant("I keep a notebook by the bed; ideas that arrive at night go into it")
        ctx.plant("I keep a notebook by the bed because the ideas that arrive "
                  "at night vanish by morning")
        recs, tick = C.load_records(Path(cli.DB))
        r1 = C.scan(recs, tick, {"seq": 0, "proposals": {}})
        r2 = C.scan(recs, tick, {"seq": 0, "proposals": {}})
        key = lambda p: (p["kind"], p["a"], p["b"], p["winner"], p["loser"],
                         p["rationale"], round(p["confidence"], 3))
        assert [key(p) for p in r1["proposals"]] == [key(p) for p in r2["proposals"]]
        assert r1["stats"] == r2["stats"]


def test_critic_confidence_is_computed_not_defaulted():
    """Every proposal's confidence matches its kind's formula and stays in
    [0, 1]: exact dedup 1.0, near-dup max(jaccard, containment),
    supersede/flag the body jaccard."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.plant("Mara left the garden gate open after the morning walk")
        ctx.plant("Mara left the garden gate open after the morning walk yesterday")
        ctx.plant("I keep a notebook by the bed; ideas that arrive at night go into it")
        ctx.plant("I keep a notebook by the bed because the ideas that arrive "
                  "at night vanish by morning")
        ctx.run("consolidate")
        recs, _ = C.load_records(Path(cli.DB))
        by_id = {r.id: r for r in recs}
        for p in ctx.proposals():
            assert 0.0 <= p["confidence"] <= 1.0, p
            ta = set(C.content_tokens(by_id[p["a"]].text))
            tb = set(C.content_tokens(by_id[p["b"]].text))
            if p["kind"] == "dedup" and p["confidence"] == 1.0:
                continue  # exact: hash sweep
            if p["kind"] == "dedup":
                expect = max(C.jaccard(ta, tb), C.containment(ta, tb))
            else:
                expect = C.jaccard(ta, tb)
            assert abs(p["confidence"] - round(expect, 3)) < 1e-9, p


# =====================================================================
# D. Archive honesty
# =====================================================================

def test_critic_every_archive_entry_complete():
    """100% of archived records carry a written reason + full original text,
    on both the accept and quarantine paths."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        loser = ctx.plant("The garden gate was left open again this morning!")
        q = ctx.plant("quarantine probe residue")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        assert ctx.run("consolidate", "--accept", str(p["id"]))[0] == 0
        assert ctx.run("consolidate", "--quarantine", q,
                       "--reason", "probe")[0] == 0
        entries = ctx.archive_entries()
        assert len(entries) == 2, entries
        for e in entries:
            assert set(e) >= {"record_id", "archived_tick", "op", "reason",
                              "original_text"}, e
            assert e["reason"] and e["reason"].strip(), e
            assert e["original_text"] and e["original_text"].strip(), e
            assert e["op"] in ("dedup", "supersede", "quarantine"), e


def test_critic_archive_is_restorable_via_public_machinery():
    """'Restorable' must be machinery, not a claim: removing the availability
    entry re-admits the record to views; read_archive keeps reason + text."""
    with CliOnTmp() as ctx:
        rid = ctx.plant("restore me: the moon is a test")
        assert ctx.run("consolidate", "--quarantine", rid,
                       "--reason", "probe")[0] == 0
        texts = lambda: [x.first_person
                         for x in cli._subject().workspace.view().experiences]
        assert not any("restore me" in t for t in texts())
        ap = Path(cli.ARCHIVE) / "availability.json"
        d = json.loads(ap.read_text(encoding="utf-8"))
        del d["excluded"][rid]
        ap.write_text(json.dumps(d), encoding="utf-8")
        assert any("restore me" in t for t in texts()), \
            "record not re-admitted after availability entry removed (mtime cache?)"
        archived = C.read_archive(Path(cli.ARCHIVE))
        assert len(archived) == 1 and archived[0]["record_id"] == rid
        assert archived[0]["reason"] == "probe"
        assert archived[0]["original_text"] == "restore me: the moon is a test"


def test_critic_quarantine_identity_root_is_audited():
    """Quarantine of the identity root is allowed (explicit + audited, unlike
    automatic proposals) — but it must NEVER be silent: full audit trail and
    the store record retained."""
    with CliOnTmp() as ctx:
        rc, _ = ctx.run("consolidate", "--quarantine", "experience-1",
                        "--reason", "critic probe of root handling")
        assert rc == 0  # allowed: explicit, waker-invoked, audited
        entries = ctx.archive_entries()
        assert len(entries) == 1, entries
        e = entries[0]
        assert e["record_id"] == "experience-1"
        assert e["reason"] == "critic probe of root handling"
        assert e["original_text"], "root archived without its text"
        recs = cli._subject().inspect()["workspace"]["records"]
        assert any(r["id"] == "experience-1" for r in recs), \
            "identity root deleted from the store"


# =====================================================================
# E. Accept retry-safety, harder paths
# =====================================================================

def test_critic_accept_fails_hash_check_on_real_store_mutation():
    """The proposal hash guards the STORE text, not just the journal: mutate
    the loser's record in mind.db and accept must leave the proposal pending."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        loser = ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        con = sqlite3.connect(str(Path(cli.DB)))
        row = con.execute("SELECT payload FROM subject WHERE id=1").fetchone()
        pl = json.loads(row[0])
        for r in pl["workspace"]["records"]:
            if r["id"] == loser:
                r["first_person"] = "The garden gate was left open again? edited"
        con.execute("UPDATE subject SET payload=? WHERE id=1",
                    (json.dumps(pl),))
        con.commit()
        con.close()
        rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
        assert rc == 1 and "no longer matches the proposal hash" in out, out
        assert ctx.journal()["proposals"][str(p["id"])]["status"] == "pending"
        assert ctx.archive_entries() == []


def test_critic_two_proposals_one_loser_archives_once():
    """Two pending proposals naming the same loser: the first archives, the
    second fails safe as pending — never double-archived, never burned."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        jp = Path(cli.PROPOSALS) / "proposals.json"
        j = json.loads(jp.read_text(encoding="utf-8"))
        (p1,) = list(j["proposals"].values())
        p2 = dict(p1)
        p2["id"] = j["seq"] + 1
        j["seq"] += 1
        j["proposals"][str(p2["id"])] = p2
        jp.write_text(json.dumps(j), encoding="utf-8")
        rc, _ = ctx.run("consolidate", "--accept",
                        str(p1["id"]), str(p2["id"]))
        assert rc == 1  # the second one could not apply
        assert len(ctx.archive_entries()) == 1, "double-archived"
        j = ctx.journal()
        assert j["proposals"][str(p1["id"])]["status"] == "accepted"
        assert j["proposals"][str(p2["id"])]["status"] == "pending"


def test_critic_availability_cache_has_no_stale_window():
    """The mtime-keyed exclusion cache must reflect a quarantine immediately,
    and a hand restore immediately — no granularity window."""
    with CliOnTmp() as ctx:
        rid = ctx.plant("cache probe record")
        avail = Path(cli.ARCHIVE) / "availability.json"
        assert rid not in C.excluded_ids(avail)
        assert ctx.run("consolidate", "--quarantine", rid,
                       "--reason", "probe")[0] == 0
        assert rid in C.excluded_ids(avail), "stale cache after quarantine"
        d = json.loads(avail.read_text(encoding="utf-8"))
        del d["excluded"][rid]
        avail.write_text(json.dumps(d), encoding="utf-8")
        assert rid not in C.excluded_ids(avail), "stale cache after restore"


# =====================================================================
# F. Genome mechanics
# =====================================================================

def test_critic_no_shadowed_definitions_in_cli():
    """Regression genome: duplicate defs silently win (the cmd_init case).
    Parse cli.py and assert every module-level def name is unique."""
    tree = ast.parse(Path(__file__).resolve().parents[2]
                     .joinpath("calibos_mind/cli.py").read_text(encoding="utf-8"))
    names = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"shadowed definitions: {dupes}"


def test_critic_init_force_clears_cache_coherently():
    """After init --force, no stale exclusion may be served — not from the
    files (deleted) and not from the module-level mtime cache."""
    with CliOnTmp() as ctx:
        rid = ctx.plant("stale exclusion probe")
        assert ctx.run("consolidate", "--quarantine", rid,
                       "--reason", "probe")[0] == 0
        avail = Path(cli.ARCHIVE) / "availability.json"
        assert rid in C.excluded_ids(avail)
        assert ctx.run("init", "--force")[0] == 0
        assert C.excluded_ids(avail) == frozenset(), \
            "stale exclusion served after reseed"
        # recycled ids are fully visible in the new incarnation
        fresh = ctx.plant("a brand new record")
        texts = [x.first_person
                 for x in cli._subject().workspace.view().experiences]
        assert "a brand new record" in texts, texts
        assert fresh not in C.excluded_ids(avail)


def test_critic_rejected_ids_never_reused():
    """Reject, then rescan with new duplicates: the sequence continues past
    the rejected id; the rejected proposal stays in the journal."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        assert ctx.run("consolidate", "--reject", str(p["id"]),
                       "--reason", "keep both")[0] == 0
        ctx.plant("Mara left the garden gate open after the morning walk")
        ctx.plant("Mara left the garden gate open after the morning walk yesterday")
        ctx.run("consolidate")
        ids = sorted(q["id"] for q in ctx.proposals())
        # The rejected proposal stays in the journal; every new id continues
        # the sequence past it — never reused. (Cross-pairs between the two
        # plantings may legitimately propose too; the invariant is about ids.)
        assert ids[0] == p["id"], ids
        assert all(i > p["id"] for i in ids[1:]), ids
        assert len(set(ids)) == len(ids), f"proposal id reused: {ids}"
        j = ctx.journal()
        assert j["proposals"][str(p["id"])]["status"] == "rejected"
        assert j["proposals"][str(p["id"])]["rejected_reason"] == "keep both"


def test_critic_malformed_journal_is_honest():
    """Unparseable proposals.json -> clean error, no traceback, rc 1."""
    with CliOnTmp() as ctx:
        Path(cli.PROPOSALS).mkdir(parents=True, exist_ok=True)
        (Path(cli.PROPOSALS) / "proposals.json").write_text("{not json",
                                                            encoding="utf-8")
        rc, out = ctx.run("consolidate", "--list")
        assert rc == 1 and "unreadable" in out, out


# =====================================================================
# G. Cross-mutation: dream isolation, drift, provenance
# =====================================================================

def test_critic_dream_isolation_holds_with_archive():
    """workspace.view() changed this round (availability exclusion). The dream
    path consults views: isolation assertions must still hold and the
    archived record must stay out of dream-time views."""
    with CliOnTmp() as ctx:
        rid = ctx.plant("dream probe residue that should stay archived")
        assert ctx.run("consolidate", "--quarantine", rid,
                       "--reason", "probe")[0] == 0
        rc, out = ctx.run("dream", "--ticks", "3")
        assert rc == 0, out  # isolation assertions inside dream_tick held
        texts = [x.first_person
                 for x in cli._subject().workspace.view().experiences]
        assert not any("dream probe residue" in t for t in texts)


def test_critic_drift_counts_archived_as_history():
    """Deliberate per CHANGELOG: drift still sees archived records as
    history; only cognition views consult the availability journal."""
    with CliOnTmp() as ctx:
        rid = ctx.plant("drift probe record")
        _, before = ctx.run("drift")
        assert ctx.run("consolidate", "--quarantine", rid,
                       "--reason", "probe")[0] == 0
        rc, after = ctx.run("drift")
        assert rc == 0, after
        n = lambda o: [l for l in o.splitlines() if "records" in l][0]
        assert n(before) == n(after), (n(before), n(after))


def test_critic_provenance_stamps_survive_consolidation():
    """Loop-1 provenance stamps (answered:/voluntary/dream-derived/cognition)
    flow through the scan; the identity root is still the cartridge root."""
    with CliOnTmp() as ctx:
        ctx.plant("a voluntary-style thought about the garden gate today",
                  generated_by="voluntary")
        ctx.plant("a voluntary-style thought about the garden gate today!")
        recs, _ = C.load_records(Path(cli.DB))
        assert C.identity_root_id(recs) == "experience-1", \
            "identity root moved off the cartridge seed"
        ctx.run("consolidate")
        assert ctx.proposals(), "expected a dedup proposal"


# =====================================================================
# H. CLI coupling wart — FAILING
# =====================================================================

def test_critic_reject_does_not_require_store():
    """FAILING: --reject operates on the proposal journal; the module-level
    C.reject() takes store_tick=0 by default precisely so it needs no store.
    But the CLI hard-requires mind.db (C.load_records) just to fetch a tick,
    so a waker cannot reject a proposal when the store is missing/corrupt —
    the safe disposition is blocked and the proposal wedges pending."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        Path(cli.DB).unlink()  # store gone; journal intact
        rc, out = ctx.run("consolidate", "--reject", str(p["id"]),
                          "--reason", "waker says no")
        assert rc == 0, out
        j = ctx.journal()
        assert j["proposals"][str(p["id"])]["status"] == "rejected"
        assert j["proposals"][str(p["id"])]["rejected_reason"] == "waker says no"


# =====================================================================
# I. Robustness — FAILING
# =====================================================================

def _sabotage_store(ctx, mode):
    con = sqlite3.connect(str(Path(cli.DB)))
    row = con.execute("SELECT payload FROM subject WHERE id=1").fetchone()
    if mode == "noid":
        pl = json.loads(row[0])
        pl["workspace"]["records"].append(
            {"source": "memory", "first_person": "no id here", "tick": 0})
        new_payload = json.dumps(pl)
    elif mode == "corrupt":
        new_payload = "not json{{{"
    con.execute("UPDATE subject SET payload=? WHERE id=1", (new_payload,))
    con.commit()
    con.close()


def test_critic_malformed_record_raises_consolidation_error():
    """FAILING: unparseable store content crashes load_records with raw
    exceptions (KeyError on a record missing 'id'; JSONDecodeError on a
    corrupt payload), which cmd_consolidate does not catch -> bare
    traceback. The genome demands unparseable records report honestly (a
    clean ConsolidationError naming the problem), never crash obscurely."""
    for mode in ("noid", "corrupt"):
        with CliOnTmp() as ctx:
            _sabotage_store(ctx, mode)
            try:
                C.load_records(Path(cli.DB))
            except C.ConsolidationError:
                continue  # honest
            except (KeyError, ValueError) as exc:
                raise AssertionError(
                    f"[{mode}] raw {type(exc).__name__} {exc!r} instead of "
                    f"ConsolidationError")
            raise AssertionError(f"[{mode}] malformed store silently accepted")


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed, failed = 0, []
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failed.append((t.__name__, str(exc)[:300]))
            print(f"FAIL {t.__name__}\n     {str(exc)[:300]}")
        except Exception as exc:  # noqa: BLE001 — a crash is also a failure
            failed.append((t.__name__, f"{type(exc).__name__}: {exc}"[:300]))
            print(f"ERROR {t.__name__}: {type(exc).__name__}: {str(exc)[:300]}")
        else:
            passed += 1
            print(f"PASS {t.__name__}")
    print(f"{passed} passed, {len(failed)} failed of {len(tests)}.")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
