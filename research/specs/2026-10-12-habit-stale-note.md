# Spec — habit-stale-note (2026-10-12)

Domain 17 (Goal structure), checks 7/8. Builder/critic loop contract.

## What

When a FORMED habit (the `formed:<trigger>:<action>` namespace in
`calibos_mind/habits.py`) fires by bypass (>= 0.65 strength, the tick's
(trigger, action) matches the habit's) and its trigger-relevant need does
NOT improve (per-tick delta >= 0) for **5 consecutive firings**, mint
exactly one temporal record noting the failure. Record-only: no
auto-removal, no strength change. One note per episode; an improving
firing (delta < 0) resets the streak and re-arms. Authored habits are
never touched.

## Why

Domain 17 check 8 ("no repeated failed attempts") is absent — the system
cannot represent a routine that is not working. In-vivo historical
evidence: record experience-671 (tick 519) observed hunger rising
+0.002/tick while formed:hunger:seek_contact kept firing, and only feeder
food events satiate hunger — a real repeated-failed-attempt that went
unrepresented. Check 7 (abandoned projects) stays record-only, as
designed: the note names the failure, it does not fix it.

## Where

Wrapper layer only (venv immutable). Extend `calibos_mind/habits.py`
(single module — the revert path is deleting one cli block plus the new
module sections). Per-habit streak state lives in the habits-formed.json
sidecar under a new top-level key `stale_streaks`, subject to the same
load/validation/reseed discipline as the rest of the sidecar
(strict-load raises on a malformed key; missing key = fresh start;
`init --force` unlinks the file, wiping the key with it).

## Detailed design

### Bypass definition

Mirrors `counterfactual._fired_habit` exactly: a formed habit with
sidecar strength >= 0.65 (`HABIT_FIRE_STRENGTH`, imported from
`counterfactual.py` — not redefined, per the shadowing-definitions
genome item), tick (trigger, action) == the habit's
(`formed:<trigger>:<action>` key match), AND the engine's own
`last_used_tick == tick` stamp on the live `state.habits` entry. The
stamp is the read-only proof the bypass branch fired this tick — the
deliberated path never writes it. Checking only the (trigger, action)
match without the stamp would misattribute ticks where deliberation
chose the same action while the habit sat in cooldown.

### Trigger-relevant delta

Same rule as the formation window row (`observe_tick`): the trigger's
own per-tick need delta for need triggers, the dominant need's delta
for pressure triggers. Factored into one helper used by both paths so
the definition cannot drift.

### Streak state machine (per formed-habit key)

Entry: `{"count", "start_tick", "last_tick", "last_delta",
"trigger_need", "owed", "noted_start"}`.

- Non-improving bypass firing (delta >= 0): count += 1 (start_tick is
  set on the 0→1 transition, so it names the first *counted* firing of
  the streak). At count == 5 (first time): set `owed` to the episode's
  mint event (the write-ahead mark) and emit it.
- Improving bypass firing (delta < 0): reset count/last/delta,
  re-arm. start_tick is left stale deliberately and re-set on the next
  counted firing. An `owed` episode from a crashed tick is preserved
  untouched — the owed dict carries its own episode identity, so a
  reset landing between crash and recovery cannot orphan it.
- Non-firing ticks: streak untouched (pauses, neither grows nor resets —
  "consecutive firings", not "consecutive ticks").
- Habit removed (disuse decay): its streak entry is pruned (self-healing,
  same as reconcile's stale-key deletion).

### Mint path (interoceptive-realization precedent)

- Temporal source, `generated_by="cognition"`, through
  `subject._add` inside the tick's transaction (a write inside the
  existing tick, never a new tick).
- Crash discipline mirrors the realization write-ahead: the `owed`
  episode dict is saved to the sidecar BEFORE the mint transaction;
  `mint_stale_note` is idempotent on (habit_key, start_tick) — a
  pre-mint crash re-mints on the next tick (no loss), a post-mint crash
  re-mints idempotently (no duplicate); `ack_stale_notes` latches
  `noted_start` and clears `owed` after the mint commits.
- Canonical text, parseable by fullmatch regex (serialization-identity
  discipline, cf. `_REALIZATION_RE`). Deviations from the draft example
  text, all deliberate:
  - No contractions ("I have" / "has not" / "is not" instead of "I've" /
    "isn't"): the contraction-negation genome items — similarity math
    sees token difference, never polarity.
  - ASCII only (semicolon, not the em-dash).
  - A second machine sentence carries every field the fitness function
    needs to recover the episode from the record alone:
    (habit_key, consecutive_firings, trigger_need, last_delta as
    fixed-format `+0.002000`, start/end ticks).

### Wiring (`cli._run_tick`, waking ticks only)

After `observe_tick` and its save, before the reconcile transaction
(the bypass stamp is read from live engine state — authoritative; it
survives the payload round-trip, but live is preferred), then mint +
ack. Dream ticks never reach `_run_tick`; read-only commands never
tick — neither can mint or touch streak state.

## Fitness function (all on synthetic /tmp stores)

1. Scripted hunger ticks (seek_contact, hunger delta +0.002) with a
   seeded formed:hunger:seek_contact @ 0.65: 5 consecutive bypass
   firings → exactly one temporal record with (habit_key,
   consecutive_firings, trigger_need, last_delta, start/end tick)
   recoverable from the text.
2. 6th and 7th consecutive non-improving firings → no second note.
3. An improving firing (feeder food event, hunger delta < 0) resets;
   5 more non-improving firings → a second note (new episode,
   new start_tick).
4. Control: fear script (energy delta < 0) → 5 bypass firings,
   zero notes.
5. Sub-threshold: 4 non-improving firings → zero notes.
6. Improving on the would-be 5th firing → zero notes.
7. Determinism: identical scripted sequences → byte-identical sidecar.
8. Full existing test suite green.
9. Cartridge/engine fingerprint byte-identical (no cartridge change).
10. Read-only sweep (status, drift, inbox, recall) on a synthetic store
    never writes; dream ticks never touch streak state;
    `init --force` wipes the sidecar; malformed `stale_streaks`
    raises per the formed-map strict-load discipline.

## Revert signal

Note fires for an improving habit; duplicate notes within one episode;
any note minted from a read-only command or dream tick; any existing
test regresses; non-determinism in the sidecar or the note text.

## assess_after

2026-10-26.

## Out of scope (noted, not built)

- Consolidation interplay: two episodes of one habit produce similar
  texts; the near-dup pass may flag them. Left to the consolidation
  passes (a missed consolidation is the safe direction).
- No strength/removal coupling: check 7 stays record-only by design.
