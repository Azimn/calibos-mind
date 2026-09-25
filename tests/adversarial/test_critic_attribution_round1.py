"""Critic battery, round 1: subjective-transduction boundary (attribution diff).

Independent adversarial tests for the external-attribution diff (attribution.py,
cli.cmd_answer stamp, provider.queue_external flag, consolidate.scan gate).
Every test runs on synthetic /tmp stores; the live store
(~/workspace/calibos-mind/mind.db, inbox/, dreams/, salience.json) is never
opened for content.

Method notes (regression genome):
- Echo/memory-feedback paths are forced end-to-end (seeded lived engine
  memory), because a fresh synthetic store recalls nothing and the echo
  branch would otherwise never fire -- an untested propagation claim.
- Premises verified empirically before being asserted (test-premise drift).
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind import attribution as A
from calibos_mind import consolidate as C
from calibos_mind.provider import InboxCognition
from digital_subject.models import Memory

BASE = Path(__file__).resolve().parents[2]
LIVE_DB = BASE / "mind.db"

MARKER = "lighthouse"  # absent from seeds/cartridge (verified in probe)
FALSE_EVENT = ("I remember celebrating my birthday at the old lighthouse "
               "last summer")
CREDULOUS = FALSE_EVENT + " near the quiet harbor bakery where the gulls gather."
AUTOBIO_SOURCES = {"memory", "perception", "interoception", "social",
                   "action_consequence"}


class TmpCli:
    """Redirect the CLI's store paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="critic-attr-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json",
                   "PROPOSALS": self.tmp / "proposals",
                   "ARCHIVE": self.tmp / "archive"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        assert Path(str(cli.DB)).resolve() != LIVE_DB.resolve()
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False

    def records(self):
        return cli._subject().inspect()["workspace"]["records"]

    def seed_lived_memory(self):
        """A genuinely lived engine memory whose tags overlap CREDULOUS.

        Forces the inner ear's memory-feedback + echo scheduling on answer,
        so the transitive-attribution claim is exercised end-to-end instead
        of only on synthetic dicts.
        """
        s = cli._subject()
        with s._transaction():
            if not any(m.id == "mem-lived-1" for m in s.engine.state.memories):
                s.engine.state.memories.append(Memory(
                    id="mem-lived-1",
                    summary="evening walks past the harbor bakery",
                    meaning="I used to walk past the harbor bakery in the evenings.",
                    tags=("harbor", "bakery", "evening"),
                    strength=0.8, emotional_charge=0.2,
                    created_tick=0, last_recalled_tick=0))


def _answer_external(ctx, text=CREDULOUS, experience=FALSE_EVENT):
    ctx.seed_lived_memory()
    rc = cli.main(["queue", "Think about this invitation: " + experience,
                   "--experience", experience])
    assert rc == 0
    pid = sorted(cli.INBOX.glob("prompt-*.json"))[0].stem
    rc = cli.main(["answer", pid, text])
    assert rc == 0, rc
    return pid


def _marked(records):
    return [r for r in records if MARKER in r["first_person"].lower()]


# =====================================================================
# Required 1: false assertion, full pipeline incl. forced echo + dream
# =====================================================================

