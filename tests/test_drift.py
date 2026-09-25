"""Tests for the drift metric (calibos_mind/drift.py).

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_drift.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind.drift import (
    drift_report,
    grown_authored_ratio,
    is_authored,
    trigger_kl,
)
from calibos_mind.salience import SalienceTracker


def _rec(rid, tick, generated_by, weight=1.0):
    return {"id": rid, "tick": tick, "generated_by": generated_by,
            "concern_links": [], "expectation_links": [], "_w": weight}


def _const_weight(rec):
    def w(rid, created_tick, now_tick, unresolved=False):
        return rec["_w"]
    return w


def test_ratio_exact_known_mix():
    """Known weights -> exact R after the floor shift."""
    authored = [_rec("a0", 0, "cartridge"), _rec("a1", 0, "cartridge")]
    grown = [_rec(f"g{i}", 10, "cognition") for i in range(6)]
    recs = authored + grown
    weights = {"a0": 1.0, "a1": 3.0, **{f"g{i}": 2.0 for i in range(6)}}
    rep = grown_authored_ratio(
        recs,
        lambda rid, ct, nt, unresolved=False: weights[rid],
        now_tick=20,
    )
    # floor = 1.0 -> authored 2.0, grown 6 x 1.0 = 6.0 -> R = 6/8
    assert rep["R"] == 0.75, rep
    assert rep["n_grown"] == 6 and rep["n_authored"] == 2
    # Heavier grown weight shifts R up, exactly.
    weights.update({f"g{i}": 4.0 for i in range(6)})
    rep2 = grown_authored_ratio(
        recs,
        lambda rid, ct, nt, unresolved=False: weights[rid],
        now_tick=20,
    )
    assert rep2["R"] == 18 / 20, rep2


def test_ratio_with_real_tracker():
    """Real salience substrate: boosting grown salience raises R."""
    tmp = Path(tempfile.mkdtemp(prefix="drift-test-"))
    tracker = SalienceTracker(tmp / "salience.json")
    # Within-class spread so the floor-shift yields a genuine fraction.
    authored = [_rec("a0", 0, "cartridge"), _rec("a1", 5, "cartridge")]
    grown = [_rec(f"g{i}", t, "cognition")
             for i, t in enumerate([30, 40, 45, 48])]
    recs = authored + grown
    tracker.add_importance("a0", 0, 0.5)
    tracker.add_importance("g1", 40, 0.3)
    tracker.add_importance("g3", 48, 0.5)
    plain = grown_authored_ratio(recs, tracker.activation, now_tick=50)
    assert 0.0 < plain["R"] < 1.0, plain
    for i in range(4):
        tracker.add_importance(f"g{i}", [30, 40, 45, 48][i], 1.0)
    boosted = grown_authored_ratio(recs, tracker.activation, now_tick=50)
    assert boosted["R"] > plain["R"], (plain, boosted)
    # Classification sanity: legacy/None and engine stamps count as grown.
    assert is_authored({"generated_by": "cartridge"})
    assert not is_authored({"generated_by": None})
    assert not is_authored({"generated_by": "body_change"})
    assert not is_authored({"generated_by": "experience-7"})


def test_ratio_undefined_when_all_tie():
    """No relative salience mass -> R is None, not a silent 0."""
    recs = [_rec("a0", 0, "cartridge"), _rec("g0", 0, "cognition")]
    rep = grown_authored_ratio(recs, lambda *a, **k: 2.5, now_tick=0)
    assert rep["R"] is None, rep


def test_trigger_kl_shift_exceeds_null():
    """Known shift separates from a split-half null (spec: >= 3x)."""
    def kinds(seq):
        return [{"kind": "cognition_trigger", "tick": i,
                 "trigger": {"kind": k}} for i, k in enumerate(seq)]

    null_trace = kinds(["external", "prior_thought"] * 10)  # stable mix, split 10/10
    null = trigger_kl(null_trace, window=10)
    assert null["ok"], null
    shifted = kinds(["external"] * 16 + ["prior_thought"] * 4
                    + ["prior_thought"] * 10)  # recent regime flips
    shift = trigger_kl(shifted, window=10)
    assert shift["ok"], shift
    assert shift["kl"] > 3 * null["kl"], (null, shift)


def test_trigger_kl_refuses_small_history():
    """Under-powered comparison reports counts, not a number."""
    trace = [{"kind": "cognition_trigger", "tick": i,
              "trigger": {"kind": "external"}} for i in range(4)]
    rep = trigger_kl(trace, window=10)
    assert rep["ok"] is False
    assert "4" in rep["reason"] and "kl" not in rep


def test_integration_temp_store():
    """End-to-end on a synthetic store in /tmp: seeds + injected thoughts."""
    from calibos_mind.provider import InboxCognition
    from calibos_mind.subject import CalibosSubject
    from digital_subject.cartridge import load_cartridge

    tmp = Path(tempfile.mkdtemp(prefix="drift-store-"))
    cart = load_cartridge(Path(__file__).resolve().parents[1] / "calibos.toml")
    sub = CalibosSubject(tmp / "test.db", cart,
                         cognition=InboxCognition(tmp / "inbox"),
                         salience_path=tmp / "salience.json")
    with sub._transaction():
        sub._add("memory", "synthetic seed one",
                 concepts=("seed",), generated_by="cartridge")
        sub._add("memory", "synthetic seed two",
                 concepts=("seed",), generated_by="cartridge")
    sub.inject_thought("synthetic grown thought one")
    sub.inject_thought("synthetic grown thought two")
    sub.inject_thought("synthetic grown thought three")

    # Spread the salience so the ratio is a genuine fraction (mirrors how
    # cmd_think/cmd_answer mark engagement, and seeds earning rehearsal).
    tracker = sub.workspace.salience_tracker
    state = sub.inspect()
    by_text = {r["first_person"]: r for r in state["workspace"]["records"]}
    tracker.add_importance(by_text["synthetic seed one"]["id"], 0, 1.0)
    for i in (1, 2, 3):
        r = by_text[f"synthetic grown thought {'one' if i == 1 else 'two' if i == 2 else 'three'}"]
        tracker.add_importance(r["id"], r["tick"], 0.3)

    state = sub.inspect()
    rep = drift_report(state, tracker)
    r = rep["ratio"]
    # 1 identity root from cartridge load + 2 synthetic seeds = 3 authored
    assert r["n_authored"] == 3, r
    assert r["n_grown"] == 3, r
    assert r["R"] is not None and 0.0 < r["R"] < 1.0, r
    # Few triggers in a fresh store -> KL honestly refuses.
    assert rep["trigger_kl"]["ok"] is False
    # Live store untouched: the subject never saw the live paths.
    assert "drift-store-" in str(tmp)
    print(f"  integration: R={r['R']:.3f} "
          f"(grown {r['grown_salience']:.3f} / authored {r['authored_salience']:.3f})")


def test_drift_is_read_only():
    """drift_report must not mutate the salience sidecar."""
    tmp = Path(tempfile.mkdtemp(prefix="drift-ro-"))
    sidecar = tmp / "salience.json"
    tracker = SalienceTracker(sidecar)
    recs = [_rec("a0", 0, "cartridge"), _rec("g0", 10, "cognition")]
    state = {"workspace": {"records": recs},
             "engine": {"tick": 20}, "trace": []}
    drift_report(state, tracker)
    assert not sidecar.exists(), "drift computed but wrote the sidecar!"


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
