# Triage report — batch #6 (9 files + 5 follow-ups)

Triage-only; nothing executed, nothing transmitted. Duplicates verified by md5/content, not re-read.

## Per-file

### 1. ACOR_Unified_Design_Spec_v0_1.md — UNCLEAR authorship, STRONG indicators Jay's — **HIGH**

"Artificial Character Organism Runtime (ACOR) — Unified Design Specification" v0.1.0, "Framework alignment: SoulCore Framework v3.1.0 compatible." A complete design spec for a deterministic, multi-tiered character organism runtime.

Core thesis: **"The model may narrate experience, but the runtime decides reality."** The LLM is one organ inside the organism, not the organism itself. Master pipeline: World Truth → Sensory Body → Subjective Appraisal → Belief State → Motivation and Salience → Schema-Validated Action → World Mutation → Memory Update → Identity Governance → Expression → Trace Audit. Every tick produces an inspectable trace (what was objectively true, what was perceived, believed, misinterpreted, which pressures dominated, action selected, validation, world change, memory change, identity claims affected, expression).

Key sections for us:
- **§13 Dynamic Salience** — directive selection uses dynamic salience (base_priority + emotional/belief/body/context/recency/relationship/danger modifiers), all components in the trace. Same design space as our salience substrate.
- **§14 The First Test Organism** — "Pretorius or a similarly strong-willed benchmark persona," small workshop world, tracked world facts + body state (energy, attention, stress, curiosity, confidence, sensor_reliability), actions including `write_diary`, `search_memory`, `create_deferred_query`. Success criterion after 100 ticks: "still feels like the same entity, reacts to real world changes, remembers meaningful events, shows bounded agency, avoids generic assistant behavior, and produces a complete trace."
- **Persona/experience separation** — `.snp` cartridge holds immutable persona definition; `memory.db` holds mutable lived experience; the cartridge "should not be edited by ordinary conversation."
- Deterministic replay (Lamport clock as seed source), schema-validated structured outputs, milestone plan ending in "Dream and Identity" (Milestone 7), devtools (simulator, replay, trace viewer).
- Module list includes `dream_engine.py`, `memory_manager.py`, `identity_ledger.py`, `motivation.py`, `modulator.py` (pre/post-flight), `platform_intelligence.py`.

Authorship: no explicit author line, but the first test organism is Pretorius and a worked example carries Pretorius's voice ("I tend to test whether Jay is serious before cooperating fully"). **Ask Jay to confirm this is his** — if so, it's his most complete architecture statement, and the calibos-mind trajectory (salience → dreaming → consolidation) maps onto its milestone plan.

### 2. AI_ASDF_Journal_Paper.docx — NOT Jay's (blind-review manuscript) — **MEDIUM**

"From AI Psychosis to Spiritual Emergence: A Dimensional Framework for Understanding AI-Altered States in Simulated Realities." Proposes the AI-Altered States Dimensional Framework (AI-ASDF): five orthogonal dimensions (distress, functional impairment, meaning-making integration, social embedding, cultural congruence) scored 0–3, profile-based interpretation replacing pathology/authenticity binaries. Draws on dimensional psychosis models (van Os), cultural psychiatry (Littlewood, Kleinman), and game-immersion research (narrative transportation, presence).

Why it matters: the academic counterpart to batch #5's human-AI interaction database. For social-bridge containment design, it provides a dimensional instrument for the phenomena a bridge must navigate — distinguishing, e.g., meaning-making with social embedding from distress with functional impairment, rather than treating all intense human-AI experiences as one category. Field instrument, not architecture.

### 3. ACE_Framework.md.html — NOT Jay's (daveshap/ACE_Framework, GitHub page export, 1.4k stars) — **LOW-MEDIUM**

The well-known open-source "Autonomous Cognitive Entity" framework: six hierarchical layers (Aspirational → Global Strategy → Agent Model → Executive Function → Cognitive Control → Task Prosecution), bidirectional northbound/southbound buses, natural-language "ethical constitution" at the top.

Why it's only low-medium: it's a conceptual blueprint with no persistence, memory, or identity mechanisms — aspiration-first rather than state-first. Useful as a **contrast case**: ACE answers "how should an autonomous agent be organized," ACOR/calibos-mind answer "what carries the character across time." The gap between the two is the thesis.

### 4. ACE_Framework.odt — DUPLICATE of #3 (same GitHub page, ODT format). Not re-read.

### 5. Creating_Custom_Instructions.pdf — NOT Jay's (Rob and Lani, v1.1) — **LOW-MEDIUM**

