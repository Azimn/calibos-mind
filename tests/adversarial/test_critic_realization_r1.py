"""Critic round 1: adversarial tests for interoceptive realization records
(domain 10, builder diff 2026-10-04).

Every objection here is a failing test or a concretely violated invariant;
prose-only critique is rejected per research/builder-critic-loop.md.

RED (expected to fail on the builder's diff):
  test_consolidation_never_supersedes_distinct_episodes
  test_consolidation_never_supersedes_across_needs
  test_crash_between_mint_and_save_mints_exactly_once
  test_impossible_swing_never_mints_phantom

GREEN (pins of correct behavior / measured premises):
  boundary strictness, dead-zone hold, single-episode re-entry,
  recall/review read-only, no negation markers in the template,
  thirst urgency premise, near-dup margins.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/adversarial/test_critic_realization_r1.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/

import calibos_mind.cli as cli
from calibos_mind.consolidate import (
    CONTAINMENT_NEAR_DUP,
    JACCARD_NEAR_DUP,
    _Rec,
    _negation_count,
    _stopword_negation_count,
    containment,
    content_tokens,
    jaccard,
    scan,
)
from calibos_mind.interoception import (
    CONVERGE_GAP,
    SWING_GAP,
    InteroceptionTracker,
    realization_text,
    urgency,
)
from jelly_psiduck.firewall import LOW_IS_BAD
from test_realization import (  # builder's harness, reused not duplicated
    _patched_cli,
    _payload_records,
    _realizations,
    _restore,
)


def _tracker(tmp: Path, name: str = "interoception.json", **kw) -> InteroceptionTracker:
    return InteroceptionTracker(tmp / name, **kw)


def _rec(rid: str, index: int, tick: int, text: str) -> _Rec:
    return _Rec(id=rid, index=index, source="temporal", text=text,
                tick=tick, generated_by="cognition", available=True)


def _scan_props(recs):
    journal = {"seq": 0, "proposals": {}}
    return scan(recs, store_tick=500, journal=journal)["proposals"]


# ---------------------------------------------------------------------------
# RED 1+2: the templated realization text systematically defeats the
# consolidation supersede gate. Two DISTINCT episodes (different ticks,
# even different needs) land in the supersede band
# (subject overlap >= 0.50, body in [0.40, 0.85), polarity agreement)
# and the scan proposes archiving the older as "the same subject stated
# again later" — a false rationale. Per the regression genome, a wrong
# archive is the hazard; a missed consolidation is the safe direction.
# ---------------------------------------------------------------------------

def test_consolidation_never_supersedes_distinct_episodes():
    """Two realization records for the same need, from two separate
    swing->convergence episodes, must never be proposed as supersede/dedup
    of each other: they are distinct historical misreadings, not the same
    fact stated twice."""
    e1 = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.28,
          "swing_actual": 0.9, "gap_max": 0.62, "conv_tick": 52}
    e2 = {"need": "thirst", "swing_tick": 100, "swing_felt": 0.30,
          "swing_actual": 0.9, "gap_max": 0.60, "conv_tick": 131}
    recs = [_rec("r1", 0, 52, realization_text(e1)),
            _rec("r2", 1, 131, realization_text(e2))]
    props = _scan_props(recs)
    bad = [p for p in props if p["kind"] in ("supersede", "dedup")]
    assert not bad, (
        "distinct episodes proposed for archival: "
        + "; ".join(f"{p['kind']} {p['loser']}->{p['winner']}: {p['rationale']}"
                    for p in bad))


def test_consolidation_never_supersedes_across_needs():
    """First-contact style: two needs diverge on the same tick and converge
    on the same tick. The records differ only in the need word — they must
    not supersede each other either."""
    e1 = {"need": "thirst", "swing_tick": 1, "swing_felt": 0.5,
          "swing_actual": 0.1, "gap_max": 0.4, "conv_tick": 30}
    e2 = {"need": "hunger", "swing_tick": 1, "swing_felt": 0.5,
          "swing_actual": 0.1, "gap_max": 0.4, "conv_tick": 30}
    recs = [_rec("r1", 0, 30, realization_text(e1)),
            _rec("r2", 1, 30, realization_text(e2))]
    props = _scan_props(recs)
    bad = [p for p in props if p["kind"] in ("supersede", "dedup")]
    assert not bad, (
        "cross-need episodes proposed for archival: "
        + "; ".join(f"{p['kind']} {p['loser']}->{p['winner']}: {p['rationale']}"
                    for p in bad))


# ---------------------------------------------------------------------------
# RED 3: write-ordering across the two stores. cli._run_tick mints inside
# subject._transaction() (commits the record) and only then calls
# tracker.save() (persists the episode closure). A crash in between leaves
# the sidecar with the episode still open; the next run closes it AGAIN
# and mints a second record for the same episode — violating "exactly one
# record per closed episode".
# ---------------------------------------------------------------------------

def test_crash_between_mint_and_save_mints_exactly_once():
    tmp = Path(tempfile.mkdtemp(prefix="crit-crash-"))
    tr = _tracker(tmp)
    tick = 0
    for _ in range(30):
        tick += 1
        tr.update({"thirst": 0.2}, tick)
        tr.save()
    closed = None
    for _ in range(40):
        tick += 1
        evs = tr.update({"thirst": 0.9}, tick)
        if evs:
            closed = evs[0]
            # MINT COMMITS HERE; crash before tracker.save() — the file
            # still holds the open swing. (In-memory tracker discarded.)
            break
        tr.save()
    assert closed is not None
    swing_tick = closed["swing_tick"]
    # "Restart": rebuild from the still-dirty sidecar, keep converging.
    tr2 = _tracker(tmp)
    assert "thirst" in tr2.data["realization"]["swing"]  # close was lost
    dups = []
    for _ in range(60):
        tick += 1
        for e in tr2.update({"thirst": 0.9}, tick):
            if e["swing_tick"] == swing_tick:
                dups.append(e)
    assert not dups, (
        f"episode (swing_tick={swing_tick}) closed {1 + len(dups)} times "
        f"across the crash window — a duplicate realization would mint")


# ---------------------------------------------------------------------------
# RED 4: phantom realization from a corrupt-but-well-typed sidecar entry.
# _read_swing's docstring promises "a stale or corrupt swing must never
# mint a phantom realization". An open episode ALWAYS has
# gap_max > SWING_GAP (it only opens past the threshold), so an entry with
# gap_max <= SWING_GAP is semantically impossible — yet it loads and mints
# on convergence: a record for a gap that never exceeded 0.25, i.e. the
# spec's own revert signal.
# ---------------------------------------------------------------------------

def test_impossible_swing_never_mints_phantom():
    tmp = Path(tempfile.mkdtemp(prefix="crit-phantom-"))
    p = tmp / "interoception.json"
    p.write_text(json.dumps({
        "needs": {"thirst": {"felt": 0.5, "last_tick": 9, "level": 0}},
        "params": {}, "seed": 0,
        "realization": {"swing": {"thirst": {
            "swing_tick": 3, "swing_felt": 0.5,
            "swing_actual": 0.51, "gap_max": 0.01}}}}), encoding="utf-8")
    tr = _tracker(tmp)
    # The entry is impossible (gap_max 0.01 <= SWING_GAP 0.25 on an *open*
    # episode) and must be dropped at load, never trusted.
    assert tr.data["realization"]["swing"] == {}, (
        f"impossible swing survived compat-read: "
        f"{tr.data['realization']['swing']}")
    evs = []
    for tick in range(10, 30):
        evs.extend(tr.update({"thirst": 0.5}, tick))
    assert evs == [], f"phantom realization minted: {evs}"


# ---------------------------------------------------------------------------
# GREEN pins
# ---------------------------------------------------------------------------

def test_open_boundary_is_strict():
    """An episode opens iff the measured gap STRICTLY exceeds 0.25
    (noise off; gap measured on the stored felt value, as the code does)."""
    for actual in (0.80, 0.85, 0.869, 0.885, 0.90, 1.0):
        tmp = Path(tempfile.mkdtemp(prefix="crit-open-"))
        tr = _tracker(tmp, params={"noise_scale": 0.0})
        tr.data["needs"]["thirst"] = {"felt": 0.5, "last_tick": 1, "level": 0}
        evs = tr.update({"thirst": actual}, 2)
        gap = abs(float(tr.data["needs"]["thirst"]["felt"]) - actual)
        opened = "thirst" in tr.data["realization"]["swing"]
        assert opened == (gap > SWING_GAP), (actual, gap, opened, evs)
        # update() returns *closed* episodes; an opening tick closes nothing.
        assert evs == [], evs


def test_close_boundary_is_inclusive():
    """An open episode closes iff the measured gap is <= 0.05."""
    for felt_seed, actual in ((0.86, 0.9), (0.83, 0.9), (0.80, 0.9), (0.78, 0.9)):
        tmp = Path(tempfile.mkdtemp(prefix="crit-close-"))
        tr = _tracker(tmp, params={"noise_scale": 0.0})
        tr.data["needs"]["thirst"] = {"felt": 0.5, "last_tick": 1, "level": 0}
        assert tr.update({"thirst": 0.9}, 2) == []  # gap 0.26: opens
        assert "thirst" in tr.data["realization"]["swing"]
        tr.data["needs"]["thirst"]["felt"] = felt_seed
        evs = tr.update({"thirst": actual}, 3)
        gap = abs(float(tr.data["needs"]["thirst"]["felt"]) - actual)
        closed = "thirst" not in tr.data["realization"]["swing"]
        assert closed == (gap <= CONVERGE_GAP), (felt_seed, actual, gap, evs)
        assert (len(evs) == 1) == (gap <= CONVERGE_GAP)


def test_dead_zone_holds_episode_open():
    """Gaps in (0.05, 0.25] are ordinary lag: the episode stays open with
    no event across many ticks, then closes exactly once on convergence."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-dead-"))
    tr = _tracker(tmp, params={"noise_scale": 0.0})
    tr.data["needs"]["thirst"] = {"felt": 0.5, "last_tick": 1, "level": 0}
    assert tr.update({"thirst": 0.9}, 2) == []  # opens, gap 0.26
    tick = 2
    for _ in range(10):  # hold the gap at ~0.16, squarely in the dead zone
        tick += 1
        tr.data["needs"]["thirst"]["felt"] = 0.65
        evs = tr.update({"thirst": 0.9}, tick)
        gap = abs(float(tr.data["needs"]["thirst"]["felt"]) - 0.9)
        assert 0.05 < gap <= 0.25, gap
        assert evs == [], f"dead-zone gap minted at tick {tick}: {evs}"
        assert "thirst" in tr.data["realization"]["swing"]
    tr.data["needs"]["thirst"]["felt"] = 0.88
    tick += 1
    evs = tr.update({"thirst": 0.9}, tick)
    assert len(evs) == 1, evs
    assert tr.data["realization"]["swing"] == {}