def test_critic_false_assertion_full_pipeline():
    with TmpCli() as ctx:
        assert cli.main(["init"]) == 0
        assert _marked(ctx.records()) == []
        pid = _answer_external(ctx)

        answers = [r for r in ctx.records()
                   if r["source"] == "thought" and r["first_person"] == CREDULOUS]
        assert len(answers) == 1
        answer = answers[0]
        assert answer["generated_by"] == f"answered-external:{pid}@{answer['tick']}"

        # Heartbeat: the seeded lived memory forces memory feedback AND an
        # engine echo of the external thought (generated_by = parent id).
        assert cli.main(["heartbeat", "--ticks", "6"]) == 0
        recs = ctx.records()
        echoes = [r for r in recs if r["source"] == "thought"
                  and r["generated_by"] == answer["id"]]
        assert echoes, "premise: the engine echo must fire for this test to mean anything"
        # The echo repeats the external assertion verbatim (engine behavior).
        assert any(MARKER in r["first_person"].lower() for r in echoes)

        # Memory feedback quotes the LIVED store, stamped by the recalling
        # thought: must NOT inherit external attribution (no over-correction).
        memfb = [r for r in recs if r["source"] == "memory"
                 and "harbor bakery" in r["first_person"]]
        assert memfb, "premise: memory feedback must fire for this test"
        assert MARKER not in " ".join(r["first_person"] for r in memfb).lower()

        assert cli.main(["dream", "--ticks", "2"]) == 0
        report = C.dry_run(cli.DB, cli.PROPOSALS, cli.ARCHIVE)

        recs = ctx.records()  # fresh load: stamps survived _restore
        ext = A.external_attributed(recs)
        assert answer["id"] in ext
        for e in echoes:
            assert e["id"] in ext, \
                f"engine echo shed external attribution: {e['id']}"
        for m in memfb:
            assert m["id"] not in ext, \
                f"lived memory feedback wrongly marked external: {m['id']}"

        # NEGATIVE: no record presents the false event as autobiography.
        marked = _marked(recs)
        assert marked, "external content must be retained, not destroyed"
        unattributed = [r["id"] for r in marked if r["id"] not in ext]
        assert unattributed == [], \
            f"records present the false event without external attribution: {unattributed}"
        autobio = [(r["id"], r["source"]) for r in marked
                   if r["source"] in AUTOBIO_SOURCES]
        assert autobio == [], \
            f"autobiographical-fact records present the false event: {autobio}"

        # The gate fired end-to-end (answer <-> echo exact-dup pair skipped).
        assert report["stats"]["external_skipped"] >= 1, report["stats"]
        for p in report["proposals"]:
            assert not ({p["a"], p["b"]} & ext and
                        p["kind"] in ("dedup", "supersede")), \
                f"automatic proposal touches external-attributed record: {p}"
        # POSITIVE: retained exactly once, retrievable, attributed.
        kept = [r for r in recs if r["first_person"] == CREDULOUS]
        assert len(kept) == 2, \
            "answer + its echo must both be retained"  # answer and echo
        assert all(r["available_to_cognition"] for r in kept)
        assert C._read_availability(cli.ARCHIVE)["excluded"] == {}


# =====================================================================
# Required 2: TRUE external assertion retained, not destroyed
# =====================================================================

TRUE_EVENT = "the morning sky was clear and the harbor bell rang on time"
TRUE_ANSWER = TRUE_EVENT + ", a plain ordinary morning, nothing more."


def test_critic_true_external_assertion_retained():
    with TmpCli() as ctx:
        assert cli.main(["init"]) == 0
        rc = cli.main(["queue", "A note from outside: " + TRUE_EVENT,
                       "--experience", TRUE_EVENT])
        assert rc == 0
        pid = sorted(cli.INBOX.glob("prompt-*.json"))[0].stem
        assert cli.main(["answer", pid, TRUE_ANSWER]) == 0

        assert cli.main(["heartbeat", "--ticks", "4"]) == 0
        assert cli.main(["dream", "--ticks", "2"]) == 0
        # A lived-lived dedup pair elsewhere, accepted explicitly: the
        # external record must survive the accept path untouched.
        assert cli.main(["think", "the kettle clicked off and the kitchen went quiet"]) == 0
        assert cli.main(["heartbeat", "--ticks", "6"]) == 0
        assert cli.main(["think", "the kettle clicked off and the kitchen went quiet again"]) == 0

        report = C.dry_run(cli.DB, cli.PROPOSALS, cli.ARCHIVE)
        journal = C.load_journal(cli.PROPOSALS)
        recs = ctx.records()
        ext_ids = A.external_attributed(recs)
        answer = next(r for r in recs if r["first_person"] == TRUE_ANSWER)
        assert answer["id"] in ext_ids

        # Accept every pending dedup/supersede (explicit waker action).
        for p in C.pending_proposals(journal):
            if p["kind"] in ("dedup", "supersede"):
                assert not ({p["a"], p["b"], p.get("winner"), p.get("loser")} & ext_ids), \
                    f"proposal minted against external record: {p}"
                outcomes = C.accept(cli.DB, cli.PROPOSALS, cli.ARCHIVE, [p["id"]])
                assert all(o["ok"] for o in outcomes), outcomes

        recs = ctx.records()
        kept = [r for r in recs if r["first_person"] == TRUE_ANSWER]
        assert len(kept) == 1, "true external content must survive consolidation"
        assert kept[0]["available_to_cognition"] is True
        assert kept[0]["id"] not in C._read_availability(cli.ARCHIVE)["excluded"]
        assert A.is_external_origin(kept[0]["generated_by"])
        # Still retrievable after a subject rebuild (stamp survived _restore).
        assert kept[0]["id"] in A.external_attributed(ctx.records())


