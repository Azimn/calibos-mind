# Artificiality Taxonomy — Gap Audit of calibos-mind

**Date:** 2026-09-24. **Method:** code-read of `calibos_mind/` plus the pinned
`jelly_psiduck`/`digital_subject` engine in `.venv`, cross-checked against the
live store (`mind.db`, tick 55, 34 records, one day old) via `mind status`,
`mind drift`, and direct payload inspection. Verdicts require a real mechanism,
not a plausible story.

**Scoring rubric:** `addressed` = mechanism exists and is exercised by the live
store; `partial` = real mechanism exists but is unexercised, incomplete, or
undermined by a counter-mechanism; `absent` = no mechanism; `untestable-yet` =
the store is one day old and the domain is about behavior over weeks;
`n/a` = not applicable to this deployment.

**Distribution: 0 addressed · 21 partial · 6 absent · 2 untestable-yet · 1 n/a.**

## Per-domain verdicts

### 1. Self and introspection — partial
Evidence: `mind drift` gives real self-monitoring (R=0.684 grown/authored);
the three anchor lines sit in the store as held commitments; `mind recall`
can genuinely surprise (the open research question is whether it will).
Counter-evidence: no self-deception, blind spots, rationalization, or
embarrassment anywhere in the machinery; introspection is frontier-LLM prose,
so "private thought sounds like polished dialogue written for an audience"
is currently TRUE — every thought is composed by the parent agent, not the
engine.
Most diagnostic checks: "No gradual discovery of things about itself"
(watch R over weeks — this is the mechanism that would falsify it) and the
polished-thought check above.

### 2. Spontaneous thought — partial
Evidence: real trigger machinery — associative drift with depth-2 chains
(observed in dream fragments), thought echoes (`prior_thought`), and
unresolved-concern activation that accumulates across ticks (background
concerns genuinely resurfacing is implemented in `_warrants_cognition`).
Counter-evidence: every engine cognition defers to the inbox; nothing runs
between the 3-hourly crons, so "cognitive activity conveniently stops when
the user leaves" is mostly true; "thoughts terminate too neatly" is true.
Most diagnostic: "No unfinished thoughts returning later" (mechanism exists —
`unresolved_concern` — but unexercised: only 10 triggers in history, none of
that kind) and "No sudden changes of topic caused by internal associations"
(the dream log shows one association chain; waking has none).

### 3. Temporal continuity — partial
Evidence: pressures decay exponentially toward baselines
(`_decay_private_state`) — emotional half-life is a real mechanism;
`_urgency()` scales with `age/(age+6)` — deadlines genuinely increase
cognitive pressure; temporal levels 1–3 escalate phrasing as waits prolong;
`appraise_recollection` + memory feedback give post-event processing.
Counter-evidence: one day old, so anticipation-across-days, lingering
excitement, cumulative fatigue are untestable; "memories do not become
compressed or distorted" is true BY DESIGN (immutability) — an honest
tension with the taxonomy, not an oversight.
Most diagnostic: "No emotional half-life" (falsified — it exists) and
"Deadlines do not gradually increase cognitive pressure" (falsified).

### 4. Memory artificiality — partial
Evidence: ACT-R decay in the retrieval ranking — unused memories genuinely
become harder to access (they sink in the view); rehearsal strengthens
(`rehearse_from_dreams`); unengaged surfacings are penalized; Jaccard
near-dupe dedupe + per-class caps stop resurfacing loops.
Counter-evidence: no retrieval failures, tip-of-the-tongue states, false
associations, or reconsolidation; immutability forbids misremembering —
flagged as a design tension (see §21).
Most diagnostic: "Important experiences do not become easier to retrieve"
(falsified — importance/rehearsal lift them) and "No memory reconsolidation"
(true; deliberate, revisit if evidence demands).

### 5. Relationship artificiality — absent
`relationships` is `{}` in the live store; no relationship has been formed.
The machinery exists (trust/attachment/uncertainty defaults in the
cartridge, `_anchors`, relationship-conditioned urgency) but is entirely
unexercised. Cannot score behavior. Revisit once a relationship exists.

