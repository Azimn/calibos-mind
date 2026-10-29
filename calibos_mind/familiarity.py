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
- Persistent-near-miss notes (domain 4, 2026-10-28): once a streak reaches
  PERSISTENT_NEAR_MISS_THRESHOLD consecutive near-miss views without
  admission, the waking tick mints exactly one temporal record
  (generated_by=cognition) naming the record id and the streak count —
  never any content of the target record. One note per episode: the
  ``noted`` guard in the sidecar latches when the note is minted and
  clears when the streak ends (admission or pruning), so a later streak
  to the threshold may note again. The guard shares the sidecar's
  discipline: old sidecars without it load cleanly, ``reset()`` wipes it
  with the streaks, and read-only/dream paths never touch it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 2 x VIEW_LIMIT — how deep into the ranked list near-misses are considered.
FAMILIARITY_WINDOW = 32
# Consecutive near-miss views before the nudge engages.
FAMILIARITY_THRESHOLD = 3
# Additive activation nudge, flat once the threshold is reached, never
# scaled by streak length.
FAMILIARITY_BOOST = 0.5
# Consecutive near-miss views before a persistent-near-miss note is
# minted, once per episode. Above the boost threshold (3) and below the
# observed in-vivo maximum streak (18), so it fires only on genuinely
# persistent cases.
PERSISTENT_NEAR_MISS_THRESHOLD = 10


