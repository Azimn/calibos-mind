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
- Corrupt/unreadable prompt files are skipped, never guessed — and while
  any unreadable ``prompt-*.json`` file is present, the expiry pass is
  deferred entirely (no expectation marked expired, streak untouched), so
  ambiguity never expires anything. Registration of readable stale prompts
  is unaffected.
- `sync` on an empty inbox with no `inbox:` expectations opens no
  transaction and touches nothing — a verifiable no-op. The policy sidecar
  is only ever *read* on that path, never written.
- `resolve` on an absent or already-closed expectation is a no-op (no
  transaction opened, no sidecar touched).
- `sync` is for waking ticks only. The dream path (`dream_tick`) never calls
  it; dream/conduct isolation is unchanged.
- The `expectation_policy.json` sidecar is written ONLY when the streak
  actually changes, or when an expectation-closure note is minted (the
  `closed_expectations` guard mark, 2026-10-13). Expiry increments it; a
  genuine answer decrements it; let-pass / refused-stale-view / no-op paths
  never write it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

#: Ticks a queued prompt may sit unanswered before it counts as unfinished
#: business. Small enough to matter, large enough not to nag about a prompt
#: queued one tick ago.
TTL_TICKS = 3

#: Ticks after an expectation expires before an unclaimable debt is allowed
#: to lapse (2026-10-01). An expired ``inbox:`` expectation stays in the
#: engine's open set so the guilt fires — but if the prompt file is gone
#: and no live prompt claims the debt in its ``supersedes`` list, the
#: matter can never be settled by answering. The guilt still gets its full
#: hearing: 12 ticks matches the frozen engine's level-3 temporal grade
#: ("The wait is becoming prolonged", age > 12 past due), so the nag runs
#: one complete prolonged cycle before the debt is released. After that the
#: expectation is confirmed with outcome "lapsed" — the opportunity died
#: with its object, and a debt that can never be settled should not nag
#: forever. Neutral on the expiry streak: the failure was already counted
#: when the expectation expired.
GRIEF_TICKS = 12

#: Confidence for a registered inbox expectation. It is an obligation we
#: imposed on ourselves ("I owe an answer"), not a prediction — 0.6 keeps the
#: engine's urgency math honest without inflating it.
CONFIDENCE = 0.6

#: Base confidence for a freshly registered inbox expectation (no failures
#: on the record). Identical to CONFIDENCE; named separately so the decay
#: policy reads as a policy, not a magic formula.
BASE_CONFIDENCE = CONFIDENCE

#: Multiplicative confidence decay per consecutive expiry. 0.8: gentle
#: enough that one or two missed prompts barely move the nag, steep enough
#: that a long streak of failures is genuinely heard as a track record.
DECAY_FACTOR = 0.8

#: Confidence floor. The urgency formula's .2*attachment + .1*uncertainty
#: terms keep the nag nonzero at any confidence, so the floor can be low
#: without silencing the honest-nagging invariant.
CONFIDENCE_FLOOR = 0.15

#: Name of the local-only policy sidecar, kept at the mind root next to
#: `inbox/` (private runtime state, never backed up).
POLICY_FILE_NAME = "expectation_policy.json"

#: Sidecar JSON key holding the consecutive-expiry streak.
EXPIRY_STREAK_KEY = "expiry_streak"

#: Sidecar JSON key holding the ids of expectations that already received
#: their expectation-closure note (2026-10-13, domain 18). One note per
#: expectation, ever: the guard survives across syncs and processes, so a
#: kill between the mint transaction's commit and the guard write cannot
#: double-mint (the idempotent mint below is the second line of defense).
CLOSED_EXPECTATIONS_KEY = "closed_expectations"

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


def _policy_path(inbox_dir) -> Path:
    """Mind-root policy sidecar: ``Path(inbox_dir).parent / "expectation_policy.json"``."""
    return Path(inbox_dir).parent / POLICY_FILE_NAME


def _read_policy(policy_path: Path) -> dict:
    """Read the policy sidecar as a dict; {} for missing/unreadable/malformed.

    Fail-closed like the old per-key readers: a broken sidecar degrades to
    defaults (streak 0, no guard marks) rather than raising. Reading never
    creates or modifies the file (the empty-inbox no-op invariant)."""
    try:
        raw = policy_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_streak(policy_path: Path) -> int:
    """Read the expiry streak; 0 for a missing, malformed, or negative one.

    A broken sidecar degrades to the base confidence, never to a failed
    tick. Reading never creates or modifies the file (the empty-inbox no-op
    invariant)."""
    n = _read_policy(policy_path).get(EXPIRY_STREAK_KEY, 0)
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        return 0
    return n


