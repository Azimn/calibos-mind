"""Critic battery, round 3 (FINAL): re-verification of the round-3 consolidation fixes.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/adversarial/test_critic_consolidate_r3.py
Plain asserts, no test runner needed (also pytest-compatible).

Cold brief for this round: the builder claims to have fixed, after the
round-2 NO SIGN-OFF,
  1. the dead `n't` catch-all in _NEGATION_RE (`\\bn't\\b` -> `n't\\b`,
     keeping the trailing `\\b`; the over-match pin on "notable" restored),
  2. negation-blind supersede: a new _STOPWORD_NEGATION_RE (not/no/nor)
     with an `sneg` count on the candidate; the deep pass vetoes supersede
     on stopword-stripped polarity mismatch and reroutes to a
     contradiction-flag. "never" is a content token and must STILL
     supersede normally,
  3. load_records() now requires the workspace.records section (missing
     workspace / non-object workspace / missing records ->
     ConsolidationError); a genuinely empty records list still scans as
     zero records honestly.

This battery attacks each fix for REALITY, not test-shape: every negation
variant, every pair phrasing, every malformed shape below is chosen to
differ from the pinned r1/r2 tests. It also re-verifies the regression
genome items most at risk from this round's edits (read-only, shadowing,
reject-without-store, id monotonicity, init --force sidecar reset,
dream/conduct isolation with an archived record present).
"""
from __future__ import annotations

import ast
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
        self.tmp = Path(tempfile.mkdtemp(prefix="critic3-cons-"))
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

    def store_tick(self):
        return cli._subject().engine.state.tick


def _pair_props(ctx, x, y):
    a = ctx.plant(x)
    b = ctx.plant(y)
    ctx.run("consolidate")
    return [p for p in ctx.proposals() if {p["a"], p["b"]} == {a, b}]


# =====================================================================
# FIX 1: the n't catch-all — novel contractions and over-match canaries
# =====================================================================

NEGATION_INVENTORY = [
    # standalone markers (explicit alternation)
    "not", "no", "never", "none", "nobody", "nothing", "neither", "nor",
    "cannot",
    # listed contractions
    "can't", "couldn't", "won't", "don't", "doesn't", "didn't", "isn't",
    "aren't", "wasn't", "weren't", "haven't", "hasn't", "hadn't",
    "wouldn't", "shouldn't", "mustn't", "needn't",
    # catch-all only: must not be droppable from the explicit list
    "ain't", "shan't", "daren't", "mightn't", "oughtn't", "usedn't",
]


def test_critic3_negation_inventory_complete():
    """No real negation word may be dropped from _NEGATION_RE. Every word
    in the inventory must count >= 1 on its own; this pins the list
    against future edits that quietly shrink it."""
    for word in NEGATION_INVENTORY:
        text = f"the quick brown fox {word} jumps today"
        assert C._negation_count(text) >= 1, \
            f"negation word dropped from _NEGATION_RE: {word!r}"


def test_critic3_catchall_mid_sentence_contractions():
    """The catch-all must fire mid-sentence, not just at string start:
    these are all unlisted in the explicit alternation."""
    for text in ("the plan ain't working out as we hoped today",
                 "I daren't go near the edge of the cliff again",
                 "you mightn't believe the story about the old mill",
                 "we oughtn't disturb the sleeping dog in the hall",
                 "they usedn't visit the farm in the cold winter"):
        assert C._negation_count(text) >= 1, \
            f"dead n't catch-all, mid-sentence: {text!r}"


def test_critic3_negation_overmatch_novel_words():
    """Word-boundary discipline on words the r2 pin didn't cover, plus
    trailing-\\b canaries: n't followed by a word char must not match."""
    for text in ("another brick in the wall",
                 "a notebook full of sketches",
                 "the notion failed to land",
                 "wont do it the old way",          # archaic, no apostrophe
                 "cantankerous old fool",            # contains 'cant' sans '
                 "the knotty problem of the day",
                 "they don'tx go there",            # trailing \b must hold
                 "isn't,"):
        want = 1 if text == "isn't," else 0
        assert C._negation_count(text) == want, \
            f"over/under-match: {text!r} -> {C._negation_count(text)}"
    # the main group's trailing \b must exist in the pattern itself
    assert ")\\b" in C._NEGATION_RE.pattern, \
        "main group lost its trailing word boundary"
    assert C._NEGATION_RE.pattern.rstrip().endswith("|n't\\b"), \
        "catch-all is not exactly n't\\b"


