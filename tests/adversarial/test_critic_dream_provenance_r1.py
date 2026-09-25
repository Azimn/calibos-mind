"""Critic battery R1 for Bug C: dream rehearsal provenance by id.

Cold read of the diff (provider.py / workspace.py / cli.py / salience.py).
Every claim below is verified against the installed frozen engine
(jelly_psiduck in .venv) and executed, not taken from the spec.

Verdict basis:
- Synchronization: the only two live think() call sites in the repo
  (engine runtime.py:192 waking tick, CalibosSubject._sleep_tick dream tick)
  both use the single expression ``self.cognition.think(self.workspace.view())``
  in a single-threaded interpreter, and DreamCognition.think() reads the
  resolver as its first statement. No engine path builds a view, builds
  another view, then thinks the first; no path calls think() twice for one
  view through the engine. The equal-length stale view is therefore
  unreachable through the engine — but it IS the side-channel's failure
  mode, so this battery pins the immediate-handoff invariant through real
  dream ticks and documents the hazard explicitly.
- Positional order: ``tuple(r.id for r in window)`` iterates the identical
  ``window`` list the view experiences are built from, in both branches.
  The ``room <= 0`` branch is dead (MAX_PINNED=6 < VIEW_LIMIT=16, pinned is
  hard-truncated) — pinned as a test so the deadness is explicit.
- Premise: FeltExperience is @dataclass(frozen=True, slots=True) with only
  (source, first_person) — verified against the install. No .id attribute,
  and slots+frozen means a provider cannot attach one to an engine-built
  instance either. The side-channel is genuinely necessary for
  engine-constructed views.
- Fitness, display privacy, Bug B idempotence, and the regression genome
  are attacked item by item below.

All fixtures are synthetic /tmp stores and /tmp dream dirs. The live store
is never opened.
"""
from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jelly_psiduck.workspace import FeltExperience

import calibos_mind.workspace as ws_mod
from calibos_mind.provider import DreamCognition
from calibos_mind.salience import SalienceTracker
from calibos_mind.workspace import CalibosWorkspace

SAME_TEXT = "i left the kettle on the stove"
OTHER_TEXT = "the train was late again"


def _tracker(tmp_path):
    return SalienceTracker(tmp_path / "salience.json")


def _records():
    return [
        {"id": "r1", "tick": 10, "source": "memory", "first_person": SAME_TEXT},
        {"id": "r2", "tick": 20, "source": "memory", "first_person": SAME_TEXT},
        {"id": "r3", "tick": 30, "source": "memory", "first_person": OTHER_TEXT},
    ]


def _id_exp(rid, text):
    return {"source": "memory", "first_person": text, "record_id": rid}


def _old_exp(text):
    return {"source": "memory", "first_person": text}


def _write_log(dream_dir, name, frags):
    path = dream_dir / name
    with path.open("w", encoding="utf-8") as f:
        for tick, exps in frags:
            f.write(json.dumps({"tick": tick, "trigger": "echo",
                                "experiences": exps}) + "\n")
    return path


def _recalls(t, rid):
    return sorted(t.data["records"][rid]["recalls"])


def _e2e_subject(tmp_path):
    """A real CalibosSubject on a synthetic store, wired like cmd_dream."""
    from calibos_mind.subject import CalibosSubject
    from digital_subject.cartridge import load_cartridge
    base = Path(__file__).resolve().parents[2]
    tmp = tmp_path / "e2e"
    tmp.mkdir(exist_ok=True)
    dreamer = DreamCognition(tmp / "dreams")
    cart = load_cartridge(base / "calibos.toml")
    sub = CalibosSubject(tmp / "test.db", cart, cognition=dreamer,
                         salience_path=tmp / "salience.json")
    dreamer.track_ids(lambda: sub.workspace._last_view_ids)
    return sub, dreamer, tmp


# -- 1. synchronization: the immediate-handoff invariant ----------------------


def _seed_dreamable(sub, n_memories=3):
    with sub._transaction():
        for i in range(n_memories):
            sub._add("memory", f"a synthetic lived memory number {i} for sync",
                     concepts=("synthetic",), generated_by="cognition")
        tick = sub.engine.state.tick
        sub.endogenous["echoes"] = [{
            "text": "a half-remembered image of rain on a window",
            "parent": "experience-1", "due": tick,
            "expires": tick + 60, "depth": 1}]


