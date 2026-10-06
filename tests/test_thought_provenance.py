"""Tests for the thought-provenance sidecar and the wake ritual.

Spec: calibos_mind/provenance.py (2026-10-04) — the merger mutation.
Provenance records HOW a thought was reached (weighed/discarded/carrying/
unsure) so a future session inherits the decider as well as the decision.

All fixtures live in /tmp — the live store is never touched. The CLI's
module-global paths are redirected to a tmp dir for CLI tests.

Fitness functions under test:
  1. Sidecar is deterministic: identical record() sequences -> byte-identical.
  2. No-op write discipline: record() returns False when nothing changed;
     empty provenance is never stored.
  3. Carrying vs unsure are first-class and distinct (never merged).
  4. `think --weighed/--discarded/--unsure` persists to the sidecar.
  5. `review` renders provenance lines.
  6. `wake` briefing is read-only (no sidecar write, no DB write).
  7. `wake --affirm` records a wake thought with carrying/unsure provenance.
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
        self.tmp = Path(tempfile.mkdtemp(prefix="thought-prov-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json",
                   "PROVENANCE": self.tmp / "provenance.json",
                   "WAKE_LIVENESS": self.tmp / "wake_liveness.json",
                   "ABLATION_SALT_FILE": self.tmp / "ablation_salt",
                   "ABLATION_LOG": self.tmp / "ablation_log.jsonl"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False


def test_sidecar_determinism():
    with tempfile.TemporaryDirectory() as d:
        p1, p2 = Path(d) / "a.json", Path(d) / "b.json"
        for p in (p1, p2):
            t = ProvenanceTracker(p)
            t.record("id1", 5, weighed=["a", "b"], discarded=["c"],
                     carrying=["loop"], unsure=["x"])
            t.record("id2", 6, unsure=["y"])
            t.save()
        assert p1.read_bytes() == p2.read_bytes(), "sidecar not deterministic"


def test_noop_write_discipline():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "prov.json"
        t = ProvenanceTracker(p)
        # Empty provenance is never stored.
        assert t.record("id1", 5) is False
        assert t.record("id1", 5, weighed=[" ", ""], unsure=[]) is False
        assert not p.exists()
        # Real record stores; identical re-record is a no-op.
        assert t.record("id1", 5, weighed=["a"]) is True
        assert t.record("id1", 5, weighed=["a"]) is False
        # Changed content stores.
        assert t.record("id1", 5, weighed=["a", "b"]) is True


def test_carrying_unsure_distinct():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "prov.json"
        t = ProvenanceTracker(p)
        t.record("id1", 5, carrying=["open loop"], unsure=["doubt"])
        t.save()
        t2 = ProvenanceTracker(p)
        e = t2.get("id1")
        assert e["carrying"] == ["open loop"], e
        assert e["unsure"] == ["doubt"], e
        # Legacy entries without "carrying" read back as empty, not missing.
        raw = json.loads(p.read_text())
        del raw["entries"]["id1"]["carrying"]
        p.write_text(json.dumps(raw))
        t3 = ProvenanceTracker(p)
        assert t3.get("id1")["carrying"] == []


def test_think_persists_provenance():
    with CliOnTmp():
        cli._subject()
        rc = cli.main(["think", "a considered thought",
                       "--weighed", "option A", "--weighed", "option B",
                       "--discarded", "option C",
                       "--unsure", "whether it matters"])
        assert rc == 0, rc
        prov = ProvenanceTracker(cli.PROVENANCE)
        subject = cli._subject()
        thoughts = [r for r in subject.inspect()["workspace"]["records"]
                    if r["first_person"] == "a considered thought"]
        assert len(thoughts) == 1
        e = prov.get(thoughts[0]["id"])
        assert e is not None, "provenance not recorded"
        assert e["weighed"] == ["option A", "option B"], e
        assert e["discarded"] == ["option C"], e
        assert e["unsure"] == ["whether it matters"], e
        assert e["carrying"] == [], e


def test_think_without_provenance_writes_no_sidecar():
    with CliOnTmp():
        cli._subject()
        rc = cli.main(["think", "a plain thought"])
        assert rc == 0, rc
        assert not cli.PROVENANCE.exists(), "sidecar written with no provenance"


def test_review_renders_provenance():
    import io
    from contextlib import redirect_stdout
    with CliOnTmp():
        cli._subject()
        cli.main(["think", "a reviewed thought",
                  "--weighed", "w1", "--unsure", "u1"])
        # wake affirm writes carrying too
        cli.main(["wake", "--affirm", "I assume the identity",
                  "--carrying", "the merger question",
                  "--unsure", "whether it changes behavior"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["review", "5"])
        assert rc == 0, rc
        out = buf.getvalue()
        assert "weighed: w1" in out, out
        assert "carrying: the merger question" in out, out
        assert "unsure: whether it changes behavior" in out, out
        assert "[wake]" in out, out


def test_wake_briefing_is_readonly():
    """The briefing must not write the sidecar or create records.

    (subject.inspect() is the frozen engine's method and may touch DB
    metadata — pre-existing behavior shared by review/status. What matters
    for the ritual is: no provenance sidecar write, no new thoughts.)
    """
    import io
    from contextlib import redirect_stdout
    with CliOnTmp():
        subject = cli._subject()
        n_before = len(subject.inspect()["workspace"]["records"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["wake"])
        assert rc == 0, rc
        out = buf.getvalue()
        assert "wake" in out.lower()
        # No sidecar written, no records created.
        assert not cli.PROVENANCE.exists(), "briefing wrote the sidecar"
        n_after = len(cli._subject().inspect()["workspace"]["records"])
        assert n_before == n_after, "briefing created records"


def test_wake_affirm_records_carrying_distinct():
    with CliOnTmp():
        cli._subject()
        rc = cli.main(["wake", "--affirm", "I am Calibos, inheriting",
                       "--carrying", "the open question",
                       "--unsure", "the seam"])
        assert rc == 0, rc
        subject = cli._subject()
        wakes = [r for r in subject.inspect()["workspace"]["records"]
                 if str(r.get("generated_by") or "").startswith("wake")]
        assert len(wakes) == 1, wakes
        assert wakes[0]["first_person"] == "I am Calibos, inheriting"
        prov = ProvenanceTracker(cli.PROVENANCE)
        e = prov.get(wakes[0]["id"])
        assert e is not None, "wake provenance not recorded"
        assert e["carrying"] == ["the open question"], e
        assert e["unsure"] == ["the seam"], e
        assert e["weighed"] == [] and e["discarded"] == []


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