def test_critic3_near_dup_veto_shant_end_to_end():
    """"shan't" (catch-all only) must fire the near-dup veto end to end:
    a contradiction-flag mints and a dedup NEVER does. A supersede may
    also mint — "shan't" tokenizes to content fragments, so like "never"
    this is a direct contradiction on one subject, i.e. the specified
    belief-update path — but then it must name the NEWER record winner."""
    with CliOnTmp() as ctx:
        a = ctx.plant("we shall return to the cabin before the dark winter night")
        b = ctx.plant("we shan't return to the cabin before the dark winter night")
        ctx.run("consolidate")
        hits = [p for p in ctx.proposals() if {p["a"], p["b"]} == {a, b}]
        assert hits, "pair not evaluated at all"
        kinds = {p["kind"] for p in hits}
        assert "contradiction-flag" in kinds, \
            f"near-dup veto did not fire for shan't: {kinds}"
        assert "dedup" not in kinds, \
            f"veto bypassed via shan't: {hits}"
        for p in hits:
            if p["kind"] == "supersede":
                assert p["winner"] == b and p["loser"] == a, \
                    f"supersede named the wrong winner: {p}"


def test_critic3_near_dup_veto_cannot_pair():
    """"cannot" is an explicitly listed marker (not the catch-all) and a
    non-stopword: the veto must fire on the containment branch
    (jaccard 0.833 < 0.85, containment 1.0). Same belief-update
    invariant as the shan't test: flag present, never dedup, any
    supersede names the newer record winner."""
    with CliOnTmp() as ctx:
        a = ctx.plant("I can lift the heavy box alone today")
        b = ctx.plant("I cannot lift the heavy box alone today")
        ctx.run("consolidate")
        hits = [p for p in ctx.proposals() if {p["a"], p["b"]} == {a, b}]
        assert hits, "pair not evaluated at all"
        kinds = {p["kind"] for p in hits}
        assert "contradiction-flag" in kinds, \
            f"near-dup veto did not fire for cannot: {kinds}"
        assert "dedup" not in kinds, f"veto bypassed via cannot: {hits}"
        for p in hits:
            if p["kind"] == "supersede":
                assert p["winner"] == b and p["loser"] == a, \
                    f"supersede named the wrong winner: {p}"


def test_critic3_near_dup_veto_no_determiner_pair():
    """"no" as a determiner is genuine negation here: identical content
    tokens after stopword stripping, polarity mismatch -> flag."""
    with CliOnTmp() as ctx:
        hits = _pair_props(
            ctx,
            "there are ripe red apples left in the wicker basket today",
            "there are no ripe red apples left in the wicker basket today")
        assert hits, "pair not evaluated at all"
        assert all(p["kind"] == "contradiction-flag" for p in hits), hits


# =====================================================================
# FIX 2: the supersede-band stopword-negation veto (novel phrasings)
# =====================================================================

def test_critic3_supersede_veto_novel_phrasing():
    """Same shape as the pinned r2 failure but different words: subject
    overlap 0.667, body 0.667 (supersede band), sneg 0 vs 1 -> flag,
    never a supersede archiving the affirmative."""
    with CliOnTmp() as ctx:
        hits = _pair_props(
            ctx,
            "Mara enjoys the evening swim at the lake",
            "Mara does not enjoy the evening swim at the pond")
        assert hits, "pair not evaluated at all"
        kinds = {p["kind"] for p in hits}
        assert kinds == {"contradiction-flag"}, \
            f"expected only a flag, got {kinds}: {hits}"
        (flag,) = hits
        assert flag["winner"] is None and flag["loser"] is None, flag
        assert flag["reason"] == "", flag
        assert "negation polarity" in flag["rationale"], flag["rationale"]


def test_critic3_supersede_veto_nor_marker():
    """"nor" (not "not") must trigger the supersede veto: the pair is
    below the near-dup containment gate (0.80 < 0.92) so only the deep
    pass can save it."""
    with CliOnTmp() as ctx:
        hits = _pair_props(
            ctx,
            "Mara enjoys the evening swim at the lake",
            "Mara enjoys neither the evening swim nor the pond")
        assert hits, "pair not evaluated at all"
        assert all(p["kind"] == "contradiction-flag" for p in hits), hits