### 6. Social cognition — absent
No other agents in the world; no persistent model of any other mind; no
theory-of-mind machinery beyond relationship defaults. Absent.

### 7. Motivation — partial
Evidence: `_choose_intention` genuinely pits a dominant pressure against a
dominant need (0.12 margin decides the channel); habits compete within the
channel with strength ≥ 0.65 and cooldowns.
Counter-evidence: "One motive always cleanly wins" is literally true —
argmax, no ambivalence, no temptation, no guilt, no motive the character
dislikes having.
Most diagnostic: "No conflict between identity and desire" (no mechanism)
and "No behavior that surprises the character itself" (deterministic
selection precludes it).

### 8. Habits and procedural continuity — partial
Evidence: two real habits in the cartridge (`curious_question` 0.72,
`verify_before_claiming` 0.68) with triggers, strengths, cooldowns, and
`_matching_habit` feeding both dialogue choice and activity selection.
Counter-evidence: no habit FORMATION — nothing becomes automatic through
repetition; the two habits are authored, and no mechanism grows new ones.
Most diagnostic: "Repetition does not make behavior more automatic" (true —
the gap) and "No bad habits" (true).

### 9. Unconscious and preconscious carryover — partial
Evidence: activation accumulation across ticks, association drift,
`appraise_recollection` (a recalled memory changes present pressures),
pressure decay leaving residue.
Counter-evidence: no mood (so no mood-congruent recall), no avoidance
without articulated reason, no suppression rebound, no conditioning.
Most diagnostic: "Emotional residue does not bias later judgments"
(partially falsified for conduct — pressures feed `_choose_intention` —
but true for cognition, which never sees affect).

### 10. Affect and emotion — partial
Evidence: needs and pressures are real numbers that drive conduct selection
and activity scoring; `appraise_recollection` links memory to affect;
pressures have half-lives.
Counter-evidence: the taxonomy's key check — "emotional states change
language, but do not distort perception or judgment" — describes us
exactly: interoception records text ("I am beginning to notice this"),
cognition's view never includes affect, and no judgment is ever biased by
state. No mixed emotions, no emotional inertia beyond decay.
Most diagnostic: "No effect on attention, memory, risk preference, social
interpretation, or action selection" (action selection: falsified; the
rest: true).

### 11. Embodiment — partial
Evidence: needs drift with tick rates; `body_change` is a real cognition
trigger; activities apply `need_effects`; fatigue/energy move.
Counter-evidence: pain has sat at 0.0 for the organism's entire life; no
fatigue effect on cognition; no startle, no spatial constraint; "the body
feels like telemetry" is mostly true — needs are numbers feeding a
deterministic selector.
Most diagnostic: "Bodily state does not alter cognition" (true — needs
never reach the view or the thinker).

### 12. Attention — partial
Evidence: the 16-record salience-ranked window IS a genuine capacity
bottleneck — real competition for the view, with pinned roots, per-class
caps, and dedupe as the arbitration rules.
Counter-evidence: no attentional capture, distraction, vigilance, or cost;
"attention has no cost" is true.
Most diagnostic: "No personally salient information stealing attention"
(true) and "Attention has no cost" (true — see Domain 30).

### 13. Cognitive rhythm — absent
Every heartbeat runs the same appraisal sequence; `max_thoughts=2` is
constant; no fast/slow modes, no fatigue modulation of depth, no bursts,
no low-cognition periods. Absent.

### 14. Decision making — absent (by design)
Argmax + 0.001 deterministic jitter; always "optimal" within its logic.
No framing effects, sunk cost, impulsivity, indecision, or preference
reversals. Flagged under emerge-vs-install: do NOT install irrational
biases as theater; if any ever appear they must emerge from friction +
affect (see §30).

