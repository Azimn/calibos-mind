"""Zeigarnik unresolved-link detection — the ONE shared helper.

All three salience call sites (workspace.view, cli status, drift ratio) use
`has_unresolved_links` here. The +1.0 UNRESOLVED_BOOST must mean "linked to
something still open", not "was ever linked" (records are frozen; the engine
never clears link fields at record creation time).

ENGINE-OPENNESS FINDING (read 2026-09-24 from the frozen engine install;
round-2 correction: the round-1 docstring wrongly claimed concerns have no
open/closed distinction — they do):
- Commitments (digital_subject/continuity.py): `status` field; open =
  {"open", "overdue"}; closed = {"kept", "broken"}. `resolve_commitment()`
  writes "kept"/"broken". The engine has NO "released" status:
  calibos-mind's `mind resolve --released` passes kept=False, so the engine
  stores "broken" (the CLI only prints it as "released").
- Expectations (digital_subject/continuity.py): `status` field; open =
  {"pending", "expired"}; closed = {"confirmed", "violated"} — the same
  split `EndogenousSubject._open_records` uses
  (jelly_psiduck/endogenous.py).
- Concerns (digital_subject/models.py: Concern has NO status field, but the
  engine's own liveness rule is the open/closed distinction:
  jelly_psiduck/endogenous.py:155 admits a residual concern as a cognition
  candidate only if `concern.description in state.unresolved` — the last-24
  description window (digital_subject/engine.py:88,248; models.py:231).
  A present concern whose description left that window is stale: the engine
  no longer surfaces it. `prospective:`-prefixed concerns are exempt from
  the gate — the engine sweeps them when their ledger root closes
  (endogenous.py:141-143), so presence ~= open for them.
  Removal paths (round-1 docstring omitted one): the `prospective:` sweep,
  AND digital_subject/engine.py:184-192 (`_decay_private_state` drops
  concerns with urgency < 0.02).

Link key shapes actually written by the engine:
- runtime.py:257 (base _project_temporal): concern_links=(commitment.id,)
- endogenous.py:135 (_project_temporal):   concern_links=("commitment:"+id /
                                          "expectation:"+id,)
- runtime.py:314 (base _hear):            concern_links=(influence.concern_key,)
                                          e.g. "fear" — written WITHOUT any
                                          liveness gate
- endogenous.py:200 (_warrants_cognition): concern_links=("concern:"+key,)
                                          for residual concerns — written
                                          ONLY for concerns passing the
                                          unresolved gate above

The open set therefore unions, per concern: the raw key (presence — the
`_hear` shape is ungated, and raw keys are the only handle on stale-vs-open
distinction below) and, only when the engine's own liveness rule holds
(`prospective:` or description in the unresolved window), the
`"concern:"+key` shape. Per open commitment/expectation: the raw id and the
`"commitment:"/"expectation:"`-prefixed id.

`has_unresolved_links` is shape-aware because a bare link key is ambiguous:
a bare key counts only through its namespaced sibling ("commitment:"+k /
"expectation:"+k / "concern:"+k) — that sibling is where liveness actually
lives. So a record linked via raw "fear" to a stale concern gets no boost
(the raw key sits in the set by presence, but its "concern:" shape is
absent), while the same link to an open concern keeps it. Namespaced links
("concern:fear", "commitment:<id>", "prospective:...") are checked by plain
membership.
"""
from __future__ import annotations

# Open = still live per the engine's own status fields (see module docstring).
OPEN_COMMITMENT_STATUSES = frozenset({"open", "overdue"})
OPEN_EXPECTATION_STATUSES = frozenset({"pending", "expired"})

PROSPECTIVE_PREFIX = "prospective:"
CONCERN_PREFIX = "concern:"


def _concern_is_open(key: str, description: str, unresolved: set[str]) -> bool:
    """The engine's own residual-concern liveness rule.

    `prospective:` concerns are open while present (the engine sweeps them
    when their ledger root closes); all others are open iff their
    description is still in the last-24 unresolved window
    (jelly_psiduck/endogenous.py:155).
    """
    return key.startswith(PROSPECTIVE_PREFIX) or description in unresolved


