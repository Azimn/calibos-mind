# Spec: relationship contact-gap instrument (2026-11-19)

## What
A read-only reporting instrument that makes relationship staleness visible:
`mind relationships` (new read-only subcommand) reports, for every
relationship in the live store, the person id, ticks since last contact
(current tick minus `last_contact_tick`), familiarity, and a staleness flag
when the gap exceeds a fixed threshold. The report is computed from the
store on every invocation. **No new sidecar, no new stored state, no
writes of any kind** — this is an instrument in the style of `mind drift`,
not a mechanism. It does not change relationships, retrieval, salience,
conduct, or any other behavior.

## Why
Domain 26 (State reset artifacts), check 4 — "relationship tension
vanishes after enough turns" — scored ABSENT on 2026-11-19, and the
finding is worse than the check states: no tension state exists at all.
All 3 live relationships carry identical template values (trust 0.32,
comfort 0.28, respect 0.35, attachment 0.12, familiarity 0.055,
uncertainty 1.0), differing only in `last_contact_tick` (143 / 533 /
670 at tick 1199) and tiny interest jitter. Last contact is up to 1056
ticks stale with no consequence anywhere in the system, and no
relationship-update code exists in `calibos_mind/` — relationships are
engine-owned (frozen engine), so the wrapper-layer move is to instrument
first: make the gap measurable so a later assessment can judge whether
anything should respond to it. Tension itself must emerge from
interaction history, not be scripted — this instrument installs no
tension, no scripted staleness penalty, no behavior change.

## Fitness function
On synthetic stores (never the live store):
- Exact gap arithmetic: relationship with `last_contact_tick` T at tick
  N reports gap exactly N - T; a relationship contacted at the current
  tick reports 0.
- Staleness flag: gap > STALE_AFTER (choose a named constant; default
  500 ticks, justified in the spec note below) flags stale; gap exactly
  500 does not; gap 501 does. (500 is the scale of the live gap that
  currently passes unnoticed: the live stale relationship sits at 1056.)
- Every relationship in the store appears exactly once, in a stable
  order (sorted by person id); an empty relationships dict reports
  "no relationships" (undefined, never a quiet zero or a crash).
- Missing `last_contact_tick` on a relationship reports gap as
  undefined for that row (never a quiet zero, never a crash) and does
  not flag it stale.
- Non-numeric / malformed relationship values fail loud (raise before
  printing a partial report) — no silent defaults.
- Read-only: invoking the command leaves the store payload byte-identical
  and creates no sidecar file (verified by the same read-only sweep
  discipline as the drift instrument).
- Determinism: identical store invoked twice yields byte-identical output.
- `mind relationships` appears in `--help` alongside the other read-only
  reporting commands; full suite green.

## Revert signal
- A report shows a gap that does not equal tick minus
  `last_contact_tick` for a seeded store, or omits / duplicates a
  relationship.
- A stale flag fires at or below the threshold, or fails to fire above it.
- Any write from the command (store payload changes, sidecar created).
- Any existing test regresses.
- In vivo (assessor's note): the instrument is inert, not a revert
  trigger, if no relationship ever crosses the threshold; a report that
  mislabels the live store's known gaps (143/533/670 at tick 1199) is a
  data problem to adjudicate, not a mechanism revert.

## Assess after
2026-12-03 (two weeks, same window as the other instruments).

## Notes for the builder
- Read the regression genome in `research/builder-critic-loop.md`
  before writing code; the critic will check it item by item.
- Tests run on synthetic `/tmp` stores, never the live store. Follow
  the existing conftest fixture discipline (complete path set redirect;
  see the 2026-10-22 CHANGELOG entry and `tests/test_sidecar_guard.py` —
  a new command must not introduce an uncovered store path).
- Do not touch the frozen engine or the cartridge. Do not add a sidecar.
- Dated CHANGELOG.md entry; no commit (the backup job handles that).
