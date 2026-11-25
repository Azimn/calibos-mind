# Spec: loss ledger (`mind stakes`) — Article 31 follow-up

Date: 2026-11-24
Domain: 31. Vulnerability and Stakes (V4 working manuscript)
Author: daily articles review (design lead)

## What changes

A new tracker, `calibos_mind/stakes.py`, plus a `mind stakes` CLI command,
recording documented losses and surfacing current at-risk stakes. No conduct
change, no gate change, no cartridge change: measurement and surfacing only,
in the established pattern of the relationships and goals instruments.

### Losses recorded (sidecar `stakes.json`, append-only rows)

1. **Habit bypass loss.** A `formed:` habit whose strength was at or above the
   bypass threshold (0.65, the threshold at which a formed habit fires
   within-channel bypassing deliberation) at the last observation and is now
   below 0.65: one row naming the habit, the tick, and the before/after
   strengths. A habit already below 0.65 when first seen mints nothing (no
   retroactive fabrication: the ledger never backfills history). A habit that
   recovers to >= 0.65 and later decays below it again mints a second row;
   the latch is per-episode, not per-habit.
2. **Broken commitment.** A commitment resolving to status `broken` (engine
   `resolve_commitment(kept=False)`, i.e. a genuine breakage, never a
   deliberate release): one row naming the commitment id, description,
   resolving tick, and outcome text. Kept and released resolutions mint
   nothing.

### Current stakes (derived live at report time, never stored)

- Formed habits at or above 0.65 (bypass active: what disuse would cost).
- Open commitments (open/overdue) with due tick where present.
- Counts of losses to date, by kind.

### `mind stakes` report (read-only)

Prints current stakes, losses to date (most recent first, capped at a stated
limit), and counts. On a store with no losses it reports "no losses recorded"
explicitly — never a quiet zero. On a fresh store it reports undefined/empty
state, never invented rows, and mints no sidecar.

## Why

Article 31 requires that a represented consequence change later options "in a
documented way." Today the mind's only losses are passive decay (habit disuse,
salience sinking), and the one genuine bypass loss on record — the hunger
habit falling 0.70 to 0.45 across ~524 silent ticks — exists only as a number
in `mind drift`, with no event marking when it lost bypass or what that cost.
The ledger makes loss an event in the mind's history and gives future Article
31 verdicts evidence to score. It does not manufacture stakes: it documents
the losses that already happen, and surfaces what is currently at risk of
being lost. A breakage-consequence design (what a broken commitment should
cost in later organization) is deliberately out of scope here: it changes
conduct and needs Jay's decision on the cost semantics.

## Observation point

Waking ticks only, via the same observation hook the other trackers use
(followthrough / authored-dormancy `observe_tick` pattern). Read-only commands
(`mind stakes`, `mind drift`, `mind status`, `mind goals`) and dream ticks
never write the sidecar and never mint rows. `mind init --force` wipes the
sidecar along with the rest of the store state.

## Fitness function

Synthetic stores only (never the live store); tests in `tests/` following the
existing tracker test pattern:

- Habit seeded at 0.70, decaying across 0.65 in one observed tick -> exactly
  one loss row (habit name, tick, before/after strengths exact); the next
  tick with no further crossing mints nothing.
- Habit recovering to >= 0.65 then decaying below it again -> a second row;
  recovery alone mints nothing.
- Habit first seen already below 0.65 -> no row, baseline recorded.
- Habit at exactly 0.65 stays above the loss line (no row); 0.649... mints
  only if the previous observation was >= 0.65.
- Broken commitment -> exactly one row (id, description, tick, outcome);
  kept and released resolutions mint nothing; a second broken commitment mints
  a second row; the same breakage observed twice mints one row.
- No losses -> report prints "no losses recorded" (or equivalent explicit
  wording) with counts 0; fresh store -> no sidecar minted by the report.
- Read-only sweep (`mind stakes`, `mind drift`, `mind status`, `mind goals`)
  leaves the sidecar byte-identical; dream ticks leave it byte-identical.
- `init --force` wipes the sidecar (including a pending/corrupt one);
  corrupt sidecar shapes fail loud before the tick advances.
- Determinism: identical tick sequences -> byte-identical sidecars.
- Full suite green; engine + cartridge byte-identical.

## Revert signal

- A loss row for a habit that never crossed below 0.65 from above, or a
  duplicate row for one crossing.
- A row for a commitment that resolved kept or released, or a duplicate row
  for one breakage.
- Any write from a read-only or dream path; sidecar surviving `init --force`.
- Any existing test regresses.
- In vivo (assessor's note): a row minting for a habit the waker judges never
  lost bypass is a data problem (adjudicate the row), not a mechanism revert;
  no rows minting across the window while habits visibly decay is a dead
  instrument, judged at assessment, not an auto-revert.

## Assess after

2026-12-08
