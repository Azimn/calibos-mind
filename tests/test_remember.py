"""Tests for `mind remember` — the merger write path (2026-10-04).

Spec: durable learnings from conversation enter the mind's record path
with provenance, instead of bypassing the machinery as prose notes
elsewhere. A remembered memory is first-class chat-learned
(generated_by="chat") — psychologically distinct from cartridge seeds
(authored temperament priors) and lived experience (heartbeat
observations).

All fixtures live in /tmp — the live store is never touched. The CLI's
module-global paths are redirected to a tmp dir for CLI tests.

Fitness functions under test:
  1. `remember` stores a memory-class record with generated_by="chat".
  2. Explicit --concepts are stored; omitted concepts are auto-extracted.
  3. weighed/discarded/carrying/unsure persist to the provenance sidecar,
     keyed by the memory record id (tracker is class-agnostic).
  4. Exact-duplicate text is refused (the self is not recorded twice).
  5. Empty text is refused; nothing is recorded.
  6. Remembering marks salience importance (revealed preference).
  7. No heartbeat tick runs: remembering is a write, not an experience
     (record count grows by exactly one; tick does not advance).
  8. Plain remember (no provenance flags) writes no sidecar.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.provenance import ProvenanceTracker


class CliOnTmp:
    """Redirect the CLI's store paths (incl. PROVENANCE) at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="remember-"))
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


def _memories():
    subject = cli._subject()
    return [r for r in subject.inspect()["workspace"]["records"]
            if r.get("generated_by") == "chat"]


def test_remember_records_chat_memory():
    with CliOnTmp():
        cli._subject()
        rc = cli.main(["remember", "Jay's dog is named Biscuit.",
                       "--concepts", "fact,pet"])
        assert rc == 0, rc
        mems = _memories()
        assert len(mems) == 1, mems
        assert mems[0]["first_person"] == "Jay's dog is named Biscuit."
        assert list(mems[0]["concepts"]) == ["fact", "pet"]


def test_remember_auto_concepts():
    with CliOnTmp():
        cli._subject()
        rc = cli.main(["remember", "I prefer short replies."])
        assert rc == 0, rc
        mems = _memories()
        assert len(mems) == 1
        # Auto-extracted concepts are non-empty keywords, not a crash.
        assert len(mems[0]["concepts"]) >= 1, mems[0]["concepts"]


def test_remember_bad_concepts_refused():
    with CliOnTmp():
        cli._subject()
        rc = cli.main(["remember", "Some fact.", "--concepts", "no-comma-here"])
        assert rc == 1, rc
        assert _memories() == [], "refused remember still recorded"


def test_remember_provenance():
    with CliOnTmp():
        cli._subject()
        rc = cli.main(["remember", "The merger write path is live.",
                       "--weighed", "keeping chat memory separate",
                       "--discarded", "a second MEMORY.md",
                       "--carrying", "the single-substrate question",
                       "--unsure", "whether the read path lands"])
        assert rc == 0, rc
        mems = _memories()
        assert len(mems) == 1
        prov = ProvenanceTracker(cli.PROVENANCE)
        e = prov.get(mems[0]["id"])
        assert e is not None, "provenance not recorded for memory"
        assert e["weighed"] == ["keeping chat memory separate"], e
        assert e["discarded"] == ["a second MEMORY.md"], e
        assert e["carrying"] == ["the single-substrate question"], e
        assert e["unsure"] == ["whether the read path lands"], e


def test_remember_without_provenance_writes_no_sidecar():
    with CliOnTmp():
        cli._subject()
        rc = cli.main(["remember", "A plain durable fact."])
        assert rc == 0, rc
        assert not cli.PROVENANCE.exists(), "sidecar written with no provenance"


def test_remember_duplicate_refused():
    with CliOnTmp():
        cli._subject()
        assert cli.main(["remember", "Do not record me twice."]) == 0
        rc = cli.main(["remember", "Do not record me twice."])
        assert rc == 1, rc
        assert len(_memories()) == 1, "duplicate memory recorded"


def test_remember_empty_refused():
    with CliOnTmp():
        cli._subject()
        assert cli.main(["remember", "   "]) == 1
        assert _memories() == [], "empty remember recorded"


def test_remember_marks_importance():
    with CliOnTmp():
        cli._subject()
        assert cli.main(["remember", "An important durable fact."]) == 0
        mems = _memories()
        assert len(mems) == 1
        sal = json.loads(Path(cli.SALIENCE).read_text(encoding="utf-8"))
        entry = sal["records"][mems[0]["id"]]
        assert entry["importance"] == 0.3, entry


def test_remember_runs_no_tick():
    """Remembering is a write, not an experience: exactly one record is
    added and the engine tick does not advance (unlike `note`, which runs
    a heartbeat)."""
    with CliOnTmp():
        subject = cli._subject()
        tick_before = subject.engine.state.tick
        n_before = len(subject.inspect()["workspace"]["records"])
        rc = cli.main(["remember", "A quiet write, no heartbeat."])
        assert rc == 0, rc
        subject = cli._subject()
        assert subject.engine.state.tick == tick_before, "remember advanced the tick"
        n_after = len(subject.inspect()["workspace"]["records"])
        assert n_after == n_before + 1, (n_before, n_after)


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