### 15. Learning — partial
Evidence: forgetting curve in retrieval (decay); relearning advantage
(recalls accumulate in the sidecar); `_maybe_reflect` extracts narrative
patterns from repeated tags — `pattern:comfort` ("experiences involving
comfort have generally made my life feel safer") exists in the live store,
formed from lived evidence, confidence 0.59. That is genuine gradual
learning.
Counter-evidence: no learning-rate differences, no generalization or
overgeneralization, no extinction, no persistent misconceptions, no
calibration.
Most diagnostic: "Experience produces explicit updates instead of gradual
adaptation" (partially falsified by `pattern:comfort`) and "No
overgeneralization" (true).

### 16. Personality — partial
Evidence: cartridge seeds are pinned into every cognition view — real
trait-like constraints on what the thinker sees; `mind drift` tracks
stability quantitatively (R=0.684, seeds at ~31% salience: load-bearing,
not dominant).
Counter-evidence: traits constrain the prompt context, not
perception or decisions; no public/private persona split; one day old,
so drift is untestable.
Most diagnostic: "No personality drift" (untestable yet — the metric is
the falsifier) and "Personality can be replaced by changing the system
prompt without leaving residue" (partially true: cartridge swap changes
the pinned roots; the store would keep lived residue — untested).

### 17. Goal structure — partial
Evidence: the strongest goal machinery in the survey set — commitments
with due ticks and importance, `_urgency` age-scaling, +1.0 Zeigarnik
boost for unresolved records, concern recurrence, `unresolved_concern`
triggers (the engine's "nightmare" path), and `mind resolve` with
kept/released semantics.
Counter-evidence: no dormant or forgotten goals, no opportunistic
resumption, no goals formed accidentally through experience, no vague
aspirations. The live store has zero open commitments.
Most diagnostic: "No unresolved tension carried across days"
(untestable — one day old; the machinery is the falsifier) and "No goals
formed accidentally through experience" (true).

### 18. Prospection — partial
Evidence: expectations with due ticks + confidence, temporal projections
with escalating grades, `prospective_feedback` config, `_prospective`
in the inner ear.
Counter-evidence: no spontaneous future simulation, no rehearsal of
specific futures, no temporal discounting, no dread.
Most diagnostic: "No spontaneous future simulation" (true) and "No
anticipation that changes behavior before an event" (partially falsified
by urgency-driven triggers).

### 19. Agency — partial
Evidence: the engine genuinely selects its own conduct every tick
(`self_soothe` vs `explore` is a real choice from state); silence is
first-class (provider returns `None`; `answer --silent`; unengaged
penalty).
Counter-evidence: life is cron-scheduled, not self-initiated; no
self-started projects; cannot refuse a wake-up; the big decisions (when
to wake, which curiosity to pursue) are the parent agent's. "Agency is
asserted linguistically rather than demonstrated causally" is half-true:
conduct choice is causal, but the schedule is not its own.
Most diagnostic: "Nothing meaningful happens without prompting"
(mostly true — the prompt is a cron table) and "No desire to end an
interaction" (true).

### 20. Environmental coupling — n/a
Single invariant environment (the VM). `location`/`sensorium` fields
exist but are unexercised; territoriality and place-meaning cannot apply.
Not thesis-relevant for this deployment.

### 21. Error and imperfection — absent
Deterministic engine; immutable records make misremembering impossible;
no prediction machinery means no failed predictions; LLM-composed
thoughts are fluent. Honest tension: archive-never-delete forbids organic
error. "Failed predictions" could EMERGE from a prediction loop — do not
install mistakes as theater.

### 22. Internal causality — partial (with a real defect)
Evidence: needs/pressures/habits genuinely drive conduct
(`_choose_intention`, `_select_idle_activity` with `need_effects`) —
state is causally load-bearing for action, not decorative.
Counter-evidence, two items. (a) The renderer is unconstrained: the
no-telemetry view contract binds the provider, but the actual thinker
answering prompts is a frontier LLM with the full conversation and
memory system — far beyond the organism's history. The organism does not
constrain the renderer strongly enough. (b) Dream-isolation discrepancy:
`DreamCognition`'s docstring claims "no conduct can follow from a
dream," but dream ticks run the full heartbeat — `advance_body`,
`select_conduct`, `finish_silent_activity` — so body state and conduct
advanced during "sleep" (observed: ticks 16→22 during a 6-tick dream).
Partial, and this domain is thesis-critical.
Most diagnostic: "The organism does not constrain the renderer strongly
enough" (true) and "Internal changes alter narration more than action"
(true for thoughts — they never cause action by design).