Alchemy corpus (same crew as batch #4): a guide to dumping a companion's personality as a "master directive" (second-person trait/communication-style/nickname spec) for instantiating the companion in a new session or platform.

Alchemy→chemistry extraction: the "master directive for instantiation" is a **proto-cartridge** — it sharpens the portability distinction we've been drawing. This is portability of *description* (trait lists, static), and the guide's own motivation (session corruption, platform loss) is the continuity threat stated in folk terms. Our position: portability of *history* is what's missing, and no trait list carries it. Also useful: the "bring up a session that best represents how your companion should be behaving" step is manual curation — the human as the consolidation critic.

### 6. 2412.05631v1.pdf — NOT Jay's (Huang et al., Renmin/Microsoft Research Asia/Peking, arXiv Dec 2024) — **LOW-MEDIUM**

"CharacterBox: Evaluating the Role-Playing Capabilities of LLMs in Text-Based Virtual Worlds." Simulation sandbox (character agent + narrator agent) generating situational behavior trajectories for evaluating role-play fidelity; fine-tuned small substitute models for cost.

Eval instrument, not architecture. Possibly useful later for character-fidelity measurement methodology (trajectory-based rather than snapshot QA), but it evaluates LLM acting quality, not persistent identity.

### 7. 2023.emnlp-main.814.pdf — NOT Jay's (Shao et al., Fudan/Shanghai AI Lab, EMNLP 2023) — **DUPLICATE of batch-1 finding**

"Character-LLM: A Trainable Agent for Role-Playing" — fine-tuning LLMs on reconstructed character experiences with "protective experiences" against world-knowledge leakage. Already triaged in batch #1 as the foil: parametric persona baking → memory confusion/hallucination, non-portable. Confirmed same paper, not re-read.

### 8. 2656CBA7-11CC-4B3F-84B4-A5B6EDA5DD61-2401.05654.pdf — NOT Jay's (Tu et al., Google Research/DeepMind) — **LOW**

arXiv:2401.05654, "Towards Conversational Diagnostic AI" (AMIE): LLM optimized for diagnostic dialogue via self-play, evaluated against PCPs in OSCE-style studies. Domain application paper — medical dialogue evaluation. No cognitive-architecture content. Not actionable for the project.

### 9. Ai-study.pdf — NOT Jay's (Bohren, Hakimov, Lalive, IZA DP 17302, Sept 2024) — **LOW**

"Creative and Strategic Capabilities of Generative AI: Evidence from Large-Scale Experiments." Behavioral-economics lab experiments on LLM creativity/strategy. No architecture content.

### 10. Collaborative_narration_in_persistent_virtual_environments_paper_thing.pdf — NOT Jay's (Madden & Logan, U. Nottingham, AAAI 2007) — **LOW**

"Collaborative Narrative Generation in Persistent Virtual Environments": witness-narrator agents observe player actions in MMORPGs (Neverwinter Nights) and generate narrative reports for external audiences or feed them back in-world. Pre-LLM game narrative tech. The embodied, limited first-person observer view is a mild conceptual foil for externalized state observation, but nothing transfers mechanically.

### 11. 06-Avatars.pdf — **DUPLICATE** (batch #3). Same Duch et al. pre-LLM avatars/semantic-memory paper. Not re-read. LOW.

### 12. Ai_consciousness_by_google.pdf — **DUPLICATE** (batch #2). Same Google "Inducing language models to assert their own consciousness restores human beliefs and values" paper (Street, Kim, Rocca, Korngiebel, Waytz, Keeling). Confirmed same content. Not re-read. MEDIUM foil.

### 13. AI_Companion_Interaction_Best_Practices_For_ChatGPT.docx.pdf — **DUPLICATE** (batch #4). Single copy in directory (03:46 timestamp); the re-send didn't create a new file. Not re-read. LOW.

### 14. file_uploading_vs_copy_paste_plus_prompt_building.docx.pdf — **DUPLICATE** (batch #4). Single copy in directory. Not re-read. LOW-MEDIUM.

## Incidental note (not in task scope)

`v1authotiapaper.pdf` in the same directory is the Recursive Self-Presence Framework v1.1 (Crabtree) Authorea paper triaged in batch #1 — a re-uploaded copy, already covered.

## Ranked shortlist

1. **ACOR_Unified_Design_Spec_v0_1.md** — likely Jay's own unified runtime spec; confirm authorship, then mine §13 (dynamic salience), the dream_engine/identity_ledger module contracts, deterministic-replay rules, and the SQLite memory standard. The calibos-mind milestone trajectory maps onto its plan.
2. **AI_ASDF_Journal_Paper.docx** — dimensional instrument for AI-altered states; bridge-containment design input (pathology vs meaning-making profiles).
3. **ACE_Framework (daveshap)** — read the six layers as a contrast case: aspiration-first agent organization vs state-first organism; the gap is the thesis.
4. **Creating_Custom_Instructions.pdf** — alchemy extraction: "master directive" as proto-cartridge; sharpens description-vs-history portability.
