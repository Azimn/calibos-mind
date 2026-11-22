# Spec: authored-dormancy lost-latch recovery

Date: 2026-11-21. Domain 28 (Lack of path dependence) adversarial pass, cycle 2.
Parent mechanism: `calibos_mind/authored_dormancy.py` (domain 27, 2026-10-21),
wired in `calibos_mind/cli.py::_run_tick`.

## The defect

The live store is in a state the existing code cannot leave: both authored
habits sit at live strength exactly 0.64 (demoted), the store holds both
demotion notes (`experience-1137` curious_question and `experience-1138`
verify_before_claiming, both tick 735, parseable by the existing
`parse_demotion_note_text`), but `authored-dormancy.json` says
`demoted: false, demoted_tick: null` for both.

Root cause: the sidecar file was lost in the harness-defect era and
re-seeded from the live firing stamps (`last_fired_tick` 7 and -10000).
The demotion edge in `observe_tick` requires `live.strength >=
HABIT_FIRE_STRENGTH` (0.65) to fire, so at 0.64 it can never re-fire.
The latch is unrecoverable by the existing code, and the sidecar now
contradicts the store and the engine. No behavioral consequence today
(0.64 is below the bypass threshold and no restore path exists), but the
audit trail for a one-way transition — the domain-28 evidence base — is
broken, and any future code that trusts the latch will be wrong.

## What changes

Add a lost-latch recovery to `AuthoredDormancyTracker`, waking-tick-only,
run from `cli._run_tick` before (or as the first step of) `observe_tick`:

1. For each authored habit key (toml baselines, same source of truth as
   `observe_tick`) whose live strength is exactly `DEMOTED_STRENGTH`
   (0.64) while the sidecar entry says `demoted: False` (including a
   freshly seeded entry), search the store's records for that habit's
   demotion note, parsed by the existing `parse_demotion_note_text`.
   The caller passes the records (or the parsed notes); the tracker never
   opens the store itself. The exact seam is the builder's choice, but
   the recovery must receive the records from `_run_tick`, where the
   subject is already available.
2. If exactly one note exists for the key: restore the latch —
   `demoted: True`, `demoted_tick` = the note's tick, `owed: None` (the
   note already exists; mint nothing, no duplicate). Validate the note
   before trusting it: its baseline must equal the toml baseline
   (rounded, same comparison `observe_tick` uses), its `demoted_tick`
   must be a sane int <= the current tick, and its `demoted_tick`
   must be more than `DORMANCY_TICKS` after the entry's
   `last_fired_tick` (the demotion edge requires tick - last_fired >
   300, so a demotion at or before that bound contradicts the
   engine's own firing stamp; a missing entry is judged on the clock
   `observe_tick` would seed for it). A note is trusted only from a
   record stamped source "temporal" and generated_by "cognition" —
   the same trust boundary as `mint_demotion_note`'s idempotency
   check — so a pasted canonical note text in a message record, or a
   temporal record stamped by another author, never latches. Save the
   sidecar when changed. Live strength is already 0.64; write nothing
   to the store. The existing `reconcile_state_habits` then converges
   on its own (a demoted entry already targets 0.64).
3. If live strength is exactly 0.64 with `demoted: False` and NO note
   exists in the store: fail loud (`ValueError`, before the tick
   advances and before any mint, sidecar bytes untouched). Strength
   0.64 can only come from a demotion, so an absent note means the
   audit trail is broken, not that the habit is fine. This follows the
   fail-loud sidecar philosophy of the module.
4. If more than one note exists for one key (different ticks): fail
   loud the same way. The mint is idempotent on (habit_key,
   demoted_tick) and the ratchet fires once, so two notes for one key
   is an inconsistent state, never a second episode.
5. Recovery never mints, never changes a live strength, never touches
   the `formed:` namespace (a formed habit at 0.64 is not an authored
   key and is ignored), never runs on read-only commands or dream ticks
   (waking-tick path only, same gate as `observe_tick`), and never
   alters `last_fired_tick` (the seeded clock value stands; it is the
   dormancy clock, already proven). Deterministic: no wall clock, no RNG.
6. A pending owed mark on a candidate entry fails loud (`ValueError`,
   before any mutation, same guarantees as 3): the owed episode's mint
   has not committed, and latching from a different episode's note
   while clearing owed would silently discard that mint obligation —
   `observe_tick` would never re-emit it and nothing would ever mint
   it. Preserving owed instead is refused: it would mint a second
   demotion note for the same habit, contradicting the spec's
   one-episode ratchet. (Demoted True + owed is the normal
   crash-recovery shape and is not a candidate — recovery skips
   already-demoted entries — so this raises only on the inconsistent
   demoted False + owed state, reachable by a sidecar edit or a
   future writer bug.)