class FamiliarityTracker:
    """Near-miss streaks for records that almost made the view.

    ``data`` is ``{"streaks": {record_id: consecutive_near_miss_count},
    "noted": {record_id: streak_count_at_note_time}}``. ``noted`` is the
    per-episode guard for persistent-near-miss notes: present while an
    episode's note is owed-or-minted, cleared when the streak ends. The
    constructor only reads the sidecar; streaks move exclusively via
    ``observe()`` + ``save()`` on the waking-tick path.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {"streaks": {}, "noted": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    streaks = loaded.get("streaks")
                    self.data["streaks"] = streaks if isinstance(streaks, dict) else {}
                    # Pre-mutation sidecars have no guard key: a missing
                    # key is a legitimate fresh start, not malformed.
                    noted = loaded.get("noted")
                    self.data["noted"] = noted if isinstance(noted, dict) else {}
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
        noted = self.data["noted"]
        changed = False
        # Self-heal orphan guards FIRST (guard without a streak at entry):
        # the guard is only ever set while a streak exists, so an orphan
        # is a hand-crafted or crashed sidecar, never a live episode. It
        # must go before the near-miss loop below, or the fresh streak
        # starting in this same call would inherit a dead episode's guard
        # and its note at the threshold would be wrongly suppressed.
        for rid in list(noted):
            if rid not in streaks:
                del noted[rid]
                changed = True
        for rid in admitted:
            if rid in streaks:
                del streaks[rid]
                changed = True
            if rid in noted:
                # Admission ends the episode: the guard clears with the
                # streak, so a later streak to the threshold notes again.
                del noted[rid]
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
                if rid in noted:
                    # Pruning ends the episode, same as admission.
                    del noted[rid]
        return changed

    def due_notes(self) -> list:
        """Episodes that crossed the persistent threshold without a note.

        Returns a sorted list of (record_id, streak) pairs: streaks at or
        above PERSISTENT_NEAR_MISS_THRESHOLD whose guard is not set. The
        caller marks each guard (mark_noted) and saves BEFORE minting, so
        a kill between the save and the mint loses the note (safe
        direction) rather than duplicating it.
        """
        return sorted(
            (rid, streak)
            for rid, streak in self.data["streaks"].items()
            if streak >= PERSISTENT_NEAR_MISS_THRESHOLD
            and rid not in self.data["noted"])

    def mark_noted(self, record_id: str, streak: int) -> None:
        """Latch the per-episode guard for a record (caller saves after).

        Records the streak count at note time for inspection; only the
        key's presence is ever consulted (due_notes).
        """
        self.data["noted"][record_id] = streak

    def reset(self) -> None:
        """Clear the sidecar (used when the store is reseeded; ids restart).

        Stale streaks must never attach to recycled ids — same bug class as
        the salience reset. The guard wipes with the streaks: a recycled
        id must never inherit a previous incarnation's noted episode.
        """
        self.data = {"streaks": {}, "noted": {}}
        self.save()

    def save(self) -> None:
        ordered = {"streaks": {k: self.data["streaks"][k]
                               for k in sorted(self.data["streaks"])},
                   "noted": {k: self.data["noted"][k]
                             for k in sorted(self.data["noted"])}}
        self.path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1),
                             encoding="utf-8")


# -- persistent-near-miss notes: canonical text, parsing, minting ---------
# (domain 4, 2026-10-28). Follows the stale-habit-note precedent: the
# record text is the canonical serialization — the only writer is
# near_miss_note_text(), the only reader is the anchored fullmatch below,
# so the identifier cannot drift from the writer. Content ban: the text
# names the record id, the streak count, and the tick only — never any
# content of the target record, so nothing is fabricated as recalled.

def near_miss_note_text(event: dict) -> str:
    """First-person text for one persistent-near-miss threshold crossing.

    No contractions (the similarity vetoes tokenize on raw text —
    regression genome), no wall clock: identical episodes mint
    byte-identical text.
    """
    return (
        f"Something feels familiar but I cannot place it. "
        f"That is {event['streak']} consecutive near-miss views of "
        f"{event['record_id']}, still unplaced, at tick {event['tick']}."
    )


_NEAR_MISS_NOTE_RE = re.compile(
    r"Something feels familiar but I cannot place it\. "
    r"That is (\d+) consecutive near-miss views of (\S+), "
    r"still unplaced, at tick (\d+)\.")


def parse_near_miss_note_text(text) -> dict | None:
    """Parse a persistent-near-miss note's canonical text, or None.

    Returns the episode identity (record_id, tick) plus the streak. None
    means "not a persistent-near-miss note" — never a partial match: the
    fullmatch anchors both ends.
    """
    if not isinstance(text, str):
        return None
    m = _NEAR_MISS_NOTE_RE.fullmatch(text)
    if m is None:
        return None
    return {"streak": int(m.group(1)), "record_id": m.group(2),
            "tick": int(m.group(3))}


def is_near_miss_note_text(text) -> bool:
    """Whether a record text is a domain-4 persistent-near-miss note."""
    return parse_near_miss_note_text(text) is not None


def mint_near_miss_note(subject, event: dict) -> str:
    """Append one persistent-near-miss note through the subject's normal
    record path.

    Waking-tick path only (cli._run_tick); the caller owns the
    transaction so the record persists, and the caller marks + saves the
    sidecar guard BEFORE calling this, so a kill between the guard save
    and this mint loses the note rather than duplicating it. Minting
    never advances the store tick and never touches the trace: it is a
    write inside the existing tick, not a new tick. Stamped
    generated_by="cognition" (engine-originated, not authored), and
    enters view eligibility, salience, and dedupe like any temporal
    record — but NOT near-miss candidacy: workspace.view() excludes
    note texts from the near-miss side-channel by canonical pattern, so
    a note can never build a streak and mint a note about itself
    (round-2 fix for the unbounded self-reference cascade). Returns the
    record id.

    Idempotent on (record_id, tick): when a note for the same record at
    the same tick is already in the store, the existing record's id is
    returned and nothing is appended. A later episode notes at a later
    tick, so it mints separately. The check reads
    subject.workspace.records, the in-memory list _add appends to, so
    mints earlier in the SAME transaction are visible.
    """
    for record in subject.workspace.records:
        if record.source != "temporal" or record.generated_by != "cognition":
            continue
        parsed = parse_near_miss_note_text(record.first_person)
        if (parsed is not None
                and parsed["record_id"] == event["record_id"]
                and parsed["tick"] == event["tick"]):
            return record.id
    item = subject._add("temporal", near_miss_note_text(event),
                        generated_by="cognition",
                        available_to_cognition=True,
                        salience=1.0, intensity=1.0)
    return item.id
