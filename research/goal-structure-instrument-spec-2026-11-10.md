# Spec: goal-structure instrument (Domain 17, 2026-11-10)

## What
A measurement-only module `calibos_mind/goals.py` plus a `mind goals` CLI
command plus a short goals section in `./mind drift` output, tracking the
live goal population so Domain 17's diagnostic checks become continuously
testable instead of one-off probes.

## Why
Domain 17 adversarial pass (2026-11-10, tick 1056, 1876 records): the entire
goal machinery is unexercised in vivo. Zero open commitments (2 total, both
kept CLI test commitments), zero open expectations (21 total, all confirmed
`inbox:pr`), engine concerns `{}` empty, `unresolved` empty, zero
`promise_made` events in 1056 ticks / ~47 days. Behavioural mechanisms
(dormancy, shielding, wish-to-plan staging) would be dead code against a
goal population of zero. Measurement first, following the personality-drift
instrument precedent (2026-11-09): make the gap visible and tracked, then
decide on behaviour from evidence. Nothing here installs a goal, an
aspiration, a quirk, or any goal content as theater.

## Components
1. `calibos_mind/goals.py`:
   - `observe(subject, tick)`: called from the waking-tick path only, at the
     same hook as the personality instrument's `observe()` (mirror that
     call site; do not invent a new hook). Records a per-tick snapshot into
     sidecar `goals.json`: open commitment count, oldest open commitment age
     in ticks (null when none open), counts by commitment status
     (open/overdue/kept/broken/released), open expectation count, counts by
     expectation status (pending/confirmed/violated/expired).
   - New-goal detection: diff the commitment id set against the ids already
     known to the sidecar; record newly-appeared ids with actor and
     description. A new commitment whose actor is not `"self"` is recorded
     as a candidate accidentally-formed goal (the frozen engine creates such
     commitments only from `promise_made` events or event metadata,
     continuity.py:384,413; the CLI `mind commit` path always sets actor
     `"self"`). A new commitment with actor `"self"` is recorded as
     authored. Never infer formation from text content.
   - Tension span: longest run of consecutive observed ticks with >= 1 open
     commitment, and the current run. This is the tracked form of "no
     unresolved tension carried across days".
   - Sidecar `goals.json`: bounded history, cap 100 rows like the
     personality sidecar (oldest dropped). Deterministic serialization.
   - `report(subject, sidecar)`: pure read of live state + sidecar, returns
     the counts, oldest-open age, current/longest tension span, newly-formed
     goals since last report, expectation outcome tallies.
2. CLI: `mind goals` prints the report. `./mind drift` gains a short goals
   section (open counts, oldest-open age, tension span), read-only.
3. Wiring rules: `observe()` runs on waking ticks only. Never from dream
   ticks, never from read-only commands (`drift`, `status`, `review`,
   `inbox`, `goals` itself, `think`, `dream` command) — those must leave
   `goals.json` byte-identical. `init --force` wipes the sidecar (extend
   the conftest complete path set; `tests/test_sidecar_guard.py` will fail
   otherwise — that failure is the guard working, fix the fixture set).
   Corrupt sidecar fails closed (ValueError before the tick advances, bytes
   untouched), same as the personality instrument.
4. No frozen-engine change, no cartridge change (both byte-identical).

## Fitness function (synthetic /tmp stores only, never the live store)
- Stores with 0/1/3 open commitments in mixed statuses (open/overdue/kept/
  broken/released) report exact counts; oldest-open age exact; a commitment
  due within 2 ticks is flagged due-soon, consistent with
  `derive_continuity_influence`'s window.
- A commitment appearing between observations with actor != `"self"` is
  recorded as a candidate formed goal; actor `"self"` as authored.
- Tension span: an open goal held across K observed ticks reports span K;
  closing all goals resets the current span to 0 while the longest span is
  retained.
- Read-only sweep (drift, status, review, inbox, goals, think, dream
  command) leaves `goals.json` byte-identical.
- `init --force` wipes the sidecar including a corrupt one; corrupt sidecar
  fails closed before the tick advances.
- Determinism: byte-identical sidecars on replay; full suite green.

## Revert signal
- Report shows a nonzero open count when the store has none, or misses an
  open commitment present in the store.
- Sidecar written by a read-only command or a dream tick.
- Sidecar surviving `init --force`.
- Any existing test regresses.
- In vivo: instrument reports tension span > 0 while zero open commitments
  exist at every observed tick.

## assess_after
2026-11-24 (14-day window, matching the personality instrument).

## Process
Follow `research/builder-critic-loop.md` (read it first, including the
regression genome — check every item). Mirror `calibos_mind/personality.py`
as the pattern for module shape, sidecar discipline, and the observe hook.
Tests on synthetic /tmp stores only, never the live store. Dated CHANGELOG.md
entry. No commit (the backup job handles that).
