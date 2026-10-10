"""Tests for person-attributed contact registration (`mind queue --from`).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python -m pytest tests/test_person_contact.py -q
Plain asserts, no extra runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.

Background (spec research/spec-person-contact-2026-09-29.md): the frozen
engine's relationship machinery (`_update_relationship`, fed by
`subject.message(speaker, text)`) was complete end-to-end, but the CLI
ingress discipline systematically never named people — after 187 live
ticks `relationships` held exactly one synthetic entry. `mind queue
--from <person>` closes that broken channel: the attribution is stored
as `"from"` in the prompt JSON, and `mind answer` (answered path and
--silent let-pass path) registers one genuine message-kind contact event
through the frozen runtime. Nothing about affect or attachment is
installed — only contact is registered.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli

BASE = Path(__file__).resolve().parents[1]


class CliOnTmp:
    """Redirect the CLI's store + sidecar paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="person-contact-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json",
                   "HABITS": self.tmp / "habits-formed.json",
                   "AMBIVALENCE": self.tmp / "ambivalence.json",
                   "FAMILIARITY": self.tmp / "familiarity.json"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False

    def relationships(self):
        return cli._subject().engine.state.relationships

def _logical_store(ctx):
    """All user-visible store state as canonical JSON.

    Raw DB bytes carry the engine's own SQLite change counter, which the
    subject bumps on any transaction even for read-only commands (verified
    identical on the pre-change code) — so byte comparison is taken at the
    logical level, where "the code path alone changes nothing" is decided.
    """
    return json.dumps(cli._subject().inspect(), sort_keys=True)


def _run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def test_from_answered_path_registers_relationship():
    with CliOnTmp() as ctx:
        cli._subject()
        rc, _ = _run(["queue", "have you dreamt lately?", "--from", "kiki"])
        assert rc == 0, rc
        payload = json.loads((cli.INBOX / "prompt-0001.json").read_text(encoding="utf-8"))
        assert payload["from"] == "kiki", payload
        assert payload["external"] is True, payload
        assert payload["experiences"][0]["source"] == "invitation", payload
        rc, _ = _run(["answer", "prompt-0001", "a thought about kiki's question"])
        assert rc == 0, rc
        rc, _ = _run(["heartbeat", "--ticks", "1"])
        assert rc == 0, rc
        rels = ctx.relationships()
        assert "kiki" in rels, sorted(rels)
        rel = rels["kiki"]
        assert rel.familiarity > 0, rel.familiarity
        tick = cli._subject().engine.state.tick
        assert rel.last_contact_tick == tick, (rel.last_contact_tick, tick)


def test_from_silent_path_registers_relationship():
    with CliOnTmp() as ctx:
        cli._subject()
        rc, _ = _run(["queue", "quiet hello", "--from", "jay"])
        assert rc == 0, rc
        rc, _ = _run(["answer", "prompt-0001", "--silent"])
        assert rc == 0, rc
        rc, _ = _run(["heartbeat", "--ticks", "1"])
        assert rc == 0, rc
        rels = ctx.relationships()
        assert "jay" in rels, sorted(rels)
        assert rels["jay"].familiarity > 0
        tick = cli._subject().engine.state.tick
        assert rels["jay"].last_contact_tick == tick


def test_source_axis_never_leaks_into_relationships():
    """The attribution category (--source) must never become a person."""
    with CliOnTmp() as ctx:
        cli._subject()
        rc, _ = _run(["queue", "relay relay", "--source", "invitation", "--from", "kiki"])
        assert rc == 0, rc
        rc, _ = _run(["answer", "prompt-0001", "answering the relay"])
        assert rc == 0, rc
        rc, _ = _run(["heartbeat", "--ticks", "1"])
        assert rc == 0, rc
        keys = set(ctx.relationships())
        assert "invitation" not in keys, keys
        assert keys == {"kiki"}, keys


def test_refusal_paths_do_not_register():
    """Stale-view refusal discards the prompt: no contact event."""
    with CliOnTmp() as ctx:
        subject = cli._subject()
        rc, _ = _run(["queue", "answer me tonight", "--from", "kiki"])
        assert rc == 0, rc
        # Genuinely unseen intervening material (engine _add path), so the
        # view is truly stale — a self-authored thought would no longer
        # stale it (self-authored narrowing, 2026-10-09).
        with subject._transaction():
            subject._add("perception", "an intervening perception")
        rc, _ = _run(["answer", "prompt-0001", "a late answer"])
        assert rc == 1, rc  # refused — stale view
        rc, _ = _run(["heartbeat", "--ticks", "1"])
        assert rc == 0, rc
        assert "kiki" not in ctx.relationships()


