"""Critic round-1 attacks on `mind remember` (commit 7b37c1e, merger write path).

Brief: spec + diff only. Every objection here is a failing test or a
concretely violated invariant; prose-only critique is rejected.

All fixtures live in /tmp — the live store is never touched. The CLI's
module-global paths are redirected to a tmp dir per test class.

RED (failing — must be fixed, not weakened):
  R1 test_critic_reseed_must_wipe_provenance_sidecar
  R2 test_critic_stale_provenance_not_shown_for_recycled_id

GREEN (verified genome / mutation-specific checks, all passing):
  G1 trace byte-identical + tick unchanged on remember (no-tick discipline)
  G2 importance marking is exactly the think channel (0.3, single call)
  G3 auto-extracted concepts never invent tokens absent from the text
  G4 exact-duplicate refusal scope: whitespace/case variants are NOT refused
      (spec assigns near-duplicates to the consolidation loop; documented)
  G5 no shadowing of cmd_remember
  G6 chat memories are not pinned and never become the identity root
  G7 read-only commands do not create/touch the provenance sidecar
  G8 malformed --concepts variants refused (incl. empty parts)
"""
from __future__ import annotations

import inspect
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.provenance import ProvenanceTracker


class CliOnTmp:
    """Redirect the CLI's store paths (incl. PROVENANCE) at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="critic-remember-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json",
                   "PROVENANCE": self.tmp / "provenance.json"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False


def _remember_id_from_stdout(text, *flags):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(["remember", text, *flags])
    assert rc == 0, buf.getvalue()
    # "remembered as experience-N (generated_by=chat)."
    line = buf.getvalue().strip().splitlines()[-1]
    return line.split("remembered as ")[1].split(" ")[0]


# --------------------------------------------------------------------------
# RED R1: genome — sidecar/state reset on reseed (init --force + provenance)
# --------------------------------------------------------------------------
# cmd_init --force unlinks FAMILIARITY / AMBIVALENCE / HABITS, resets
# salience + interoception, and wipes proposals + archive — because "record
# ids restart at experience-1 on reseed; the sidecars must restart too".
# PROVENANCE is missing from that list. `remember --weighed` writes
# memory-class provenance entries into the same sidecar, so a reseed leaves
# a stale entry behind that attaches to the recycled id.
def test_critic_reseed_must_wipe_provenance_sidecar():
    with CliOnTmp():
        assert cli.main(["init"]) == 0
        rid = _remember_id_from_stdout("Fact alpha.", "--weighed", "alt one")
        assert ProvenanceTracker(cli.PROVENANCE).get(rid) is not None
        assert cli.main(["init", "--force"]) == 0
        # A reseed starts with no tracker history at all (same bug class as
        # the salience/familiarity resets): no stale provenance may survive.
        assert not cli.PROVENANCE.exists(), (
            "provenance sidecar survived init --force; stale entries will "
            "attach to recycled record ids")
        assert ProvenanceTracker(cli.PROVENANCE).get(rid) is None


# --------------------------------------------------------------------------
# RED R2: the stale entry is user-visible misattribution, not just a file
# --------------------------------------------------------------------------
# After reseed, record ids restart. A fresh record reusing the id inherits
# the dead memory's provenance, and `mind review` renders it as if it were
# the new record's own decider trace.
def test_critic_stale_provenance_not_shown_for_recycled_id():
    with CliOnTmp():
        assert cli.main(["init"]) == 0
        rid = _remember_id_from_stdout("Fact alpha.", "--weighed", "alt one")
        assert cli.main(["init", "--force"]) == 0
        # A post-reseed record reuses the recycled id (ids restart).
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert cli.main(["think", "A brand new thought after reseed."]) == 0
        new_id = buf.getvalue().strip().splitlines()[-1].split("recorded as ")[1].rstrip(".")
        assert new_id == rid, (new_id, rid)  # premise: ids really do recycle
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert cli.main(["review"]) == 0
        assert "alt one" not in buf.getvalue(), (
            "review shows pre-reseed provenance under a post-reseed record:\n"
            + buf.getvalue())


# --------------------------------------------------------------------------
# GREEN G1: no-tick discipline — trace byte-identical, tick unchanged
# --------------------------------------------------------------------------
def test_critic_remember_trace_byte_identical():
    with CliOnTmp():
        assert cli.main(["init"]) == 0
        s = cli._subject()
        tick_before = s.engine.state.tick
        trace_before = json.dumps(s.inspect()["trace"], sort_keys=True)
        assert cli.main(["remember", "A quiet write, no heartbeat."]) == 0
        s = cli._subject()
        assert s.engine.state.tick == tick_before
        assert json.dumps(s.inspect()["trace"], sort_keys=True) == trace_before


# --------------------------------------------------------------------------
# GREEN G2: salience-0.3 is exactly the think channel, single marking
# --------------------------------------------------------------------------
def test_critic_remember_importance_matches_think_channel():
    with CliOnTmp():
        assert cli.main(["init"]) == 0
        assert cli.main(["remember", "An important durable fact."]) == 0
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert cli.main(["think", "A voluntary thought."]) == 0
        sal = json.loads(Path(cli.SALIENCE).read_text(encoding="utf-8"))
        imps = [e["importance"] for e in sal["records"].values()]
        # think marks 0.3 (cli.cmd_think); remember must mark exactly the
        # same channel with no double-count: deltas are 0.3, never 0.6.
        assert imps.count(0.3) == 2, imps
        assert all(i == 0.3 for i in imps), imps


# --------------------------------------------------------------------------
# GREEN G3: auto-extracted concepts never invent tokens absent from the text
# --------------------------------------------------------------------------
def test_critic_auto_concepts_never_invented():
    with CliOnTmp():
        assert cli.main(["init"]) == 0
        text = "Jay's dog Biscuit prefers the blue armchair."
        assert cli.main(["remember", text]) == 0
        rec = [r for r in cli._subject().inspect()["workspace"]["records"]
               if r["first_person"] == text][0]
        tokens = set(text.casefold().replace("'", " ").split())
        # concepts() is re.findall(r"[^\W_]+", text.casefold()); every stored
        # concept must be a literal token of the text.
        import re
        raw_tokens = set(re.findall(r"[^\W_]+", text.casefold()))
        assert set(rec["concepts"]) <= raw_tokens, rec["concepts"]
        assert len(rec["concepts"]) >= 1


# --------------------------------------------------------------------------
# GREEN G4: duplicate-refusal scope — documents the spec boundary
# --------------------------------------------------------------------------
# Spec: exact-duplicate text is refused; near-duplicates are the
# consolidation loop's business (Jaccard dedupe with the negation-polarity
# veto). Whitespace/case variants are therefore NOT refused here — this test
# pins the boundary so a future change of the spec is deliberate.
def test_critic_duplicate_refusal_is_exact_only():
    with CliOnTmp():
        assert cli.main(["init"]) == 0
        assert cli.main(["remember", "Do not record me twice."]) == 0
        # exact repeat: refused
        assert cli.main(["remember", "Do not record me twice."]) == 1
        # leading/trailing whitespace collapses via strip: refused
        assert cli.main(["remember", "   Do not record me twice.  "]) == 1
        # internal-whitespace / case variants: NOT refused here (near-dup ->
        # consolidation's Jaccard dedupe, which tokenizes casefolded).
        assert cli.main(["remember", "Do  not  record me twice."]) == 0
        assert cli.main(["remember", "DO NOT RECORD ME TWICE."]) == 0


# --------------------------------------------------------------------------
# GREEN G5: no shadowing definitions
# --------------------------------------------------------------------------
def test_critic_no_shadowed_cmd_remember():
    src = inspect.getsource(cli)
    assert src.count("def cmd_remember(") == 1


# --------------------------------------------------------------------------
# GREEN G6: chat memories are not pinned and never become the identity root
# --------------------------------------------------------------------------
def test_critic_chat_memories_not_pinned_not_identity_root():
    with CliOnTmp():
        assert cli.main(["init"]) == 0
        assert cli.main(["remember", "A chat-learned fact."]) == 0
        from calibos_mind import consolidate
        recs, _ = consolidate.load_records(cli.DB)
        root = consolidate.identity_root_id(recs)
        chat = [r for r in recs if r.generated_by == "chat"]
        assert chat, "no chat memory found"
        assert root is not None and all(r.id != root for r in chat)
        assert all(r.generated_by == "cartridge"
                   for r in recs if r.id == root)


# --------------------------------------------------------------------------
# GREEN G7: read-only commands do not create/touch the provenance sidecar
# --------------------------------------------------------------------------
def test_critic_read_paths_do_not_touch_provenance_sidecar():
    with CliOnTmp():
        assert cli.main(["init"]) == 0
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert cli.main(["review"]) == 0
            assert cli.main(["drift"]) == 0
        assert not cli.PROVENANCE.exists(), (
            "a read-only command created the provenance sidecar")


# --------------------------------------------------------------------------
# GREEN G8: malformed --concepts variants refused, nothing recorded
# --------------------------------------------------------------------------
def test_critic_concepts_malformed_variants_refused():
    with CliOnTmp():
        assert cli.main(["init"]) == 0
        for bad in ("no-comma-here", ",", "a,", ",b", "  ,  "):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["remember", f"Fact for {bad!r}.",
                               "--concepts", bad])
            assert rc == 1, (bad, buf.getvalue())
        mems = [r for r in cli._subject().inspect()["workspace"]["records"]
                if r.get("generated_by") == "chat"]
        assert mems == [], mems


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {t.__name__}: {exc}")
        else:
            print(f"PASS {t.__name__}")
    print(f"{len(tests) - failed}/{len(tests)} passed.")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
