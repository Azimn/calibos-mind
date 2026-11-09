# Spec: time-based disuse decay for formed habits (extinction on trigger silence)

Date: 2026-11-08. Design lead: daily articles review, Domain 15 (Learning).
Status: spec — builder/critic loop round 1 not yet run.

## What changes

`calibos_mind/habits.py` only (plus tests + CHANGELOG entry). No engine,
cartridge, or other module changes.

Today, a formed habit decays only when its trigger dominates a tick but a
different action is chosen (disuse decay, `DECAY_STEP = 0.05`,
`REMOVAL_FLOOR = 0.30`). A habit whose trigger simply stops occurring never
decays at all. In vivo at tick 1024: `formed:hunger:seek_contact` sits at
strength 0.70 with `last_tick` 720 — 304 ticks of trigger silence, zero
decay. Extinction is trigger-conditional, not time-based. That is the
Domain 15 check (5) "No extinction curves" gap: the curve exists only for
one of the two ways a habit can go unused.

Add silence decay: for each formed habit, every full 100 waking ticks of
continuous trigger silence (measured from the habit's `last_tick`) lower its
strength by one `DECAY_STEP` (0.05). Removal below `REMOVAL_FLOOR` (0.30)
follows the existing removal path (archival note with reason; mirror the
existing disuse-removal note, naming silence as the cause).

State: one new per-habit field in the formed entry,
`silence_windows_applied` (int, default 0). On each waking-tick fold, for
each formed habit:
- `windows_elapsed = (tick - last_tick) // 100`
- `new_windows = windows_elapsed - silence_windows_applied`
- if `new_windows > 0`: `strength -= DECAY_STEP * new_windows`;
  `silence_windows_applied = windows_elapsed`; then apply the existing
  removal check.
- On any co-fire (trigger fires and habit grows/fires), `last_tick` updates
  per the existing rule AND `silence_windows_applied` resets to 0 — the
  silence clock restarts when the habit is exercised.

Silence decay composes with the existing dominance-based disuse decay; both
feed the same removal floor.

## Why

Domain 15 (Learning) adversarial pass, 2026-11-08: extinction is partial —
the mechanism exists but only fires on trigger-dominance-with-different-action.
A habit that is never triggered again should extinguish, gradually, like a
real one. This is a learning *mechanism* (same class as the existing disuse
decay), not an installed bias, quirk, or piece of theater: no new conduct,
no new content, only the time course of an existing learned structure.

## Fitness function (synthetic stores, /tmp only)

1. Formed habit at 0.70, trigger silent 100 ticks → strength 0.65; 200 ticks
   → 0.60; 300 ticks → 0.55 (exactly one step per full 100-tick window).
2. Silence of 99 ticks → no decay; silence of 100 ticks → exactly one step.
3. Trigger fires again mid-silence (co-fire at, e.g., tick +50) → silence
   clock resets: a further 99 silent ticks after the co-fire cause no decay.
4. Decay to below 0.30 removes the habit with a written archival note
   (silence named as cause); the removed habit never fires again.
5. A habit firing regularly (trigger dominates and habit fires within every
   100-tick window) is byte-identical to pristine behavior — no silence
   decay applied, no new sidecar fields changing existing entries.
6. Read-only commands and dream ticks never apply silence decay and never
   touch `silence_windows_applied` (sidecar byte-identical across a read-only
   sweep and across dream ticks).
7. `init --force` wipes silence-decay state with the rest of the sidecar.
8. Determinism: identical tick sequences → byte-identical sidecar.
9. Full existing suite green; no engine/cartridge bytes change.

## Revert signal

- A habit that fires regularly loses strength (silence decay applied while
  its trigger was active within the window).
- A habit removed while its trigger had fired within the preceding 100 ticks.
- Silence decay applied from a read-only command or a dream tick.
- Any existing test regresses.
- In vivo (assessed 2026-11-22): a formed habit removed while its trigger
  had fired within the preceding 100 ticks, or a habit's strength falling
  while it is still firing regularly.

## Assess after

2026-11-22 (14 days).

## Notes for the builder

- Read `calibos_mind/habits.py` first — mirror the existing disuse-decay and
  removal code paths rather than inventing parallel ones. The fold entry
  point is the per-waking-tick fold; dream ticks and read-only commands must
  never reach it (verify, don't assume).
- `SILENCE_WINDOW_TICKS = 100` and the step reuses `DECAY_STEP`; name the
  constant.
- Tests run on synthetic `/tmp` stores, never the live store. Dated
  CHANGELOG.md entry. No commits.