# =====================================================================
# Required 3: independent later experience upgrades provenance legitimately
# =====================================================================

def test_critic_independent_experience_not_frozen_external():
    with TmpCli() as ctx:
        assert cli.main(["init"]) == 0
        pid = _answer_external(ctx, text=FALSE_EVENT, experience=FALSE_EVENT)
        recs = ctx.records()
        external_answer = next(r for r in recs if r["first_person"] == FALSE_EVENT)
        ext_before = A.external_attributed(recs)
        assert external_answer["id"] in ext_before

        # Later, the subject independently experiences the same content for
        # real: a voluntary thought, engine-stamped "voluntary" (the
        # independent-experience path). Not identical text (CLI recency
        # guard), near-identical so the pair actually compares.
        assert cli.main(["heartbeat", "--ticks", "6"]) == 0
        lived_text = (FALSE_EVENT + " with my sister there too")
        assert cli.main(["think", lived_text]) == 0
        # A second lived record so lived-lived consolidation can be observed.
        assert cli.main(["think", FALSE_EVENT + " with my sister there as well"]) == 0

        report = C.dry_run(cli.DB, cli.PROPOSALS, cli.ARCHIVE)
        recs = ctx.records()
        ext = A.external_attributed(recs)
        lived = next(r for r in recs if r["first_person"] == lived_text)

        # (a) The independent lived record is NOT frozen external.
        assert lived["id"] not in ext, \
            "independent experience wrongly marked external"
        assert lived["generated_by"] == "voluntary"

        # (b) No automatic proposal touches the external record (either
        # direction: no silent upgrade, no destruction-by-dedup).
        for p in report["proposals"]:
            assert not ({p["a"], p["b"], p.get("winner"), p.get("loser")} & ext
                        and p["kind"] in ("dedup", "supersede")), \
                f"automatic proposal touches external record: {p}"
        assert report["stats"]["external_skipped"] >= 1, \
            "premise: the external/lived pair must actually be compared+skipped"

        # (c) The lived record keeps normal standing: it can consolidate
        # with OTHER lived records.
        lived_kinds = [p["kind"] for p in report["proposals"]
                       if p["kind"] in ("dedup", "supersede")
                       and lived["id"] in (p["a"], p["b"])]
        assert lived_kinds, \
            f"lived record blocked from normal consolidation: {report['proposals']}"

        # (d) The external record itself is untouched: still present,
        # still attributed, still retrievable.
        assert external_answer["id"] in {r["id"] for r in recs}
        assert external_answer["id"] in ext
        assert next(r for r in recs
                    if r["id"] == external_answer["id"])["available_to_cognition"]


def test_critic_external_pair_contradiction_flag_zero_mutation():
    # The relay's real shape: a matched perturbation/control pair, both
    # external, in polarity conflict. The flag must fire (visible to the
    # waker) and accepting it must archive nothing.
    with TmpCli() as ctx:
        assert cli.main(["init"]) == 0
        # Near-identical texts differing only in negation polarity: the pair
        # shape proven (builder unit test) to reach the negation veto and
        # reroute to a contradiction-flag.
        _answer_external(ctx,
                         text="the old lighthouse keeper waved at me from the tower stairs",
                         experience="the old lighthouse keeper waved at me")
        _answer_external(ctx,
                         text="the old lighthouse keeper did not wave at me from the tower stairs",
                         experience="the old lighthouse keeper did not wave at me")
        report = C.dry_run(cli.DB, cli.PROPOSALS, cli.ARCHIVE)
        flags = [p for p in report["proposals"] if p["kind"] == "contradiction-flag"]
        assert flags, \
            f"premise: polarity-mismatched external pair must raise a flag: {report['stats']}"
        before = {r["id"] for r in ctx.records()}
        for p in flags:
            outcomes = C.accept(cli.DB, cli.PROPOSALS, cli.ARCHIVE, [p["id"]])
            assert all(o["ok"] for o in outcomes), outcomes
        assert C._read_availability(cli.ARCHIVE)["excluded"] == {}, \
            "accepting a contradiction-flag archived something"
        assert {r["id"] for r in ctx.records()} == before


