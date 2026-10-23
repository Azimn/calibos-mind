# Spec: learned event expectations — trajectory-shaped surprise

Date: 2026-10-22. Domain 28 (Lack of path dependence) adversarial pass.

## The gap

Domain 28 check (4): "Early experiences do not alter interpretation of later
ones." Currently **absent** — no mechanism, live store or code:

- `SubjectEngine._own_event` (`.venv/.../digital_subject/engine.py:217-228`):
  `meaning` comes from event metadata/rule text; `surprise` falls back to
  `clamp(event.intensity * 0.35)` whenever `expected_valence` or
  `actual_valence` is None — which is every event except the feeder's
  (feeder sets both to 0.0 explicitly). `relevance` counts matched memories
  (`+0.04` each) without using their content. There is no learned expectation
  anywhere: a mild event after a month of intense events is interpreted
  exactly as it would be on day one.

## Why this mutation is load-bearing, not theater

`surprise` is not a label — the frozen engine spends it:

- `engine.py:234`: concern scale `event.intensity * (1.0 + surprise * 0.4)`
- `engine.py:240`: concern urgency `0.25 + intensity*0.55 + surprise*0.2`
- `engine.py:253-254`: memory emotional charge `|valence| + intensity*0.7 +
  surprise*0.2` and initial memory strength `0.3 + intensity*0.45 +
  surprise*0.25`
- `engine.py:445`: cognition-admission trigger `1.0 + self_relevance*0.25 +
  surprise*0.25`

Making `expected_valence` a function of the organism's own lived event
history makes surprise — and everything downstream of it (which events become
concerns, how strongly memories are charged, when cognition fires) —
trajectory-dependent. This is emergence from history (the habit-formation
analog for interpretation), not an installed bias: nothing is asserted about
*what* to expect, only that expectations are learned from what happened.

## What changes

New wrapper module `calibos_mind/expectation.py`, engine overlay at import
time (the `narrative.py` precedent — venv stays byte-identical; revert =
delete the module + its import line in `calibos_mind/__init__.py`):

1. **Predict.** Patch `SubjectEngine._own_event`: at the top, if
   `event.expected_valence is None` AND the local sidecar
   (`expectations.json`, local-only, gitignored) holds an EMA entry for this
   event's key, set `event.expected_valence = ema` and
   `event.actual_valence = event.actual_valence if not None else
   event.valence`. If the sidecar has no entry for the key (cold start),
   touch nothing — the engine's pristine fallback (`intensity * 0.35`)
   applies byte-identically.
2. **Learn.** After the engine's own `_own_event` body runs (use-then-update
   ordering: the current event's valence must never inform its own
   expectation), update the EMA for the key with
   `actual = event.actual_valence if not None else event.valence`,
   `ema := ema + 0.3 * (actual - ema)`, storing `{ema, count, last_tick}`.
   Keys are `kind + "|" + ",".join(sorted(tags))`; sidecar capped at 256
   entries, evicting stalest `last_tick` first (documented).
3. **Never override.** Events carrying an explicit `expected_valence`
   (feeder's 0.0/0.0 today) are never touched and never learn into the
   sidecar — the expectation machinery observes only the organism's
   interpreted world, never the host's scheduled feedings. (Whether feeder
   events should teach expectations is a design question for the assessor,
   not a default.)
4. **Hygiene.** Read-only commands and dream ticks never write the sidecar
   (dream ticks process no events; assert isolation in tests).
   `mind init --force` wipes `expectations.json` alongside the other sidecars
   (genome: sidecar/state reset on reseed). Replay of an identical event
   sequence yields a byte-identical sidecar.

## Fitness function (all on synthetic /tmp stores, never the live store)

- Two synthetic stores, identical probe event (`kind="observation"`,
  `valence=0.0`, same tags/intensity), different early histories for that
  key (five events at valence +0.8 vs five at -0.8): the probe's `surprise`
  differs between the stores, and the minted memory record's charge/strength
  differ accordingly. History changes interpretation — the check-(4) symptom
  is addressed in vivo-mechanically.
- Cold start: a first-ever event of a key produces `surprise` byte-identical
  to the pristine engine's fallback (`intensity * 0.35`) — prove against the
  unpatched engine on the same fixture (blinded-region parity: fresh stores
  behave exactly as before).
- Explicit `expected_valence` (feeder-style 0.0/0.0) is never overridden and
  never writes the sidecar.
- Read-only sweep (`drift`, `status`, `review`, `consolidate --list`) and
  `dream` ticks leave the sidecar byte-identical (and never create it).
- `init --force` deletes the sidecar; a post-reseed event starts cold.
- Identical event sequences on two stores → byte-identical sidecar.
- Full existing suite green with the overlay imported.

## Revert signal

- `surprise` differs from the pristine fallback on a cold-start key (false
  expectation — the ruled edge: first event of a key is untouched, learning
  starts with the second).
- `expected_valence` set when an explicit value was provided, or a feeder
  event written into the sidecar.
- Sidecar written by any read-only command or dream tick; stale EMA attaching
  after `init --force`; non-determinism in the sidecar.
- Any existing test regresses.
- In vivo (assess_after window): EMA never moving across the window, or
  surprise deltas never propagating to concern urgency in any 100-tick
  stretch (dead weight = theater — revert).

## Assess_after

2026-11-05 (14-day window, matching the other shipped mutations).