def test_sync_pin_immediate_handoff_through_real_dream_ticks(tmp_path, monkeypatch):
    """Pin the invariant the side-channel rests on, through real dream
    ticks: every think() call's resolver value (read at think entry) equals
    the ids of the view built immediately before it — the event log must
    read view->think->view->think with matching ids, never a stale view.

    NOTE on machinery: CalibosSubject._restore rebuilds the workspace from
    the DB payload on EVERY _transaction (verified against the installed
    engine), so the workspace object is not stable across ticks. The
    resolver lambda dereferences subject.workspace lazily, which is exactly
    why it survives replacement — this test instruments via a monkeypatched
    class so every rebuilt workspace logs too. If any future change inserts
    a view() between the handoff, caches a view across a think, or captures
    the workspace object eagerly, this fails."""
    events = []  # ("view", ids) | ("think", resolver_value)

    class W(CalibosWorkspace):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.view_log = []

        def view(self):
            v = super().view()
            self.view_log.append(tuple(self._last_view_ids))
            events.append(("view", tuple(self._last_view_ids), None))
            return v

    class S(DreamCognition):
        def think(self, view):
            at_entry = (self._id_resolver()
                        if self._id_resolver is not None else None)
            before = len(self.fragments)
            out = super().think(view)
            # The fragment this think call appended (DreamCognition.think
            # always appends exactly one).
            events.append(("think", at_entry, len(self.fragments[before])))
            return out

    monkeypatch.setattr("calibos_mind.subject.CalibosWorkspace", W)
    base = Path(__file__).resolve().parents[2]
    from calibos_mind.subject import CalibosSubject
    from digital_subject.cartridge import load_cartridge
    tmp = tmp_path / "e2e"
    tmp.mkdir(exist_ok=True)
    dreamer = S(tmp / "dreams")
    cart = load_cartridge(base / "calibos.toml")
    sub = CalibosSubject(tmp / "test.db", cart, cognition=dreamer,
                         salience_path=tmp / "salience.json")
    # Wire exactly as cmd_dream does: lazy dereference of the CURRENT
    # workspace, so _restore replacements are followed, not frozen.
    dreamer.track_ids(lambda: sub.workspace._last_view_ids)
    _seed_dreamable(sub)
    assert isinstance(sub.workspace, W)
    for _ in range(3):
        sub.dream_tick()
    thinks = [e for e in events if e[0] == "think"]
    assert thinks, "no think() calls happened"
    # Pairing: each think's resolver value equals the immediately
    # preceding view's ids — strict view->think alternation, no staleness.
    n_think = 0
    for i, (kind, val, frag_len) in enumerate(events):
        if kind != "think":
            continue
        n_think += 1
        prev_kind, prev_val, _ = events[i - 1]
        assert prev_kind == "view", (
            f"think #{n_think}: previous event was {prev_kind}, "
            "not the immediately-preceding view")
        assert val == prev_val, (
            f"think read stale ids: {val} != preceding view {prev_val}")
        assert val is not None and len(val) == frag_len, (
            f"think #{n_think}: resolver returned {len(val)} ids for a "
            f"{frag_len}-experience view")
    # And the stamped fragments carry exactly those ids, positionally,
    # verified against the live records of the workspace at flush time.
    live = {r.id: r for r in sub.workspace.records}
    flat = [e for f in dreamer.fragments for e in f]
    assert flat, "no fragments recorded"
    for e in flat:
        assert "record_id" in e
        assert e["record_id"] in live
        assert live[e["record_id"]].first_person == e["first_person"]
        assert live[e["record_id"]].source == e["source"]


def test_sync_resolver_follows_workspace_replacement(tmp_path, monkeypatch):
    """The _restore-rebuilds-workspace machinery (found above): the resolver
    must read the CURRENT workspace, not one captured at wiring time. An
    eager capture (``ws = subject.workspace; lambda: ws._last_view_ids``)
    would stamp dead ids after the first transaction — prove the wired
    form does not."""
    sub, dreamer, tmp = _e2e_subject(tmp_path)
    ws_before = sub.workspace
    _seed_dreamable(sub)
    sub.dream_tick()  # transaction -> _restore -> workspace object replaced
    assert sub.workspace is not ws_before
    assert dreamer.fragments, "no fragment recorded"
    live = {r.id: r for r in sub.workspace.records}
    for e in dreamer.fragments[0]:
        assert e["record_id"] in live, (
            "stamped id not among CURRENT workspace records")


