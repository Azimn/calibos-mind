"""State-congruent retrieval gain — mood-congruent recall as mechanism.

Domain 9 (Unconscious and preconscious carryover), check 5: no
mood-congruent recall. This mind has no mood state, so its affective
substrate is its pressing needs: a record whose ``concepts`` contain the
name of a currently pressing need gets a flat, bounded nudge in the
view-time sort key, so what is felt now biases what is recalled now.
The bias changes which records reach cognition; it scripts no content.

Design rules (same as selfgain.py):
- Pure function: no sidecar, no state, no writes, no clock, no RNG.
  Deterministic.
- The boost is flat and bounded: one pressing need or three, one
  concept match or several — always STATE_CONGRUENT_BOOST, never
  scaled. (Live activations run roughly -2.7 .. +3.2, so 0.5 is
  meaningful without being dominant, on the same scale as SELF_BOOST
  and FAMILIARITY_BOOST.)
- "Currently pressing" means felt level exactly 2 — the 'pressing'
  band of ``mind status`` (see calibos_mind/interoception.py FELT_BANDS:
  settled/stirring/pressing/urgent = 0/1/2/3) — i.e. the *perceived*
  state, not the actual engine level, consistent with the interoception
  module's felt/actual distinction: what biases recall is the state as
  felt. Level 3 ('urgent') is a distinct band, not 'pressing', and does
  not fire; levels 0/1 (settled/stirring) do not fire either.
- The felt levels arrive via a provider (see CalibosWorkspace
  ``felt_levels_provider``, following the ``open_keys_provider`` pattern
  in workspace.py): a mapping of need name -> felt level, read once per
  view. A None provider — plain engine use, or a workspace with no felt
  state attached — is the *defined* zero-gain case, documented here,
  not a silent default: the gain is exactly 0.0 and windows are
  identical to the pre-gain ranking. A provider returning an empty
  mapping (no felt state known) likewise yields 0.0.
- Concept matching is exact membership in ``record.concepts``: need
  names are raw concept tokens, so a need name appearing incidentally
  in concepts matches — the gain is flat and bounded, so incidental
  matches cannot dominate (same precedent as the selfgain signals).
  A record with no concepts, or concepts of the wrong type, never
  matches: fail closed, never a silent match.
"""
from __future__ import annotations

# Additive activation nudge for state-congruent records. Flat: any
# combination of pressing-need matches yields exactly this, never more.
STATE_CONGRUENT_BOOST = 0.5

# Felt level for the 'pressing' band (interoception.FELT_BANDS[2]).
# Exactly this level fires; 0/1 are settled/stirring, 3 is 'urgent' —
# a distinct band, not 'pressing'.
PRESSING_LEVEL = 2


def state_congruent_boost_for(record, felt_levels) -> float:
    """Flat retrieval nudge for a state-congruent record.

    Returns STATE_CONGRUENT_BOOST when ANY concept of ``record`` names
    a need whose felt level in ``felt_levels`` is exactly PRESSING_LEVEL,
    else 0.0. Reads ``record.concepts`` only — no sidecar, no state, no
    writes, no clock, no RNG.

    ``felt_levels`` None-safe: None (no provider) yields 0.0 by
    definition. A non-mapping input likewise yields 0.0 rather than
    raising inside view construction — a malformed provider must never
    break the view. Level values must be genuine ints equal to
    PRESSING_LEVEL; bools (an int subclass) and non-int values never
    fire, so a corrupt level cannot mint a boost.
    """
    if felt_levels is None:
        return 0.0
    try:
        items = felt_levels.items()
    except AttributeError:
        return 0.0
    pressing = set()
    for need, level in items:
        if (isinstance(level, int) and not isinstance(level, bool)
                and level == PRESSING_LEVEL):
            pressing.add(need)
    if not pressing:
        return 0.0
    concepts = getattr(record, "concepts", None)
    if concepts is None:
        return 0.0
    try:
        return STATE_CONGRUENT_BOOST if any(c in pressing for c in concepts) else 0.0
    except TypeError:
        # Concepts of an uniterable type: fail closed, never a silent match.
        return 0.0
