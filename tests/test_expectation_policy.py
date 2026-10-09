"""Tests for the expectation confidence-decay policy (2026-09-27 mutation).

Fitness (research/spec-expectation-confidence-decay-2026-09-27.md):
- fresh policy (no sidecar) -> stale prompt registers at exactly 0.6.
- one expiry -> streak 1; next stale prompt registers at 0.48.
- two more expiries -> 0.384, then 0.3072 (0.6 * 0.8**3).
- streak forced to 20 -> confidence floored at exactly 0.15, never below.
- resolve("answered") on a live expectation -> streak decrements, floors
  at 0, never negative; no sidecar write when N is already 0.
- resolve("let-pass") / resolve("refused-stale-view") -> streak unchanged.
- resolve no-op (absent/already-closed) -> streak untouched, no sidecar
  write.
- at floor confidence an expired expectation still resurfaces: temporal
  record with the concern link + unresolved_concern trigger within a
  bounded window (the 2026-09-26 e2e, re-run at streak >= 10).
- empty inbox, no expiries, no answers -> sidecar untouched if absent,
  byte-identical if present; expectations dict untouched; no store write.

Narrow reading pinned (spec is silent): one sync that marks K expectations
"expired" increments the streak by K — the streak counts *failures*, and
two failures in one cycle are two failures, not one.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_expectation_policy.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import io
import json
import math
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.inbox_expectations import (
    BASE_CONFIDENCE,
    CONFIDENCE,
    CONFIDENCE_FLOOR,
    DECAY_FACTOR,
    TTL_TICKS,
    _confidence_for,
    _read_streak,
    resolve,
    sync,
)

BASE = Path(__file__).resolve().parents[1]


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="exppol-cli-"))
        self.saved = {}

    def __enter__(self):
        targets = {"DB": self.tmp / "t.db",
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

    def policy_path(self):
        return Path(cli.INBOX).parent / "expectation_policy.json"

    def streak(self):
        return _read_streak(self.policy_path())


def _write_prompt(pid, view_tick, view_sequence=0):
    payload = {
        "id": pid,
        "prompt": "test prompt",
        "view_tick": view_tick,
        "view_sequence": view_sequence,
        "experiences": [
            {"source": "invitation", "first_person": "test invitation"}],
    }
    (Path(cli.INBOX) / f"{pid}.json").write_text(
        json.dumps(payload), encoding="utf-8")


def _expectations():
    return cli._subject().continuity.state.expectations


def _tick():
    return cli._subject().engine.state.tick


def _stale_register(pid, ctx):
    """Write a stale prompt and sync it; return the registered Expectation."""
    _write_prompt(pid, view_tick=_tick() - TTL_TICKS - 1)
    out = sync(cli._subject(), cli.INBOX)
    assert out["registered"] == [f"inbox:{pid}"], out
    return _expectations()[f"inbox:{pid}"]


def _expire(pid, ctx):
    """Vanish the prompt file without answering; sync marks it expired."""
    (Path(cli.INBOX) / f"{pid}.json").unlink()
    out = sync(cli._subject(), cli.INBOX)
    assert out["expired"] == [f"inbox:{pid}"], out
    assert _expectations()[f"inbox:{pid}"].status == "expired"


# --- named constants, no magic numbers -------------------------------------

def test_policy_constants_are_named_and_exact():
    assert BASE_CONFIDENCE == 0.6 == CONFIDENCE
    assert DECAY_FACTOR == 0.8
    assert CONFIDENCE_FLOOR == 0.15
    for n in range(0, 100):
        c = _confidence_for(n)
        assert c == max(0.6 * 0.8 ** n, 0.15)
        assert c >= 0.15, f"confidence below floor at streak {n}: {c}"


# --- fresh policy -----------------------------------------------------------

def test_fresh_policy_registers_base_confidence():
    with CliOnTmp() as ctx:
        assert not ctx.policy_path().exists()
        ex = _stale_register("prompt-0001", ctx)
        assert ex.confidence == 0.6
        # Registration wrote no sidecar: nothing changed, so nothing persisted.
        assert not ctx.policy_path().exists(), \
            "registration wrote the sidecar without the streak changing"


# --- expiry increments, confidence decays geometrically ---------------------

def test_expiry_increments_streak_to_one():
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _expire("prompt-0001", ctx)
        assert ctx.streak() == 1
        assert json.loads(
            ctx.policy_path().read_text(encoding="utf-8")) == \
            {"expiry_streak": 1}


def test_confidence_decays_after_one_expiry():
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _expire("prompt-0001", ctx)
        ex = _stale_register("prompt-0002", ctx)
        assert math.isclose(ex.confidence, 0.48, rel_tol=1e-12), \
            ex.confidence


def test_confidence_decays_geometrically_over_three_expiries():
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _expire("prompt-0001", ctx)
        ex2 = _stale_register("prompt-0002", ctx)
        assert math.isclose(ex2.confidence, 0.48, rel_tol=1e-12)
        _expire("prompt-0002", ctx)
        assert ctx.streak() == 2
        ex3 = _stale_register("prompt-0003", ctx)
        assert math.isclose(ex3.confidence, 0.384, rel_tol=1e-12), \
            ex3.confidence
        _expire("prompt-0003", ctx)
        assert ctx.streak() == 3
        ex4 = _stale_register("prompt-0004", ctx)
        assert math.isclose(ex4.confidence, 0.3072, rel_tol=1e-12), \
            ex4.confidence


def test_streak_forced_to_20_floors_at_exactly_015():
    with CliOnTmp() as ctx:
        ctx.policy_path().write_text(
            json.dumps({"expiry_streak": 20}), encoding="utf-8")
        ex = _stale_register("prompt-0001", ctx)
        assert ex.confidence == 0.15, ex.confidence
        assert ex.confidence == CONFIDENCE_FLOOR


def test_multi_expiry_single_sync_increments_per_failure():
    # Narrow reading pin: one sync marking K expectations expired is K
    # failures, so the streak moves by K.
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _stale_register("prompt-0002", ctx)
        (Path(cli.INBOX) / "prompt-0001.json").unlink()
        (Path(cli.INBOX) / "prompt-0002.json").unlink()
        out = sync(cli._subject(), cli.INBOX)
        assert sorted(out["expired"]) == \
            ["inbox:prompt-0001", "inbox:prompt-0002"]
        assert ctx.streak() == 2


# --- answered decrements; let-pass / refused are neutral --------------------

def test_resolve_answered_decrements_streak():
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _expire("prompt-0001", ctx)
        _stale_register("prompt-0002", ctx)
        _expire("prompt-0002", ctx)
        assert ctx.streak() == 2
        item = resolve(cli._subject(), "prompt-0002", outcome="answered")
        assert item is not None and item.status == "confirmed"
        assert ctx.streak() == 1
        item = resolve(cli._subject(), "prompt-0001", outcome="answered")
        assert item is not None
        assert ctx.streak() == 0


def test_streak_never_goes_negative_and_zero_writes_nothing():
    with CliOnTmp() as ctx:
        ex = _stale_register("prompt-0001", ctx)
        assert ex.confidence == 0.6
        before = (ctx.policy_path().exists()
                  and ctx.policy_path().read_bytes() or None)
        item = resolve(cli._subject(), "prompt-0001", outcome="answered")
        assert item is not None
        # max(0, 0-1) = 0: unchanged, so no sidecar write — absent stays
        # absent.
        assert ctx.streak() == 0
        assert ctx.policy_path().exists() == (before is not None)
        # Same when the sidecar exists holding 0: byte-identical.
        ctx.policy_path().write_text(
            json.dumps({"expiry_streak": 0}), encoding="utf-8")
        raw = ctx.policy_path().read_bytes()
        ex2 = _stale_register("prompt-0002", ctx)
        resolve(cli._subject(), "prompt-0002", outcome="answered")
        assert ex2.confidence == 0.6
        assert ctx.policy_path().read_bytes() == raw, \
            "a no-change decrement touched the sidecar"


def test_resolve_let_pass_leaves_streak_unchanged():
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _expire("prompt-0001", ctx)
        assert ctx.streak() == 1
        raw = ctx.policy_path().read_bytes()
        item = resolve(cli._subject(), "prompt-0001", outcome="let-pass")
        assert item is not None and item.outcome == "let-pass"
        assert ctx.streak() == 1
        assert ctx.policy_path().read_bytes() == raw


def test_resolve_refused_stale_view_leaves_streak_unchanged():
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _expire("prompt-0001", ctx)
        assert ctx.streak() == 1
        raw = ctx.policy_path().read_bytes()
        item = resolve(cli._subject(), "prompt-0001",
                       outcome="refused-stale-view")
        assert item is not None and item.outcome == "refused-stale-view"
        assert ctx.streak() == 1
        assert ctx.policy_path().read_bytes() == raw


def test_resolve_noop_does_not_touch_streak():
    with CliOnTmp() as ctx:
        ctx.policy_path().write_text(
            json.dumps({"expiry_streak": 3}), encoding="utf-8")
        raw = ctx.policy_path().read_bytes()
        # Absent expectation: no-op.
        assert resolve(cli._subject(), "prompt-9999",
                       outcome="answered") is None
        assert ctx.streak() == 3
        assert ctx.policy_path().read_bytes() == raw
        # Already-closed expectation: no-op too.
        _stale_register("prompt-0001", ctx)
        resolve(cli._subject(), "prompt-0001", outcome="let-pass")
        assert resolve(cli._subject(), "prompt-0001",
                       outcome="answered") is None
        assert ctx.streak() == 3
        assert ctx.policy_path().read_bytes() == raw


# --- corrupt-but-present prompt files: never guessed ----------------------------

def _corrupt_prompt(pid):
    (Path(cli.INBOX) / f"{pid}.json").write_text("{not-json",
                                                 encoding="utf-8")


def _write_prompt_custom(filename, pid, view_tick):
    """Write a prompt whose payload id differs from its file stem."""
    payload = {
        "id": pid,
        "prompt": "test prompt",
        "view_tick": view_tick,
        "view_sequence": 0,
        "experiences": [
            {"source": "invitation", "first_person": "test invitation"}],
    }
    (Path(cli.INBOX) / filename).write_text(
        json.dumps(payload), encoding="utf-8")


def test_corrupt_divergent_id_stays_pending_streak_untouched():
    # Round 3: the payload id differs from the file stem ("id":
    # "custom-xyz" in file prompt-0002.json). The expectation registers
    # under the payload id; once the file corrupts, the stem is all we can
    # read — not enough to verify the prompt "vanished without settlement".
    # Fail closed means the expiry pass is deferred entirely: across 3
    # syncs the expectation stays pending, the streak stays 0, and no
    # sidecar is written.
    with CliOnTmp() as ctx:
        _write_prompt_custom("prompt-0002.json", "custom-xyz",
                             view_tick=_tick() - TTL_TICKS - 1)
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": ["inbox:custom-xyz"], "expired": [], "superseded": [], "lapsed": []}, \
            out
        _corrupt_prompt("prompt-0002")
        for _ in range(3):
            out = sync(cli._subject(), cli.INBOX)
            assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}, out
            assert _expectations()["inbox:custom-xyz"].status == "pending"
        assert ctx.streak() == 0
        assert not ctx.policy_path().exists()


def test_deleting_divergent_corrupt_file_expires_and_streaks_once():
    # Once the corrupt file is actually gone, the evidence is unambiguous
    # again: the expectation expires honestly and the streak increments by
    # exactly 1, not 1+1.
    with CliOnTmp() as ctx:
        _write_prompt_custom("prompt-0002.json", "custom-xyz",
                             view_tick=_tick() - TTL_TICKS - 1)
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": ["inbox:custom-xyz"], "expired": [], "superseded": [], "lapsed": []}, \
            out
        _corrupt_prompt("prompt-0002")
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}, out
        (Path(cli.INBOX) / "prompt-0002.json").unlink()
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [],
                       "expired": ["inbox:custom-xyz"],
                       "superseded": [], "lapsed": []}, out
        assert _expectations()["inbox:custom-xyz"].status == "expired"
        assert ctx.streak() == 1
        # A further sync changes nothing: the streak moved exactly once.
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}, out
        assert ctx.streak() == 1


def test_mixed_inbox_defers_all_expiry_until_corrupt_file_gone():
    # The conservative trade-off, pinned explicitly: one corrupt file plus
    # one genuinely vanished prompt — the vanished prompt's expiry is
    # deferred too (streak 0) until the corrupt file is removed, then both
    # expire normally (+2 streak). One unreadable file taints the whole
    # expiry pass; ambiguity is never split by guesswork.
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _stale_register("prompt-0002", ctx)
        _corrupt_prompt("prompt-0001")
        (Path(cli.INBOX) / "prompt-0002.json").unlink()  # genuinely gone
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}, out
        assert _expectations()["inbox:prompt-0001"].status == "pending"
        assert _expectations()["inbox:prompt-0002"].status == "pending"
        assert ctx.streak() == 0
        assert not ctx.policy_path().exists()
        (Path(cli.INBOX) / "prompt-0001.json").unlink()  # corrupt file gone
        out = sync(cli._subject(), cli.INBOX)
        assert sorted(out["expired"]) == \
            ["inbox:prompt-0001", "inbox:prompt-0002"], out
        assert ctx.streak() == 2


def test_corrupt_file_before_registration_is_skipped_not_registered():
    # Present but unreadable: fail closed means skip, not register.
    with CliOnTmp() as ctx:
        _corrupt_prompt("prompt-0001")
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}, out
        assert "inbox:prompt-0001" not in _expectations()
        assert ctx.streak() == 0


def test_corrupt_file_not_vanished_keeps_pending_across_syncs():
    # A pending expectation whose prompt file became unreadable is NOT
    # "vanished without settlement": it stays pending and the streak never
    # moves — a filesystem hiccup must not decay future confidence.
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _corrupt_prompt("prompt-0001")
        for _ in range(2):
            out = sync(cli._subject(), cli.INBOX)
            assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}, out
            assert _expectations()["inbox:prompt-0001"].status == "pending"
        assert ctx.streak() == 0
        assert not ctx.policy_path().exists()


def test_deleting_corrupt_file_then_expires_and_streaks_once():
    # Once the corrupt file is actually gone, the expectation expires
    # honestly and the streak increments by exactly 1.
    with CliOnTmp() as ctx:
        _stale_register("prompt-0001", ctx)
        _corrupt_prompt("prompt-0001")
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}, out
        (Path(cli.INBOX) / "prompt-0001.json").unlink()
        out = sync(cli._subject(), cli.INBOX)
        assert out == {"registered": [], "expired": ["inbox:prompt-0001"], "superseded": [], "lapsed": []}, out
        assert _expectations()["inbox:prompt-0001"].status == "expired"
        assert ctx.streak() == 1


# --- empty-inbox no-op: sidecar untouched ------------------------------------

def test_empty_inbox_noop_leaves_sidecar_untouched():
    with CliOnTmp() as ctx:
        sub = cli._subject()  # construction always rewrites the payload row
        mtime_before = Path(ctx.tmp / "t.db").stat().st_mtime_ns
        # Case A: sidecar absent stays absent.
        out = sync(sub, cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}
        assert not ctx.policy_path().exists()
        # Case B: sidecar present stays byte-identical.
        ctx.policy_path().write_text(
            json.dumps({"expiry_streak": 4}), encoding="utf-8")
        raw = ctx.policy_path().read_bytes()
        out = sync(sub, cli.INBOX)
        assert out == {"registered": [], "expired": [], "superseded": [], "lapsed": []}
        assert ctx.policy_path().read_bytes() == raw
        # mtime must be checked before any helper that constructs a subject
        # (construction itself rewrites the payload row).
        assert Path(ctx.tmp / "t.db").stat().st_mtime_ns == mtime_before, \
            "sync opened a write path on an empty inbox"
        assert dict(cli._subject().continuity.state.expectations) == {}


# --- floor does not silence the nag (e2e at streak >= 10) --------------------

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


def test_floor_confidence_still_resurfaces_at_streak_12():
    # The 2026-09-26 end-to-end, re-run at streak >= 10: at the 0.15 floor
    # the engine's own temporal machinery must still surface the concern —
    # decay must never become learned helplessness.
    tmp = Path(tempfile.mkdtemp(prefix="exppol-e2e-"))
    inbox = tmp / "inbox"
    inbox.mkdir()
    (tmp / "expectation_policy.json").write_text(
        json.dumps({"expiry_streak": 12}), encoding="utf-8")
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
    ex = sub.continuity.state.expectations["inbox:prompt-0001"]
    assert ex.confidence == CONFIDENCE_FLOOR, ex.confidence
    key = "expectation:inbox:prompt-0001"
    used = _run_until_unresolved(sub, key, 60)
    assert used is not None, \
        "unresolved_concern never fired at floor confidence within 60 ticks"
    temporals = [r for r in sub.inspect()["workspace"]["records"]
                 if r["source"] == "temporal"
                 and key in r.get("concern_links", ())]
    assert temporals, "no temporal record linked to the inbox expectation"


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
