"""Critic battery, round 2: interoceptive gap (felt vs. drive) mutation.

Re-verification after the builder's round-2 fixes (record_id provenance
via _ViewWithIds + track_view_ids fallback; BAND_NOISE_FLOOR filter).

Round-1 battery (test_critic_interoception_r1.py) must pass UNMODIFIED —
it does (13/13). This file attacks only the NEW code from the round-2
diff:

A. _ViewWithIds integrity: does it survive everything the engine does with
   a CognitiveView? Is positional correspondence airtight at both view()
   return sites? (The `room <= 0` pinned-saturation branch is unreachable
   given MAX_PINNED=6 < VIEW_LIMIT=16 — dead branch, noted; its
   construction helper is unit-tested directly.)
B. Queue-time stamping: view-carried ids vs track_view_ids fallback
   precedence, fail-closed on length mismatch, bare-provider legacy shape,
   queue_external, lazy-dereference staleness across workspace rebuilds.
C. `mind answer --silent` id-preferred join, end-to-end through the REAL
   cli._subject wiring: stale/malicious id -> skip with NO text fallback;
   legacy id-less -> text fallback; adversarial id/text mismatch -> id wins.
   Plus: no record_id leak into `mind inbox` display.
D. BAND_NOISE_FLOOR: is 0.02 principled (bounds the measured jitter wander)
   rather than tuned to the test?
E. Regression genome re-check against the changed areas: read-only
   violations (status/drift/tracker-construction must not write
   interoception.json), silent defaults (bare provider, keyless
   interoception records), shadowing (new method names).

Conventions: plain asserts, pytest-compatible, synthetic /tmp stores only.
    cd ~/workspace/calibos-mind && ./.venv/bin/python -m pytest tests/adversarial/test_critic_interoception_r2.py -q
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import calibos_mind.cli as cli
from calibos_mind.interoception import (
    BAND_NOISE_FLOOR,
    NOISE_SCALE,
    InteroceptionTracker,
)
from calibos_mind.provider import InboxCognition
from calibos_mind.workspace import (
    MAX_PINNED,
    VIEW_LIMIT,
    CalibosWorkspace,
    _view_with_ids,
)
from digital_subject.cartridge import load_cartridge
from jelly_psiduck.cognition import cognitive_prompt
from jelly_psiduck.workspace import CognitiveView, FeltExperience

ROOT = Path(__file__).resolve().parents[2]


def _needs(**over):
    base = {"hunger": 0.5, "thirst": 0.5, "fatigue": 0.5, "energy": 0.5}
    base.update(over)
    return base


class CliOnTmpRealWiring:
    """Redirect CLI paths at tmp but keep the REAL cli._subject.

    Unlike the r1 harness (which replaced _subject with a manual
    reimplementation), this exercises the shipped wiring under test:
    provider.track_queue_time + provider.track_view_ids as coded in
    cli._subject.
    """

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.paths = {
            "DB": tmp / "mind.db",
            "INBOX": tmp / "inbox",
            "DREAMS": tmp / "dreams",
            "SALIENCE": tmp / "salience.json",
            "INTEROCEPTION": tmp / "interoception.json",
            "PROPOSALS": tmp / "proposals",
            "ARCHIVE": tmp / "archive",
        }
        self.saved = {}
        self._cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def __enter__(self):
        for name, path in self.paths.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        assert cli.INTEROCEPTION != self.paths["INTEROCEPTION"]
        assert cli.DB != self.paths["DB"]


def _quiet(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args)
    return rc, buf.getvalue()


def _tmp_inbox():
    d = Path(tempfile.mkdtemp(prefix="critic-r2-inbox-")) / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _only_prompt(inbox: Path) -> dict:
    pids = sorted(inbox.glob("prompt-*.json"))
    assert len(pids) == 1, f"expected exactly one prompt, got {len(pids)}"
    return json.loads(pids[0].read_text(encoding="utf-8"))


def _exps(n=3):
    return tuple(FeltExperience("memory", f"synthetic memory {i}") for i in range(n))


def _ids(n=3, start=1):
    return tuple(f"experience-{start + i}" for i in range(n))


# --------------------------------------------------------------------------
# A. _ViewWithIds integrity
# --------------------------------------------------------------------------

def test_r2_view_ids_never_enter_prompt_text():
    """cognitive_prompt() — the actual engine renderer — must serialize only
    declared dataclass fields. record_ids rides in a non-dataclass slot, so
    asdict() cannot see it; the thinker must never meet a record id."""
    v = _view_with_ids(_exps(3), _ids(3))
    d = dataclasses.asdict(v)
    assert set(d.keys()) == {"experiences"}, f"asdict leaked fields: {set(d.keys())}"
    prompt = cognitive_prompt(v)
    for rid in _ids(3):
        assert rid not in prompt, f"record id {rid} leaked into prompt text"
    assert "record_ids" not in prompt and "record_id" not in prompt


def test_r2_view_ids_positional_main_branch():
    """Positional correspondence record_ids[i] <-> experiences[i] on the real
    view() path, WITH substitution active and a full 16-window."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-r2-pos-"))
    with CliOnTmpRealWiring(tmp):
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = cli._subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 31):  # felt hunger converged high (level 3)
            tr.update(_needs(hunger=0.95), tick)
        tr.save()
        with subject._transaction():
            subject._add("interoception", "I'm hungry.", concepts=("hunger",),
                         salience=0.9, intensity=0.9)
            for i in range(12):  # fill the window past the pinned roots
                subject._add("memory", f"positional probe memory {i}",
                             salience=0.9, intensity=0.9)
        view = subject.workspace.view()
        # Per-class caps (memory: 4, interoception: 2) bound the admitted
        # window: 6 pinned + 1 interoception + 4 memories = 11. The caps are
        # the point — positional correspondence must hold across classes.
        assert len(view.record_ids) == len(view.experiences) == 11, \
            f"unexpected window: {len(view.experiences)}"
        # Substitution was active inside this window (felt level 3 text).
        assert any(e.first_person == "It is hard to think past this: I'm hungry."
                   for e in view.experiences if e.source == "interoception"), \
            "expected the felt-substituted body text in the window"
        recs = {r.id: r for r in subject.workspace.records}
        for i, rid in enumerate(view.record_ids):
            r = recs[rid]
            felt = tr.text_for_record(r)
            expected = felt if felt is not None else r.first_person
            assert view.experiences[i].source == r.source, \
                f"position {i}: source mismatch for {rid}"
            assert view.experiences[i].first_person == expected, \
                f"position {i}: text mismatch for {rid}"


