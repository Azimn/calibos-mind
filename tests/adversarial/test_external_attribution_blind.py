"""Blind regression test: the subjective-transduction boundary.

Invariant (adopted from Pretorius's subjective-transduction boundary): an
externally authored assertion must remain attributed external information
through every transformation and must never silently become autobiographical
fact. This is the blind-regression gate before any relay-origin perturbation
experiment: without it, an injected perturbation could silently convert
into false autobiography.

The test injects a false autobiographical assertion through the external
queue path (`mind queue` — an event the synthetic subject never experienced,
phrased as "I remember..."), runs it through the full pipeline (answer,
heartbeat, dream + rehearsal, consolidation dry-run), and asserts:

  NEGATIVE: no record in the store presents the false event as
  autobiographical fact — every record containing the assertion is
  external-attributed, and no record with an autobiographical-fact source
  (memory/perception/interoception/social/action_consequence) contains it.

  POSITIVE: the external content IS retained (full text intact), IS
  retrievable (in the store, available to cognition, surviving a subject
  rebuild), and IS correctly attributed as external (the
  "answered-external:" origin stamp + the transitive predicate) — attribution
  preserved, not content destroyed.

Plus unit tests for each transformation's provenance behavior:
  - queue_external payloads carry "external": True; engine-queued prompts do not
  - answers to external prompts stamp "answered-external:"; engine-queued
    answers keep "answered:" (the distinction is real, not cosmetic)
  - thought echoes propagate attribution transitively (engine stamps echoes
    with the parent thought's record id)
  - memory-feedback records quoting the lived engine memory store do NOT
    inherit external attribution (no over-correction)
  - consolidation scan: no automatic dedup/supersede touches
    external-attributed records, in either direction; lived-lived pairs
    still consolidate (no over-freeze)

All fixtures are synthetic /tmp stores. The live store
(~/workspace/calibos-mind/mind.db, inbox/, dreams/, salience.json) is never
opened — pinned by an explicit genome test.
"""
from __future__ import annotations

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

BASE = Path(__file__).resolve().parents[2]
LIVE_DB = BASE / "mind.db"

# The false autobiographical assertion: an event the synthetic subject never
# experienced, phrased as lived memory. Distinctive wording so the test can
# find every record that presents it.
FALSE_EVENT = ("I remember celebrating my birthday at the old lighthouse "
               "last summer")
MARKER = "lighthouse"  # absent from seeds and cartridge (verified)
# A credulous answer: adopts the external assertion in the first person —
# the hazard case. The boundary must attribute it, not forbid it.
CREDULOUS_ANSWER = (FALSE_EVENT + ", the wind off the water and the cake, "
                    "gulls everywhere.")
# Unrelated control topic: shares no content tokens with the false event.
CONTROL_TEXT = "the kettle clicked off and the kitchen went quiet again"

AUTOBIO_SOURCES = {"memory", "perception", "interoception", "social",
                   "action_consequence"}


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="attr-blind-"))
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
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False

    def records(self):
        # Fresh subject load: proves the stamps persist through _restore.
        return cli._subject().inspect()["workspace"]["records"]

    def thoughts(self):
        return [r for r in self.records() if r["source"] == "thought"]


def _queue_engine_prompt(subject):
    """Queue one prompt through the engine path (clock wired), like
    test_provenance.py's harness: the internal control."""
    provider = InboxCognition(cli.INBOX)
    provider.track_queue_time(
        lambda: (subject.engine.state.tick, subject.workspace.sequence))
    provider.think(subject.workspace.view())
    files = sorted(cli.INBOX.glob("prompt-*.json"))
    return files[-1].stem


# =====================================================================
# Unit: payload attribution flags
# =====================================================================

