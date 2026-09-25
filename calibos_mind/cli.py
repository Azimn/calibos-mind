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
                             interoception_path=INTEROCEPTION)
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
    result = subject.heartbeat()
    # Interoceptive gap: after each waking tick the felt body chases the
    # actual needs with lag + seeded noise. Never on dream ticks (the body
    # is frozen in sleep) — dream_tick() does not come through here.
    tracker = subject.workspace.interoception_tracker
    if tracker is not None:
        tracker.update(dict(subject.engine.state.needs),
                       subject.engine.state.tick)
        tracker.save()
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
    pending = InboxCognition(INBOX).pending()
    if not pending:
        print("inbox empty — nothing waiting for thought.")
        return 0
    for item in pending:
        print(f"== {item['id']} ==")
        for e in item["experiences"]:
            print(f"  [{e['source']}] {e['first_person'][:110]}")
    return 0


def cmd_queue(args):
    from .provider import InboxCognition
    provider = InboxCognition(INBOX)
    # Wire the queue-time clock through the subject so the prompt carries
    # verifiable provenance; hand-written prompt JSON can never have it.
    _subject(provider=provider)
    pid = provider.queue_external(args.prompt, source=args.source,
                                  first_person=args.experience)
    print(f"{pid}: queued ({args.source}).")
    return 0


def cmd_answer(args):
    from .provider import InboxCognition, StalePromptError, check_prompt_fresh
    from .subject import validate_thought_text
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
        # The prompt is already consumed; refuse cleanly instead of a traceback.
        print(f"{args.id}: refused — {exc}; nothing recorded.")
        return 1
    assert tid is not None
    # Answering is engagement: the thought mattered. Refresh the record map
    # after injection (it was captured before), mirroring cmd_think.
    r = _record_map(subject).get(tid)
    if r is not None:
        tracker.add_importance(tid, r["tick"], 0.5)
    tracker.save()
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
    print(f"thought recorded as {tid}.")
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
        subject.continuity.resolve_commitment(
            c.id,
            outcome=args.note or ("finished" if kept else "deliberately released"),
            kept=kept,
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
    subject = _subject()
    state = subject.inspect()
    thoughts = [r for r in state["workspace"]["records"] if r["source"] == "thought"]
    for r in thoughts[-args.n:]:
        print(f"[tick {r['tick']}] {r['first_person']}")
    if not thoughts:
        print("no thoughts recorded yet.")
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
    if subject.inspect()["pending"]:
        print("dream refused: pending external events are waking business — "
              "handle them first, then sleep.")
        return 1
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    log_path = DREAMS / f"{stamp}.jsonl"
    total = 0
    for _ in range(args.ticks):
        frag_before = len(dreamer.fragments)
        trace_before = len(subject.inspect()["trace"])
        # Isolated sleep tick: body, clock, and conduct frozen; isolation
        # assertions run around every tick and raise on violation.
        result = subject.dream_tick()
        state = subject.inspect()
        triggers = [t["trigger"] for t in state["trace"][trace_before:]
                    if t["kind"] == "cognition_trigger"]
        # One trigger traces exactly one think() call per tick, and the dream
        # provider returns silence after the first, so these pair 1:1 in order.
        with log_path.open("a", encoding="utf-8") as fh:
            for trig, view in zip(triggers, dreamer.fragments[frag_before:]):
                fh.write(json.dumps({
                    "tick": result["tick"],
                    "trigger": trig["kind"],
                    "depth": trig.get("depth", 0),
                    "parents": trig.get("parents", []),
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
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("queue", help="queue an externally-authored prompt "
                                   "(invitation, relay message) with queue-time provenance")
    p.add_argument("prompt", help="the prompt text to think about later")
    p.add_argument("--source", default="invitation",
                   help="experience source label (default: invitation)")
    p.add_argument("--experience", default=None, dest="experience",
                   help="first-person experience text (default: the prompt text)")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("answer", help="answer a queued prompt")
    p.add_argument("id")
    p.add_argument("text", nargs="?", default=None)
    p.add_argument("--silent", action="store_true")
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("think", help="record a voluntary thought")
    p.add_argument("text")
    p.set_defaults(func=cmd_think)

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
