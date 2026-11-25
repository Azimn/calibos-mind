"""Resident visit mode: the inhabitant chooses.

Each step shows the scene as it is, plus the proposer's shortlist of up
to three actions, and the resident picks one — by number, or by typing an
action of their own. The visit ends when the resident types `leave`.
There is no fixed visit length and no real-time correlation: sim minutes
are sim minutes.

Command language (one per line, interactive or from a script file):
    1 | 2 | 3            pick that action from the shortlist
    look
    move study
    quiet 20             quiet, sit, and watch_fish take minutes (1..20)
    touch blanket        touch, smell, taste, listen take a target
    wear_outfit waistcoat
    wear_item satchel
    remove_item waist
    name_fish Pip
    bookmark first real visit
    write_note The fish is Pip now.
    feed_fish
    write_diary Today the rain kept coming.
    read_diary
    take_book low-tide   take_book takes an optional book id
    leave
"""
from __future__ import annotations

import sys

from . import rhythm
from .tick import Home

CHOICE_OPS = {
    "move": ("to",),
    "wear_outfit": ("look_id",),
    "wear_item": ("item_id",),
    "remove_item": ("slot",),
    "name_fish": ("name",),
    "bookmark": ("name",),
    "write_note": ("text",),
    "write_diary": ("text",),
    "take_book": ("book_id",),
    "put_book": ("book_id",),
}

MINUTES_OPS = ("quiet", "sit", "watch_fish")
SENSE_OPS = ("touch", "smell", "taste", "listen")


def parse_command(line: str) -> tuple[str, dict] | None:
    """Parse one visit command line. Returns (op, args), or None for a
    blank line or a comment."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line == "leave":
        return ("leave", {})
    parts = line.split(None, 1)
    op = parts[0].strip().lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if op in MINUTES_OPS:
        args: dict = {}
        if rest:
            try:
                args["minutes"] = int(rest)
            except ValueError:
                raise ValueError(f"{op} takes minutes, got {rest!r}")
        return (op, args)
    if op in CHOICE_OPS:
        key = CHOICE_OPS[op][0]
        if not rest:
            return (op, {})
        return (op, {key: rest})
    if op in SENSE_OPS:
        return (op, {"target": rest} if rest else {})
    if op in rhythm.ALL_OPS:
        return (op, {})
    raise ValueError(f"unknown visit command: {line!r}")


def shortlist_lines(home: Home) -> tuple[list[str], list[tuple[str, dict]]]:
    """The proposer's current shortlist: display lines and the actions
    themselves, from the same proposal, so a numbered pick always means
    the action that was shown.

    At night the garden is not offered: it is dark out there, and the
    shortlist is a suggestion, not a dare. Daytime visits (past 06:00
    sim time) offer it again. The resident can always type "move garden"
    freely; this only keeps the suggestion honest.
    """
    shortlist, _chosen = home.proposer.propose(home.state, night=False)
    if not rhythm._daylight(home.state["minute"]):
        shortlist = [(op, args) for op, args in shortlist
                     if not (op == "move" and args.get("to") == "garden")]
    lines = []
    for i, (op, args) in enumerate(shortlist, 1):
        if op == "move":
            lines.append(f"{i}. move to {args.get('to', '?')}")
        elif args:
            detail = " ".join(f"{k}={v}" for k, v in args.items())
            lines.append(f"{i}. {op} {detail}")
        else:
            lines.append(f"{i}. {op}")
    return lines, shortlist


def run_visit(home: Home, commands: list[str] | None = None,
              out=None) -> list[str]:
    """Run one resident visit. `commands` is a scripted list of command
    lines; when None, the resident types at the prompt. Returns the log
    lines shown. The visit ends on `leave`."""
    out = out or sys.stdout
    log: list[str] = []

    def show(text: str) -> None:
        log.append(text)
        print(text, file=out)

    home.resolve_weather(for_night=False)
    home.apply_overrides(for_night=False)
    show(rhythm.scene_text(home.state, home.active_overrides))
    show("")
    interactive = commands is None
    script = list(commands or [])
    while True:
        options, shortlist = shortlist_lines(home)
        show("The house suggests:")
        for line in options:
            show("  " + line)
        if interactive:
            try:
                raw = input("What do you do? ").strip()
            except EOFError:
                raw = "leave"
        else:
            if not script:
                raw = "leave"
            else:
                raw = script.pop(0).strip()
                show(f"> {raw}")
        if raw in ("1", "2", "3"):
            idx = int(raw) - 1
            if idx >= len(shortlist):
                show("That is not one of the suggestions.")
                continue
            chosen = shortlist[idx]
        else:
            try:
                parsed = parse_command(raw)
            except ValueError as exc:
                show(str(exc))
                continue
            if parsed is None:
                continue
            chosen = parsed
        op, args = chosen
        if op == "leave":
            show("I left the house.")
            break
        try:
            record = home.do(op, args)
        except rhythm.HomeError as exc:
            show(f"That does not work here: {exc}")
            continue
        show("")
        show(record["text"])
        show("")
        show(rhythm.scene_text(home.state, home.active_overrides))
        show("")
    return log
