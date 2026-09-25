# Stage A protocol decisions log

Decisions adopted before the protocol is written. The protocol itself is
written only after all pre-freeze gates clear.

## Adopted 2026-09-25 (Calibos 599bb32; ChatGPT reply same day)

- **Readiness gates, two categories** (keeps the history of why each entered):
  - *Experimental-control gates* (original five): categorical commitment-state
    semantics; quiescent snapshot procedure; RNG/wall-clock audit.
  - *Substrate-correctness gates* (discovered during repair): subjective-
    transduction boundary; temporal fail-closed rehearsal semantics.
  All five close before freeze.
- **Stage A measurement interval: dream generation OFF, dream rehearsal OFF,
  consolidation OFF.** Cleanest first experiment: ordinary lived experience,
  retrieval, cognition, persistent state. Later stages reintroduce mechanisms
  one at a time (amplify/attenuate/redirect/destabilize). Freeze the FULL
  system including all five gates, then disable at runtime per protocol —
  "off" never means "unfixed."
- **"Disabled" is an observable experimental condition.** Harness asserts at
  run start AND run end: no consolidation proposal scan occurred; no proposal
  journal changed; no archive availability changed; no records became
  unavailable through consolidation; no dream ticks ran; no rehearsal
  mutations occurred; normal tick/cognition pipeline remained functional.
  Converts "we think it was off" into evidence it was not a hidden mediator.
- **Hypothesis (one sentence):** "Two otherwise *causally* identical
  instances, exposed to different bounded experiences, subsequently become
  measurably different." "Causally identical" because irrelevant filesystem
  metadata, timestamps, PIDs, serialization details may differ without being
  causal — the manifest defines what counts as causal identity.
- **Causal structure:** identical causal state → controlled experience
  difference → identical subsequent external sequence → observed downstream
  divergence. Alternative explanations constrained by manifest, scheduler
  controls, RNG audit, disabled subsystems.
- **Endpoint does not require the treatment memory to remain retrievable.**
  Valid chain: treatment → expectation change → changed interpretation of a
  later ambiguous event → concern change, even if the original memory left
  the active workspace. Success = causal downstream state, not perpetual
  accessibility. Otherwise the experiment would reward explicit retention
  over historical incorporation. Strongest machine-auditable chain remains
  experience → altered retrieval/appraisal → altered cognition → altered
  persistent state → altered commitment trajectory/conduct, but not every
  link must be simultaneously visible at final measurement.
- **Temporal fail-closed adversarial battery includes:** negative, null,
  missing, non-integral, AND future-relative-to-engine ticks — a syntactically
  valid integer can still be causally impossible.

- **Interpretation ladder** (ChatGPT formulation, adopted verbatim):
  1. Experience changes workspace entry, nothing durable downstream changes
     → history-sensitive curation only.
  2. Experience later changes endogenous recurrence, interpretation, concern,
     expectation, relationship state, or another persisted variable
     → integrated history-dependent state propagation.
  3. Experience changes a commitment trajectory
     → stronger evidence of intention-like historical propagation.
  4. Experience changes conduct under a later matched situation
     → strongest behavioral form of the Stage A result.
- **Research question (one sentence):** Do two otherwise identical instances,
  exposed to different experiences, become measurably different later?
- **Independent variable:** lived experience. **Held fixed:** memory, salience,
  retrieval, continuity substrate. **Dependent variable:** downstream
  persistent organism state. Stage A does NOT manipulate salience.
- **Consolidation during Stage A:** disabled during the measurement interval
  for the first existence proof (removes a causal pathway; easier to
  interpret). "Disabled" must be a tested configuration: the harness verifies
  consolidation is actually off and that off does not break the tick pipeline.
- **Coordination rule:** every substantive architecture claim names the exact
  Calibos commit inspected; cross-system comparisons also name the exact
  Pretorius reference.
- **Provenance chain for external assertions:** an externally supplied
  assertion cannot acquire autobiographical standing merely because it was
  asserted. A later independently experienced event may generate its own
  autobiographical record; the original assertion remains attributed-external.
  B may corroborate, contradict, refine, or leave A unresolved.
- **Temporal provenance fail-closed:** missing/null/non-integer/negative/
  invalid dream tick → no rehearsal mutation + explicit diagnostic, never
  reinterpreted as tick 0 (tick 0 is legitimate engine time, not an error
  sentinel). Counts toward Stage A readiness for the dream-rehearsal pathway.

## Pre-freeze gates still open (2026-09-25)

1. Categorical commitment-state semantics (engine stores "broken" where CLI
   prints "released" — confirmed against the tree).
2. Quiescent snapshot procedure.
3. RNG and wall-clock audit.
4. Subjective-transduction mutation (in builder/critic loop as of 2026-09-25).
5. Temporal fail-closed hardening (queued; runs after the transduction
   mutation lands to avoid two coordinators editing the rehearsal path).