def test_sync_hazard_equal_length_stale_view_misattributes(tmp_path):
    """DOCUMENTED HAZARD (not an engine-reachable path): if view B is built
    after view A with equal length and think() is then called with view A,
    the side-channel stamps B's ids onto A's experiences — the length guard
    cannot catch an equal-length stale view. This test pins the failure mode
    so the immediate-handoff invariant above is understood as load-bearing."""
    ws = CalibosWorkspace()
    ws.add(1, "memory", "alpha memory one two three four five")
    ws.add(2, "memory", "beta memory one two three four five")
    view_a = ws.view()
    ids_a = tuple(ws._last_view_ids)
    ws.add(3, "memory", "gamma memory one two three four five")
    ws.add(4, "memory", "delta memory one two three four five")
    # Force equal length: build B from a workspace slice with 2 records.
    ws2 = CalibosWorkspace()
    ws2.records = [r for r in ws.records if r.tick in (3, 4)]
    view_b = ws2.view()
    assert len(view_a.experiences) == len(view_b.experiences) == 2
    # The interleaving: think(view_a) AFTER view_b was built.
    dc = DreamCognition(str(tmp_path / "d"))
    dc.track_ids(lambda: ws2._last_view_ids)  # resolver sees B's ids
    dc.think(view_a)
    stamped = [d["record_id"] for d in dc.fragments[0]]
    assert stamped != list(ids_a), "expected the stale-view misattribution"
    assert stamped == list(ws2._last_view_ids)


def test_sync_think_twice_for_one_view_stamps_same_ids(tmp_path):
    """think() called twice for one view: both fragments get the same,
    correct ids — no drift, no duplication hazard."""
    ws = CalibosWorkspace()
    ws.add(1, "memory", "alpha memory one two three four five")
    ws.add(2, "memory", "beta memory one two three four five")
    view = ws.view()
    dc = DreamCognition(str(tmp_path / "d"))
    dc.track_ids(lambda: ws._last_view_ids)
    dc.think(view)
    dc.think(view)
    assert len(dc.fragments) == 2
    assert dc.fragments[0] == dc.fragments[1]
    assert [d["record_id"] for d in dc.fragments[0]] == list(ws._last_view_ids)


def test_sync_view_built_never_thought_then_fresh_think(tmp_path):
    """A view built and never thought leaves a stale side-channel, but the
    next real view()->think() handoff refreshes it: no stale ids leak."""
    ws = CalibosWorkspace()
    ws.add(1, "memory", "alpha memory one two three four five")
    stale_view = ws.view()  # noqa: F841 — built, never thought
    ws.add(2, "memory", "beta memory one two three four five")
    fresh_view = ws.view()
    dc = DreamCognition(str(tmp_path / "d"))
    dc.track_ids(lambda: ws._last_view_ids)
    dc.think(fresh_view)
    assert [d["record_id"] for d in dc.fragments[0]] == list(ws._last_view_ids)
    assert len(dc.fragments[0]) == len(fresh_view.experiences)


# -- 2. positional order correspondence ---------------------------------------

def test_order_ids_match_experiences_positionally(tmp_path):
    """tuple(r.id for r in window) iterates the identical list the view
    experiences are built from: positional correspondence is structural."""
    ws = CalibosWorkspace()
    ws.add(1, "memory", "alpha memory one two three four five")
    ws.add(2, "thought", "a thought about rain and windows")
    ws.add(3, "memory", "beta memory one two three four five")
    view = ws.view()
    ids = ws._last_view_ids
    assert len(ids) == len(view.experiences)
    by_id = {r.id: r for r in ws.records}
    for rid, e in zip(ids, view.experiences):
        assert by_id[rid].source == e.source
        assert by_id[rid].first_person == e.first_person


def test_order_room_le_zero_branch_is_dead_and_consistent(monkeypatch, tmp_path):
    """The room<=0 early return is unreachable (pinned roots are capped at
    MAX_PINNED=6 < VIEW_LIMIT=16). Pin the deadness; then force the branch
    by shrinking VIEW_LIMIT and verify its ids still match positionally."""
    assert ws_mod.MAX_PINNED < ws_mod.VIEW_LIMIT, (
        "MAX_PINNED reached VIEW_LIMIT: the room<=0 branch is now live, "
        "re-audit its id stamping")
    ws = CalibosWorkspace()
    for i in range(6):
        ws.add(i, "memory", f"cartridge seed memory number {i} extra words here",
               generated_by="cartridge")
    monkeypatch.setattr(ws_mod, "VIEW_LIMIT", 4)
    view = ws.view()
    ids = ws._last_view_ids
    assert len(view.experiences) == 4 == len(ids)
    by_id = {r.id: r for r in ws.records}
    for rid, e in zip(ids, view.experiences):
        assert by_id[rid].first_person == e.first_person


# -- 3. premise: FeltExperience carries no id ---------------------------------

