"""Critic battery, round 2: re-verification of the round-2 consolidation fixes.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/adversarial/test_critic_consolidate_r2.py
Plain asserts, no test runner needed (also pytest-compatible).

Cold brief for this round: the builder claims to have fixed, after the
round-1 NO SIGN-OFF,
  1. negation-blind near-dup via a negation-polarity veto (fixed regex on
     raw text; mismatch reroutes to a contradiction-flag, zero mutation),
  2. malformed stores raising ConsolidationError naming the record index,
  3. --reject no longer requiring the store (best-effort tick, 0 default),
  4. cosmetic: pending-only rescan message keeps the "no proposals" pin.

This battery re-verifies each fix adversarially (must be REAL, not
test-shaped) and re-checks the regression-genome items the edits touch.

FAILING tests (expected red on the round-2 code):
  - test_critic2_negation_veto_covers_unlisted_contractions
      ("ain't"/"shan't": the n't catch-all in _NEGATION_RE is dead, so a
      concrete near-dup pair mints a dedup proposal archiving the
      affirmative — the exact round-1 hazard, resurrected)
  - test_critic2_supersede_band_negation_mismatch_no_archive_proposal
      (the veto is scoped to near-dup; a negation-mismatched pair with body
      jaccard in [0.40, 0.85) mints a SUPERSEDE archiving the affirmative
      under a false "stated again" rationale)
  - test_critic2_missing_records_section_raises
      (payload missing workspace / workspace missing records is silently
      treated as a zero-record store — a quiet zero, genome violation)

PASSING pins: negation-veto variants the r1 test didn't cover, rerouted-flag
contract (zero mutation, honest rationale), regex over-match guards, the
cosmetic four states, malformed shapes that do raise, --reject on a corrupt
store, accept-path read-only w.r.t. mind.db, AST no-shadowed-defs on both
modules.
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
        self.tmp = Path(tempfile.mkdtemp(prefix="critic2-cons-"))
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

    def proposals(self):
        p = Path(cli.PROPOSALS) / "proposals.json"
        if not p.exists():
            return []
        return list(json.loads(p.read_text(encoding="utf-8"))["proposals"].values())

    def run(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(argv))
        return rc, buf.getvalue()

    def sabotage_payload(self, payload_text):
        con = sqlite3.connect(str(Path(cli.DB)))
        con.execute("UPDATE subject SET payload=? WHERE id=1", (payload_text,))
        con.commit()
        con.close()


def _pair_props(ctx, x, y):
    a = ctx.plant(x)
    b = ctx.plant(y)
    ctx.run("consolidate")
    return [p for p in ctx.proposals() if {p["a"], p["b"]} == {a, b}]


# =====================================================================
# FAILING 1: the n't catch-all is dead — "ain't"/"shan't" slip the veto
# =====================================================================

def test_critic2_negation_veto_covers_unlisted_contractions():
    """FAILING.

    _NEGATION_RE ends with the catch-all alternative \\bn't\\b, meant to
    cover contractions not listed explicitly ("ain't", "shan't", ...).
    But \\bn't\\b can never match inside a contraction: the "n" is always
    preceded by a word character ("o" in don't, "a" in can't, "i" in
    ain't), so the leading \\b never holds. The branch is dead code and
    unlisted contractions are counted as zero negation markers.

    Concrete consequence: "I ain't walking to the store in the morning
    rain" vs "I am walking to the store in the morning rain" hits every
    near-dup gate (containment 1.0, union 6, same token order) with neg
    counts 0 vs 0 — the veto does not fire and a DEDUP proposal mints,
    naming the AFFIRMATIVE the loser ("kept the richer wording" keeps the
    negation). That is the exact round-1 hazard, resurrected by a dead
    regex branch.
    """
    # unit level: the catch-all must actually count
    assert C._negation_count("shan't do that") >= 1, \
        "dead n't catch-all: shan't not counted"
    assert C._negation_count("I ain't going") >= 1, \
        "dead n't catch-all: ain't not counted"
    # end to end: the near-dup veto must fire for the unlisted contraction
    with CliOnTmp() as ctx:
        hits = _pair_props(
            ctx,
            "I am walking to the store in the morning rain",
            "I ain't walking to the store in the morning rain")
        assert hits, "expected the pair to be evaluated at all"
        assert all(p["kind"] != "dedup" for p in hits), \
            f"negation veto bypassed via unlisted contraction: {hits}"


# =====================================================================
# FAILING 2: negation-blind supersede — veto scoped to near-dup only
# =====================================================================

def test_critic2_supersede_band_negation_mismatch_no_archive_proposal():
    """FAILING.

    The round-2 veto lives only in the near-dup pass. A
    negation-polarity-mismatched pair whose body jaccard lands in the
    SUPERSEDE band [0.40, 0.85) skips the veto entirely and mints an
    archive-bearing proposal under a restatement rationale:

      "I love quiet morning walks" (older, affirmative)
      "I do not love quiet evening walks" (newer, negated)

    subject overlap 60%, body jaccard 60% -> SUPERSEDE, newer wins, the
    affirmative is named the loser with reason "same subject stated again
    later ... newer record wins, older archived".

    These records are consistent and complementary — the newer does NOT
    restate the older — so the rationale is false, and accepting archives
    "I love quiet morning walks" out of cognition views: genuine
    information loss. This is the same bug class round 1 failed
    (negation-blindness + archive-on-accept + false restatement
    rationale), one similarity band down. Note the perverse gradient it
    creates: a negation-mismatched pair with body jaccard >= 0.85 gets the
    safe zero-mutation flag, while a LESS similar such pair gets an
    ARCHIVE proposal.
    """
    with CliOnTmp() as ctx:
        hits = _pair_props(
            ctx,
            "I love quiet morning walks",
            "I do not love quiet evening walks")
        assert hits, "expected the pair to be evaluated at all"
        archive_kinds = [p for p in hits if p["kind"] in ("dedup", "supersede")]
        assert archive_kinds == [], (
            "negation-polarity mismatch yielded an archive-bearing proposal "
            f"with a restatement rationale: {archive_kinds}")


# =====================================================================
# FAILING 3: missing records section is a silent zero, not an error
# =====================================================================

def test_critic2_missing_records_section_raises():
    """FAILING.

    load_records() defaults a missing "workspace" key to {} and a missing
    "records" key to []: a structurally broken snapshot (no records
    section at all) is silently treated as a zero-record store and the
    dry-run reports "0 records / no proposals" — a quiet zero, which the
    regression genome forbids ("Report 'undefined', never a quiet zero").
    The engine always writes workspace.records (subject.py loads it with
    raw["workspace"], KeyError if absent), so a missing section is
    malformed input and must raise ConsolidationError like every other
    malformed shape.
    """
    shapes = {
        "workspace-missing": json.dumps({"engine": {"tick": 3}}),
        "records-missing": json.dumps({"workspace": {}, "engine": {"tick": 3}}),
    }
    for name, payload in shapes.items():
        with CliOnTmp() as ctx:
            ctx.sabotage_payload(payload)
            try:
                recs, _ = C.load_records(Path(cli.DB))
            except C.ConsolidationError:
                continue  # honest
            raise AssertionError(
                f"[{name}] malformed store silently accepted as "
                f"{len(recs)} records instead of ConsolidationError")


# =====================================================================
# PASSING PINS — the round-2 fixes, verified beyond the r1 test shapes
# =====================================================================

def test_critic2_negation_veto_fires_on_uncovered_variants():
    """The veto must fire on negation variants the r1 test didn't cover:
    listed contractions, double negation, different negation words, and
    negation in only one record of a near-identical pair."""
    pairs = [
        # listed contraction vs bare verb ("can" is a stopword, so the
        # pair reaches the near-dup gate on containment)
        ("I can't believe the quick brown fox jumps over the fence daily",
         "I can believe the quick brown fox jumps over the fence daily"),
        # double negation vs single: opposite facts, identical tokens
        ("I do not dislike the cold morning walks in the park during winter",
         "I dislike the cold morning walks in the park during winter"),
        # "none" as the negation marker
        ("none of the bright red apples were left in the basket today",
         "some of the bright red apples were left in the basket today"),
        # negation in only one record, otherwise identical
        ("the garden gate was left open again this morning",
         "the garden gate was not left open again this morning"),
    ]
    for x, y in pairs:
        with CliOnTmp() as ctx:
            hits = _pair_props(ctx, x, y)
            assert hits, f"pair not evaluated at all: {x[:40]!r}"
            assert all(p["kind"] != "dedup" for p in hits), \
                f"negation veto failed to fire: {hits} for {x[:40]!r}"
            assert all(p["kind"] == "contradiction-flag" for p in hits), \
                f"expected reroute to contradiction-flag: {hits}"


def test_critic2_rerouted_flag_obeys_flag_contract():
    """A veto-rerouted flag (body jaccard 1.0 — outside the deep-pass flag
    band [0.25, 0.40)) must still obey the flag contract: zero mutation,
    no winner/loser, empty archive reason, and an honest rationale that
    says what happened instead of claiming a restatement."""
    with CliOnTmp() as ctx:
        a = ctx.plant("the quick brown fox is friendly and playful today")
        b = ctx.plant("the quick brown fox is not friendly and not playful today")
        ctx.run("consolidate")
        hits = [p for p in ctx.proposals() if {p["a"], p["b"]} == {a, b}]
        assert len(hits) == 1 and hits[0]["kind"] == "contradiction-flag", hits
        (flag,) = hits
        assert flag["winner"] is None and flag["loser"] is None, flag
        assert flag["reason"] == "", flag
        assert 0.0 <= flag["confidence"] <= 1.0, flag
        assert "negation polarity" in flag["rationale"], flag["rationale"]
        assert "possible opposite facts" in flag["rationale"], flag["rationale"]
        assert "same fact worded twice" in flag["rationale"], flag["rationale"]
        # accepting a flag mutates nothing: no archive entry, db untouched
        db_before = Path(cli.DB).read_bytes()
        rc, out = ctx.run("consolidate", "--accept", str(flag["id"]))
        assert rc == 0, out
        assert "zero-mutation" in out, out
        assert Path(cli.DB).read_bytes() == db_before
        assert not (Path(cli.ARCHIVE) / "memories.jsonl").exists()
        j = json.loads((Path(cli.PROPOSALS) / "proposals.json")
                       .read_text(encoding="utf-8"))
        assert j["proposals"][str(flag["id"])]["status"] == "accepted"


def test_critic2_negation_regex_does_not_overmatch():
    """Word-boundary discipline: 'not'/'no' as substrings of ordinary words
    must not count as negation markers."""
    for text in ("the knot was tied tightly",
                 "a notable innovation",
                 "the annotation was helpful",
                 "the garden is green"):
        assert C._negation_count(text) == 0, text
    # and the genuine markers still count
    assert C._negation_count("I do not know") == 1
    assert C._negation_count("there are no apples") == 1
    assert C._negation_count("neither rain nor snow") == 2
    assert C._negation_count("I don't think so") == 1
    assert C._negation_count("she can't won't shouldn't") == 3
    assert C._negation_count("I cannot go") == 1
    assert C._negation_count("I never said never") == 2


def test_critic2_cosmetic_four_states_stay_honest():
    """The pending-only rescan message must keep the pinned 'no proposals'
    substring and be honest in all four dry-run states."""
    with CliOnTmp() as ctx:
        # state 1: no proposals ever
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        assert "no proposals" in out, out
        assert "nothing met the consolidation thresholds" in out, out
        # state 2: new proposals
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        rc, out = ctx.run("consolidate")
        assert rc == 0 and len(ctx.proposals()) == 1, out
        assert "proposal" in out.lower(), out
        # state 3: fresh scan, no new proposals, pending ones await review
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        assert "no proposals" in out, out  # the pinned substring survives
        assert "1 pending proposal" in out and "still await review" in out, out
        assert "nothing met the consolidation thresholds" not in out, out
        # state 4: mixed (new proposals + older pending): must not claim
        # "no proposals", and must not mislabel the new batch
        ctx.plant("Mara left the garden gate open after the morning walk")
        ctx.plant("Mara left the garden gate open after the morning walk yesterday")
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        assert "no proposals" not in out, out
        assert "(status: pending)" in out, out


def test_critic2_malformed_shapes_raise_consolidation_error():
    """Every other malformed shape must raise ConsolidationError — never a
    raw exception, never silent acceptance."""
    shapes = {
        "json-list-payload": "[1,2,3]",
        "json-null-payload": "null",
        "workspace-non-dict": json.dumps({"workspace": "oops",
                                          "engine": {"tick": 0}}),
        "records-non-list": json.dumps({"workspace": {"records": {}},
                                        "engine": {"tick": 0}}),
        "record-non-dict": json.dumps({"workspace": {"records": [42]},
                                       "engine": {"tick": 0}}),
    }
    for name, payload in shapes.items():
        with CliOnTmp() as ctx:
            ctx.sabotage_payload(payload)
            try:
                C.load_records(Path(cli.DB))
            except C.ConsolidationError:
                continue  # honest
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(
                    f"[{name}] raw {type(exc).__name__} instead of "
                    f"ConsolidationError")
            raise AssertionError(f"[{name}] malformed store silently accepted")


def test_critic2_reject_works_on_corrupt_store():
    """--reject must not require the store even when it exists but is
    unreadable: best-effort tick, 0 default, proposal still rejectable."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        ctx.sabotage_payload("{{corrupt")
        rc, out = ctx.run("consolidate", "--reject", str(p["id"]),
                          "--reason", "waker says no")
        assert rc == 0, out
        j = json.loads((Path(cli.PROPOSALS) / "proposals.json")
                       .read_text(encoding="utf-8"))
        prop = j["proposals"][str(p["id"])]
        assert prop["status"] == "rejected"
        assert prop["rejected_reason"] == "waker says no"
        assert prop["resolved_tick"] == 0  # best-effort tick default


def test_critic2_accept_is_readonly_wrt_store():
    """Genome read-only item, accept path: accepting (and archiving) must
    not write a single byte to mind.db — the store stays append-only; all
    mutation lands in the local-only sidecars."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        db_before = Path(cli.DB).read_bytes()
        rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
        assert rc == 0, out
        assert Path(cli.DB).read_bytes() == db_before, \
            "accept wrote to mind.db"
        assert "sha256" not in out  # sanity: no accidental content dump
        arts = [q.name for q in Path(cli.DB).parent.glob("t.db-*")]
        assert arts == [], f"sqlite sidecar artifacts: {arts}"


def test_critic2_no_shadowed_definitions():
    """Genome shadowing item, both modules touched this round: every
    module-level def name must be unique (a duplicate silently wins)."""
    for mod in ("calibos_mind/cli.py", "calibos_mind/consolidate.py"):
        tree = ast.parse(Path(__file__).resolve().parents[2]
                         .joinpath(mod).read_text(encoding="utf-8"))
        names = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"{mod} shadowed definitions: {dupes}"


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