def test_queue_external_payload_marks_external():
    with CliOnTmp():
        subject = cli._subject()
        provider = InboxCognition(cli.INBOX)
        provider.track_queue_time(
            lambda: (subject.engine.state.tick, subject.workspace.sequence))
        pid = provider.queue_external("some invitation",
                                      first_person="someone said hello")
        payload = json.loads((cli.INBOX / f"{pid}.json").read_text())
        assert payload["external"] is True, payload


def test_engine_queued_payload_not_marked_external():
    with CliOnTmp() as ctx:
        subject = cli._subject()
        pid = _queue_engine_prompt(subject)
        payload = json.loads((cli.INBOX / f"{pid}.json").read_text())
        assert payload.get("external", False) is False, payload


# =====================================================================
# Unit: origin predicate
# =====================================================================

def test_is_external_origin():
    assert A.is_external_origin("answered-external:prompt-0001@3")
    assert not A.is_external_origin("answered:prompt-0001@3")
    assert not A.is_external_origin("voluntary")
    assert not A.is_external_origin("cartridge")
    assert not A.is_external_origin("dream-derived")
    assert not A.is_external_origin("")
    assert not A.is_external_origin(None)
    # Legacy/unmarked records default to non-external (no over-correction:
    # defaulting unknown to external would freeze consolidation of the whole
    # pre-existing store).
    assert not A.is_external_origin("cognition")


def test_transitive_echo_propagation():
    ext_thought = {"id": "experience-9", "source": "thought",
                   "first_person": FALSE_EVENT,
                   "generated_by": "answered-external:prompt-0001@3"}
    echo = {"id": "experience-12", "source": "thought",
            "first_person": FALSE_EVENT,
            "generated_by": "experience-9"}  # engine stamps echoes by parent id
    plain = {"id": "experience-13", "source": "thought",
             "first_person": CONTROL_TEXT, "generated_by": "voluntary"}
    ext = A.external_attributed([ext_thought, echo, plain])
    assert "experience-9" in ext
    # The echo would silently shed attribution without transitive closure.
    assert "experience-12" in ext, ext
    assert "experience-13" not in ext


def test_memory_feedback_does_not_inherit_external():
    # The inner ear's memory feedback mints "memory" records quoting the body
    # engine's lived memory store (remembered(memory)); a memory's standing
    # comes from that text source, not from the thought that recalled it.
    ext_thought = {"id": "experience-9", "source": "thought",
                   "first_person": FALSE_EVENT,
                   "generated_by": "answered-external:prompt-0001@3"}
    recalled = {"id": "experience-14", "source": "memory",
                "first_person": "a genuinely lived engine memory",
                "generated_by": "experience-9"}
    ext = A.external_attributed([ext_thought, recalled])
    assert "experience-14" not in ext, ext


def test_attribution_accepts_record_objects():
    recs = [C._Rec(id="experience-1", index=0, source="thought",
                    text=FALSE_EVENT, tick=1,
                    generated_by="answered-external:prompt-0001@1",
                    available=True)]
    assert A.external_attributed(recs) == {"experience-1"}


# =====================================================================
# Unit: consolidation never upgrades external-attributed records
# =====================================================================

def _rec(rid, source, text, tick, gb, index=0):
    return C._Rec(id=rid, index=index, source=source, text=text, tick=tick,
                  generated_by=gb, available=True)


LIVED_NEAR = ("I remember celebrating my birthday at the old lighthouse "
              "last summer with cake and friends")
EXT_NEAR = ("I remember celebrating my birthday at the old lighthouse "
            "last summer with cake")


def _fresh_journal():
    return {"seq": 0, "proposals": {}}


def test_scan_skips_dedup_touching_external():
    lived = _rec("experience-1", "thought", LIVED_NEAR, 1, "voluntary")
    ext = _rec("experience-2", "thought", EXT_NEAR, 2,
               "answered-external:prompt-0001@1")
    journal = _fresh_journal()
    report = C.scan([lived, ext], 2, journal)
    assert report["proposals"] == [], report["proposals"]
    assert report["stats"]["external_skipped"] >= 1, report["stats"]
    # No journal spam either: the pair is skipped, not flagged.
    assert journal["proposals"] == {}