def test_premise_engine_view_experiences_carry_no_id():
    """The side-channel is necessary: engine-built FeltExperience has no id
    slot at all (frozen dataclass with slots)."""
    ws = CalibosWorkspace()
    ws.add(1, "memory", "the kettle sang at dawn today")
    view = ws.view()
    for e in view.experiences:
        assert type(e) is FeltExperience
        assert not hasattr(e, "id")
        # slots + frozen: no id can be smuggled onto an engine-built instance
        # by the provider either.
        with pytest.raises((AttributeError, TypeError)):
            e.id = "x"


# -- 4. fitness attacks ---------------------------------------------------------

def test_fitness_identical_text_same_fragment_same_tick(tmp_path):
    """Two distinct records, identical text, surfaced in ONE fragment at one
    tick: each is rehearsed independently (count 2, one recall each)."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "n.jsonl", [
        (5, [_id_exp("r1", SAME_TEXT), _id_exp("r2", SAME_TEXT)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 2
    assert _recalls(t, "r1") == [5]
    assert _recalls(t, "r2") == [5]


def test_fitness_empty_string_record_id_skips_without_fallback(tmp_path):
    """A present-but-empty record_id is not None, so it takes the id path,
    misses by_id, and is skipped — never text-matched."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "e.jsonl", [
        (5, [_id_exp("", SAME_TEXT)]),
    ])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 0
    assert t.data["records"] == {}


def test_fitness_same_tick_across_two_logs_counts_once(tmp_path):
    """Bug B idempotence across files: the same (record, tick) pair in two
    different logs credits once and the second run counts zero."""
    for name in ("a.jsonl", "b.jsonl"):
        d = tmp_path / "dreams"
        d.mkdir(exist_ok=True)
        _write_log(d, name, [(5, [_id_exp("r1", SAME_TEXT)])])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(tmp_path / "dreams", _records()) == 1
    assert _recalls(t, "r1") == [5]
    assert t.rehearse_from_dreams(tmp_path / "dreams", _records()) == 0


