"""Tests for the Zeigarnik unresolved-link fix (calibos_mind/unresolved.py).

Fitness: a record linked only to a resolved concern/commitment/expectation
gets no +1.0 boost; a record linked to an open one keeps it. All three
salience call sites (workspace.view, cli status, drift ratio) must compute
their `unresolved` flag through the single shared helper has_unresolved_links.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_unresolved.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind.unresolved import (
    has_unresolved_links,
    live_open_keys,
    open_link_keys,
)
from calibos_mind.salience import SalienceTracker, UNRESOLVED_BOOST
from calibos_mind.workspace import CalibosWorkspace


def _payload():
    """Synthetic inspect() payload with every status variant.

    Concern "fear" is open (description inside the unresolved window);
    concern "stale" is present but stale (description left the window).
    """
    def c(cid, status):
        return {"id": cid, "status": status, "description": cid}
    def e(eid, status):
        return {"id": eid, "status": status, "proposition": eid}
    return {
        "engine": {
            "concerns": {
                "fear": {"key": "fear", "description": "a sudden loud noise",
                         "urgency": 0.9, "persistence": 0.985,
                         "last_updated_tick": 3},
                "stale": {"key": "stale", "description": "old news, long past",
                          "urgency": 0.4, "persistence": 0.985,
                          "last_updated_tick": 1},
                "prospective:commitment:c-open": {
                    "key": "prospective:commitment:c-open",
                    "description": "I still do not know what followed",
                    "urgency": 0.5, "persistence": 0.985,
                    "last_updated_tick": 3},
            },
            "unresolved": ["a sudden loud noise"],
        },
        "continuity": {
            "commitments": {
                "c-open": c("c-open", "open"),
                "c-overdue": c("c-overdue", "overdue"),
                "c-kept": c("c-kept", "kept"),
                "c-broken": c("c-broken", "broken"),
            },
            "expectations": {
                "e-pending": e("e-pending", "pending"),
                "e-expired": e("e-expired", "expired"),
                "e-confirmed": e("e-confirmed", "confirmed"),
                "e-violated": e("e-violated", "violated"),
            },
        },
    }


def test_open_link_keys_status_split():
    """Open statuses enter the set (raw + prefixed); closed ones do not.

    Concerns: raw keys enter by presence; the "concern:"-prefixed shape only
    for open ones (description in the unresolved window, or prospective:).
    """
    ok = open_link_keys(_payload())
    for key in ("fear", "concern:fear",
                "prospective:commitment:c-open",
                "concern:prospective:commitment:c-open",
                "c-open", "commitment:c-open",
                "c-overdue", "commitment:c-overdue",
                "e-pending", "expectation:e-pending",
                "e-expired", "expectation:e-expired"):
        assert key in ok, key
    for key in ("c-kept", "commitment:c-kept",
                "c-broken", "commitment:c-broken",
                "e-confirmed", "expectation:e-confirmed",
                "e-violated", "expectation:e-violated",
                "concern:stale"):
        assert key not in ok, key
    # Present-but-stale: raw key stays (presence), prefixed shape is gone.
    assert "stale" in ok


def test_live_open_keys_agrees_with_payload_form():
    """The live-object builder and the inspect-payload builder agree."""
    eng = SimpleNamespace(
        concerns={
            "fear": SimpleNamespace(key="fear", description="a sudden loud noise"),
            "stale": SimpleNamespace(key="stale", description="old news, long past"),
        },
        unresolved=["a sudden loud noise"],
    )
    def c(cid, status):
        return SimpleNamespace(id=cid, status=status)
    def e(eid, status):
        return SimpleNamespace(id=eid, status=status)
    cont = SimpleNamespace(
        commitments={cid: c(cid, s) for cid, s in
                     [("c-open", "open"), ("c-kept", "kept"), ("c-broken", "broken")]},
        expectations={eid: e(eid, s) for eid, s in
                      [("e-pending", "pending"), ("e-violated", "violated")]},
    )
    assert live_open_keys(eng, cont) == {
        "fear", "concern:fear", "stale",
        "c-open", "commitment:c-open",
        "e-pending", "expectation:e-pending"}


def test_has_unresolved_links_fitness():
    """Resolved-only links get no boost; open links keep it."""
    ok = open_link_keys(_payload())
    cases = [
        # (concern_links, expectation_links, expected, why)
        (("c-kept",), (), False, "linked only to a kept commitment"),
        (("commitment:c-kept",), (), False, "prefixed link to a kept commitment"),
        (("c-broken",), (), False, "released (->broken) commitment counts as closed"),
        ((), ("e-confirmed",), False, "confirmed expectation"),
        ((), ("e-violated",), False, "violated expectation"),
        (("c-open",), (), True, "open commitment, raw id"),
        (("commitment:c-overdue",), (), True, "overdue commitment, prefixed"),
        ((), ("e-pending",), True, "pending expectation"),
        ((), ("expectation:e-expired",), True, "expired expectation, prefixed"),
        (("fear",), (), True, "open concern, raw key (via the gated concern: shape)"),
        (("concern:fear",), (), True, "open concern, engine's prefixed shape"),
        (("stale",), (), False, "present-but-stale concern: raw key, no gated shape"),
        (("concern:stale",), (), False, "stale concern, prefixed shape absent"),
        (("prospective:commitment:c-open",), (), True,
         "prospective concern stays open while present"),
        (("c-kept", "c-open"), (), True, "mixed: one open link suffices"),
        (("c-kept", "concern:fear"), (), True, "mixed: open concern link suffices"),
        ((), (), False, "no links at all"),
    ]
    for cl, el, expected, why in cases:
        got = has_unresolved_links(cl, el, ok)
        assert got is expected, f"{why}: links={cl or el} -> {got}"
    # No state available: explicit legacy fallback, not a silent False.
    assert has_unresolved_links(("anything",), (), None) is True
    assert has_unresolved_links((), (), None) is False


def test_activation_boost_exactly_1_0():
    """The fitness delta lands in the real activation math as +1.0."""
    tmp = Path(tempfile.mkdtemp(prefix="unresolved-boost-"))
    tracker = SalienceTracker(tmp / "salience.json")
    ok = open_link_keys(_payload())
    open_flag = has_unresolved_links(("c-open",), (), ok)
    closed_flag = has_unresolved_links(("c-kept",), (), ok)
    assert open_flag and not closed_flag
    a_open = tracker.activation("r1", 10, 20, unresolved=open_flag)
    a_closed = tracker.activation("r1", 10, 20, unresolved=closed_flag)
    assert a_open - a_closed == UNRESOLVED_BOOST == 1.0


def test_workspace_view_consults_open_set():
    """view() ranks an open-linked record above an identical resolved-linked one."""
    tmp = Path(tempfile.mkdtemp(prefix="unresolved-view-"))
    tracker = SalienceTracker(tmp / "salience.json")
    ok = open_link_keys(_payload())
    calls = []
    ws = CalibosWorkspace()
    ws.add(10, "memory", "the open-linked recollection is distinct enough",
           concern_links=("c-open",))
    ws.add(10, "memory", "the resolved-linked recollection is distinct enough",
           concern_links=("c-kept",))
    ws.salience_tracker = tracker
    ws.open_keys_provider = lambda: calls.append(1) or ok
    try:
        view = ws.view()
    finally:
        ws.salience_tracker = None
        ws.open_keys_provider = None
    assert calls, "view() never consulted the open-set provider"
    texts = [e.first_person for e in view.experiences]
    assert texts[0].startswith("the open-linked"), texts


def test_workspace_view_legacy_fallback_without_provider():
    """No provider -> shared helper's legacy any-link check (both boosted)."""
    tmp = Path(tempfile.mkdtemp(prefix="unresolved-legacy-"))
    tracker = SalienceTracker(tmp / "salience.json")
    ws = CalibosWorkspace()
    ws.add(10, "memory", "linked recollection one is distinct enough",
           concern_links=("c-kept",))
    ws.add(11, "memory", "linked recollection two is distinct enough",
           concern_links=("c-kept",))
    ws.salience_tracker = tracker
    assert ws.open_keys_provider is None  # the class default
    try:
        view = ws.view()
    finally:
        ws.salience_tracker = None
    # Both carry the boost, so the newer tick wins by the tiebreak.
    texts = [e.first_person for e in view.experiences]
    assert texts[0].startswith("linked recollection two"), texts


