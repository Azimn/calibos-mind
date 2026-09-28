# Spec: expectation confidence decay on repeated expiry — learning from repeated failure

Date: 2026-09-27 (daily articles review, Domain 3: Temporal continuity)

## Finding

Domain 3 adversarial pass (19 checks): verdict **partial**. Check 19 —
"Repeated failures do not alter future expectations" — is fully true of the
current build. `inbox_expectations.sync` (shipped 2026-09-26) registers every
stale prompt at a fixed `CONFIDENCE = 0.6`, forever. The organism can let
prompts expire unanswered any number of times and its next expectation is
born exactly as confident as the first: no track record, no learning.

The frozen engine already makes confidence behaviorally load-bearing, so a
confidence change is causal, not theater: `Expectation.confidence` feeds
`_open_records` → `_urgency(due, importance=confidence, actor)` → the
temporal record's salience and the `unresolved_concern` candidacy urgency.
Decaying confidence on repeated expiry genuinely weakens the nag; the
urgency formula's `.2*attachment + .1*uncertainty` terms keep it from ever
reaching zero, so the honest-nagging invariant from the 2026-09-26 spec
("expired stays in the open set, the nagging continues") is preserved.

## What changes

New local-only sidecar `expectation_policy.json` at the mind root (next to
`salience.json`; add to `.gitignore` — it is private runtime state, not
architecture): `{"expiry_streak": N}`, the count of consecutive stale-cycle
failures net of answers. Path derivation: `Path(inbox_dir).parent /
"expectation_policy.json"` (the mind root holds `inbox/`).

- `sync`: when registering a new expectation, `confidence =
  max(BASE_CONFIDENCE * DECAY_FACTOR ** N, CONFIDENCE_FLOOR)` with
  `BASE_CONFIDENCE = 0.6` (existing `CONFIDENCE`), `DECAY_FACTOR = 0.8`,
  `CONFIDENCE_FLOOR = 0.15` — named module constants, no magic numbers.
  The rest of the registered fields are unchanged (id, proposition from
  pid + view_tick only, created_tick, due_tick, status pending).
- When `sync` marks an expectation `"expired"` (prompt file vanished
  without settlement — the only expiry path): `N += 1`, persist sidecar.
- `resolve(..., outcome="answered")` when it actually transitions an
  expectation (returns non-None): `N = max(0, N - 1)`, persist. Learning
  works both ways: a streak of answered stale prompts restores confidence.
- `outcome="let-pass"` and `outcome="refused-stale-view"`: neutral, `N`
  unchanged (a deliberate choice to disengage, and a blocked attempt, are
  neither failure nor success).
- Sidecar is read on every `sync` (cheap); it is **written only when `N`
  actually changes**. Empty inbox with no expiries → no sidecar write, no
  transaction — the verifiable no-op invariant from 2026-09-26 holds.
- Dream path untouched: `sync` never runs on dream ticks (existing hook
  discipline unchanged).

## Fitness function (all on synthetic /tmp stores, never the live store)

- Fresh policy (no sidecar file) → a stale prompt registers at confidence
  exactly 0.6.
- Expire once (delete the prompt file, re-sync) → streak reads 1; the next
  stale prompt registers at 0.48.
- Two more expiries → 0.384, then 0.3072 (0.6 * 0.8³).
- Streak forced to 20 → confidence floored at exactly 0.15, never below.
- `resolve("answered")` on a live expectation → streak decrements by 1,
  floors at 0 (never negative).
- `resolve("let-pass")` / `resolve("refused-stale-view")` → streak
  unchanged.
- At floor confidence, an expired expectation still resurfaces: a
  `temporal` record with the concern link and an `unresolved_concern`
  cognition trigger fire within the bounded window (the 2026-09-26
  end-to-end test, re-run at streak ≥ 10).
- Empty inbox, no expiries, no answers → sidecar file untouched if absent,
  byte-identical if present; expectations dict untouched (no-op).
- Full existing suite green, including `tests/test_inbox_expectations.py`
  (19 tests).

## Revert signal

- Confidence ever below 0.15, or resurfacing stops at high streak
  (over-correction into learned helplessness — the failure mode this
  mutation must not become).
- Streak increments on any path other than genuine expiry (fresh-prompt
  registration, read-only commands, dream ticks).
- Streak decrements below 0, or decrements when `resolve` was a no-op
  (no live expectation).
- Sidecar written when `N` did not change (no-op violation).
- Proposition text, trigger math, or any other behavior altered.

## assess_after

2026-10-08 (same fitness window as the other open mutations).
