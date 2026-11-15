# Spec: prediction-error trace (domain 21 — Error and imperfection)

Date: 2026-11-14. Design lead: daily articles review (domain 21, cycle 2).
Status: spec — builder/critic loop pending.

## What changes

The mind already has a real prediction loop: `ExpectationTracker`
(`calibos_mind/expectation.py`) holds a per-key EMA of actual event valence
and the `_own_event` overlay feeds it to the frozen engine as
`expected_valence`, so the engine's surprise becomes `|actual - ema|` — a
function of lived history. But that surprise is computed and consumed
silently inside the engine: nothing ever observes a failed prediction, so
domain 21 check (10) "No failed predictions" stays partial and check (15)
"Errors have no downstream consequences" has no visible error to carry
consequences.

This mutation makes the engine's own surprise visible. New module
`calibos_mind/prediction_errors.py`, mirroring `counterfactual.py` exactly
in shape and discipline:

- Sidecar `prediction_errors.json`, `{"records": [...]}`. One record per
  suprathreshold prediction error: `tick`, `key`, `expected`, `actual`,
  `error`. No store record is ever minted from this path — sidecar only,
  like counterfactual traces.
- Hook inside the existing `_own_event_with_expectations` overlay in
  `expectation.py`: when a non-explicit event carries a tracker prediction
  (EMA exists for its key), compute `error = |actual - ema|` with the same
  `actual` the overlay already hands the engine
  (`event.actual_valence if not None else event.valence`). If
  `error >= THRESHOLD`, append the trace. The overlay already writes the
  expectations sidecar from `learn()` inside this same call, so a second
  sidecar write here is precedented, not a new write-site class.
- `mind predictions`: a read-only report command (counts, trace rate over
  the last 100 prediction-carrying owned events, recent traces, largest
  errors). Read-only: never writes the sidecar, never mints one on a fresh
  store.
- No change to the frozen engine or the cartridge (fingerprint unchanged).
  Revert = delete the module + its import line + the sidecar, same as the
  counterfactual precedent.

## Why this is emergence, not theater

The audit's emerge-vs-install flag for domain 21 reads: "Failed predictions
may emerge from a future prediction loop." The loop now exists (shipped
2026-10-22 as learned expectations); the errors are the engine's own
surprise quantity, computed from lived history. Recording them installs no
mistake — it observes one. The trace threshold is calibrated, not staged:
the builder measures the error distribution on synthetic streams first and
picks a threshold with a <5% trace rate, the same tripwire class as
counterfactual traces.

## Threshold calibration (builder's first job, before fixing the constant)

Measure `event.valence` range and the `|actual - ema|` distribution on
synthetic event streams (repeated same-key events, then a valence step;
mixed-key streams; constant-valence streams). Pick THRESHOLD so that:
a long mixed synthetic stream traces <5% of prediction-carrying owned
events, and a constant-valence stream traces zero. Document the measured
distribution and the chosen threshold in the module docstring and the
CHANGELOG entry. Do not guess the threshold from the valence range alone.

## Fitness function (synthetic /tmp stores only, never the live store)

- Repeated same-key events at valence 0.5, then one at 0.0: exactly one
  trace minted, with `expected` ~= 0.5, `actual` 0.0, `error` ~= 0.5, tick
  correct, key `kind|tags` correct.
- Unknown-key first event (no EMA): no trace, and the event still learns
  normally (EMA entry created by `learn`).
- Explicit `expected_valence` event (feeder-style): no trace, and no learn
  contamination (never-override rule preserved).
- Small-error event (error below threshold): no trace.
- Threshold calibration: measured trace rate <5% of prediction-carrying
  owned events on a long mixed synthetic stream; zero traces on a
  constant-valence stream.
- `mind predictions` report: correct counts and recent traces on a seeded
  sidecar; on a fresh store it reports zero and mints no sidecar.
- Read-only sweep (`drift`, `status`, `review`, `inbox`, `predictions`)
  leaves the sidecar byte-identical; dream ticks never write (use the
  established `_expectation_suspended` gating, same as predict/learn).
- `init --force` wipes the sidecar, including a corrupt one. Corrupt
  sidecar raises ValueError before the tick advances, bytes untouched.
- Determinism: identical event sequences replay to a byte-identical
  sidecar; save/load round-trip identical.
- No existing test regresses; full suite green; engine + cartridge
  byte-identical.

## Revert signal

- A trace fires for an event with no tracker prediction (unknown key) or
  for an explicit-`expected_valence` event.
- Trace rate exceeds 5% of prediction-carrying owned events in any
  100-event in-vivo window (noise, not signal).
- A trace fires with error below the calibrated threshold.
- Sidecar written by a read-only command or a dream tick.
- Sidecar surviving `init --force`.
- Any existing test regresses.
- In vivo watch item (not a trigger): one key supplying >50% of traces
  over a day suggests a miscalibrated key rather than surprise — report it
  at assessment; do not revert on it alone.

## Assess after

2026-11-28 (14-day window).

## Regression genome

Per research/builder-critic-loop.md: the critic checks every genome item
explicitly — shadowing definitions, sidecar reset on reseed, read-only
violations, silent defaults, unrestored swapped config, dream/conduct
isolation, negation-blind similarity, dead regex branches,
contraction tokenization, silent zero on missing sections,
negative-activation collapse, contraction-negation blindness,
test-premise drift, CLI construction mtime behavior. The critic must attack
with failing tests or concretely violated invariants; prose-only critique
is rejected.
