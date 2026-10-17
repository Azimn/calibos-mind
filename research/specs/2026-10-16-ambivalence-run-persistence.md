# Spec: ambivalence-run persistence across process boundaries

Date: 2026-10-16. Author: daily articles review (domain 22, Internal causality).

## What

Persist the open ambivalence standing-tie run to the `ambivalence.json`
sidecar so contested-margin runs survive process boundaries. On
`AmbivalenceTracker` construction, resume any persisted open run onto the
subject so the next waking tick extends or closes it honestly (extend on
same-key continuation, offset on clear/changed/bypass).

## Why

In-vivo evidence (ambivalence.json, ticks 574–650): 57 onset markers vs 7
offset markers; 24 same-key consecutive-tick onset pairs that are fractured
multi-tick runs. The 2026-10-03 fitness demands onset tick, offset tick,
and margin min/max recoverable from the stream alone — across process
boundaries this fails: margin evolution and offset ticks are unrecoverable,
and the stream misrepresents multi-tick runs as repeated one-tick onsets.
This is check 9 of domain 22 (behavioral consequences traceable across
time) — a mechanical gap in existing machinery, not a new mechanism, and
not theater: it completes the mechanism's own stated recoverability
requirement.

## Design constraints

- The sidecar gains an `open_run` field: `{contenders, channels,
  onset_tick, last_tick, margin_min, margin_max}`. Persisted whenever the
  run changes (absorb/extend/close); cleared on close.
- Resume path: tracker construction reads `open_run` and re-attaches it to
  the subject's `_RUN_ATTR` so the observer's normal extend/close logic
  applies on the next waking tick. No new run may be opened while a
  resumed run is open unless the keys genuinely changed (then: close with
  reason "changed", open the new one — same as the in-memory path).
- Crash discipline: the persisted run must never produce a double-onset
  for the same tick after a kill between persist and marker flush. Mirror
  the existing staging order (note offset BEFORE clearing, per
  `_close_open_run`); on resume, the first contested tick with the same
  key extends rather than re-emitting onset.
- Read-only commands and dream ticks never touch the sidecar (existing
  genome). `mind init --force` wipes `open_run` with the rest of the
  sidecar (genome: sidecar/state reset on reseed).
- One-tick runs keep the existing behavior: onset marker stands alone, no
  offset, no persisted residue.
- The venv (`calibos-mind/.venv`) is a frozen immutable snapshot — never
  edited. Wrapper-layer only (`calibos_mind/ambivalence.py`, tests).
- Tests on synthetic `/tmp` stores only, never the live store. Dated
  CHANGELOG.md entry. No commit.

## Fitness function

On synthetic stores:
1. Two-process replay: process A ticks 1–2 contested (same key), process B
   ticks 3–4 (same key, then clear) → exactly ONE onset marker and ONE
   offset marker naming onset_tick=1, offset/last tick, margin min/max.
2. One-tick run → exactly one onset marker, no offset, no `open_run`
   residue in the sidecar.
3. Kill between processes (persist done, next tick never ran) → resume on
   next startup extends or closes honestly; never a second onset for the
   same tick.
4. Contender change across the boundary → offset with reason "changed" for
   the old run + onset for the new.
5. A 10-tick in-memory standing tie (no boundary) → unchanged behavior:
   one onset + one offset (the 2026-10-03 fitness still holds).
6. Read-only command sweep and dream ticks → sidecar byte-identical.
7. `init --force` → `open_run` wiped.
8. Full existing suite green.

## Revert signal

- Duplicate onsets for one tick; an offset naming an onset_tick with no
  onset marker in the stream; `open_run` surviving `init --force`;
  read-only or dream commands writing the sidecar; any existing test
  regresses; in vivo: onset/offset ratio not moving toward balance
  across the window. Revert = remove the `open_run` persistence (field
  ignored/deleted, in-memory behavior restored).

## assess_after

2026-10-30.