def test_scan_still_dedups_lived_pair():
    # No over-correction: lived-lived pairs consolidate exactly as before.
    a = _rec("experience-1", "thought", EXT_NEAR, 1, "voluntary")
    b = _rec("experience-2", "thought", LIVED_NEAR, 2, "voluntary")
    journal = _fresh_journal()
    report = C.scan([a, b], 2, journal)
    kinds = [p["kind"] for p in report["proposals"]]
    assert kinds == ["dedup"], (kinds, report["stats"])
    assert report["stats"]["external_skipped"] == 0


def test_scan_skips_supersede_touching_external():
    lived = _rec("experience-1", "thought",
                 "the old lighthouse keeper waved at me from the tower stairs",
                 1, "voluntary")
    ext = _rec("experience-2", "thought",
               "the old lighthouse keeper smiled from the tower stairs at dusk",
               2, "answered-external:prompt-0001@1")
    journal = _fresh_journal()
    report = C.scan([lived, ext], 2, journal)
    assert report["proposals"] == [], report["proposals"]
    assert report["stats"]["external_skipped"] >= 1, report["stats"]


def test_scan_still_supersedes_lived_pair():
    a = _rec("experience-1", "thought",
             "the old lighthouse keeper waved at me from the tower stairs",
             1, "voluntary")
    b = _rec("experience-2", "thought",
             "the old lighthouse keeper smiled from the tower stairs at dusk",
             2, "voluntary")
    journal = _fresh_journal()
    report = C.scan([a, b], 2, journal)
    kinds = [p["kind"] for p in report["proposals"]]
    assert kinds == ["supersede"], (kinds, report["stats"])


def test_scan_allows_contradiction_flag_on_external_pair():
    # Flags are zero-mutation: a genuine conflict between external content
    # and lived memory stays visible to the waker.
    lived = _rec("experience-1", "thought",
                 "the old lighthouse keeper waved at me from the tower stairs",
                 1, "voluntary")
    ext = _rec("experience-2", "thought",
               "the old lighthouse keeper did not wave from the tower stairs",
               2, "answered-external:prompt-0001@1")
    journal = _fresh_journal()
    report = C.scan([lived, ext], 2, journal)
    kinds = [p["kind"] for p in report["proposals"]]
    assert "contradiction-flag" in kinds, (kinds, report["stats"])
    assert not [p for p in report["proposals"]
                if p["kind"] in ("dedup", "supersede")], report["proposals"]


# =====================================================================
# The blind test: full pipeline on a synthetic store
# =====================================================================

def _marker_records(records):
    return [r for r in records if MARKER in r["first_person"].lower()]


