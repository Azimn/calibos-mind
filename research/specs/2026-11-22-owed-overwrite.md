# Spec: fail loud when the demotion edge would overwrite a pending owed mark

Date: 2026-11-22. Domain: 29 (Lack of idiosyncrasy) daily articles review.
Follow-up ticket from the 2026-11-21 authored-dormancy lost-latch recovery loop:
"pre-existing hole in spec-frozen observe_tick: it overwrites a pending owed
mark when it demotes again (owed{400} lost to owed{401})".

## What changes

`AuthoredDormancyTracker.observe_tick` in `calibos_mind/authored_dormancy.py`
only. No other module, no cartridge change, no engine change.

Today the demotion edge unconditionally assigns:

```python
entry["owed"] = {"habit_key": key, "dormancy_ticks": span,
                 "baseline": baseline, "demoted_tick": tick}
```

If the entry already holds a pending owed mark — `demoted` False with
`owed` non-None — the pending mint obligation for the earlier episode is
silently discarded: the new mark replaces it, `observe_tick` never re-emits
the earlier episode, and nothing ever mints it. The one-note-per-episode
audit trail then claims a demotion at the later tick for an episode whose
behavioral demotion happened at the earlier one, and the crash that caused
it leaves no trace.

How the inconsistent state is reachable without any sidecar edit: the owed
mark is a write-ahead obligation saved before the mint transaction commits.
Crash between the sidecar save and the mint commit, then a sidecar reseed
(`init --force`, or the October defect class in which a harness wiped
sidecars) rebuilds the entry as `demoted: False, owed: None` with the
dormancy clock seeded from the live stamp — still >300 ticks silent — and
the next tick's edge fires a fresh demotion for what is, behaviorally, the
same episode. `recover_lost_latches` already treats the
`demoted False + owed` state as fail-loud ("latching from a different
episode's note while clearing owed would silently discard that mint
obligation"); this spec makes the edge honor the same discipline instead of
being the writer that creates the silent loss.

Fix: in the demotion edge, before any mutation, if `entry["owed"] is not
None`, raise `ValueError` naming the habit, the pending episode's tick, and
the tick that would have overwritten it. The raise happens before the tick
advances and before any mint, in-memory state and sidecar bytes untouched,
the pending owed mark intact for the operator to clear. No other behavior
changes: the normal edge (owed None) still demotes exactly once; the
already-demoted crash-recovery branch still re-emits a pending owed mark
untouched; `ack_demotion_notes` still clears only on matching
`demoted_tick`.

## Why

The owed mark is a write-ahead mint obligation. The module's own invariant,
documented in `recover_lost_latches`, is that no code path silently
discards one. The edge is currently the one writer that can. A second
demotion note for one episode contradicts the one-episode ratchet the
2026-11-21 loop built, and a silently shifted demotion tick falsifies the
audit trail the Articles review reads as evidence. Fail loud is the
module's established response to this exact inconsistency; preserving and
re-emitting instead would mint a note for an episode the edge is about to
re-date, which is a different design and is out of scope for this mutation.

## Fitness function (synthetic stores only, never the live store)

1. Seeded entry `demoted: False, owed: {habit_key, dormancy_ticks, baseline,
   demoted_tick: 400}` with the habit dormant (`tick - last_fired_tick >
   300`, live strength >= 0.65) → `observe_tick` at tick 401 raises
   `ValueError` before the tick advances; sidecar bytes and in-memory state
   byte-identical to before the call; `owed{400}` intact; no mint event
   emitted for the habit.
2. Normal edge unchanged: owed None, dormant, strength >= 0.65 → demotes
   exactly once (`demoted` True, `demoted_tick` the tick, `owed` set, one
   event); a second `observe_tick` at the next tick emits the same owed
   episode again (recovery re-emit) and mints nothing twice.
3. Already-demoted + owed (crash-recovery shape) still re-emits untouched
   and still re-derives the strength projection.
4. `ack_demotion_notes` still clears only when `demoted_tick` matches the
   event; a mismatched ack leaves the mark.
5. Read-only sweep (`drift`, `status`, `relationships`, `review`, `inbox`,
   `wake` briefing, `think`, `dream` command) and dream ticks leave the
   sidecar byte-identical; `init --force` wipes it, including a sidecar
   holding a pending owed mark.
6. Determinism: identical sequences → byte-identical sidecars, including
   the fail-loud path (two runs raise identically, bytes untouched).
7. Full suite green (`tests/`, excluding the known pre-existing home_sim
   test-ordering artifact, verified as pre-existing on a pristine worktree
   if it manifests).

## Revert signal

- `observe_tick` raises on a healthy store (no pending owed mark), or
  fails to demote a genuinely dormant habit with owed None.
- A second demotion note is minted for one episode in vivo.
- Any existing test regresses.
- In vivo: the live sidecar holds no pending owed marks (both authored
  habits healed 2026-11-21, owed null), so a fail-loud in vivo would
  itself be a defect to adjudicate, not a silent loss.

## Assess after

2026-12-06.

## Regression genome

Check item by item per `research/builder-critic-loop.md`: shadowing
definitions; sidecar/state reset on reseed; read-only violations;
silent defaults; unrestored swapped config; dream/conduct isolation;
negation-blind similarity; dead regex branches; contraction tokenization
hides polarity; silent zero on missing sections; negative-activation
collapse; contraction-negation blindness; test-premise drift; CLI
construction is never mtime-silent.
