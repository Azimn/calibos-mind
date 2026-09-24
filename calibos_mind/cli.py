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
    mind resolve <id> [--released]     close a commitment (done, or released)
    mind status                        tick, needs, open loops, inbox depth
    mind review [n]                    recent private thoughts
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from digital_subject.cartridge import load_cartridge
from digital_subject.models import Event

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "mind.db"
INBOX = BASE / "inbox"
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


def _subject():
    from .provider import InboxCognition
    from .subject import CalibosSubject
    cartridge = load_cartridge(CARTRIDGE_PATH)
    return CalibosSubject(DB, cartridge, cognition=InboxCognition(INBOX))


def cmd_init(args):
    if DB.exists() and not args.force:
        print(f"store already exists at {DB} (use --force to reseed)")
        return 1
    if args.force and DB.exists():
        DB.unlink()
    subject = _subject()
    with subject._transaction():
        for text, concept_pair in SEED_MEMORIES:
            subject._add("memory", text, concepts=concept_pair, generated_by="cartridge")
    print(f"initialized {DB}")
    print("identity + preference roots seeded and pinned.")
    return 0


def cmd_note(args):
    subject = _subject()
    tags = tuple(t.strip() for t in args.tags.split(",") if t.strip()) if args.tags else ()
    if args.kind == "message":
        subject.message(args.source, args.text)
    else:
        subject.enqueue(Event(args.kind, args.source, args.text, tags=tags, valence=args.valence))
    _run_tick(subject)
    return 0


def _run_tick(subject):
    before = len(subject.inspect()["trace"])
    result = subject.heartbeat()
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


def cmd_answer(args):
    from .provider import InboxCognition
    provider = InboxCognition(INBOX)
    payload = provider.consume(args.id)
    if args.silent:
        print(f"{args.id}: let pass (silence).")
        return 0
    if not args.text:
        print("give the thought as text, or use --silent.")
        return 1
    subject = _subject()
    tid = subject.inject_thought(args.text, trigger_kind="answered")
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
    tid = subject.inject_thought(args.text, trigger_kind="voluntary")
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
    needs = eng["needs"]
    notable = {k: round(v, 2) for k, v in needs.items()
               if abs(v - 0.5) > 0.25}
    print(f"needs (off-baseline): {notable or 'all settled'}")
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

    p = sub.add_parser("answer", help="answer a queued prompt")
    p.add_argument("id")
    p.add_argument("text", nargs="?", default=None)
    p.add_argument("--silent", action="store_true")
    p.set_defaults(func=cmd_answer)

    p = sub.add_parser("think", help="record a voluntary thought")
    p.add_argument("text")
    p.set_defaults(func=cmd_think)

    p = sub.add_parser("resolve", help="close a commitment as done or released")
    p.add_argument("id", help="commitment id (or unique prefix / description match)")
    p.add_argument("--released", action="store_true",
                   help="release it instead of marking it done")
    p.add_argument("--note", default="", help="outcome note for the record")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("status", help="what is on my mind")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("review", help="recent private thoughts")
    p.add_argument("n", type=int, nargs="?", default=5)
    p.set_defaults(func=cmd_review)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