def test_critic3_supersede_veto_contraction_negation():
    """FAILING — the round-2 hazard resurrected via contraction.

    `_STOPWORD_NEGATION_RE` only matches standalone "not"/"no"/"nor".
    A contraction ("doesn't", "won't", ...) tokenizes to content
    fragments ("doesn"/"t"), so `sneg` is 0 vs 0 and the deep-pass veto
    does not fire — yet the full negation count (`neg`) sees the
    polarity flip 0 vs 1, and the pair sits squarely in the supersede
    band. The builder's comment claims contractions are "visible to the
    similarity math", but the math sees token difference, not polarity:
    it cannot distinguish a contraction-negated COMPLEMENT from a
    restatement any better than it could "do not".

    Concrete: "the quiet garden path looks beautiful in morning light"
    (older, affirmative) vs "the quiet garden path doesn't look
    beautiful in evening light" (newer, negated) — subject overlap 50%,
    body 60%, containment 0.857 (below the near-dup gate, so the
    near-dup veto never sees it). Both can be true at once (beautiful
    at dawn, not at dusk); the newer does NOT restate the older. The
    scan mints SUPERSEDE with "same subject stated again later ... older
    archived" — the exact false-rationale + archive hazard round 2
    failed, one tokenization quirk away. Accepting archives the
    affirmative: genuine information loss.

    The "won't" pair shows the same hazard WITH a near-dup flag also
    minted (containment 1.0): the flag is honest, but the accompanying
    SUPERSEDE still proposes archiving the complementary affirmative.
    """
    pairs = [
        ("the quiet garden path looks beautiful in morning light",
         "the quiet garden path doesn't look beautiful in evening light"),
        ("the morning train arrives at platform nine on time",
         "the morning train won't arrive at platform nine on time "
         "during the strike"),
    ]
    for x, y in pairs:
        with CliOnTmp() as ctx:
            hits = _pair_props(ctx, x, y)
            assert hits, f"pair not evaluated at all: {x[:40]!r}"
            archive_kinds = [p for p in hits
                             if p["kind"] in ("dedup", "supersede")]
            assert archive_kinds == [], (
                "contraction-negated complement yielded an archive-bearing "
                f"proposal with a restatement rationale: {archive_kinds} "
                f"for {x[:40]!r}")


def test_critic3_supersede_never_pair_still_supersedes():
    """"never" is a content token (sneg 0 vs 0): the pair must keep the
    ordinary supersede path — newer wins — and accept must archive the
    older with the stated reason. A veto false-positive here would be a
    real regression (belief updates must consolidate)."""
    with CliOnTmp() as ctx:
        a = ctx.plant("she will never trust strangers at night")
        b = ctx.plant("she will trust strangers at night now")
        ctx.run("consolidate")
        hits = [p for p in ctx.proposals() if {p["a"], p["b"]} == {a, b}]
        assert len(hits) == 1 and hits[0]["kind"] == "supersede", hits
        (prop,) = hits
        assert prop["winner"] == b and prop["loser"] == a, prop
        assert "stated again later" in prop["reason"], prop["reason"]
        rc, out = ctx.run("consolidate", "--accept", str(prop["id"]))
        assert rc == 0, out
        excl = json.loads((Path(cli.ARCHIVE) / "availability.json")
                          .read_text(encoding="utf-8"))["excluded"]
        assert a in excl and b not in excl, excl


# =====================================================================
# FIX 3: malformed shapes raise; honest zero survives
# =====================================================================

