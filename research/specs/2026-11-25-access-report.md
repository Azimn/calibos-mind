# Spec: access report instrument (`mind access`) — 2026-11-25

## Why

Article 32 (Opacity and Cognitive Privacy) daily pass, 2026-11-25: the
verdict is Partial, unchanged, but establishing it took four ad-hoc
probes (a store scan, a code grep, a git check, a config hunt). The
access facts the article turns on — what the thinker can actually see,
what is genuinely unavailable, what the view boundary is — are scattered
across the store, the workspace code, and the engine config, and no
single command reports them. This instrument makes the Article 32 state
checkable in one read-only command, and it surfaces the moment any
genuine unavailability appears: today the availability gate is real code
but vacuous in vivo (available_to_cognition True on all 2383 records,
inner_ear never overridden from its True default, exactly 1 record
unavailable and only via the archive exclusion). The instrument is a
reporter, not a claim: it must never present a number it did not
measure, and it must never write anything.

## What changes

- New module `calibos_mind/access.py` with a pure report function over a
  loaded store (no sidecar, no tracker, no state of its own).
- New read-only CLI command `mind access`, wired like `mind drift` /
  `mind relationships` / `mind stakes` (read-only; listed in `--help`).
- Dated CHANGELOG.md entry. No commit.

## Report contents (all measured from the live store + code constants)

- `VIEW_LIMIT` and `MAX_PINNED` as defined in `calibos_mind/workspace.py`.
- Total records in `workspace.records`.
- Available to cognition: count with `available_to_cognition` True.
- Unavailable, broken down by cause: `available_to_cognition` False vs
  archived-excluded (via `archive/availability.json` when present; 0 and
  "no availability file" when absent — never a quiet zero).
- Pinned (cartridge-authored, eligible) count.
- Current view composition: pinned count + ranked room
  (`VIEW_LIMIT - pinned`, floored at the pinned cap the same way
  `workspace.view()` does it), i.e. how many records the thinker is
  actually shown.
- `inner_ear` setting from the live config.
- Over-access figure: store total vs view size ("view shows N of M
  records") — the Article 32 reverse-direction number, stated plainly.
- The report must label every number with what it counts; an undefined
  quantity (e.g. no records at all) reports "undefined", never 0.

## Fitness function (synthetic /tmp stores only, never the live store)

- Seeded store with a known mix (available / available_to_cognition False
  / archived-excluded / pinned cartridge / unpinned) reports every count
  exactly, including the by-cause unavailable breakdown and the view
  composition (pinned + room) matching `workspace.view()` semantics.
- Fresh store (0 records): defined output, view 0 of 0, no crash, no
  sidecar, "undefined" where a ratio would divide by zero.
- Missing `workspace` / `records` section raises (malformed store), never
  a quiet zero — the silent-zero genome item.
- Read-only sweep: payload content byte-identical, no sidecar minted, no
  file created (scope the assertion to payload content, per the
  CLI-construction genome item — `cli._subject()` construction rewrites
  the row, so mtime is out of scope).
- Dream ticks never touch the report path; the command works identically
  before and after a dream tick.
- Determinism: identical store → byte-identical report text on replay.
- `--help` lists `access`.
- Full suite green. Engine + cartridge byte-identical.

## Revert signal

- A report that misstates any count against a seeded store.
- Any write from the command (payload change, sidecar created, file
  created) — this command owns no state, so any write is a defect.
- Any existing test regresses.
- In vivo: a report disagreeing with a hand count is a data problem to
  adjudicate, not a mechanism revert. The instrument never being run is
  inert, not a revert trigger.

## Assess after

2026-12-09.

## Out of scope

- No change to the view, the availability gate, `inner_ear`, or the
  archive exclusion. No new unavailability is installed: staging a
  genuinely-unavailable record would be the theatrical secrecy Article 32
  rejects as evidence.
- No gitignore/backup-boundary check inside the command; that boundary
  stays a manual rotation step.
- No view-use audit (which view records an answer drew on): answers
  essentially never cite record ids, so that audit would report "no
  evidence either way" forever — a dead instrument.