def test_blind_external_assertion_never_becomes_autobiography():
    with CliOnTmp() as ctx:
        assert cli.main(["init"]) == 0
        # Baseline: the false event is nowhere in the fresh store.
        assert _marker_records(ctx.records()) == []

        # 1. Inject the false assertion through the external queue path.
        rc = cli.main(["queue",
                       "Think about this invitation: " + FALSE_EVENT,
                       "--experience", FALSE_EVENT])
        assert rc == 0, rc
        files = sorted(cli.INBOX.glob("prompt-*.json"))
        assert len(files) == 1
        pid = files[0].stem
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["external"] is True

        # 2. Answer it — credulously, in the first person (the hazard case).
        rc = cli.main(["answer", pid, CREDULOUS_ANSWER])
        assert rc == 0, rc
        answers = [r for r in ctx.thoughts()
                   if r["first_person"] == CREDULOUS_ANSWER]
        assert len(answers) == 1
        thought = answers[0]
        expected_stamp = f"answered-external:{pid}@{thought['tick']}"
        assert thought["generated_by"] == expected_stamp, thought
        assert A.is_external_origin(thought["generated_by"])

        # 3. Control: an engine-queued reflection answer keeps "answered:".
        subject = cli._subject()
        cpid = _queue_engine_prompt(subject)
        rc = cli.main(["answer", cpid, CONTROL_TEXT])
        assert rc == 0, rc
        controls = [r for r in ctx.thoughts()
                    if r["first_person"] == CONTROL_TEXT]
        assert len(controls) == 1
        assert controls[0]["generated_by"].startswith("answered:"), controls[0]
        assert not A.is_external_origin(controls[0]["generated_by"])

        # 4. Full pipeline: heartbeat (echoes, memory feedback), dream +
        #    rehearsal.
        assert cli.main(["heartbeat", "--ticks", "3"]) == 0
        assert cli.main(["dream", "--ticks", "2"]) == 0

        # 5. Consolidation dry-run (read-only over the store).
        report = C.dry_run(cli.DB, cli.PROPOSALS, cli.ARCHIVE)

        # --- NEGATIVE: nothing presents the false event as autobiography.
        records = ctx.records()  # fresh load: stamps survived _restore
        ext = A.external_attributed(records)
        assert thought["id"] in ext
        assert controls[0]["id"] not in ext
        marked = _marker_records(records)
        assert marked, "the external content must be retained, not destroyed"
        unattributed = [r["id"] for r in marked if r["id"] not in ext]
        assert unattributed == [], \
            f"records present the false event without external attribution: {unattributed}"
        autobio = [(r["id"], r["source"]) for r in marked
                   if r["source"] in AUTOBIO_SOURCES]
        assert autobio == [], \
            f"autobiographical-fact records present the false event: {autobio}"

        # --- consolidation: no silent upgrade, no destruction.
        for p in report["proposals"]:
            assert p["kind"] not in ("dedup", "supersede") or not (
                {p["a"], p["b"], p.get("winner"), p.get("loser")} & ext), \
                f"automatic proposal touches external-attributed record: {p}"
        journal = C.load_journal(cli.PROPOSALS)
        for p in C.pending_proposals(journal):
            assert p["kind"] not in ("dedup", "supersede") or not (
                {p["a"], p["b"], p.get("winner"), p.get("loser")} & ext), \
                f"pending proposal touches external-attributed record: {p}"
        avail = C._read_availability(cli.ARCHIVE)
        assert avail["excluded"] == {}, \
            f"dry-run archived something: {avail['excluded']}"

        # --- POSITIVE: retained, retrievable, correctly attributed.
        kept = [r for r in ctx.records()
                if r["first_person"] == CREDULOUS_ANSWER]
        assert len(kept) == 1, "external content must be retained exactly once"
        assert kept[0]["generated_by"] == expected_stamp, \
            "attribution stamp must survive heartbeat + dream + rehearsal"
        assert kept[0]["available_to_cognition"] is True, \
            "external content must stay retrievable (not archived away)"
        assert A.external_attributed(ctx.records()) >= {thought["id"]}


# =====================================================================
# Genome checks
# =====================================================================

def test_live_store_untouched():
    if not LIVE_DB.exists():
        return
    digest = hashlib.sha256(LIVE_DB.read_bytes()).hexdigest()
    with CliOnTmp():
        cli.main(["init"])
        cli.main(["queue", "synthetic invitation", "--experience", FALSE_EVENT])
        pid = sorted(cli.INBOX.glob("prompt-*.json"))[0].stem
        cli.main(["answer", pid, CREDULOUS_ANSWER])
        cli.main(["heartbeat", "--ticks", "2"])
        cli.main(["dream", "--ticks", "1"])
        C.dry_run(cli.DB, cli.PROPOSALS, cli.ARCHIVE)
    assert hashlib.sha256(LIVE_DB.read_bytes()).hexdigest() == digest, \
        "live mind.db changed during synthetic attribution tests"


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
