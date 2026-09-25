"""Critic battery, round 1: interoceptive gap (felt vs. drive) mutation.

Cold adversarial review of calibos_mind/interoception.py + the view
substitution in CalibosWorkspace.view() + the _run_tick hook + `mind
status` bands.

Conventions: plain asserts, pytest-compatible, runnable directly:
    cd ~/workspace/calibos-mind && ./.venv/bin/python tests/adversarial/test_critic_interoception_r1.py
All fixtures live in /tmp — the live store is never touched.

EXPECTED FAILURES (the critic's objections):
- test_critic_silent_answer_applies_unengaged_penalty_through_substitution
  (MAJOR): `mind answer --silent` joins prompt experiences to records on
  (source, first_person) text. View substitution re-renders body
  interoception text from felt urgency, so the join silently misses and the
  unengaged-salience penalty is never applied to body records the thinker
  saw but let pass. Spec claims salience untouched; it is not.
- test_critic_felt_bands_empty_at_baseline (MINOR): felt_bands() docstring
  promises "only needs felt off-baseline", but at exact baseline every need
  reads level 1 (urgency 0.5 >= 0.45 — the builder's own hysteresis test
  asserts this), so a calm body renders as all-"stirring" and `mind status`
  can never print "all settled" again.

Everything else below is expected to PASS, locking the invariants the
mutation gets right (regression genome coverage noted per test).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind.interoception import (
    HYSTERESIS_MARGIN,
    THRESHOLDS,
    InteroceptionTracker,
    felt_level,
    felt_text,
    urgency,
)
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject
from calibos_mind.workspace import CalibosWorkspace
from digital_subject.cartridge import load_cartridge

# Engine ground truth: the graded vocabulary this mutation claims to reuse,
# never fork. Verified against jelly_psiduck/endogenous.py::_project_body
# and jelly_psiduck/firewall.py (2026-09-25).
ENGINE_THRESHOLDS = (0.45, 0.65, 0.85)
ENGINE_HYSTERESIS_MARGIN = 0.03
ENGINE_LOW_IS_BAD = frozenset({"energy", "warmth", "comfort", "safety",
                               "focus", "satisfaction"})


def _needs(**over):
    base = {"hunger": 0.5, "thirst": 0.5, "fatigue": 0.5, "energy": 0.5}
    base.update(over)
    return base


class CliOnTmp:
    """Redirect every CLI path the mutation touches at tmp; restore after."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.paths = {
            "DB": tmp / "mind.db",
            "INBOX": tmp / "inbox",
            "SALIENCE": tmp / "salience.json",
            "INTEROCEPTION": tmp / "interoception.json",
            "PROPOSALS": tmp / "proposals",
            "ARCHIVE": tmp / "archive",
            "DREAMS": tmp / "dreams",
        }
        self.saved = {}
        self._cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def __enter__(self):
        for name, path in self.paths.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)

        def make_subject(provider=None):
            if provider is None:
                provider = InboxCognition(self.paths["INBOX"])
            return CalibosSubject(
                str(self.paths["DB"]), self._cartridge, cognition=provider,
                salience_path=str(self.paths["SALIENCE"]),
                interoception_path=str(self.paths["INTEROCEPTION"]))

        self.saved["_subject"] = cli._subject
        cli._subject = make_subject
        self.make_subject = make_subject
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        # Genome: fixture redirects must never leak into the live CLI.
        assert cli.INTEROCEPTION != self.paths["INTEROCEPTION"]
        assert cli.DB != self.paths["DB"]


def _quiet(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args)
    return rc, buf.getvalue()


# --------------------------------------------------------------------------
# OBJECTION 1 (MAJOR): --silent unengaged-penalty join broken by substitution
# --------------------------------------------------------------------------

