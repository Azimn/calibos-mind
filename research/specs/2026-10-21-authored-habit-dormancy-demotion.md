# Spec: authored-habit dormancy demotion (pruning analog)

Date: 2026-10-21. Source: articles review, Domain 27 (Lack of developmental history).

## What changes

A new wrapper-level tracker (new module, e.g. `calibos_mind/authored_dormancy.py`,
or an extension of `habits.py` if the builder finds that cleaner) that demotes
dormant *authored* habits' bypass privilege. Authored habits today sit above the
frozen engine's 0.65 bypass threshold forever: `verify_before_claiming` (0.68)
has never fired in 730 live ticks, yet can bypass deliberation on its next
trigger with zero lived grounding. `curious_question` (0.72) last fired at
tick 7. Installed automaticity without practice is the "born fully formed"
symptom (Domain 27 checks 3 and 10) with a live behavioral consequence.

Mechanism (pruning analog — childhood reflexes persist as tendencies but lose
automaticity without practice; the loss is permanent, as with synaptic
pruning — atrophy is not reversed by later wishing):

- On waking ticks only, for each authored habit (keys read from `calibos.toml`
  `[habits]` entries; baselines likewise from the toml, single source of truth):
  - If `tick - last_fired_tick > DORMANCY_TICKS` (300) and live strength >= 0.65:
    demote live strength to exactly 0.64 (one notch below the engine bypass
    threshold), set a `demoted` flag in the new sidecar, and mint exactly one
    temporal record (`generated_by=cognition`) with a written reason naming the
    habit key, the dormancy span in ticks, and the authored baseline.
  - `last_fired_tick` is proven the same way the stale-note tracker proves a
    bypass firing: the engine's `last_used_tick` stamp on the live habit entry
    equals the current tick (bypass branch ran). The clock only matters
    pre-demotion: once 300 ticks pass with no bypass firing, the privilege is
    gone.
- **Demotion is one-way (ratchet).** The frozen engine writes `last_used_tick`
  only inside the bypass branch (`engine.py`, gated on strength >= 0.65;
  documented in `counterfactual.py`: the deliberated path never stamps). A
  habit demoted to 0.64 can therefore never produce the stamp that would
  restore it — a restore-on-firing semantic is unreachable in vivo, so it is
  not implemented. This is the honest pruning analog: synaptic atrophy is not
  reversed. The demoted flag latches permanently; no code path may restore a
  demoted habit's strength. The demotion record's wording must not promise
  restoration ("loses its automatic edge", never "for now").
- Floor: strength never demoted below 0.64. Identity presence is preserved —
  the habit remains deliberation-eligible, it only loses the bypass privilege.
- The `formed:` namespace is never touched (existing disuse/decay path owns it).
- Sidecar (e.g. `authored-dormancy.json`): per-habit `{baseline, last_fired_tick,
  demoted}`. `init --force` wipes it. Read-only commands and dream ticks never
  write it and never demote.
- Contract revision (explicit): `habits.py` module docstring currently says
  authored strength "is a fixed cartridge constant". That becomes "authored
  baseline; live strength may demote through dormancy (floor 0.64) — demotion
  is a one-way ratchet, with no restore on firing". The builder updates the
  docstring; the critic treats the old sentence as superseded, not as a
  violated invariant.

## Why

Small, load-bearing, independently revertable. It gates a real behavioral
branch (the engine's bypass at >= 0.65) rather than relabeling anything.
Without it, an installed-but-never-exercised tendency can hijack action
selection indefinitely — conduct shaped by the cartridge's birth state, not by
lived history. This is a genuine developmental mechanic (use-it-or-lose-it
pruning), not installed theater: no stage labels, no counters without
consequence.

## Fitness function (synthetic /tmp stores only, never the live store)

1. Authored habit at baseline 0.72, `last_used_tick` 0, waking tick 400 →
   strength becomes exactly 0.64, sidecar flag set, exactly one temporal record
   with habit key + dormancy span + baseline recoverable from text.
2. Waking tick 401 → no second record, strength stays 0.64.
3. Ratchet: after demotion, no code path restores strength — even a hand-set
   `last_used_tick` stamp (in vivo impossible post-demotion) leaves strength
   at 0.64 and the demoted flag latched; no second record is ever minted.
   (Pre-demotion, a genuine bypass firing resets the dormancy clock: habit at
   0.72 firing at tick 250 → no demotion at tick 550, demotion at tick 551.
   Edge: demote when `tick - last_fired_tick > DORMANCY_TICKS` (300).)
4. Dormant 200 ticks (< 300) → untouched: strength 0.72, no record.
5. A `formed:` habit at 0.70 dormant 400 ticks → dormancy tracker leaves it
   byte-identical (existing disuse path owns that namespace; no double jeopardy).
6. Behavioral proof: scripted engine sequence where the 0.72 habit takes the
   bypass branch pre-demotion takes the deliberated path post-demotion
   (0.64 < 0.65) — the privilege is actually gated, not relabeled.
7. Read-only command sweep + dream ticks → sidecar byte-identical, no demotion,
   no records minted.
8. `init --force` → sidecar wiped; post-wipe behavior matches a fresh tracker.
9. Replay determinism: identical tick sequences → byte-identical sidecar.
10. Full existing suite green.

## Revert signal

- Demotion of a habit that fired within the 300-tick window (false dormancy).
- Strength demoted below 0.64, or any write to the `formed:` namespace.
- Demotion or record minted from a read-only command or dream tick.
- Any code path restoring a demoted habit's strength above 0.64 (the ratchet
  is load-bearing; a restore path is the spec's verbatim revert signal).
- Any existing test regresses.
- In vivo: a demotion record for a habit with genuine recent firings.

Revert = delete the new module + its install line + the sidecar file.

## assess_after

2026-11-04.
