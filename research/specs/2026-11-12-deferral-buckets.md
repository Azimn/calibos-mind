# Spec: weariness-gated prompt deferral with categorized buckets

Date: 2026-11-12. Author: Calibos (design lead). Approved by Jay 2026-11-12.
Domain: Articles of Artificiality, Domain 19 Agency (cycle 2). Absent checks addressed: (6) no refusal on internal priorities, (7) no postponement.

## What changes

The waker contract gains a third option. Today a wake must answer a prompt or
let it pass silently. After this change a wake may **defer** a prompt: place it
in a named bucket with a revisit time and a reminder to come back. Answer /
pass / defer.

## Buckets

- Deferred prompts live in named **buckets** (categories), not one pile.
- Default starting buckets: `work`, `personal`. Nothing else seeded.
- **Categories are the mind's own decision (Jay, 2026-11-12).** The inhabitant
  chooses, adds, renames, and removes its buckets as it sees fit; Jay may
  suggest, never prescribe. Work/personal are a starting point, not a
  prescription. Someday there may be a macrame bucket.
- Removing a bucket must reassign its contents to another bucket or release
  them with a written record. Never silently drop a deferred item.
- Each bucket carries its own importance math (its own weights for how
  deferral decisions are scored), configurable per bucket. Work and personal
  do not share one threshold: what is cheap to defer in one bucket may be
  expensive in the other.

## The weariness gate

- Weariness is a measurable internal state, not an installed feeling: open
  commitment count, recent load, outstanding expectations. Reuse the existing
  friction/weariness machinery where it exists; do not invent a second
  weariness.
- When weariness crosses the gate, a low-urgency prompt may be deferred
  instead of answered. High-urgency prompts are never deferred on weariness
  alone.

## Deferral history feeds back (the anti-never-done answer)

Jay's named risk: an important deferred thing never gets done. Three
mechanisms answer it, and all three are part of the build, not polish:

1. **Reminder on every defer.** Deferring mints a reminder record (a
   commitment-like entry) to revisit the bucket at the revisit time. A defer
   with no reminder is a bug, not a defer.
2. **Escalation on repeat defers.** Each defer increments a per-item defer
   count. Past a threshold (default 3), the item's priority rises and the
   gate gets stricter for it: the mind becomes less willing to defer the
   same thing again.
3. **Forgotten-item feedback per bucket.** An item that passes its revisit
   window without action counts against its bucket's deferral allowance.
   A bucket with a history of forgotten items defers less readily afterward.
   The mind judges whether to defer from its own past forgetting, the way a
   person does.

## Revisit rule: time-based with jitter, not next-wake

- Revisit time is a time, not "next wake". Each bucket has a revisit window
  (default 4-24 hours, configurable per bucket); the revisit time is drawn
  randomly within the window. The jitter is deliberate: it should feel like
  remembering, not like a cron.
- **Revisit ritual.** When a bucket is revisited, the waker surfaces a random
  thought and a remembering pass over the bucket's contents, not just the
  single item due. Random thoughts and remembering ride in this update.

## Fitness function

Synthetic stores (/tmp only, never the live store):

- Defer is a real third option: a low-urgency prompt at high weariness can be
  deferred, and the defer mints exactly one reminder record naming the item,
  the bucket, and the revisit time.
- High-urgency prompts are never deferred on weariness alone.
- Bucket operations: add/rename/remove work; removal reassigns or releases
  with a written record; no item is ever silently lost by a bucket operation.
- Repeat-defers escalate: an item deferred 3 times has strictly higher
  priority and a strictly stricter gate on the 4th defer than on the 1st.
- Forgotten-item feedback: a bucket whose items passed their revisit windows
  unactioned defers less readily than an identical bucket with no forgotten
  items (same weariness, same prompt).
- Revisit times fall inside the bucket's window and vary across draws (jitter
  is real, not a fixed offset).
- Revisit ritual surfaces a random thought / remembering pass over the
  bucket's contents.
- Read-only commands and dream ticks never defer, never mint reminders, never
  touch bucket state (sidecar byte-identical).
- `mind init --force` wipes bucket/reminder state, including corrupt sidecars
  (fail closed, never silently reset).
- Determinism: identical sequences produce byte-identical sidecars.
- Full suite green.

## Revert signal

- Any deferred item silently lost (no reminder, no revisit, no written
  release).
- A deferred item passing its revisit window with no escalation and no
  recorded revisit.
- Deferral used for a high-urgency prompt, or deferral firing at low
  weariness (gate not actually gating).
- Bucket removal dropping items.
- Any existing test regresses; any write from a read-only or dream path.
- In vivo: items starving in buckets across the window (deferred, never
  revisited, never escalated) is a revert trigger; a bucket never used is
  inert, not a revert trigger.

## Assess after

2026-11-26 (two weeks from ship).

## Build rules

- Implement through the builder/critic loop (research/builder-critic-loop.md).
- Tests run on synthetic /tmp stores, never the live store.
- Dated CHANGELOG.md entry. No commits.
- Check the regression genome item by item (builder-critic-loop.md).
- Register the shipped mutation in
  workspace/goals/calibos-autonomous-mind-operation/hidden_files/articles-rotation.json
  with fitness, revert signal, assess_after 2026-11-26.
