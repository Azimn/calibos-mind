"""Inbox-expectation registration: wiring unfinished business into the engine.

Domain 2 (Spontaneous thought) adversarial finding, 2026-09-26: the frozen
engine's unfinished-business machinery is complete end-to-end
(`_open_records` -> `_project_temporal` escalating temporal records ->
`_warrants_cognition` admitting `unresolved_concern` -> "This unfinished
matter returns to my attention"), but it had never fired in 100 live ticks:
the store held zero open commitments, zero expectations, zero concerns. No
code path in calibos_mind ever *registers* an Expectation — the creation
channel was missing. This module is the wiring, not theater: genuinely
pending human-initiated business (prompts queued to the inbox and left
unanswered past a tick TTL) becomes frozen-engine Expectation records, and
the engine's own temporal machinery does the resurfacing with its own
activation math. Nothing is installed — no persistent questions as theater,
no fake intrusive memories, no invented proposition content (propositions
carry only the prompt id and its queue tick).

Fail-closed rules (regression-genome discipline):
- Prompts without `view_tick` (hand-written/legacy) are SKIPPED. Tick age is
  never guessed from wall clock or file mtime.
- Corrupt/unreadable prompt files are skipped, never guessed.
- `sync` on an empty inbox with no `inbox:` expectations opens no
  transaction and touches nothing — a verifiable no-op.
- `resolve` on an absent or already-closed expectation is a no-op (no
  transaction opened).
- `sync` is for waking ticks only. The dream path (`dream_tick`) never calls
  it; dream/conduct isolation is unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

#: Ticks a queued prompt may sit unanswered before it counts as unfinished
#: business. Small enough to matter, large enough not to nag about a prompt
#: queued one tick ago.
TTL_TICKS = 3

#: Confidence for a registered inbox expectation. It is an obligation we
#: imposed on ourselves ("I owe an answer"), not a prediction — 0.6 keeps the
#: engine's urgency math honest without inflating it.
CONFIDENCE = 0.6

#: Prefix for expectation ids minted here. One prompt, one expectation.
ID_PREFIX = "inbox:"


def expectation_id(pid: str) -> str:
    """The continuity id for a prompt: ``inbox:<pid>``."""
    return f"{ID_PREFIX}{pid}"


def _read_payload(path: Path) -> dict | None:
    """Parse a prompt file; None when unreadable (fail closed)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _prompt_pid(payload: dict, path: Path) -> str:
    pid = payload.get("id")
    return str(pid) if pid else path.stem


