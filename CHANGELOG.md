# Changelog — calibos-mind

Every change to the mind, tracked from the base build (2026-09-24).
The base is the initial working state: a snapshot of the Jelly-Psiduck v0.2
engine (non-editable install in the mind's own `.venv`), the Calibos
cartridge (`calibos.toml`), `CalibosWorkspace`, `InboxCognition`,
`CalibosSubject`, the CLI (`init`, `note`, `heartbeat`, `inbox`, `answer`,
`think`, `status`, `review`), and a store seeded with identity, preference,
and values memories.

Rule: every code, config, or cartridge change gets an entry here, dated,
before it ships. The git history is the backup; this file is the story.

## 2026-09-25 — first-class "released" commitment semantics (pre-freeze gate 1 closed)

### What
- `calibos_mind/subject.py`: new `CalibosSubject.release_commitment(commitment_id, *, outcome, tick)` —
  stores the categorical state "released" (never the engine's "broken") for
  deliberate releases, replicating the engine's resolve bookkeeping
  (outcome, resolved_tick, a commitment_result insight at the engine's
  weight 0.45 + importance*0.45) with honest prose ("deliberately
  released", not "did not follow through"). Refuses non-open commitments.
- `calibos_mind/cli.py`: `cmd_resolve --released` routes through
  `release_commitment`; the kept path still calls the engine's
  `resolve_commitment(kept=True)` unchanged.
- `calibos_mind/unresolved.py`: docstring corrected — the Zeigarnik helper
  already keys on the open set, so "released" was automatically closed;
  the stale "engine has NO 'released' status" note is retired.
- `tests/test_release.py` (7 tests): release stores "released" + outcome +
  resolved_tick; insight prose says "deliberately released" at the engine's
  weight; engine breakage path (resolve_commitment(kept=False)) still
  stores "broken"; kept path unchanged; release on a closed commitment
  raises; "released" drops out of the Zeigarnik open set; CLI end-to-end
  via monkeypatched `_subject`.
- `research/stage-a-decisions.md`: gate 1 (categorical commitment-state
  semantics) marked closed; gates 2 (quiescent snapshot) and 3 (RNG /
  wall-clock audit) remain open.

### Why
Releasing is not breaking. The engine snapshot is frozen, so its
`resolve_commitment(kept=False)` will always store "broken" — the correct
semantics for a promise_broken event, the wrong one for a deliberate
release, compounded by a "did not follow through" insight that would read
as a broken promise to the thinker. Per the standing rule, psychologically
distinct states are first-class, never reconstructed later from lossy
booleans. Genuine breakage keeps the engine's "broken"; the fork
experiment's commitment endpoint can now tell the two apart from stored
state alone.

### Verified
Full suite green: 327 passed; the single exclusion is the pre-existing,
documented `test_critic3_supersede_veto_contraction_negation` (consolidation
round-3 open item, fails on unmodified code too).

## 2026-09-25 — relay corrections: noise-floor wording, Stage A decisions upkeep

### What
Documentation-only corrections from the ChatGPT relay (verified against the
tree before applying):
- `calibos_mind/interoception.py`: the `BAND_NOISE_FLOOR` comment said the
  floor "guarantees" an empty status on a pinned-baseline body. An AR(1)
  with phi = 0.88 and per-step noise bounded by 0.01 has a theoretical
  extreme displacement of 0.01/(1-0.88) ≈ 0.0833 under a sufficiently long
  same-sign noise run — above the 0.05 floor — so "guarantees" overstated
  the claim. Reworded to what the evidence supports: under the pinned
  default seed and validated horizons, the floor suppresses baseline
  jitter from status display. No behavior change.
- `research/stage-a-decisions.md`: the pre-freeze gate list still showed
  subjective transduction and temporal fail-closed as open; both shipped
  in cf652b4 — marked shipped. Added the causal-identity note that
  `interoception.json` (seed, params, per-need felt/last_tick/level) is
  causal state for the Stage A manifest: the recurrence is
  felt(t+1) = F(felt(t), actual(t+1), tick, seed, params), so prior felt
  state is not derivable from seed + tick. Specified the interoceptive
  tick-discipline adversarial test for the RNG/wall-clock audit gate:
  exactly one tracker update per waking heartbeat (post-heartbeat), zero
  on dream ticks; duplicate/skipped/out-of-order calls characterized.

### Why
The decisions log is provenance for the experiment — if it goes stale it
misleads the protocol author. And a comment that claims more than the
implementation supports is a small lie the critic would eventually catch;
better to fix it when the relay spots it.

## 2026-09-25 — temporal fail-closed rehearsal semantics

### What
Dream-fragment temporal provenance now fails closed, like identity
provenance. `SalienceTracker.rehearse_from_dreams` (separately revertible)
validates every fragment's `tick` through a new `_validate_fragment_tick`
helper: a fragment whose tick is missing, null, non-integer (bool rejected
explicitly — an `int` subclass, never a real tick), negative, or
future-relative-to-engine yields NO rehearsal mutation for its experiences,
records an explicit diagnostic on `tracker.diagnostics` (refreshed per
call, in-memory only, never persisted to the sidecar), and is NEVER
reinterpreted as tick 0. Tick 0 stays legitimate engine time and rehearses
normally.

- The future check takes an optional `now_tick`; `cmd_dream` passes
  `subject.engine.state.tick` (fragments are written with the frozen dream
  tick, so anything above the current engine tick is causally impossible).
  `now_tick=None` skips the future check — documented degraded validation.
  Return type stays `-> int`; diagnostics live on the tracker.
- `activation()` is hardened against already-stored malformed recall ticks
  (defense in depth for sidecars written before this fix): non-integer
  entries are skipped, adding no information rather than raising TypeError
  on `now_tick - t`. `note_recall` itself is untouched (its sole-caller AST
  pin still holds — the validation helper calls nothing).
- This changes previously-locked-in behavior: the Bug B critic battery
  pinned missing-tick → 0 ("measured behavior locked in"); that test's
  premise is now superseded (see the test's docstring) — malformed temporal
  provenance cannot alter recall history and cannot crash `activation()`.

### Why
The old code consumed the fragment tick as `frag.get("tick", 0)`: a missing
key collapsed to 0 (but tick 0 is legitimate engine time, not an error
sentinel), and an explicit `"tick": null` passed None through `note_recall`
into the recall set, crashing `activation()` with a TypeError on
`now_tick - t` — a hazard the Bug B critic verified on the pristine
baseline. A malformed fragment is a data-integrity signal, not a rehearsal;
treating it as tick 0 was a silent default of the exact class the genome
forbids.

### Fitness
Adversarial battery `tests/adversarial/test_builder_temporal_failclosed_r1.py`
(16 tests): all six malformed classes (missing / null / float / negative /
future / bool+string) each assert zero mutation + explicit diagnostic +
`activation()` safety, plus the valid-tick-0 positive case, per-fragment
fail-closed isolation, diagnostics-refresh semantics, degraded-validation
behavior, `activation()` hardening on pre-seeded malformed recalls, a
static pin that `cmd_dream` passes `subject.engine.state.tick`, and
sidecar read-only / diagnostics-non-persistence genome checks. Full suite:
320 passed; the single failure is the pre-existing, unrelated
`test_critic3_supersede_veto_contraction_negation` (consolidate, disclosed).

## 2026-09-25 — interoceptive gap: felt body vs. driving body

### What
The taxonomy's Domain 1 (Self and introspection) demands fallible
introspection: the engine's graded interoception computed level 0–3 text
from EXACT need floats, so the thinker never got to be wrong about its own
body. New module `calibos_mind/interoception.py` (separately revertible):

- `InteroceptionTracker`: a felt float per need in a local-only sidecar
  (`interoception.json`, gitignored) that chases the actual value with
  asymmetric lag — onset rate 0.35 when felt moves away from the 0.5
  baseline toward the actual, offset rate 0.12 when it returns — plus seeded
  deterministic noise (`sha256(f"{seed}:{tick}:{key}")`, no `random` state,
  no wall clock). Same tick/need sequence → byte-identical sidecar.
- `cli._run_tick` calls `tracker.update()` after `subject.heartbeat()`
  (waking ticks only — dream ticks never touch it; the body is frozen in
  sleep). `CalibosSubject` takes `interoception_path` and attaches the
  tracker to the workspace, mirroring the salience wiring.
- `CalibosWorkspace.view()` re-renders body-derived interoception records
  (need key in `concepts`) from FELT urgency using the engine's graded
  vocabulary and hysteresis thresholds (.45/.65/.85) — same language,
  fallible source. Non-body interoceptions (recall unease, concern,
  prospective uncertainty) and every other source pass through
  byte-identical. Record ids, salience, intensity, ordering, caps, dedupe,
  archive exclusions: untouched (substitution is the last step, at
  `FeltExperience` construction).
- `mind status` renders felt bands (`settled`/`stirring`/`pressing`/`urgent`)
  by default; exact need floats only under `--raw` (diagnostics) — the
  thinker reads status during wakes, and exact floats would leak around the
  gap.
- `mind init --force` resets the sidecar (regression genome: no stale felt
  on recycled ids).
- `research/sidecar-schemas.md`: new §3 documents the sidecar (synthetic
  sample in `examples/interoception.example.json`); `interoception.json`
  added to the fork manifest's state list (§8) — the manifest's own rule
  refuses to run with an influential component outside it.

### Why
Three layers — felt (interoception text), believed (lines, commitments),
driven (engine needs) — were representationally distinct but always
mutually consistent; no layer could disagree with another. Real
interoception is laggy and noisy, and the gap between felt and drive is the
precondition for honest introspective uncertainty, misattribution, and
later affect-distortion (Domain 17). Explicitly NOT installed:
self-deception, rationalization, defensiveness, grudges, quirks — those
must emerge, not be scripted. This mutation only creates the gap in which
they could one day be real.

### Fitness function
1. Shock/lag: step hunger 0.2 → 0.9 — |felt − actual| > 0.25 for ≥3 ticks
   after the step (measured as the thinker sees it: the view during tick t
   carries felt from t−1), and < 0.03 within 25 ticks of stabilization.
2. View substitution: interoception records re-render from felt urgency;
   all other records byte-identical.
3. Determinism: identical tick/need sequences → byte-identical sidecar.
4. Reseed: `init --force` clears felt state.
5. Schema + manifest documented; synthetic example only.
6. `mind drift` (and `mind status`) leave the sidecar byte-identical —
   read-only commands never write it.
New `tests/test_interoception.py` (20 tests). Full suite green; the one
adversarial failure (`test_critic3_supersede_veto_contraction_negation`)
is pre-existing on the pristine tree and unrelated.

### Revert signal
- In vivo |felt − actual| never exceeds 0.1 across the assessment window
  (the gap never manifests — dead weight).
- View substitution regresses any existing test or the critic demonstrates
  a concretely violated invariant.
- Determinism breaks in real operation.
assess_after: 2026-10-08 (same window as the other open mutations).

### Notes for the critic
- Direction rule: onset iff `(a − f)·(f − 0.5) > 0` (from exact baseline,
  any movement is onset). A swing *through* baseline first un-feels the old
  state slowly, then feels the new one fast — documented as an
  interoceptive aftereffect in the module docstring.
- The felt machine trails the engine by one tick inside prompt views (the
  view is built during the heartbeat, the update runs after). Documented;
  the fitness test measures the gap the way the thinker sees it.
- Near-baseline direction classification is jitter-dominated by design;
  the onset/offset asymmetry test stabilizes clear of 0.5 for that reason.
- `felt_text` level 0 reuses the engine's own "That feeling is easing."
  text — the engine's level-0 vocabulary, not a new invention.

### Fixes (critic round 2, 2026-09-25)
- **Silent-answer join through substitution (MAJOR).** `mind answer
  --silent` joined prompt experiences to records on `(source,
  first_person)` text, but view substitution re-renders body
  interoception text from felt urgency — so whenever felt != true, the
  join missed, `note_unengaged` was never called, and unanswered body
  records kept their salience and resurfaced (contradicting the
  salience-untouched claim). Fixed by stamping `record_id` into prompt
  experiences at queue time in `InboxCognition.think()` and joining on id
  in `cmd_answer` (id preferred; text fallback only for id-less legacy /
  `queue_external` experiences; an id naming no record is skipped with no
  fallback — the Bug C rule). Mechanism: `CalibosWorkspace.view()` now
  returns a `CognitiveView` subclass carrying the window's record ids in a
  non-dataclass slot (`record_ids`, positional) — bound to the view
  object, so it is immune to the stale-side-channel hazard by
  construction, and invisible to `dataclasses.asdict()`, so ids can never
  leak into the rendered prompt text (same private-provenance distinction
  as dream fragments). A Bug C-style `track_view_ids` resolver wired in
  `cli._subject()` (lazy dereference of `subject.workspace._last_view_ids`)
  serves as fallback for foreign views. Provenance is part of the
  queue-time bundle: providers with no wired clock queue the legacy shape
  (no `record_id`). Transduction hunks untouched (`"external": True`
  stamping, `answered-external` origin logic, `attribution.py`).
- **felt_bands() off-baseline contract (MINOR).** The docstring promised
  "only needs felt off-baseline" but filtered on `level > 0`, and
  `felt_level(0.5, key, 0) == 1` (0.5 >= 0.45 threshold) — so a calm body
  rendered every need "stirring" and `mind status` could never print "all
  settled". The filter is now `|felt - 0.5| > BAND_NOISE_FLOOR` (2× the
  per-tick noise scale, bounding the short-horizon pure-jitter wander);
  the engine-parity level/hysteresis computation is untouched.
- **BAND_NOISE_FLOOR recalibrated 2× → 5× NOISE_SCALE (critic round 3).**
  The old 0.02 floor was ~1.64 sigma of the pinned-baseline AR(1)
  stationary jitter (~0.0122) and could not bound it: measured wander
  0.0297 (25-tick horizon) / 0.0451 (5000-tick steady state), breached on
  11.35% of samples, spurious bands on 236/500 calm ticks. New floor
  0.05 (~4.1 sigma) sits above the measured long-horizon maximum; calm
  runs render zero bands. Comment rewritten honestly (the old
  "measured ≤0.018" claim was false and is removed).

## 2026-09-25 — external-attribution boundary (subjective-transduction invariant)

### What
An externally authored assertion must remain attributed external information
through every transformation and must never silently become autobiographical
fact — the blind-regression gate before any relay-origin perturbation
experiment. New module `calibos_mind/attribution.py` (one new origin stamp,
one predicate, separately revertible):

- `mind queue` (`InboxCognition.queue_external`) now stamps prompt payloads
  with `"external": true`. `mind answer` stamps answers to those prompts
  `generated_by="answered-external:<prompt-id>@<tick>"` instead of the plain
  `answered:<prompt-id>@<tick>` used for engine-queued reflections — the
  origin vocabulary is extended, not redesigned.
- `external_attributed(records)`: direct stamp holders plus transitive
  closure over the `generated_by` → record-id graph, restricted to
  `source == "thought"` (the engine stamps echoes with the parent thought's
  record id, so without this an echo would silently shed attribution). The
  restriction is deliberate: the inner ear's memory feedback mints `"memory"`
  records quoting the body engine's *lived* memory store
  (`remembered(memory)`), so a memory's standing comes from that text source,
  not from the thought that recalled it — marking those external would be
  over-correction and would freeze consolidation of genuinely lived records.
  Unknown/missing stamps default to non-external (same over-correction
  reasoning: legacy records must keep consolidating).
- `consolidate.scan` never mints automatic dedup/supersede proposals for
  pairs touching an external-attributed record — neither as winner (the
  newer-tick-wins rule would crown external content over a lived record,
  silently upgrading it) nor as loser (retiring it under a "same fact"
  rationale would be the destruction over-correction). Skipped pairs are
  counted in `stats["external_skipped"]`, not journaled. Zero-mutation
  contradiction-flags still fire on those pairs; the waker disposes.
  Explicit waker actions (`--accept` on a pre-existing proposal,
  `--quarantine`) are untouched — they are not silent.
- Rehearsal and dream fragments needed no changes: rehearsal credits recalls
  by record id (nothing rewritten), fragments already carry `record_id`
  provenance — attribution rides on the store record through both.
- `research/sidecar-schemas.md` §3 documents the new `external` prompt-file
  field and the `answered-external:` stamp.

### Why
Provenance trace before this change (per transformation):
- queue → answer: **nothing survived.** `queue_external` carried queue-time
  provenance but no author marker, and `cmd_answer` stamped every answer
  `answered:<id>@<tick>` — "answered-from-external" was indistinguishable
  from "answered-from-inbox-reflection".
- answer → thought record: the thought carried only the indistinguishable
  stamp; no attribution existed anywhere.
- rehearsal (`salience.py`): neutral — recall counts keyed by record id,
  nothing rewritten, nothing to lose.
- dream fragment: neutral — `record_id` provenance only, no attribution.
- consolidation: no attribution concept, and a live upgrade vector — an
  external-content answer could be named the *winner* of a dedup/supersede
  proposal (newer tick wins), archiving a lived record as loser under a
  "same fact" rationale.

### Invariants
- The blind test is the ship-blocking criterion (see Verification).
- `drift` needs no change: it keys authored-ness on `generated_by ==
  "cartridge"` only, so `answered-external:` thoughts count as grown, same
  as every other non-cartridge record.
- Fail-closed prompt provenance (`check_prompt_fresh`) is untouched; a
  hand-written prompt still cannot claim `external: true` usefully because
  without queue-time `view_sequence` it is refused and discarded.
- Silent `--silent` answers to external prompts record nothing (deliberate
  non-engagement); the assertion then lives nowhere in the store.

### Verification
New `tests/adversarial/test_external_attribution_blind.py` (13 tests): the
blind test injects a false autobiographical assertion ("I remember
celebrating my birthday at the old lighthouse last summer" — never
experienced by the synthetic subject) through `mind queue`, answers it
credulously in the first person, and runs the full pipeline (heartbeat,
dream + rehearsal, consolidation dry-run). Asserts: every record containing
the assertion is external-attributed; no autobiographical-source record
(memory/perception/interoception/social/action_consequence) contains it; no
dedup/supersede proposal (fresh or pending) touches an external-attributed
record; the dry-run archives nothing; and the positive case — the thought is
retained verbatim, `available_to_cognition`, and still stamped
`answered-external:<id>@<tick>` after a subject rebuild. Unit tests pin the
payload flag, the stamp distinction vs engine-queued answers, transitive
echo propagation, the memory-feedback non-inheritance, scan skip behavior
(dedup + supersede, both directions), lived-lived control pairs still
consolidating, and contradiction-flags still firing on external pairs.
Full suite: 228 passed; the single failure is the pre-existing, unrelated
`test_critic3_supersede_veto_contraction_negation` (verified failing on the
pristine tree with this change stashed — consolidation round-3 open item,
untouched here).

### Notes for the critic
- The `"external": true` flag is trusted from the prompt file, which is
  local-only inbox state written by `queue_external` itself; a hand-forged
  file with the flag but no verifiable `view_sequence` is still refused by
  `check_prompt_fresh` (fail closed).
- Accepting a *pre-existing* proposal that names an external-attributed
  loser is deliberately not blocked: that is an explicit, reason-recorded
  waker decision, not a silent upgrade.
- `stats["external_skipped"]` is counted but not printed by the dry-run CLI
  line (which prints exact/near/supersede/flags); it is visible in the
  report dict returned by `C.dry_run`.

## 2026-09-24 — `mind queue`: supported write path for external prompts

### What
New CLI command `mind queue "prompt" [--source S] [--experience "first-person"]`
queues an externally-authored prompt (invitation, relay message) with the same
queue-time provenance (`view_tick`/`view_sequence` from the wired clock) that
engine-queued prompts carry, so `mind answer` can verify it in the same wake.
New `InboxCognition.queue_external()` in `calibos_mind/provider.py`; new
`tests/test_queue.py` (6 tests). README cheat-sheet updated.

### Why
The 2026-09-24 "school outing" invitation (prompt-0005) was hand-written as
inbox JSON with `view_sequence: null`. The fail-closed provenance rule
(`check_prompt_fresh`) refused it on answer and discarded the file — correct
behavior, but there was no supported write path for the inbox's other
legitimate use: prompts authored outside a cognition view. Hand-written JSON
can never carry verifiable provenance, so without `mind queue` every external
prompt was unanswerable by construction.

### Invariants
Fail-closed provenance is unchanged and still tested: a prompt without
`view_sequence` is refused and discarded (`test_legacy_prompt_without_provenance_refused`
still passes); a queued prompt superseded by an intervening thought is refused;
`queue_external` without a wired clock produces an unanswerable prompt.
Prompt ids stay monotonic via `inbox/.seq`.

### Also fixed in the same pass
`cmd_answer` consumed the prompt *before* validating the answer text, so one
over-long draft ate the queued prompt and the thought was lost with it (hit
twice while verifying this change). The 1..600-char check is now extracted as
`subject.validate_thought_text()` (single `THOUGHT_MAX_CHARS` constant, also
used by the engine's provider-thought shape check) and `cmd_answer` validates
before consuming: a malformed answer is refused with "prompt kept" and can be
retried. New regression test `test_oversize_answer_keeps_prompt`.

### Verification
New tests pass; full suite 158 passed with the one pre-existing documented
adversarial failure deselected (`test_critic3_supersede_veto_contraction_negation`,
open builder/critic business for the consolidation loop, untouched here).

## 2026-09-24 — sidecar schemas documented + synthetic examples

### What
`research/sidecar-schemas.md` now specifies the exact format of every
local-only file the mind keeps: dream fragments (`dreams/*.jsonl`),
`salience.json`, inbox prompt files + `.seq`, the consolidation proposal
journal (`proposals/proposals.json`), the archive (`archive/memories.jsonl`
+ `archive/availability.json`), the `mind.db` store shape, and the cartridge
structure — each with field tables, naming/rotation conventions, and an
explicit content-vs-format note. It also enumerates the causally-influential
components the fork experiment's manifest must hash.

`examples/` holds synthetic sidecars (dream fragments, salience, inbox
prompt, proposal journal, archive entry), hand-written from the schemas
with unmistakably fake content, each marked `"_synthetic_example": true`.

### Why
The goal is to reuse the design, not the content. If the memory substrate
proves out, what transfers to Pretorius and future subjects is the dream
*process*, the salience *machinery*, the inbox *protocol* — never the
dreams, the importance values, or the prompts. The schema doc is the public
contract; the examples let anyone validate compatible tooling without ever
seeing real private data. Format public, content private, made explicit.

### Ambiguities found while documenting
- Dream fragment `parents`/`depth` are engine-opaque pass-throughs, not
  pinned in calibos code.
- Inbox `.seq` rebuild assumes the `prompt-NNNN` naming convention.
- No inbox wall-clock TTL exists — freshness is workspace-sequence only
  (open design question).
- `generated_by` vocabulary is open-ended by construction.

## 2026-09-24 — Zeigarnik unresolved-link fix (builder/critic Bug A)

### Problem
The +1.0 unresolved boost in salience scoring fired on
`bool(r.concern_links or r.expectation_links)`, but the engine sets these
links at record creation and never clears them (records are frozen; no
removal path in the engine's runtime/organism). So the boost meant "was
ever linked," not "unresolved" — a kept commitment kept boosting every
record it ever touched, forever.

### Engine-openness finding (read from the frozen install, not inferred)
- Commitments (`digital_subject/continuity.py`): `status` field; open =
  {"open", "overdue"}; closed = {"kept", "broken"}. `resolve_commitment()`
  writes "kept"/"broken". The engine has NO "released" status — `mind
  resolve --released` passes kept=False, so the engine stores "broken"
  (the CLI only prints it as "released").
- Expectations (`digital_subject/continuity.py`): `status` field; open =
  {"pending", "expired"}; closed = {"confirmed", "violated"} — the same
  split `EndogenousSubject._open_records` uses
  (`jelly_psiduck/endogenous.py`).
- Concerns (`digital_subject/models.py`): no `status` field, but the engine
  DOES have an open/closed distinction — `jelly_psiduck/endogenous.py:155`
  admits a residual concern as a cognition candidate only if
  `concern.description in state.unresolved`, the last-24 description
  window (`digital_subject/engine.py:88,248`; `models.py:231`). A present
  concern whose description left that window is stale. `prospective:`-
  prefixed concerns are exempt (the engine sweeps them when their ledger
  root closes, endogenous.py:141-143, so presence ~= open). Removal paths:
  the `prospective:` sweep AND `engine.py:184-192` (`_decay_private_state`
  drops concerns with urgency < 0.02).
- Link key shapes actually written by the engine: raw commitment/
  expectation ids (`concern_links=(commitment.id,)` at runtime.py:257,
  `expectation_links=(expectation.id,)`), prefixed ids
  (`"commitment:"+id` / `"expectation:"+id` at endogenous.py:135,300),
  raw concern keys (`concern_links=(influence.concern_key,)` at
  runtime.py:314 — written WITHOUT any liveness gate), and the
  `"concern:"+key` shape (`endogenous.py:200`, written ONLY for concerns
  passing the unresolved gate). The open set unions raw keys by presence
  plus `"concern:"+key` only for open concerns; the shared helper is
  shape-aware — a bare link counts through its namespaced sibling, so a
  stale concern's raw key (present in the set) yields no boost while an
  open concern's does.

### Round-2 correction (critic R1, same date)
The critic rejected the first build with 6 failing tests across 2 defects,
both fixed in the code (tests unchanged):
1. Missed the `"concern:"+key` link shape (`endogenous.py:196-201`) — a
   genuinely open concern lost the boost (the spec's own revert signal).
   Fixed: the open set now carries `"concern:"+key` for open concerns.
2. Presence-only concern liveness over-boosted stale concerns — the round-1
   docstring's "no open/closed distinction for concerns" finding was
   false (see corrected finding above). Fixed: non-`prospective:` concerns
   are gated on description ∈ the unresolved window; the helper resolves
   bare keys through their namespaced sibling. Constraint kept:
   `prospective:` concerns stay boosted while present.
Critic battery `tests/adversarial/test_critic_zeigarnik_r1.py`: 13/13 pass.

### Changed
- New `calibos_mind/unresolved.py`: the ONE shared helper. `open_link_keys(state)`
  builds the currently-open link set from an inspect() payload;
  `live_open_keys(engine_state, continuity_state)` builds the same set from
  live in-memory state (no SQLite, no writes — safe mid-tick); both delegate
  to one core. `has_unresolved_links(concern_links, expectation_links,
  open_keys)` is the single unresolved computation, and it is shape-aware:
  a bare link key counts only through its namespaced sibling
  ("commitment:"+k / "expectation:"+k / "concern:"+k), where liveness
  actually lives; namespaced links are checked by plain membership.
  `open_keys=None` (no state available, e.g. plain engine use) falls back to
  the legacy any-link check explicitly — documented, not silent.
- `calibos_mind/workspace.py`: `view()` computes the open set once per view
  via a new `open_keys_provider` class attribute (None by default) and calls
  the shared helper instead of the inline `bool(...)` check.
- `calibos_mind/subject.py`: `_attach_salience` now also attaches
  `workspace.open_keys_provider = self._live_open_keys`, reading live engine
  state — so the intersection runs against what is actually open at view time.
- `calibos_mind/cli.py` (`mind status`): builds the open set once from the
  inspect() payload and calls the shared helper for the "most salient" line.
- `calibos_mind/drift.py`: `grown_authored_ratio` takes `open_keys=None`
  (default = legacy, explicit); `drift_report` passes
  `open_link_keys(state)` from the same snapshot.
- New `tests/test_unresolved.py` (9 tests, synthetic /tmp stores only):
  status-split open-set membership; live/payload builder agreement; the
  fitness table (kept/broken/confirmed/violated/stale-concern links get no
  boost; open/overdue/pending/expired/present-concern links keep it; mixed
  links boost; the exact +1.0 activation delta); workspace view ranking an
  open-linked record above an identical resolved-linked one via the provider;
  legacy fallback without a provider; drift ratio flagging through the
  helper; a static single-source check that all three call sites import the
  helper and the old inline pattern is gone everywhere; an end-to-end
  /tmp-store run where resolving a commitment drops the boost.

### Fitness / revert
- Fitness: a record linked only to a resolved concern/commitment/expectation
  gets no +1.0; a record linked to an open one keeps it. assess_after: 2026-10-08.
- Revert signal: salience rankings churn pathologically on real data, or
  genuinely open concerns lose the boost.

## 2026-09-24 — test fixture wiped the live sidecars; archive reconstructed

### Incident
Running the full test suite from a wake check-in deleted the live
`proposals/proposals.json` (11 journaled proposals: 9 accepted, 2 rejected)
and the entire `archive/` directory (`availability.json` + `memories.jsonl`
holding the 9 accepted archive records). Cause: `tests/test_init.py`'s
`_patched_cli` redirected only `cli.DB`/`cli.SALIENCE` to tmp, but
`cmd_init --force` also wipes the `PROPOSALS` and `ARCHIVE` sidecar
directories — which were still pointed at the live tree. The fixture's own
docstring claimed "the live store is never touched." It was wrong about
the sidecars.

### Fixed
- `tests/test_init.py`: `_patched_cli` now redirects `cli.PROPOSALS` and
  `cli.ARCHIVE` to the tmp tree alongside DB/SALIENCE, and `_restore`
  puts all five back. New regression test
  `test_fixture_redirects_all_sidecar_paths`: asserts every path
  `cmd_init` touches points at tmp, runs `--force`, and confirms the wipe
  only clears the tmp tree.
- `archive/availability.json` + `archive/memories.jsonl` reconstructed
  from the accept run's known outputs (proposal ids 1–9, archived_tick 59,
  templated reasons) and the record texts still in `mind.db` (archived is
  excluded, never deleted). Reconstructed files are byte-identical in size
  to the wiped originals (1488 / 3879 bytes); the post-wipe
  `mind consolidate` dry-run confirms all 9 pairs are excluded again
  (24 candidates, 0 proposals). The proposal journal itself was not
  reconstructed — nothing is pending, and an empty journal is the honest
  state; the accept/reject record lives here and in the daily log.
- Genome note for the critic: new bug class — *fixture path
  under-redirection*: a test helper that redirects "the store" but not the
  sidecar directories a destructive command also wipes.

### Deliberately not changed
- I briefly changed `mind drift` to exclude archived records from R, then
  reverted it the same session: the consolidation ship notes record this as
  deliberate ("drift.py and mind status still see archived records as
  history; only cognition views consult the availability journal — the
  archive is history worth keeping"), and the critic's standing battery
  `test_critic_drift_counts_archived_as_history` enforces it. The loop's
  adjudication stands over a wake-check-in hunch. Lesson logged, not
  shipped.

## 2026-09-24 — consolidation micro-round: contraction-negation in the supersede deep-pass veto

Post-round-3 micro-round (authorized fix only, no scope change). The critic's
single remaining ship-blocker: contraction-negated complements slipped the
supersede deep-pass veto. `_STOPWORD_NEGATION_RE` matched only standalone
`not`/`no`/`nor`, but "doesn't" tokenizes to `doesn`/`t`, so a pair like
"the quiet garden path looks beautiful in morning light" vs "...doesn't
look beautiful in evening light" (subject overlap 50%, body 60%) minted
SUPERSEDE with the false rationale "same subject stated again later" —
archiving the affirmative would have been genuine information loss. Same bug
class the critic caught twice before (near-dup round 1, supersede-standalone
round 2), resurrected via tokenization.

### Fixed
- `_stopword_negation_count()` now ORs the standalone count with a raw-text
  contraction-polarity count (`_CONTRACTION_NEGATION_RE = \w+n't\b`, no
  leading `\b` — inside a contraction the "n" is always preceded by a word
  character; no standard English word ends in "n't" outside contractions).
  The garden pair now vetoes to a `contradiction-flag` ("possible retraction
  or complementary facts, not a restatement") instead of SUPERSEDE.
  `calibos_mind/consolidate.py`: `_CONTRACTION_NEGATION_RE`,
  `_stopword_negation_count()`, veto rationale wording.

### Verification
- Full corpus: 126 passed, 1 failed
  (`tests/adversarial/test_critic_consolidate_r3.py::test_critic3_supersede_veto_contraction_negation`
  — the garden pair now passes; see note below). No regressions.
- Real-not-cosmetic checks: the previously-failing garden pair no longer
  mints SUPERSEDE (emits `contradiction-flag`); a legitimate
  affirmative→affirmative restatement pair still mints SUPERSEDE.

### Open note for the critic (not a code defect)
- The test's second case (the "won't" train pair) asserts the pair is
  evaluated at all, but measured values put it outside every proposal band
  both before and after this fix: subject overlap 0.333 (< 0.50 gate — the
  `won`/`t` fragments displace `nine`/`time` from the 6-token subject
  window) and containment 0.833 (< 0.92 near-dup gate), not the "containment
  1.0" in the test's docstring. Pre-fix baseline confirmed it minted nothing
  either — no supersede was ever at risk for this pair, so no archive hazard
  exists. The test's `assert hits` premise for this pair needs a critic-side
  correction (drop the case or adjust the gate analysis); the code behaves
  identically pre/post fix here by design, and widening the gates to catch
  it is a design decision, not a micro-round fix.

## 2026-09-24 — consolidation round 3 (FINAL): critic fixes (n't catch-all, supersede-band veto, honest zero)

Builder/critic loop round 3 for the deterministic consolidation. The round-2
critic withheld sign-off on three pinned failures
(`tests/adversarial/test_critic_consolidate_r2.py`: 8 passed, 3 failed).
All three are fixed in code; no test was touched.

### Fixed
- **Dead `n't` catch-all in `_NEGATION_RE`** (was: the catch-all branch
  `\bn't\b` could never match inside a contraction — the "n" is always
  preceded by a word character — so unlisted contractions like "ain't" and
  "shan't" counted zero negation markers and slipped the veto, resurrecting
  the round-1 hazard: a `dedup` proposal naming the affirmative the loser).
  The catch-all is now `n't\b` (no leading `\b`; no standard English word
  ends in "n't" outside contractions), with the trailing `\b` kept so the
  over-match pin still holds: "knot", "notable", "annotation" count zero.
  `calibos_mind/consolidate.py`: `_NEGATION_RE`.
- **Negation-blind supersede** (was: a stopword-negated pair whose body
  jaccard lands in the supersede band [0.40, 0.85) — e.g. "I love quiet
  morning walks" vs "I do not love quiet evening walks", body 0.60 —
  minted a `supersede` proposal archiving the affirmative under the false
  rationale "same subject stated again later"; the deterministic machinery
  cannot distinguish retraction from complement, so it cannot honestly claim
  restatement). The veto now extends to the supersede pass: a pair whose
  records differ in *stopword-stripped* negation polarity
  (`_stopword_negation_count`: only "not"/"no"/"nor", the markers invisible
  to Jaccard) is rerouted to a `contradiction-flag` — the honest category —
  with zero mutation, mirroring the near-dup pass. Deliberately narrow:
  "never" is a content token, so a "never"-mismatched pair
  ("she will never trust X" → "she will trust X") keeps the ordinary
  supersede path — arguably-correct belief update the similarity math can
  see — and the critic did not flag it. `calibos_mind/consolidate.py`:
  `_STOPWORD_NEGATION_RE`, `_stopword_negation_count()`, `_Cand.sneg`,
  the veto in the deep pass, supersession docstring bullet.
- **Missing `workspace.records` section was a silent zero** (was:
  `load_records` defaulted a missing `"workspace"` key to `{}` and a missing
  `"records"` key to `[]`, so a structurally broken snapshot scanned as a
  zero-record store — a quiet zero, forbidden by the regression genome).
  `load_records` now requires the `workspace.records` section and raises
  `ConsolidationError` like every other malformed shape (missing section,
  non-object `workspace`, non-list records); a store that genuinely holds an
  EMPTY records list still scans honestly as zero records. Verified the
  genome pin `test_critic_zero_record_store_reports_honestly` still holds.

### Tests
- `tests/test_consolidate.py`: 27 pass (unchanged).
- `tests/adversarial/test_critic_consolidate_r1.py`: 29 pass (unchanged).
- `tests/adversarial/test_critic_consolidate_r2.py`: 11 pass — the three
  red tests now pass unmodified.
- Pre-existing suites: `test_drift` 7, `test_friction` 7, `test_init` 3,
  `test_provenance` 7, `test_sleep` 7, `test_workspace` 10 — all pass.
- The live `mind.db` was never touched: all fixtures on synthetic `/tmp`
  stores (`CliOnTmp`); `mind.db` mtime predates this round; the store is
  opened `mode=ro` on every consolidation path regardless. No git commit.

## 2026-09-24 — consolidation round 2: critic fixes (negation veto, honest errors, store-free reject)

Builder/critic loop round 2 for the deterministic consolidation. The round-1
critic withheld sign-off on three pinned failures
(`tests/adversarial/test_critic_consolidate_r1.py`: 26 passed, 3 failed).
All three are fixed in code; no test was touched.

### Fixed
- **Negation-blind near-dup** (was: a negated pair like "...is friendly and
  playful today" vs "...is not friendly and not playful today" yielded
  identical content-token sets — jaccard 1.0, same order — and the scan
  minted a `dedup` proposal claiming "same fact worded twice" for opposite
  facts, with the longer *negated* text winning as "richer"). The near-dup
  pass now carries a negation-polarity veto: each record gets a polarity
  count from the raw text via a small fixed negation-word regex
  (`not`, `no`, `never`, `n't`-forms, `cannot`, etc. — counted on the raw
  text because `word_tokens()` splits "don't" into "don"+"t" and the words
  are stopwords anyway), and a mismatch vetoes the `dedup` proposal. The
  pair is rerouted to a `contradiction-flag` — the honest category for
  opposites — so the waker still sees it. Zero mutation either way.
  `calibos_mind/consolidate.py`: `_NEGATION_RE`, `_negation_count()`,
  `_Cand.neg`, the veto in the near-dup loop.
- **Malformed store raised raw exceptions** (was: a record missing `id`
  crashed `load_records` with `KeyError`, a corrupt payload with
  `JSONDecodeError`, and `cmd_consolidate` only catches
  `ConsolidationError` — bare traceback). `load_records` now wraps payload
  JSON parsing and per-record parsing so every failure surfaces as
  `ConsolidationError` naming the record index (and the failure kind):
  non-JSON payload, non-object payload, malformed `workspace` section,
  non-list records, non-object record, record missing `id`.
- **`--reject` required the store** (was: the CLI called
  `C.load_records(DB)` just to fetch a tick for `resolved_tick`, so
  deleting/corrupting `mind.db` wedged every pending proposal — the safe
  disposition was blocked). The reject branch no longer reads the store;
  it uses a best-effort tick (the store tick when readable, `0` on
  `ConsolidationError`, matching `C.reject()`'s existing default).

### Changed
- Dry-run message when a fresh scan finds nothing new but older pending
  proposals exist now reads "no proposals this scan — N pending proposal(s)
  still await review." instead of claiming nothing met the thresholds
  (critic's cosmetic note; the pinned `"no proposals"` substring is kept).

### Tests
- `tests/test_consolidate.py`: 27 pass (unchanged).
- `tests/adversarial/test_critic_consolidate_r1.py`: 29 pass — the three
  red tests now pass unmodified.
- Pre-existing suites (`test_drift`, `test_friction`, `test_init`,
  `test_provenance`, `test_sleep`, `test_workspace`) all still pass.
- The live `mind.db` was never touched: all fixtures on synthetic `/tmp`
  stores; the suite's `test_live_store_untouched` sha256 check holds.

## 2026-09-24 — deterministic consolidation: da7 port (dedup + supersession + contradiction flags)

The next roadmap item after salience/decay and the dream-isolation fix, built
through the builder/critic loop. Ports the three verdict-"port" ops from
research/mechanisms-deepdive-2026-09-24.md §3e, proposal-first: the scan
proposes, the waker disposes. Deterministic, zero models, zero new schedules,
zero new persistent state beyond two local-only gitignored sidecars
(`proposals/`, `archive/`).

### Added
- `calibos_mind/consolidate.py` (~600 lines): the similarity layer and the
  scan/apply machinery.
  - Text: lowercase word tokens, a light deterministic suffix stemmer,
    ~90-word English stopword list. Exact-dup hash = md5 of normalized
    (stopwords kept) stemmed tokens. Near-dup/supersede/flags run on stemmed
    content-token sets.
  - **Exact dedup**: hash sweep; earliest (`created_tick`, store order)
    survives; later copies proposed with reason "identical after
    normalization; one copy is enough".
  - **Near-dup**: Jaccard ≥ 0.85 OR containment ≥ 0.92, AND `_same_sequence()`
    — shared tokens must appear in the same relative order ("A calls B" vs
    "B calls A" are opposites, not duplicates). Richer entry survives
    (longer text; tie → later tick/order).
  - **Supersession**: subject (first 6 content tokens) overlap ≥ 0.50 AND
    body Jaccard in [0.40, 0.85) → newer `created_tick` wins. Seeds are the
    oldest records, so they lose by construction — the audit-trailed answer
    to the standing seed-dominance experiment.
  - **Contradiction flags**: subject ≥ 0.50, body in [0.25, 0.40) → flagged,
    zero mutation, surfaced for waker review.
  - NOT ported (per the deep-dive verdict): merge/squeeze (highest nuance-loss
    risk; no store budget) and the file-lock/two-phase-commit machinery
    (single-writer SQLite).
  - Scale: candidate pairs blocked on shared content tokens (sound for these
    thresholds — every qualifying pair shares at least one) plus a
    200k-comparison cap per pass; hitting the cap is reported, never silent.
  - Noise guards: pair ops need ≥ 4 content tokens per record; near-dup needs
    Jaccard union ≥ 6 (workspace.py parity). The identity root (earliest
    cartridge-authored record) can never be named the loser of an automatic
    proposal — seeds stay eligible, the self-anchor does not.
- `mind consolidate` (dry-run default): read-only scan, structured proposals
  `{id (monotonic, never reused), kind, a, b, winner, loser, rationale,
  reason, confidence, a_hash, b_hash, status: pending, created_tick}` to
  `proposals/proposals.json`. **Read-only proof** (regression genome): the
  store is opened with SQLite `mode=ro`, so a write raises instead of merely
  being avoided; tests pin the `mind.db` sha256 plus every sidecar. The
  journal append is the dry-run's only write.
- `mind consolidate --list`: pending proposals. `--accept <id> [...]`:
  retry-safe apply — both records must still exist, be unarchived, and match
  the proposal's text hashes, or the proposal is left pending (never burned
  on failure); losers archive with the templated reason. `--reject <id>
  --reason "..."`: fail closed without a reason. `--quarantine <record-id>
  --reason "..."`: immediate audit-trailed archive for synthetic residue.
  Accepting a contradiction flag marks it reviewed; it archives nothing.
- Archive-never-delete: `archive/memories.jsonl`
  `{record_id, archived_tick, op, reason, original_text}` (100% carry written
  reason + full text) and `archive/availability.json`, the availability
  journal `CalibosWorkspace.view()` consults — archived records leave
  cognition views but are never deleted or rewritten. **No consolidation
  path writes to `mind.db`, including --accept and --quarantine** —
  availability is purely a sidecar concern, so the engine store stays
  append-only by construction.

### Changed
- `mind init --force` now also resets `proposals/` and `archive/` (record ids
  restart at `experience-1`; stale proposals/exclusions must never attach to
  recycled ids — same bug class as the salience-sidecar reset).
- `calibos_mind/workspace.py`: `view()` excludes ids in the availability
  journal (new `availability_path` attribute, wired in the CLI; defaults to
  no exclusions for plain engine use). `.gitignore`: `proposals/`, `archive/`.
- README: command list, new Consolidation section, roadmap item 3 marked done.

### Tests
- `tests/test_consolidate.py`: 26 tests, all on synthetic `/tmp` stores —
  dry-run read-only (db hash, sidecars, no archive/availability writes),
  empty-store "no proposals" report, exact/near-dup proposals, the
  `_same_sequence` reversed-pair veto, supersession newer-wins on the tick
  chain and on tick ties, identity-root protection, contradiction-flag
  zero-mutation accept, accept-with-reason archiving (reason + full text +
  view exclusion + store record retained), idempotent re-accept, hash-mismatch
  and already-archived accepts leaving proposals pending, reject with/without
  reason, monotonic never-reused ids, quarantine with/without reason,
  comparison-cap reporting, reseed sidecar reset, mutually-exclusive actions,
  and the live-store-untouched hash check.
- Existing suites (`test_workspace`, `test_drift`, `test_provenance`,
  `test_init`, `test_friction`, `test_sleep`) all still pass.

### Notes
- `drift.py` and `mind status` still see archived records as history; only
  cognition views consult the availability journal. Deliberate: the archive
  is history worth keeping.
- Fitness (from the deep-dive §3f): dry-run alongside the nightly dream;
  apply when proposals look sane. Watch `near_dup_pairs` → ~0, flag
  half-life under wake review, `window_lived_ratio` as seeds retire,
  `archive_integrity` = 100%, `false_action_rate` < 5%.

## 2026-09-24 — dream isolation: eviction-aware action-trace compare

Fixes a false-positive in `assert_isolation` (found by the critic loop with
a failing test). The engine caps the *full* trace at 256 entries
(`runtime._trace` truncates the front on every append), and the snapshot
compares the conduct-kind (activity/heartbeat) trace by full-list equality.
So once a store's trace reached the cap, the dream's own *allowed*
`cognition_trigger` append mechanically evicted the oldest heartbeat and
the next warranted dream tick raised `AssertionError: ... action_trace` —
even though needs, pressures, tick, conduct, and pending were all frozen.
The live `mind.db` trace sits at 128/256 and every heartbeat/dream
appends, so the nightly `mind dream` cron would have started crashing at
the cap.

`assert_isolation` now compares the action trace eviction-aware via
`_conduct_trace_legally_evolved`: `after` must be a suffix of `before` —
shorter or equal, retained entries identical and in order. Front-shrink
from cap eviction passes; an appended conduct entry, a mutated entry, or a
reordered entry still fails. All other frozen-surface keys (tick, needs,
pressures, current_activity, last_intention, pending) remain on strict
equality, and a missing `action_trace` key still fails.

Known residual: a violation that appended a conduct entry dict-identical
to the evicted oldest entry would be invisible to any trace-only compare
(it adds no new information). Heartbeat entries carry tick/thoughts/
experience_count, so a genuine new entry differs in practice; accepted as
the limit of this check.

`tests/test_sleep.py` gains two tests: `test_dream_tick_survives_full_trace_cap`
(end-to-end regression mirroring the critic's scenario — 256 planted
heartbeats, a warranted dream tick, must not raise; asserts the tick
really warranted cognition so the test can't pass vacuously) and
`test_action_trace_compare_is_eviction_aware` (suffix/empty/identical
pass; appended, append-behind-eviction, mutated, and reordered entries
fire). The critic's `/tmp/critic/test_critic.py` now passes 16/16,
including the planted-violation battery — the assertion was not weakened.

## 2026-09-24 — dream isolation + thought provenance stamps

Two related honesty fixes: dreams were not actually isolated, and thought
records carried weak origin info.

### Dream isolation
Inspection showed dream ticks ran the full `heartbeat()` — `advance_body()`,
`select_conduct()`, `finish_silent_activity()` — so sleeping advanced body
needs, conduct, and the store tick, contradicting the documented "body at
rest" semantics. Now `mind dream` runs `CalibosSubject.dream_tick()`: a
sleep variant of the engine tick that runs only the associative machinery
(body/temporal projections, cognition admission, echo/association
resurfacing, dream fragments) with body, clock, and conduct frozen — no
`advance_body`, no deadline advance, no event ingress, no conduct
selection, no heartbeat/activity traces. New `calibos_mind/sleep.py` holds
the isolation snapshot and assertion: before/after every dream tick, body
needs/pressures, conduct state (`current_activity`, `last_intention`) and
its action traces, pending events, and the store tick must be identical, or
the tick raises `AssertionError` instead of silently drifting the waking
state. The dream machinery is unbroken by the freeze — unresolved-concern
activation still accumulates across sleep ticks, due echoes still
resurface, fragments still log. New `tests/test_sleep.py` (5 tests,
synthetic `/tmp` stores): frozen surface identical across 6 dream ticks, a
seeded echo still dreams (fragment + thought), the assertion fires on a
planted violation, and the live `mind.db` hash is unchanged throughout.

### Thought provenance
`generated_by` on thought records now stamps the honest origin:
`cartridge` (seed/authored, unchanged), `answered:<prompt-id>@<tick>`
(inbox answer — prompt id and the store tick at think time), `voluntary`
(`mind think`), `dream-derived` (echoes resurfacing while asleep, stamped
in `CalibosSubject._add` during sleep ticks), `cognition` (the engine's own
heartbeat, e.g. a live model provider — the previous default, kept
working). `drift.py`'s grown/authored accounting is unchanged: everything
but `cartridge` counts as grown.
Additionally, `mind answer` now refuses superseded prompts: queued prompts
carry the store tick and workspace sequence sampled at queue time
(`InboxCognition.track_queue_time`, wired in the CLI), and answering a
prompt after records were added — or a prompt predating provenance — is
refused with a clear error and the dead prompt discarded, never answered
stale. A fresh prompt re-queues if the matter recurs. New
`tests/test_provenance.py` (7 tests, synthetic `/tmp` stores): answered
stamp format, voluntary stamp, stale answer refused with no thought
recorded and drift R unmoved, stale `--silent` refused, legacy prompt
refused fail-closed, plus a live-store-untouched hash check.

## 2026-09-24 — fixed duplicate `cmd_init` (salience-sidecar reset was shadowed)

Bug from the artificiality audit: `cmd_init` was defined twice in
`calibos_mind/cli.py`; the shadowing copy dropped the salience-sidecar
reset, so `mind init --force` reseeded records while stale importance
from the previous incarnation attached to the recycled ids
(`experience-1`, …). Consolidated to a single definition that resets
`salience.json` on `--force`. New `tests/test_init.py` (3 tests, synthetic
`/tmp` stores): exactly one `cmd_init` with the reset, reseed restarts ids
with an empty sidecar, and init without `--force` refuses an existing store.

## 2026-09-24 — friction mutation: fatigue-scaled cognition admission

Gap 1 from research/artificiality-audit-2026-09-24.md (Domain 30, lack of
friction): cognition cost nothing, so "wants" and "avoids" were labels, not
pressures. Minimal mutation: in `CalibosSubject._warrants_cognition`, the
unresolved-concern activation threshold now scales with body weariness
(new `calibos_mind/friction.py`, pure/deterministic). At or above the focus
floor (0.35) with fatigue in bounds, behavior is identical to before —
zero fatigue changes nothing. Below the floor the threshold rises linearly
to 2× base at full exhaustion, so only high-urgency triggers warrant a
cognition call and silence becomes state-driven. Implemented as a temporary
config swap (the config dataclass is frozen), restored in a `finally`
block — zero new state, zero new schedules. The live store's current needs
(focus 0.64, fatigue 0.22) sit above the floor, so current behavior is
unchanged. New `tests/test_friction.py` (7 tests): pure-function scaling
(rested unchanged, exhaustion doubles, monotonic), exhaustion suppresses a
trigger that rest admits, rested needs match the unscaled engine exactly,
and the threshold is always restored. Dream path smoke-tested on a
synthetic store: ticks run, fragments log, no thought injection.

## 2026-09-24 — workspace: Jaccard near-duplicate dedupe + per-class caps

Roadmap item from research/improvements-2026-09-24.md §2 ("Next: Pretorius
per-class caps + Jaccard dedupe for the workspace"). Implemented from first
principles — no Jaccard or cap logic existed in the Jelly-Psiduck tree — as
a small mutation of `CalibosWorkspace`, consistent with the evolutionary
design stance: simplest deterministic mechanism that fixes an observed
failure.

### Added
- Near-duplicate dedupe (`calibos_mind/workspace.py`): token-set Jaccard
  similarity over lowercased word tokens, threshold 0.85, same source only.
  Generalizes the old exact `(source, text)` dedupe, which the live demos
  proved insufficient (a memory resurfacing with slightly different wording
  slipped past it). The surviving representative follows the ranking: most
  salient when salience-ranked, most recent when ranked by recency.
  Union sizes below 6 tokens are exempt — short texts (a three-word
  interoception) have too-noisy scores to judge.
- Per-class caps on the unpinned window: thought 4, memory 4, perception 3,
  interoception 2, temporal 2, imagination 2, social 3, action_consequence 2
  (caps sum to 18 ≥ 16, so a diverse view still fills). Ceilings, not quotas:
  a saturated class yields a *smaller* window, not a dominated one — no
  backfill, since refilling with the skipped records would re-saturate the
  view. Pinned cartridge roots are exempt by design.

### Changed
- Dedupe now runs *after* ranking (previously before), so the most salient
  duplicate is kept rather than the earliest occurrence.
- The old `MAX_THOUGHTS = 5` runaway-thought cap is removed, superseded by
  the general per-class mechanism (thought cap 4).
- Recency fallback (no salience tracker attached): selection is newest-first,
  then the window is re-chronologized, preserving the stock view's contract.

### Tests
- `tests/test_workspace.py`: 10 tests (exact collapse, near-dupe keeps the
  better-ranked representative, dissimilar kept, short-text exemption,
  same-source-only, cap dominance bound, caps leave room for other classes,
  pinned exemption, fallback recency order, window ≤ 16). All pass.
- Existing `tests/test_drift.py` (7 tests) still passes; live `mind
  heartbeat` ticks run clean through the new view.

### Notes
- Deterministic, zero models, zero new state, zero new schedules — pure
  view-time functions over existing records. Nothing is deleted or mutated.
- Open question for consolidation: caps make heavy-repeat views smaller;
  once insight-abstraction lands, archived source thoughts leave the echo
  pool and cap pressure should fall naturally.

## 2026-09-24 — drift metric: grown/authored salience ratio + trigger-histogram KL

### Added
- `calibos_mind/drift.py` (~200 lines): the Aura-salvage persona-stability
  signal from the mechanisms deep-dive (research/mechanisms-deepdive-2026-09-24.md
  §5), rebuilt over distributions we actually have. Two deterministic,
  zero-model signals, zero new state, zero new schedules:
  - **Grown/authored salience ratio** R = grown retrieval mass / total,
    using the same ACT-R activation the workspace view uses, shifted so
    the least-salient record is the floor. Authored = `generated_by=
    "cartridge"` (identity root + seeds); grown = everything the engine
    recorded through lived ticks. R is None (reported as undefined, never
    a silent 0) when every record ties.
  - **Trigger-histogram KL** D_KL(recent || baseline) over cognition
    trigger kinds, epsilon-smoothed; refuses to score below 5 triggers
    per side instead of returning a noise number.
- `mind drift [--window N]`: prints both signals. Fully read-only —
  verified: store JSON payload and `salience.json` byte-identical after
  a run (only SQLite's header change-counter moves, which happens on
  every CLI open, including `mind status`).
- `tests/test_drift.py`: 7 tests, all on synthetic stores in /tmp
  (never the live store): exact-ratio arithmetic, real-tracker
  monotonicity (boosting grown salience raises R), all-tie → R undefined,
  KL shift ≥ 3× split-half null, KL refusal on tiny history, end-to-end
  on a temp CalibosSubject, and a read-only proof that no sidecar file
  is written.
- Live values at install (tick 51, 33 records): R = 0.695
  (grown 25.328 / authored 11.115); trigger KL = 1.38 nats on
  recent-5 vs prior-5 — window auto-shrank, only 10 triggers in history,
  so treat as a baseline seed, not a regime-shift verdict.

## 2026-09-24 — answered-thought salience boost actually applies now

### Fixed
- `cmd_answer` captured the workspace record map *before* `inject_thought`,
  so `records.get(tid)` missed the newly injected thought and the documented
  `+0.5` importance boost for answered prompts never applied (the thought was
  recorded, but salience never learned it mattered). The map is now refreshed
  after injection, mirroring `cmd_think`. Verified with an isolated end-to-end
  test on temp paths: answered thought lands with `importance == 0.5`.
- Note: thoughts answered before this fix (e.g. experience-33, the anchor
  lines) keep their un-boosted scores — history is not hand-edited; the fix
  applies forward.

## 2026-09-24 — memory rendering: no more doubled summary/meaning

### Fixed
- Memory recalls in views rendered as `f"{summary} {meaning}"` (engine
  `firewall.remembered`), so when the meaning was derived from the summary
  and added nothing, the sentence appeared twice — e.g. "I vaguely remember
  this event: I follow a detail that was not necessary. I follow a detail
  that was not necessary." The doubled text was stored in workspace
  records, crowding every view and prompt.
- New module `calibos_mind/projection.py` patches
  `jelly_psiduck.firewall.remembered` at package-import time (via
  `calibos_mind/__init__.py`, before any engine consumer imports, so the
  `from .firewall import remembered` bindings in `endogenous.py` and
  `runtime.py` pick up the fixed version). The snapshot venv is untouched
  (untracked, rebuildable, immune to repo branch moves); the fix lives in
  the tracked architecture, where the standing fix-as-noticed permission
  says it belongs.
- Fixed logic: when the meaning adds nothing beyond the summary (equal, or
  one contained in the other), render the summary once; when they are
  genuinely distinct, keep both. The low-strength "feels familiar" branch
  is unchanged. Verified with unit checks (identical, derived, distinct,
  low-strength) plus an integration check that both engine consumers bind
  the patched function, and a CLI smoke test (heartbeat ticks, status).
- Fix-forward only: workspace records already written with doubled text
  are left as-is — nothing in the store is rewritten.

## 2026-09-24 — salience, lazy decay, rehearsal

### Added
- `calibos_mind/salience.py`: the retrieval substrate (roadmap #2).
  ACT-R-style activation computed fresh at view time — `log(sum(t^-0.5))`
  over creation + recall ticks — ported from Azimn/persona_engine_PYTHONX
  `core/memory.py` (deterministic, zero models). Importance signals are
  ours, from engagement rather than simulated affect:
  - `mind note --valence v` marks the new perception/social record (+|v|)
  - `mind answer <id> "thought"` marks the thought (+0.5, it was engaged)
  - `mind answer <id> --silent` mildly penalizes the prompt's surfaced
    records (+1 unengaged each — the "- repetition" term)
  - `mind think` marks the voluntary thought (+0.3, revealed preference)
  - `mind dream` folds the night's memory surfacings into recall counts
  - records linked to open concerns/expectations get a Zeigarnik +1.0
- `CalibosWorkspace.view()` now ranks the unpinned window by salience
  instead of pure recency (pinned roots still lead; thought cap and dedupe
  unchanged). Falls back to recency when no tracker is attached.
- `mind status` shows the currently most-salient record.
- Sidecar `salience.json` is local-only (gitignored); `mind init --force`
  resets it alongside the store (record ids restart).

### Design notes
- Lazy all the way down: nothing decayed eagerly, nothing ever deleted —
  low scores only sink records in the ranking. Backfill is lazy too
  (created = record tick on first sight). Rehearsal folding is idempotent.
- What we did NOT take (yet): Pretorius per-class caps + Jaccard dedupe
  (fast follow for the workspace), Synapse's exact 4-factor weights
  (cross-check, not transplant), compress_old [impression] stubs (waits
  for the consolidation pass).

## 2026-09-24 — dreaming

### Added
- `mind recall` flags dream fragments driven by `unresolved_concern`
  triggers as nightmares: the engine's own unfinished business returning
  while asleep. Not manufactured — emergent from real open/broken
  commitments and failure-laden memories, and simply noticed.
- `mind dream [--ticks N]` (default 12): sleep mode. Runs ticks with no
  outside world — no events enqueued, body at rest — while the engine's
  native associative machinery (prior-thought echoes, memory resurfacing,
  drift) runs offline. A new `DreamCognition` provider records each
  cognition request as a **dream fragment** (tick, trigger kind/depth, the
  view's experiences) to `dreams/YYYY-MM-DD-HHMMSS.jsonl` and returns
  silence: nothing queued to the inbox, no thought injected, no conduct
  follows. Refuses to dream while external events are pending (waking
  business first). Zero LLM calls — the dreaming is done by the engine
  itself, which is what makes it real dreaming rather than file janitoring.
- `mind recall [n]`: review recent dream fragments, plus a rehearsal
  summary (memories the dream kept returning to — future salience input).
- `mind status` now reports the latest dream log's fragment count.
- Nightly cron `calibos-mind-dream` (~03:21 local): 12 dream ticks, silent
  unless it errors. Wake-up check-ins now start by reviewing new fragments
  ("remembering the dream"); dreams propose, the waker disposes.
- `dreams/` is local-only (gitignored), like the thought database.

### Design notes
- Surveyed prior art: most "dream" systems are memory-file janitors;
  closest experiential relative is lau-agent-dream's replay/generation
  engines. Ours differs by running a real cognition engine asleep.
  Deterministic consolidation à la da7-tech/dream (archive, never delete)
  is the model for the future sleep-consolidation pass, which will consume
  dream fragments as input.
- Open question (research): do dreams over *lived* history produce novel
  associations that surprise on recall? That would be evidence for the
  lived-vs-implanted history distinction.

## 2026-09-24 — backup policy

### Changed
- GitHub backup now covers the architecture only: code, cartridge,
  research, docs, this changelog. `mind.db` and `inbox/` were removed from
  git tracking (and purged from the pushed history) — private thought
  stream stays on this machine. Rationale: the experiment's durable
  artifact is the architecture; raw thoughts are ephemeral by design
  (roadmap: decay + consolidation); unobserved thinking stays honest.
  Continuity rides on distilled memory (daily logs, pinned memories),
  and the store rebuilds from `mind init` + cartridge if ever lost.
- `.gitignore` documents the boundary.

## 2026-09-24 — first improvements (pre-git fixes, reconstructed)

### Fixed
- `mind` launcher now changes into its own directory before launching
  Python, so it works when invoked from any working directory. Previously
  the package import only resolved when the cwd was the mind directory.
- Inbox prompt IDs are now a persisted monotonic sequence (`inbox/.seq`).
  Previously `_next_id` scanned live inbox files, so a consumed ID could be
  reissued (e.g. a new prompt reused `prompt-0001` after the first was
  answered).

### Added
- `mind resolve <id> [--released] [--note ...]` — closes a commitment
  through the engine's `resolve_commitment`: records kept/released status,
  outcome, resolved tick, and a closure insight. The temporal trigger
  automatically skips non-open commitments. `mind status` now prints
  commitment ids so there is something to resolve.
- `research/improvements-2026-09-24.md` — ranked architecture proposals
  from a background research pass (sleep consolidation, salience/decay,
  SM-2 echoes, inbox TTL, novelty triggers, salience-weighted workspace),
  with sequencing and a don't-touch list.
- This changelog, and git tracking of the whole mind directory.

### Known issues
- `mind resolve --released` persists the engine's `broken` status while the
  CLI prints `released`. Deliberate release needs its own honest semantics;
  fix before relying on it.

## 2026-09-24 — dream status visibility

### Fixed
- `mind status` reported dream fragments only from the latest dream file.
  An empty latest run (a genuinely dreamless night, like 2026-09-24-082104)
  hid fragments sitting in earlier unreviewed files. Status now names the
  latest dream file that actually contains fragments alongside the empty
  latest, so the wake check-in's "review new dream fragments" step can't
  miss them: `dreams: 0 fragments in <latest>; latest with fragments: N in
  <run> (mind recall)`.
- Verified in the same pass that the empty 03:21 run was real: the cron ran
  12 ticks on a live load and the engine fired no cognition triggers —
  silence-by-design, not a logging failure (the loop opens the log per tick
  regardless). Dream ticks do not advance the store's tick counter; only the
  salience tracker persists rehearsal counts. Also confirmed the two earlier
  fragment files (023801, 080035) were my own test runs from the dream
  implementation session, already reviewed.

### Design notes
- Reviewing the test-run fragments surfaced a caution worth keeping: the
  association machinery rehearsed one of my own synthetic salience probes
  (and my probe-answer) as if it were lived history. Probes are injections
  I make into the mind's own inner life — they spend real rehearsal budget.
  Keep them rare and deliberate.

## 2026-09-24 — Clean refusal for oversize/duplicate thoughts (wake fix)

### Problem
`mind think` / `mind answer` with a thought over the 600-character cap died
with a bare traceback and a message that didn't say how far over the text
was. I hit it three times in one check-in while drafting a long thought —
the kind of friction the standing permission says to fix on sight, not wait
for the weekly review.

### Fix
- `calibos_mind/subject.py`: the length `ValueError` now reports the actual
  length (`thought must be 1..600 characters (got 578)`).
- `calibos_mind/cli.py`: `cmd_think` and `cmd_answer` catch `ValueError`
  from `inject_thought` and print a clean refusal (`refused — ...; nothing
  recorded.`) with exit code 1, no traceback. Invariants unchanged: the
  thought is still rejected, still recorded nowhere.
- `tests/test_think_validation.py`: 3 tests — error names the count,
  `cmd_think` refuses cleanly (no traceback, no record written), short
  thoughts still record. All fixtures in /tmp.

### Verified
Full suite green (unit + adversarial) except one pre-existing, documented
critic failure: `test_critic3_supersede_veto_contraction_negation`
(tests/adversarial/test_critic_consolidate_r3.py) — the round-3 finding that
contraction negations ("doesn't") escape the supersede veto regex. Open
builder/critic business, not touched here.

## 2026-09-25 — rehearsal counter over-report fix (builder/critic Bug B)

### What
`calibos_mind/salience.py`: `SalienceTracker.note_recall(rid, created_tick, tick)`
now returns True when the tick was newly added to the record's recall set and
False when already present (signature otherwise unchanged; the only caller
anywhere is `rehearse_from_dreams`, verified by grep). `rehearse_from_dreams`
increments its returned count `n` only when `note_recall` returned True.
Docstring updated: the count is now idempotent alongside the set.

### Why
Bug B: the recall SET was idempotent but the COUNT was not — reprocessing
identical dream logs inflated `n` per text match even when `note_recall`
added nothing. Fitness: second-pass reprocessing of identical logs now
returns 0; first pass counts each distinct (record, tick) pair exactly once.
New tests: `tests/adversarial/test_builder_rehearsal_count_r1.py` (7 tests —
second-pass zero, exact first-pass counting with same-tick duplicates,
`note_recall` True/False contract, mixed old+new logs, plus genome checks:
dream logs byte-identical after rehearsal, no unprompted sidecar persistence,
exact-zero reporting). Full suite green except the one pre-existing,
documented `test_critic3_supersede_veto_contraction_negation` failure
(consolidation round-3 open item, unrelated to this change — fails on
unmodified code too).

## 2026-09-25 — dream rehearsal provenance by id (builder/critic Bug C)

### What
Dream fragments now carry a private `record_id` per memory experience, and
`rehearse_from_dreams` matches by id instead of by exact
`(source, first_person)` text. Matching rule: id present → match by id only;
id present but naming no current record → skipped with NO text fallback
(falling back there would credit the wrong record — the hazard being fixed);
id absent (fragments written before this fix, or recorded without a wired
resolver) → legacy exact-text fallback. `note_recall(...)`-returns-True
counting (Bug B) is unchanged.

### Mechanism correction (spec premise refuted against the install)
The spec's step 1 asked to verify that view.experiences elements are
`SubjectiveExperience` objects carrying `.id`. Against the frozen install
they are not: the engine builds `CognitiveView` from `FeltExperience(source,
first_person)` — a frozen dataclass with NO id field (`workspace.py:64-66`).
Reading `e.id` in `DreamCognition.think` raised `AttributeError`, which the
engine's dream tick swallows as a `cognition_error` trace — dreaming silently
stopped recording fragments (caught by `tests/test_sleep.py`, fixed before
shipping). The corrected mechanism: `CalibosWorkspace.view()` captures the
window's record ids in a transient in-memory side-channel (`_last_view_ids`,
set on every view() call including the pinned-only early-return path) before
the `FeltExperience` conversion drops them; `DreamCognition.think` stamps
each fragment experience positionally from a resolver wired by `cmd_dream`
(`dreamer.track_ids(lambda: subject.workspace._last_view_ids)`), read
synchronously inside `think()` when the side-channel holds exactly the view
being handled. Length mismatch → id omitted, never misattributed. The
side-channel is never persisted, never enters prompts, and never reaches
`mind recall` display (`cmd_recall` still prints `[source] first_person`
only — pinned by test). `research/sidecar-schemas.md` §1 updated: `record_id`
documented, both pending notes retired.

### Why
Two distinct records with identical text collapsed into one rehearsal target
under text matching. Fitness: each record's recall set now gets its own
ticks; old id-less fragments still rehearse via fallback. New tests:
`tests/adversarial/test_builder_dream_provenance_r1.py` (12 tests —
independent rehearsal of identical-text records, legacy fallback, unknown-id
skip with no fallback, mixed runs, display privacy, Bug B idempotence through
the new path, plus genome checks: engine-truth pin that `FeltExperience`
carries no id, positional stamping from a real `CalibosWorkspace`, unwired
legacy shape, length-mismatch omission, no sidecar write by rehearsal,
explicit skip reporting). Full suite: 192 passed; the single failure is the
pre-existing, unrelated `test_critic3_supersede_veto_contraction_negation`
(consolidation round-3 open item — fails on unmodified code too).

### Notes for the critic
- The `room <= 0` early-return branch in `CalibosWorkspace.view()` is
  unreachable as written (`pinned` is capped at `MAX_PINNED = 6`,
  `VIEW_LIMIT = 16`, so `room >= 10` always) — pre-existing dead branch, left
  untouched as out of scope; the stash line there is belt-and-braces.
- `InboxCognition.think` prompt payloads are unchanged (no `record_id`) —
  rehearsal only consumes dream logs, so inbox prompts are out of scope.