def test_critic3_malformed_novel_shapes_raise():
    """Shapes the r2 pin didn't cover: null workspace, string records."""
    shapes = {
        "workspace-null": json.dumps({"workspace": None,
                                      "engine": {"tick": 1}}),
        "records-string": json.dumps({"workspace": {"records": "oops"},
                                      "engine": {"tick": 1}}),
        "records-number": json.dumps({"workspace": {"records": 7},
                                      "engine": {"tick": 1}}),
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


def test_critic3_empty_records_list_scans_honestly():
    """A store that genuinely holds an EMPTY records list must scan as
    zero records — an honest zero, not an error and not a silent skip."""
    with CliOnTmp() as ctx:
        ctx.sabotage_payload(json.dumps({"workspace": {"records": []},
                                         "engine": {"tick": 5}}))
        recs, tick = C.load_records(Path(cli.DB))
        assert recs == [] and tick == 5, (recs, tick)
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        assert "0 records" in out, out
        assert ctx.proposals() == []


# =====================================================================
# REGRESSION GENOME — items most at risk from this round's edits
# =====================================================================

def test_critic3_reject_with_store_deleted():
    """--reject must work when the store file itself is gone, not just
    corrupt: best-effort tick, 0 default, proposal still rejectable."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        Path(cli.DB).unlink()
        rc, out = ctx.run("consolidate", "--reject", str(p["id"]),
                          "--reason", "store gone, still rejecting")
        assert rc == 0, out
        j = json.loads((Path(cli.PROPOSALS) / "proposals.json")
                       .read_text(encoding="utf-8"))
        prop = j["proposals"][str(p["id"])]
        assert prop["status"] == "rejected"
        assert prop["resolved_tick"] == 0


def test_critic3_proposal_ids_monotonic_never_reused():
    """Rejecting a proposal must not recycle its id: the next scan mints
    a fresh id even though id 1 is free."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p1,) = ctx.proposals()
        assert p1["id"] == 1
        rc, _ = ctx.run("consolidate", "--reject", str(p1["id"]),
                        "--reason", "testing reuse")
        assert rc == 0
        ctx.plant("The lighthouse keeper polished the brass lamp at dusk")
        ctx.plant("The lighthouse keeper polished the brass lamp at dusk!")
        ctx.run("consolidate")
        ids = sorted(p["id"] for p in ctx.proposals())
        assert 1 in ids, ids  # rejected id 1 stays in the journal
        fresh = [p for p in ctx.proposals() if p["status"] == "pending"]
        assert len(fresh) == 1 and fresh[0]["id"] == 2, ids


def test_critic3_init_force_resets_consolidation_sidecars():
    """Genome: stale proposal ids / archive reasons / availability
    exclusions must never attach to recycled record ids after reseed."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.run("consolidate")
        (p,) = ctx.proposals()
        rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
        assert rc == 0, out
        assert (Path(cli.ARCHIVE) / "availability.json").exists()
        rc, out = ctx.run("init", "--force")
        assert rc == 0, out
        assert not (Path(cli.ARCHIVE) / "availability.json").exists(), \
            "stale availability exclusions survived reseed"
        assert not (Path(cli.PROPOSALS) / "proposals.json").exists(), \
            "stale proposals survived reseed"
        # reseed is clean: a fresh scan runs and mints id 1 again
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        rc, out = ctx.run("consolidate")
        assert rc == 0, out
        (p2,) = ctx.proposals()
        assert p2["id"] == 1 and p2["status"] == "pending", p2


def test_critic3_dream_isolation_after_flag_accept():
    """Genome dream/conduct isolation, with a veto-rerouted flag accepted
    (zero mutation) and a dedup accepted (archived): dream ticks must not
    move the store tick, and the archive sidecars must be byte-identical
    before and after."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.plant("the quick brown fox is friendly and playful today")
        ctx.plant("the quick brown fox is not friendly and not playful today")
        ctx.run("consolidate")
        for p in ctx.proposals():
            rc, out = ctx.run("consolidate", "--accept", str(p["id"]))
            assert rc == 0, out
        tick_before = ctx.store_tick()
        archive_before = (Path(cli.ARCHIVE) / "memories.jsonl").read_bytes()
        avail_before = (Path(cli.ARCHIVE) / "availability.json").read_bytes()
        rc, out = ctx.run("dream", "--ticks", "2")
        assert rc == 0, out  # isolation assertions inside dream_tick held
        assert ctx.store_tick() == tick_before, "dream moved the store tick"
        assert (Path(cli.ARCHIVE) / "memories.jsonl").read_bytes() \
            == archive_before, "dream touched the archive"
        assert (Path(cli.ARCHIVE) / "availability.json").read_bytes() \
            == avail_before, "dream touched availability"


def test_critic3_consolidate_has_no_db_write_paths():
    """Static guard: consolidate.py may only ever open mind.db read-only.
    A future edit must not smuggle a write path past the mode=ro claim."""
    src = Path(C.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    connects = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(getattr(n.func, "value", None), "id", "")
                == "sqlite3"]
    assert len(connects) == 1, "expected exactly one sqlite3.connect call"
    assert "mode=ro" in src
    for forbidden in ("mode=rw", "mode=rwc", "UPDATE", "INSERT", "DELETE",
                      "CREATE", "DROP", "ALTER"):
        assert forbidden not in src, \
            f"write path token in consolidate.py: {forbidden}"


def test_critic3_no_shadowed_definitions():
    """Genome shadowing item, re-verified after this round's edits."""
    for mod in ("calibos_mind/cli.py", "calibos_mind/consolidate.py"):
        tree = ast.parse(Path(__file__).resolve().parents[2]
                         .joinpath(mod).read_text(encoding="utf-8"))
        names = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"{mod} shadowed definitions: {dupes}"


def test_critic3_scan_is_deterministic():
    """Two scans of the same store (journal reset between) must produce
    identical proposal content: kinds, pairs, winners, losers."""
    with CliOnTmp() as ctx:
        ctx.plant("The garden gate was left open again this morning.")
        ctx.plant("The garden gate was left open again this morning!")
        ctx.plant("Mara enjoys the evening swim at the lake")
        ctx.plant("Mara does not enjoy the evening swim at the pond")
        ctx.run("consolidate")
        first = sorted((p["kind"], p["a"], p["b"], p["winner"], p["loser"])
                       for p in ctx.proposals())
        (Path(cli.PROPOSALS) / "proposals.json").unlink()
        ctx.run("consolidate")
        second = sorted((p["kind"], p["a"], p["b"], p["winner"], p["loser"])
                        for p in ctx.proposals())
        assert first and first == second, (first, second)


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