def test_drift_ratio_uses_shared_helper():
    """grown_authored_ratio flags unresolved through the helper, not inline."""
    from calibos_mind.drift import grown_authored_ratio
    ok = open_link_keys(_payload())
    seen = {}
    def w(rid, ct, nt, unresolved=False):
        seen[rid] = unresolved
        return 1.0
    recs = [
        {"id": "g1", "tick": 1, "generated_by": "cognition",
         "concern_links": ["c-open"], "expectation_links": []},
        {"id": "g2", "tick": 1, "generated_by": "cognition",
         "concern_links": ["c-kept"], "expectation_links": []},
        {"id": "a1", "tick": 0, "generated_by": "cartridge",
         "concern_links": [], "expectation_links": []},
    ]
    grown_authored_ratio(recs, w, now_tick=5, open_keys=ok)
    assert seen == {"g1": True, "g2": False, "a1": False}, seen
    # Default (no open set): legacy any-link behavior, explicit.
    grown_authored_ratio(recs, w, now_tick=5)
    assert seen == {"g1": True, "g2": True, "a1": False}, seen


def test_single_source_of_truth():
    """Every call site imports has_unresolved_links; no inline reimplementations."""
    base = Path(__file__).resolve().parents[1] / "calibos_mind"
    for name in ("workspace.py", "cli.py", "drift.py"):
        src = (base / name).read_text(encoding="utf-8")
        assert "has_unresolved_links" in src, f"{name} does not use the shared helper"
    # The old inline pattern must be gone everywhere (the shared helper's
    # documented None-fallback is a different, explicit shape).
    for path in base.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "bool(r.concern_links or r.expectation_links)" not in src, path.name
        assert 'bool(r["concern_links"] or r["expectation_links"])' not in src, path.name
        assert 'bool(r.get("concern_links") or r.get("expectation_links"))' not in src, path.name


