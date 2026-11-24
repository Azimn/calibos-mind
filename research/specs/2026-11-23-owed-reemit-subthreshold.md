# Spec: authored-dormancy owed re-emit under a sub-threshold baseline

Date: 2026-11-23. Domain 30 (Lack of friction) daily review, cycle 2.
Origin: follow-up ticket from the 2026-11-22 builder/critic loop (owed-overwrite
fail-loud guard), critic observation 2, carried in the articles rotation state.
That observation was pre-existing and out of the 11-22 spec's scope; this spec
is its fix.

## The defect

`AuthoredDormancyTracker.observe_tick` (`calibos_mind/authored_dormancy.py`)
has two loops over the authored habits. In the main loop, the first branch is:

```python
if baseline < HABIT_FIRE_STRENGTH:  # 0.65
    # Never held the bypass privilege; nothing to demote.
    continue
```

The `continue` skips the whole iteration — including the owed re-emit at the
bottom of the loop (`if entry["owed"] is not None: events.append(...)`). So an
entry whose baseline has dropped below 0.65 (reachable only via a cartridge
baseline change, which ships with a store migration, or a hand edit) while it
still holds a pending `owed` mark never re-emits its owed demotion episode.
The mint obligation is stranded: still in the sidecar file, never emitted,
never acked, silently stalled forever. The fail-loud pre-pass has the same
skip, so it never raises either — the stall is silent, not loud.

The same skip also strands the crash-recovery re-derivation branch
(`entry["demoted"] and live.strength != DEMOTED_STRENGTH`): a sub-threshold
entry whose store still holds the pre-demotion strength never gets its
projection re-derived, so a minted note could claim a demotion that never
behaviorally happened.

## What changes

Restructure `observe_tick` so that, for every authored habit entry, the owed
re-emit (and, where applicable, the crash-recovery strength re-derivation)
runs regardless of whether the entry's baseline is below
`HABIT_FIRE_STRENGTH`. The sub-threshold `continue` must skip only the
demotion-edge / firing / clock-reset logic — never the re-emit and never the
re-derivation. Concretely:

- A pending `owed` mark re-emits as an event on every waking tick until
  `ack_demotion_notes` clears it, for every baseline, including
  sub-threshold ones.
- No new demotion may fire for a sub-threshold entry: `demoted` must stay
  as it was, `demoted_tick` unchanged, no new `owed` minted, no strength
  projection change beyond the existing crash-recovery re-derivation.
- No behavior change for at-or-above-threshold entries: replaying the
  existing test sequences must produce byte-identical sidecars and event
  lists.
- The fail-loud pre-pass keeps its current scope (it mirrors the demotion
  edge, which cannot fire below threshold), but it must not be the thing
  that strands an owed mark: after this fix, a sub-threshold entry with a
  pending owed mark re-emits instead of stalling, so the pre-pass's silence
  there is benign, not a loss. Document that reasoning in the code comment
  where the pre-pass skips sub-threshold baselines.
- No changes to `recover_lost_latches`, the mint idempotency rule, the
  one-episode ratchet, or any other module. No cartridge change. No store
  migration.

## Why

The `owed` mark is a write-ahead mint obligation. Every other path in this
module treats it as sacred: the demotion edge fails loud rather than
overwrite it, the recovery path fails loud rather than clear it, the ack
clears only on a matching `demoted_tick`. The sub-threshold `continue` is
the one path that silently abandons it. A stranded obligation is worse
than a loud failure: nothing ever tells the operator, and the demotion note
the store owes never arrives.

## Fitness function (synthetic /tmp stores only, never the live store)

1. Seeded entry: baseline 0.64 (< 0.65), `demoted=True`,
   `owed={"habit_key": K, "dormancy_ticks": 350, "baseline": 0.64,
   "demoted_tick": 400}`, live strength 0.64, `last_fired_tick` 50.
   `observe_tick` at tick 401 re-emits exactly the owed event; a second
   call at tick 402 re-emits it again (until ack); `ack_demotion_notes`
   with the matching event clears it, and the next tick emits nothing.
2. Same seed with live strength 0.65 (crash between sidecar save and
   reconcile): `observe_tick` reports `state_changed=True` so the caller
   re-derives the projection to 0.64, and still re-emits the owed event.
3. Same seed, no owed mark: `observe_tick` emits no event, changes nothing
   (`sidecar_changed=False` apart from any fresh-entry seeding the call
   itself performs — assert the exact flags), and never demotes.
4. Boundary: baseline exactly 0.65 behaves exactly as before (edge can
   fire when dormant); baseline 0.649 re-emits owed but never demotes,
   even when dormant for > 300 ticks.
5. At-threshold regression: replay the existing owed-overwrite and
   latch-recovery test sequences; sidecars and event lists byte-identical
   to pre-change behavior (assert against the pre-change outputs, not just
   "tests pass").
6. Read-only sweep + dream ticks leave the sidecar byte-identical;
   `init --force` wipes it, including a pending-owed sidecar.
7. Determinism: identical sequences produce byte-identical sidecars,
   including on the fail-loud and re-emit paths.
8. Full suite green (`tests/`, mind venv; the known pre-existing home_sim
   ordering artifact, if it manifests, must be verified pre-existing on a
   pristine worktree, not assumed).

## Revert signal

- `observe_tick` raises on a healthy store (no pending owed mark, no
  demotion due), or fails to demote a genuinely dormant habit with
  `owed=None` at an at-threshold baseline.
- A sub-threshold baseline demotes a habit, mints a new demotion note, or
  changes a live strength outside the crash-recovery re-derivation.
- An owed episode is emitted twice for one mint (the mint is idempotent,
  so this is about the events list carrying duplicates in one call).
- Any existing test regresses; any write from a read-only or dream path.
- In vivo: the live sidecar holds no pending owed marks (both authored
  habits `demoted=True`, `owed=null`, verified 2026-11-22), so a fail-loud
  or a re-emit in vivo after shipping would itself be a defect to
  adjudicate, not a silent loss.

## Assess after

2026-12-07 (two weeks).

## Process

Builder/critic loop per research/builder-critic-loop.md. Tests on synthetic
/tmp stores only, never the live store. Dated CHANGELOG.md entry. No commit.