def _read_closed(policy_path: Path) -> set[str]:
    """Ids already closure-noted; empty for a missing/malformed sidecar.

    Lenient like ``_read_streak`` (this module's fail-closed discipline,
    not the habits sidecar's fail-loud one): a malformed closed list
    degrades to empty rather than raising. The mint path below is
    idempotent on the expectation id, so a lost guard mark degrades to a
    records-scan recheck, never to a double mint.
    """
    raw = _read_policy(policy_path).get(CLOSED_EXPECTATIONS_KEY)
    if not isinstance(raw, list):
        return set()
    return {e for e in raw if isinstance(e, str) and e}


def _write_policy(policy_path: Path, data: dict) -> None:
    """Rewrite the policy sidecar with sorted keys and a sorted guard list.

    The single writer for all sidecar fields, so a streak write can never
    clobber the closure guard marks (or vice versa). Only keys already
    present in ``data`` are written — no field is forced into existence.
    Sorted output keeps the file byte-deterministic on identical content.
    """
    policy_path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1),
        encoding="utf-8")


def _write_streak(policy_path: Path, streak: int) -> None:
    """Persist the streak, preserving the other sidecar fields.

    Reads the current sidecar and rewrites it with the streak updated, so
    the closure-note guard marks survive a streak write. Only called when
    the streak actually changed."""
    data = _read_policy(policy_path)
    data[EXPIRY_STREAK_KEY] = streak
    _write_policy(policy_path, data)


def _add_closed(policy_path: Path, eids: list[str]) -> None:
    """Merge newly closure-noted ids into the guard set.

    Writes only when the set grows. Called after the mint transaction
    commits: a kill between the commit and this write leaves the guard
    short, but the confirmed status of the item (same transaction as the
    mint) means no later sync can re-enter the lapse path for it, and the
    idempotent mint skips the record anyway.
    """
    eids = [e for e in eids if e]
    if not eids:
        return
    data = _read_policy(policy_path)
    closed = _read_closed(policy_path)
    if all(e in closed for e in eids):
        return
    closed.update(eids)
    data[CLOSED_EXPECTATIONS_KEY] = sorted(closed)
    _write_policy(policy_path, data)


def _confidence_for(streak: int) -> float:
    """Registration confidence for streak N: ``max(0.6 * 0.8**N, 0.15)``."""
    return max(BASE_CONFIDENCE * DECAY_FACTOR ** streak, CONFIDENCE_FLOOR)