def sync(subject, inbox_dir, ttl_ticks: int = TTL_TICKS) -> dict:
    """Register stale pending inbox prompts as Expectation records.

    For each ``prompt-*.json`` with a ``view_tick`` and
    ``store tick - view_tick >= ttl_ticks`` that has no expectation yet,
    registers ``Expectation(id="inbox:<pid>", proposition=..., created_tick=
    view_tick, due_tick=view_tick+ttl_ticks, confidence=0.6, status="pending")``
    via the engine's own ``create_expectation``. Prompts whose file vanished
    while their expectation is still pending (consumed but never answered —
    the only file-deleting path is ``InboxCognition.consume``) are marked
    ``"expired"`` with the engine's own expiry bookkeeping
    (``resolved_tick``, ``prediction_error``) — honest: we expected to
    answer, we have no evidence we did. ``expired`` stays in the engine's
    open set (``pending | expired``), so the nagging continues.

    Idempotent: one prompt, one expectation; re-running changes nothing.
    Returns ``{"registered": [...], "expired": [...]}`` — explicit lists,
    never a collapsed zero.

    The only store write is a single transaction, opened only when there is
    something to register or expire. Empty inbox with no ``inbox:``
    expectations -> no transaction, expectations dict untouched.
    """
    inbox = Path(inbox_dir)
    state = subject.continuity.state
    expectations = state.expectations

    prompt_paths = sorted(inbox.glob("prompt-*.json"))
    live_pids: set[str] = set()
    stale: list[tuple[str, str, int]] = []  # (expectation id, pid, view_tick)

    if prompt_paths:
        now = subject.engine.state.tick
        for path in prompt_paths:
            payload = _read_payload(path)
            if payload is None:
                continue
            pid = _prompt_pid(payload, path)
            live_pids.add(pid)
            live_pids.add(path.stem)
            view_tick = payload.get("view_tick")
            # Fail closed: no view_tick (hand-written/legacy) or a
            # non-integer one means the prompt's tick age is unknowable —
            # never guess it from wall clock or mtime.
            if not isinstance(view_tick, int) or isinstance(view_tick, bool):
                continue
            if now - view_tick >= ttl_ticks:
                eid = expectation_id(pid)
                if eid not in expectations:
                    stale.append((eid, pid, view_tick))
    elif not any(eid.startswith(ID_PREFIX) for eid in expectations):
        # Empty inbox, nothing of ours to reconcile: provable no-op. No
        # transaction is opened, so no write path is touched at all.
        return {"registered": [], "expired": []}

    vanished = [
        eid for eid, item in expectations.items()
        if eid.startswith(ID_PREFIX)
        and item.status == "pending"
        and eid[len(ID_PREFIX):] not in live_pids
    ]
    if not stale and not vanished:
        return {"registered": [], "expired": []}

    with subject._transaction():
        now = subject.engine.state.tick
        registered: list[str] = []
        for eid, pid, view_tick in stale:
            # Re-check inside the transaction: one prompt, one expectation,
            # even if the store moved between the scan and the write.
            if eid in subject.continuity.state.expectations:
                continue
            subject.continuity.create_expectation(
                f"I still owe an answer to the question queued at tick "
                f"{view_tick} ({pid})",
                tick=view_tick,
                due_tick=view_tick + ttl_ticks,
                confidence=CONFIDENCE,
                evidence_ids=(),
                expectation_id=eid,
            )
            registered.append(eid)
        expired: list[str] = []
        for eid in vanished:
            item = subject.continuity.state.expectations.get(eid)
            if item is not None and item.status == "pending":
                # The prompt is gone and was never answered. Mirror the
                # engine's own advance_deadlines expiry bookkeeping; the
                # status stays in _open_records' open set.
                item.status = "expired"
                item.resolved_tick = now
                item.prediction_error = item.confidence
                expired.append(eid)
    return {"registered": registered, "expired": expired}


def resolve(subject, pid: str, *, outcome: str):
    """Close the ``inbox:<pid>`` expectation as confirmed.

    Called after a prompt is genuinely dealt with — ``outcome="answered"``
    after a successful ``inject_thought``, ``outcome="let-pass"`` on the
    ``--silent`` path, ``outcome="refused-stale-view"`` when ``cmd_answer``
    consumed the prompt but refused the answer (stale view, or an
    inject-time validation failure). Transitions ``pending`` *or*
    ``expired`` to ``"confirmed"`` (settling closes the matter even after
    the due date); already-closed expectations and absent ones are a no-op
    with no transaction opened. Returns the expectation item, or None when
    there is nothing to resolve.

    These are obligations, not predictions, so no ``resolve_expectation``
    insight ("My expectation was supported: ...") is written — that prose
    would misdescribe a settled debt as a confirmed forecast. The status,
    outcome, and resolved tick are the honest audit trail.
    """
    eid = expectation_id(pid)
    item = subject.continuity.state.expectations.get(eid)
    if item is None or item.status in {"confirmed", "violated"}:
        return None
    with subject._transaction():
        item = subject.continuity.state.expectations.get(eid)
        if item is None or item.status in {"confirmed", "violated"}:
            return None
        now = subject.engine.state.tick
        item.status = "confirmed"
        item.outcome = str(outcome)
        item.prediction_error = max(0.0, min(1.0, 1.0 - item.confidence))
        item.resolved_tick = now
        return item