7. Recovery is idempotent: a second tick after a successful recovery
   changes nothing (sidecar byte-identical).

## What does NOT change

- The demotion edge itself (`observe_tick`): thresholds, ratchet, mint
  discipline, crash write-ahead, and `reconcile_state_habits` are
  untouched. A habit at baseline strength with no note still demotes
  only through the normal edge after 300 dormant ticks, with exactly
  one note.
- No restore path is added. Recovery restores the *latch*, never a
  strength: a demoted habit stays at 0.64.
- No new sidecar, no new schedule, no cartridge change.

## Fitness function (synthetic /tmp stores only, never the live store)

- Lost latch, note present: authored habit at 0.64, sidecar
  `demoted: False`, store holds the note minted by `demotion_note_text`
  for that key/tick → one waking tick restores `demoted: True`,
  `demoted_tick` = the note's tick, `owed: None`; no new record is
  minted; live strength stays 0.64; a second tick leaves the sidecar
  byte-identical.
- Both live habits: the exact live shape (curious_question 0.72/735 and
  verify_before_claiming 0.68/735, sidecar `demoted: False` for both)
  recovers both latches in one tick with no mint.
- Fresh sidecar: no sidecar file at all, habit at 0.64, note in store →
  the seeded entry recovers in the same tick (no second tick needed).
- No note: habit at 0.64, `demoted: False`, no note in store → the
  waking tick raises `ValueError` before any mint and before the tick
  advances; sidecar bytes untouched; no record appended.
- Multiple notes: two notes for one key (different ticks) →
  `ValueError`, same guarantees.
- Note validation: note baseline != toml baseline, or note
  `demoted_tick` > current tick, or note `demoted_tick` not more than
  `DORMANCY_TICKS` after the entry's `last_fired_tick` (boundary: 300
  fails, 301 recovers) → `ValueError`, same guarantees.
- Note trust boundary: a canonical note text in a message record, or
  in a temporal record stamped by another author, is not trusted →
  `ValueError` (no trusted note), same guarantees.
- Pending owed: entry `demoted: False` with a pending `owed` mark,
  note for a different episode present → `ValueError` before any
  mutation; sidecar bytes and in-memory state untouched, the owed mark
  intact, and `observe_tick` still re-emits the owed episode so its
  note can be minted; end to end, the waking tick raises before the
  tick advances and before any mint.
- Normal paths unchanged: habit at baseline 0.72, `demoted: False`,
  no note → no recovery, and the normal demotion edge still fires
  after 300 dormant ticks with exactly one note; a habit already
  `demoted: True` stays latched; a proven firing pre-demotion still
  resets the clock silently.
- `formed:` namespace untouched: a formed habit at 0.64 is ignored by
  recovery (no entry created, no error).
- Read-only sweep (drift, status, review, inbox, wake briefing, think,
  dream command) leaves the sidecar byte-identical and performs no
  recovery; dream ticks never recover. `mind init --force` wipes the
  sidecar (existing behavior).
- Determinism: identical tick sequences produce byte-identical sidecars.
- Full suite green.

## Revert signal

- A recovery mints a record, changes a live strength, or sets
  `demoted: True` for a habit with no note in the store.
- A recovery clears a pending owed mark without its mint having
  committed (critic round 1, FAIL 1), or latches from a note whose
  provenance is not source temporal + generated_by cognition, or
  from a note whose demoted_tick is not more than DORMANCY_TICKS
  after the entry's last_fired_tick.
- A recovery fires from a read-only or dream path.
- Any existing test regresses.
- In vivo (assessor's note, not an auto-revert): after shipping, the
  next waking tick should heal the live sidecar (both entries
  `demoted: True`, `demoted_tick: 735`, no new record minted). If the
  live tick instead raises, that is the fail-loud path working as
  designed — adjudicate the note state, do not weaken the check.

## Assess after

2026-12-05.

## Regression genome (critic checks item by item)

Shadowing definitions; sidecar/state reset on reseed; read-only
violations (byte-identical payload content, mtime bounded by
construction — `cli._subject()` construction rewrites the payload row);
silent defaults (report undefined, never a quiet zero); unrestored
swapped config; dream/conduct isolation; negation-blind similarity;
dead regex branches; contraction tokenization hides polarity; silent
zero on missing sections; negative-activation collapse;
contraction-negation blindness; test-premise drift (prove a red test's
premise against measured behavior before calling it a code defect);
CLI construction is never mtime-silent.

Harness discipline (2026-11-08 / 2026-11-15 incidents): tests run under
pytest with the conftest fixture only. Never run test helpers or
standalone probes outside pytest; never call `cmd_init` (or any
destructive path) without the complete redirect set — route through
`tests/conftest.py::redirect_all_cli_paths`. A standalone probe that
touches the live store is a live public write, not a test.
