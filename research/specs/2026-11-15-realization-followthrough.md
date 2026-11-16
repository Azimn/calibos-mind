# Spec: realization follow-through tracker (2026-11-15)

## What
A read-side instrument, in the family of the personality-drift (2026-11-09)
and goal-structure (2026-11-10) instruments: it measures whether a recorded
consequence ever feeds back into conduct. It does not change any action,
retrieval, or cognition path.

Domain 22 (Internal causality), check 9: "Behavioral consequences are not
traceable across time." The causal chain IS recorded (counterfactual traces,
prediction-error traces, realization records with swing/convergence ticks),
but whether a recorded consequence ever changes later conduct is untested —
the 2026-11-14 domain-21 pass noted no conduct change observably follows a
realization record. This instrument supplies the missing evidence.

## Mechanism
New module `calibos_mind/followthrough.py` + `mind followthrough` CLI report.

- The tracker keeps a sidecar `followthrough.json` holding a capped,
  append-only log of action-bearing waking ticks: `[[tick, action], ...]`
  (cap 256 rows, mirroring the engine trace cap; oldest dropped on append).
  Observed once per waking tick from the engine's chosen action
  (`last_intention` / `heartbeat()` result `"action"`), exactly the source
  personality.py uses. Ticks with no chosen action record nothing.
- The report scans the live store for realization records using the existing
  canonical parser `interoception.parse_realization_text` (never a substring
  heuristic): each yields (need, swing_tick, conv_tick, felt_band,
  actual_band).
- For each realization, the report compares the action-share distribution
  in the 20 action-bearing ticks BEFORE the swing tick with the 20
  action-bearing ticks AFTER the convergence tick, as total variation
  distance (same math family as personality drift: half the L1 distance
  between the two share vectors over the union of actions, range [0, 1]).
  Per-realization row: need, swing_tick, conv_tick, before-mix, after-mix,
  tv distance. Aggregate: mean tv over measurable realizations, count
  measurable, count insufficient-window.
- A realization whose before- or after-window is not fully present in the
  action log reports `insufficient-window` for that row — never a quiet
  0.0. The aggregate excludes such rows and says so. (Genome: silent-zero
  on missing sections.)
- The tracker is read-side only: no conduct, retrieval, salience, or
  expectation path changes. Engine + cartridge byte-identical. No new
  record minting, no sidecar writes from read-only commands or dream ticks.

## Fitness function (synthetic, /tmp stores only, never the live store)
- Scripted store: one realization record (need comfort, swing s, conv c)
  with an action log whose 20 pre-swing ticks are all `seek_contact` and
  20 post-conv ticks all `approach` -> that row reports tv exactly 1.0,
  with correct before/after mixes.
- Identical before/after mixes -> tv exactly 0.0.
- Mixed known distributions -> tv matches hand-computed value.
- Two realizations -> two rows, each with its own windows (no cross-talk).
- Realization whose swing predates the action log (window absent) -> row
  reports insufficient-window, aggregate excludes it and names the count;
  no fabricated 0.0.
- Read-only sweep (drift, status, review, inbox, followthrough report,
  predictions, goals) leaves the sidecar byte-identical and mints no
  sidecar on a fresh store.
- Dream ticks never append to the action log and never write the sidecar.
- `init --force` wipes the sidecar, including a corrupt one.
- Corrupt sidecar shapes (truncated JSON, wrong types, row not
  [tick, action], negative tick, tick out of order) fail loud before the
  tick advances; no silent load.
- Determinism: identical tick/action sequences -> byte-identical sidecar;
  save/load round-trip identical.
- Full suite green; no existing test regresses.

## Revert signal
- A row reports a tv distance for a realization whose before- or
  after-window is absent from the action log (fabrication).
- The sidecar is written by a read-only command or a dream tick.
- The sidecar survives `init --force`.
- Any existing test regresses.
- In vivo (assessor's note): rows systematically 0.0 across the window
  while conduct visibly changed around realizations is a dead instrument,
  judged at assessment, not an auto-revert; universal insufficient-window
  while realizations keep minting means the log cap is too short, also
  judged at assessment, not a revert trigger.

## Assess after
2026-11-29 (14-day window).

## Standing rules
- Tests on synthetic /tmp stores, never the live store.
- Dated CHANGELOG.md entry. No commit (the backup job handles that).
- Builder/critic loop per research/builder-critic-loop.md; critic briefed
  only with this spec + the diff; every objection as a failing test or a
  concretely violated invariant; regression genome checked item by item.
