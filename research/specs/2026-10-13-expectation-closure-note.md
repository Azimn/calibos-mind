# Spec: expectation-closure note (2026-10-13)

## What
When `calibos_mind/inbox_expectations.sync` confirms an expectation with outcome
`"lapsed"` AND the expectation's confidence is at the floor (0.15 — sustained
neglect, streak N where 0.6*0.8^N <= 0.15), mint exactly one temporal record
recording the plan's death: expectation id, proposition summary, created_tick,
due_tick, final streak/confidence, outcome "lapsed".

## Why
Domain 18 (Prospection), checks 14/1 — "updating plans as circumstances change"
and "prospective memory". The current machinery registers future obligations,
resurfaces them while open, and confirms them when they die — but plan-death
is recorded only as a status-field tombstone (`status: confirmed, outcome:
lapsed`). Future cognition never encounters its own unfulfilled plans as
memories: there is nothing to rehearse, nothing to learn from. A lapsed
expectation is invisible to retrieval — only its corpse is. The closure note
makes an unfulfilled plan a retrievable first-person memory ("I owed an answer
to prompt-0236; it expired unanswered after repeated reminders") so the mind
can prospect over its own failures rather than only its pending business.

This is mechanism, not theater: records must exist for cognition to encounter
them. The floor gate keeps the signal rare (plans that died after sustained
neglect, not quick misses) — the same discipline as habit-stale-note's
episode guard.

## Design rules (same as habit-stale-note)
- One note per expectation, ever: a `closed_expectations` set (or equivalent
  persistent mark) guards against duplicates across syncs.
- Mint path is the sync confirm-lapsed path only — never from read-only
  commands, dream ticks, or replay.
- Byte-identical note text on identical sequences (determinism).
- `init --force` wipes the guard state.
- No changes to the frozen engine or the venv; wrapper layer only.
- No commit from the builder; dated CHANGELOG.md entry required.

## Fitness function (all on synthetic /tmp stores, never the live store)
1. Expectation at floor confidence (streak >= 7, confidence 0.15) lapses via
   sync → exactly one temporal record; expectation id, outcome "lapsed",
   created_tick, due_tick, final streak/confidence recoverable from the
   record text.
2. Lapse above floor confidence → zero notes.
3. Confirm with outcome "answered" / "let-pass" / "refused-stale-view" /
   "superseded" → zero notes.
4. Second sync after lapse → zero further notes (guard holds).
5. Byte-identical store state on identical sequences (determinism).
6. Read-only command sweep (drift, status, review, inbox) never mints;
   dream ticks never mint.
7. `init --force` wipes the guard state.
8. Full existing test suite green.

## Revert signal
- Note mints for a non-lapsed outcome, or for a lapse above floor confidence.
- Duplicate notes for one expectation.
- Note minted from a read-only command or a dream tick.
- Non-determinism in note text or guard state.
- Any existing test regresses.
- In vivo: notes on >5% of confirmed expectations in any 100-tick window
  (noise, not signal).

## assess_after
2026-10-27

## Revert procedure
Delete the minting code + guard state (one module or one clearly-marked block
in inbox_expectations.py, author's choice), remove the CHANGELOG entry's
shipped line (keep a reverted line), full suite green.
