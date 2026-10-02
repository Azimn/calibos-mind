"""Tests for inbox-expectation registration (calibos_mind/inbox_expectations.py).

Fitness (research/spec-inbox-expectations-2026-09-26.md):
- empty inbox -> zero expectations; sync is a no-op (expectations dict
  untouched, no store write beyond the tick's normal one).
- fresh prompt (age < TTL) -> no registration.
- stale prompt (age >= TTL) -> exactly one pending expectation with the right
  id/proposition/due_tick/confidence; resync adds nothing more.
- after due passes, heartbeats produce "temporal" records with
  concern_links=("expectation:inbox:prompt-XXXX",) and an
  unresolved_concern cognition_trigger fires within a bounded tick window.
- cmd_answer path (consume + resolve) -> expectation "confirmed"; no further
  resurfacing of that key. --silent resolves with outcome "let-pass".
- prompt file deleted without answer -> next sync marks "expired", still
  resurfacing (still in the engine's open set).
- hand-written payload without view_tick -> skipped, no expectation.
- full existing test suite stays green (run separately).

Regression genome pins:
- read-only commands (drift) never register expectations and never write.
- dream ticks never register expectations (dream/conduct isolation).
- exactly one def sync / def resolve in the module (no shadowing defs).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_inbox_expectations.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
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
from calibos_mind.inbox_expectations import (
    CONFIDENCE,
    TTL_TICKS,
    expectation_id,
    resolve,
    sync,
)
from calibos_mind.unresolved import open_link_keys

BASE = Path(__file__).resolve().parents[1]


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree (all 7, like the
    consolidation tests — cmd_init clears ARCHIVE/PROPOSALS, so they must
    never point at the live dirs)."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="inexp-cli-"))
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


def _write_prompt(pid, view_tick, view_sequence=0, experiences=None):
    payload = {
        "id": pid,
        "prompt": "test prompt",
        "view_tick": view_tick,
        "view_sequence": view_sequence,
        "experiences": experiences or [
            {"source": "invitation", "first_person": "test invitation"}],
    }
    (Path(cli.INBOX) / f"{pid}.json").write_text(
        json.dumps(payload), encoding="utf-8")


def _expectations():
    return cli._subject().continuity.state.expectations


def _expect_dict():
    return {eid: asdict(e) for eid, e in _expectations().items()}


def _tick():
    return cli._subject().engine.state.tick


def _seq():
    return cli._subject().workspace.sequence


# --- fitness: empty inbox is a provable no-op ------------------------------

def _db_payload_bytes():
    import sqlite3
    db = sqlite3.connect(cli.DB)
    try:
        return db.execute(
            "SELECT payload FROM subject WHERE id=1").fetchone()[0].encode()
    finally:
        db.close()


def test_sync_empty_inbox_is_noop():
    with CliOnTmp() as ctx:
        sub = cli._subject()  # construction always rewrites the payload row
        assert _expect_dict() == {}
        mtime_before = Path(ctx.tmp / "t.db").stat().st_mtime_ns
        out = sync(sub, cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}
        # mtime must be checked before any helper that constructs a subject
        # (construction itself rewrites the payload row).
        assert Path(ctx.tmp / "t.db").stat().st_mtime_ns == mtime_before, \
            "sync opened a write path on an empty inbox"
        assert _expect_dict() == {}, "sync touched the expectations dict"


def test_run_tick_empty_inbox_registers_nothing():
    with CliOnTmp():
        with redirect_stdout(io.StringIO()):
            cli._run_tick(cli._subject())
        assert _expect_dict() == {}, \
            "the _run_tick -> sync hook registered something with an empty inbox"


# --- fitness: fresh vs stale ------------------------------------------------

def test_fresh_prompt_not_registered():
    with CliOnTmp():
        now = _tick()
        _write_prompt("prompt-0007", view_tick=now)  # age 0 < TTL
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}
        assert _expect_dict() == {}


def test_stale_prompt_registers_exactly_once():
    with CliOnTmp():
        now = _tick()
        vt = now - TTL_TICKS - 2
        _write_prompt("prompt-0007", view_tick=vt)
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": ["inbox:prompt-0007"], "expired": [], "superseded": [], "lapsed": []}
        ex = _expectations()["inbox:prompt-0007"]
        assert ex.id == "inbox:prompt-0007"
        assert ex.proposition == (
            f"I still owe an answer to the question queued at tick {vt} "
            f"(prompt-0007)")
        assert ex.created_tick == vt
        assert ex.due_tick == vt + TTL_TICKS
        assert ex.confidence == CONFIDENCE
        assert ex.source_record_ids == ()
        assert ex.status == "pending"
        # Resync is idempotent: one prompt, one expectation.
        out2 = sync(cli._subject(), cli.INBOX)
        assert out2 == {"registered": [], "expired": [], "superseded": [], "lapsed": []}
        assert [e for e in _expectations() if e.startswith("inbox:")] == \
            ["inbox:prompt-0007"]


def test_run_tick_hook_registers_stale_prompt():
    # The wiring itself: a waking _run_tick (not a direct sync call) must
    # register a stale prompt.
    with CliOnTmp():
        now = _tick()
        _write_prompt("prompt-0007", view_tick=now - TTL_TICKS - 1)
        with redirect_stdout(io.StringIO()):
            cli._run_tick(cli._subject())
        assert "inbox:prompt-0007" in _expectations()


# --- fail closed ------------------------------------------------------------

def test_handwritten_without_view_tick_skipped():
    with CliOnTmp():
        for pid, vt in (("prompt-0009", None), ("prompt-0010", "twelve")):
            payload = {"id": pid, "prompt": "hand-written",
                       "experiences": [{"source": "x", "first_person": "y"}]}
            if vt != "twelve":
                payload["view_tick"] = vt  # explicit null, like legacy files
            else:
                payload["view_tick"] = vt
            (Path(cli.INBOX) / f"{pid}.json").write_text(
                json.dumps(payload), encoding="utf-8")
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}
        assert _expect_dict() == {}, \
            "a prompt without a real view_tick must never be age-guessed"


def test_corrupt_prompt_file_skipped():
    with CliOnTmp():
        (Path(cli.INBOX) / "prompt-0011.json").write_text(
            "{not json", encoding="utf-8")
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}
        assert _expect_dict() == {}


# --- vanished prompt -> expired, still in the open set -----------------------

def test_vanished_prompt_marked_expired_still_open():
    with CliOnTmp():
        now = _tick()
        _write_prompt("prompt-0007", view_tick=now - TTL_TICKS - 1)
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0007"].status == "pending"
        # The only file-deleting path is InboxCognition.consume; here the
        # prompt vanished without an answer.
        (Path(cli.INBOX) / "prompt-0007.json").unlink()
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": ["inbox:prompt-0007"], "superseded": [], "lapsed": []}
        ex = _expectations()["inbox:prompt-0007"]
        assert ex.status == "expired"
        assert ex.resolved_tick is not None
        # Expired stays in the engine's open set (pending|expired): the
        # nagging continues.
        keys = open_link_keys(cli._subject().inspect())
        assert "expectation:inbox:prompt-0007" in keys


# --- answer paths ------------------------------------------------------------

def test_answer_confirms_and_stops_resurfacing():
    with CliOnTmp() as ctx:
        now, seq = _tick(), _seq()
        _write_prompt("prompt-0007", view_tick=now - TTL_TICKS - 1,
                      view_sequence=seq)
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0007"].status == "pending"
        rc, _ = ctx.run("answer", "prompt-0007", "a considered thought")
        assert rc == 0
        ex = _expectations()["inbox:prompt-0007"]
        assert ex.status == "confirmed", ex.status
        assert ex.outcome == "answered"
        assert ex.resolved_tick is not None
        # No further resurfacing of that key: bounded heartbeats through the
        # real CLI tick path must never fire unresolved_concern for it, and
        # no new temporal record may link to it.
        answer_tick = _tick()
        seen_trigger = False
        for _ in range(25):
            with redirect_stdout(io.StringIO()):
                cli._run_tick(cli._subject())
            trace = cli._subject().inspect()["trace"]
            for t in trace:
                if t["kind"] == "cognition_trigger":
                    trig = t["trigger"]
                    if (trig["kind"] == "unresolved_concern"
                            and "expectation:inbox:prompt-0007"
                            in trig.get("parents", [])):
                        seen_trigger = True
        assert not seen_trigger, \
            "a confirmed expectation kept resurfacing"
        for r in cli._subject().inspect()["workspace"]["records"]:
            if r["tick"] > answer_tick and r["source"] == "temporal":
                assert "expectation:inbox:prompt-0007" not in \
                    r.get("concern_links", ()), \
                    "temporal record still linked to a confirmed expectation"


def test_silent_answer_confirms_as_let_pass():
    with CliOnTmp() as ctx:
        now, seq = _tick(), _seq()
        _write_prompt("prompt-0003", view_tick=now - TTL_TICKS - 1,
                      view_sequence=seq)
        sync(cli._subject(), cli.INBOX)
        rc, _ = ctx.run("answer", "prompt-0003", "--silent")
        assert rc == 0
        ex = _expectations()["inbox:prompt-0003"]
        assert ex.status == "confirmed", ex.status
        assert ex.outcome == "let-pass"


# --- refused-after-consume: settled as a first-class refused category -------

def test_refused_stale_answer_confirms_as_refused_stale_view():
    # Critic round-2 defect: a prompt consumed but refused on staleness used
    # to leave its expectation pending -> expired at the next sync -> the
    # engine nagged forever about business that could never be done. Now the
    # refusal path resolves it as confirmed / "refused-stale-view".
    with CliOnTmp() as ctx:
        now, seq = _tick(), _seq()
        _write_prompt("prompt-0007", view_tick=now - TTL_TICKS - 1,
                      view_sequence=seq)
        with redirect_stdout(io.StringIO()):
            cli._run_tick(cli._subject())  # registers the expectation, and
                                           # moves the workspace past the view
        assert _expectations()["inbox:prompt-0007"].status == "pending"
        assert _seq() != seq, "the tick did not move the workspace sequence"
        rc, out = ctx.run("answer", "prompt-0007", "a considered thought")
        assert rc == 1, "a stale-view answer must be refused"
        assert "refused" in out and "prompt discarded." in out, out
        ex = _expectations()["inbox:prompt-0007"]
        assert ex.status == "confirmed", ex.status
        assert ex.outcome == "refused-stale-view", ex.outcome
        assert ex.resolved_tick is not None
        # No further resurfacing of that key over a bounded window: the
        # defect's whole point was the endless pending->expired nag.
        refused_tick = _tick()
        seen_trigger = False
        for _ in range(25):
            with redirect_stdout(io.StringIO()):
                cli._run_tick(cli._subject())
            for t in cli._subject().inspect()["trace"]:
                if t["kind"] != "cognition_trigger":
                    continue
                trig = t["trigger"]
                if (trig["kind"] == "unresolved_concern"
                        and "expectation:inbox:prompt-0007"
                        in trig.get("parents", [])):
                    seen_trigger = True
        assert not seen_trigger, \
            "a refused-stale-view expectation kept resurfacing"
        for r in cli._subject().inspect()["workspace"]["records"]:
            if r["tick"] > refused_tick and r["source"] == "temporal":
                assert "expectation:inbox:prompt-0007" not in \
                    r.get("concern_links", ()), \
                    "temporal record still linked to a refused expectation"


def test_refused_answer_behavior_otherwise_unchanged():
    # The fix settles the expectation; the refusal itself is untouched:
    # exit code 1, the file is gone, and the prompt is not re-consumable.
    from calibos_mind.provider import InboxCognition
    with CliOnTmp() as ctx:
        now, seq = _tick(), _seq()
        _write_prompt("prompt-0008", view_tick=now - TTL_TICKS - 1,
                      view_sequence=seq)
        with redirect_stdout(io.StringIO()):
            cli._run_tick(cli._subject())
        rc, out = ctx.run("answer", "prompt-0008", "a considered thought")
        assert rc == 1
        assert "refused" in out
        assert not (Path(cli.INBOX) / "prompt-0008.json").exists(), \
            "the consumed prompt file must stay deleted"
        try:
            InboxCognition(cli.INBOX).consume("prompt-0008")
        except ValueError:
            pass
        else:
            raise AssertionError("a refused prompt was re-consumable")


def test_refused_duplicate_thought_answer_confirms():
    # The second refusal-after-consume path: inject_thought rejects (thought
    # recorded recently) after the prompt was consumed. The expectation is
    # settled as refused-stale-view too — an unanswerable debt is an
    # unanswerable debt, not an open one.
    with CliOnTmp() as ctx:
        now, seq = _tick(), _seq()
        _write_prompt("prompt-0009", view_tick=now, view_sequence=seq)
        rc, _ = ctx.run("answer", "prompt-0009", "the same thought twice")
        assert rc == 0
        fresh_seq = _seq()
        assert fresh_seq != seq, "answering must move the workspace sequence"
        _write_prompt("prompt-0010", view_tick=now - TTL_TICKS - 1,
                      view_sequence=fresh_seq)
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0010"].status == "pending"
        rc, out = ctx.run("answer", "prompt-0010", "the same thought twice")
        assert rc == 1, "the duplicate thought must be refused"
        assert "refused" in out and "nothing recorded." in out, out
        ex = _expectations()["inbox:prompt-0010"]
        assert ex.status == "confirmed", ex.status
        assert ex.outcome == "refused-stale-view", ex.outcome
        assert ex.resolved_tick is not None


def test_resolve_absent_is_noop():
    with CliOnTmp() as ctx:
        sub = cli._subject()  # construction always rewrites the payload row
        mtime_before = Path(ctx.tmp / "t.db").stat().st_mtime_ns
        assert resolve(sub, "prompt-9999", outcome="answered") is None
        # mtime before any subject-constructing helper (see above).
        assert Path(ctx.tmp / "t.db").stat().st_mtime_ns == mtime_before, \
            "resolve opened a write path for an absent expectation"
        assert _expect_dict() == {}


# --- regression genome: read-only and dream paths ----------------------------

def test_drift_never_registers_or_writes():
    # mind drift is read-only: a stale prompt sitting in the inbox must not
    # gain an expectation, and the store must be logically untouched.
    # (Note: cli._subject() construction itself rewrites the payload row —
    # pre-existing engine behavior — so this asserts byte-identical payload
    # content, not mtime, around the drift command itself.)
    with CliOnTmp() as ctx:
        now = _tick()
        _write_prompt("prompt-0007", view_tick=now - TTL_TICKS - 1)
        before = _db_payload_bytes()
        rc, _ = ctx.run("drift")
        assert rc == 0
        assert _db_payload_bytes() == before, \
            "a read-only command logically mutated the store"
        assert _expect_dict() == {}, "a read-only command registered state"


def test_dream_tick_never_syncs():
    # Dream/conduct isolation: dream ticks must never register inbox
    # expectations, whatever sits in the inbox.
    from calibos_mind.provider import DreamCognition
    from calibos_mind.subject import CalibosSubject
    from digital_subject.cartridge import load_cartridge
    with CliOnTmp():
        tmp = Path(cli.DB).parent
        dreamer = DreamCognition(tmp / "dreams")
        cart = load_cartridge(BASE / "calibos.toml")
        sub = CalibosSubject(tmp / "dream.db", cart, cognition=dreamer,
                             salience_path=tmp / "salience.json",
                             interoception_path=tmp / "interoception.json")
        _write_prompt("prompt-0007", view_tick=-TTL_TICKS - 1)
        for _ in range(3):
            sub.dream_tick()
        got = [e for e in sub.continuity.state.expectations
               if e.startswith("inbox:")]
        assert got == [], f"dream ticks registered expectations: {got}"


def test_no_shadowing_defs():
    # Genome: duplicate defs silently win (the cmd_init shadowing bug).
    src = (BASE / "calibos_mind" / "inbox_expectations.py").read_text(
        encoding="utf-8")
    assert src.count("\ndef sync(") == 1
    assert src.count("\ndef resolve(") == 1
    assert src.count("\ndef expectation_id(") == 1


# --- end-to-end through the frozen engine -------------------------------------

class _ScriptedProvider:
    """Answers every warranted cognition with a fixed thought: the trigger
    still fires and is traced, but no new prompt is queued, so the inbox
    stays exactly as the test arranged it."""

    def think(self, view):
        from jelly_psiduck.workspace import Thought
        return Thought("noted; the matter is registered")


def _engine_subject(tmp):
    from calibos_mind.subject import CalibosSubject
    from digital_subject.cartridge import load_cartridge
    cart = load_cartridge(BASE / "calibos.toml")
    return CalibosSubject(tmp / "e2e.db", cart, cognition=_ScriptedProvider(),
                          salience_path=tmp / "salience.json",
                          interoception_path=tmp / "interoception.json")


def _run_until_unresolved(sub, key, max_ticks):
    """Heartbeat until an unresolved_concern trigger fires for `key`.

    Scans the trace after every tick (eviction-safe: a fresh trigger is
    always near the back when first observed). Returns the tick count used,
    or None when the window closes without the trigger."""
    for n in range(1, max_ticks + 1):
        sub.heartbeat()
        for t in sub.inspect()["trace"]:
            if t["kind"] != "cognition_trigger":
                continue
            trig = t["trigger"]
            if (trig["kind"] == "unresolved_concern"
                    and key in trig.get("parents", [])):
                return n
    return None


def test_unresolved_concern_fires_after_due_passes():
    # The spec's core end-to-end claim: a stale unanswered prompt, wired in
    # as an Expectation, is resurfaced by the engine's OWN temporal machinery
    # — temporal records with the expectation concern-link, then an
    # unresolved_concern cognition_trigger — with no new triggers, models,
    # or prompt-contract changes.
    tmp = Path(tempfile.mkdtemp(prefix="inexp-e2e-"))
    inbox = tmp / "inbox"
    inbox.mkdir()
    sub = _engine_subject(tmp)
    (inbox / "prompt-0001.json").write_text(json.dumps({
        "id": "prompt-0001", "prompt": "e2e", "view_tick": 0,
        "view_sequence": 0,
        "experiences": [{"source": "invitation",
                         "first_person": "e2e invitation"}]}),
        encoding="utf-8")
    for _ in range(TTL_TICKS + 1):  # tick 4: prompt is stale (age 4 >= 3)
        sub.heartbeat()
    out = sync(sub, inbox)
    assert out == {"registered": ["inbox:prompt-0001"], "expired": [], "superseded": [], "lapsed": []}
    key = "expectation:inbox:prompt-0001"
    used = _run_until_unresolved(sub, key, 60)
    assert used is not None, \
        "unresolved_concern never fired for the stale prompt within 60 ticks"
    temporals = [r for r in sub.inspect()["workspace"]["records"]
                 if r["source"] == "temporal"
                 and key in r.get("concern_links", ())]
    assert temporals, "no temporal record linked to the inbox expectation"
    assert any("has passed" in r["first_person"]
               or "waiting" in r["first_person"]
               or "prolonged" in r["first_person"] for r in temporals), \
        "temporal record does not carry the engine's escalation prose"


def test_no_inbox_trigger_when_inbox_empty():
    # Revert-signal control: with a genuinely empty inbox and no expectations,
    # heartbeats must never fire unresolved_concern for an inbox: key
    # (hallucinated unfinished business).
    tmp = Path(tempfile.mkdtemp(prefix="inexp-e2e-empty-"))
    (tmp / "inbox").mkdir()
    sub = _engine_subject(tmp)
    for _ in range(30):
        sub.heartbeat()
    for t in sub.inspect()["trace"]:
        if t["kind"] != "cognition_trigger":
            continue
        trig = t["trigger"]
        assert not (trig["kind"] == "unresolved_concern"
                    and any("expectation:inbox:" in p
                            for p in trig.get("parents", []))), \
            "unresolved_concern fired for unfinished business that does not exist"


# --- lapse: an expired, unclaimable debt is released after its grief window
#
# Regression target (2026-10-01): prompt-0090's expectation expired at
# tick 251 and kept resurfacing "I still owe an answer" forever — the
# prompt file was gone, no live prompt claimed the debt, and answering
# was structurally impossible. The guilt deserves its hearing, not a life
# sentence.

def _expire_unanswered(pid):
    """Register pid, then vanish it without an answer: expired expectation."""
    now = _tick()
    _write_prompt(pid, view_tick=now - TTL_TICKS - 1)
    sync(cli._subject(), cli.INBOX)
    (Path(cli.INBOX) / f"{pid}.json").unlink()
    out = sync(cli._subject(), cli.INBOX)
    assert _expectations()[expectation_id(pid)].status == "expired"
    return out


def _advance_ticks(n):
    with redirect_stdout(io.StringIO()):
        for _ in range(n):
            cli._run_tick(cli._subject())


def test_lapsed_after_grief_window():
    from calibos_mind.inbox_expectations import GRIEF_TICKS
    with CliOnTmp():
        _expire_unanswered("prompt-0021")
        # Not yet lapsed: inside the grief window the debt still nags.
        out = sync(cli._subject(), cli.INBOX)
        assert out["lapsed"] == []
        assert _expectations()["inbox:prompt-0021"].status == "expired"
        # One tick short of the window: still expired, still nagging.
        _advance_ticks(GRIEF_TICKS - 1)
        assert _expectations()["inbox:prompt-0021"].status == "expired"
        out = sync(cli._subject(), cli.INBOX)
        assert out["lapsed"] == []
        # The window closes: the debt is released (the sync inside the
        # heartbeat performs the lapse, so assert the settled state).
        _advance_ticks(1)
        ex = _expectations()["inbox:prompt-0021"]
        assert ex.status == "confirmed", ex.status
        assert ex.outcome == "lapsed", ex.outcome
        assert ex.resolved_tick is not None
        # Released from the engine's open set: no more resurfacing.
        keys = open_link_keys(cli._subject().inspect())
        assert "expectation:inbox:prompt-0021" not in keys


def test_no_lapse_while_prompt_still_live():
    from calibos_mind.inbox_expectations import GRIEF_TICKS
    with CliOnTmp():
        # Prompt file still present but long overdue: the debt is still
        # settleable by answering, so it never lapses.
        now = _tick()
        _write_prompt("prompt-0022", view_tick=now - TTL_TICKS - GRIEF_TICKS - 10)
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0022"].status == "pending"
        _advance_ticks(GRIEF_TICKS + 5)
        out = sync(cli._subject(), cli.INBOX)
        assert out["lapsed"] == []
        # The frozen engine may have expired it past due, but a live prompt
        # is still answerable: the debt is never released, only kept open.
        ex = _expectations()["inbox:prompt-0022"]
        assert ex.status != "confirmed", ex.status
        assert ex.outcome != "lapsed"


def test_no_lapse_when_claimed_by_live_supersedes():
    from calibos_mind.inbox_expectations import GRIEF_TICKS
    with CliOnTmp():
        _expire_unanswered("prompt-0023")
        # A live prompt claims the debt in its supersedes list: the
        # consideration evidence exists even though the expiry came first
        # (the engine expires past-due debts on its own; the supersede
        # arrives a tick later). Not an engine-minted prompt, so the
        # heartbeat's queue-time supersede does not eat the claimant.
        payload = {
            "id": "prompt-0024",
            "prompt": "test prompt",
            "view_tick": _tick() - TTL_TICKS - 1,
            "view_sequence": 0,
            "supersedes": ["prompt-0023"],
            "experiences": [{"source": "invitation",
                             "first_person": "test invitation"}],
        }
        (Path(cli.INBOX) / "prompt-0024.json").write_text(
            json.dumps(payload), encoding="utf-8")
        _advance_ticks(GRIEF_TICKS)
        out = sync(cli._subject(), cli.INBOX)
        assert out["lapsed"] == []
        ex = _expectations()["inbox:prompt-0023"]
        assert ex.status == "confirmed", ex.status
        assert ex.outcome == "superseded", ex.outcome


def test_lapse_is_neutral_on_streak():
    from calibos_mind.inbox_expectations import GRIEF_TICKS
    with CliOnTmp():
        from calibos_mind.inbox_expectations import _policy_path, _read_streak
        _expire_unanswered("prompt-0025")
        before = _read_streak(_policy_path(cli.INBOX))
        _advance_ticks(GRIEF_TICKS)
        sync(cli._subject(), cli.INBOX)
        assert _expectations()["inbox:prompt-0025"].outcome == "lapsed"
        assert _read_streak(_policy_path(cli.INBOX)) == before, \
            "lapse must not move the expiry streak"


def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — test runner reports
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