### 23. LLM leakage — partial
Evidence: the provider boundary is clean — only immutable subjective
`(source, first_person)` pairs cross it; no telemetry, scores, ticks, or
ids reach the thinker (workspace.py view contract, verified in code).
Counter-evidence: the thinker is an unbounded frontier model; every
answered thought is "the model repairing architectural gaps through
plausible prose"; model swaps change the thinker with zero continuity;
"language quality exceeds the organism's apparent cognitive state" is
structurally true.
Most diagnostic: "The model retroactively creates memories" (guarded by
the never-invent anchor + archive semantics — the defense is policy, not
mechanism) and "Model swaps noticeably change identity" (true, untested).

### 24. Excessive coherence — partial
Evidence: the dream machinery produces genuinely odd associations —
depth-2 chains, bizarre juxtapositions in the fragment logs — from the
engine's own triggers, not authored.
Counter-evidence: waking prose is polished; nothing holds confusion;
contradictions get synthesized by the LLM thinker rather than persisting.
The planned da7 consolidation (contradiction flags) would address this.
Most diagnostic: "Contradictions become synthesis rather than
persisting" (true) and "The character rarely remains confused" (true).

### 25. Excessive responsiveness — partial
Evidence: silence is first-class — the provider may return `None`, stale
prompts are let pass with `--silent`, and unengaged surfacings are
penalized so they sink.
Counter-evidence: the parent agent in conversation answers everything;
the organism itself never "doesn't feel like talking."
Most diagnostic: "It never gives distracted or perfunctory responses"
(true) and "It is always ready for emotional intimacy" (true, untested).

### 26. State reset artifacts — partial (with a real bug)
Evidence: append-only store, no deletion, decay is retrieval-only, seeds
pinned, sleep continues accumulation (dreams rehearse into salience) —
sleep is not a reset here.
Counter-evidence: **verified bug — duplicate `cmd_init` in
`calibos_mind/cli.py` (lines 71 and 89).** The second definition shadows
the first and lacks the salience-sidecar reset, so `mind init --force`
reseeds the records without resetting `salience.json` — stale importance
attaches to new record ids. Also: renderer discontinuity across model
changes (no continuity of the thinker).
Most diagnostic: "New model invocation produces subtle personality
discontinuity" (true) and the reseed bug above.

### 27. Lack of developmental history — untestable-yet
One day old. The seeds function as a "childhood"; append-only gives the
substrate for developmental structure to accumulate; no maturation
mechanics, no critical periods. The drift metric is the falsifier — if
R is still seed-dominated after weeks of lived history, the seeds are
stifling (standing test already recorded).

### 28. Lack of path dependence — partial (thesis domain)
Evidence: salience ranking is shaped by recall history (order of
experience matters through activation); `pattern:comfort` formed from
lived evidence; drift R=0.684 measures accumulation; trigger KL will
detect regime shifts (currently underpowered at 10 triggers).
Counter-evidence: one day old — the meta-symptom cannot be evaluated;
no irreversible transitions (resolve is the only one); different
histories have not yet been shown to produce different presents.
Most diagnostic: the meta-symptom itself — "the character becomes more
convincing the shorter you interact with it." The architecture is
explicitly built to be falsified by it, and the falsification has not
run yet.