def test_critic_silent_answer_applies_unengaged_penalty_through_substitution():
    """`mind answer --silent` must apply the unengaged salience penalty to
    every surfaced record the thinker let pass — including body
    interoception records whose prompt text was re-rendered from felt.

    FAILS: the (source, first_person) join in cmd_answer misses the true
    record whenever felt text != true text, so the penalty is silently
    skipped. The dream-rehearsal path already solved this exact problem by
    stamping record_id; the prompt schema has no such key.
    """
    tmp = Path(tempfile.mkdtemp(prefix="critic-silent-"))
    with CliOnTmp(tmp) as ctx:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = ctx.make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 31):  # felt hunger converged high (level 3)
            tr.update(_needs(hunger=0.95), tick)
        tr.save()
        assert tr.data["needs"]["hunger"]["level"] == 3

        # True body record at engine-level-2 text; the view renders it from
        # felt at level 3 — the gap the mutation exists to create.
        # Control: a memory record, which passes through byte-identical.
        with subject._transaction():
            subject._add("interoception", "I'm hungry.", concepts=("hunger",),
                         salience=0.9, intensity=0.9)
            subject._add("memory", "I remember the garden gate.", salience=0.9)

        view = subject.workspace.view()
        body_texts = [e.first_person for e in view.experiences
                      if e.source == "interoception"]
        assert body_texts == ["It is hard to think past this: I'm hungry."], body_texts

        provider = InboxCognition(ctx.paths["INBOX"])
        provider.track_queue_time(
            lambda: (subject.engine.state.tick, subject.workspace.sequence))
        provider.think(view)
        pids = sorted(ctx.paths["INBOX"].glob("prompt-*.json"))
        assert len(pids) == 1
        pid = pids[0].stem

        recs = {r["id"]: r for r in subject.inspect()["workspace"]["records"]}
        body_rid = [rid for rid, r in recs.items()
                    if r["source"] == "interoception"
                    and "hunger" in (r.get("concepts") or [])][0]
        mem_rid = [rid for rid, r in recs.items()
                   if r["first_person"] == "I remember the garden gate."][0]
        assert recs[body_rid]["first_person"] == "I'm hungry."  # true text

        rc, _ = _quiet(cli.cmd_answer,
                       argparse.Namespace(id=pid, silent=True, text=None))
        assert rc == 0
        sal = json.loads(ctx.paths["SALIENCE"].read_text(encoding="utf-8"))
        mem_uneng = sal["records"][mem_rid]["unengaged"]
        body_uneng = sal.get("records", {}).get(body_rid, {}).get("unengaged", 0)
        assert mem_uneng == 1, "control: pass-through record must get the penalty"
        assert body_uneng == 1, (
            f"body record let pass with no unengaged penalty "
            f"(felt text != true text broke the join): body={body_uneng}")


# --------------------------------------------------------------------------
# OBJECTION 2 (MINOR): felt_bands() contradicts its own "off-baseline" contract
# --------------------------------------------------------------------------

def test_critic_felt_bands_empty_at_baseline():
    """felt_bands() docstring: 'only needs felt off-baseline'. At exact
    baseline, felt_level() is 1 for every need (urgency 0.5 >= 0.45 — the
    builder's own hysteresis test asserts felt_level(0.5, 'hunger', 0) == 1),
    so the `level > 0` filter includes them all and `mind status` on a calm
    body prints every need as 'stirring', never 'all settled'.

    FAILS: docstring/behavior mismatch; the pre-mutation display reported
    'all settled' at baseline.
    """
    tmp = Path(tempfile.mkdtemp(prefix="critic-bands-"))
    with CliOnTmp(tmp) as ctx:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = ctx.make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 6):
            tr.update(_needs(), tick)  # every need exactly at baseline
        tr.save()
        bands = tr.felt_bands()
        assert bands == {}, f"baseline needs must be excluded, got {bands}"
        _, out = _quiet(cli.cmd_status, argparse.Namespace(raw=False))
        assert "all settled" in out, out


# --------------------------------------------------------------------------
# Genome + spec invariants the mutation gets right (must stay green)
# --------------------------------------------------------------------------

def test_critic_non_interoception_records_byte_identical():
    """Spec fitness 2 (part 2): with a tracker attached, every
    non-interoception record renders byte-identical — same (source, text),
    same order — as the tracker-less view."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-ident-"))
    tr = InteroceptionTracker(tmp / "i.json")
    for tick in range(1, 31):
        tr.update(_needs(hunger=0.95), tick)
    tr.save()

    def build(tracker):
        ws = CalibosWorkspace()
        rows = [
            (1, "memory", "I remember the garden gate.", ()),
            (2, "thought", "I should prepare for the meeting.", ()),
            (3, "perception", "The room is quiet.", ()),
            (4, "social", "Mara smiled at me.", ()),
            (5, "interoception", "I'm hungry.", ("hunger",)),
            (6, "interoception", "Remembering that leaves me uneasy.", ()),
            (7, "temporal", "The time I expected has passed: the call.", ()),
        ]
        for tick, source, text, concepts in rows:
            ws.add(tick, source, text, concepts=concepts)
        ws.interoception_tracker = tracker
        try:
            return ws.view()
        finally:
            ws.interoception_tracker = None

    with_tracker = build(tr)
    without_tracker = build(None)
    seq = lambda v: [(e.source, e.first_person) for e in v.experiences
                     if e.source != "interoception"]
    assert seq(with_tracker) == seq(without_tracker)
    # And the non-body interoception (no need key) passes through too.
    nb = [e.first_person for e in with_tracker.experiences
          if e.source == "interoception"]
    assert "Remembering that leaves me uneasy." in nb
    assert "It is hard to think past this: I'm hungry." in nb


def test_critic_last_view_ids_see_true_ids():
    """Genome (Bug C): the _last_view_ids side-channel must see TRUE record
    ids even when substitution re-renders the text — substitution is the
    last step, after the provenance capture."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-ids-"))
    tr = InteroceptionTracker(tmp / "i.json")
    for tick in range(1, 31):
        tr.update(_needs(hunger=0.95), tick)
    tr.save()
    ws = CalibosWorkspace()
    ws.add(1, "interoception", "I'm hungry.", concepts=("hunger",))
    ws.add(2, "memory", "I remember the gate.", concepts=())
    ws.interoception_tracker = tr
    try:
        view = ws.view()
    finally:
        ws.interoception_tracker = None
    true_ids = [r.id for r in ws.records]
    assert list(ws._last_view_ids) == true_ids, ws._last_view_ids
    assert len(view.experiences) == len(ws._last_view_ids)
    # The substituted experience still sits behind the true record id.
    assert ws._last_view_ids[0] == true_ids[0]


