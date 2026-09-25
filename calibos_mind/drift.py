"""Drift metric — a quantitative persona-stability signal.

Salvage of the Aura/LCE "identity tracking" idea, rebuilt over
distributions we actually have instead of a hardcoded trait vector
(their traits were constants; the KL tracked nothing real). Two
deterministic signals, zero new models, zero new persistent state,
zero new schedules.

1. Grown/authored salience ratio
   R = sum(salience of grown records) / sum(salience of all records).
   Authored = records stamped generated_by="cartridge" — the identity
   root and the seed memories, i.e. the hand-authored content. Grown =
   everything the engine recorded through lived ticks: answered and
   voluntary thoughts ("cognition"), body events, engine-propagated
   records, and legacy records that predate the generated_by stamp.
   Prompt answers become thoughts via the inner ear, so the thinking
   itself counts as grown; salience is the same ACT-R activation the
   workspace view uses. Raw activations can be negative (an old,
   unrehearsed record scores below zero), so weights are shifted to put
   the least-salient record at the floor: R is the grown share of
   *relative* retrieval mass above that floor. If every record ties,
   there is no relative mass and R is None — reported as undefined,
   never as a silent 0.

2. Trigger-histogram KL
   D_KL(P_recent || P_baseline) over cognition trigger kinds
   (external, body_change, prior_thought, association, ...). Recent =
   the last W cognition triggers, baseline = everything before them.
   Detects regime shifts in what activates cognition — e.g. the view
   saturation crowding-out seen in the live demos. Epsilon-smoothed;
   refuses to score below a minimum window (no silent defaults —
   an under-powered comparison reports its counts, not a number).

Read-only: computes from the store payload and the salience sidecar.
It never calls tracker.save() and never writes anywhere.
"""
from __future__ import annotations

import math
from typing import Callable

from .unresolved import has_unresolved_links, open_link_keys

AUTHORED_GENERATOR = "cartridge"  # the hand-authored content stamp
MIN_TRIGGERS = 5                  # minimum triggers per window to score KL
DEFAULT_WINDOW = 10               # recent-window size for the trigger KL
EPS = 1e-3                        # additive smoothing for the KL


def is_authored(record: dict) -> bool:
    """Authored = stamped by the cartridge (identity root, seed memories)."""
    return record.get("generated_by") == AUTHORED_GENERATOR


def grown_authored_ratio(records: list[dict],
                         weight_fn: Callable[..., float],
                         now_tick: int,
                         open_keys: set | None = None) -> dict:
    """R = grown salience / total salience over all records.

    Archived records still count as history here by deliberate design
    (see CHANGELOG consolidation notes): only cognition views consult
    the availability journal. R measures the organism's accumulated
    retrieval mass, not just the currently available window.

    weight_fn(rid, created_tick, now_tick, unresolved=bool) -> float,
    mirroring SalienceTracker.activation. The `unresolved` flag comes
    from the shared has_unresolved_links helper: a record's links are
    intersected with the currently-open set (pass open_keys from
    open_link_keys(state)); open_keys=None keeps the legacy any-link
    check explicitly. Raw weights are shifted so the least-salient
    record sits at 0 (relative retrieval mass); R is None when every
    record ties and there is no relative mass to share.
    """
    raw: list[tuple[dict, float]] = []
    for r in records:
        raw.append((r, weight_fn(
            r["id"], r["tick"], now_tick,
            unresolved=has_unresolved_links(
                r.get("concern_links"), r.get("expectation_links"), open_keys),
        )))
    floor = min((w for _, w in raw), default=0.0)
    grown = authored = 0.0
    n_grown = n_authored = 0
    top_grown: list[tuple[float, str]] = []
    top_authored: list[tuple[float, str]] = []
    for r, w in raw:
        w = w - floor
        if is_authored(r):
            authored += w
            n_authored += 1
            top_authored.append((w, r["id"]))
        else:
            grown += w
            n_grown += 1
            top_grown.append((w, r["id"]))
    total = grown + authored
    top_grown.sort(reverse=True)
    top_authored.sort(reverse=True)
    return {
        "R": (grown / total) if total > 0 else None,
        "grown_salience": grown,
        "authored_salience": authored,
        "n_grown": n_grown,
        "n_authored": n_authored,
        "top_grown": top_grown[:3],
        "top_authored": top_authored[:3],
    }


def _trigger_kinds(trace: list[dict]) -> list[str]:
    """Cognition trigger kinds in tick order."""
    pairs = []
    for t in trace or []:
        if t.get("kind") == "cognition_trigger":
            trig = t.get("trigger") or {}
            pairs.append((t.get("tick", 0), trig.get("kind", "unknown")))
    pairs.sort(key=lambda p: p[0])
    return [k for _, k in pairs]


def trigger_kl(trace: list[dict], window: int = DEFAULT_WINDOW,
               eps: float = EPS, min_n: int = MIN_TRIGGERS) -> dict:
    """KL(recent trigger histogram || baseline trigger histogram), in nats.

    Recent = last W triggers, baseline = all earlier triggers. If the
    store holds fewer than 2*min_n triggers, the window shrinks to half
    the available history (reported honestly); below min_n per side it
    refuses to score rather than returning a noise number.
    """
    kinds = _trigger_kinds(trace)
    n = len(kinds)
    w = min(window, n // 2)
    if w < min_n:
        return {
            "ok": False,
            "reason": (f"insufficient trigger history: {n} cognition triggers, "
                       f"need >= {2 * min_n} to score (window {window})"),
            "n_total": n,
            "n_recent": 0,
            "n_baseline": 0,
        }
    recent = kinds[-w:]
    baseline = kinds[:-w]
    vocab = sorted(set(recent) | set(baseline))
    k = len(vocab)
    rp = {c: (recent.count(c) + eps) / (w + eps * k) for c in vocab}
    bq = {c: (baseline.count(c) + eps) / ((n - w) + eps * k) for c in vocab}
    kl = sum(rp[c] * math.log(rp[c] / bq[c]) for c in vocab)
    return {
        "ok": True,
        "kl": kl,
        "window": w,
        "n_total": n,
        "n_recent": w,
        "n_baseline": n - w,
        "recent_hist": {c: recent.count(c) for c in vocab},
        "baseline_hist": {c: baseline.count(c) for c in vocab},
    }


def drift_report(state: dict, tracker, window: int = DEFAULT_WINDOW) -> dict:
    """Both signals from one inspect() snapshot. Read-only (no save)."""
    records = state["workspace"]["records"]
    now_tick = state["engine"]["tick"]
    ratio = grown_authored_ratio(
        records, tracker.activation, now_tick, open_keys=open_link_keys(state))
    kl = trigger_kl(state.get("trace", []), window=window)
    return {
        "tick": now_tick,
        "n_records": len(records),
        "ratio": ratio,
        "trigger_kl": kl,
    }