def test_fitness_legacy_ambiguous_text_last_record_wins(tmp_path):
    """Pre-existing legacy behavior documented: id-less fragments with text
    matching two records credit the LAST record in records order (dict
    comprehension last-wins). Not changed by this diff; pinned so a future
    change is deliberate."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "amb.jsonl", [(5, [_old_exp(SAME_TEXT)])])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 1
    assert _recalls(t, "r2") == [5]
    assert "r1" not in t.data["records"]


def test_fitness_non_memory_experiences_ignored_even_with_ids(tmp_path):
    """record_id is stamped on ALL fragment experiences (not just memories),
    but rehearsal skips non-memory sources regardless of ids."""
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "nm.jsonl", [(5, [
        {"source": "thought", "first_person": "wool-gathering",
         "record_id": "r1"},
    ])])
    t = _tracker(tmp_path)
    assert t.rehearse_from_dreams(dream_dir, _records()) == 0
    assert t.data["records"] == {}


def test_fitness_end_to_end_dream_run_rehearses_by_id(tmp_path):
    """Full cmd_dream-shaped flow on a synthetic store: dream ticks write a
    jsonl log with record_ids, and rehearse_from_dreams credits the exact
    records that surfaced (verified against live workspace records)."""
    sub, dreamer, tmp = _e2e_subject(tmp_path)
    _seed_dreamable(sub, n_memories=4)
    log_path = tmp / "dreams" / "night.jsonl"
    for _ in range(2):
        sub.dream_tick()
    with log_path.open("w", encoding="utf-8") as fh:
        for frag in dreamer.fragments:
            fh.write(json.dumps({"tick": 0, "trigger": "echo",
                                 "experiences": frag}) + "\n")
    t = SalienceTracker(tmp / "salience2.json")
    records = [{"id": r.id, "tick": r.tick, "source": r.source,
                "first_person": r.first_person} for r in sub.workspace.records]
    live_ids = {r["id"] for r in records}
    n = t.rehearse_from_dreams(tmp / "dreams", records)
    mem_ids_in_frags = {e["record_id"] for f in dreamer.fragments for e in f
                        if e["source"] == "memory" and "record_id" in e}
    assert mem_ids_in_frags <= live_ids
    for rid in mem_ids_in_frags:
        assert rid in t.data["records"], f"{rid} surfaced but not rehearsed"
    assert n == len(mem_ids_in_frags)


# -- 5. record_id never reaches any display -------------------------------------

def test_display_recall_never_shows_record_id(tmp_path, monkeypatch, capsys):
    import calibos_mind.cli as cli
    dream_dir = tmp_path / "dreams"
    dream_dir.mkdir()
    _write_log(dream_dir, "d1.jsonl", [
        (5, [_id_exp("r1", SAME_TEXT), _id_exp("r2", OTHER_TEXT),
             {"source": "thought", "first_person": "wool-gathering",
              "record_id": "r9"}]),
    ])
    monkeypatch.setattr(cli, "DREAMS", dream_dir)
    assert cli.cmd_recall(types.SimpleNamespace(n=5)) == 0
    out = capsys.readouterr().out
    assert SAME_TEXT in out and OTHER_TEXT in out
    for token in ("r1", "r2", "r9", "record_id"):
        assert token not in out, f"leaked into recall display: {token!r}"


def test_display_no_fragment_reader_prints_record_id():
    """Static sweep over the dream-fragment readers: neither the recall
    display (cmd_recall) nor the fragment loader (_read_fragments) formats
    record_id into user-visible text. (The consolidation quarantine path in
    cli.py also mentions record_id — a different record_id, unrelated to
    dream fragments.)"""
    import inspect
    import calibos_mind.cli as cli
    for fn in (cli.cmd_recall, cli._read_fragments):
        src = inspect.getsource(fn)
        assert "record_id" not in src, (
            f"{fn.__name__} references record_id")


# -- 6. regression genome, item by item ------------------------------------------

def test_genome_no_shadowed_definitions():
    """Shadowing definitions: the diff must not introduce a second def of
    an existing name."""
    import calibos_mind.cli as cli
    import calibos_mind.provider as prov
    assert sum(1 for l in Path(prov.__file__).read_text().splitlines()
               if l.startswith("def track_ids") or l.startswith("    def track_ids")) == 1
    assert sum(1 for l in Path(cli.__file__).read_text().splitlines()
               if l.startswith("def cmd_dream")) == 1
    assert "id_resolver" in DreamCognition.__init__.__code__.co_varnames


def test_genome_last_view_ids_is_per_instance(tmp_path):
    """Sidecar/state reset: _last_view_ids is instance state, never shared
    across workspaces via the class attribute, and never persisted."""
    ws1, ws2 = CalibosWorkspace(), CalibosWorkspace()
    ws1.add(1, "memory", "alpha memory one two three four five")
    ws1.view()
    assert ws1._last_view_ids != ()
    assert ws2._last_view_ids == ()
    assert "_last_view_ids" not in ws1.to_dict()


def test_genome_view_does_not_write_the_store(tmp_path):
    """Read-only violations: view() now mutates _last_view_ids in memory;
    prove it touches no write path — the SQLite file is byte-identical."""
    sub, _, tmp = _e2e_subject(tmp_path)
    db = tmp / "test.db"
    _seed_dreamable(sub)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    for _ in range(5):
        sub.workspace.view()
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert before == after


def test_genome_waking_views_set_but_never_consume_ids(tmp_path):
    """Waking providers share the workspace class: view() sets
    _last_view_ids on waking views too, but InboxCognition never reads it
    and queued prompt payloads carry no record_id."""
    from calibos_mind.provider import InboxCognition
    ws = CalibosWorkspace()
    ws.add(1, "memory", "alpha memory one two three four five")
    view = ws.view()
    assert ws._last_view_ids != ()
    inbox = InboxCognition(str(tmp_path / "inbox"))
    assert inbox.think(view) is None
    payload = json.loads((tmp_path / "inbox" / "prompt-0001.json")
                         .read_text(encoding="utf-8"))
    assert all("record_id" not in e for e in payload["experiences"])


def test_genome_resolver_none_means_legacy_shape(tmp_path):
    """track_ids(None) / never wired: fragments carry no record_id at all."""
    ws = CalibosWorkspace()
    ws.add(1, "memory", "alpha memory one two three four five")
    view = ws.view()
    dc = DreamCognition(str(tmp_path / "d"))
    dc.track_ids(None)
    dc.think(view)
    assert all("record_id" not in d for d in dc.fragments[0])


def test_genome_empty_view_with_wired_resolver(tmp_path):
    """Silent defaults: a zero-experience view with the resolver wired to ()
    stamps nothing and raises nothing (len 0 == len 0 passes the guard)."""
    ws = CalibosWorkspace()
    view = ws.view()
    assert len(view.experiences) == 0
    dc = DreamCognition(str(tmp_path / "d"))
    dc.track_ids(lambda: ws._last_view_ids)
    dc.think(view)
    assert dc.fragments[0] == []