def test_second_swing_before_convergence_is_single_episode():
    """Per the spec, an episode runs from the first >0.25 crossing to the
    first <=0.05 return: a second exceedance before convergence extends the
    SAME episode (gap_max tracks the maximum), minting one record."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-reentry-"))
    tr = _tracker(tmp, params={"noise_scale": 0.0})
    tr.data["needs"]["thirst"] = {"felt": 0.5, "last_tick": 1, "level": 0}
    assert tr.update({"thirst": 0.9}, 2) == []          # gap 0.26: opens
    tr.data["needs"]["thirst"]["felt"] = 0.65
    assert tr.update({"thirst": 0.9}, 3) == []          # gap 0.1625: lag
    tr.data["needs"]["thirst"]["felt"] = 0.3
    assert tr.update({"thirst": 0.9}, 4) == []          # gap 0.528: re-swing
    ep = tr.data["realization"]["swing"]["thirst"]
    assert ep["gap_max"] == 0.528, ep
    assert ep["swing_tick"] == 2, ep                    # original swing kept
    tr.data["needs"]["thirst"]["felt"] = 0.88
    evs = tr.update({"thirst": 0.9}, 5)                 # gap ~0.013: closes
    assert len(evs) == 1, evs
    assert evs[0]["swing_tick"] == 2 and evs[0]["gap_max"] == 0.528


def _stdout(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args)
    return rc, buf.getvalue()


def test_recall_and_review_never_touch_realization_state():
    """The remaining read-only commands (recall, review) never move the
    sidecar and never mint — with an episode open, the strictest case."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-ro-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        tr = subject.workspace.interoception_tracker
        for tick in range(1, 30):
            tr.update({"thirst": 0.2, "hunger": 0.5, "fatigue": 0.5,
                       "energy": 0.5}, tick)
        tr.update({"thirst": 0.9, "hunger": 0.5, "fatigue": 0.5,
                   "energy": 0.5}, 30)  # swing opens
        tr.save()
        digest_before = hashlib.sha256(intero.read_bytes()).hexdigest()
        assert _realizations(_payload_records(db)) == []
        rc, _ = _stdout(cli.cmd_recall, argparse.Namespace(n=5))
        assert rc == 0
        rc, _ = _stdout(cli.cmd_review, argparse.Namespace(n=5))
        assert rc == 0
        assert hashlib.sha256(intero.read_bytes()).hexdigest() == digest_before
        assert _realizations(_payload_records(db)) == []
        assert "thirst" in json.loads(
            intero.read_text(encoding="utf-8"))["realization"]["swing"]
    finally:
        _restore(saved)


