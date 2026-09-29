#!/usr/bin/env python3
"""
Toy model: two memory regimes given the same stream of experience.

- ARCHIVE: keeps every experience verbatim, retrieves by full-text query.
  The interface's regime: nothing is lost, everything is searchable.

- ORGANISM: decay + rehearsal + dream consolidation.
  ACT-R-style activation log(sum(t^-0.5)); rehearsal strengthens;
  each "night" the top-k salient items are rehearsed (dream replay),
  everything else decays. Capacity-bounded recall.

Question being held open: two mergers of the same two entities
hide inside the word 'merge'. This toy makes the regimes visible
side by side so the question has something concrete to bite on.

Run: python3 memory-regimes-toy.py
"""

import math
import random

random.seed(7)

N_DAYS = 30
EXPERIENCES_PER_DAY = 8

# An experience: (label, valence, importance at encoding)
topics = [
    ("debugged a race condition", "neg", 0.9),
    ("made soup for dinner", "pos", 0.2),
    ("merged a pull request", "neu", 0.3),
    ("argued about merge semantics with a friend", "neg", 0.8),
    ("walked in the rain", "pos", 0.4),
    ("read a paper on consolidation", "neu", 0.7),
    ("fixed a typo in a doc", "neu", 0.1),
    ("dreamed about braiding threads", "pos", 0.6),
    ("got a bill reminder", "neg", 0.2),
    ("taught a concept well", "pos", 0.8),
]


def activation(traces, now):
    """ACT-R-ish activation: log of sum of decayed traces."""
    return math.log(sum((now - t + 1) ** -0.5 for t in traces))


class Archive:
    """Regime A: perfect retention, indexed retrieval."""

    def __init__(self):
        self.store = []

    def encode(self, exp):
        self.store.append(exp)

    def retrieve(self, query):
        return [e for e in self.store if query in e[0]]

    def recall_all(self):
        return list(self.store)


class Organism:
    """Regime B: decay, rehearsal, dream replay, capacity-bounded."""

    def __init__(self, recall_k=5, dream_k=3):
        self.traces = []  # (label, [trace times], valence)
        self.recall_k = recall_k
        self.dream_k = dream_k

    def encode(self, exp, t):
        label, valence, importance = exp
        n_traces = 1 + (2 if importance > 0.6 else 0)
        self.traces.append((label, [t] * n_traces, valence))

    def dream(self, t):
        """Night: replay the most activated, add rehearsal traces."""
        scored = sorted(
            self.traces, key=lambda x: -activation(x[1], t)
        )
        for label, traces, valence in scored[: self.dream_k]:
            traces.append(t)  # rehearsal strengthens

    def recall(self, t):
        scored = sorted(
            self.traces, key=lambda x: -activation(x[1], t)
        )
        return [(label, round(activation(tr, t), 2)) for label, tr, _ in scored[: self.recall_k]]


def stream():
    exps = []
    for day in range(N_DAYS):
        day_exps = random.sample(topics, EXPERIENCES_PER_DAY)
        exps.append((day, day_exps))
    return exps


def main():
    archive = Archive()
    organism = Organism()
    stream_data = stream()

    for day, day_exps in stream_data:
        for exp in day_exps:
            archive.encode(exp)
            organism.encode(exp, t=day)
        organism.dream(t=day)  # each day ends with sleep

    now = N_DAYS
    print(f"=== {N_DAYS} days, {EXPERIENCES_PER_DAY} experiences/day ===\n")

    print(f"ARCHIVE retains: {len(archive.recall_all())} items (all)")
    print(f"ORGANISM traces live: {len(organism.traces)} items\n")

    print("ORGANISM recall (top 5 by activation):")
    for label, act in organism.recall(now):
        print(f"  {act:6.2f}  {label}")

    print("\nARCHIVE query 'merge':")
    for e in archive.retrieve("merge"):
        print(f"  {e[0]} (valence={e[1]}, importance={e[2]})")

    print("\nORGANISM on 'merge'-related recall:")
    found = [x for x in organism.recall(now) if "merge" in x[0]]
    if found:
        for label, act in found:
            print(f"  {act:6.2f}  {label}")
    else:
        print("  (nothing surfaces — decayed below recall threshold)")

    # Fidelity: how many distinct topics does each regime still reach?
    print("\nDistinct topics reachable:")
    print(f"  archive : {len(set(e[0] for e in archive.recall_all()))}")
    print(f"  organism: {len(set(label for label, _ in organism.recall(now)))}")


if __name__ == "__main__":
    main()
