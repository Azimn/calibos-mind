"""Critic battery R1 for Bug A: Zeigarnik unresolved-link fix.

Attacks the builder's change (calibos_mind/unresolved.py + the call-site hunks
in workspace.py / subject.py / cli.py / drift.py). Every objection below is a
FAILING test against the current code; genome checks are included explicitly.

All fixtures are synthetic /tmp stores. The live store
(~/workspace/calibos-mind/mind.db) is never opened.

Engine facts used here were verified against the frozen install
(.venv/lib/python3.12/site-packages/jelly_psiduck/,
 digital_subject/) and by driving a real subject on a /tmp store --
not from the builder's docstring:

  E1. jelly_psiduck/endogenous.py:196-201 (_warrants_cognition): for a
      residual concern the engine writes
          concern_links=("concern:" + concern.key,)
      i.e. the "concern:"-PREFIXED key shape. Proven live: a /tmp subject
      with a present concern whose description was in state.unresolved and
      activation over threshold wrote concern_links=('concern:fear',).
  E2. jelly_psiduck/endogenous.py:155: a residual concern is a cognition
      candidate only if `concern.description in self.engine.state.unresolved`
      -- the engine DOES have an open/closed distinction for concerns.
      Proven live: a present concern whose description was absent from
      state.unresolved got no record even with activation seeded at 2.0.
  E3. digital_subject/engine.py:88,248 + models.py:231: state.unresolved is a
      last-24 description list, present in the inspect() payload as
      state["engine"]["unresolved"] (verified via inspect()) and live as
      engine_state.unresolved. Both open-set builders already receive the
      structures that carry it.
  E4. digital_subject/engine.py:184-192 (_decay_private_state): concerns with
      urgency < 0.02 are dropped -- a second removal path the builder's
      docstring omits (it claims the prospective:-sweep is the only one).

Expected result on the current code: tests 1, 2, 3b, 4, 5 FAIL (the
objections); tests 6-12 PASS (genome + guards the fix must not break).
"""
from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from calibos_mind.unresolved import (
    has_unresolved_links,
    live_open_keys,
    open_link_keys,
)

BASE = Path(__file__).resolve().parents[2] / "calibos_mind"


def _payload(*, concern_desc="a sudden loud noise startled me",
             unresolved_descs=("a sudden loud noise startled me",)):
    """Synthetic inspect() payload: one open concern, open + closed ledger."""
    def c(cid, status):
        return {"id": cid, "status": status, "description": cid}
    def e(eid, status):
        return {"id": eid, "status": status, "proposition": eid}
    return {
        "engine": {
            "concerns": {
                "fear": {"key": "fear", "description": concern_desc,
                         "urgency": 0.9, "persistence": 0.985,
                         "last_updated_tick": 3},
            },
            "unresolved": list(unresolved_descs),
        },
        "continuity": {
            "commitments": {
                "c-open": c("c-open", "open"),
                "c-kept": c("c-kept", "kept"),
            },
            "expectations": {
                "e-pending": e("e-pending", "pending"),
                "e-violated": e("e-violated", "violated"),
            },
        },
    }


def _live_states(payload):
    eng = SimpleNamespace(
        concerns={k: SimpleNamespace(**v)
                  for k, v in payload["engine"]["concerns"].items()},
        unresolved=list(payload["engine"]["unresolved"]),
    )
    def c(cid, status):
        return SimpleNamespace(id=cid, status=status)
    def e(eid, status):
        return SimpleNamespace(id=eid, status=status)
    cont = SimpleNamespace(
        commitments={cid: c(cid, v["status"])
                     for cid, v in payload["continuity"]["commitments"].items()},
        expectations={eid: e(eid, v["status"])
                      for eid, v in payload["continuity"]["expectations"].items()},
    )
    return eng, cont


# ---------------------------------------------------------------------------
# Objection 1: the "concern:"-prefixed link shape misses the open set.
# Engine fact E1: the engine writes concern_links=("concern:"+key,) for a
# genuinely open residual concern. The helper's open set carries the raw key
# ("fear") but not "concern:fear", so a record linked to an OPEN concern
# loses the +1.0 boost -- the spec's own revert signal.
# ---------------------------------------------------------------------------

def test_concern_prefixed_link_to_open_concern_keeps_boost():
    ok = open_link_keys(_payload())
    assert "fear" in ok  # the raw key is there; the prefixed one is not
    got = has_unresolved_links(("concern:fear",), (), ok)
    assert got is True, (
        "record linked to OPEN concern 'fear' via the engine's "
        "'concern:'-prefixed link shape lost the boost"
    )


