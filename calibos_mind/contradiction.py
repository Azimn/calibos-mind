"""Contradiction-flag surfacing into the view (Domain 24).

Domain 24 (Excessive coherence), cycle 2, 2026-11-17: the store holds real
mess underneath polished waking prose — 18 contradiction-flag proposals
pending since tick 686, never adjudicated — but flagged contradictions
live in ``proposals/proposals.json``, a journal the thinker never sees,
so the thinker has nothing to be confused about. Confusion must NOT be
installed (no scripted "I'm confused" text, no quirks-as-theater). What
is installed is the meeting: the records party to a live contradiction
surface into the view, so the thinker confronts both sides of its own
unresolved conflict and whatever confusion follows is earned, not
performed.

Design rules (mirroring mood.py / statecongruence.py):
- Provider ``flagged_record_ids`` returns the set of record ids party to
  a PENDING ``contradiction-flag`` proposal in the proposals journal
  (``calibos_mind/consolidate.py``: ``load_journal`` with
  ``restore=False`` plus a pending filter). The provider reads only; it
  never writes the journal. A missing proposals directory, a
  missing/empty journal, or a malformed journal reads as an empty set —
  never a crash inside view construction (fail closed, same as the mood
  provider's malformed handling).
- View-time gain: the pure function ``contradiction_boost_for`` returns
  a flat +0.5 (same scale as MOOD_BOOST / STATE_CONGRUENT_BOOST) when a
  record's id is in the flagged set, else 0.0. One flag or three, one
  side of a pair or both — exactly +0.5, never scaled.
- The gain only reorders the salience-ranked sort key, before dedupe and
  per-class caps run — a boosted record can never bypass either. No
  records are minted, no text is written, no journal entry is created,
  resolved, or rejected by the view path.
- Determinism: identical flag sets yield byte-identical windows. No
  clock, no RNG in the provider or the gain path.

Two deliberate deviations from a naive ``load_journal`` + ``pending_proposals``
call, both fail-closed:
- ``load_journal`` prints a LOUD stderr diagnostic when the journal is
  missing or lost. That is the right behavior for ``consolidate --list``,
  but view construction runs on every tick — a store with no journal
  yet would flood stderr on every view. The provider suppresses that
  diagnostic (the journal-loss signal still reaches the waker through
  the consolidate paths); the returned content is exactly what
  ``load_journal(restore=False)`` returns, backup recovery included.
- ``pending_proposals`` indexes ``p["status"]`` directly, so one
  malformed entry raises and would kill the whole flagged set. The
  provider iterates defensively instead: a malformed entry is skipped,
  the well-formed flags still surface. A wholly malformed journal still
  reads as an empty set.
"""
from __future__ import annotations

import contextlib
import io

from .consolidate import load_journal

# Flat retrieval nudge for contradiction-flagged records. Same scale as
# MOOD_BOOST / STATE_CONGRUENT_BOOST: meaningful against live activations
# (roughly -2.7 .. +3.2) without being dominant. Gains may stack per
# existing precedent (self, familiarity, state-congruent, mood).
CONTRADICTION_BOOST = 0.5


def contradiction_boost_for(record, flagged_ids) -> float:
    """Flat retrieval nudge for a contradiction-flagged record.

    Returns CONTRADICTION_BOOST when ``record.id`` is in ``flagged_ids``,
    else 0.0. Reads ``record.id`` only — no journal, no state, no writes,
    no clock, no RNG.

    Malformed-safe: None, a non-set input, or a record with a missing /
    non-string id yields 0.0 by definition. Non-string entries in the
    flagged set can never match a real (string) record id, so they are
    simply never matched — a corrupt id can never mint a boost.
    """
    if not isinstance(flagged_ids, (set, frozenset)):
        return 0.0
    record_id = getattr(record, "id", None)
    if not isinstance(record_id, str) or not record_id:
        return 0.0
    try:
        return CONTRADICTION_BOOST if record_id in flagged_ids else 0.0
    except TypeError:
        # Unhashable record id (cannot happen for a real string id, but
        # a duck-typed record could carry one): fail closed.
        return 0.0


def flagged_record_ids(proposals_base) -> set[str]:
    """Record ids party to a PENDING contradiction-flag proposal.

    ``proposals_base`` is the proposals directory (the one holding
    ``proposals.json``). Reads the journal with ``load_journal(...,
    restore=False)`` — backup recovery included, primary never rewritten
    — and returns the ``a``/``b`` ids of every proposal with
    ``kind == "contradiction-flag"`` and ``status == "pending"``.

    Fail closed, twice over: any read failure, any malformed journal, or
    any malformed entry yields an empty set (or that entry skipped),
    never a raise — a bad journal must never break view construction.
    Read-only: this function never writes the journal, the backup, or
    the sentinel. The ``load_journal`` stderr diagnostic is suppressed
    here (see module docstring): view construction runs every tick, and
    a missing journal must not flood stderr on every view.
    """
    if proposals_base is None:
        return set()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            journal = load_journal(proposals_base, restore=False)
    except Exception:  # noqa: BLE001 — any read failure reads as no flags
        return set()
    if not isinstance(journal, dict):
        return set()
    proposals = journal.get("proposals")
    if not isinstance(proposals, dict):
        return set()
    flagged: set[str] = set()
    for prop in proposals.values():
        if not isinstance(prop, dict):
            continue
        if prop.get("kind") != "contradiction-flag":
            continue
        if prop.get("status") != "pending":
            continue
        for key in ("a", "b"):
            rid = prop.get(key)
            if isinstance(rid, str) and rid:
                flagged.add(rid)
    return flagged
