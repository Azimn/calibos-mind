"""Tests for expectation-closure notes (domain 18: prospective memory).

Spec: research/specs/2026-10-13-expectation-closure-note.md —
calibos_mind/inbox_expectations.py.
All fixtures live in /tmp — the live store is never touched.

When sync confirms an expectation with outcome "lapsed" AND its
confidence is at the floor (0.15 — sustained neglect, streak >= 7), it
mints exactly one temporal record noting the plan's death: expectation
id, proposition summary, created/due ticks, final streak and confidence,
outcome "lapsed". Lapses above the floor confirm silently; other
outcomes never mint; read-only commands and dream ticks never mint.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_expectation_closure_note.py
Plain asserts, no test runner needed (also pytest-compatible).
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind import consolidate
from calibos_mind.inbox_expectations import (
    CONFIDENCE,
    CONFIDENCE_FLOOR,
    GRIEF_TICKS,
    POLICY_FILE_NAME,
    TTL_TICKS,
    _add_closed,
    _read_closed,
    _read_streak,
    _write_streak,
    closure_note_text,
    expectation_id,
    is_closure_note_text,
    mint_closure_note,
    parse_closure_note_text,
    resolve,
    sync,
)

BASE = Path(__file__).resolve().parents[1]


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree (all 12, like the
    consolidation tests — cmd_init clears ARCHIVE/PROPOSALS, so they must
    never point at the live dirs)."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="eclose-cli-"))
        self.saved = {}

    def __enter__(self):
        # 2026-10-21: BASE redirected too — the reseed journal stash is
        # written under BASE/journal-stash, which must never land in the
        # live checkout during a routine test run.
        targets = {"BASE": self.tmp,
                   "DB": self.tmp / "t.db",
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
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(argv))
        return rc, buf.getvalue()


def _policy_path() -> Path:
    return Path(cli.INBOX).parent / POLICY_FILE_NAME


def _write_prompt(pid, view_tick, view_sequence=0, supersedes=None):
    payload = {
        "id": pid,
        "prompt": "test prompt",
        "view_tick": view_tick,
        "view_sequence": view_sequence,
        "experiences": [
            {"source": "invitation", "first_person": "test invitation"}],
    }
    if supersedes is not None:
        payload["supersedes"] = supersedes
    (Path(cli.INBOX) / f"{pid}.json").write_text(
        json.dumps(payload), encoding="utf-8")


def _expectations():
    return cli._subject().continuity.state.expectations


def _expect_dict():
    return {eid: asdict(e) for eid, e in _expectations().items()}


def _tick():
    return cli._subject().engine.state.tick


def _advance_ticks(n):
    with redirect_stdout(io.StringIO()):
        for _ in range(n):
            cli._run_tick(cli._subject())


def _closure_notes(subject=None):
    sub = subject or cli._subject()
    return [r for r in sub.workspace.records
            if r.source == "temporal" and r.generated_by == "cognition"
            and is_closure_note_text(r.first_person)]


def _lapse_via_sync(pid):
    """Register pid at floor confidence, expire it unanswered, backdate
    resolved_tick past the grief window, then run one direct sync: the
    sync's own lapse scan confirms it (and mints, when the floor gate
    holds). No heartbeats run, so the engine queues no prompts of its
    own — the scenario is isolated and fast. Returns the settled
    expectation."""
    _write_streak(_policy_path(), 7)  # 0.6 * 0.8**7 < 0.15 -> floor
    _write_prompt(pid, view_tick=_tick() - TTL_TICKS - 1)
    sync(cli._subject(), cli.INBOX)
    assert _expectations()[expectation_id(pid)].confidence == \
        CONFIDENCE_FLOOR
    (Path(cli.INBOX) / f"{pid}.json").unlink()
    sync(cli._subject(), cli.INBOX)
    sub = cli._subject()
    target = sub.engine.state.tick - GRIEF_TICKS
    with sub._transaction():
        ex = sub.continuity.state.expectations[expectation_id(pid)]
        assert ex.status == "expired"
        # Simulate the grief window having passed: the lapse condition is
        # scan_tick - resolved_tick >= GRIEF_TICKS, evaluated by sync.
        ex.resolved_tick = target
    out = sync(cli._subject(), cli.INBOX)
    assert out["lapsed"] == [expectation_id(pid)], out
    ex = _expectations()[expectation_id(pid)]
    assert ex.status == "confirmed" and ex.outcome == "lapsed", \
        (ex.status, ex.outcome)
    return ex


# --- fitness 1: floor-confidence lapse mints exactly one note ----------------

def test_floor_lapse_mints_exactly_one_note():
    with CliOnTmp():
        ex = _lapse_via_sync("prompt-0101")
        notes = _closure_notes()
        assert len(notes) == 1, f"expected 1 closure note, got {len(notes)}"
        note = notes[0]
        assert note.source == "temporal"
        assert note.generated_by == "cognition"
        parsed = parse_closure_note_text(note.first_person)
        assert parsed is not None
        # Everything recoverable from the record text alone.
        assert parsed["expectation_id"] == "inbox:prompt-0101"
        assert parsed["outcome"] == "lapsed"
        assert parsed["created_tick"] == ex.created_tick
        assert parsed["due_tick"] == ex.due_tick
        assert parsed["confidence"] == 0.15 == CONFIDENCE_FLOOR
        assert parsed["streak"] == 8  # 7 seeded + 1 expiry this run
        # Guard persisted in the policy sidecar.
        assert _read_closed(_policy_path()) == {"inbox:prompt-0101"}
        # The note is first-person prose, not a status-field tombstone.
        assert "I owed an answer" in note.first_person


def test_note_text_parse_round_trip_and_rejections():
    event = {"expectation_id": "inbox:prompt-0101", "pid": "prompt-0101",
             "created_tick": 41, "due_tick": 44,
             "streak": 8, "confidence": 0.15}
    text = closure_note_text(event)
    parsed = parse_closure_note_text(text)
    assert parsed == {"expectation_id": "inbox:prompt-0101",
                      "pid": "prompt-0101", "created_tick": 41,
                      "due_tick": 44, "streak": 8, "confidence": 0.15,
                      "outcome": "lapsed"}
    assert closure_note_text(parsed) == text
    # Near-misses are rejected, never partially matched.
    assert not is_closure_note_text(text.replace("I owed", "I've owed"))
    assert not is_closure_note_text(
        text.replace("inbox:prompt-0101", "inbox:prompt-0102"))
    assert not is_closure_note_text(text + " ")
    assert not is_closure_note_text(" " + text)
    assert not is_closure_note_text("just some text")
    assert not is_closure_note_text(None)
    assert not is_closure_note_text(42)


def test_mint_is_idempotent_on_expectation_id():
    with CliOnTmp():
        sub = cli._subject()
        event = {"expectation_id": "inbox:prompt-0101",
                 "pid": "prompt-0101", "created_tick": 41, "due_tick": 44,
                 "streak": 8, "confidence": 0.15}
        with sub._transaction():
            first = mint_closure_note(sub, event)
            second = mint_closure_note(sub, event)
        assert first == second
        assert len(_closure_notes(sub)) == 1


def test_heartbeat_path_mints_for_floor_lapse():
    # End-to-end through real waking ticks: the heartbeat's own sync
    # lapses a floor-confidence expectation and mints its note. The
    # engine queues prompts of its own on heartbeats, so assertions are
    # scoped to our pid (an engine prompt lapsing at the floor would
    # legitimately mint too — the spec has no carve-out for it).
    with CliOnTmp():
        _write_streak(_policy_path(), 7)
        _write_prompt("prompt-0501", view_tick=_tick() - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        (Path(cli.INBOX) / "prompt-0501.json").unlink()
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0501"].status == "expired"
        _advance_ticks(GRIEF_TICKS)
        ex = _expectations()["inbox:prompt-0501"]
        assert ex.status == "confirmed" and ex.outcome == "lapsed"
        ours = [n for n in _closure_notes()
                if parse_closure_note_text(n.first_person)[
                    "expectation_id"] == "inbox:prompt-0501"]
        assert len(ours) == 1, \
            "the heartbeat lapse path must mint exactly one note"
        parsed = parse_closure_note_text(ours[0].first_person)
        assert parsed["outcome"] == "lapsed"
        assert parsed["confidence"] == CONFIDENCE_FLOOR
        assert "inbox:prompt-0501" in _read_closed(_policy_path())


# --- fitness 2: lapse above the floor mints nothing --------------------------

def test_lapse_above_floor_mints_nothing():
    with CliOnTmp():
        now = _tick()
        _write_prompt("prompt-0102", view_tick=now - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0102"].confidence == CONFIDENCE
        (Path(cli.INBOX) / "prompt-0102.json").unlink()
        sync(cli._subject(), cli.INBOX)
        sub = cli._subject()
        target = sub.engine.state.tick - GRIEF_TICKS
        with sub._transaction():
            sub.continuity.state.expectations[
                "inbox:prompt-0102"].resolved_tick = target
        out = sync(cli._subject(), cli.INBOX)
        assert out["lapsed"] == ["inbox:prompt-0102"], out
        ex = _expectations()["inbox:prompt-0102"]
        assert ex.status == "confirmed" and ex.outcome == "lapsed"
        assert _closure_notes() == [], \
            "a lapse above floor confidence must confirm silently"
        # The guard is untouched and the sidecar keeps its old shape.
        assert _read_closed(_policy_path()) == set()
        assert json.loads(
            _policy_path().read_text(encoding="utf-8")) == \
            {"expiry_streak": 1}


# --- fitness 3: non-lapsed outcomes never mint ------------------------------

def test_non_lapse_outcomes_mint_nothing():
    with CliOnTmp():
        _write_streak(_policy_path(), 7)  # floor: any wrongful mint fires
        now = _tick()
        # answered
        _write_prompt("prompt-0201", view_tick=now - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0201"].confidence == \
            CONFIDENCE_FLOOR
        resolve(cli._subject(), "prompt-0201", outcome="answered",
                inbox_dir=cli.INBOX)
        # let-pass
        _write_prompt("prompt-0202", view_tick=now - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        resolve(cli._subject(), "prompt-0202", outcome="let-pass",
                inbox_dir=cli.INBOX)
        # refused-stale-view
        _write_prompt("prompt-0203", view_tick=now - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        resolve(cli._subject(), "prompt-0203", outcome="refused-stale-view",
                inbox_dir=cli.INBOX)
        # superseded (sync confirms it, never resolves)
        _write_prompt("prompt-0204", view_tick=now - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        _write_prompt("prompt-0205", view_tick=now,
                      supersedes=["prompt-0204"])
        (Path(cli.INBOX) / "prompt-0204.json").unlink()
        out = sync(cli._subject(), cli.INBOX)
        assert out["superseded"] == ["inbox:prompt-0204"]
        assert _expectations()["inbox:prompt-0204"].outcome == "superseded"
        # expiry alone (never lapsed) mints nothing either
        _write_prompt("prompt-0206", view_tick=now - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        (Path(cli.INBOX) / "prompt-0206.json").unlink()
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0206"].status == "expired"
        assert _closure_notes() == [], \
            "only the sync confirm-lapsed branch may mint"


def test_resolve_with_lapsed_outcome_never_mints():
    # Narrow reading pinned (spec is silent on resolve): the mint path is
    # the sync confirm-lapsed branch only — resolve() never mints, even
    # handed outcome="lapsed" directly.
    with CliOnTmp():
        _write_streak(_policy_path(), 7)
        _write_prompt("prompt-0207", view_tick=_tick() - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0207"].confidence == \
            CONFIDENCE_FLOOR
        resolve(cli._subject(), "prompt-0207", outcome="lapsed",
                inbox_dir=cli.INBOX)
        assert _expectations()["inbox:prompt-0207"].outcome == "lapsed"
        assert _closure_notes() == []


# --- fitness 4: the guard holds across syncs --------------------------------

def test_second_sync_mints_nothing_and_later_lapses_still_mint():
    with CliOnTmp():
        _lapse_via_sync("prompt-0101")
        assert len(_closure_notes()) == 1
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [],
                       "lapsed": []}
        out = sync(cli._subject(), cli.INBOX)
        assert out["lapsed"] == []
        notes = _closure_notes()
        assert len(notes) == 1, "the guard must hold: no second note"
        # The guard is per-expectation, not a global latch: a later
        # floor-confidence lapse still gets its own note.
        _lapse_via_sync("prompt-0102")
        notes = _closure_notes()
        assert len(notes) == 2
        ids = {parse_closure_note_text(n.first_person)["expectation_id"]
               for n in notes}
        assert ids == {"inbox:prompt-0101", "inbox:prompt-0102"}
        assert _read_closed(_policy_path()) == \
            {"inbox:prompt-0101", "inbox:prompt-0102"}


def test_add_closed_is_merge_and_noop_when_unchanged():
    with CliOnTmp():
        policy = _policy_path()
        _write_streak(policy, 3)
        before = policy.read_bytes()
        _add_closed(policy, [])
        _add_closed(policy, ["inbox:prompt-0101"])
        assert _read_closed(policy) == {"inbox:prompt-0101"}
        assert _read_streak(policy) == 3, \
            "guard write must not clobber the streak"
        # A streak write must not clobber the guard.
        _write_streak(policy, 4)
        assert _read_closed(policy) == {"inbox:prompt-0101"}
        assert _read_streak(policy) == 4
        after = policy.read_bytes()
        _add_closed(policy, ["inbox:prompt-0101"])  # no-op: no rewrite
        assert policy.read_bytes() == after
        assert before != after  # sanity: the file really changed above


# --- fitness 5: determinism -------------------------------------------------

def test_identical_sequences_produce_identical_notes_and_sidecar():
    def _run():
        ctx = CliOnTmp()
        ctx.__enter__()
        try:
            _lapse_via_sync("prompt-0101")
            notes = _closure_notes()
            assert len(notes) == 1
            return (notes[0].first_person,
                    _policy_path().read_bytes(),
                    json.dumps(_expect_dict(), sort_keys=True))
        finally:
            ctx.__exit__()

    assert _run() == _run()


# --- fitness 6: read-only commands and dream ticks never mint ---------------

def test_readonly_and_dream_never_mint():
    with CliOnTmp() as ctx:
        _write_streak(_policy_path(), 7)
        now = _tick()
        _write_prompt("prompt-0301", view_tick=now - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        (Path(cli.INBOX) / "prompt-0301.json").unlink()
        sync(cli._subject(), cli.INBOX)
        # Make the grief window already past WITHOUT a waking tick (no
        # sync may run): the very next sync would lapse-and-mint, so any
        # note minted by the read-only/dream paths below is wrongful.
        # The tick is read before the transaction: _tick() constructs a
        # fresh subject (a second connection), which would deadlock
        # against this transaction's write lock.
        sub = cli._subject()
        target = sub.engine.state.tick - GRIEF_TICKS - 1
        with sub._transaction():
            ex = sub.continuity.state.expectations["inbox:prompt-0301"]
            ex.resolved_tick = target
        assert _expectations()["inbox:prompt-0301"].status == "expired"
        for argv in (["drift"], ["status"], ["review"], ["inbox"]):
            rc, _ = ctx.run(*argv)
            assert rc == 0, argv
        assert _closure_notes() == [], \
            "a read-only command minted a closure note"
        for _ in range(3):
            cli._subject().dream_tick()
        assert _closure_notes() == [], \
            "a dream tick minted a closure note"
        assert _read_closed(_policy_path()) == set(), \
            "a read-only/dream path touched the guard"
        # Positive control: one waking tick lapses and mints exactly one
        # note for this expectation (the engine may queue a fresh prompt
        # of its own on the tick — it cannot be stale, so the count is
        # still exact, but scope the assertion to our pid anyway).
        _advance_ticks(1)
        ex = _expectations()["inbox:prompt-0301"]
        assert ex.status == "confirmed" and ex.outcome == "lapsed"
        ours = [n for n in _closure_notes()
                if parse_closure_note_text(n.first_person)[
                    "expectation_id"] == "inbox:prompt-0301"]
        assert len(ours) == 1


# --- fitness 7: init --force wipes the guard --------------------------------

def test_init_force_wipes_guard():
    with CliOnTmp() as ctx:
        _lapse_via_sync("prompt-0101")
        assert _read_closed(_policy_path()) == {"inbox:prompt-0101"}
        rc, _ = ctx.run("init", "--force")
        assert rc == 0
        assert not _policy_path().exists(), \
            "init --force must wipe the policy sidecar including the guard"
        # A reseeded mind starts with an empty guard: a fresh floor lapse
        # mints normally.
        _lapse_via_sync("prompt-0401")
        assert len(_closure_notes()) == 1
        assert _read_closed(_policy_path()) == {"inbox:prompt-0401"}


# --- critic round-1 findings, folded in --------------------------------------
# The critic's round-1 attack battery found two defects (3 failing tests):
# (a) consolidation's near-dup/deep passes minted dedup/supersede
# proposals against two DISTINCT closure notes (templated wording defeats
# the similarity gates — the same bug class as the realization pair
# carve-out), and (b) the writer/reader contract broke for whitespace
# pids: closure_note_text could mint notes whose pid the anchored reader
# could not parse. The fixes: a _closure_note_pair carve-out in
# consolidate.py mirroring _realization_pair, and whitespace-tolerant
# non-greedy pid/eid groups in _CLOSURE_NOTE_RE. The critic's failing
# tests are folded in here as regression pins.

def test_two_distinct_closure_notes_never_supersede():
    """Two closure notes for two different lapses must never draw a
    dedup/supersede proposal: each names its own expectation id by
    construction, so "the same fact worded twice" and "newer wins" can
    never honestly apply. The exact-dup hash sweep still applies to
    byte-identical text (crash-window double-mints)."""
    with CliOnTmp():
        _lapse_via_sync("prompt-0101")
        _lapse_via_sync("prompt-0202")
        assert len(_closure_notes()) == 2
        records, tick = consolidate.load_records(cli.DB)
        journal = {"seq": 0, "proposals": {}}
        report = consolidate.scan(records, tick, journal)
        note_ids = {r.id for r in records
                    if is_closure_note_text(r.text)}
        assert len(note_ids) == 2
        bad = [p for p in report["proposals"]
               if p["kind"] in ("dedup", "supersede")
               and p.get("loser") in note_ids]
        assert not bad, (
            "consolidation slated a closure note for archival: "
            + "; ".join(f'{p["kind"]} loser={p["loser"]} '
                        f'winner={p["winner"]} :: {p["rationale"][:120]}'
                        for p in bad))


def test_space_pid_note_parseable_by_own_reader():
    """Writer/reader contract on whitespace pids: _prompt_pid accepts any
    string, so the writer can mint a note whose pid contains whitespace —
    the anchored reader must recover it, or fitness item 1
    (recoverability from record text) and the idempotent-mint backstop
    break."""
    event = {"expectation_id": "inbox:prompt 0101", "pid": "prompt 0101",
             "created_tick": 41, "due_tick": 44,
             "streak": 8, "confidence": 0.15}
    text = closure_note_text(event)
    parsed = parse_closure_note_text(text)
    assert parsed is not None, \
        "the writer minted text its own anchored reader rejects"
    assert parsed["pid"] == "prompt 0101"
    assert parsed["expectation_id"] == "inbox:prompt 0101"
    assert parsed["created_tick"] == 41 and parsed["due_tick"] == 44
    assert parsed["streak"] == 8 and parsed["confidence"] == 0.15
    assert parsed["outcome"] == "lapsed"
    assert closure_note_text(parsed) == text


def test_space_pid_lapses_end_to_end():
    """A whitespace pid lapses via the real sync path: the mint happens
    exactly once and its fields are recoverable from the record text."""
    with CliOnTmp():
        _write_streak(_policy_path(), 7)
        pid = "prompt 0101"
        payload = {"id": pid, "prompt": "x",
                   "view_tick": _tick() - TTL_TICKS - 1,
                   "experiences": []}
        (Path(cli.INBOX) / "prompt-01 01.json").write_text(
            json.dumps(payload), encoding="utf-8")
        sync(cli._subject(), cli.INBOX)
        assert _expectations()[expectation_id(pid)].confidence == \
            CONFIDENCE_FLOOR
        (Path(cli.INBOX) / "prompt-01 01.json").unlink()
        sync(cli._subject(), cli.INBOX)
        sub = cli._subject()
        with sub._transaction():
            sub.continuity.state.expectations[
                expectation_id(pid)].resolved_tick = \
                sub.engine.state.tick - GRIEF_TICKS
        out = sync(cli._subject(), cli.INBOX)
        assert out["lapsed"] == [expectation_id(pid)]
        notes = [r for r in cli._subject().workspace.records
                 if r.source == "temporal" and r.generated_by == "cognition"]
        assert len(notes) == 1, \
            "expected the mint to happen exactly once"
        parsed = parse_closure_note_text(notes[0].first_person)
        assert parsed is not None, \
            "minted a note whose fields are not recoverable from its text"
        assert parsed["pid"] == pid
        assert parsed["expectation_id"] == expectation_id(pid)


def test_paren_pid_round_trips():
    """Pids may legally contain ')' with no whitespace: the non-greedy
    pid/eid groups plus the identity cross-check must still pin the right
    parse."""
    event = {"expectation_id": "inbox:a)b", "pid": "a)b",
             "created_tick": 41, "due_tick": 44,
             "streak": 8, "confidence": 0.15}
    text = closure_note_text(event)
    parsed = parse_closure_note_text(text)
    assert parsed is not None and parsed["pid"] == "a)b"
    assert parsed["expectation_id"] == "inbox:a)b"
    assert closure_note_text(parsed) == text


def test_forged_machine_sentence_pid_rejected():
    """A spaceless pid trying to smuggle a second machine sentence into
    the prose slot must not parse as a DIFFERENT expectation."""
    pid = "x)lapsing:"
    event = {"expectation_id": f"inbox:{pid}", "pid": pid,
             "created_tick": 41, "due_tick": 44,
             "streak": 8, "confidence": 0.15}
    text = closure_note_text(event)
    parsed = parse_closure_note_text(text)
    assert parsed is not None
    assert parsed["expectation_id"] == f"inbox:{pid}", parsed
    assert parsed["pid"] == pid