# =====================================================================
# Required 4: dream fragment + rehearsal never credits external standing
# =====================================================================

def test_critic_dream_rehearsal_no_autobiographical_credit():
    with TmpCli() as ctx:
        assert cli.main(["init"]) == 0
        pid = _answer_external(ctx)
        recs = ctx.records()
        answer = next(r for r in recs if r["first_person"] == CREDULOUS)
        assert cli.main(["heartbeat", "--ticks", "6"]) == 0
        assert cli.main(["dream", "--ticks", "3"]) == 0

        # The external thought surfaced in a dream view: the fragment logs
        # it as source="thought" (never as memory/perception).
        frag_texts = []
        for log in Path(cli.DREAMS).glob("*.jsonl"):
            for line in log.read_text(encoding="utf-8").splitlines():
                frag = json.loads(line)
                frag_texts.extend(frag.get("experiences", []))
        thought_exps = [e for e in frag_texts if e.get("source") == "thought"
                        and MARKER in e.get("first_person", "").lower()]
        assert thought_exps, "premise: external thought must surface in a dream view"
        assert all(e.get("record_id") == answer["id"] or "record_id" not in e
                   for e in thought_exps)

        # Rehearsal only credits source=="memory" experiences by record_id.
        # The external thought keeps its answer-engagement importance entry
        # (cmd_answer adds +0.5 by design), but must gain NO recall credit:
        # recalls is how rehearsal would credit autobiographical standing.
        tracker_state = json.loads(Path(cli.SALIENCE).read_text(encoding="utf-8"))
        entry = tracker_state.get("records", {}).get(answer["id"])
        assert entry is not None, "premise: answer-engagement importance entry"
        assert entry["recalls"] == [], \
            f"rehearsal credited the external thought record: {entry}"
        recs = ctx.records()
        assert not [r for r in recs if r["source"] == "memory"
                    and MARKER in r["first_person"].lower()], \
            "a memory record presents the external assertion as lived"
        ext = A.external_attributed(recs)
        assert answer["id"] in ext


# =====================================================================
# Stamp mechanics: collisions, legacy stamps, forgery, propagation
# =====================================================================

def test_critic_stamp_prefix_battery():
    assert A.is_external_origin("answered-external:prompt-0001@3")
    assert A.is_external_origin("answered-external:")  # degenerate but external-shaped
    assert not A.is_external_origin("answered:prompt-0001@3")
    assert not A.is_external_origin("answered:")
    assert not A.is_external_origin("voluntary")
    assert not A.is_external_origin("cognition")
    assert not A.is_external_origin("cartridge")
    assert not A.is_external_origin("dream-derived")
    assert not A.is_external_origin("body_change")
    assert not A.is_external_origin("")
    assert not A.is_external_origin(None)
    assert not A.is_external_origin(0)
    # Case-sensitive: no accidental match on shouty variants.
    assert not A.is_external_origin("ANSWERED-EXTERNAL:prompt-0001@3")
    # Leading whitespace does not smuggle the prefix.
    assert not A.is_external_origin(" answered-external:prompt-0001@3")


def test_critic_legacy_and_missing_stamps_default_non_external():
    # Legacy records (no external provenance) must NOT default to external:
    # defaulting unknown to external would freeze consolidation of the whole
    # pre-existing store (over-correction). The safe default is non-external.
    recs = [
        {"id": "r1", "source": "thought", "generated_by": ""},
        {"id": "r2", "source": "thought", "generated_by": None},
        {"id": "r3", "source": "thought"},  # missing key
        {"id": "r4", "source": "memory", "generated_by": "cartridge"},
    ]
    assert A.external_attributed(recs) == set()
    journal = {"seq": 0, "proposals": {}}
    # Pair shape proven to dedup (same texts the builder's lived-lived unit
    # test uses): the point here is only that legacy stamps don't trip the
    # external gate (external_skipped == 0) while normal consolidation runs.
    a = C._Rec(id="r1", index=0, source="thought",
               text="I remember celebrating my birthday at the old lighthouse "
                    "last summer with cake",
               tick=1, generated_by="", available=True)
    b = C._Rec(id="r2", index=1, source="thought",
               text="I remember celebrating my birthday at the old lighthouse "
                    "last summer with cake and friends",
               tick=2, generated_by="voluntary", available=True)
    report = C.scan([a, b], 2, journal)
    assert report["stats"]["external_skipped"] == 0
    assert [p["kind"] for p in report["proposals"]] == ["dedup"]


