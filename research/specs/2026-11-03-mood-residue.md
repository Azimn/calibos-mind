# Spec: slow affective residue ("mood tone") — 2026-11-03

## Source finding

Daily articles review, Domain 10 (Affect and emotion), cycle 2, 2026-11-03.
Verdict: partial, unchanged from baseline but strengthened. Two checks are
now addressed (delayed emotional realization, felt/actual mismatch) and the
2026-11-02 state-congruent gain falsified the memory/attention clauses of
the no-effect check. Still absent, among others: "No diffuse mood without
identifiable cause" — this mind has no mood state at all (all 1604 live
records carry `affect=[]`; `calibos_mind/statecongruence.py` says so
explicitly). A mood that is *causeless* would be theater and must not be
installed. A mood as *residue* is not: a slow-moving background state that
outlasts its trigger is exactly what "diffuse mood" means mechanistically,
and it is load-bearing only if it changes what is recalled, not merely
what is labeled.

## What changes

A new module `calibos_mind/mood.py`, mirroring the `statecongruence.py`
pattern (same scale, same flat/bounded discipline, same provider pattern):

1. **Sidecar `mood.json`** (next to `interoception.json`): per-pressure
   slow EMA ("residue") over the deviation of each engine pressure from
   its baseline (`engine.pressure_baselines`: trust, attachment, shame,
   fear, anger, arousal, self_story_stability). Leaky integrator updated
   once per waking tick in `_run_tick` (the same waking-only seam the
   feeder, interoception update, and habit machinery use):
   `residue = residue * (1 - ALPHA) + ALPHA * deviation`, with ALPHA
   declared as a constant giving a half-life in the ~100–200 tick range
   (builder pins the value and justifies it; the constant must be
   declared and tested, never tuned silently). Baselines are read from
   the engine, never hardcoded.
2. **View-time gain.** A new workspace provider `mood_provider`
   (mirroring `felt_levels_provider` in `workspace.py`, wired in
   `subject.py` like `_live_felt_levels`) supplies, per view, the current
   residues plus the current fast deviations. A pure function
   `mood_residue_boost_for(record, residues, fast_deviations)` returns a
   flat `+0.5` (same scale as `STATE_CONGRUENT_BOOST`) when ANY concept
   of the record names a pressure whose residue is at or above a declared
   threshold (suggested 0.20, builder pins it) **and** whose fast
   deviation is back near baseline (suggested |dev| <= 0.05, builder pins
   it) — i.e. the mood fires only after the trigger has settled, so it
   cannot duplicate the state-congruent gain during a live spike. The
   gain is added in the same view sort-key expression as the existing
   gains; gains may stack per existing precedent, and non-boosted
   relative order must be preserved.
3. **No content is scripted.** The gain only reorders the salience
   window. No records are minted, no text is written, no engine or
   cartridge code changes (fingerprint unchanged).

## Hard constraints (from the regression genome — the critic checks each)

- Waking ticks only: `_run_tick` updates the sidecar; read-only commands
  (`drift`, `status`, view construction) and `dream_tick()` never write
  it. Scope no-write assertions to the sidecar (byte-identical across
  read-only sweeps and dream ticks); CLI construction itself rewrites
  the SQLite payload row, so process mtime is not the assertion.
- `init --force` wipes `mood.json` (same place as the other sidecars).
- Corrupt sidecar fails loud, never silent-delete or silent-zero.
- Determinism: identical tick/pressure sequences yield byte-identical
  sidecars and windows. No clock, no RNG in the gain path.
- Malformed provider input (None, non-mapping, non-numeric residues)
  yields 0.0, never a crash inside view construction.
- The boost never admits a record that dedupe/per-class caps excluded;
  verify with a targeted test plus the full suite.
- Tests run on synthetic `/tmp` stores only — never the live store.
- Dated CHANGELOG.md entry. No commit (the backup job handles that).
- First step: run the full suite on the unmodified tree. If failures
  pre-exist, report them and stop rather than building on red.

## Fitness function (synthetic)

1. Spike-and-settle: store A runs a pressure (e.g. attachment) spiked to
   1.0 for ~30 ticks, then back to baseline; store B stays at baseline.
   A record with concept `attachment` ranks strictly above an identical
   control record in A's view but not in B's.
2. Live-spike block: while the fast pressure is still high, the mood
   gain is exactly 0.0 (record ranks equal to control) — the
   fast-settled clause holds.
3. Decay: after a long baseline stretch (~1000 ticks), residue falls
   below threshold and windows are byte-identical to the no-mood
   baseline ranking.
4. Flatness: residues elevated on three pressures, record matching
   several → still exactly +0.5, never scaled.
5. Zero-gain cases: no provider, empty mapping, malformed provider →
   0.0 and byte-identical windows to pre-gain ranking.
6. Order preservation: non-boosted relative order preserved; boosted
   record never bypasses dedupe/per-class caps.
7. Isolation: read-only sweep + dream ticks leave `mood.json`
   byte-identical; `init --force` wipes it; corrupt sidecar raises;
   byte-identical replay.
8. Full suite green.

## Revert signal

- Gain fires while the fast pressure is NOT settled (fast-settled clause
  broken — the mood duplicating a live state gain).
- Mood-boosted records exceed 50% of the unpinned window over a day
  (dominance).
- Any write from a read-only or dream path; sidecar surviving
  `init --force`; any existing test regresses.
- In vivo: gain firing on a pressure whose residue never had a genuine
  spike (phantom residue) IS a revert trigger. Gain never firing across
  the window is inert, not a revert trigger (pressure-named concepts in
  records may be sparse, same caveat as the state-congruent gain).

## Assessment

Register in `articles-rotation.json` as shipped-awaiting-assessment with
`assess_after` 2026-11-17 (14-day window). The design lead adjudicates
ship/revert/escalate after the critic signs off or 3 rounds elapse
(early adjudication on evidence after 2 rounds if the loop drags).