def test_r2_view_with_ids_binds_positionally_and_normalizes():
    """The construction helper (also used by the unreachable room<=0 pinned
    branch) binds ids positionally and normalizes to tuples."""
    exps = _exps(2)
    v = _view_with_ids(exps, ("experience-1", "experience-2"))
    assert v.record_ids == ("experience-1", "experience-2")
    assert v.experiences == exps
    v2 = _view_with_ids(list(exps), ["experience-1", "experience-2"])
    assert isinstance(v2.record_ids, tuple) and isinstance(v2.experiences, tuple)


def test_r2_view_stays_frozen_and_is_a_cognitive_view():
    """The engine's only real consumers are isinstance checks and asdict();
    both must hold, and the frozen contract must survive the subclass."""
    from dataclasses import FrozenInstanceError
    v = _view_with_ids(_exps(2), _ids(2))
    assert isinstance(v, CognitiveView)
    assert isinstance(hash(v), int)
    for attr in ("experiences", "record_ids"):
        try:
            setattr(v, attr, ())
        except FrozenInstanceError:
            pass
        else:
            raise AssertionError(f"view.{attr} is mutable")


def test_r2_copied_views_carry_no_ids_fail_closed():
    """Latent sharp edge, documented: copy/deepcopy/dataclasses.replace drop
    the non-field slot, so a copied view carries NO ids. The provider must
    then omit record_id (fail closed) rather than stamp a wrong one."""
    v = _view_with_ids(_exps(2), _ids(2))
    inbox = _tmp_inbox()
    for dup in (copy.copy(v), copy.deepcopy(v),
                dataclasses.replace(v, experiences=_exps(2))):
        assert getattr(dup, "record_ids", None) is None, \
            "copied view must not carry stale ids"
        provider = InboxCognition(inbox)
        provider.track_queue_time(lambda: (9, 9))  # wired clock, NO resolver
        provider.think(dup)
    for path in sorted(inbox.glob("prompt-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert all("record_id" not in e for e in payload["experiences"]), \
            "copied view must queue the legacy shape, never a guessed id"


# --------------------------------------------------------------------------
# B. Queue-time stamping: primary channel vs track_view_ids fallback
# --------------------------------------------------------------------------

def _wired_provider(inbox, clock=(7, 42), resolver=None):
    p = InboxCognition(inbox)
    p.track_queue_time(lambda: clock)
    if resolver is not None:
        p.track_view_ids(resolver)
    return p


def test_r2_think_stamps_view_carried_ids_positionally():
    inbox = _tmp_inbox()
    p = _wired_provider(inbox, resolver=lambda: ("experience-99",) * 3)
    p.think(_view_with_ids(_exps(3), _ids(3)))
    payload = _only_prompt(inbox)
    assert [e["record_id"] for e in payload["experiences"]] == list(_ids(3))


def test_r2_view_ids_take_precedence_over_resolver():
    """A lying resolver must not override ids bound to the view object."""
    inbox = _tmp_inbox()
    p = _wired_provider(inbox, resolver=lambda: ("experience-666",) * 3)
    p.think(_view_with_ids(_exps(3), _ids(3)))
    payload = _only_prompt(inbox)
    assert [e["record_id"] for e in payload["experiences"]] == list(_ids(3))


def test_r2_corrupt_view_ids_never_cascade_to_resolver():
    """View-carried ids with a length mismatch are corrupt: omit entirely,
    never cascade to the resolver for a second opinion (misattribution is
    the hazard; omission is the safe direction)."""
    inbox = _tmp_inbox()
    p = _wired_provider(inbox, resolver=lambda: _ids(3))
    view = _view_with_ids(_exps(3), ("experience-1",))  # corrupt: 1 id, 3 exps
    p.think(view)
    payload = _only_prompt(inbox)
    assert all("record_id" not in e for e in payload["experiences"])


def test_r2_foreign_view_uses_resolver_fallback():
    """A plain engine CognitiveView carries no ids; the wired resolver
    supplies them (the Bug C-style fallback actually fires)."""
    inbox = _tmp_inbox()
    p = _wired_provider(inbox, resolver=lambda: _ids(3))
    p.think(CognitiveView(_exps(3)))
    payload = _only_prompt(inbox)
    assert [e["record_id"] for e in payload["experiences"]] == list(_ids(3))


def test_r2_foreign_view_resolver_mismatch_omits():
    inbox = _tmp_inbox()
    p = _wired_provider(inbox, resolver=lambda: ("experience-1",))
    p.think(CognitiveView(_exps(3)))
    payload = _only_prompt(inbox)
    assert all("record_id" not in e for e in payload["experiences"])


def test_r2_foreign_view_no_resolver_legacy_shape():
    """Clock wired, no resolver, foreign view: legacy shape, no record_id."""
    inbox = _tmp_inbox()
    p = _wired_provider(inbox)
    p.think(CognitiveView(_exps(2)))
    payload = _only_prompt(inbox)
    assert all("record_id" not in e for e in payload["experiences"])


def test_r2_bare_provider_queues_legacy_shape():
    """Genome (silent defaults): a provider with no wired clock and no
    resolver is bare/legacy use — it must queue the legacy shape even when
    handed a _ViewWithIds. Stamping is part of the queue-time bundle."""
    inbox = _tmp_inbox()
    p = InboxCognition(inbox)  # neither track_queue_time nor track_view_ids
    p.think(_view_with_ids(_exps(2), _ids(2)))
    payload = _only_prompt(inbox)
    assert all("record_id" not in e for e in payload["experiences"])
    assert payload["view_tick"] is None and payload["view_sequence"] is None


def test_r2_queue_external_carries_no_record_id():
    """Externally authored prompts have no view and no records behind them."""
    inbox = _tmp_inbox()
    p = _wired_provider(inbox)
    p.queue_external("a relay message", source="invitation")
    payload = _only_prompt(inbox)
    assert payload["external"] is True
    assert all("record_id" not in e for e in payload["experiences"])


def test_r2_resolver_lazy_dereference_survives_workspace_rebuild():
    """The cli._subject wiring closes over `subject`, not `subject.workspace`:
    after a transaction rebuild replaces the workspace object, the resolver
    must read the NEW workspace's side-channel, never a dead object's."""
    inbox = _tmp_inbox()
    provider = InboxCognition(inbox)
    subject = SimpleNamespace(
        workspace=SimpleNamespace(_last_view_ids=("experience-1",)))
    # Same shape as cli._subject's wiring: lazy attribute dereference.
    provider.track_view_ids(lambda: subject.workspace._last_view_ids)
    provider.track_queue_time(lambda: (5, 5))
    # Transaction rebuild: the workspace object is replaced wholesale.
    subject.workspace = SimpleNamespace(
        _last_view_ids=("experience-9", "experience-10"))
    provider.think(CognitiveView(_exps(2)))
    payload = _only_prompt(inbox)
    assert [e["record_id"] for e in payload["experiences"]] == [
        "experience-9", "experience-10"]


# --------------------------------------------------------------------------
# C. `mind answer --silent` id-preferred join, end-to-end via real wiring
# --------------------------------------------------------------------------

def _setup_two_memories(tmp):
    """Init a store, add two distinguishable memory records, queue one prompt
    through the REAL cli._subject wiring. Returns (ctx, pid, idA, idB)."""
    ctx = CliOnTmpRealWiring(tmp)
    ctx.__enter__()
    assert cli.cmd_init(argparse.Namespace(force=True)) == 0
    provider = InboxCognition(ctx.paths["INBOX"])
    subject = cli._subject(provider=provider)  # shipped wiring applied here
    with subject._transaction():
        subject._add("memory", "alpha text one", salience=0.9, intensity=0.9)
        subject._add("memory", "beta text two", salience=0.9, intensity=0.9)
    recs = {r["id"]: r for r in subject.inspect()["workspace"]["records"]}
    idA = next(rid for rid, r in recs.items()
               if r["first_person"] == "alpha text one")
    idB = next(rid for rid, r in recs.items()
               if r["first_person"] == "beta text two")
    provider.think(subject.workspace.view())
    pids = sorted(ctx.paths["INBOX"].glob("prompt-*.json"))
    assert len(pids) == 1
    pid = pids[0].stem
    payload = json.loads(pids[0].read_text(encoding="utf-8"))
    assert any(e.get("record_id") == idA for e in payload["experiences"]), \
        "queue-time stamping missed record A through the real wiring"
    assert any(e.get("record_id") == idB for e in payload["experiences"]), \
        "queue-time stamping missed record B through the real wiring"
    return ctx, pids[0], pid, idA, idB


def _unengaged(tmp, rid):
    sal = json.loads((tmp / "salience.json").read_text(encoding="utf-8"))
    return sal.get("records", {}).get(rid, {}).get("unengaged", 0)


def test_r2_silent_answer_stale_id_skipped_no_text_fallback():
    """Bug C rule, end-to-end: an id naming no current record is skipped with
    NO text fallback — even when the experience text exactly matches a live
    record. Falling back would credit the wrong record."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-r2-stale-"))
    ctx, ppath, pid, idA, idB = _setup_two_memories(tmp)
    try:
        payload = json.loads(ppath.read_text(encoding="utf-8"))
        for e in payload["experiences"]:
            if e.get("record_id") == idA:
                e["record_id"] = "experience-9999"  # stale; text still A's
        ppath.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        rc, _ = _quiet(cli.cmd_answer,
                       argparse.Namespace(id=pid, silent=True, text=None))
        assert rc == 0
        assert _unengaged(tmp, idA) == 0, \
            "stale id fell back to text and penalized the wrong record"
    finally:
        ctx.__exit__()


def test_r2_silent_answer_legacy_idless_text_fallback():
    """Legacy prompts (queued before record_id existed) and queue_external
    prompts still join on (source, first_person) text."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-r2-legacy-"))
    ctx, ppath, pid, idA, idB = _setup_two_memories(tmp)
    try:
        payload = json.loads(ppath.read_text(encoding="utf-8"))
        for e in payload["experiences"]:
            e.pop("record_id", None)
        ppath.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        rc, _ = _quiet(cli.cmd_answer,
                       argparse.Namespace(id=pid, silent=True, text=None))
        assert rc == 0
        assert _unengaged(tmp, idA) == 1, "legacy text fallback stopped working"
        assert _unengaged(tmp, idB) == 1, "legacy text fallback stopped working"
    finally:
        ctx.__exit__()