def test_subject_wires_live_open_set():
    """End-to-end on a synthetic /tmp store: resolving a commitment drops the boost."""
    from calibos_mind.provider import InboxCognition
    from calibos_mind.subject import CalibosSubject
    from digital_subject.cartridge import load_cartridge

    tmp = Path(tempfile.mkdtemp(prefix="unresolved-e2e-"))
    cart = load_cartridge(Path(__file__).resolve().parents[1] / "calibos.toml")
    sub = CalibosSubject(tmp / "test.db", cart,
                         cognition=InboxCognition(tmp / "inbox"),
                         salience_path=tmp / "salience.json")
    with sub._transaction():
        c = sub.continuity.create_commitment(
            actor="self", description="synthetic open loop",
            tick=sub.engine.state.tick)
        rid = sub._add("memory", "a recollection tied to the open loop",
                       concern_links=(c.id,)).id
    assert c.id in sub._live_open_keys()
    assert ("commitment:" + c.id) in sub._live_open_keys()
    assert has_unresolved_links((c.id,), (), sub._live_open_keys()) is True
    with sub._transaction():
        sub.continuity.resolve_commitment(
            c.id, outcome="done", kept=True, tick=sub.engine.state.tick)
    assert c.id not in sub._live_open_keys()
    assert ("commitment:" + c.id) not in sub._live_open_keys()
    assert has_unresolved_links((c.id,), (), sub._live_open_keys()) is False
    assert sub.workspace.open_keys_provider is not None


if __name__ == "__main__":
    test_open_link_keys_status_split()
    test_live_open_keys_agrees_with_payload_form()
    test_has_unresolved_links_fitness()
    test_activation_boost_exactly_1_0()
    test_workspace_view_consults_open_set()
    test_workspace_view_legacy_fallback_without_provider()
    test_drift_ratio_uses_shared_helper()
    test_single_source_of_truth()
    test_subject_wires_live_open_set()
    print("test_unresolved: 9 passed")