def _streak_inbox_dir(subject, explicit=None):
    """Where the policy sidecar lives for `subject`'s inbox.

    An explicit dir wins; otherwise the subject's own cognition provider
    (InboxCognition carries `.inbox`). Providers without an inbox (dream,
    scripted) have no streak to learn — returns None and the caller skips
    the sidecar entirely.
    """
    if explicit is not None:
        return Path(explicit)
    inbox = getattr(getattr(subject, "cognition", None), "inbox", None)
    return Path(inbox) if inbox is not None else None


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
    open set (``pending | expired``), so the nagging continues. If any
    ``prompt-*.json`` file is present but unreadable, the expiry pass is
    deferred entirely — no expectation is marked expired and the streak is
    untouched — until the file is deleted or repaired; registration of
    readable stale prompts is unaffected. No expiry decisions on ambiguous
    evidence.

    Idempotent: one prompt, one expectation; re-running changes nothing.
    Returns ``{"registered": [...], "expired": [...], "superseded": [...],
    "lapsed": [...]}`` — explicit lists, never a collapsed zero.

    Lapse (2026-10-01): an ``expired`` ``inbox:`` expectation whose prompt
    is gone and which no live prompt claims in its ``supersedes`` list is
    an unsettlable debt — the opportunity to answer died with its object.
    It still nags for ``GRIEF_TICKS`` after expiry (the guilt gets its full
    hearing, one complete prolonged cycle), then sync confirms it with
    outcome ``"lapsed"`` so the engine's open set stops resurfacing it.
    A debt claimed by a live prompt in ``"supersedes"`` is confirmed
    ``"superseded"`` instead (the consideration evidence exists even
    though the expiry came first). Expectations whose prompt is still
    live never lapse. Deferred wholesale while any prompt file is
    present but unreadable (fail closed), and never touches the streak.

    Supersession settlement (2026-09-28): a prompt file replaced by the
    provider's queue-time supersede (the newcomer names it in "supersedes")
    whose expectation is still pending is confirmed with outcome
    "superseded" — considered and closed by a newer view, not abandoned —
    and never touches the expiry streak.

    Expectation-closure note (2026-10-13, domain 18): when the confirm-
    lapsed branch confirms an expectation whose confidence is at the
    floor (0.15 — sustained neglect, streak >= 7), it mints exactly one
    temporal record noting the plan's death (expectation id, proposition
    summary, created/due ticks, final streak and confidence, outcome
    "lapsed") — the unfulfilled plan becomes a retrievable first-person
    memory. Lapses above the floor confirm silently. A
    ``closed_expectations`` set in the policy sidecar guards one-note-per-
    expectation across syncs; the mint is also idempotent on the
    expectation id against the in-memory records. Mint path is this
    branch only: read-only commands, dream ticks, and ``resolve`` never
    mint.

    Confidence policy (2026-09-27): a stale prompt registers at
    ``max(0.6 * 0.8**N, 0.15)`` where N is the consecutive-expiry streak in
    the ``expectation_policy.json`` sidecar. Each expectation marked
    ``"expired"`` increments the streak (persisted once, only when it
    changes); a genuine answer decrements it via ``resolve``. The rest of
    the registered fields are unchanged.

    The only store write is a single transaction, opened only when there is
    something to register or expire. Empty inbox with no ``inbox:``
    expectations -> no transaction, expectations dict untouched, sidecar
    read but never written.
    """
    inbox = Path(inbox_dir)
    policy_path = _policy_path(inbox)
    streak = _read_streak(policy_path)
    closed = _read_closed(policy_path)
    state = subject.continuity.state
    expectations = state.expectations

    prompt_paths = sorted(inbox.glob("prompt-*.json"))
    live_pids: set[str] = set()
    stale: list[tuple[str, str, int]] = []  # (expectation id, pid, view_tick)
    unreadable_present = False

    if prompt_paths:
        now = subject.engine.state.tick
        for path in prompt_paths:
            payload = _read_payload(path)
            if payload is None:
                # Fail closed: a present-but-unreadable file is skipped, never
                # guessed — and while ANY unreadable prompt-*.json file is
                # present, the expiry pass is deferred entirely (critic round
                # 3, 2026-09-27). For a corrupt-but-present file we know the
                # file stem but cannot know the payload pid (payload "id"
                # may differ from the file stem — the real queue writer
                # always sets them equal, but hand-written files diverge and
                # the code acknowledges them via the dual live_pids add
                # below), so we cannot verify that any pending expectation's
                # prompt has "vanished without settlement". Ambiguous
                # evidence never expires anything: pending expectations keep
                # resurfacing honestly, the streak is untouched, and once
                # the corrupt file is deleted or repaired the next sync
                # expires normally. Registration of readable stale prompts
                # proceeds regardless.
                unreadable_present = True
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
        return {"registered": [], "expired": [], "superseded": [],
                "lapsed": []}

    vanished: list[str] = []
    if not unreadable_present:
        # No expiry decisions on ambiguous evidence: while any prompt file
        # is present but unreadable, we cannot distinguish "vanished without
        # settlement" from "corrupt-but-present", so the expiry pass is
        # deferred wholesale (critic round 3, 2026-09-27). Registration of
        # readable stale prompts is unaffected.
        vanished = [
            eid for eid, item in expectations.items()
            if eid.startswith(ID_PREFIX)
            and item.status == "pending"
            and eid[len(ID_PREFIX):] not in live_pids
        ]
    # Supersession settlement (2026-09-28): InboxCognition.think() replaces
    # the standing engine prompt instead of piling up stillborn ones, and
    # records the replaced ids in the newcomer's "supersedes" list. A
    # replaced prompt whose expectation was still pending was considered
    # and closed by a newer view — not abandoned. Confirm it with outcome
    # "superseded" (neutral on the expiry streak, like refused-stale-view)
    # instead of marking it expired. Only readable payloads count; an
    # unreadable file never identifies a supersession (fail closed).
    superseded_eids: set[str] = set()
    if not unreadable_present:
        for path in prompt_paths:
            payload = _read_payload(path)
            if payload is None:
                continue
            for sp in payload.get("supersedes") or []:
                superseded_eids.add(expectation_id(str(sp)))
    vanished_superseded = [eid for eid in vanished if eid in superseded_eids]
    vanished_expired = [eid for eid in vanished if eid not in superseded_eids]
    # Lapse (2026-10-01): an expectation already "expired" whose prompt is
    # gone and which no live prompt claims in its "supersedes" list is an
    # unsettlable debt — answering it is structurally impossible. It has
    # had its grief window (GRIEF_TICKS past resolved_tick): confirm it
    # with outcome "lapsed" so the open set stops resurfacing it. If a
    # live prompt DOES claim it in "supersedes", the consideration
    # evidence exists even though the expiry came first (the engine can
    # expire a debt past due while its file is still standing, and the
    # supersede arrives a tick later): confirm it "superseded" instead.
    # A prompt that is still live never lapses. Like expiry, deferred
    # wholesale on ambiguous evidence (fail closed).
    lapsed: list[str] = []
    late_superseded: list[str] = []
    if not unreadable_present:
        scan_tick = subject.engine.state.tick
        for eid, item in expectations.items():
            if not eid.startswith(ID_PREFIX):
                continue
            if item.status != "expired":
                continue
            if eid[len(ID_PREFIX):] in live_pids:
                continue
            resolved = getattr(item, "resolved_tick", None)
            # Fail closed: no integer resolved tick means the expiry age is
            # unknowable — never guess it.
            if (not isinstance(resolved, int)
                    or isinstance(resolved, bool)):
                continue
            if scan_tick - resolved < GRIEF_TICKS:
                continue
            if eid in superseded_eids:
                late_superseded.append(eid)
            else:
                lapsed.append(eid)
    if not stale and not vanished and not lapsed and not late_superseded:
        return {"registered": [], "expired": [], "superseded": [],
                "lapsed": []}

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
                confidence=_confidence_for(streak),
                evidence_ids=(),
                expectation_id=eid,
            )
            registered.append(eid)
        expired: list[str] = []
        for eid in vanished_expired:
            item = subject.continuity.state.expectations.get(eid)
            if item is not None and item.status == "pending":
                # The prompt is gone and was never answered. Mirror the
                # engine's own advance_deadlines expiry bookkeeping; the
                # status stays in _open_records' open set.
                item.status = "expired"
                item.resolved_tick = now
                item.prediction_error = item.confidence
                expired.append(eid)
        superseded_closed: list[str] = []
        for eid in vanished_superseded:
            item = subject.continuity.state.expectations.get(eid)
            if item is not None and item.status == "pending":
                # Considered and closed by a newer view: confirm (settled),
                # mirroring resolve()'s confirmed shape — status, outcome,
                # and resolved tick are the honest audit trail. Neutral on
                # the expiry streak: a replacement is neither a failure nor
                # a genuine answer.
                item.status = "confirmed"
                item.outcome = "superseded"
                item.prediction_error = max(
                    0.0, min(1.0, 1.0 - item.confidence))
                item.resolved_tick = now
                superseded_closed.append(eid)
        lapsed_closed: list[str] = []
        # The consecutive-expiry streak as of the lapse: the sidecar value
        # at sync start plus this sync's own expiries, computed before the
        # loop so the note text is deterministic on identical sequences.
        # (A "final streak" reading documented in the 2026-10-13 CHANGELOG
        # entry: the global track record at death, not a per-expectation
        # counter — the expectation carries its own confidence.)
        final_streak = streak + len(expired)
        lapsed_noted: list[str] = []
        for eid in lapsed:
            item = subject.continuity.state.expectations.get(eid)
            if item is not None and item.status == "expired":
                # The debt outlived its object and has had its grief
                # window: confirm (settled) with outcome "lapsed", the
                # same confirmed shape as supersession — status, outcome,
                # and resolved tick are the honest audit trail. Neutral on
                # the streak: the expiry already counted the failure, and
                # lapse is a release, not a settlement. These are
                # obligations, not predictions, so no resolve_expectation
                # insight prose is written.
                item.status = "confirmed"
                item.outcome = "lapsed"
                item.prediction_error = max(
                    0.0, min(1.0, 1.0 - item.confidence))
                item.resolved_tick = now
                lapsed_closed.append(eid)
                # Expectation-closure note (2026-10-13, domain 18): a plan
                # that died after sustained neglect becomes a retrievable
                # first-person memory, so future cognition can prospect
                # over its own failures instead of only its pending
                # business. The floor gate keeps the signal rare: quick
                # misses lapse silently. One note per expectation, ever —
                # the closed_expectations guard (persisted in the policy
                # sidecar after the transaction commits) plus the
                # idempotent mint. Mint path is this confirm-lapsed branch
                # only: read-only commands and dream ticks never call
                # sync, so they can never mint.
                if (eid not in closed
                        and item.confidence <= CONFIDENCE_FLOOR):
                    mint_closure_note(subject, {
                        "expectation_id": eid,
                        "pid": eid[len(ID_PREFIX):],
                        "created_tick": item.created_tick,
                        "due_tick": item.due_tick,
                        "streak": final_streak,
                        "confidence": item.confidence,
                    })
                    lapsed_noted.append(eid)
        for eid in late_superseded:
            item = subject.continuity.state.expectations.get(eid)
            if item is not None and item.status == "expired":
                # Late consideration: the expiry came first (engine
                # expires past-due debts on its own), but a live prompt
                # claims the debt was considered and closed by a newer
                # view. The evidence of consideration exists, so the
                # honest outcome is "superseded", not "lapsed". Neutral
                # on the streak, like the pending-path settlement.
                item.status = "confirmed"
                item.outcome = "superseded"
                item.prediction_error = max(
                    0.0, min(1.0, 1.0 - item.confidence))
                item.resolved_tick = now
                superseded_closed.append(eid)
    if expired:
        # The streak counts failures: one per expectation that expired
        # unanswered in this sync. Written once, only when it changed.
        streak += len(expired)
        _write_streak(policy_path, streak)
    if lapsed_noted:
        # Persist the one-note-per-expectation guard. Written after the
        # mint transaction commits; the confirmed status (same
        # transaction) already bars any re-entry to the lapse path, so a
        # kill in between degrades to a harmless missing mark, never a
        # double note.
        _add_closed(policy_path, lapsed_noted)
    return {"registered": registered, "expired": expired,
            "superseded": superseded_closed, "lapsed": lapsed_closed}


def resolve(subject, pid: str, *, outcome: str, inbox_dir=None):
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

    Streak bookkeeping (2026-09-27): ``outcome="answered"`` on a real
    transition decrements the expiry streak (``N = max(0, N-1)``), persisted
    to the policy sidecar only when it actually changes — learning works
    both ways, and a streak already at 0 writes nothing. ``"let-pass"`` and
    ``"refused-stale-view"`` are neutral: a deliberate choice to disengage,
    and a blocked attempt, are neither failure nor success, so the streak
    is untouched. ``inbox_dir`` overrides the sidecar location; by default
    it is derived from the subject's own cognition provider.

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
        result = item
    if outcome == "answered":
        policy_dir = _streak_inbox_dir(subject, inbox_dir)
        if policy_dir is not None:
            policy_path = _policy_path(policy_dir)
            streak = _read_streak(policy_path)
            if streak > 0:
                _write_streak(policy_path, streak - 1)
    return result


# -- expectation-closure notes: canonical text, parsing, minting -----------
# (domain 18, 2026-10-13). Follows the stale-habit-note precedent
# (habits.py): the record text is the canonical serialization — the only
# writer is closure_note_text(), the only reader is the anchored
# fullmatch below, so the identifier cannot drift from the writer.

def closure_note_text(event: dict) -> str:
    """First-person text for one lapsed-at-floor expectation.

    The first sentence is the proposition summary (which question, queued
    when); the machine sentence carries everything the fitness function
    needs to recover the episode from the record alone: the expectation
    id, created/due ticks, the final consecutive-expiry streak, the final
    confidence in a fixed format, and the outcome.

    No contractions (the similarity vetoes tokenize on raw text —
    regression genome), no wall clock, rounded fixed-format floats:
    identical sequences mint byte-identical text.
    """
    return (
        f"I owed an answer to the question queued at tick "
        f"{event['created_tick']} ({event['pid']}); it went unanswered "
        f"through {event['streak']} straight expiries and now the debt "
        f"has lapsed. "
        f"That was expectation {event['expectation_id']} lapsing: "
        f"created tick {event['created_tick']}, due tick "
        f"{event['due_tick']}, final streak {event['streak']}, final "
        f"confidence {event['confidence']:.6f}, outcome lapsed."
    )


_CLOSURE_NOTE_RE = re.compile(
    r"I owed an answer to the question queued at tick (-?\d+) \((.+?)\); "
    r"it went unanswered through (\d+) straight expiries and now the "
    r"debt has lapsed\. "
    r"That was expectation (inbox:.+?) lapsing: created tick (-?\d+), due "
    r"tick (-?\d+), final streak (\d+), final confidence "
    r"([+-]?\d+\.\d{6}), outcome (lapsed)\.")


def parse_closure_note_text(text) -> dict | None:
    """Parse an expectation-closure note's canonical text, or None.

    Returns the episode identity (expectation_id) plus the rendered
    fields. None means "not a closure note" — never a partial match: the
    fullmatch anchors both ends, and the expectation id must name exactly
    ``inbox:<pid>`` for the parsed pid while the created tick and streak
    must agree across the prose and machine sentences (a
    serialization-identity check, not a substring heuristic).
    """
    if not isinstance(text, str):
        return None
    # pid/eid groups are non-greedy (.+?) and whitespace-tolerant: _prompt_pid
    # accepts any string, so a whitespace pid flows through sync untouched and
    # the writer mints its text. The non-greed would be ambiguous alone (a
    # pid may contain ")" or mimic the machine sentence), but the
    # serialization-identity cross-checks below — eid == "inbox:"+pid with the
    # prose tick/streak agreeing with the machine tick/streak — pin the only
    # self-consistent parse.
    m = _CLOSURE_NOTE_RE.fullmatch(text)
    if m is None:
        return None
    created1, pid, streak1 = int(m.group(1)), m.group(2), int(m.group(3))
    eid, created2 = m.group(4), int(m.group(5))
    if eid != f"{ID_PREFIX}{pid}":
        return None
    if created1 != created2:
        return None
    if streak1 != int(m.group(7)):
        return None
    return {"expectation_id": eid, "pid": pid,
            "created_tick": created1, "due_tick": int(m.group(6)),
            "streak": streak1, "confidence": float(m.group(8)),
            "outcome": m.group(9)}


def is_closure_note_text(text) -> bool:
    """Whether a record text is a domain-18 expectation-closure note."""
    return parse_closure_note_text(text) is not None


def mint_closure_note(subject, event: dict) -> str:
    """Append one expectation-closure note through the normal record path.

    Waking-tick path only (sync's confirm-lapsed branch); the caller owns
    the transaction so the record persists. Minting never advances the
    store tick and never touches the trace: it is a write inside the
    existing tick, not a new tick. Stamped generated_by="cognition"
    (engine-originated, not authored), and enters view eligibility and
    salience like any temporal record. Consolidation's near-dup and deep
    passes skip closure-note pairs (distinct texts are distinct plan-death
    episodes by construction — the templated wording would otherwise mint
    false dedup/supersede proposals that archive the very memory this
    mutation keeps); the byte-identical exact-dup sweep still applies as
    crash-window double-mint protection. Returns the record id.

    Idempotent on the expectation id: when a closure note for the same
    expectation is already in the store — e.g. a re-mint after a kill
    between the mint transaction's commit and the guard sidecar's write
    — the existing record's id is returned and nothing is appended:
    exactly one record per lapsed expectation. The check reads
    subject.workspace.records, the in-memory list _add appends to, so
    mints earlier in the SAME transaction are visible.
    """
    for record in subject.workspace.records:
        if record.source != "temporal" or record.generated_by != "cognition":
            continue
        parsed = parse_closure_note_text(record.first_person)
        if (parsed is not None
                and parsed["expectation_id"] == event["expectation_id"]):
            return record.id
    item = subject._add("temporal", closure_note_text(event),
                        concepts=(event["pid"],),
                        generated_by="cognition",
                        available_to_cognition=True,
                        salience=1.0, intensity=1.0)
    return item.id