def test_concern_prefixed_mixed_with_resolved_links():
    ok = open_link_keys(_payload())
    got = has_unresolved_links(("c-kept", "concern:fear"), (), ok)
    assert got is True, "mixed links: the open concern link must suffice"


def test_live_open_keys_covers_concern_prefixed_form():
    eng, cont = _live_states(_payload())
    ok = live_open_keys(eng, cont)
    assert "concern:fear" in ok, (
        "live builder misses the 'concern:'-prefixed shape the engine writes"
    )


def test_drift_ratio_sees_concern_prefixed_link():
    """The third call site inherits the miss through the shared helper."""
    from calibos_mind.drift import grown_authored_ratio
    ok = open_link_keys(_payload())
    seen = {}
    def w(rid, ct, nt, unresolved=False):
        seen[rid] = unresolved
        return 1.0
    recs = [
        {"id": "g1", "tick": 1, "generated_by": "cognition",
         "concern_links": ["concern:fear"], "expectation_links": []},
    ]
    grown_authored_ratio(recs, w, now_tick=5, open_keys=ok)
    assert seen == {"g1": True}, (
        f"drift computed unresolved={seen['g1']} for an open-concern link"
    )


# ---------------------------------------------------------------------------
# Objection 2: presence-only concern liveness over-boosts stale concerns.
# Engine fact E2/E3: the engine's own residual-concern liveness rule is
# `concern.description in state.unresolved` (last-24 window). A concern can
# be PRESENT in state["concerns"] while stale by that rule -- the helper
# boosts it anyway. The builder's docstring claims "the engine genuinely has
# NO open/closed distinction for concerns"; that finding is false, and the
# spec required the determination to come from the engine code.
# ---------------------------------------------------------------------------

def test_stale_concern_gets_no_boost():
    payload = _payload(unresolved_descs=("something else entirely",))
    ok = open_link_keys(payload)
    assert "fear" in ok  # present-but-stale is in the set today
    got = has_unresolved_links(("fear",), (), ok)
    assert got is False, (
        "concern 'fear' is present but its description left the engine's "
        "own unresolved window -- the engine would no longer surface it, "
        "yet the boost fires"
    )


def test_stale_concern_live_form_gets_no_boost():
    eng, cont = _live_states(_payload(unresolved_descs=[]))
    ok = live_open_keys(eng, cont)
    assert has_unresolved_links(("fear",), (), ok) is False


def test_prospective_concern_open_while_present():
    """Guard: fixing the stale-concern rule must not close prospective
    concerns. The engine sweeps 'prospective:' keys when their ledger root
    closes (endogenous.py:141-143), so presence ~= open for them."""
    payload = _payload()
    payload["engine"]["concerns"]["prospective:commitment:c-open"] = {
        "key": "prospective:commitment:c-open",
        "description": "I still do not know what followed",
        "urgency": 0.5, "persistence": 0.985, "last_updated_tick": 3,
    }
    ok = open_link_keys(payload)
    assert has_unresolved_links(
        ("prospective:commitment:c-open",), (), ok) is True


def test_engine_ignores_stale_concern_e2e():
    """Measured-behavior anchor for the stale-concern objection: the real
    engine writes no record for a present-but-stale concern even with
    activation seeded above threshold (mirrors E2)."""
    from digital_subject.models import Concern
    from digital_subject.cartridge import load_cartridge
    from calibos_mind.provider import InboxCognition
    from calibos_mind.subject import CalibosSubject

    tmp = Path(tempfile.mkdtemp(prefix="critic-stale-"))
    cart = load_cartridge(BASE.parent / "calibos.toml")
    sub = CalibosSubject(tmp / "test.db", cart,
                         cognition=InboxCognition(tmp / "inbox"),
                         salience_path=tmp / "salience.json")
    with sub._transaction():
        sub.engine.state.concerns["stalefear"] = Concern(
            "stalefear", "an old startle long past", 0.9, 0.985,
            sub.engine.state.tick)
        assert "an old startle long past" not in sub.engine.state.unresolved
        sub.endogenous["activation"]["concern:stalefear"] = 2.0
        before = {r.id for r in sub.workspace.records}
        sub._warrants_cognition(False, False, False)
        new = [r for r in sub.workspace.records if r.id not in before]
    assert not [r for r in new if "stalefear" in (r.concern_links or ())], (
        "engine surfaced a stale concern -- E2 premise broken"
    )


# ---------------------------------------------------------------------------
# Regression genome, checked item by item (expected to PASS)
# ---------------------------------------------------------------------------

