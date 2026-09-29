"""Familiarity traces — metacognition for near-miss retrieval.

Domain 4 (Memory artificiality), check 17: a record ranking just below the
view cut vanishes without a trace — the organism has no state for
"something relevant almost surfaced." This module installs the *mechanism*
(a near-miss streak with a bounded retrieval nudge), not the *symptom*:
the "unplaceable" part stays honest — the streak knows an id, the view
still lacks its content until the nudge earns admission.

Design rules:
- Local-only sidecar (familiarity.json, gitignored). Engine records are
  never mutated; only the retrieval-time sort key changes, and
  ``SalienceTracker.activation()`` itself is untouched — ``salience.json``,
  ``mind drift``, and R are unaffected.
- The boost is flat and bounded: once the streak reaches the threshold the
  record gets FAMILIARITY_BOOST, never more, however long the streak runs.
  (Live activations run roughly -2.7 .. +3.2, so 0.5 is meaningful without
  being dominant.)
- No-op write discipline: ``observe()`` returns True only when the streak
  dict actually changed; the caller saves only then. A view with no
  near-misses and no resets writes nothing — absent stays absent, present
  stays byte-identical.
- Determinism: identical sequences of (view, observe) cycles produce
  byte-identical sidecars (sorted keys, no wall clock, no RNG).
- Waking ticks only: the caller (``cli._run_tick``) never runs this on the
  dream path, and read-only commands never call it. Constructing a view
  must not touch the sidecar.
"""
from __future__ import annotations

import json
from pathlib import Path

# 2 x VIEW_LIMIT — how deep into the ranked list near-misses are considered.
FAMILIARITY_WINDOW = 32
# Consecutive near-miss views before the nudge engages.
FAMILIARITY_THRESHOLD = 3
# Additive activation nudge, flat once the threshold is reached, never
# scaled by streak length.
FAMILIARITY_BOOST = 0.5


class FamiliarityTracker:
    """Near-miss streaks for records that almost made the view.

    ``data`` is ``{"streaks": {record_id: consecutive_near_miss_count}}``.
    The constructor only reads the sidecar; streaks move exclusively via
    ``observe()`` + ``save()`` on the waking-tick path.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"streaks": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    streaks = loaded.get("streaks")
                    self.data["streaks"] = streaks if isinstance(streaks, dict) else {}
            except (ValueError, OSError):
                pass

    def boost_for(self, record_id: str) -> float:
        """The retrieval-time nudge for a record: FAMILIARITY_BOOST once its
        streak reaches the threshold, else 0.0. Bounded by construction —
        a streak of 100 still yields 0.5."""
        if self.data["streaks"].get(record_id, 0) >= FAMILIARITY_THRESHOLD:
            return FAMILIARITY_BOOST
        return 0.0

    def observe(self, admitted_ids, near_miss_ids) -> bool:
        """Fold one view's outcome into the streaks.

        - Every near-miss id's streak increments.
        - Streaks for admitted ids are dropped (admission resets — the
          familiar thing got placed).
        - Ids in neither set are dropped (pruning — the sidecar never grows
          unbounded: a record that stopped competing is forgotten).

        Returns True iff the streak dict changed, so the caller saves only
        then. Accepts any iterables of ids.
        """
        admitted = set(admitted_ids)
        near = set(near_miss_ids)
        streaks = self.data["streaks"]
        changed = False
        for rid in admitted:
            if rid in streaks:
                del streaks[rid]
                changed = True
        for rid in near:
            if rid in admitted:
                # Cannot happen by construction (a near-miss is not
                # admitted); admission wins if it ever does.
                continue
            streaks[rid] = streaks.get(rid, 0) + 1
            changed = True
        for rid in list(streaks):
            if rid not in admitted and rid not in near:
                del streaks[rid]
                changed = True
        return changed

    def reset(self) -> None:
        """Clear the sidecar (used when the store is reseeded; ids restart).

        Stale streaks must never attach to recycled ids — same bug class as
        the salience reset.
        """
        self.data = {"streaks": {}}
        self.save()

    def save(self) -> None:
        ordered = {"streaks": {k: self.data["streaks"][k]
                               for k in sorted(self.data["streaks"])}}
        self.path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1),
                             encoding="utf-8")