def _collect(concerns, commitments, expectations, unresolved_descs) -> set[str]:
    """Core open-set builder.

    concerns: iterable of (key, description) pairs for present concerns.
    commitments / expectations: iterables of (id, status) pairs.
    unresolved_descs: the engine's last-24 unresolved description window.
    """
    unresolved = set(unresolved_descs or ())
    open_keys: set[str] = set()
    for key, description in concerns or ():
        open_keys.add(key)  # presence: the raw `_hear` shape is ungated
        if _concern_is_open(key, description, unresolved):
            open_keys.add(CONCERN_PREFIX + key)
    for cid, status in commitments or ():
        if status in OPEN_COMMITMENT_STATUSES:
            open_keys.add(cid)
            open_keys.add("commitment:" + cid)
    for eid, status in expectations or ():
        if status in OPEN_EXPECTATION_STATUSES:
            open_keys.add(eid)
            open_keys.add("expectation:" + eid)
    return open_keys


def _payload_concerns(engine: dict):
    for dict_key, c in (engine.get("concerns") or {}).items():
        c = c or {}
        yield c.get("key", dict_key), c.get("description")


def open_link_keys(state: dict) -> set[str]:
    """Currently-unresolved link keys from an inspect() payload dict.

    Reads state["engine"]["concerns"] (dict keyed by concern key, values
    carrying "key"/"description"), state["engine"]["unresolved"] (the
    last-24 description window), and
    state["continuity"]["commitments"/"expectations"] (dicts keyed by id,
    values carrying "status"). Tolerates missing sections; a missing
    unresolved window means non-prospective concerns get no boost (a missed
    boost is the safe direction).
    """
    engine = state.get("engine") or {}
    continuity = state.get("continuity") or {}
    commitments = continuity.get("commitments") or {}
    expectations = continuity.get("expectations") or {}
    return _collect(
        _payload_concerns(engine),
        [(cid, (c or {}).get("status")) for cid, c in commitments.items()],
        [(eid, (e or {}).get("status")) for eid, e in expectations.items()],
        engine.get("unresolved"),
    )


def live_open_keys(engine_state, continuity_state) -> set[str]:
    """Currently-unresolved link keys from live engine/continuity state objects.

    Reads in-memory dataclasses only — no SQLite, no writes — so it is safe
    to call mid-tick from workspace.view(). Mirrors open_link_keys exactly.
    """
    return _collect(
        [(c.key, c.description)
         for c in getattr(engine_state, "concerns", {}).values()],
        [(c.id, c.status) for c in
         getattr(continuity_state, "commitments", {}).values()],
        [(e.id, e.status) for e in
         getattr(continuity_state, "expectations", {}).values()],
        getattr(engine_state, "unresolved", ()),
    )


def has_unresolved_links(concern_links, expectation_links, open_keys) -> bool:
    """True iff a record's links intersect the currently-open set.

    THE shared helper: every salience call site computes its `unresolved`
    flag through this function. Shape-aware: a bare (unprefixed) link key
    counts only through its namespaced sibling — "commitment:"+k,
    "expectation:"+k, or "concern:"+k — because that is where liveness
    lives. In particular a bare concern key whose "concern:" shape is
    absent is stale by the engine's own rule (its description left the
    unresolved window) even though the raw key is present in the set.
    Namespaced links ("concern:fear", "commitment:<id>", "prospective:...")
    are checked by plain membership.
    open_keys=None (no state available, e.g. plain engine use) falls back
    to the legacy any-link check explicitly — the caller cannot do better
    without state.
    """
    links = tuple(concern_links or ()) + tuple(expectation_links or ())
    if not links:
        return False
    if open_keys is None:
        return True  # legacy: no open set to intersect against
    for k in links:
        if not k:
            continue
        if ":" in k:
            if k in open_keys:
                return True
        elif (("commitment:" + k) in open_keys
                or ("expectation:" + k) in open_keys
                or ("concern:" + k) in open_keys):
            return True
    return False
