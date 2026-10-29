# Spec: persistent-near-miss note (Domain 4, Memory artificiality)

Date: 2026-10-28. Design lead: daily articles review, cycle 2, domain 4.
Status: in builder/critic loop. No commit (the backup job handles commits).

## What

When a familiarity near-miss streak reaches 10 consecutive near-miss views
without admission, mint exactly one temporal record (generated_by=cognition)
reporting the threshold crossing, once per episode. Admission of the target
record, or pruning of its streak (it stops near-missing), ends the episode;
a later streak to 10 may note again.

## Why

Domain 4, check 18 (familiar-but-unplaceable): familiarity near-miss traces
exist (`calibos_mind/familiarity.py`, shipped 2026-09-28) and persist in vivo
(max streak 18 views at tick 844, several >= 11), but they never reach
cognition — the streak is a sidecar id, never narrated as a felt
"familiar but unplaceable" state. This mutation gives cognition a report of
a persistently almost-recalled record without fabricating recall content:
the note names the record id and the streak count only. The target record's
content stays unplaced until the existing 0.5 boost earns it admission.

## Mechanism (constraints, not a blank cheque)

- Threshold: `PERSISTENT_NEAR_MISS_THRESHOLD = 10`, one named constant.
  10 sits above the 3-view boost threshold and below the observed in-vivo
  maximum (18), so it fires only on genuinely persistent cases.
- Episode guard: live in the familiarity sidecar alongside `streaks`
  (e.g. a `noted` map), so it shares the sidecar's existing discipline:
  old sidecars without the guard key load cleanly; `reset()` wipes guard
  and streaks together (init --force); read-only commands and dream ticks
  never touch the sidecar.
- Mint path: waking tick path only, reusing the existing temporal-record
  minting helper/pattern used by the habit-stale-note and
  expectation-closure-note mechanisms (find it; do not invent a new write
  path). The note record must be a normal temporal record with
  generated_by=cognition.
- Content ban: the note text MUST NOT contain, paraphrase, or quote any
  content of the target record. Id + streak count + window facts only.
  A test with a unique marker string in the target record must prove the
  marker absent from the note.
- No engine edits (venv stays byte-identical), no cartridge change, no
  change to the boost (flat 0.5 at streak >= 3, unchanged) or to streak
  counting/pruning semantics. The streak itself is untouched; only the
  guard and the mint are new.
- Determinism: identical view sequences produce byte-identical sidecars
  and identical note records (no wall clock, no RNG).

## Fitness function (all on synthetic /tmp stores, never the live store)

1. Streak 9 consecutive near-miss views -> zero notes.
2. Streak reaching 10 -> exactly one temporal record, generated_by=cognition,
   naming the record id and streak 10, containing no target content
   (unique-marker test).
3. Streak continuing to 25 -> still exactly one note for the episode.
4. Admission of the target, then a fresh streak to 10 -> a second note
   (new episode).
5. Pruning (target stops near-missing), then a fresh streak to 10 -> a
   second note.
6. Read-only sweep (drift, status, review, inbox, consolidate --list) and
   dream ticks mint zero notes and leave the familiarity sidecar
   byte-identical (scope: sidecar bytes + record count; CLI construction
   payload rewrite is the known, pre-existing exception, not this change).
7. `init --force` wipes streaks AND the episode guard (a post-reseed
   streak to 10 notes exactly once, with no recycled-id attachment).
8. Byte-identical replay: identical view sequences -> byte-identical
   sidecar and identical note records.
9. Full existing test suite green; venv byte-identical; cartridge untouched.

## Revert signal

- A note minting below streak 10, or a duplicate note within one episode.
- A note minted from a read-only command or a dream tick.
- A note containing any content of its target record.
- The episode guard surviving `init --force`, or attaching to a recycled id.
- Any existing test regressing.
- In vivo: duplicate notes within one episode, or notes covering >5% of
  waking ticks in any 100-tick window (noise, not signal).

Revert = remove the mint call + guard (restore familiarity.py sidecar
shape); the note records already minted stay as history, never deleted.

## Assess after

2026-11-11 (14-day window). Register in articles-rotation.json on ship.

## CHANGELOG

Dated entry, same style as prior mutation entries. No commit.