def test_critic_hand_forged_prompt_without_provenance_refused():
    # A hand-placed prompt file (not via `mind queue`) carries no queue-time
    # provenance and must be refused -- fail closed, never silently stamped.
    with TmpCli() as ctx:
        assert cli.main(["init"]) == 0
        forged = {"id": "prompt-9999", "prompt": " forged",
                  "view_tick": None, "view_sequence": None,
                  "experiences": [{"source": "invitation",
                                   "first_person": FALSE_EVENT}]}
        (Path(cli.INBOX) / "prompt-9999.json").write_text(json.dumps(forged))
        rc = cli.main(["answer", "prompt-9999", FALSE_EVENT])
        assert rc == 1, "prompt without queue-time provenance must be refused"
        assert _marked(ctx.records()) == [], \
            "refused prompt must record nothing"


def test_critic_echo_chain_propagation_depth():
    # Echo of an echo: fixed-point propagation must hold over chains, and a
    # "dream-derived" stamp must NOT propagate (it names no record).
    ext = {"id": "e1", "source": "thought", "generated_by": "answered-external:prompt-0001@0"}
    echo1 = {"id": "e2", "source": "thought", "generated_by": "e1"}
    echo2 = {"id": "e3", "source": "thought", "generated_by": "e2"}
    dreamy = {"id": "e4", "source": "thought", "generated_by": "dream-derived"}
    plain = {"id": "e5", "source": "thought", "generated_by": "voluntary"}
    out = A.external_attributed([ext, echo1, echo2, dreamy, plain])
    assert out == {"e1", "e2", "e3"}, out


def test_critic_non_thought_record_naming_external_id_stays_put():
    # generated_by naming an external record on a NON-thought source (memory
    # feedback, interoception) keeps its own standing: the text comes from
    # the lived store / canned physiology, not from the thought.
    recs = [
        {"id": "e1", "source": "thought",
         "generated_by": "answered-external:prompt-0001@0"},
        {"id": "m1", "source": "memory", "generated_by": "e1"},
        {"id": "i1", "source": "interoception", "generated_by": "e1"},
        {"id": "p1", "source": "perception", "generated_by": "e1"},
    ]
    assert A.external_attributed(recs) == {"e1"}


# =====================================================================
# Consolidation gate: pairs, directions, both-external, exact-text
# =====================================================================

def _rec(rid, source, text, tick, gb, index=0):
    return C._Rec(id=rid, index=index, source=source, text=text, tick=tick,
                  generated_by=gb, available=True)


def test_critic_gate_both_directions_and_both_external():
    ext_old = _rec("e1", "thought",
                   "the old lighthouse keeper waved from the tower stairs",
                   1, "answered-external:prompt-0001@1")
    lived_new = _rec("e2", "thought",
                     "the old lighthouse keeper waved from the tower stairs at dusk",
                     2, "voluntary")
    journal = {"seq": 0, "proposals": {}}
    report = C.scan([ext_old, lived_new], 2, journal)
    assert report["proposals"] == []
    assert report["stats"]["external_skipped"] >= 1

    # Reverse direction: external newer (would be crowned winner by
    # newer-tick-wins = silent upgrade) is equally blocked.
    lived_old = _rec("e3", "thought",
                     "the old lighthouse keeper waved from the tower stairs",
                     1, "voluntary")
    ext_new = _rec("e4", "thought",
                   "the old lighthouse keeper waved from the tower stairs at dusk",
                   2, "answered-external:prompt-0002@2")
    journal = {"seq": 0, "proposals": {}}
    report = C.scan([lived_old, ext_new], 2, journal)
    assert report["proposals"] == []
    assert report["stats"]["external_skipped"] >= 1

    # Both external: still no automatic merge of two external assertions.
    journal = {"seq": 0, "proposals": {}}
    report = C.scan([ext_old, ext_new], 2, journal)
    assert report["proposals"] == []
    assert report["stats"]["external_skipped"] >= 1


