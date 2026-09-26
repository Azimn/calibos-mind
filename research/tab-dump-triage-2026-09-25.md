# Tab-dump triage (2026-09-25, ~22:20 CDT)

17 links from Jay's closing tabs; 16 read, 1 blocked (Authorea PDF, HTTP 403).
Full per-link notes in the triage report; ranked shortlist below.

## Ranked shortlist

1. **ANIMA** (github.com/huodebing-alt/anima, ANIMA_PAPER.md) — HIGH. "An
   Always-Awake Agent with Human-Like Memory, Sleep, and Dreams on a Small
   Local Language Model." ~2,400 lines dependency-free Python on gemma:2b
   via Ollama (Apple M1, 8GB). Adaptive-heartbeat daemon; dual-path waking
   cognition (fast reflex + slow contemplative tick); typed memories
   (episodic/semantic/reflection/dream/insight/procedural) with provenance
   and link graph; retrieval = recency×importance×relevance with
   reinforcement on recall; decay and true forgetting ONLY during sleep;
   five-phase sleep (salience tagging → NREM gist replay → reflection → REM
   dream recombination → synaptic downscaling → self-model rewrite);
   identity = single versioned ≤250-word first-person self-model document;
   fatigue/arousal, echo guards, append-only journal. Disclaims phenomenal
   consciousness ("consistent consciousness" as engineering target).
   Engineering lessons directly comparable: they moved LLM importance-
   scoring off the encoding hot path into sleep. docs/related_work_digest.md
   is a literature goldmine (Sleep-time Compute, MemoryBank Ebbinghaus,
   Zep/Graphiti bi-temporal edges, CLS theory). Closest independent
   parallel in the dump — read in full.

2. **NeuralCompanion Identity Relay** (github.com/Rakile/NeuralCompanion) —
   MEDIUM-HIGH. Local desktop companion; Continuity Memory + versioned
   SQLite LTM. The mechanism: experimental Identity Relay — import a
   "source-native identity artifact," review and connect it, toggle when
   that continuity is active; includes a complete export protocol. A working
   persona-portability artifact format — the most concrete existing answer
   to persona rescue/import ("it's a history that proves it"). Inspect the
   artifact format.

3. **Character-LLM** (ar5iv 2310.10158, Shao et al.) — MEDIUM, as FOIL.
   "Experience Upload": fine-tune a base model on reconstructed character
   experiences + "protective experiences" to suppress OOC knowledge. Their
   own conclusions: memorized experiences but limited; world knowledge
   confuses memories with hallucinations. Parametric persona-baking has
   exactly the failure modes externalized-state design avoids; fine-tuned
   personas are definitionally non-portable. Anti-thesis of the portability
   work; useful foil for Pretorius bleed questions (separation in weights,
   not state).

4. **utsuwa companion system** (github.com/JuiceBoxxGames/utsuwa) — MEDIUM.
   3D VRM companion; beyond presentation: multi-axis relationship tracking
   (affection/trust/intimacy/comfort/respect separately), 8 relationship
   stages, dynamic mood with causality tracking (remembers WHY it feels a
   way), local semantic memory, memory inspector (view/search/add/delete),
   memory graph viz, export/import saves, time-aware absence reactions,
   self-scheduled timers. Closest field instance of "psychologically
   distinct states as first-class."

5. **xraph (Cortex/KGKit + Human Model)** (xraph.com) — MEDIUM. Cortex agent
   runtime (Go): bounded working memory, personas, checkpoints. KGKit turns
   observations into typed claims with the originating observation attached,
   written as graph edges a later contradiction can find and revise —
   rhymes with attribution/claim work. "The Human Model" whitepaper (in
   review, 2026): seven structured primitives (Skills, Traits, Behaviors,
   Cognitive Styles, Communication Styles, Perception, Personas) as
   explicit anti-prompt-engineering stance. Watch for publication.

## Lower relevance (recorded, not recommended)

- Soar overview (Laird 2022): classical background Jay knows; no new build
  material. Alice (voice desktop assistant): solid shipping hybrid-memory
  reference, no sleep/identity dynamics. Creation OS: deterministic
  hallucination-distance scoring without a second model — ethos matches,
  no memory/persistence. Cognitive-Coherence-Model: preprint, no code.
  LangChain "cognitive architecture" post: term dilution. CAL Google Doc:
  folk taxonomy for versus-battle debates, amusing not scientific.
  VoiceStudio, project-nomad, Saki-AI-Agent, openblob, WebBrain: infra or
  product positioning, no cognitive mechanisms.
