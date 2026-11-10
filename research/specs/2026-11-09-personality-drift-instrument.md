# Spec: personality-drift instrument (Domain 16, 2026-11-09)

## What
A small instrumentation module, `calibos_mind/personality.py`, plus a
`mind drift` line, that gives Domain 16's most diagnostic check ("No
personality drift" — baseline: "untestable yet, the metric is the
falsifier") a number. It measures drift of the conduct mix: the L1
distance between the trailing-window action shares and the lifetime
action shares, alongside a trait-state snapshot (formed-habit
strengths, authored-vs-learned preference counts, narrative-claim
confidences) so a drift reading can be attributed.

This is measurement, not a new behavior: it observes conduct, it never
steers it. No action selection, retrieval, or cognition path changes.
No cartridge or engine change. No store migration (new sidecar only).

## Why
The 2026-11-09 Domain 16 pass found drift is now testable in the
salience/conduct sense but unmeasured at the trait level: authored
salience share fell 31% -> 0.49% since baseline, the action mix moved
(approach 43% -> 20%, seek_contact 29% -> 54% across trace ticks
961-1040), authored habits atrophied while formed habits took over
conduct — and none of that is instrumented as a drift reading. R
measures grown/authored salience, not personality. Without a drift
instrument the check stays an eyeball comparison across runs.

## Design
- New sidecar `personality-drift.json` at the repo root, owned by a
  `ConductDriftTracker` in `calibos_mind/personality.py`.
- The tracker is fed ONLY from the waking-tick path (`cli._run_tick`),
  after the tick's action is chosen: it records the tick and the chosen
  action (the engine's `last_intention` / chosen action for that tick).
  Ticks with no chosen action record nothing.
- Sidecar shape (all plain JSON, deterministic key order):
  `{"lifetime": {"<action>": <count>, ...}, "window": [[tick, action], ...]}`
  where `window` holds the last 100 action-bearing ticks (oldest
  dropped on append; cap is a module constant `WINDOW = 100`).
  `lifetime` counts every action-bearing waking tick since the sidecar
  was created. No other fields, no timestamps, no thought text.
- `drift()` returns `(distance, window_shares, lifetime_shares)` where
  distance is the L1 distance between the window shares and the
  lifetime shares over the union of actions (range [0, 2]). When the
  window holds fewer than `MIN_WINDOW = 20` action-bearing ticks, the
  drift is reported as `None` ("insufficient window") and the sidecar
  still accumulates.
- `mind drift` gains one line after the trigger-KL block, e.g.:
  `conduct drift (last 100 vs lifetime): 0.42  window {seek_contact: 0.54, approach: 0.20, ...} lifetime {...}`
  or `conduct drift: insufficient window (n/20)` when under the
  minimum. The line is read-only: `mind drift` never writes the
  sidecar. `mind status` is unchanged.
- Trait snapshot: `drift` output also carries a `traits` dict —
  `{formed_habit: strength}` for each formed habit, counts of authored
  vs learned preferences, and narrative-claim confidences — read live
  from the subject at report time, never persisted in the sidecar.
  (Snapshot content is diagnostic context for the drift reading, not a
  second mechanism.)
- Wiring constraints (hard, inherited from every prior mutation):
  - Read-only commands (`drift`, `status`, `review`, `inbox`, ...) and
    dream ticks NEVER write the sidecar; verify byte-identical sidecar
    across a read-only sweep and dream ticks.
  - `cmd_init --force` wipes the sidecar (add the path constant to
    `cli.py`, its filename to `tests/conftest.py` `_FILENAMES`, and its
    name to `REDIRECTED` — the conftest docstring mandates all three,
    and `test_sidecar_guard.py` enforces it).
  - Determinism: identical scripted tick sequences -> byte-identical
    sidecar. No wall-clock, no randomness, no model calls.
  - A malformed sidecar fails closed before the tick runs: the tracker
    constructor raises ValueError at load (mood/interoception
    precedent), the engine tick does not advance, and the corrupt bytes
    are left untouched — never silently reset.
  - Engine + cartridge byte-identical; no fingerprint change; no
    migration.

## Fitness function
On synthetic /tmp stores (pytest, conftest fixture — never the live store):
1. Two scripted windows with different action mixes show drift > 0.4
   (e.g. 20+ ticks approach-dominant, then 20+ ticks
   seek_contact-dominant, window 100 not yet full — use a small
   WINDOW override or a directly-constructed sidecar for the distance
   unit tests; the end-to-end test drives real ticks).
2. Identical action mixes in window and lifetime show drift ~0
   (exactly 0.0 when the window is the whole lifetime).
3. Distance is bounded: 0 <= drift <= 2.0 on every constructed case,
   including disjoint action sets (expect exactly 2.0).
4. Window cap: after 100+ action-bearing ticks the sidecar window
   holds exactly 100 entries, oldest dropped, lifetime counts keep
   growing.
5. Below MIN_WINDOW action-bearing ticks, drift reports insufficient
   window and the sidecar still accumulates.
6. Read-only sweep (`drift`, `status`, plus a dream tick) leaves the
   sidecar byte-identical.
7. `init --force` wipes the sidecar; a fresh store starts empty.
8. Determinism: identical scripts -> byte-identical sidecar bytes.
9. Trait snapshot: formed-habit strengths, authored/learned preference
   counts, and narrative confidences in the report match live subject
   state.
10. Full suite green; no existing test regresses.

## Revert signal
- Drift reports non-zero on identical action mixes, or exceeds 2.0,
  or is exactly 2.0 while window and lifetime share any action.
- Sidecar written by a read-only command or a dream tick.
- Sidecar surviving `init --force`.
- Any existing test regresses.
- In vivo: drift stuck at 0.0 across a window where the action mix
  visibly changed, or drift pinned at the max while conduct is stable
  (both indicate the window/lifetime bookkeeping is wrong, not that
  personality is stable).
- Revert = delete `calibos_mind/personality.py`, its import/wiring in
  `cli.py` and `drift.py`, the sidecar, and the `mind drift` line.

## Assess after
2026-11-23 (2 weeks). In-vivo fitness: drift line present in daily
`mind drift` output; drift moves when the action mix moves and sits
near 0 when conduct is steady; no revert signal fired.

## Non-goals
- No wiring of preferences into action selection (that is a behavior
  change, larger and riskier — a proposal for Jay, not this loop).
- No public/private persona split and no installed quirks or
  characteristic mistakes (Domain 16 rule: what should emerge is not
  installed).
- No trait-conflict arbitration (same rule; the concise-vs-depth
  preference tension is noted, not mechanized).