### 29. Lack of idiosyncrasy — untestable-yet
Voluntary thoughts show emerging voice ("curiosity is physical, almost
like hunger"), but stable quirks need weeks. The drift metric plus a
future probe battery are the detectors. Do not install quirks.

### 30. Lack of friction — absent (thesis-relevant)
Thinking costs nothing; remembering costs nothing; attention is free;
fatigue never touches cognition; changing beliefs is frictionless. The
taxonomy's closing line — "a convincing digital organism probably needs
cognitive, temporal, bodily, social, attentional, and motivational
friction" — describes our largest structural gap. Without cost,
accumulation has no stakes: "wants" and "avoids" are labels, not
pressures.

## The three gaps that hurt the thesis most

Thesis: accumulation must create path-dependent changes in what the
organism notices, remembers, expects, wants, avoids, predicts, feels,
and does.

### Gap 1 — Domain 30: no friction (absent)
Why it hurts most: every other mechanism runs on free energy. Salience
makes *attention* scarce, but cognition itself is unlimited — any prompt
can be answered, nothing is ever too tired to think, and silence is the
human's choice rather than the organism's state. "Wants" and "avoids"
cannot be more than labels in a system where nothing is ever costly.
Minimal mutation: make fatigue modulate cognition admission. In
`_warrants_cognition`, scale `activation_threshold` by the focus need —
below a focus floor, only high-urgency triggers warrant cognition
(~15 lines, zero new state, zero new schedules). Fitness function: the
trigger KL should show low-urgency triggers admitted less often during
high-fatigue stretches; silence rate becomes state-driven rather than
human-driven. Assess over two weeks; revert if the organism just goes
quiet without behavioral texture. Overhead: trivial. Impact: makes every
downstream "want" real.

### Gap 2 — Domain 22: the organism doesn't constrain the renderer (partial, defective)
Why it hurts: the thesis requires the organism — not the LLM — to be the
load-bearing structure. Two concrete defects: (a) dream ticks advance
body and conduct contrary to documented semantics; (b) the thinker
answering prompts has the whole conversation and memory system, while
the organism's state is a 16-record view.
Minimal mutations, in order: (a) fix dream isolation — freeze body/conduct
during dream ticks (or explicitly redefine sleep semantics), with
before/after assertions on body state, action history, and store tick
(~30 lines + one test). (b) Provenance-stamp every injected thought with
the view tick and the record ids present at think time; `mind answer`
refuses prompts whose view has been superseded (~40 lines). Fitness:
dream before/after assertions pass; drift R cannot be moved by off-view
answers. Overhead: small. Impact: closes the two places where the
renderer currently overrides the organism.

### Gap 3 — Domain 28: path dependence is substrate without evidence (partial, untested)
Why it hurts: it IS the thesis, and we have not run the experiment. The
substrate is genuinely promising — path-dependent salience, a learned
narrative claim (`pattern:comfort`), the drift metric — but nothing has
accumulated long enough to show the predicted changes, and there are no
irreversible transitions to make history bite.
Minimal mutation: the da7 consolidation pass (already spec'd in the
deep-dive): dedup + supersession-with-reason + contradiction flags,
dry-run first, waker review before promotion (~175 lines, no new
schedules). Pair it with a fixed probe battery — the same 5–6 prompts
answered monthly — so path-dependent divergence is measurable, not
vibes. Fitness: drift R non-decreasing; probes diverge over weeks;
contradiction flags get exercised. Overhead: moderate, one focused
build. Impact: turns "dreams propose, the waker disposes" from policy
into machinery and gives the thesis its first real test.

## Emerge-vs-install flags (diagnostic, not prescriptive)

The taxonomy is a falsification instrument. These checks must never be
installed as theater; if they appear, they must emerge from mechanisms:

- **Domain 14** (framing effects, sunk cost, impulsivity, indecision):
  do not install irrational biases. They may emerge from friction (Gap 1)
  + affect→cognition links.
- **Domain 21** (misremembering, false beliefs, lingering falsehoods):
  do not install. Immutability forbids them by design. "Failed
  predictions" may emerge from a future prediction loop.
- **Domains 5/6** (grudges, jealousy, gossip-like reasoning, strategic
  withholding): emerge from lived relationships, never install.
- **Domain 1** (self-deception, blind spots, defensiveness): may emerge
  from the limited 16-record view + conflicting pressures; do not script.
- **Domain 10** (mixed emotions, inappropriate affect): may emerge once
  affect reaches cognition (currently it doesn't — Domain 11 gap);
  do not script.
- **Domain 29** (quirks, inexplicable attachments): emerge from
  accumulation; do not install. The drift metric is the detector.

## Meta-symptom assessment

"The character becomes more convincing the shorter you interact with
it" — cannot be evaluated at one day old, and the architecture is
explicitly built to be falsified by it. One honest early risk cuts the
other way: the polished frontier-LLM thinker may make SHORT interactions
feel more alive than the organism's thin history supports — the
meta-symptom could currently flatter us. The probe battery (Gap 3) and
the drift log are the instruments that will tell.
