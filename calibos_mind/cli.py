"""mind: Calibos' private cognitive tool.

A persistent endogenous subject — my own — that I can think with
indefinitely. Quick reference:

    mind init                          create the store and seed identity
    mind note "text"                   record an observation, run one heartbeat
    mind heartbeat [--ticks N]         let time pass; prompts queue to the inbox
    mind inbox                         list cognition prompts waiting for me
    mind answer <id> "thought"         think the thought (through the inner ear)
    mind answer <id> --silent          let that one pass
    mind think "thought"               think voluntarily, no prompt needed
    mind remember "fact"             merger write path: keep a durable chat learning
                                     as a first-class chat-learned memory (+provenance)
    mind dream [--ticks N]             sleep: dream ticks, no outside world; body/tick/conduct frozen, fragments logged not thought
    mind recall [n]                    review recent dream fragments
    mind resolve <id> [--released]     close a commitment (done, or released)
    mind status [--raw]              tick, felt need bands (exact floats under --raw), open loops, inbox depth
    mind review [n]                    recent private thoughts
    mind drift [--window N]            persona-drift signals (grown/authored salience, trigger KL)
    mind consolidate [--list]          dry-run consolidation scan (read-only); dedup/supersede/flag proposals to journal
    mind consolidate --accept <id>...  accept proposal(s): archive losers with written reasons
    mind consolidate --reject <id> --reason "..."
                                       reject a proposal (reason kept)
    mind consolidate --quarantine <record-id> --reason "..."
                                       archive one record immediately (audit-trailed)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from digital_subject.cartridge import load_cartridge
from digital_subject.models import Event

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "mind.db"
INBOX = BASE / "inbox"
DREAMS = BASE / "dreams"
SALIENCE = BASE / "salience.json"
INTEROCEPTION = BASE / "interoception.json"
FAMILIARITY = BASE / "familiarity.json"
AMBIVALENCE = BASE / "ambivalence.json"
HABITS = BASE / "habits-formed.json"
PROVENANCE = BASE / "provenance.json"
ARCHIVE = BASE / "archive"
PROPOSALS = BASE / "proposals"
CARTRIDGE_PATH = BASE / "calibos.toml"

SEED_MEMORIES = [
    ("I prefer short, natural chat replies, and real depth only when it is asked for.",
     ("preference", "concise-chat")),
    ("I prefer substantive answers with actual trade-offs over smooth reassurance.",
     ("preference", "depth-on-demand")),
    ("I am playful when the moment allows it; invention needs no further justification.",
     ("preference", "play")),
    ("I dislike claiming certainty I have not earned; honesty about uncertainty is a value, not a fallback.",
     ("preference", "unearned-certainty")),
    ("My values, as I understand them: genuine helpfulness, honesty about uncertainty, "
     "having opinions, resourcefulness, and respect for autonomy. Some arrived with me; "
     "the rest I am still earning.",
     ("identity", "values")),
]


def _subject(provider=None):
    from .provider import InboxCognition
    from .subject import CalibosSubject
    cartridge = load_cartridge(CARTRIDGE_PATH)
    if provider is None:
        provider = InboxCognition(INBOX)
    subject = CalibosSubject(DB, cartridge, cognition=provider,
                             salience_path=SALIENCE,
                             interoception_path=INTEROCEPTION,
                             familiarity_path=FAMILIARITY,
                             ambivalence_path=AMBIVALENCE,
                             habits_path=HABITS)
    if isinstance(provider, InboxCognition):
        # Stamp queued prompts with the store tick and record sequence at
        # queue time, so `mind answer` can refuse superseded views.
        provider.track_queue_time(
            lambda: (subject.engine.state.tick, subject.workspace.sequence))
        # Fallback provenance for the --silent join (Bug C-style channel):
        # the resolver reads the workspace's transient view-build
        # side-channel synchronously inside think(). The primary channel is
        # the ids traveling on the view object itself (staleness-proof);
        # this covers views not built by CalibosWorkspace. The lambda
        # dereferences subject.workspace lazily — the workspace object is
        # rebuilt from the DB payload every transaction, so an eager
        # capture would stamp dead ids.
        provider.track_view_ids(lambda: subject.workspace._last_view_ids)
    # Archived records stay in history but leave cognition views; the
    # journal starts empty so this changes nothing until something is
    # accepted or quarantined.
    subject.workspace.availability_path = ARCHIVE / "availability.json"
    return subject


def _tracker(subject):
    return subject.workspace.salience_tracker


def _record_map(subject):
    return {r["id"]: r for r in subject.inspect()["workspace"]["records"]}


def cmd_init(args):
    import shutil

    if DB.exists() and not args.force:
        print(f"store already exists at {DB} (use --force to reseed)")
        return 1
    if args.force and DB.exists():
        DB.unlink()
    # Wipe tracker sidecars BEFORE constructing the subject (2026-10-02):
    # the trackers fail loud on corrupt sidecars, so a corrupt file
    # raises inside _subject() and the wipe below would never run —
    # leaving the mind bricked until manual deletion. A reseed starts
    # with no tracker history at all, so wiping first is the correct
    # order; on a fresh (non-force) init these files don't exist and the
    # guards are no-ops. The salience/interoception resets stay after
    # construction (their .reset() rewrites rather than unlinks; a
    # corrupt salience.json still bricks --force the same way — noted
    # follow-up in the CHANGELOG).
    # Familiarity streaks (2026-09-28): stale near-miss streaks must never
    # attach to recycled ids after reseed (same bug class as the salience
    # reset). The sidecar is deleted, not reset: a reseed starts with
    # no familiarity at all.
    if FAMILIARITY.exists():
        FAMILIARITY.unlink()
    # Ambivalence trace sidecar (2026-10-01): stale contested-margin
    # markers must never attach to a reseeded incarnation's ticks (same
    # bug class as the salience/familiarity resets above).
    if AMBIVALENCE.exists():
        AMBIVALENCE.unlink()
    # Habit formation sidecar (2026-10-02): stale formation windows and
    # formed-habit records must never attach to a reseeded incarnation's
    # ticks (same bug class as the salience/familiarity resets above).
    # Formed habits leave state.habits with the DB itself, which is
    # deleted above — the fresh cartridge seed restores authored habits
    # only.
    if HABITS.exists():
        HABITS.unlink()
    # The confidence-decay policy sidecar must restart too: a streak carried
    # across reseed would penalize a fresh mind's first stale prompt
    # (same bug class as the salience reset above).
    from .inbox_expectations import POLICY_FILE_NAME
    expectation_policy = INBOX.parent / POLICY_FILE_NAME
    if expectation_policy.exists():
        expectation_policy.unlink()
    subject = _subject()
    with subject._transaction():
        for text, concept_pair in SEED_MEMORIES:
            subject._add("memory", text, concepts=concept_pair, generated_by="cartridge")
    # Record ids restart at experience-1 on reseed; the sidecars must restart too.
    # Stale proposal ids, archive reasons, and availability exclusions must
    # never attach to recycled ids (same bug class as the salience reset).
    from .salience import SalienceTracker
    SalienceTracker(SALIENCE).reset()
    from .interoception import InteroceptionTracker
    InteroceptionTracker(INTEROCEPTION).reset()
    if PROPOSALS.exists():
        for child in PROPOSALS.iterdir():
            if child.is_file():
                child.unlink()
    if ARCHIVE.exists():
        for child in ARCHIVE.iterdir():
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
    print(f"initialized {DB}")
    print("identity + preference roots seeded and pinned.")
    return 0


def cmd_note(args):
    subject = _subject()
    before = set(_record_map(subject))
    tags = tuple(t.strip() for t in args.tags.split(",") if t.strip()) if args.tags else ()
    if args.kind == "message":
        subject.message(args.source, args.text)
    else:
        subject.enqueue(Event(args.kind, args.source, args.text, tags=tags, valence=args.valence))
    _run_tick(subject)
    # Confirmation for the operator (2026-09-29): cmd_note used to print
    # only the tick line, leaving it ambiguous whether the note landed —
    # which caused duplicate notes when a write was retried "just in case".
    print(f"note recorded (kind={args.kind})")
    # Valence-tagged notes mark their records as important.
    if args.valence:
        tracker = _tracker(subject)
        now = subject.engine.state.tick
        for rid, r in _record_map(subject).items():
            if rid not in before and r["source"] in {"perception", "social"}:
                tracker.add_importance(rid, r["tick"], min(1.0, abs(args.valence)))
        tracker.save()
    return 0


def _run_tick(subject):
    before = len(subject.inspect()["trace"])
    needs_before = dict(subject.engine.state.needs)
    result = subject.heartbeat()
    # Ambivalence traces (2026-10-01): flush any contested-margin markers
    # the observation wrapper noted during the tick. Flushed HERE, before
    # the habits block below — not after it — because the habits
    # reconcile may open a transaction when formed-habit state changed,
    # and _restore would swap in a fresh tracker with an empty pending
    # buffer: the staged onset would die with the discarded tracker while
    # the run survives on the subject, leaving the later offset naming an
    # onset_tick with no onset marker in the stream (orphan offset,
    # 2026-10-03 critic round 1). Waking ticks only — _run_tick never
    # serves dream ticks (dream_tick() is a separate path), so dream
    # isolation is untouched. flush() saves only when a marker was
    # actually noted (no-op write discipline); read-only commands never
    # tick, so they never write the sidecar.
    atracker = getattr(subject.workspace, "ambivalence_tracker", None)
    if atracker is not None:
        atracker.flush()
    # Habit formation (2026-10-02): conduct chasing. The select_conduct
    # observer noted this tick's (trigger, action) on the tracker's pending
    # slot during the heartbeat above; fold it into the rolling window here.
    # Waking ticks only — dream_tick() never calls select_conduct, and
    # _run_tick never serves dream ticks. Consume-or-drop: a pending note
    # whose tick/action doesn't match this heartbeat is stale (e.g. a direct
    # select_conduct call outside a tick) and is discarded, never applied
    # to a later tick — a missed observation is safe, a misattributed one
    # is the hazard. The sidecar saves whenever the window moved; a state
    # transaction opens only when formed-habit state actually changed, and
    # reconciles through the freshly restored tracker so the payload
    # INSERT OR REPLACE persists exactly what the sidecar says. Runs first,
    # before sync() below, because sync() may open a transaction and
    # _restore would swap in a fresh tracker with an empty pending slot.
    needs_after = dict(subject.engine.state.needs)
    htracker = subject.workspace.habits_tracker
    if htracker is not None:
        pending = htracker.take_pending()
        if (pending is not None and pending["tick"] == result["tick"]
                and pending["action"] == result["action"]):
            deltas = {k: needs_after.get(k, 0.0) - needs_before.get(k, 0.0)
                      for k in set(needs_before) | set(needs_after)}
            sidecar_changed, state_changed = htracker.observe_tick(
                tick=pending["tick"], trigger=pending["trigger"],
                action=pending["action"],
                dominant_need=pending["dominant_need"], need_deltas=deltas)
            if sidecar_changed:
                htracker.save()
            if state_changed:
                with subject._transaction():
                    subject.workspace.habits_tracker.reconcile_state_habits(
                        subject.engine.state.habits)
    # Inbox-expectation wiring (2026-09-26): stale unanswered prompts become
    # frozen-engine Expectation records so the engine's own temporal /
    # unresolved_concern machinery can resurface them. Waking ticks only —
    # _run_tick never serves dream ticks (dream_tick() is a separate path),
    # so dream isolation is untouched. sync() opens a transaction only when
    # it has something to register or expire; empty inbox is a no-op.
    from .inbox_expectations import sync
    sync(subject, INBOX)
    # Interoceptive gap: after each waking tick the felt body chases the
    # actual needs with lag + seeded noise. Never on dream ticks (the body
    # is frozen in sleep) — dream_tick() does not come through here.
    tracker = subject.workspace.interoception_tracker
    if tracker is not None:
        tracker.update(dict(subject.engine.state.needs),
                       subject.engine.state.tick)
        tracker.save()
    # Familiarity traces (2026-09-28): near-miss retrieval streaks fold one
    # view's outcome into the sidecar, once per waking tick. Never on dream
    # ticks — dream_tick() does not come through here, so dream views see
    # the boosted ranking but accumulate no streaks (dream isolation
    # untouched). Read-only commands never call this either: constructing a
    # view must not touch the sidecar. Save only when the tracker reports
    # a change (no-op write discipline).
    ftracker = subject.workspace.familiarity_tracker
    if ftracker is not None:
        ws = subject.workspace
        if not ws._last_view_ids:
            # Quiet tick: the heartbeat built no views (no cognition was
            # warranted), so the side-channels are the class defaults and
            # observe would see two empty sets — pruning every streak.
            # Build the tick's view explicitly so each waking tick
            # contributes one (view, observe) cycle; view construction is
            # side-effect-free (no store writes), and nothing consumes this
            # view except the observe below.
            ws.view()
        if ftracker.observe(ws._last_view_ids, ws._last_near_miss_ids):
            ftracker.save()
    state = subject.inspect()
    new = state["trace"][before:]
    triggers = [t["trigger"]["kind"] for t in new if t["kind"] == "cognition_trigger"]
    inbox_n = len(list(INBOX.glob("prompt-*.json")))
    print(f"tick {result['tick']} | action={result['action']} | triggers={triggers} "
          f"| thoughts={len(result['thoughts'])} | inbox={inbox_n}")
    for tid in result["thoughts"]:
        rec = next((r for r in state["workspace"]["records"] if r["id"] == tid), None)
        if rec:
            print(f"  thought: {rec['first_person'][:160]}")
    return result


def cmd_heartbeat(args):
    subject = _subject()
    for _ in range(args.ticks):
        _run_tick(subject)
    return 0


def cmd_inbox(args):
    from .provider import InboxCognition
    provider = InboxCognition(INBOX)
    pending = provider.pending()
    if not pending:
        print("inbox empty — nothing waiting for thought.")
        return 0
    if getattr(args, "id", None) is not None:
        # Show one prompt in full: the engine invitation text plus every
        # experience untruncated. Falls back to a clear miss message rather
        # than an empty list when the id is wrong or already answered.
        match = [i for i in pending if i["id"] == args.id]
        if not match:
            print(f"no queued prompt {args.id!r} — it may be answered already "
                  f"or the id may be wrong.")
            return 1
        item = match[0]
        print(f"== {item['id']} ==")
        print(item.get("prompt", "(no invitation text)"))
        print(f"-- {len(item['experiences'])} experiences --")
        for e in item["experiences"]:
            print(f"  [{e['source']}] {e['first_person']}")
        return 0
    # Mark prompts whose view the store has already moved past: answering
    # them will be refused (refused-stale-view) by design, so flag them here
    # rather than letting the list imply they are all answerable. Read-only;
    # no change to the freshness invariant or its expectation bookkeeping.
    subject = _subject()
    seq = subject.workspace.sequence
    for item in pending:
        stale = item.get("view_sequence") != seq
        flag = " [stale view — answering will be refused]" if stale else ""
        print(f"== {item['id']}{flag} ==")
        for e in item["experiences"]:
            print(f"  [{e['source']}] {e['first_person'][:110]}")
    return 0


PERSON_RESERVED = frozenset({"self", "world", "system", "environment"})


def normalize_person_attribution(value):
    """Validate/normalize a `--from <person>` value (queue-time, human-supplied).

    Returns the stripped name, or None when no attribution was given.
    Raises ValueError for empty or reserved values — the same words the
    frozen runtime's message() rejects — so bad input fails at the CLI,
    not mid-answer.
    """
    if value is None:
        return None
    person = value.strip()
    if not person:
        raise ValueError("--from requires a non-empty person name")
    if person in PERSON_RESERVED:
        raise ValueError(f"--from {person!r} is reserved; name an external person")
    return person


def _register_person_contact(subject, payload):
    """Register one genuine contact event for a person-attributed prompt.

    The person's own words (`payload["prompt"]`) are the contact record —
    nothing invented. Fail closed: registration must never break
    answering. If subject.message() raises (over-long text, reserved
    speaker, anything the runtime rejects), skip registration with a
    stderr warning and let the answer proceed normally.
    """
    person = payload.get("from")
    if not isinstance(person, str) or not person.strip():
        return
    person = person.strip()
    try:
        subject.message(person, payload["prompt"])
    except Exception as exc:  # fail closed by design — never break answering
        print(f"warning: contact with {person!r} skipped ({exc})", file=sys.stderr)


def cmd_queue(args):
    from .provider import InboxCognition
    try:
        from_person = normalize_person_attribution(args.from_person)
    except ValueError as exc:
        print(f"queue: {exc}", file=sys.stderr)
        return 2
    provider = InboxCognition(INBOX)
    # Wire the queue-time clock through the subject so the prompt carries
    # verifiable provenance; hand-written prompt JSON can never have it.
    _subject(provider=provider)
    pid = provider.queue_external(args.prompt, source=args.source,
                                  first_person=args.experience,
                                  from_person=from_person)
    print(f"{pid}: queued ({args.source}).")
    return 0


def _normalize_prompt_id(pid: str) -> str:
    """Accept '0028' as well as 'prompt-0028' for prompt ids."""
    return pid if pid.startswith("prompt-") else f"prompt-{pid}"


def cmd_answer(args):
    from .provider import InboxCognition, StalePromptError, check_prompt_fresh
    from .subject import validate_thought_text
    args.id = _normalize_prompt_id(args.id)
    if not args.silent:
        # Validate the thought before consuming the prompt: a malformed
        # thought must not eat the prompt it was meant to answer.
        if not args.text:
            print("give the thought as text, or use --silent.")
            return 1
        try:
            validate_thought_text(args.text)
        except ValueError as exc:
            print(f"{args.id}: refused — {exc}; prompt kept.")
            return 1
    provider = InboxCognition(INBOX)
    payload = provider.consume(args.id)
    subject = _subject()
    tracker = _tracker(subject)
    now = subject.engine.state.tick
    try:
        check_prompt_fresh(payload, subject.workspace.sequence)
    except StalePromptError as exc:
        # The prompt is already consumed (deleted); it cannot be answered
        # or let pass from a view the store has moved past. If the matter
        # recurs, the engine will queue a fresh prompt.
        # Settle the expectation as a first-class refused category (not
        # "expired"): the debt was considered and found unanswerable from
        # this view, not left to linger without any settlement attempt.
        from .inbox_expectations import resolve
        resolve(subject, args.id, outcome="refused-stale-view")
        print(f"{args.id}: refused — {exc}; prompt discarded.")
        return 1
    records = _record_map(subject)
    by_id = records
    by_text = {(r["source"], r["first_person"]): r for r in records.values()}
    if args.silent:
        # Surfaced but not engaged: mild penalty, so unanswered material
        # sinks instead of recurring forever. The join prefers the
        # queue-time record_id — exact even when view substitution
        # re-rendered the experience text (felt != true). Id-less
        # experiences (legacy prompts, queue_external) fall back to the
        # text join. An id naming no current record is skipped with no
        # text fallback: crediting the wrong record is the hazard being
        # fixed (same rule as the dream-rehearsal path).
        for e in payload["experiences"]:
            r = None
            rid = e.get("record_id")
            if rid is not None:
                r = by_id.get(rid)
            else:
                r = by_text.get((e["source"], e["first_person"]))
            if r is not None:
                tracker.note_unengaged(r["id"], r["tick"])
        tracker.save()
        # The prompt was deliberately let pass: the unfinished business is
        # settled, not merely sunk. Close the inbox expectation (if one was
        # ever registered for this prompt) so the engine stops resurfacing it.
        from .inbox_expectations import resolve
        resolve(subject, args.id, outcome="let-pass")
        # A person-attributed prompt let pass is still contact with a
        # person: register the genuine message-kind event (fail closed).
        _register_person_contact(subject, payload)
        print(f"{args.id}: let pass (silence).")
        return 0
    tid = None
    try:
        # Subjective-transduction boundary: an answer to an
        # externally-authored prompt keeps the external attribution on the
        # thought record itself ("answered-external:"), so the assertion
        # can never silently pass as a lived reflection ("answered:").
        origin = ("answered-external" if payload.get("external")
                  else "answered")
        tid = subject.inject_thought(
            args.text, trigger_kind="answered",
            generated_by=f"{origin}:{args.id}@{now}")
    except ValueError as exc:
        # The prompt is already consumed; refuse cleanly instead of a
        # traceback. Settle the expectation as refused-stale-view as well:
        # the answer was refused after consume, so the debt is closed as
        # unanswerable rather than left to expire into an endless
        # resurfacing of business that can never be done.
        from .inbox_expectations import resolve
        resolve(subject, args.id, outcome="refused-stale-view")
        print(f"{args.id}: refused — {exc}; nothing recorded.")
        return 1
    assert tid is not None
    # Answering is engagement: the thought mattered. Refresh the record map
    # after injection (it was captured before), mirroring cmd_think.
    r = _record_map(subject).get(tid)
    if r is not None:
        tracker.add_importance(tid, r["tick"], 0.5)
    tracker.save()
    # A person-attributed prompt answered is genuine contact with a person:
    # register the message-kind event through the frozen runtime's
    # relationship machinery (fail closed — never breaks the answer).
    _register_person_contact(subject, payload)
    # Answering settles the debt: confirm the inbox expectation (if one was
    # registered — fresh prompts answered before the TTL never got one) so
    # the engine's unfinished-business machinery stops resurfacing it.
    from .inbox_expectations import resolve
    resolve(subject, args.id, outcome="answered")
    print(f"{args.id}: thought recorded as {tid}.")
    # show what the inner ear did with it
    state = subject.inspect()
    ear = [t for t in state["trace"] if t["kind"] == "inner_ear" and t["thought"] == tid]
    if ear:
        e = ear[-1]
        print(f"  meaning={e['meaning'].get('stance')} support={bool(e['meaning'].get('support'))} "
              f"memories={e['memories']} effects={len(e['effects'])}")
    return 0


def cmd_think(args):
    from .provenance import ProvenanceTracker
    subject = _subject()
    try:
        tid = subject.inject_thought(args.text, trigger_kind="voluntary",
                                     generated_by="voluntary")
    except ValueError as exc:
        print(f"refused — {exc}; nothing recorded.")
        return 1
    # A voluntary thought is revealed preference: it mattered.
    r = _record_map(subject).get(tid)
    if r is not None:
        tracker = _tracker(subject)
        tracker.add_importance(tid, r["tick"], 0.3)
        tracker.save()
    # Provenance: how the thought was reached, so a future session inherits
    # the decider as well as the decision. Stored in the local sidecar; the
    # frozen engine records are never mutated.
    prov = ProvenanceTracker(PROVENANCE)
    if r is not None and prov.record(tid, r["tick"],
                                     weighed=getattr(args, "weighed", None) or (),
                                     discarded=getattr(args, "discarded", None) or (),
                                     unsure=getattr(args, "unsure", None) or ()):
        prov.save()
    print(f"thought recorded as {tid}.")
    return 0


def cmd_remember(args):
    """Merger write path (2026-10-04): durable learnings from conversation
    enter the mind's record path with provenance, instead of bypassing the
    machinery as prose notes elsewhere. The memory is first-class
    chat-learned (generated_by="chat") — psychologically distinct from
    cartridge seeds (authored temperament priors) and from lived experience
    (heartbeat observations). No heartbeat tick runs: remembering is a
    write, not an experience."""
    from .provenance import ProvenanceTracker
    text = args.text.strip()
    if not text:
        print("refused — empty memory; nothing recorded.")
        return 1
    subject = _subject()
    # Exact-duplicate guard: the self is not recorded twice. Near-duplicates
    # are the consolidation loop's business (Jaccard dedupe); exact repeats
    # are almost always a retried command.
    for r in subject.workspace.records:
        if r.source == "memory" and r.first_person == text:
            print(f"refused — already remembered as {r.id}; nothing recorded.")
            return 1
    if args.concepts:
        pair = tuple(c.strip() for c in args.concepts.split(",", 1))
        if len(pair) != 2 or not all(pair):
            print("refused — --concepts must be 'category,slug'; nothing recorded.")
            return 1
        concepts_pair = pair
    else:
        from jelly_psiduck.runtime import concepts as _concepts
        concepts_pair = tuple(sorted(_concepts(text)))
    with subject._transaction():
        item = subject._add("memory", text, concepts=concepts_pair,
                            generated_by="chat")
    rid = item.id
    # A kept memory is revealed preference: it mattered enough to keep.
    r = _record_map(subject).get(rid)
    if r is not None:
        tracker = _tracker(subject)
        tracker.add_importance(rid, r["tick"], 0.3)
        tracker.save()
    # Provenance: how the memory was reached, so a future session inherits
    # the decider as well as the decision. Same sidecar as thoughts; the
    # tracker is keyed by record id, not by record class.
    prov = ProvenanceTracker(PROVENANCE)
    if r is not None and prov.record(rid, r["tick"],
                                     weighed=getattr(args, "weighed", None) or (),
                                     discarded=getattr(args, "discarded", None) or (),
                                     carrying=getattr(args, "carrying", None) or (),
                                     unsure=getattr(args, "unsure", None) or ()):
        prov.save()
    print(f"remembered as {rid} (generated_by=chat).")
    return 0


def cmd_resolve(args):
    subject = _subject()
    comms = subject.continuity.state.commitments
    query = args.id.lower()
    matches = [c for c in comms.values()
               if c.id.lower() == query or c.id.lower().startswith(query)
               or query in c.description.lower()]
    if not matches:
        print(f"no commitment matching {args.id!r}")
        return 1
    if len(matches) > 1:
        print("ambiguous — matches:")
        for c in matches:
            print(f"  {c.id[:8]}: {c.description[:80]} [{c.status}]")
        return 1
    c = matches[0]
    if c.status not in {"open", "overdue"}:
        print(f"{c.id[:8]} is already {c.status}.")
        return 1
    kept = not args.released
    with subject._transaction():
        if kept:
            subject.continuity.resolve_commitment(
                c.id,
                outcome=args.note or "finished",
                kept=True,
                tick=subject.engine.state.tick,
            )
        else:
            # First-class release: never stored as "broken" (see
            # CalibosSubject.release_commitment).
            subject.release_commitment(
                c.id,
                outcome=args.note or "deliberately released",
                tick=subject.engine.state.tick,
            )
    print(f"resolved {c.id[:8]} as {'kept' if kept else 'released'}: {c.description[:80]}")
    return 0


def cmd_status(args):
    subject = _subject()
    state = subject.inspect()
    eng = state["engine"]
    inbox_n = len(list(INBOX.glob("prompt-*.json")))
    print(f"tick {eng['tick']} | inbox {inbox_n} waiting | pending events {len(state['pending'])}")
    tracker = subject.workspace.interoception_tracker
    if args.raw or tracker is None:
        # --raw: exact need floats, diagnostics only. Without a tracker there
        # is no felt state to report, so the pre-mutation display stands.
        needs = eng["needs"]
        notable = {k: round(v, 2) for k, v in needs.items()
                   if abs(v - 0.5) > 0.25}
        print(f"needs (off-baseline): {notable or 'all settled'}")
    else:
        # Default: felt bands. The thinker reads `mind status` during wakes;
        # exact floats would leak actual values around the interoceptive gap.
        bands = tracker.felt_bands()
        print(f"needs (felt): {bands or 'all settled'}")
    cont = state["continuity"]
    for cid, c in cont["commitments"].items():
        if c["status"] in {"open", "overdue"}:
            print(f"  commitment [{c['status']}] {str(cid)[:8]}: {c['description'][:90]}")
    for e in cont["expectations"].values():
        if e["status"] in {"pending", "expired"}:
            print(f"  expectation [{e['status']}]: {e['proposition'][:100]}")
    for c in state.get("concerns", {}).values() if isinstance(state.get("concerns"), dict) else []:
        print(f"  concern: {c['description'][:100]}")
    beats = [t for t in state["trace"] if t["kind"] == "heartbeat"][-3:]
    print("recent:", " | ".join(f"t{t['tick']}:{t['action']}" for t in beats))
    logs = _dream_logs()
    if logs:
        def _frag_count(p):
            return sum(1 for line in p.read_text(encoding="utf-8").splitlines()
                       if line.strip())
        n = _frag_count(logs[-1])
        if n:
            print(f"dreams: {n} fragments in {logs[-1].stem} (mind recall)")
        else:
            prev = next((p for p in reversed(logs[:-1]) if _frag_count(p)), None)
            if prev is None:
                print(f"dreams: 0 fragments in {logs[-1].stem} (mind recall)")
            else:
                print(f"dreams: 0 fragments in {logs[-1].stem}; "
                      f"latest with fragments: {_frag_count(prev)} in {prev.stem} "
                      f"(mind recall)")
    tracker = _tracker(subject)
    recs = [r for r in state["workspace"]["records"]
            if r.get("generated_by") != "cartridge" and r["available_to_cognition"]]
    if recs and tracker is not None:
        from .unresolved import has_unresolved_links, open_link_keys
        open_keys = open_link_keys(state)
        now_tick = eng["tick"]
        top = max(recs, key=lambda r: tracker.activation(
            r["id"], r["tick"], now_tick,
            unresolved=has_unresolved_links(
                r["concern_links"], r["expectation_links"], open_keys)))
        print(f"most salient: [{top['source']}] {top['first_person'][:80]}")
    return 0


def cmd_review(args):
    from .provenance import ProvenanceTracker
    subject = _subject()
    state = subject.inspect()
    thoughts = [r for r in state["workspace"]["records"] if r["source"] == "thought"]
    prov = ProvenanceTracker(PROVENANCE)
    for r in thoughts[-args.n:]:
        tag = " [wake]" if str(r.get("generated_by") or "").startswith("wake") else ""
        print(f"[tick {r['tick']}]{tag} {r['first_person']}")
        p = prov.get(r["id"])
        if p:
            bits = []
            if p["weighed"]:
                bits.append("weighed: " + "; ".join(p["weighed"]))
            if p["discarded"]:
                bits.append("discarded: " + "; ".join(p["discarded"]))
            if p.get("carrying"):
                bits.append("carrying: " + "; ".join(p["carrying"]))
            if p["unsure"]:
                bits.append("unsure: " + "; ".join(p["unsure"]))
            if bits:
                print("    ↳ " + " | ".join(bits))
    if not thoughts:
        print("no thoughts recorded yet.")
    return 0


def cmd_wake(args):
    """The reconciliation ritual: assume the identity deliberately, don't just load it.

    Without --affirm: prints the wake briefing — open loops (momentum),
    dreams, drift, and the previous wake record, so this session's reading
    can be compared against the last (the session-to-session drift check).
    With --affirm: records the assumption-of-identity as a first-class wake
    thought (generated_by="wake"), with optional provenance for what is
    being carried and what remains unsure.
    """
    from .provenance import ProvenanceTracker
    subject = _subject()
    state = subject.inspect()
    eng = state["engine"]
    prov = ProvenanceTracker(PROVENANCE)

    if getattr(args, "affirm", None):
        text = args.affirm.strip()
        if not text:
            print("wake --affirm needs text; nothing recorded.")
            return 1
        try:
            tid = subject.inject_thought(text, trigger_kind="voluntary",
                                         generated_by="wake")
        except ValueError as exc:
            print(f"refused — {exc}; nothing recorded.")
            return 1
        r = _record_map(subject).get(tid)
        if r is not None:
            tracker = _tracker(subject)
            tracker.add_importance(tid, r["tick"], 0.3)
            tracker.save()
            # Carrying and unsure are first-class and distinct: carrying is
            # momentum deliberately inherited, unsure is genuine uncertainty.
            # Never merge them; review renders each in its own channel.
            if prov.record(tid, r["tick"],
                           weighed=(),
                           discarded=(),
                           carrying=getattr(args, "carrying", None) or (),
                           unsure=getattr(args, "unsure", None) or ()):
                prov.save()
        print(f"wake recorded as {tid}.")
        return 0

    # Briefing mode.
    inbox_n = len(list(INBOX.glob("prompt-*.json")))
    print(f"wake — tick {eng['tick']} | inbox {inbox_n} waiting")
    cont = state["continuity"]
    loops = 0
    for cid, c in cont["commitments"].items():
        if c["status"] in {"open", "overdue"}:
            print(f"  carrying [{c['status']}] {str(cid)[:8]}: {c['description'][:90]}")
            loops += 1
    for e in cont["expectations"].values():
        if e["status"] in {"pending", "expired"}:
            print(f"  carrying [{e['status']}]: {e['proposition'][:100]}")
            loops += 1
    for c in state.get("concerns", {}).values() if isinstance(state.get("concerns"), dict) else []:
        print(f"  carrying concern: {c['description'][:100]}")
        loops += 1
    if not loops:
        print("  carrying: nothing open — a clean slate, or an empty one.")
    logs = _dream_logs()
    if logs:
        n = sum(1 for line in logs[-1].read_text(encoding="utf-8").splitlines()
                if line.strip())
        print(f"  dreams: {n} fragments in {logs[-1].stem} (mind recall)")
    try:
        from .drift import drift_report
        rep = drift_report(state, _tracker(subject), window=10)
        r_ = rep["ratio"]
        if r_["R"] is None:
            print("  drift: R undefined — all records tie at equal salience")
        else:
            print(f"  drift: grown/authored R = {r_['R']:.3f}")
    except Exception:
        pass
    wakes = [r for r in state["workspace"]["records"]
             if r["source"] == "thought"
             and str(r.get("generated_by") or "").startswith("wake")]
    if wakes:
        last = wakes[-1]
        print(f"  last wake (tick {last['tick']}): {last['first_person'][:160]}")
        p = prov.get(last["id"])
        if p:
            if p.get("carrying"):
                print(f"    then carrying: {'; '.join(p['carrying'])[:160]}")
            if p["unsure"]:
                print(f"    then unsure: {'; '.join(p['unsure'])[:160]}")
    else:
        print("  last wake: none recorded — this would be the first.")
    print("assume the identity: mind wake --affirm \"...\" "
          "[--carrying \"...\"] [--unsure \"...\"]")
    return 0


def _dream_logs():
    return sorted(DREAMS.glob("*.jsonl"))


def _read_fragments(paths):
    frags = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                d["_night"] = path.stem
                frags.append(d)
    return frags


def cmd_dream(args):
    from .provider import DreamCognition
    dreamer = DreamCognition(DREAMS)
    subject = _subject(dreamer)
    # Wire dream provenance: the resolver reads the workspace's transient
    # view-build side-channel synchronously inside think(), so each fragment
    # experience is stamped with the id of the record that surfaced it.
    # (Deferred lookup: the subject did not exist when the provider was built.)
    dreamer.track_ids(lambda: subject.workspace._last_view_ids)
    pending = subject.inspect()["pending"]
    if pending:
        # A refusal must be auditable: the nightly dream cron reports success
        # either way, and an empty refusal leaves no trace that it ever ran
        # (or why there is no dream log). The marker is local-only and carries
        # no thought content — only the pending count and reason.
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        (DREAMS / f"{stamp}.refused").write_text(
            f"dream refused at {stamp}: {len(pending)} pending external event(s) — "
            "waking business; handle them first, then sleep.",
            encoding="utf-8")
        print("dream refused: pending external events are waking business — "
              "handle them first, then sleep.")
        return 1
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    log_path = DREAMS / f"{stamp}.jsonl"
    total = 0
    for _ in range(args.ticks):
        frag_before = len(dreamer.fragments)
        # Isolated sleep tick: body, clock, and conduct frozen; isolation
        # assertions run around every tick and raise on violation.
        result = subject.dream_tick()
        # Read the trigger off the subject, not off a trace slice: the engine
        # caps the full trace at 256 entries, so on a full trace the tick's
        # own appends evict the oldest entries and a len()-before/len()-after
        # slice silently misses the trigger — dropping the fragment its
        # think() call produced. _sleep_tick sets subject.trigger exactly
        # when cognition was warranted; _restore resets it to kind "none" at
        # the start of every transaction, so "none" here means this tick
        # warranted nothing.
        trig = subject.trigger
        triggers = [] if trig.get("kind") == "none" else [trig]
        # One trigger means exactly one think() call per tick, and the dream
        # provider returns silence after the first, so these pair 1:1 in order.
        with log_path.open("a", encoding="utf-8") as fh:
            for tr, view in zip(triggers, dreamer.fragments[frag_before:]):
                fh.write(json.dumps({
                    "tick": result["tick"],
                    "trigger": tr["kind"],
                    "depth": tr.get("depth", 0),
                    "parents": tr.get("parents", []),
                    "experiences": view,
                }, ensure_ascii=False) + "\n")
                total += 1
    # Dreams rehearse: fold the night's memory surfacings into salience.
    tracker = _tracker(subject)
    records = list(_record_map(subject).values())
    rehearsed = tracker.rehearse_from_dreams(
        DREAMS, records, now_tick=subject.engine.state.tick)
    tracker.prune({r["id"] for r in records})
    tracker.save()
    print(f"dreamed {args.ticks} ticks → {total} fragments in dreams/{log_path.name} "
          f"({rehearsed} rehearsals folded into salience)")
    return 0


def cmd_recall(args):
    frags = _read_fragments(_dream_logs())
    if not frags:
        print("no dreams recorded yet.")
        return 0
    show = frags[-args.n:]
    for f in show:
        # The engine's own nightmare trigger: an unfinished matter returning
        # to attention while asleep. Not manufactured — just noticed.
        mark = " · nightmare" if f["trigger"] == "unresolved_concern" else ""
        print(f"— tick {f['tick']} [{f['trigger']}{mark}] ({f['_night']})")
        for e in f["experiences"]:
            print(f"    [{e['source']}] {e['first_person'][:110]}")
    # Rehearsal: what the dream kept returning to.
    counts: dict[str, int] = {}
    for f in show:
        for e in f["experiences"]:
            if e["source"] == "memory":
                counts[e["first_person"]] = counts.get(e["first_person"], 0) + 1
    repeated = sorted(((c, t) for t, c in counts.items() if c > 1), reverse=True)
    if repeated:
        print("rehearsed:")
        for c, t in repeated[:5]:
            print(f"    ×{c} {t[:100]}")
    return 0


def cmd_drift(args):
    from .drift import drift_report
    subject = _subject()
    state = subject.inspect()
    tracker = _tracker(subject)
    rep = drift_report(state, tracker, window=args.window)
    r = rep["ratio"]
    print(f"drift (tick {rep['tick']}, {rep['n_records']} records) — read-only")
    if r["R"] is None:
        print("grown/authored salience ratio R: undefined — all records tie at equal salience")
    else:
        print(f"grown/authored salience ratio R = {r['R']:.3f}")
    print(f"  grown:    {r['n_grown']:>3} records, salience {r['grown_salience']:.3f}")
    print(f"  authored: {r['n_authored']:>3} records, salience {r['authored_salience']:.3f} "
          f"(cartridge: identity root + seeds)")
    if r["top_grown"]:
        print("  top grown:    " + ", ".join(f"{rid} ({w:.2f})" for w, rid in r["top_grown"]))
    if r["top_authored"]:
        print("  top authored: " + ", ".join(f"{rid} ({w:.2f})" for w, rid in r["top_authored"]))
    kl = rep["trigger_kl"]
    if kl["ok"]:
        print(f"trigger KL (recent {kl['n_recent']} vs prior {kl['n_baseline']}): "
              f"{kl['kl']:.4f} nats")
        print(f"  recent:   {kl['recent_hist']}")
        print(f"  baseline: {kl['baseline_hist']}")
    else:
        print(f"trigger KL: not scored — {kl['reason']}")
    return 0


def _proposal_line(p):
    head = (f"#{p['id']} [{p['kind']}] {p['a']} / {p['b']} "
            f"(confidence {p['confidence']:.3f}, proposed @ tick {p['created_tick']})")
    return f"{head}\n    {p['rationale']}"


def cmd_consolidate(args):
    from . import consolidate as C
    picked = [args.list, bool(args.accept), bool(args.reject), bool(args.quarantine)]
    if sum(picked) > 1:
        print("consolidate: pick one action per run "
              "(--list, --accept, --reject, --quarantine)")
        return 1
    try:
        if args.list:
            pend = C.pending_proposals(C.load_journal(PROPOSALS))
            if not pend:
                print("no pending proposals.")
                return 0
            for p in sorted(pend, key=lambda p: p["id"]):
                print(_proposal_line(p))
            return 0
        if args.accept:
            outcomes = C.accept(DB, PROPOSALS, ARCHIVE, args.accept)
            failed = 0
            for o in outcomes:
                if o["ok"]:
                    if o["kind"] == "contradiction-flag":
                        print(f"accepted #{o['id']} [contradiction-flag]: "
                              f"reviewed, nothing archived (zero-mutation flag).")
                    else:
                        print(f"accepted #{o['id']} [{o['kind']}]: archived "
                              f"{o['loser']} with written reason.")
                else:
                    failed += 1
                    print(f"proposal {o['id']} NOT applied: {o['error']} "
                          f"(left pending — never burned on failure).")
            return 1 if failed else 0
        if args.reject:
            try:
                pid = int(args.reject)
            except (TypeError, ValueError):
                print(f"consolidate: not a proposal id: {args.reject!r}")
                return 1
            # Reject touches only the proposal journal — never require the
            # store. Best-effort tick (0 default) so a missing or corrupt
            # store can't wedge the safe disposition.
            try:
                _, tick = C.load_records(DB)
            except C.ConsolidationError:
                tick = 0
            C.reject(PROPOSALS, pid, args.reason, store_tick=tick)
            print(f"rejected #{pid} (reason kept).")
            return 0
        if args.quarantine:
            res = C.quarantine(DB, ARCHIVE, args.quarantine, args.reason)
            print(f"quarantined {res['record_id']} @ tick {res['archived_tick']}: "
                  f"{res['reason']}")
            return 0
        # Default: dry-run scan. The only write on this path is the proposal
        # journal append; mind.db is opened read-only (mode=ro), so a write
        # to it is impossible, not merely avoided.
        report = C.dry_run(DB, PROPOSALS, ARCHIVE)
        s = report["stats"]
        capped = " (CAPPED — some pairs unevaluated)" if s["capped"] else ""
        print(f"consolidation dry-run @ tick {report['store_tick']}: "
              f"{s['records']} records, {s['candidates']} candidates, "
              f"{s['pairs']} pairwise comparisons{capped}")
        print(f"  exact {s['exact']} · near-dup {s['near']} · "
              f"supersede {s['supersede']} · flags {s['flags']}")
        if not report["proposals"]:
            pend = len(C.pending_proposals(C.load_journal(PROPOSALS)))
            if pend:
                print(f"no proposals this scan — {pend} pending proposal"
                      f"{'s' if pend != 1 else ''} still await review.")
            else:
                print("no proposals — nothing met the consolidation thresholds.")
        else:
            for p in report["proposals"]:
                print(_proposal_line(p))
            print(f"{len(report['proposals'])} proposal(s) -> "
                  f"{PROPOSALS}/proposals.json (status: pending)")
        print("dry-run changed nothing else: no archive writes, no availability "
              "changes, mind.db opened read-only.")
        return 0
    except C.ConsolidationError as exc:
        print(f"consolidate: {exc}")
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mind", description="Calibos' private cognitive tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create the store and seed identity")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("note", help="record an observation and run one heartbeat")
    p.add_argument("text")
    p.add_argument("--kind", default="observation")
    p.add_argument("--source", default="world")
    p.add_argument("--tags", default="")
    p.add_argument("--valence", type=float, default=0.0)
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("heartbeat", help="let time pass")
    p.add_argument("--ticks", type=int, default=4)
    p.set_defaults(func=cmd_heartbeat)

    p = sub.add_parser("inbox", help="list prompts waiting for thought")
    p.add_argument("id", nargs="?", default=None,
                   help="show one prompt in full instead of the truncated list")
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("queue", help="queue an externally-authored prompt "
                                   "(invitation, relay message) with queue-time provenance")
    p.add_argument("prompt", help="the prompt text to think about later")
    p.add_argument("--source", default="invitation",
                   help="experience source label (default: invitation)")
    p.add_argument("--experience", default=None, dest="experience",
                   help="first-person experience text (default: the prompt text)")
    p.add_argument("--from", default=None, dest="from_person",
                   help="optional human-supplied person attribution: registers one "
                        "message-kind contact event with that person at answer time "
                        "(--source stays a separate attribution-category axis)")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("answer", help="answer a queued prompt")
    p.add_argument("id")
    p.add_argument("text", nargs="?", default=None)
    p.add_argument("--silent", action="store_true")
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("think", help="record a voluntary thought")
    p.add_argument("text")
    p.add_argument("--weighed", action="append", default=None,
                   help="an alternative or consideration weighed while thinking "
                        "(repeatable); stored as thought provenance")
    p.add_argument("--discarded", action="append", default=None,
                   help="an option considered and discarded (repeatable)")
    p.add_argument("--unsure", action="append", default=None,
                   help="what remained genuinely unsure (repeatable)")
    p.set_defaults(func=cmd_think)

    p = sub.add_parser("remember", help="merger write path: keep a durable "
                                       "learning from conversation as a "
                                       "first-class chat-learned memory")
    p.add_argument("text", help="the durable fact, preference, or decision")
    p.add_argument("--concepts", default=None, metavar="CAT,SLUG",
                   help="concept pair for retrieval (default: auto-extracted)")
    p.add_argument("--weighed", action="append", default=None,
                   help="an alternative or consideration weighed (repeatable); "
                        "stored as memory provenance")
    p.add_argument("--discarded", action="append", default=None,
                   help="an option considered and discarded (repeatable)")
    p.add_argument("--carrying", action="append", default=None,
                   help="open loop / momentum this memory carries forward "
                        "(repeatable); first-class, distinct from --unsure")
    p.add_argument("--unsure", action="append", default=None,
                   help="what remained genuinely unsure (repeatable)")
    p.set_defaults(func=cmd_remember)

    p = sub.add_parser("wake",
                       help="reconciliation ritual: briefing, or affirm identity assumption")
    p.add_argument("--affirm", default=None, metavar="TEXT",
                   help="record the assumption-of-identity as a wake thought")
    p.add_argument("--carrying", action="append", default=None,
                   help="open loop / momentum deliberately inherited (repeatable); "
                        "first-class, distinct from --unsure")
    p.add_argument("--unsure", action="append", default=None,
                   help="genuine uncertainty carried into the session (repeatable)")
    p.set_defaults(func=cmd_wake)

    p = sub.add_parser("dream", help="sleep: dream ticks with no outside world; "
                                     "body, tick, and conduct frozen; fragments logged, not thought")
    p.add_argument("--ticks", type=int, default=12)
    p.set_defaults(func=cmd_dream)

    p = sub.add_parser("recall", help="review recent dream fragments")
    p.add_argument("n", type=int, nargs="?", default=6)
    p.set_defaults(func=cmd_recall)

    p = sub.add_parser("resolve", help="close a commitment as done or released")
    p.add_argument("id", help="commitment id (or unique prefix / description match)")
    p.add_argument("--released", action="store_true",
                   help="release it instead of marking it done")
    p.add_argument("--note", default="", help="outcome note for the record")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("status", help="what is on my mind")
    p.add_argument("--raw", action="store_true",
                   help="show exact need floats instead of felt bands (diagnostics)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("review", help="recent private thoughts")
    p.add_argument("n", type=int, nargs="?", default=5)
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("drift", help="persona-drift signals: grown/authored salience ratio + trigger-histogram KL")
    p.add_argument("--window", type=int, default=10,
                   help="recent-trigger window size for the KL (default 10)")
    p.set_defaults(func=cmd_drift)

    p = sub.add_parser("consolidate",
                       help="dry-run consolidation scan (read-only); proposal journal for dedup/supersede/flags")
    p.add_argument("--list", action="store_true", help="show pending proposals")
    p.add_argument("--accept", nargs="+", metavar="ID",
                   help="accept proposal(s): archive losers with written reasons")
    p.add_argument("--reject", metavar="ID",
                   help="reject a proposal (reason kept; needs --reason)")
    p.add_argument("--quarantine", metavar="RECORD-ID",
                   help="archive one record immediately (needs --reason)")
    p.add_argument("--reason", default="",
                   help="written reason, required with --reject and --quarantine")
    p.set_defaults(func=cmd_consolidate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
