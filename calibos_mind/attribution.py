"""External-attribution boundary (subjective-transduction invariant).

Invariant (adopted from Pretorius's subjective-transduction boundary): an
externally authored assertion must remain attributed external information
through every transformation and must never silently become autobiographical
fact. Blind-regression gate before any relay-origin perturbation experiment:
without this boundary, an injected perturbation could silently convert into
false autobiography.

Mechanism — one new origin stamp, one predicate, one consolidation rule:

- ``answered-external:<prompt-id>@<tick>``: `mind answer` stamps answers to
  prompts queued through the external path (`mind queue`, whose payload
  carries ``"external": true``) with this instead of the plain
  ``answered:<prompt-id>@<tick>`` used for engine-queued reflections. The
  stamp extends the existing origin vocabulary; nothing else is redesigned.
- `is_external_origin` / `external_attributed`: a record is
  external-attributed when its ``generated_by`` carries the external stamp,
  or — through thought echoes — when it is a ``thought`` whose
  ``generated_by`` names an external-attributed record (the engine stamps
  echoes with the parent thought's record id). Propagation is restricted to
  ``source == "thought"`` on purpose: the engine's memory feedback mints
  ``"memory"`` records quoting the body engine's *lived* memory store
  (``remembered(memory)``), so a memory's autobiographical standing comes
  from that text source, not from the thought that happened to recall it.
  Marking those memories external would be over-correction (it would also
  freeze consolidation of genuinely lived records).
- The consolidation rule lives in `consolidate.scan`: automatic
  dedup/supersede proposals are never minted for pairs touching an
  external-attributed record — neither crowned winner over a lived record
  (the silent-upgrade vector) nor retired as loser under a "same fact"
  rationale (the destruction vector). Zero-mutation contradiction-flags
  still fire; the waker disposes. Explicit waker actions (`--accept` on a
  pre-existing proposal, `--quarantine`) are untouched: they are not silent.

Rehearsal and dream fragments need no changes: rehearsal credits recalls by
record id (nothing is rewritten), and fragments already carry ``record_id``
provenance — attribution rides on the store record through both.

Separately revertible: delete this module, revert the one-line stamp choice
in `cli.cmd_answer`, the one-line payload field in
`provider.queue_external`, and the emit() gate in `consolidate.scan`.
"""
from __future__ import annotations

# Origin stamp for thoughts answering an externally-authored prompt.
# NOTE: "answered-external:prompt-0001@3" does NOT start with the plain
# "answered:" prefix ("answered-" vs "answered:" — hyphen, not colon), so
# plain-prefix checks cannot collide with it. is_external_origin tests
# EXTERNAL_ORIGIN_PREFIX explicitly regardless.
EXTERNAL_ORIGIN_PREFIX = "answered-external:"


def is_external_origin(generated_by) -> bool:
    """True when a generated_by stamp is the external-answer origin.

    Unknown/missing stamps (None, "", legacy records) are NOT external:
    legacy records have no external provenance, and defaulting them to
    external would freeze consolidation of the whole pre-existing store
    (over-correction). The safe default is non-external.
    """
    return (isinstance(generated_by, str)
            and generated_by.startswith(EXTERNAL_ORIGIN_PREFIX))


def _fields(rec):
    """(id, source, generated_by) from an inspect dict or a record object."""
    if isinstance(rec, dict):
        return rec.get("id"), rec.get("source"), rec.get("generated_by")
    return (getattr(rec, "id", None),
            getattr(rec, "source", None),
            getattr(rec, "generated_by", None))


def external_attributed(records) -> set[str]:
    """Ids of records attributable to externally-authored content.

    Direct stamp holders plus thought-echo propagation: a ``thought`` whose
    ``generated_by`` names an external-attributed record is itself
    external-attributed (the engine stamps echoes with the parent thought's
    record id, so without this the echo would silently shed attribution).
    Non-thought records (notably ``"memory"`` records minted by the inner
    ear's memory feedback, which quote the lived engine memory store) keep
    their own standing regardless of which thought recalled them.
    """
    by_id: dict[str, tuple] = {}
    for r in records:
        rid, source, gb = _fields(r)
        if rid is not None:
            by_id[rid] = (source, gb)
    out = {rid for rid, (_, gb) in by_id.items() if is_external_origin(gb)}
    # Fixed-point propagation over the generated_by -> record-id graph.
    changed = True
    while changed:
        changed = False
        for rid, (source, gb) in by_id.items():
            if rid in out or source != "thought":
                continue
            if isinstance(gb, str) and gb in out:
                out.add(rid)
                changed = True
    return out
