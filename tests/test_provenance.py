"""Tests for thought provenance stamps and stale-prompt refusal.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_provenance.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched. The CLI's
module-global paths (DB/INBOX/DREAMS/SALIENCE) are redirected to a tmp dir
for the duration of each CLI test and restored afterwards.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.drift import drift_report
from calibos_mind.provider import InboxCognition, StalePromptError, check_prompt_fresh, prompt_answerable

BASE = Path(__file__).resolve().parents[1]
LIVE_DB = BASE / "mind.db"


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="prov-cli-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False

    def queue_prompt(self, subject):
        """Queue one prompt through the real queue path (clock wired)."""
        provider = InboxCognition(cli.INBOX)
        provider.track_queue_time(
            lambda: (subject.engine.state.tick, subject.workspace.sequence))
        provider.think(subject.workspace.view())
        return "prompt-0001"

    def thoughts(self):
        subject = cli._subject()
        return [r for r in subject.inspect()["workspace"]["records"]
                if r["source"] == "thought"]


def test_answered_stamp_carries_prompt_and_tick():
    with CliOnTmp() as ctx:
        subject = cli._subject()
        pid = ctx.queue_prompt(subject)
        rc = cli.main(["answer", pid, "a timely thought about the prompt"])
        assert rc == 0, rc
        matches = [r for r in ctx.thoughts()
                   if r["first_person"] == "a timely thought about the prompt"]
        assert len(matches) == 1
        stamp = matches[0]["generated_by"]
        assert re.fullmatch(r"answered:prompt-0001@\d+", stamp), stamp
        assert stamp.endswith(f"@{matches[0]['tick']}"), stamp


def test_voluntary_stamp():
    with CliOnTmp() as ctx:
        cli._subject()  # create the store
        rc = cli.main(["think", "a voluntary musing"])
        assert rc == 0, rc
        matches = [r for r in ctx.thoughts()
                   if r["first_person"] == "a voluntary musing"]
        assert len(matches) == 1
        assert matches[0]["generated_by"] == "voluntary", matches[0]


def test_stale_prompt_refused_never_answered():
    """A prompt queued before *unseen* intervening records is refused, not
    answered stale: no thought is recorded and drift cannot move on it.
    (Self-authored intervening records — think/affirm/answer — do not
    stale a view; see test_self_authored_intervening_allows_answer.)"""
    with CliOnTmp() as ctx:
        subject = cli._subject()
        pid = ctx.queue_prompt(subject)
        # The store moves on after the prompt was queued, with material the
        # thinker never composed (engine _add path, generated_by=None).
        with subject._transaction():
            subject._add("perception", "an intervening perception")
        tracker = subject.workspace.salience_tracker
        r_before = drift_report(subject.inspect(), tracker)["ratio"]["R"]
        n_before = len(ctx.thoughts())

        rc = cli.main(["answer", pid, "a late answer to a dead view"])
        assert rc == 1, rc
        # Prompt consumed (discarded), nothing injected.
        assert not list(cli.INBOX.glob("prompt-*.json"))
        after = ctx.thoughts()
        assert len(after) == n_before, after
        assert not any(r["first_person"] == "a late answer to a dead view"
                       for r in after)
        r_after = drift_report(cli._subject().inspect(), tracker)["ratio"]["R"]
        assert r_after == r_before, (r_before, r_after)


def test_self_authored_intervening_allows_answer():
    """The narrowing: records the thinker composed between queue time and
    answer time (think, affirm, answer) do not stale the view — the
    hazard the freshness invariant guards is unseen material."""
    with CliOnTmp() as ctx:
        subject = cli._subject()
        pid = ctx.queue_prompt(subject)
        subject.inject_thought("an intervening thought of my own",
                               trigger_kind="voluntary",
                               generated_by="voluntary")
        rc = cli.main(["answer", pid, "a timely answer after my own thought"])
        assert rc == 0, rc
        assert any(r["first_person"] == "a timely answer after my own thought"
                   for r in ctx.thoughts())
        assert not list(cli.INBOX.glob("prompt-*.json"))


def test_stale_prompt_silence_also_refused():
    with CliOnTmp() as ctx:
        subject = cli._subject()
        pid = ctx.queue_prompt(subject)
        with subject._transaction():
            subject._add("perception", "another intervening perception")
        rc = cli.main(["answer", pid, "--silent"])
        assert rc == 1, rc
        assert not list(cli.INBOX.glob("prompt-*.json"))


def test_legacy_prompt_without_provenance_refused():
    """Fail closed: a prompt that predates queue-time provenance cannot
    prove its view, so it is refused rather than answered."""
    with CliOnTmp() as ctx:
        cli._subject()
        cli.INBOX.mkdir(parents=True, exist_ok=True)
        (cli.INBOX / "prompt-0001.json").write_text(json.dumps({
            "id": "prompt-0001", "prompt": "legacy",
            "experiences": [],
        }))
        rc = cli.main(["answer", "prompt-0001", "answering blind"])
        assert rc == 1, rc
        assert not any(r["first_person"] == "answering blind"
                       for r in ctx.thoughts())


def test_check_prompt_fresh_unit():
    fresh = {"view_tick": 4, "view_sequence": 10}
    check_prompt_fresh(fresh, 10)  # no raise
    try:
        check_prompt_fresh({"view_tick": 4, "view_sequence": 10}, 11)
    except StalePromptError as exc:
        assert "superseded" in str(exc), exc
    else:
        raise AssertionError("stale sequence not refused")
    try:
        check_prompt_fresh({"view_tick": 4}, 10)
    except StalePromptError:
        pass
    else:
        raise AssertionError("missing provenance not refused")


class _FakeRecord:
    def __init__(self, seq, generated_by=None):
        self.id = f"experience-{seq}"
        self.generated_by = generated_by


def _refuses(payload, sequence, records):
    try:
        check_prompt_fresh(payload, sequence, records)
    except StalePromptError:
        return True
    return False


def test_check_prompt_fresh_self_authored_narrowing():
    # Strict two-arg form keeps the original behavior (records omitted).
    assert _refuses({"view_tick": 4, "view_sequence": 10}, 12, None)
    payload = {"view_tick": 4, "view_sequence": 10}
    # Only thinker-authored records intervened: think, affirm, answer.
    own = [_FakeRecord(11, "voluntary"),
           _FakeRecord(12, "wake"),
           _FakeRecord(13, "answered:prompt-0001@9"),
           _FakeRecord(14, "answered-external:prompt-0002@9")]
    check_prompt_fresh(payload, 14, own)  # no raise
    assert prompt_answerable(payload, 14, own)
    # One engine-authored record (heartbeat perception) -> still refused.
    mixed = own + [_FakeRecord(15, None)]
    assert _refuses(payload, 15, mixed)
    assert not prompt_answerable(payload, 15, mixed)
    # Dream echoes are not thinker-composed -> refused.
    dreamed = own + [_FakeRecord(15, "dream-derived")]
    assert _refuses(payload, 15, dreamed)
    # Engine heartbeat thoughts ("cognition") are not thinker-composed.
    engine_thought = own + [_FakeRecord(15, "cognition")]
    assert _refuses(payload, 15, engine_thought)
    # An evicted (missing) intervening record fails closed.
    gapped = [_FakeRecord(11, "voluntary"), _FakeRecord(13, "wake")]
    assert _refuses(payload, 13, gapped)
    # Legacy prompt without provenance still refused even with records.
    assert _refuses({"view_tick": 4}, 14, own)


def test_live_store_untouched():
    if not LIVE_DB.exists():
        return
    import hashlib
    digest = hashlib.sha256(LIVE_DB.read_bytes()).hexdigest()
    with CliOnTmp() as ctx:
        subject = cli._subject()
        pid = ctx.queue_prompt(subject)
        assert cli.main(["answer", pid, "synthetic answer"]) == 0
        assert cli.main(["think", "synthetic voluntary"]) == 0
    assert hashlib.sha256(LIVE_DB.read_bytes()).hexdigest() == digest, \
        "live mind.db changed during synthetic provenance tests"


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