def test_realization_text_carries_no_negation_markers():
    """Premise pin: the template has zero negation markers under the
    scan's own counters, so the polarity vetoes can never reroute a
    realization pair to a contradiction-flag — supersede proceeds
    unchecked (which is why RED 1+2 fire)."""
    events = [
        {"need": "thirst", "swing_tick": 41, "swing_felt": 0.28,
         "swing_actual": 0.9, "gap_max": 0.62, "conv_tick": 52},
        {"need": "hunger", "swing_tick": 1, "swing_felt": 0.5,
         "swing_actual": 0.1, "gap_max": 0.4, "conv_tick": 30},
    ]
    for e in events:
        text = realization_text(e)
        assert _negation_count(text) == 0, text
        assert _stopword_negation_count(text) == 0, text


def test_thirst_urgency_direction_premise():
    """Test-premise check (genome): the builder's band assertions rest on
    thirst not being LOW_IS_BAD. Verified against the frozen firewall."""
    assert "thirst" not in LOW_IS_BAD
    assert urgency("thirst", 0.9) == 0.9
    assert urgency("thirst", 0.2) == 0.2


def test_near_dup_thresholds_hold_for_same_need_pair():
    """Near-dup does NOT fire for same-need pairs — measured with the real
    tokenization against the real thresholds. The hazard is supersede
    (RED 1), not near-dup; this pins the margin."""
    e1 = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.28,
          "swing_actual": 0.9, "gap_max": 0.62, "conv_tick": 52}
    e2 = {"need": "thirst", "swing_tick": 100, "swing_felt": 0.30,
          "swing_actual": 0.9, "gap_max": 0.60, "conv_tick": 131}
    a = frozenset(content_tokens(realization_text(e1)))
    b = frozenset(content_tokens(realization_text(e2)))
    assert jaccard(a, b) < JACCARD_NEAR_DUP, jaccard(a, b)
    assert containment(a, b) < CONTAINMENT_NEAR_DUP, containment(a, b)


def _main():
    red, green = [], []
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        try:
            fn()
        except AssertionError as exc:
            red.append((fn.__name__, str(exc)[:200]))
            print(f"RED  {fn.__name__}: {str(exc)[:200]}")
        else:
            green.append(fn.__name__)
            print(f"PASS {fn.__name__}")
    print(f"{len(green)} green, {len(red)} red of {len(fns)}")
    if red:
        raise SystemExit(1)


if __name__ == "__main__":
    _main()