def test_critic_dedupe_and_caps_see_true_text():
    """Dedupe and per-class caps must be computed on TRUE record text, not
    felt renderings: two true texts that are NOT near-duplicates must both
    survive even when substitution renders them identically."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-dedupe-"))
    tr = InteroceptionTracker(tmp / "i.json")
    for tick in range(1, 31):
        tr.update(_needs(hunger=0.95), tick)
    tr.save()
    ws = CalibosWorkspace()
    # True texts: Jaccard 0.3 — not near-duplicates. Felt: both level 3.
    ws.add(1, "interoception", "I'm hungry.", concepts=("hunger",))
    ws.add(2, "interoception", "It is hard to think past this: I'm hungry.",
           concepts=("hunger",))
    ws.interoception_tracker = tr
    try:
        view = ws.view()
    finally:
        ws.interoception_tracker = None
    texts = [e.first_person for e in view.experiences
             if e.source == "interoception"]
    assert len(texts) == 2, texts  # dedupe saw true text, kept both
    assert texts[0] == texts[1] == \
        "It is hard to think past this: I'm hungry."  # felt rendering


def test_critic_substitution_does_not_reorder():
    """Substitution must not change view ordering: ids in view order are
    identical with and without the tracker."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-order-"))
    tr = InteroceptionTracker(tmp / "i.json")
    for tick in range(1, 31):
        tr.update(_needs(hunger=0.2), tick)
    tr.save()

    def ids(tracker):
        ws = CalibosWorkspace()
        ws.add(1, "memory", "I remember the gate.", concepts=())
        ws.add(2, "interoception", "It is hard to think past this: I'm hungry.",
               concepts=("hunger",))
        ws.add(3, "thought", "Something to consider.", concepts=())
        ws.interoception_tracker = tracker
        try:
            ws.view()
            return list(ws._last_view_ids)
        finally:
            ws.interoception_tracker = None

    assert ids(tr) == ids(None)


def test_critic_read_only_commands_never_write():
    """Genome (read-only violations): drift/status/review must not touch the
    sidecar's write path at all — proved by patching save() to raise, not
    just by byte comparison."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-ro-"))
    with CliOnTmp(tmp) as ctx:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = ctx.make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 10):
            tr.update(_needs(hunger=0.8), tick)
        tr.save()
        digest = lambda: hashlib.sha256(
            ctx.paths["INTEROCEPTION"].read_bytes()).hexdigest()
        before = digest()
        orig_save = InteroceptionTracker.save

        def boom(self):
            raise AssertionError("read-only command touched the write path")
        InteroceptionTracker.save = boom
        try:
            for name, fn, ns in [
                    ("drift", cli.cmd_drift, argparse.Namespace(window=10)),
                    ("status", cli.cmd_status, argparse.Namespace(raw=False)),
                    ("review", cli.cmd_review, argparse.Namespace(n=5))]:
                rc, _ = _quiet(fn, ns)
                assert rc == 0, name
            subject.dream_tick()
            subject.dream_tick()
        finally:
            InteroceptionTracker.save = orig_save
        assert digest() == before, "sidecar bytes changed"


def test_critic_dream_freezes_felt_values():
    """Spec: dream ticks freeze the body, so felt values AND last_tick must
    freeze with it — not just the file bytes, the felt trajectory."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-dream-"))
    with CliOnTmp(tmp) as ctx:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = ctx.make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 10):
            tr.update(_needs(hunger=0.8), tick)
        tr.save()
        frozen = {k: (e["felt"], e["last_tick"], e["level"])
                  for k, e in tr.data["needs"].items()}
        subject.dream_tick()
        subject.dream_tick()
        # Re-read from disk: nothing may have moved.
        retr = InteroceptionTracker(ctx.paths["INTEROCEPTION"])
        now = {k: (e["felt"], e["last_tick"], e["level"])
               for k, e in retr.data["needs"].items()}
        assert now == frozen, (frozen, now)


