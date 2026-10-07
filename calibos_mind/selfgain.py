"""Self-relevance retrieval gain — the cocktail-party effect as mechanism.

Domain 12 (Attention), check 12: no personally salient information
stealing attention. The frozen engine already marks self-relevant events
(``digital_subject/engine.py`` ``_own_event`` falls back to first-person
meaning starting with ``"This event concerns me: "`` when the event kind
has no ``EVENT_RULES`` entry and no ``first_person_meaning`` metadata
override — the target plays no role in meaning selection) and tags identity records with the 'identity' concept — but
those signals die at event processing. This module extends them into retrieval: a
self-referential record gets a flat, bounded nudge in the view-time sort
key, so identity-level information can capture the cognitive window
bottom-up. Capture emerges from the ranking; nothing is scripted to
"notice its name."

Design rules (same as familiarity.py):
- Pure function: no sidecar, no state, no writes. Deterministic.
- The boost is flat and bounded: one signal or three, one mention or
  five — always SELF_BOOST, never scaled. (Live activations run roughly
  -2.7 .. +3.2, so 0.5 is meaningful without being dominant, on the same
  scale as FAMILIARITY_BOOST.)
- Three self-referential signals: the engine's self-relevance stamp, the
  'identity' concept, and a whole-word case-insensitive display-name
  mention. A None display name disables only the name-mention signal —
  never a silent match.
"""
from __future__ import annotations

import re

# Additive activation nudge for self-referential records. Flat: any
# combination of signals yields exactly this, never more.
SELF_BOOST = 0.5

# The frozen engine's own self-relevance stamp, verbatim from
# digital_subject/engine.py _own_event:
#   f"This event concerns me: {event.description}"
# minted exactly when the event kind has no EVENT_RULES entry
# (event.target plays no role in meaning selection).
OWN_EVENT_PREFIX = "This event concerns me: "


def self_boost_for(record, display_name) -> float:
    """Flat retrieval nudge for a self-referential record.

    Returns SELF_BOOST when ANY of the three self-referential signals
    fires, else 0.0. Reads ``record.first_person`` and ``record.concepts``
    only — no sidecar, no state, no writes, no clock, no RNG.
    ``display_name`` None-safe: a None name only disables the name-mention
    signal.
    """
    text = record.first_person or ""
    if text.startswith(OWN_EVENT_PREFIX):
        return SELF_BOOST
    if "identity" in (record.concepts or ()):
        return SELF_BOOST
    if display_name:
        name = display_name.strip()
        # Whole-word intent via lookarounds: equivalent to \b for
        # word-char names, but still fires for names edged in punctuation
        # (\b can never hold adjacent to a non-word char — dead branch).
        if name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)",
                              text, re.IGNORECASE):
            return SELF_BOOST
    return 0.0