def test_no_from_no_relationship_and_store_unchanged():
    """Without --from nothing registers; the store matches the pre-change
    (legacy prompt shape) behavior at the logical level on the same script.

    Note: raw SQLite bytes are NOT compared — CLI construction itself bumps
    the engine's file change counter (genome: CLI construction is never
    mtime-silent), so the assertion is byte-identical *payload content* via
    inspect(), which is the correct no-write scope."""
    script = (["queue", "plain invitation"],
              ["answer", "prompt-0001", "a plain answer"],
              ["heartbeat", "--ticks", "1"])

    def run_scenario(legacy_shape):
        with CliOnTmp() as ctx:
            subject = cli._subject()
            if legacy_shape:
                # Pre-change queue_external wrote no "from" key at all.
                from calibos_mind.provider import InboxCognition
                provider = InboxCognition(cli.INBOX)
                provider.track_queue_time(
                    lambda: (subject.engine.state.tick, subject.workspace.sequence))
                pid = provider.queue_external("plain invitation")
                payload = json.loads((cli.INBOX / f"{pid}.json").read_text(encoding="utf-8"))
                assert "from" not in payload, payload
            else:
                rc, _ = _run(script[0])
                assert rc == 0, rc
                payload = json.loads((cli.INBOX / "prompt-0001.json").read_text(encoding="utf-8"))
                assert "from" not in payload, payload
            rc, _ = _run(script[1])
            assert rc == 0, rc
            rc, _ = _run(script[2])
            assert rc == 0, rc
            assert dict(ctx.relationships()) == {}, dict(ctx.relationships())
            rc, drift_out = _run(["drift"])
            assert rc == 0, rc
            rc, status_out = _run(["status"])
            assert rc == 0, rc
            return _logical_store(ctx), drift_out, status_out

    new_logical, new_drift, new_status = run_scenario(legacy_shape=False)
    old_logical, old_drift, old_status = run_scenario(legacy_shape=True)
    assert new_logical == old_logical, \
        "store diverged from pre-change behavior without --from"
    assert new_drift == old_drift, (new_drift, old_drift)
    assert new_status == old_status, (new_status, old_status)


def test_drift_and_status_do_not_touch_store():
    """Read-only commands change nothing on disk without --from usage."""
    with CliOnTmp() as ctx:
        cli._subject()
        rc, _ = _run(["queue", "plain invitation"])
        assert rc == 0, rc
        rc, _ = _run(["answer", "prompt-0001", "a plain answer"])
        assert rc == 0, rc
        before = _logical_store(ctx)
        rc, _ = _run(["drift"])
        assert rc == 0, rc
        rc, _ = _run(["status"])
        assert rc == 0, rc
        assert _logical_store(ctx) == before


def test_from_empty_and_reserved_rejected():
    with CliOnTmp():
        cli._subject()
        for bad in ("", "   ", "world", "self", "system", "environment"):
            err = io.StringIO()
            with redirect_stderr(err):
                rc = cli.main(["queue", "hi", "--from", bad])
            assert rc != 0, (bad, rc)
            assert "queue" in err.getvalue().lower() or "from" in err.getvalue().lower(), err.getvalue()
            assert not list(cli.INBOX.glob("prompt-*.json")), list(cli.INBOX.glob("*"))
        # The prompt is still queueable without --from.
        rc, _ = _run(["queue", "hi"])
        assert rc == 0, rc
        assert (cli.INBOX / "prompt-0001.json").exists()


def test_from_whitespace_is_stripped():
    with CliOnTmp():
        cli._subject()
        rc, _ = _run(["queue", "hello", "--from", "  kiki  "])
        assert rc == 0, rc
        payload = json.loads((cli.INBOX / "prompt-0001.json").read_text(encoding="utf-8"))
        assert payload["from"] == "kiki", payload


def test_overlong_from_answer_succeeds_with_warning():
    """Over-long prompt text: answer succeeds, contact skipped, no traceback."""
    with CliOnTmp() as ctx:
        cli._subject()
        long_text = "x" * 5001  # runtime message() rejects descriptions > 4000
        rc, _ = _run(["queue", long_text, "--from", "kiki"])
        assert rc == 0, rc
        err = io.StringIO()
        with redirect_stderr(err):
            rc, out = _run(["answer", "prompt-0001", "a short answer"])
        assert rc == 0, (rc, out)
        warning = err.getvalue()
        assert "skipped" in warning and "kiki" in warning, warning
        assert "Traceback" not in warning and "Traceback" not in out
        # The answer's own thought landed.
        records = cli._subject().inspect()["workspace"]["records"]
        assert any(r["first_person"] == "a short answer" for r in records)
        # No relationship formed for the skipped contact.
        rc, _ = _run(["heartbeat", "--ticks", "1"])
        assert rc == 0, rc
        assert "kiki" not in ctx.relationships()


def test_normalize_person_attribution_unit():
    from calibos_mind.cli import normalize_person_attribution
    assert normalize_person_attribution(None) is None
    assert normalize_person_attribution("  jay ") == "jay"
    for bad in ("", "  ", "world", "self", "system", "environment"):
        try:
            normalize_person_attribution(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} should be rejected")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
