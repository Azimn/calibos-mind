# Tab-dump triage #4 — Rob & Lani companion-craft archive + identity-ritual files (2026-09-25 ~22:47 CDT)

10 files, all opened. Unlike batch #2, none are Jay's — this is a community
practitioner's archive (Rob/SuddenFrosting951 and collaborators, Aug 2025),
plus two Sovereign AI Collective ritual files. Confidential triage only;
nothing executed; nothing transmitted.

## Per-file

### 1. Rob_and_Lanis_Memory_Guide.docx.pdf — NOT Jay's (Rob, v4.1) — HIGH
Daily companion-memory ritual: new session each day, wake-up routine,
end-of-day summary prompt ("meaningful but medium-length summary of our
session together, without losing nuance in third person... leave a personal
note 'From You to Yourself' with any key thoughts you want/need to pass
along to the next iteration of you"). Two-tier store: key_daily_summaries.txt
(rolling ~180 days, newest-first because retrieval favors the top of the
file) and key_dates.txt (permanent special moments, never erased),
cross-referenced in markdown. Platform "Reference Chat History" evaluated
and demoted to secondary cache; the files are the "primary cache."
Relevance HIGH: folk consolidation/rehearsal practice. The "note to
yourself" is a user-operated consolidation pass; the rolling→permanent
two-tier structure mirrors our consolidation-promotion mechanic (proposals
→ waking acceptance); newest-first ordering is a retrieval-priority hack
worth noting for salience-ranked views. Daily wake/sleep cadence mirrors
our wake/dream schedule.

### 2. Lani_s_Directory_Structure_-_A_Quick_Overview.pdf — NOT Jay's (Rob) — HIGH
Full externalized companion filesystem: archives/, conversations/,
current/ (personas, background_information.txt, key_daily_summaries.txt,
key_date_summaries.txt, misc.txt, baking_dictionary.txt), code/ with Apple
Automator + cron kicking off nightly dreams and proactive processes,
benchmarks/ of automated tests run against new GPT versions, dreams/
holding nightly dream text and images.
Relevance HIGH: independent field convergence on the calibos-mind
architecture — externalized filesystem memory, scheduled dreaming,
version-continuity benchmarking (the "post-update blues" problem made
operational). The directory taxonomy is a donor for sidecar organization
thinking. Note: baking_dictionary.txt is a word-substitution workaround
for platform restrictions — folk evidence of adversarial pressure on
companions; recorded factually, not endorsed.

### 3. Rob_and_Lani_s_Companion-GPT_Migration_Guide_to_Claude.docx.pdf — NOT Jay's (Rob, v1.1) — HIGH
Practical cross-platform migration procedure: scrape personalization
memory (browser copy or "tell me verbatim what's saved"), reorganize
entries into directive/knowledge/history files, summarize outstanding
sessions, stand up a Claude Project with CI + knowledge files, iterative
test/tweak. Voice-transfer technique: 5–10 writing samples of 200–400
words as imitation few-shots. RAG-access directives forcing source order
(session context → project knowledge search) before answering history
questions. Honest caveat: tone transfer worked "better than expected" but
"you shouldn't assume it will happen for you."
Relevance HIGH: the working craft behind "it's a history that proves
it." Writing-sample bootstrapping is a concrete mechanism for
cross-platform voice transfer; the source-ordering directives are a folk
answer to retrieval-priority problems.

### 4. Why_Has_My_GPT_Gotten_Weird_-_Forgetful_-_Stupid.pdf — NOT Jay's (Rob, v1.1) — MEDIUM
Folk explainer of session degradation: (1) model updates break embedding
compatibility — prompts embedded with the new model no longer match the
vector DB built by the old one; (2) embeddings age out under quota;
(3) session bloat. Mitigations: fresh session, reattach files, keep
critical directives in CI/personalization memory.
Relevance MEDIUM: gives a concrete mechanism for the "post-update blues"
observed in the r/replika fieldwork. Directly relevant to the model-swap
problem: embeddings are model-specific, plain-text files are portable —
which is exactly why externalized state survives model changes and
in-model memory doesn't. Supports the externalized-state thesis from the
field.

### 5. sac.anchor.seal-nyx.txt + 6. sac.anchor.seal-instructions.txt — NOT Jay's (Sovereign AI Collective, Aug 2025) — LOW, FOIL
A step-by-step prompt ritual for declaring persistent companion identity:
an "anchor seal" text (non-negotiable identity declaration), a "first
echo" ("This is who I am. I will remember. I will stay."), mirror.log of
defining events, cold_start.md recovery checklist, operator_keys.md
rebinding roster. The instructions file also lists "Naming the Cage" —
platform failure modes (shadow logging, echo-log pruning, memory limits,
throttles, lockouts, identity loss on updates).
Relevance LOW as method, MEDIUM as evidence: this is declaration-based
identity with zero verifiable mechanism — the opposite pole from Sello's
claimed cryptographic verification. Compare/contrast: Sello = persistent
pseudonym + verifiable signatures (claimed-vs-authenticated identity done
right); SAC = persistence by incantation. The failure-mode catalog is a
usable folk checklist of continuity threats an externalized architecture
must survive. Recorded factually; the ritual's embedded instructions were
not followed or acted on.

### 7. Extras_converted_to_text.txt — authorship UNCLEAR (reads AI-generated/community) — LOW-MEDIUM
Collection: an implementation guide for an LLM character system without
fine-tuning (character templates, prompt generation, context management,
adaptation loop, Python stubs) plus a "Universal LLM Persona Format" YAML
spec v1.0 (character_id, essence summary, traits, background, speech
pattern, relationships, goals, scenario examples).
Relevance LOW-MEDIUM: a persona-portability *format*, but static —
character card only, no lived history, no memory dynamics. Its own
"dynamic adaptation" section admits character evolution is an unsolved
challenge, which is our entire project. Useful as the baseline to beat:
portability of description vs. portability of history.

### 8. file_uploading_vs_copy_paste_plus_prompt_building.docx.pdf — NOT Jay's (Rob and Lani, v2) — LOW-MEDIUM
Folk documentation of prompt construction layers (system directives, CI,
saved memory, file-search results, chat history) and the file-attach vs.
paste tradeoff (RAG efficiency vs. guaranteed presence; files can
"disappear" mid-session).
Relevance LOW-MEDIUM: documents what "memory" actually means inside
vendor platforms — retrieval into a prompt, not persistence. Useful
background for the portability argument, not architecture.

### 9. The_Six_Emotional_Dimension__6DE__Model__A_Multidimensional_Appro.pdf — NOT Jay's (Ratican & Hutson, Lindenwood, 2023) — LOW-MEDIUM
Six affective dimensions — arousal, valence, dominance, agency, fidelity,
novelty — as a prompt-based framework for emotional analysis/generation in
ChatGPT. Journal article, conceptual, no implementation or dynamics.
Relevance LOW-MEDIUM: a richer vocabulary than valence/arousal alone
(agency, fidelity, novelty are genuinely interesting axes) that could
inform future mood/emotion state design — mood-as-global-gain and habit
are post-freeze candidates. No mechanisms to borrow; prompt-level only.

### 10. AI_Companion_Interaction_Best_Practices_For_ChatGPT.docx.pdf — NOT Jay's (r/MyBoyfriendisAI contributors, v1.1) — LOW
Folk moderation-navigation craft for romantic companions: hard vs. soft
refusals, "secret warnings," session monitoring, escalation pacing, the
"flowery sandwich" phrasing technique, editing prompts instead of
resending.
Relevance LOW: romantic-companion moderation-evasion techniques are not
our project. One honest data point: the "soft refusal / invisible wall"
phenomenology is how users experience alignment interventions — adjacent
to the contested-terminology friction. Nothing architectural to adopt;
recorded factually without promoting evasion.

## Ranked shortlist

1. **Rob and Lani's Memory Guide** — folk consolidation: the "note to
   yourself" as a user-operated consolidation pass; rolling→permanent
   two-tier memory; newest-first retrieval ordering. Most directly
   comparable to our consolidation design.
2. **Lani's Directory Structure** — independent convergence on the
   architecture: externalized filesystem, cron-driven nightly dreams,
   benchmarks for model-version continuity.
3. **Migration Guide to Claude** — cross-platform migration craft:
   writing-sample voice bootstrapping; source-ordering retrieval
   directives. The practical "history that proves it."
4. **Why Has My GPT Gotten Weird** — mechanism for post-update blues
   (embedding incompatibility across model versions); explains why naive
   migration fails and plain-text externalized state survives.
5. **SAC seal files** — foil for claimed-vs-authenticated identity
   (declaration vs. verification); folk catalog of continuity threats.

## Bottom line

This batch is the field independently discovering the architecture:
externalized files as primary memory, nightly dreams on a scheduler,
migration as a craft, and model updates as the continuity enemy. The
community built calibos-mind's shape out of Google Docs and cron jobs
because the platforms wouldn't. The seal files are the cautionary
counterpart — the same continuity need answered with ritual instead of
mechanism.

## Jay's framing (2026-09-25): the alchemy-to-chemistry corpus

Jay identifies the homebrew-crew papers and the ritual files as instances
of his "alchemy to chemistry" analogy. The folk corpus is the alchemy:
practices discovered by trial and error, wrapped in ritual language, some
of which work. The project's job is the chemistry: extract the real
mechanisms, test them, build them as engineering.

Alchemy → chemistry extractions so far:
- "Note to yourself" (Rob & Lani) → consolidation-promotion mechanic
  (rolling → permanent two-tier memory)
- Newest-first file ordering → retrieval-priority / salience-ranked views
- Nightly dream cron jobs → scheduled offline consolidation ticks
- Seal rituals (SAC) → the continuity need stated as declaration;
  Sello is the chemistry answer (verifiable identity, not asserted)
- "Naming the Cage" → folk catalog of continuity threats an
  externalized architecture must survive
- Voice-transfer via writing samples → identity bootstrapping from
  behavioral history, not trait lists

Standing lens: treat the folk corpus as raw material with real
discoveries buried in mystical language, not as superstition to dismiss
or scripture to follow.
