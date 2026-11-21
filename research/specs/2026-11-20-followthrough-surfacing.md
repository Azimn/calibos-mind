# Spec: realization follow-through surfacing into the view

Date: 2026-11-20
Domain: 27. Lack of developmental history, check 9 (No historical self-comparison)
Author: design lead (daily articles review)

## What changes

When a realization's follow-through row becomes measurable — `report()`
returns a defined total-variation distance for it, i.e. both the 20-tick
before-window and the 20-tick after-window hold enough action-bearing
ticks — the waking-tick path mints exactly one temporal record summarizing
the measured conduct change. The record is a normal cognition-authored
store record, so it enters salience ranking like any record and the
thinker can encounter its own measured past-vs-present comparison in a
later view.

Record text is factual only, naming: the need, swing tick, convergence
tick, felt band, actual band, the before and after action mixes, and the
tv distance. Example shape (values illustrative):

> After the thirst realization (swing 1177, convergence 1185), seeking
> contact fell from 0.35 to 0.20 and approach rose from 0.50 to 0.55;
> total-variation distance 0.15.

No interpretation, no promise, no judgment about whether the change is
good. Interpretation stays emergent at the thinker layer.

## Why

Domain 27 check 9 is partial in cycle 2 (2026-11-20): the comparison is
computed — `mind followthrough` reports it, and the first measurable row
landed today (thirst swing 1177 conv 1185, tv 0.15) — but nothing renders
it into the thinker's view. The drift report is waker-facing; realization
records state a felt/actual gap closed, not a conduct comparison. This
mutation is instrumentation of an existing measurement, the same class as
the dormancy demotion note (2026-10-21) and the contradiction-flag
surfacing (2026-11-17): a measured fact about the mind's own past conduct,
surfaced as a record. It does not script what the thinker should feel
about it, and it does not install a comparison the mind did not measure.

## Where

- `calibos_mind/followthrough.py`: a `surface_newly_measurable()` (or
  equivalent) that, given the current report, mints one summary record per
  newly-measurable row and records the realization identity in a persisted
  `surfaced` set in `followthrough.json`.
- `calibos_mind/cli.py`: call it from `_run_tick` (waking path only), after
  the existing follow-through tick feed.
- Crash discipline mirrors the dormancy demotion note
  (`authored_dormancy.py`): owed mark on the sidecar entry saved BEFORE
  the mint transaction commits; mint idempotent on the realization
  identity (need, swing tick, convergence tick); ack after commit. A kill
  between sidecar save and mint must re-mint nothing on recovery.
- The minted record goes through the same sanctioned temporal-record mint
  path the demotion note uses. Minting must not advance the engine tick
  or alter the engine trace.
- `followthrough.json` is local-only (gitignored), atomic-saved,
  fail-loud on corruption, wiped by `init --force`.

## Fitness function (all on synthetic /tmp stores, never the live store)

- Synthetic store with a realization whose before/after windows complete:
  exactly one summary record minted, naming swing tick, conv tick, need,
  felt band, actual band, tv value, and before/after mixes matching
  `report()` exactly. A second tick run mints nothing (idempotence).
- Insufficient-window rows mint nothing (before 0/20, after 20/20, and
  the swing>conv case).
- Two distinct realizations becoming measurable on the same tick mint two
  records, one per realization, no cross-talk.
- Crash-window kill between sidecar save and mint commit: exactly one
  record on recovery (idempotent mint returns the existing record id),
  no duplicates.
- Read-only sweep (`drift`, `status`, `followthrough` report, `review`,
  `inbox`, wake briefing, `think`, `dream` command) never mints and
  leaves `followthrough.json` byte-identical; dream ticks never mint.
- Determinism: identical tick sequences produce byte-identical sidecars
  and identical minted record text.
- `init --force` wipes the surfaced set (and the store, by reseed).
- Minting leaves engine tick/trace byte-identical (tick does not advance
  on a mint).
- Full suite green; engine + cartridge byte-identical (no store
  fingerprint change — pure wrapper).

## Revert signal

- A summary minted for a realization whose row is insufficient-window
  (fabrication), or two summaries for one realization.
- Summary text misstates the measured tv or mixes (mismatch vs
  `report()`).
- Any write from a read-only or dream path; sidecar surviving
  `init --force`; any existing test regresses.
- In vivo: summaries minting for rows the waker judges mismeasured is a
  data problem (adjudicate the row), not a mechanism revert; a summary
  never surfacing in any view over the window is inert, not a revert
  trigger.

## Assess after

2026-12-04
