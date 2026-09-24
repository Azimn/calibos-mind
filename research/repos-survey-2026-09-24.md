# Repo survey: stealable mechanisms for calibos-mind

Survey date: 2026-09-24. Research-only: nothing here is implemented.
Prior art already surveyed and NOT repeated: da7-tech/dream, SuperInstance/lau-agent-dream,
idlecreative/dreaming, openclaw sleep-consolidation, RemAgent, phenobarbital/ai-parrot dream-cycle,
Letta sleep-time compute (arXiv:2504.13171), Mem0 decay, Zep/Graphiti bi-temporal supersession,
Generative Agents reflection, Cognee.

## Hard constraints (fit judged against these)

- **No new models, no local serving, no LLM calls** in new mechanisms. Deterministic math or
  assistant-through-CLI only.
- **Frozen engine**: all changes live in `calibos_mind/`, `calibos.toml`, or CLI/cron tooling.
- **Archive-never-delete**; **no manufactured affect**; **no machinery in cognition views**;
  `mind.db`/`inbox/`/`dreams/` stay local-only.
- Existing roadmap (`research/improvements-2026-09-24.md`): salience+decay (#2) is the substrate;
  consolidation (#1), SM-2 echoes (#4), inbox TTL (#5), novelty/dissonance (#6), salience-weighted
  workspace (#7). Don't-touch: reconsolidation (recall-mutates-memory), trigger proliferation,
  bulky wake-ups.

## Part 1 — Azimn's other repos

### Azimn/persona_engine_PYTHONX — "Python lab reconstruction of Persona Engine" (non-fork, Python)

The clear ancestor of the Jelly-Psiduck lineage: a deterministic digital-organism prototype with
`.snp` cartridge-driven identity, session-persisted lived history, and an explicit doctrine that
the LLM is a renderer only. The richest source in the whole survey — nearly every file in
`persona_engine/core/` is a concrete, model-free mechanism on our wishlist:

- **`core/memory.py`** — ACT-R-style activation computed fresh at retrieval time:
  `log(sum(t^-decay))` over creation + recall times; a dependency-free "semantic-ish" similarity
  (lexical + synonym-group expansion + character 4-grams — explicitly a no-embedding design);
  plus a salience term (emotional_intensity×1.5, relationship×1.0, identity×1.2, unresolved×1.0).
  Also `compress_old()`: memories older than 30 days with low intensity are compressed to
  `[impression]` stubs rather than deleted — archive-never-delete compatible.
  `https://github.com/Azimn/persona_engine_PYTHONX/blob/main/persona_engine/core/memory.py`
- **`core/intention.py`** — `IntentionQueue` plus Zeigarnik-style `OpenLoop`: intentions decay in
  priority with age; `due_open_loop()` selects by `urgency × emotional_charge / (1 + surfaced_count)`
  and decays urgency on each surfacing. A ready-made unresolved-commitment surfacing lifecycle —
  directly composable with our `mind resolve` semantics.
  `https://github.com/Azimn/persona_engine_PYTHONX/blob/main/persona_engine/core/intention.py`
- **`core/dream_engine.py`** — offline consolidation pass: reads only new events since
  `last_consolidated`, evaluates cartridge threshold rules (`evaluate_rules`), runs at a minimum
  interval. Directly composable with our existing dream cron.
  `https://github.com/Azimn/persona_engine_PYTHONX/blob/main/persona_engine/core/dream_engine.py`
- **`core/event_classifier.py`** — deterministic write-time memory gate: assigns memory_type,
  multi-axis relevance (identity/relationship/somatic/symbolic), and
  `importance = 0.25 + emotional×0.35 + identity×0.35 + relationship×0.25 + somatic×0.20 + symbolic×0.25`;
  `should_store` requires canonical-truth eligibility AND importance ≥ 0.20; auto-creates open
  loops when emotional > 0.45 or identity > 0.4. Includes a **canonical-truth promotion firewall**
  (renderer output can never become canonical memory). `dream_consolidation` events map to
  "semantic" memory type.
  `https://github.com/Azimn/persona_engine_PYTHONX/blob/main/persona_engine/core/event_classifier.py`
- **`core/cognition_schemas.py`** — `EVIDENCE_WEIGHTS` for habit learning: observed_outcome 0.70 >
  expressed_action 0.50 > expressed_speech 0.30 > private_cognition 0.10 (a principled "what counts
  as learning" hierarchy), plus the `CognitiveApplicationReport` pattern: raw model prose never
  mutates state, only validated structured fields with accept/reject reasons.
  `https://github.com/Azimn/persona_engine_PYTHONX/blob/main/persona_engine/core/cognition_schemas.py`
- **`core/proactive.py`** — `ProactiveQueue.evaluate()`: inspects body/relationship/world/intention
  state and emits bounded event proposals (`open_loop_return`, `quiet_check_in`,
  `movement_pressure`, `return_acknowledgement`) with priority and a human-readable
  `public_reason`. A deterministic check-in driver that fits the wake cycle and the "no machinery
  in views" constraint.
  `https://github.com/Azimn/persona_engine_PYTHONX/blob/main/persona_engine/core/proactive.py`
- **`core/belief_ledger.py`** — evidence-gated belief drift: threshold rules on event counts since
  last consolidation, linear decay toward bounds, `fixed` beliefs exempt, disclosure eligibility
  gating.
  `https://github.com/Azimn/persona_engine_PYTHONX/blob/main/persona_engine/core/belief_ledger.py`
- **`core/emotion.py`** — per-state `DECAY_PROFILES` (different decay rates per pressure) and
  `SELF_ACCESS` masking (`accessible_name()` renders e.g. "vague unease" instead of internal state
  labels). The affect content itself violates our no-manufactured-affect rule, but the *masking
  pattern* is a clean answer to "cognition views must never leak machinery/telemetry."
  `https://github.com/Azimn/persona_engine_PYTHONX/blob/main/persona_engine/core/emotion.py`

### Azimn/Agent-Pretorius — Hermes-based persistent research collaborator (non-fork, TS/Python)

A richer-architecture Pretorius agent with a local SQLite life record (autobiographical memories,
relationships, action outcomes, self-model claims, agenda items, research notes) and scheduled wake
cycles. The agent itself is LLM-driven, but its recall plugin is deliberately **local and
deterministic** (vector DBs explicitly deferred):

- **`plugins/pretorius-state/__init__.py`** — the full recipe: bounded discriminative query-term
  extraction (stopwords stripped, long/numeric tokens prioritized); SQLite candidate pool across
  evidence classes; scoring = `3.5×overlap + 2.0×coverage + 0.65×recency + 0.5×confidence +
  1.0×salience` plus class bonuses (agenda priority, named-relationship +3.0, commitment count,
  unresolved count); recency decays slowly as `1/(1+age/30d)`; per-class caps (e.g. memory 4,
  agenda 2) so no class monopolizes; near-duplicate suppression at Jaccard ≥ 0.72 within class;
  whole-record assembly (records never truncated); fail-open fallback to legacy recency
  projection. Also a small high-salience/recent reservoir that only survives final scoring if
  meaningfully connected to the query. Design doc at `docs/CONTEXTUAL_RECALL.md`; debuggable via
  `scripts/preview_recall.py`. Maps 1:1 onto our salience-weighted workspace research item and
  addresses the view-saturation seen in the demos. SQLite-based, zero models.
  `https://github.com/Azimn/Agent-Pretorius/blob/main/plugins/pretorius-state/__init__.py`

### Azimn/TinyPersonaEngine — "Living Entity: First-Person Bridge" (non-fork, Python)

Converts authoritative game observations into a limited private first-person frame, with an
authority model (`world_fact`/`perception`/`belief`/`experience`/`subjective_completion`) and a
`WorldLedger` that rejects non-world records at the canonical write boundary — the same conduct
authority vs. private cognition separation already in calibos-mind, useful as a pattern reference:

- **`src/living_entity_firstperson/perception.py`** — deterministic stimulus salience:
  `min(1.0, 0.65×intensity + 0.25×confidence + 0.10×urgent)`, with access confidence gated by
  embodiment (distance, occlusion, visual range, modality sensitivities). A compact
  attention/salience kernel for trigger gating.
  `https://github.com/Azimn/TinyPersonaEngine/blob/main/src/living_entity_firstperson/perception.py`

### Azimn/npc-steering-plus — fork, symbolic affect steering a frozen LLM (Python)

The affect steering itself is manufactured affect → **excluded**. One stealable kernel in
`lib/state.py`: **exponential decay of each scalar toward a per-scalar baseline target** (not
toward zero) on per-axis time constants — decay-to-baseline is the useful pattern for our
homeostatic-adjacent state, distinct from decay-to-zero forgetting.
`https://github.com/Azimn/npc-steering-plus/blob/main/lib/state.py`

### Weaker fits (skimmed, little to take)

- **Azimn/rho** (fork) — long-running agent; memory is LLM-written `brain/*.jsonl` templates, no
  engine-native salience or retrieval. Only `HEARTBEAT.md.template` (checklist with per-task "last
  run" timestamps) is mildly stealable as recurring-task bookkeeping.
- **Azimn/AliceMod** (fork) — memory = hnswlib vectors + LLM summarization + "emotion awareness".
  Rejected: embeddings + LLM summarization violate no-model-serving; emotion modeling collides
  with no-manufactured-affect.
- **Azimn/Omnicore** (doc-only README) — three-tier memory sketch (5–10 turn volatile; 1–7 day
  decaying mid-term; persistent curated long-term) with an `impact {valence, activation,
  personality_shift}` entry schema "designed to run without vector search". Concept only; the
  emotional weighting clashes with constraints.
- **Azimn/epistemic-verisimilitude-engine** — empty (README title only). Nothing.
- **Azimn/Kurzweil-Brain-Experiments** — developmental attractor nets; longitudinal-experiment
  methodology only, no memory/trigger/consolidation mechanisms.
- **Azimn/Agent-K1-K1** — empty. Nothing.
- **Azimn/Artificial-Life-Research-Journal** — notes/registries only. Nothing.

## Part 2 — Broad survey

### Salience scoring & forgetting curves

**ardhaecosystem/synapse** — "Synapse — Synthetic Hippocampus for AI Agents"
https://github.com/ardhaecosystem/synapse (fork: https://github.com/legato666/synapse)
Self-hosted temporal KG memory (Graphiti + FalkorDB) with a "Hippocampus layer" of nine
biologically-inspired algorithms. The infrastructure is too heavy for us (FalkorDB, LLM entity
extraction), but the *algorithms* are pure math and directly stealable:
- **Salience Scoring**: 4-factor weighted score — recency 35%, frequency 30%, correction 20%,
  emotional markers 15%. (We can adapt "emotional" to the valence tags `mind note` already takes.)
- **Forgetting Curve**: Ebbinghaus exponential decay *modulated by salience* — high-salience
  memories decay ~4x slower; recall events reset the clock (spaced repetition); prune at < 0.05.
- **Consolidation Engine**: Hebbian strengthening of co-occurring entities, contradiction detection
  (primary: `invalid_at` temporal field; secondary: keyword patterns like "instead of"), pruning.
  Runs on a schedule (every 6h).
- **Prediction Error**: novelty detection + contradiction-triggered updates + surprise signals.
- **Schema Extraction**: periodic clustering of entities into generalized schema nodes — the
  episodic→semantic abstraction ladder (cf. our consolidation proposal #1).
- **Retrieval-induced forgetting**: competing memories sink in ranking when a related one is recalled.
- **Reconsolidation** (labile window on recall): CONFLICTS with our don't-touch rule — noted as
  an explicit reject below.

**sukoji/persode** — official implementation of *Persode: Personalized Visual Journaling with
Episodic Memory-Aware AI Agent* (ICES 2025 Best Oral)
https://github.com/sukoji/persode
Ebbinghaus forgetting curve, memory-strength scoring, salience-fused RAG, dual-template journals.
Notably: the memory core is implemented **deterministically and offline** — the GPT-4o calls are
replaced by transparent stubs so the whole thing is unit-testable with no API key. That is exactly
our constraint profile, making it the best *reference implementation* to cross-check our decay math
against when we build proposal #2.

**shivamjohri247/mnemo** — "Biologically-inspired local-first AI agent memory"
https://github.com/shivamjohri247/mnemo
Local-first, SQLite, no cloud. Retention model straight from a paper:
`S(m) = max(S_min, α·log(1+a) + β·ι + γ_c·γ + δ·ε)`, `R(t) = exp(-t·λ_eff / S(m))`,
where `a` = access count (log spacing effect), `ι` = importance, `γ` = confirmation count
(corroboration), `ε` = emotional salience, plus a trust score (untrusted facts decay 3x faster).
Lifecycle bands: Active > 0.8, Warm > 0.5, Cold > 0.2, Archive > 0.05, Forgotten ≤ 0.05 (GC after
7 days — we would archive, not GC). The **confirmation-count term** is the notable addition over
our plan: corroboration as a distinct rehearsal signal.

**huodebing-alt/anima** — *Anima* design paper (built on Ollama + gemma:2b, but architecture is
the point)
https://github.com/huodebing-alt/anima/blob/HEAD/docs/ANIMA_PAPER.md
An always-awake agent with human-like memory (recency × importance × relevance, reinforcement on
recall, Ebbinghaus decay) and a biologically-patterned sleep cycle: NREM consolidation replays
episodic memories into semantic gists; REM dreaming recombines importance-weighted random memory
samples into free-associative narratives **mined for ideas**; a synaptic-downscaling pass decays
and archives weak memories (principled forgetting). Also: the agent's **self-model document — the
seat of its identity — is rewritten during sleep**. Take the phased-sleep design (it validates our
dream → consolidation sequencing, and "REM mined for ideas" is our planned generation phase);
treat the self-model rewrite as considered-and-deferred — it collides with our cartridge-fingerprint
caution. The cognition half requires a local model, which we reject; the memory dynamics don't.

### Rehearsal & spaced repetition

**cheanus/SRSA** — Spaced Repetition Systems for Agents
https://github.com/cheanus/SRSA
Memory self-improvement layer: memories → review cards → scheduled review → self-evaluation →
memory update. Conceptually adjacent to our SM-2 echoes (proposal #4), but its review step needs
the agent to self-evaluate with a model — we reject that half. Our engagement signals
(answer vs. silence) are the honest substitute for its self-grading.
**FSRS** (Free Spaced Repetition Scheduler, ML-based, Python lib available): state of the art for
flashcards, 30–50% fewer reviews than SM-2 — but ML-fitted weights make it a "model" in spirit;
SM-2 stays the cleaner choice for us. Mentioned so we don't re-derive it later.

### Surprise, novelty & prediction error

**genesis-agent** (garrus800-stack/genesis-agent; earlier fork davidwuchn/genesis-agent)
https://github.com/garrus800-stack/genesis-agent
Self-aware cognitive agent with a cognitive meta-loop
(Expect → Simulate → Act → Surprise → Learn → Dream → Schema → better Expect). Stealable pieces:
- **ExpectationEngine**: quantitative predictions with **no LLM calls**.
- **SurpriseAccumulator**: prediction error as information-theoretic surprise (−log₂P); high
  surprise amplifies learning up to 4×. For us, the probability model can be replaced by our
  *expectations* (the continuity system already tracks them): surprise = expectation violated.
  Deterministic, engine-native, feeds proposal #6.
- **Never-pruned change register** (`/changes`): journals every loss the cycle produces — pruned
  knowledge, released memories, consolidations. This is our archive-never-delete principle with an
  audit surface. Cheap to implement as an append-only event log.
- **SelfNarrative**: ~200-token autobiographical identity summary injected into every cognition
  call. Adjacent to our pinned-roots workspace — but note it *injects identity into the prompt*,
  which is our existing design; the idea to steal is versioning it (see persona-as-artifact below).
- DreamCycle phases 1–4 are heuristics (phase 5 needs one LLM call — reject that phase).
- Reject: self-modifying code, "emotional state" as simulated affect, Ollama/Claude/GPT dependence.

**robit-man/egg-events** — `docs/COGNITIVE_MEMORY_RESEARCH.md`
https://github.com/robit-man/egg-events/blob/HEAD/docs/COGNITIVE_MEMORY_RESEARCH.md
A research doc (not code) with a concrete, deterministic attention-gating formula:
`attention = novelty + prediction_error + user_relevance + unresolved_uncertainty + task_or_safety_priority − repetition − interruption_cost`.
Directly usable as a triage/priority formula for inbox ordering or trigger gating (proposals #5/#6).
The candidate novelty signals list (new stable entity, violated short-horizon prediction, direct
correction, speech/vision mismatch…) is a good checklist for what our `--surprising` flag should mean.

### Consolidation pipelines

**Synapse Consolidation Engine** (above): Hebbian co-occurrence boost + `invalid_at`
supersession + scheduled runs. The `invalid_at` pattern is Zep-style bi-temporal thinking without
the graph DB — implementable as a column.
**Anima NREM pass** (above): episodic → semantic gists on a schedule.
**da7-tech/dream** (prior survey): deterministic dedup/supersede-with-reason/archive — still the
best template for the deterministic half of our consolidation.

**alphaonedev/ai-memory-mcp** — `docs/persona.md`: "Persona-as-artifact"
https://github.com/alphaonedev/ai-memory-mcp/blob/HEAD/docs/persona.md
A **Persona as a first-class memory row**: synthesized from a cluster of Reflection rows about an
entity, with `entity_id`, monotonic `persona_version`, 300–500 word Markdown body where every
claim is footnoted to its source reflection ID, `derived_from` provenance edges, and **old
versions kept on disk for audit**. This is the strongest external validation of the user's
lived-vs-implanted-history thesis found in this survey: identity *distilled from* accumulated
reflections rather than implanted up front. Implementable as a consolidation output (a specialized
kind of our proposal #1 `insight`), fully within constraints. The versioning + footnoted
provenance + keep-old-versions pattern should be copied wholesale.

### Persona & lived history (contrast)

**ARPAHLS/mnemolink** — "Curated, injectable personas and artificial memories for AI agents"
https://github.com/ARPAHLS/mnemolink
Packages versioned persona/memory/lineage "mnemonic products" to inject into agents — the
industrial version of *implanted* history. Useful only as the anti-pattern: everything our
lived-history bet stands against. One line in the report, no theft.

**zbbsdsb/macha** — `docs/research/academic_cognitive_models.md` (spec, not code)
https://github.com/zbbsdsb/macha/blob/HEAD/docs/research/academic_cognitive_models.md
A clean module decomposition (Perception → WorkingMemory → EpisodicMemory → SemanticMemory →
Persona → SocialModel → Reflection) with the Persona module producing *constraints* (Big Five,
defense mechanisms, forbidden drift) that gate reasoning. The "persona as constraint set rather
than prompt text" framing is a useful lens for future cartridge work.

### Attention, workspace & trigger mechanisms

**mitige/humanity** — LLM-free, neural-net-free cognitive agent; GWT + AST + HOT + active
inference as running code, 382 tests, MIT
https://github.com/mitige/humanity
The honesty contract ("reproducing the functional mechanisms does not prove phenomenality") is
philosophically adjacent to ours. Mechanisms worth reading: **specialist coalitions competing for
workspace ignition** (a principled alternative to our fixed 16-slot recency window — ignition
thresholds instead of top-K), **temporal thickness** (retention of the just-past + protention of
the just-about-to-happen as part of the cognitive moment — protention could formalize how our
*expectations* show up in the view), **default-mode wandering** (Phase 7 roadmap — our dream
ticks are a working instance of it). Read for algorithms, not code; it's a whole separate agent.

**adamlap/neural-state-architecture** — `docs/COGNITIVE_STATE_RESEARCH.md` (design doc)
https://github.com/adamlap/neural-state-architecture/blob/HEAD/docs/COGNITIVE_STATE_RESEARCH.md
Deterministic cognitive substrate: perception → persistent prediction → prediction error →
competitive attention (salience/confidence/novelty) → bounded global workspace → broadcast →
integration graph ↔ self-model. The pipeline ordering is a useful sanity check on our own
trigger → workspace → cognition flow; nothing to steal that we don't already have.

### Commitments, goals & intentions

**aget-framework/aget** — `drafts/AGET_GOAL_SPEC.md`
https://github.com/aget-framework/aget/blob/HEAD/drafts/AGET_GOAL_SPEC.md
KAOS goal typing: **Achieve** (one-shot end-state), **Maintain** (steady-state the agent
regulates), **Soft** (quality optimized, not satisfied); "Goal-Loop Ownership" — each goal owns
≥1 loop ⟨owner, trigger, review-action, consequence, cadence⟩. Our commitments are all
Achieve-type; **Maintain-type standing intentions** could formalize things like "keep the inbox
triaged" or "review open concerns weekly" as first-class objects instead of cron-body prose.
Conceptual only; no code to take.

**BDI literature** (boisenoise/skills-collections, muratcankoylan/agent-skills-for-context-engineering,
synthanai/intent-of-thought): belief–desire–intention chains with RDF ontologies. The one
transferable result is the classical **commitment termination conditions** (Bratman; Rao &
Georgeff; Cohen & Levesque): an intention is dropped when its purpose is achieved, becomes
unachievable, or background conditions change — which is exactly our kept/released semantics in
`mind resolve`. The RDF machinery itself is rejected as heavyweight overkill for what our
commitment ledger already does simply.

## Ranked "worth stealing" list (merged: Azimn repos + broad survey)

1. **persona_engine_PYTHONX `core/memory.py`** (Azimn/persona_engine_PYTHONX) — ACT-R-style
   activation computed fresh at retrieval (`log(sum(t^-decay))` over creation + recall times),
   dependency-free similarity (lexical + synonym groups + char 4-grams, no embeddings), a weighted
   salience term, and `compress_old()`: >30-day low-intensity memories become `[impression]`
   stubs instead of being deleted. Our proposal #2's substrate, from our own lineage, plain
   Python, zero models. Read this file first when salience/decay work starts.
2. **Agent-Pretorius `plugins/pretorius-state` recall recipe** (Azimn/Agent-Pretorius) —
   deterministic contextual recall: `3.5×overlap + 2.0×coverage + 0.65×recency + 0.5×confidence +
   1.0×salience`, slow recency decay `1/(1+age/30d)`, per-class caps, Jaccard ≥ 0.72 dedupe,
   whole-record assembly, fail-open fallback. Maps 1:1 onto proposal #7 (salience-weighted
   workspace) and fixes the view-saturation seen in demos. SQLite, zero models.
3. **Synapse 4-factor salience + salience-modulated decay** (ardhaecosystem/synapse) — external
   validation of the same direction: `salience = .35·recency + .30·frequency + .20·correction +
   .15·valence-markers`; decay `R(t) = exp(−t·λ/salience)`; archive at < 0.05. Pure math.
   Cross-check against #1; adapt "emotional markers" to our existing `--valence` tags.
4. **persona_engine_PYTHONX `core/intention.py` OpenLoop** (Azimn/persona_engine_PYTHONX) —
   Zeigarnik-style open loops: `due_open_loop()` selects by
   `urgency × emotional_charge / (1 + surfaced_count)`, urgency decays on each surfacing.
   A ready-made unresolved-commitment surfacing lifecycle that composes with `mind resolve`.
5. **Expectation-violation surprise** (genesis-agent SurpriseAccumulator + synapse prediction
   error) — surprise computed against our *existing expectations* in the continuity system
   (genesis does −log₂P with no LLM calls; we substitute expectation-match for P). The
   deterministic core of proposal #6 (novelty/dissonance triggers).
6. **Persona-as-artifact** (alphaonedev/ai-memory-mcp) — versioned persona rows distilled from
   reflection clusters, footnoted provenance, old versions kept. A consolidation output type
   (specialized `insight`) that directly serves the lived-vs-implanted-history research question.
   Copy the versioning + provenance pattern wholesale.
7. **Never-pruned change register** (genesis-agent `/changes`) — append-only journal of every
   loss: archived memories, released commitments, consolidations. Our archive-never-delete
   principle with an audit surface; trivially implementable.
8. **persona_engine_PYTHONX `core/event_classifier.py` + `core/dream_engine.py`**
   (Azimn/persona_engine_PYTHONX) — deterministic write-time memory gate (multi-axis importance
   formula, `should_store` threshold, canonical-truth promotion firewall so renderer output can
   never become canonical memory) and an offline consolidation pass (only new events since
   `last_consolidated`, minimum interval, cartridge threshold rules) that composes directly with
   our dream cron.
9. **Egg-events attention formula** — `novelty + prediction_error + user_relevance +
   unresolved_uncertainty + priority − repetition − interruption_cost` as deterministic triage
   math for inbox ordering / trigger gating (proposals #5/#6); its novelty-signal checklist
   defines what our `--surprising` flag should mean.
10. **TinyPersonaEngine perception salience kernel** (Azimn/TinyPersonaEngine) — the smallest
    stealable unit: `min(1.0, 0.65×intensity + 0.25×confidence + 0.10×urgent)` for attention/trigger
    gating. Plus: npc-steering-plus's **decay-to-baseline** (per-scalar baseline targets, per-axis
    time constants) as the pattern for any future homeostatic-adjacent state — pattern only, its
    affect machinery is excluded.

Honorable mentions: Persode's deterministic memory core (second reference implementation for
decay math); Mnemo's confirmation-count (corroboration) term and Active/Warm/Cold/Archive bands;
Anima's phased sleep ("REM mined for ideas" names our generation phase; self-model rewrite
deferred — collides with cartridge-fingerprint caution); AGET's KAOS Maintain-goals (standing
intentions with owned loops — conceptual); humanity's GWT ignition + temporal thickness
(protention as a way to formalize expectations in the view — read, don't transplant);
persona_engine_PYTHONX's `SELF_ACCESS` masking pattern (clean answer to "no machinery in views")
and `EVIDENCE_WEIGHTS` habit-learning hierarchy.

## Explicit rejects (good ideas, wrong constraints)

- **Synapse reconsolidation** (labile-window rewrite on recall) — violates don't-touch #8.
- **SRSA's LLM self-evaluation loop** — needs model calls; our answer/silence engagement
  signals are the honest substitute.
- **mnemolink implanted persona packs** — the anti-pattern for the lived-history thesis.
- **BDI RDF ontology stacks** — heavyweight infra for what the commitment ledger does simply.
- **FalkorDB / Graphiti / Neo4j substrates** — take the algorithms, leave the stack.
- **Anima's Ollama-backed cognition; genesis-agent's self-modifying code & simulated affect** —
  both trip the no-models / no-manufactured-affect constraints; only the deterministic
  mechanisms transfer.

## Sequencing note

Items 1–3 unblock roadmap proposal #2 (salience/decay substrate): read persona_engine_PYTHONX
`core/memory.py` first when that work starts, cross-check against Synapse and Persode. Items 5
and 9 feed proposal #6 (novelty/dissonance). Items 4, 6, 7, 8 feed proposal #1 (consolidation)
and the commitment lifecycle. Item 2 is proposal #7's (salience-weighted workspace) blueprint.
Item 10 is seasoning for later.
