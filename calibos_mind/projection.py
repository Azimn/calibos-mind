"""Overlay on the engine's phenomenological projection (jelly_psiduck.firewall).

The snapshot engine's `remembered()` renders a recall as
``f"{memory.summary} {memory.meaning}"``. When the meaning was derived from
the summary and adds nothing (the common case), the sentence renders twice
in the view, e.g.::

    I vaguely remember this event: I follow a detail that was not
    necessary. I follow a detail that was not necessary.

The doubled text is stored in workspace records, so it crowds every view
and prompt until it ages out.

The engine lives in the snapshot venv (untracked, rebuildable, deliberately
immune to repo branch moves), so it is not edited. Instead this module
patches `jelly_psiduck.firewall.remembered` at import time. It is imported by
`calibos_mind/__init__.py` on package load, before any engine consumer is
imported (all engine imports in this package are lazy), so the
`from .firewall import remembered` bindings in endogenous.py and runtime.py
pick up the fixed version.

Fix-forward only: workspace records already written with doubled text are
left as they are; nothing in the store is rewritten.
"""
from __future__ import annotations

import jelly_psiduck.firewall as _firewall


def remembered(memory) -> str:
    # Strength is a retrieval-quality heuristic, not a calibrated truth
    # probability. Unchanged from the engine.
    if memory.strength < 0.2:
        return "Something about this feels familiar, but I cannot recover the details."
    prefix = (
        "I vaguely remember this event:"
        if memory.strength < 0.5
        else "I remember this event:"
    )
    summary = (memory.summary or "").strip()
    meaning = (memory.meaning or "").strip()
    if not meaning or meaning == summary or summary in meaning or meaning in summary:
        # The meaning adds nothing the summary doesn't already say.
        body = summary or meaning
    else:
        body = f"{summary} {meaning}"
    return f"{prefix} {body}"


_firewall.remembered = remembered