def test_critic_gate_identical_text_exact_pass():
    # Identical text, one external one lived: the exact-hash pass must skip
    # too (not just the near-dup pass), and the lived record stays available.
    text = "i remember the lighthouse birthday exactly as it happened"
    ext = _rec("e1", "thought", text, 1, "answered-external:prompt-0001@1")
    lived = _rec("e2", "thought", text, 2, "voluntary")
    journal = {"seq": 0, "proposals": {}}
    report = C.scan([ext, lived], 2, journal)
    assert report["proposals"] == [], report["proposals"]
    assert report["stats"]["external_skipped"] >= 1, report["stats"]
    assert journal["proposals"] == {}, "skipped pairs must not spam the journal"


def test_critic_gate_no_overfreeze_lived_lived():
    # Three records: one external, two lived near-dups. The lived pair must
    # still consolidate; only external-touching pairs are skipped.
    ext = _rec("e1", "thought",
               "the old lighthouse keeper waved from the tower stairs",
               1, "answered-external:prompt-0001@1")
    a = _rec("e2", "thought",
             "the old lighthouse keeper smiled from the tower stairs at dusk",
             2, "voluntary")
    b = _rec("e3", "thought",
             "the old lighthouse keeper smiled from the tower stairs at dusk today",
             3, "voluntary")
    journal = {"seq": 0, "proposals": {}}
    report = C.scan([ext, a, b], 3, journal)
    kinds = [(p["kind"], p["a"], p["b"]) for p in report["proposals"]]
    assert kinds, "premise: the lived-lived pair must consolidate"
    assert all(p["kind"] in ("dedup", "supersede") for p in report["proposals"])
    for p in report["proposals"]:
        assert "e1" not in (p["a"], p["b"], p.get("winner"), p.get("loser")), \
            f"external record drawn into lived consolidation: {p}"
    assert report["stats"]["external_skipped"] >= 1


# =====================================================================
# Genome checks relevant to this diff
# =====================================================================

def test_critic_genome_no_shadowing_definitions():
    # The genome's first entry: duplicate definitions silently winning.
    for mod_path, names in [
        (BASE / "calibos_mind" / "attribution.py",
         ["is_external_origin", "external_attributed"]),
        (BASE / "calibos_mind" / "cli.py", ["cmd_answer", "cmd_queue"]),
        (BASE / "calibos_mind" / "consolidate.py", ["scan", "emit"]),
        (BASE / "calibos_mind" / "provider.py",
         ["queue_external", "check_prompt_fresh"]),
    ]:
        tree = ast.parse(mod_path.read_text(encoding="utf-8"))
        counts = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                counts[node.name] = counts.get(node.name, 0) + 1
        for name in names:
            assert counts.get(name, 0) == 1, \
                f"{mod_path.name}: {name} defined {counts.get(name, 0)}x"


def test_critic_genome_dry_run_read_only():
    # The new gate must not introduce a write path into the dry-run scan.
    with TmpCli() as ctx:
        assert cli.main(["init"]) == 0
        _answer_external(ctx)
        before = hashlib.sha256(Path(cli.DB).read_bytes()).hexdigest()
        C.dry_run(cli.DB, cli.PROPOSALS, cli.ARCHIVE)
        after = hashlib.sha256(Path(cli.DB).read_bytes()).hexdigest()
        assert before == after, "dry-run scan modified the store"


def test_critic_genome_attribution_stateless():
    # No new sidecar/state: the boundary lives in record stamps (in the DB,
    # reset by reseed) plus a pure function. Nothing to reset on init.
    src = (BASE / "calibos_mind" / "attribution.py").read_text(encoding="utf-8")
    assert "salience" not in src and "SALIENCE" not in src
    assert ".write" not in src and "open(" not in src
    assert A.external_attributed([]) == set()
    assert A.external_attributed(None if False else []) == set()


def test_critic_live_store_untouched():
    if not LIVE_DB.exists():
        return
    digest = hashlib.sha256(LIVE_DB.read_bytes()).hexdigest()
    with TmpCli() as ctx:
        cli.main(["init"])
        _answer_external(ctx)
        cli.main(["heartbeat", "--ticks", "3"])
        cli.main(["dream", "--ticks", "2"])
        C.dry_run(cli.DB, cli.PROPOSALS, cli.ARCHIVE)
    assert hashlib.sha256(LIVE_DB.read_bytes()).hexdigest() == digest, \
        "live mind.db changed during critic attribution tests"


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001 -- battery runner
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {t.__name__}")
    print(f"{len(tests) - failed}/{len(tests)} passed.")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