def test_critic_full_run_determinism():
    """Spec fitness 3, end-to-end: two full synthetic runs (init, scripted
    waking ticks with need shocks, interleaved dream ticks) produce
    byte-identical sidecars."""
    def run(tag):
        tmp = Path(tempfile.mkdtemp(prefix=f"critic-det-{tag}-"))
        with CliOnTmp(tmp) as ctx:
            assert cli.cmd_init(argparse.Namespace(force=True)) == 0
            subject = ctx.make_subject()
            script = [0.5] * 3 + [0.9] * 8 + [0.2] * 10 + [0.7] * 6
            with contextlib.redirect_stdout(io.StringIO()):
                for v in script:
                    subject.engine.state.needs["hunger"] = v
                    subject.engine.state.needs["thirst"] = 1.0 - v
                    cli._run_tick(subject)
                subject.dream_tick()
                subject.dream_tick()
                for v in [0.4] * 4:
                    subject.engine.state.needs["energy"] = v
                    cli._run_tick(subject)
            return hashlib.sha256(
                ctx.paths["INTEROCEPTION"].read_bytes()).hexdigest()
    assert run("a") == run("b")


def test_critic_reseed_restores_defaults():
    """Genome (sidecar reset on reseed): init --force clears felt state AND
    restores default params/seed — no stale felt on recycled ids, no stale
    tuning either."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-reseed-"))
    with CliOnTmp(tmp) as ctx:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        tr = InteroceptionTracker(ctx.paths["INTEROCEPTION"],
                                  params={"rate_onset": 1.0, "noise_scale": 0.0})
        for tick in range(1, 20):
            tr.update(_needs(hunger=0.95), tick)
        tr.save()
        assert tr.data["needs"]["hunger"]["level"] == 3
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        payload = json.loads(ctx.paths["INTEROCEPTION"].read_text(encoding="utf-8"))
        assert payload["needs"] == {}, payload["needs"]
        assert payload["params"] == {"rate_onset": 0.35, "rate_offset": 0.12,
                                     "noise_scale": 0.01}, payload["params"]
        assert payload["seed"] == 0, payload["seed"]


def test_critic_status_fresh_store_all_settled():
    """No felt state yet (no tick ever ran): no silent defaults — status
    reports 'all settled', never invented bands."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-fresh-"))
    with CliOnTmp(tmp) as ctx:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        _, out = _quiet(cli.cmd_status, argparse.Namespace(raw=False))
        assert "all settled" in out, out
        assert "stirring" not in out and "urgent" not in out, out


def test_critic_engine_vocabulary_parity():
    """The 'reused — never forked' invariant: thresholds, hysteresis margin,
    urgency mapping, and graded vocabulary match the engine exactly."""
    assert THRESHOLDS == ENGINE_THRESHOLDS
    assert HYSTERESIS_MARGIN == ENGINE_HYSTERESIS_MARGIN
    from jelly_psiduck.firewall import LOW_IS_BAD as ENGINE_LIBS, NEED_LANGUAGE
    import calibos_mind.interoception as I
    assert set(ENGINE_LIBS) == set(ENGINE_LOW_IS_BAD)
    # Urgency mapping parity on both sides of LOW_IS_BAD.
    assert urgency("hunger", 0.2) == 0.2
    assert urgency("energy", 0.2) == 0.8
    # Hysteresis parity: engine keeps the previous level while urgency stays
    # within .03 below the threshold it fell from.
    assert felt_level(0.83, "hunger", 3) == 3
    assert felt_level(0.81, "hunger", 3) == 2
    # Vocabulary parity with EndogenousSubject._project_body (rising texts;
    # level 0 keeps the engine's own easing text — documented).
    for key, desc in NEED_LANGUAGE.items():
        assert felt_text(key, 1) == f"I am beginning to notice this: {desc}"
        assert felt_text(key, 2) == desc
        assert felt_text(key, 3) == f"It is hard to think past this: {desc}"
    assert felt_text("hunger", 0) == "That feeling is easing."
    assert I.BASELINE == 0.5  # the engine's own needs default


def test_critic_no_shadowed_definitions():
    """Genome (shadowing definitions): no duplicate top-level def/class
    names in the touched modules — a second definition would silently win."""
    import ast
    root = Path(__file__).resolve().parents[2]
    for rel in ("calibos_mind/interoception.py", "calibos_mind/cli.py",
                "calibos_mind/workspace.py", "calibos_mind/subject.py"):
        src = (root / rel).read_text(encoding="utf-8")
        names = [n.name for n in ast.walk(ast.parse(src))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef))]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"{rel}: shadowed definitions {dupes}"


def _main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - battery reports, never hides
            failed += 1
            print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {fn.__name__}")
    print(f"{len(fns) - failed}/{len(fns)} critic tests passed; "
          f"{failed} failing objections")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _main()