def test_r2_silent_answer_id_beats_text_on_mismatch():
    """Adversarial: the id names record B while the text is record A's.
    The id must win — the text join is the fallback, never the override."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-r2-mismatch-"))
    ctx, ppath, pid, idA, idB = _setup_two_memories(tmp)
    try:
        payload = json.loads(ppath.read_text(encoding="utf-8"))
        for e in payload["experiences"]:
            if e.get("record_id") == idA:
                e["record_id"] = idB  # id says B, text still says A
        ppath.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        rc, _ = _quiet(cli.cmd_answer,
                       argparse.Namespace(id=pid, silent=True, text=None))
        assert rc == 0
        # B was already penalized once as itself in the same payload run;
        # the mismatched experience must add exactly one more to B, none to A.
        assert _unengaged(tmp, idB) == 2, \
            f"id did not win over text: B={_unengaged(tmp, idB)}"
        assert _unengaged(tmp, idA) == 0, \
            f"text overrode the id: A={_unengaged(tmp, idA)}"
    finally:
        ctx.__exit__()


def test_r2_inbox_display_hides_record_id():
    """`mind inbox` renders [source] + truncated text only; the private
    provenance in the payload file must never reach the display."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-r2-inbox-"))
    with CliOnTmpRealWiring(tmp):
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        provider = InboxCognition(cli.INBOX)
        subject = cli._subject(provider=provider)
        with subject._transaction():
            subject._add("memory", "display probe memory",
                         salience=0.9, intensity=0.9)
        provider.think(subject.workspace.view())
        payload = _only_prompt(cli.INBOX)
        assert any("record_id" in e for e in payload["experiences"])
        rc, out = _quiet(cli.cmd_inbox, argparse.Namespace())
        assert rc == 0
        assert "experience-" not in out, \
            f"record id leaked into mind inbox display:\n{out}"
        assert "record_id" not in out