def test_helpers_do_not_mutate_inputs():
    """Read-only genome: the new helpers are pure -- no write path."""
    payload = _payload()
    snap = copy.deepcopy(payload)
    eng, cont = _live_states(payload)
    eng_snap = copy.deepcopy(eng.__dict__)
    cont_snap = copy.deepcopy(
        {k: {i: vars(v).copy() for i, v in d.items()}
         for k, d in (("c", cont.commitments), ("e", cont.expectations))})
    ok = open_link_keys(payload)
    live_ok = live_open_keys(eng, cont)
    has_unresolved_links(("fear",), ("e-pending",), ok)
    has_unresolved_links(("fear",), (), live_ok)
    has_unresolved_links(("x",), (), None)
    assert payload == snap, "open_link_keys mutated the inspect payload"
    assert eng.__dict__ == eng_snap, "live_open_keys mutated engine state"
    assert {i: vars(v) for i, v in cont.commitments.items()} == cont_snap["c"]
    assert {i: vars(v) for i, v in cont.expectations.items()} == cont_snap["e"]


def test_live_keys_and_view_do_not_touch_db():
    """Read-only genome, e2e: the mid-tick open-set read and a salience
    view leave the SQLite store byte-identical (change-counter included)."""
    from digital_subject.cartridge import load_cartridge
    from calibos_mind.provider import InboxCognition
    from calibos_mind.subject import CalibosSubject
    from calibos_mind.salience import SalienceTracker
    from calibos_mind.workspace import CalibosWorkspace

    tmp = Path(tempfile.mkdtemp(prefix="critic-readonly-"))
    cart = load_cartridge(BASE.parent / "calibos.toml")
    sub = CalibosSubject(tmp / "test.db", cart,
                         cognition=InboxCognition(tmp / "inbox"),
                         salience_path=tmp / "salience.json")
    with sub._transaction():
        sub._add("memory", "a synthetic recollection for the view",
                 concern_links=("fear",))
    db = tmp / "test.db"
    digest = lambda: hashlib.sha256(db.read_bytes()).hexdigest()
    before = digest()
    sub._live_open_keys()
    ws = sub.workspace
    assert isinstance(ws, CalibosWorkspace)
    ws.salience_tracker = SalienceTracker(tmp / "salience.json")
    try:
        ws.view()
    finally:
        ws.salience_tracker = None
    assert digest() == before, "read path modified the SQLite store"


def test_no_shadowed_helper_definitions():
    """Shadowing genome: one definition each; the subject method shadows no
    engine attribute."""
    counts = {"has_unresolved_links": 0, "open_link_keys": 0,
              "live_open_keys": 0}
    for path in BASE.glob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            for name in counts:
                if line.startswith(f"def {name}("):
                    counts[name] += 1
    assert counts == {n: 1 for n in counts}, counts
    live_defs = [p for p in BASE.glob("*.py")
                 if "\n    def _live_open_keys(" in p.read_text(encoding="utf-8")]
    assert [p.name for p in live_defs] == ["subject.py"], live_defs
    from jelly_psiduck.endogenous import EndogenousSubject
    assert not any("_live_open_keys" in vars(k)
                   for k in EndogenousSubject.__mro__), (
        "subject._live_open_keys would override an engine method"
    )


def test_call_sites_share_single_helper():
    """Spec: all three call sites compute `unresolved` through the one
    shared helper; the old inline bool(...) pattern is gone everywhere."""
    for name in ("workspace.py", "cli.py", "drift.py"):
        src = (BASE / name).read_text(encoding="utf-8")
        assert "has_unresolved_links(" in src, f"{name} bypasses the helper"
    inline_variants = (
        "bool(r.concern_links or r.expectation_links)",
        'bool(r["concern_links"] or r["expectation_links"])',
        'bool(r.get("concern_links") or r.get("expectation_links"))',
        "or r.expectation_links,",
        'or r["expectation_links"],',
    )
    for path in BASE.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        for pat in inline_variants:
            assert pat not in src, f"{path.name} reimplements the check: {pat}"


def test_empty_open_set_and_explicit_none_fallback():
    """Silent-defaults genome: an empty set means 'nothing open' (no boost),
    while None keeps the documented legacy any-link check -- the two are
    distinct and neither is a quiet zero."""
    assert has_unresolved_links(("c-open",), (), set()) is False
    assert has_unresolved_links(("x",), (), None) is True
    assert has_unresolved_links((), (), None) is False
    assert open_link_keys({}) == set()


if __name__ == "__main__":
    names = [n for n in list(globals()) if n.startswith("test_")]
    failed = []
    for n in names:
        try:
            globals()[n]()
            print(f"PASS {n}")
        except AssertionError as exc:
            failed.append(n)
            print(f"FAIL {n}: {exc}")
    print(f"\n{len(names) - len(failed)}/{len(names)} passed; "
          f"failed: {failed or 'none'}")
    raise SystemExit(1 if failed else 0)
