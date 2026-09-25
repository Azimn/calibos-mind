"""Tests for the workspace view (calibos_mind/workspace.py):
Jaccard near-duplicate dedupe + per-class caps.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_workspace.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures are in-memory; the live store is never touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibos_mind.workspace import CLASS_CAPS, CalibosWorkspace


def _ws(texts, pinned=(), tracker=None):
    """Build a workspace. texts: list of (source, text). pinned: list of text."""
    ws = CalibosWorkspace()
    tick = 1
    for source, text in texts:
        ws.add(tick, source, text)
        tick += 1
    for text in pinned:
        ws.add(tick, "memory", text, generated_by="cartridge")
        tick += 1
    ws.salience_tracker = tracker
    try:
        return ws.view()
    finally:
        ws.salience_tracker = None


class _ConstTracker:
    """Activation = per-record weight supplied in `weights` (default 1.0)."""

    def __init__(self, weights):
        self.weights = weights

    def activation(self, rid, created_tick, now_tick, unresolved=False):
        return self.weights.get(rid, 1.0)


def test_exact_duplicates_collapse():
    view = _ws([
        ("memory", "I remember the garden gate being open this morning and the light was strange"),
        ("memory", "I remember the garden gate being open this morning and the light was strange"),
    ])
    texts = [e.first_person for e in view.experiences]
    assert len(texts) == 1, texts


def test_near_duplicate_dropped_in_salience_order():
    # Same memory resurfacing with slightly different wording; the more
    # salient phrasing wins and only one survives.
    t1 = ("memory", "I vaguely recall the garden gate being open this morning and the light was very strange indeed")
    t2 = ("memory", "I vaguely remember the garden gate being open this morning and the light was very strange indeed")
    ws = CalibosWorkspace()
    ws.add(1, *t1)
    ws.add(2, *t2)
    # t1 gets higher activation -> t1 is the surviving representative.
    ws.salience_tracker = _ConstTracker({"experience-1": 5.0, "experience-2": 1.0})
    try:
        view = ws.view()
    finally:
        ws.salience_tracker = None
    texts = [e.first_person for e in view.experiences]
    assert len(texts) == 1, texts
    assert texts[0] == t1[1], texts


def test_dissimilar_texts_both_kept():
    view = _ws([
        ("memory", "I remember the garden gate being open this morning while the kettle sang loudly"),
        ("memory", "I considered whether the river would freeze before the first snow of winter arrived"),
    ])
    assert len(view.experiences) == 2


def test_short_texts_exempt_from_dedupe():
    # Too few tokens for a trustworthy Jaccard verdict; both stay.
    view = _ws([
        ("interoception", "That feeling is easing now"),
        ("interoception", "That feeling is fading now"),
    ])
    assert len(view.experiences) == 2


def test_dedupe_is_same_source_only():
    # Identical wording in different classes is a real phenomenon, not a dupe.
    view = _ws([
        ("memory", "I noticed the garden gate was open this morning and the light was strange"),
        ("thought", "I noticed the garden gate was open this morning and the light was strange"),
    ])
    assert len(view.experiences) == 2


def test_class_caps_bound_dominance():
    # 14 memories competing for 16 slots: memory cap is 4, no backfill, so
    # the window ends up smaller instead of memory-dominated.
    texts = [("memory", f"Memory number {i} about the garden gate and the morning light {i}th time")
             for i in range(14)]
    view = _ws(texts, tracker=_ConstTracker({}))
    exps = view.experiences
    mems = [e for e in exps if e.source == "memory"]
    assert len(mems) == CLASS_CAPS["memory"] == 4, len(mems)
    assert len(exps) == 4, len(exps)  # hard ceiling: no backfill


def test_caps_leave_room_for_other_classes():
    # A saturated class must not crowd out others.
    texts = ([("memory", f"Memory number {i} about the garden gate and the morning light {i}th time")
              for i in range(10)]
             + [("thought", "I wonder whether the river will freeze before the first snow arrives this year"),
                ("perception", "The kettle sang loudly on the stove while the morning light came through the window"),
                ("interoception", "A quiet restlessness settles in my chest like a held breath")])
    view = _ws(texts, tracker=_ConstTracker({}))
    by_source = {}
    for e in view.experiences:
        by_source[e.source] = by_source.get(e.source, 0) + 1
    assert by_source["memory"] == 4, by_source
    assert by_source["thought"] == 1 and by_source["perception"] == 1
    assert by_source["interoception"] == 1, by_source


def test_pinned_exempt_from_caps_and_dedupe():
    view = _ws(
        [("memory", f"Memory number {i} about the garden gate and the morning light {i}th time")
         for i in range(10)],
        pinned=["I know myself as Calibos and I keep this identity firmly in mind",
                "I prefer short natural chat replies and real depth only when it is asked for"],
    )
    assert len(view.experiences) == 6  # 2 pinned + 4 capped memories
    pinned_texts = [e.first_person for e in view.experiences[:2]]
    assert pinned_texts[0].startswith("I know myself"), pinned_texts


def test_fallback_recency_order_preserved():
    # No tracker: newest records win, window stays chronological, capped at 16.
    texts = [(s, f"{s} record number {i} with enough words to dodge the dupe rule {i}")
             for i, s in enumerate(["thought"] * 10 + ["memory"] * 10)]
    view = _ws(texts)  # tracker=None
    exps = view.experiences
    by_source = {}
    for e in exps:
        by_source[e.source] = by_source.get(e.source, 0) + 1
    assert by_source["thought"] == 4 and by_source["memory"] == 4, by_source
    assert len(exps) == 8, len(exps)
    # Chronological: the newest memory should come after the newest thought
    # only if tick ordering holds; just check the texts are tick-ordered.
    order = [int(e.first_person.split("number ")[1].split(" ")[0]) for e in exps]
    assert order == sorted(order), order


def test_window_never_exceeds_limit():
    texts = [(s, f"A distinct {s} record number {i} with plenty of unique wording here {i} x")
             for i, s in enumerate(["thought", "memory", "perception", "interoception",
                                    "temporal", "social"] * 10)]
    view = _ws(texts, tracker=_ConstTracker({}))
    assert len(view.experiences) <= 16, len(view.experiences)
    # Sum of class caps must cover the limit: 4+4+3+2+2+3 = 18 >= 16.
    assert sum(CLASS_CAPS.values()) >= 16


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok: {fn.__name__}")
    print(f"{len(fns)} passed")