# --------------------------------------------------------------------------
# D. BAND_NOISE_FLOOR: principled bound, or tuned to the test?
# --------------------------------------------------------------------------

def test_r2_noise_floor_is_derived_not_magic():
    """The floor must be derived from the named noise constant, not a magic
    literal tuned to make a test pass."""
    assert BAND_NOISE_FLOOR == 5 * NOISE_SCALE
    import inspect
    import calibos_mind.interoception as mod
    src = inspect.getsource(mod)
    assert "BAND_NOISE_FLOOR = 5 * NOISE_SCALE" in src


def test_r2_noise_floor_bounds_pinned_baseline_wander():
    """The code comment justifies the floor as bounding 'the short-horizon
    pure-jitter wander (measured <=0.018 over the pinned baseline scenario)'.

    Reproduce that scenario: needs pinned at baseline for the 25-tick
    fitness horizon, default seed. The max |felt - 0.5| must stay within the
    floor — otherwise pure seeded jitter renders felt bands, and the floor
    does not do what its own documentation claims.
    """
    tmp = Path(tempfile.mkdtemp(prefix="critic-r2-floor-"))
    tr = InteroceptionTracker(tmp / "i.json")
    keys = ["hunger", "thirst", "fatigue", "energy"]
    maxdev = 0.0
    for tick in range(1, 26):
        tr.update({k: 0.5 for k in keys}, tick)
        for k in keys:
            maxdev = max(maxdev, abs(tr.data["needs"][k]["felt"] - 0.5))
    assert maxdev <= BAND_NOISE_FLOOR, (
        f"pinned-baseline jitter wander {maxdev:.4f} exceeds the "
        f"BAND_NOISE_FLOOR={BAND_NOISE_FLOOR} that claims to bound it "
        f"(comment also claims a <=0.018 measurement, not reproduced)")


def test_r2_calm_body_stays_all_settled_over_time():
    """A body pinned at baseline is genuinely settled: felt_bands() must not
    flicker bands from pure seeded jitter over a long calm run. (Steady-state
    jitter sigma is ~0.012; a 0.02 floor is breached ~11% of samples.)"""
    tmp = Path(tempfile.mkdtemp(prefix="critic-r2-calm-"))
    tr = InteroceptionTracker(tmp / "i.json")
    keys = ["hunger", "thirst", "fatigue", "energy"]
    flickers = 0
    ticks = 500
    for tick in range(1, ticks + 1):
        tr.update({k: 0.5 for k in keys}, tick)
        if tr.felt_bands():
            flickers += 1
    assert flickers == 0, (
        f"calm body rendered felt bands on {flickers}/{ticks} ticks "
        f"from pure seeded jitter — the noise floor does not bound the noise")


# --------------------------------------------------------------------------
# E. Regression genome re-check against the changed areas
# --------------------------------------------------------------------------

def test_r2_read_only_paths_never_write_interoception_sidecar():
    """Genome (read-only violations): constructing the tracker, `mind
    status` (both modes), and `mind drift` must not touch
    interoception.json — byte-identical before/after. The constructor must
    not even create the file."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-r2-ro-"))
    ghost = tmp / "ghost.json"
    InteroceptionTracker(ghost)  # read-only construction...
    assert not ghost.exists(), "tracker construction created the sidecar file"
    with CliOnTmpRealWiring(tmp):
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = cli._subject()
        for tick in range(1, 6):
            tr = subject.workspace.interoception_tracker
            tr.update(_needs(hunger=0.8), tick)
            tr.save()
        sidecar = Path(cli.INTEROCEPTION)
        before = sidecar.read_bytes()
        _quiet(cli.cmd_status, argparse.Namespace(raw=False))
        _quiet(cli.cmd_status, argparse.Namespace(raw=True))
        _quiet(cli.cmd_drift, argparse.Namespace(window=10))
        assert sidecar.read_bytes() == before, \
            "a read-only command modified interoception.json"


def test_r2_keyless_interoception_records_pass_through():
    """Genome (silent defaults): interoception records with no need key in
    concepts (recall unease, concern influence, prospective uncertainty)
    must pass through byte-identical — text_for_record returns None, never
    a quiet zero or a guess."""
    tmp = Path(tempfile.mkdtemp(prefix="critic-r2-keyless-"))
    with CliOnTmpRealWiring(tmp):
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = cli._subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 11):
            tr.update(_needs(hunger=0.95), tick)
        tr.save()
        with subject._transaction():
            subject._add("interoception",
                         "An old unease surfaces without a clear source.",
                         concepts=("recall",), salience=0.9, intensity=0.9)
        view = subject.workspace.view()
        texts = [e.first_person for e in view.experiences
                 if e.source == "interoception"]
        assert texts == ["An old unease surfaces without a clear source."], texts
        assert tr.text_for_record(
            next(r for r in subject.workspace.records
                 if r.source == "interoception")) is None


def test_r2_no_shadowed_definitions():
    """Genome (shadowing): every new/changed definition exists exactly once.
    The dream battery pins exactly one `def track_ids`; the new fallback is
    deliberately named track_view_ids so it cannot shadow it."""
    checks = {
        "provider": ["def track_view_ids", "def track_ids",
                     "def queue_external", "def pending", "def consume"],
        "workspace": ["class _ViewWithIds", "def _view_with_ids",
                      "def _view_experience", "def view"],
        "interoception": ["def felt_bands", "def need_key", "def text_for_record",
                          "def update", "def reset"],
        "cli": ["def cmd_status", "def cmd_answer", "def _subject",
                "def _run_tick"],
    }
    for mod, names in checks.items():
        lines = (ROOT / "calibos_mind" / f"{mod}.py").read_text(
            encoding="utf-8").splitlines()
        for name in names:
            hits = [l for l in lines
                    if l.startswith(name + "(") or l.startswith("    " + name + "(")]
            assert len(hits) == 1, \
                f"{mod}.py: {name} defined {len(hits)}x (shadowing?)"


def test_r2_dream_ticks_never_touch_felt_state():
    """Genome (dream/conduct isolation): the interoception update lives in
    cli._run_tick only; subject.dream_tick() must not move felt values."""
    import inspect
    from calibos_mind import subject as subject_mod
    src = inspect.getsource(subject_mod.CalibosSubject.dream_tick)
    assert "interoception" not in src and "update(" not in src
    cli_src = (ROOT / "calibos_mind" / "cli.py").read_text(encoding="utf-8")
    assert cli_src.count("tracker.update(") == 1, \
        "interoception update must have exactly one call site (_run_tick)"
