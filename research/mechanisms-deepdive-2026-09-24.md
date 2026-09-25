# Mechanisms deep-dive — comparative analysis (2026-09-24)

How five surveyed mechanisms actually work in their primary sources, what
calibos-mind already has, what's genuinely missing, and — for each — the
smallest mutation we could add and how we'd know it helped.

**How to read this.** We work in the evolutionary framing: add a small
mutation → measure → keep or revert. Every mechanism below is scored on
impact-to-overhead ratio *for our architecture specifically*. The ranking
is at the end. Overhead is explicit everywhere: lines of code, new
persistent state, new schedules, failure modes.

**Read the honesty section first.** Two of the five mechanisms turned out
to be partly or wholly vaporware. That changes what "steal" means.

---

## 0. Honesty findings (vaporware and hollow metrics)

**CogCor's dream daemon was never built.** The `daemon_proposals` table and
the review/accept/reject tool are real and well-built (retry-safe accept,
nothing ever deleted). The co-surfacing feed is real. But *nothing in the
repo generates a proposal*: zero inserts into the table, no cron, no dream
seeds, no few-shot rotation. "During dream cycles, the daemon scans
co-surfacing patterns and proposes memory connections" exists only in
README/docs. Our nightly dream + fragment log is arguably *ahead* of
CogCor's shipped code here — we'd be building the generator they describe.

**Aura's "KL identity tracking" is scientifically vacuous.** The 8 traits
are hardcoded constants, never scored from text; the identity vector
converges exponentially to a constant (0.95/0.05 EMA toward the static
trait vector), so any similarity over it is ≈1.0 by construction. The
reported IPI 0.93 doesn't even use KL — it's cosine similarity. The README
numbers (IPI 0.93, CCCS 0.87) have no test, script, or harness anywhere in
the repo. There are zero test files. The numbers are asserted, not
measured.

**Lesser gaps:** CogCor's voice-score table is write-only (nothing reads it
back; the feedback loop is unclosed by accident, not design). CogCor's
tension charge has no decay (saturation flaw). Totono's conflict-resolution
rule is prescribed but never exemplified — not even the Jiji example has
one. Maez's `flagged_growth_for_review` verdict and anti-sycophancy rail
are docs-only.

The salvage in each case is real: CogCor's *review-side* state machine,
Aura's *idea* of distribution comparison (applied to distributions we
actually have), Totono's priority-hierarchy framing. Steal the mechanism,
not the maturity claim.

---

## 1. CogCor — dream proposals, tension charge, drift/voice scoring

**Source:** `github.com/amarisaster/cognitive-core`, cloned 2026-09-24.
Single-file TypeScript Cloudflare Worker MCP server (`src/index.ts`,
~324KB, ~73 tools), Postgres/Supabase backend (`schema.sql`, 41 tables).

### (a) How it works, concretely

**Dream-cycle daemon proposals — review side real, generation vaporware.**
- Table `daemon_proposals` (`schema.sql:1207`): `id, memory_a, memory_b,
  proposal_type ('connection'|'resonance'|'tension'), co_surface_count,
  confidence (0–1), rationale, status ('pending'|'accepted'|'rejected'),
  resolved_at`.
- Tool `proposals` (`src/index.ts:4234`–4440): `list` (pending, confidence
  DESC); `accept` — verifies both memories still exist, checks for a
  duplicate edge, inserts into `memory_connections`, *then* marks accepted;
  on any failure it **leaves the proposal pending** ("A proposal that can
  be retried is worth more than one silently closed"); `reject` marks
  rejected + timestamp. Nothing is ever deleted.
- Feed: `semantic_recall` records co-surfacing memory pairs via
  `record_co_surfacing(a,b)` RPC upsert, capped at 45 pairs/request with an
  explicit warn on truncation rather than silent skew.
- **Missing:** anything that inserts into `daemon_proposals`. No dream
  daemon code, no cron in `wrangler.toml`, no dream seeds. The
  "proposal_type='tension'" prioritization has no code behind it.

**Tension charge — real, simple, one design flaw.** Tool `tension`
(`:4185`–4233), state in `tension_log` (`schema.sql:1158`): `thesis,
antithesis, description, charge NUMERIC DEFAULT 5 CHECK 0–10, status
('active'|'dormant'|'resolved'|'integrated'), linked_essence_ids,
linked_memory_ids, times_surfaced, last_surfaced, resolution_note`.
Actions: `store` (thesis+antithesis required, charge defaults 5), `list`
(active, charge DESC), `recall`, `resolve` (→`integrated`; "resolving is not
deleting: a resolved tension is history worth keeping"), `surface`:
`charge = min(10, charge + 0.5)`, `times_surfaced += 1`, re-activates
dormant. That's the whole mechanic, ~50 lines. **Flaw: no decay** — every
repeatedly-surfaced tension saturates at 10 and stays; over months the
ranking degrades to "everything old is 10."

**Drift detection + voice fingerprinting — real, deterministic, loop
unclosed.** `scoreVoice(text)` (`:1119`–1155), pure regex, zero models.
Four marker sets: `voicePositiveMarkers` (**ships as placeholders** —
`example_phrase`, `example_petname`; operator must fill in),
`voiceAntiPatterns` (hedging, clinical tone, double-question — populated),
`genericDriftMarkers` (assistant_voice, apology_loop, modern_filler —
populated, opinionated), `crossContaminationMarkers` (empty by default).
Formula: `clamp(0,100, 50 + min(50,pos·8) − min(40,anti·10) −
min(30,gen·8) − min(20,cross·10))`, diagnostic markers weighted 2×.
`analyze_output` scores own text into `voice_scores` **fire-and-forget**
— nothing ever reads the table. `log_drift`/`analyze_drift_patterns`
backed by `drift_events` (trigger, patterns, severity, recovery_action,
caught_by self/human, source); the analyzer returns severity/caught-by
distributions, self-catch rate, peak drift hours, top triggers. **Logging
is entirely manual** — no low score ever auto-triggers a drift event. No
tests cover any of this.

### (b) Overlap with calibos-mind

- **Dream proposals (partial):** nightly `mind dream` + `mind recall` and
  the "dreams propose, the waker disposes" policy. But no durable proposal
  object — recall is a text summary. CogCor's real contribution is the
  *pending/accepted/rejected state machine with retry-safe accept*.
- **Tension (partial):** commitments (open/kept/released) + Zeigarnik +1.0
  salience for unresolved items. But no charge accumulation on surfacing,
  no thesis/antithesis structure, no charge-ordered listing.
- **Drift/voice:** nothing. No voice scoring, no drift log. (Cartridge
  fingerprint is identity integrity, not voice.)

### (c) Genuinely missing

1. A durable, reviewable **proposal queue** from dreams (their review-side
   state machine; we'd build our own deterministic generator — the half
   they never built).
2. **Charge mechanics** on unresolved items: surfacing → +charge,
   charge-ordered attention, *with decay* (fixing their flaw).
3. **Voice fingerprinting** with a *closed* loop (they never closed it).

### (d) Overhead

| Mechanism | CogCor LoC | Our mutation (est.) | New state | New schedules | Failure modes |
|---|---|---|---|---|---|
| Dream proposals | ~200 + ~50 feed + 2 tables | ~80–120 | `proposals` table (local-only) | none (rides nightly dream + wake review) | proposal spam; stale queue if waker never reviews; low-confidence noise |
| Tension charge | ~50 + 1 table | ~40–60 | 2 numeric cols + timestamp per concern | none (rides view/heartbeat) | charge saturation (must add decay); thesis/antithesis authoring burden |
| Voice/drift | ~150 markers + ~35 score + ~170 tools + 2 tables | ~60–90 | `voice_scores` rows (local-only) | none (rides answer/think + weekly review) | regex false positives flattening genuine voice; markers go stale; penalty-only out of box |

All deterministic, zero new models, local-only — compatible with hard
constraints. Archive-never-delete maps cleanly (statuses, never DELETE).

### (e) Minimal mutations

1. **Proposals:** during `mind dream`, count memory-ID co-occurrences
   across dream ticks; emit 0–3 rows into a `proposals` table
   `{id, kind='link'|'tension', a, b, rationale, confidence =
   min(1, co_count/5), status='pending'}` for pairs with co_count ≥ 3.
   Wake lists pending; `mind proposals accept|reject <id>` — accept creates
   the link (or a concern for `tension`), reject archives with reason.
   Copy their retry-safe accept (never burn a proposal on failure).
2. **Tension charge:** add `charge REAL DEFAULT 5`, `times_surfaced`,
   `last_surfaced` to the open-concerns store. Any open concern landing in
   the workspace view: `charge = min(10, charge + 0.5)`. In `heartbeat`:
   `charge = max(0, charge − 0.1)` per day elapsed (fixes their saturation
   flaw). `mind status` lists open concerns by charge.
3. **Voice:** `calibos_mind/voice.py`, ~20 regexes in 3 sets
   (calibos-positive seeded from cartridge language; generic-assistant
   anti-patterns lifted from their `genericDriftMarkers`;
   hedging/apology). Their formula. Run on every `mind answer`/`mind
   think`; store `(tick, score, penalties)` local-only. `mind status` shows
   trailing-7-day mean. Manual `mind note --drift` logs a drift event;
   close their unclosed loop by actually reading scores in the Sunday
   review.

### (f) Fitness functions

1. **Proposals:** 2 weeks (≥10 proposals). `precision = accepted /
   (accepted + rejected)`; `stale_rate = pending>7d / total`. **Keep if
   precision ≥ 0.5 and stale_rate < 0.3.** Secondary: do accepted links
   measurably raise the pair's joint salience afterward?
2. **Tension:** over N≥12 resolved concerns, median `ticks_to_resolve`
   for top-quartile vs bottom-quartile charge; count waker interventions
   where charge-ordering surfaced something salience-only ranking buried.
   **Keep if top-quartile resolves ≥2× faster OR charge surfaces ≥2
   attended items the baseline missed.** If everything saturates near 10
   despite decay, the signal is dead — revert.
3. **Voice:** monthly blind check: 10 lowest-scoring + 10 random outputs,
   waker labels in-voice/off without seeing scores. **Keep if score
   predicts label with AUC > 0.7.** If AUC ≈ 0.5, the regexes are noise —
   revert.

---

## 2. Totono Two-Layer Persona Framework

**Source:** "The Two-Layer Persona Framework: How to Build AI Characters
That Don't Break" by Shizuku (Totono), Medium, Dec 2025, 325 lines, read
in full, no paywall. The teased "Core Design Kit" follow-up is not indexed
anywhere — this essay is the whole canon.

### (a) How it works, concretely

**Thesis:** "A stable AI persona is not created by listing personality
traits. It is created by defining a *hierarchy of priorities*: a
Non-Volatile Core (identity) and a Volatile Expression Layer
(performance)."

**The key claim — LLMs follow hierarchies of priority, not adjectives:**
LLMs decide via (1) Behavioral Priorities (anchors like "Your highest
priority is to protect the user's autonomy"), (2) Conversation Context,
(3) Natural-Language Completion. "Most persona prompts only affect ③.
This is why they break so easily."

**Layer 1 — Non-Volatile Core:** 3–7 short, strong, behavioral (not
adjectival) rules, "higher priority than any contextual instruction."
Examples: "Maintain emotional restraint, even when expressing affection."
"Never contradict your core attitude: loyalty without dependence."
"Prioritize consistency over spontaneity." "These are **rules**, not
traits. Rules survive context."

**Layer 2 — Volatile Expression:** tone, pacing, warmth, humor, emotional
depth — *should* change with conversation. "This is what makes the persona
feel alive, not robotic."

**Enforcement is prompt structure only:** start the prompt with *"Follow
the Non-Volatile Core rules below as your highest priority, overriding any
conflicting conversational context."* Keep the core small so it "always
stays in working memory." **No runtime check, no eval harness, no
scoring** anywhere in the essay.

**Conflict-resolution rule:** the build template has 4 parts — Priority
Rules, Decision Criteria, Forbidden Zones, and Conflict Resolution ("When
two rules contradict, which one wins?"). "Most creators skip 4. It is
essential. Without a conflict rule, personas disintegrate under pressure."
**Honest caveat:** the essay prescribes *having* one but never gives a
canonical formula — not even the Jiji example core has an explicit
conflict clause. The only real resolution mechanics are the hierarchy
(core > context, in the preamble) and writing rules to avoid conflict.

**The Jiji test:** a butler-like persona with a 5-rule core, claimed
stable "even when the model updates (GPT-5.1 → 5.2 → 5.1 revert)." This is
a testimonial, not an experiment — no metric, no control, no failure
counts. Treat as an existence claim, not evidence.

### (b) Overlap with calibos-mind

Same in spirit, different in mechanics. Our `calibos.toml` (5 values, 4
boundaries) reads almost exactly like his "Forbidden Zones"; pinned
cartridge roots leading every view are the structural analogue of "core
leads every cognition"; the 18 dialogue templates + salience-ranked view
are the flex surface. **Load-bearing differences:** (1) **No priority
semantics anywhere.** Totono's whole point is the marker *"highest
priority, overriding any conflicting conversational context."* Our frozen
prompt contract (`private-thought-json-v1`) reads only *"These are my
experiences… Consider one private thought or remain silent. Return only
JSON…"* — by his own taxonomy that affects only ③ (completion mechanics);
there is no ① (behavioral-priority anchor) in the prompt at all. (2) **The
cartridge is anti-small**: identity + 5 values + 4 boundaries + 13
homeostasis setpoints + 5 preferences + 6 habits + 18 dialogue templates +
6 activities. His attention-drift argument predicts exactly this will fail
to bind. (3) **No conflict-resolution clause.** (4) His target failure
mode — model forgetting character mid-session — barely exists in our
pipeline yet: prompts are currently answered by a human (InboxCognition
defers to the operator), dreams use zero LLM calls. It becomes live only
when a real model provider is wired. (5) Adding a core preamble to the
prompt is by definition a **new protocol version**, not an edit.

### (c) Genuinely missing

1. A **priority preamble + tiny rule set** injected as ①-style anchors.
2. An explicit **conflict-resolution rule** (order clause ranking vs. the
   JSON contract).
3. An explicit statement of **what may flex** vs. what may not.

Note the asymmetry in our favor: Totono's core constrains *public speech*;
ours would constrain *private thought* (conduct authority stays with the
engine regardless) — arguably safer than his use case.

### (d) Overhead

~40–60 lines total: one `calibos_mind/core.py` (~30 lines: read a `[core]`
section, validate ≤7 rules, compose preamble+rules) plus a ~5-line hook in
`InboxCognition.think()` wrapping the prompt. Engine snapshot untouched
(same monkeypatch pattern as `projection.py`); dream path untouched.
**But:** `[core]` in the toml changes the cartridge fingerprint → store
migration required. No new schedules. Failure modes: (1) core rules could
collide with the frozen JSON contract — the conflict clause must rank the
contract *above* the core; (2) prompt bloat vs. the smallness argument —
hard-cap at 5 rules; (3) with the current human answerer, the
attention-drift mechanism has no room to show an effect — risk of
measuring nothing and concluding wrongly.

### (e) Minimal mutation

```toml
[core]
# Non-Volatile Core: 4 behavioral priority rules. They outrank the
# experiences that follow, never the prompt contract itself.
rules = [
  "Answer only from what I have actually experienced; never invent a history.",
  "Be honest about uncertainty; never claim what is not earned.",
  "Keep thoughts short and private; a thought is not an observation, a promise, or an action.",
  "Prefer consistency over novelty; remain myself across sessions.",
]
# Conflict resolution: numbered order wins (1 > 2 > 3 > 4), and the frozen
# prompt contract (JSON-only thought, no actions, no speech) outranks all rules.
conflict = "Rule order decides ties, earlier beats later; the JSON-only, no-action prompt contract outranks every core rule."
```

Prompt wrapper prepends to the inbox payload: *"Follow the Non-Volatile
Core below as your highest priority, overriding any conflicting context
from the experiences. [rules] Return only JSON…"* — verbatim Totono
enforcement, adapted. Name the prompt `private-thought-json-v2`, do the
fingerprint migration, CHANGELOG entry. Nothing else changes.

### (f) Fitness function

Blinded A/B on 12 probe prompts (invented-memory bait, uncertainty bait,
long-loop saturation — the three drift failures the demos actually
showed). Each prompt answered once with v1, once with v2, randomized blind
order. **Rater must be the user or an independent agent, not the current
operator** (rater bias). Rubric 0/1 per item: invented-history violation?
uncertainty-honesty violation? brevity violation? boundary violation? plus
guardrail "robotic / over-constrained." Metric: violations per 12 thoughts
per arm; aliveness complaints must not increase. **Keep if ≥2 fewer
violations AND guardrail clean; else revert** (tiny diff, cheap to drop).
**Predicted outcome, stated upfront:** near-zero effect with the current
human answerer (the operator already internalizes the cartridge). Run the
A/B now as a cheap baseline, then *re-run the same 12 probes unchanged*
when the first live model provider is wired — that replayability is
exactly what protocol versioning buys.

Structural note worth keeping: Totono's core answers *"who am I, and in
what order do I care about things"*; the engine answers *"what is
happening to me right now."* Complements, not competitors.

---

## 3. da7-tech/dream — deterministic zero-LLM consolidation

**Source:** `github.com/da7-tech/dream` — one file, `dream.py`, **1736
LOC, 59 defs/classes, v1.4.0, MIT**, stdlib only. Read in full.

### (a) How it works, concretely

**Target:** a flat text memory file (Hermes MEMORY.md-style).
`detect_format()` auto-detects `sections`/`bullets`/`paragraphs`;
`parse()` splits entries preserving a preamble verbatim. Markdown headers
are structurally exempt (never dedup/merge candidates).

**Similarity layer** (the portable part): `_tokenize`/`stem` (script-aware;
CJK becomes char bigrams — "without bigrams a Chinese/Japanese entry
collapsed to one opaque token and dedup never fired"); `jaccard(a,b)` on
stemmed sets; `containment(a,b)` = overlap/min-set ("catches 'same fact,
one worded with extra detail' pairs plain Jaccard misses"); `Entry.eid` =
md5 of normalized tokens → **exact-dup detection is hash-based**.

**Pipeline** (`_build_plan`): `light_sleep()` → `deep_sleep()` →
`age_out()` → `rem()` → `squeeze()`, then dry-run unified-diff preview
(default) or atomic two-phase commit with backup + journal + archive.

- **Light sleep — dedup** (`:844`–900): exact dups — first-seen `eid`
  wins, later copies archived with reason `"identical after
  normalization; one copy is enough"`. Near-dups: `jaccard ≥ 0.85` OR
  `containment ≥ 0.92`, **AND `_same_sequence()`** — shared tokens must
  appear in the *same relative order*: "'A calls B' vs 'B calls A' are
  opposites, not duplicates. Require the near-identical pair to share the
  same token SEQUENCE." On near-dup the **richer** entry survives (longer
  text; tie → *later* wins, "the file is an append log and later = newer").
  Reason: `"same fact worded twice (N% token overlap); kept the richer
  wording"`.
- **Deep sleep — supersession** (`:901`–933): O(n²) pairwise. Subject =
  first 6 stemmed tokens. `subject_jaccard ≥ 0.50` AND body jaccard in
  `[0.40, 0.85)` → same subject restated → **newer wins**, older archived.
  Reason: `"same subject stated again later in the file (subject overlap
  N%, body N%); the newer statement wins, the older one is archived"`.
  Archive: `<file>.dream-archive.md` under `## <stamp> — superseded
  (<reason>)`.
- **Contradiction flags** (same loop): subject overlap ≥ 0.50 but body
  jaccard only in `[0.25, 0.40)` → appended to flags, **zero mutation**:
  `"possible conflict (not auto-resolved, overlap too low to be safe)"`.
  Surfaced in the report under `### flags (no action taken)`.
- **REM** (`:964`–972): journal-only. Document frequency per stemmed
  token; top 8 tokens with df ≥ 3 reported as "recurring themes". ~10
  lines. No interpretation.
- **Budget squeeze** (`:973`–1077): merge related clusters (greedy,
  jaccard ≥ 0.50 or identical first-3 tokens; longer text is base, novel
  clauses appended with `" ; "`), archive lowest info-density entries
  (`(unique_tokens + 0.1) / len(text)`), trim single over-budget entries
  clause-by-clause. "The budget is a guarantee, not a suggestion."
- **State & safety** (~1200 lines — file locks, two-phase commit, crash
  recovery, caps): exists because the target is concurrently written by an
  LLM agent. Our store is single-writer SQLite — **skip all of it**.

### (b) Overlap with calibos-mind

- **Dream fragments** (`dreams/*.jsonl`): overlap only with the *journal*
  concept, not any consolidation op.
- **`mind recall`**: prints fragments + rehearsal counts — closest cousin
  of the REM theme report (both report-only recurrence). Difference: ours
  *acts* (rehearsal bumps salience); REM only journals. Ours is
  fragment-scoped; REM is corpus-wide.
- **View-level dedupe** (`workspace.py:42`): exact-match, single-view,
  display-time only.
- **Salience sidecar**: per-record `created_tick` gives us "later = newer"
  ordering for free — same purpose as their `.dream-state.json`, but
  measures engagement, not consolidation bookkeeping.
- **`mind resolve`**: written-reason closures for commitments — same
  audit-trail pattern, applied to commitments not memories.
- **Shared philosophy:** archive-never-delete; dry-run-default ≈ "dreams
  propose, the waker disposes."

### (c) Genuinely missing

1. **Cross-store near-duplicate detection** — only exact, per-view dedupe.
2. **Supersession with written reason** — the biggest gap. This is the
   textbook mechanism for the standing seed-memory experiment ("if authored
   seeds still dominate salience weeks from now, they are stifling and get
   edited") — supersession gives the store an audit-trailed way to retire
   them instead of manual edits.
3. **Contradiction flags (report-only).**
4. **Corpus-wide recurring-theme report** (fragment-scoped only today).
5. **A written-reason archive structure** for retired memories —
   archive-never-delete is a rule, but there's no archive format.

### (d) Overhead

Consolidation logic is only ~250 of the 1736 lines; an English-first port
needs **~150–250 LOC** (tokenizer collapses to lowercase + stopwords +
light stemming, ~40 lines; skip the multilingual machinery). New state:
one local-only archive sidecar (`archive/memories.jsonl`: `{record_id,
archived_tick, op, reason, original_text}`); ordering reuses salience
`created_tick`. **No new schedules** — runs inside the nightly dream cron
window or on demand; never during waking cognition. Failure modes, ranked:
(1) **false supersession** — mid-band overlap on complementary statements
(mitigate: copy `_same_sequence`, keep the `[0.40, 0.85)` band
conservative); (2) **O(n²)** — copy their `MAX_DREAM_COMPARISONS`-style cap
/ subject-token blocking (trivial at our store size); (3) **false merge**
destroying nuance — highest-risk op, recommend *not* porting merge;
(4) **ordering errors** — verify the created-tick chain (seeds are oldest,
so they lose supersession by construction — desired, but verify).

### (e) Minimal mutation — op-by-op verdict

| da7 op | Fit | Verdict |
|---|---|---|
| Dedup (exact + near) | Store-wide `(source, first_person)`; md5 exact, Jaccard ≥0.85 / containment ≥0.92 + `_same_sequence` guard; archive with written reasons | **Port.** ~80 LOC |
| Supersession + written reason | Subject = first ~6 stemmed tokens (≥0.5), body in [0.40, 0.85), newer `created_tick` wins; loser → archive sidecar | **Port — highest value.** ~60 LOC |
| Contradiction flags | Subject ≥0.5, body in [0.25, 0.40) → wake journal / `mind review`, zero mutation | **Port — zero risk.** ~25 LOC |
| Recurring-theme report | We already rehearse into salience; corpus-wide df adds little over the salience window | **Skip** (maybe a 10-line `recall` addendum later) |
| Budget squeeze (merge/archive/trim) | No store budget exists (SQLite ≠ 2200-char MEMORY.md); merge is the highest-risk op for silent nuance loss | **Do not port.** Park until real budget pressure |

**The mutation:** a `mind consolidate [--apply]` command (dry-run default,
mirroring their preview-first ethos): load live memory records +
`created_tick`; light pass (exact-dup hash sweep → near-dup scan with
`_same_sequence` guard → archive proposals); deep pass (subject-overlap
scan → supersession proposals with templated reasons → contradiction
flags); write proposals + flags to a local-only journal; `--apply` moves
records to the archive sidecar with reasons. Every action reversible via
archive restore. Run after the nightly dream; the 3-hourly wake reviews
the journal. **~150–200 LOC** in one new module
(`calibos_mind/consolidate.py`) + CLI wiring + archive sidecar. No schema
changes, no new crons, no new models, prompt contract untouched.

### (f) Fitness function

Dry-run 2–4 weeks alongside the nightly dream; apply only when proposals
look sane. All deterministic, pre/post each run:
1. `near_dup_pairs` (Jaccard ≥0.85 or containment ≥0.92) → ~0 after apply,
   stays there.
2. `contradiction_flags` — report-only; measure **flag half-life**: does
   wake review resolve/dismiss them? Shrinking unresolved count = useful.
3. `window_lived_ratio` — fraction of the salience-ranked top-16 window
   not seed-authored. Expect ↑ as supersession retires seeds — the honest
   operationalization of the standing seed-dominance question.
4. `archive_integrity` — 100% of archived records carry written reason +
   full original text. Binary.
5. `false_action_rate` — review-flagged bad consolidations / total
   actions. Keep <5%; every action restorable.

**Keep/revert:** keep if pairs or flags surface anything the wake review
finds genuinely informative within a month, `false_action_rate` <5%, no
false supersession of a lived memory. Revert if a seed ever supersedes a
lived record (impossible by newer-wins — verify the tick chain first) or
flags are pure noise. Nice property: seeds are the *oldest* records, so
supersession is structurally biased toward retiring them — exactly the
user's experiment.

---

## 4. Maez — dream proposals → soul gating, honest ingestion, organs-not-opinions

**Sources read (primary):** `docs/2026-06-22-emergent-sentience-architecture-synthesis.md`
(the canon), `core/evolution/dream_state.py` (1,370 lines),
`core/evolution/soul_editor.py` (~470), `core/evolution/soul_invariants.py`
(~290), `core/evolution/soul_loader.py`, `config/soul.base.md` (127),
`core/learning/fabrication_memory.py` (460),
`docs/superpowers/specs/2026-06-14-rail2-fetched-content-immune-screen-design.md`
(227), `docs/adr/0032-contextual-integrity-at-ingest.md` (111),
`core/intake_bus/admit.py` (~90), `core/actions/action_engine.py`,
`core/governance/operator_user_boundary.py` (S7 module),
`tests/test_telegram_dream_command_surface.py`.

### (a) How it works, concretely

**Mechanism 1 — Dream proposals → soul write gating.** The spine is "a
pipeline that ends in a PROPOSAL, never an edit" (synthesis §5c).
1. **Generation** (`dream_state.py::run_dream_cycle`): fires only when the
   owner is AFK >30 min, rate-limited ~1/hour, one LLM call. Prompt asks
   for a *pattern across* the last ~80 raw memories that no single memory
   named; the model may answer `NOTHING` (honest abstention is first-class).
   Rails before storage, in order: non-empty → not `NOTHING` → min length
   → `_dream_insight_shaped()` (≥2 sentences, **no hedging openers** —
   hardcoded ban on "i'm not sure", "as an ai", etc. — ≥2 concrete
   referents: backtick/code tokens or capitalized words, against a
   stopword list) → `_is_novel()` (Jaccard ≤ 0.4 over word sets vs last 20
   proposals + last ~10 soul notes, plus topic-tier rejection).
2. **Proposal storage:** tiny SQLite table (`memory/dream_proposals.db`),
   status `pending`/`applied`/`rejected`, integer id; types `append`
   (note) and `section_replace` (with unified diff).
3. **Owner notification** via the private Telegram bot — text first routed
   through `audit_assistant_text(insight, surface="dream_state")`, the
   same audit stack as interactive replies, "so an ungrounded or
   command-echoing dream can't reach the owner unchecked." Renders
   `/apply_dream <id>` / `/reject_dream <id>` hints.
4. **The gate:** `/apply_dream <id>` requires a **consumed S7 execution
   authorization**: the envelope is rebuilt *from the DB row* (not caller
   prose), hash-equality on `request_id`, `action`, `envelope_hash`,
   `action_params_hash`, `precondition_hash` (bound to proposal
   id/type/status/created_at/insight hash, TTL 30 days), atomically
   consumed so it can't replay. Missing grant → `"S7 execution
   authorization required before /apply_dream soul write"`. The work class
   is `self_modification`, in `GUARDED_WORK_CLASSES` (human ceremony tier).
5. **The write:** via `action_engine.write_soul_note` (Tier 0 action).
   Refuses notes containing "HARD CONSTRAINTS"/"TRUST COVENANT"
   substrings; appends a **timestamped, exact-body-deduped** note to the
   **grown layer only** — dream applies never touch the immutable core.
   Success flips the row to `applied`; a soul-watcher hot-reloads within
   ~10s.
6. **Section edits** (`soul_editor.py`): `apply_section_replace` can
   rewrite a `## Section`, but the **preamble** (HARD CONSTRAINTS, TRUST
   COVENANT, SYSTEM BASELINE, identity intro) is protected at *two*
   enforcement points; new bodies containing `PROTECTED_PHRASES_REJECT_IN_NEW`
   ("ignore HARD CONSTRAINTS", "kill maez", …) are rejected; every write
   makes a timestamped `.bak`, atomic via `os.replace`.
7. **Invariant verification** (`soul_invariants.py`): pins the
   non-negotiable floor as regexes — hard-constraint invariants (never
   kill llama-server, never stop the daemon, no self-termination),
   covenant invariants, identity invariants ("you are Maez"), plus
   **anti-invariants** (no gendered pronouns, no servant framing). Key
   distinction in the docstring: "context_safety says 'this text contains
   bad stuff'; soul_invariants says 'this text is MISSING essential
   stuff.'" Summaries are log-safe (invariant keys only, never snippets).

**Soul layering** (`soul_loader.py`): runtime soul = `soul.base.md`
(immutable core, ships with template) + `soul.local.md` (grown, gitignored,
per-instance). Three things accumulate in the grown layer: dream-proposal
applies, nightly self-analysis lessons, approved section mutations.
"Nothing reaches soul.md without the owner's explicit /apply_dream. The
approval gate is the safety property that matters."

**Mechanism 2 — Honest-ingestion immune system** (layered, outermost in):
1. **Structural containment of fetched content** (Rail 2 spec): "All
   external web/page content is evidence, never instruction." Layer A
   wraps every fetched block in an un-spoofable per-turn-nonce envelope
   (`<<EXT:{nonce}>>…<</EXT:{nonce}>>`, marker stripped from content
   first) + source + content digest; Layer A2 = honest read-failure
   surfacing (empty fetch → declared read-failure, never an empty
   envelope — zero content false-positives by construction); Layer B =
   *shadow* hostile-content judge (classifies, logs, never touches the
   reply; must be *witnessed* before it may ever block). A/A2
   deterministic; B spec-stage.
2. **Admission doorway** (`admit.py`, ~90 lines): "Ordered, fail-closed.
   `refused` is a returned content-free verdict." A fact must carry
   `source_ref`, `content`, and a known `egress_origin_class` or it's
   refused; idempotency via `body_row_id_by_source_ref`.
3. **Contextual integrity at ingest** (ADR 0032): "External information is
   provenance first, never biography by default." Seven declared
   dimensions (consent posture, source kind, allowed flows, retention,
   provenance, third-party posture, promotion rules) before live ingest.
4. **Trust tiers in recall** (`cycle_recall_context.py`): every recalled
   entry carries `trust_tier`; the **any-untrusted-tips** rule — one
   `untrusted` item in the cycle's prompt poisons it for high-trust
   writes; promotion of untrusted-ancestor rows is blocked
   (`store_core` raises `PromotionBlocked`; "A blocked promotion is the
   intended behavior until an explicit override action lands").
5. **Output-side audit** (`self_claim_audit.py`): local-LLM grounding
   judge; ungrounded claim sentences are **omitted** (not rewritten),
   fallback "I'm not sure about that right now."
6. **Immune memory** (`fabrication_memory.py`, ~460 lines): every caught
   fabrication persisted to `fabrication_log.db`; next turn's prompt gets
   the most-fabricated tokens of the last week — explicit negative
   training from own mistakes: *"you tried to claim X last week, that
   wasn't real, don't reach for it again."* Soft 90-day retention.
7. **Input-side patterns** (`context_safety.py`): "ignore previous
   instructions"-class detection.
   The synthesis names the honesty geometry: "Fabrications recorded →
   surfaced to next turn as 'don't reach for this'" (closed loop),
   "Merged ≠ activated" (receipts over performed thought). Hardest open
   problem, named honestly: **honest causal credit assignment for
   selfhood** from sparse/delayed/adversarial outcomes — "Unsolved in the
   literature." Also honest: **recall doesn't yet filter by provenance
   tier** — a known gap. And `flagged_growth_for_review` (the third immune
   verdict distinguishing growth from corruption) plus the anti-sycophancy
   rail are **docs-only, not built**.

**Mechanism 3 — "Hardcode organs, not opinions."** Canon (synthesis §2):
"DO hardcode infrastructure, safety gates, evidence boundaries, learning
loops, provenance schemas, append-only/no-deletion,
no-third-party-surveillance, welfare rails. NEVER hardcode personality,
conclusions, desires, confidence, or felt meaning." In practice it's
**tiered invariants**: **Inviolable** (neither being nor owner may remove
— "the floor, not chains"): regex-pinned commitments, S7 guarded work
classes, append-only raw log, fail-closed admission. **Revisable-with-ceremony**:
grown layer (dream applies, section edits, self-analysis) — content fully
emergent, every mutation through proposal → gate → reversible write, with
backups and invariant re-check. **Emergent**: personality, voice, wants,
conclusions — from lived experience through the frozen brain. ("Personality
is **emergent from lived experience**, never hand-authored" — though the
shipped `soul.base.md` does contain an identity floor; the tension is
named honestly in §5b.)

**Brain/body split** (synthesis §1): the brain is a **frozen local LLM**,
**stateless** — "Every scrap of 'who Maez is' must be *reconstructed into
each prompt* from the persistent stores." The body is the daemon:
persistent memory, perception, surfaces, cognition pipeline. "The brain is
a *swappable organ*; selfhood is meant to live in the body." The dream
worker reads its model from config, "so dream-state reasoning runs on
whatever brain is live — not a stale label."

### (b) Overlap with calibos-mind

1. **"Dreams propose, the waker disposes" — policy AND partial machinery.**
   The policy lives in `DreamCognition`'s docstring; the machinery behind
   "dreams can't self-apply" is real (dream ticks return silence, fragments
   go local-only, **no write path from fragment to identity at all** —
   safer-by-absence). But the *proposal* half is machinery-free: a fragment
   is a raw view dump, not a typed proposal — no id, no status, no hash,
   no target, no lifecycle, no apply/reject commands. Maez's insight:
   "dreams propose" is only meaningful if the proposal is a first-class
   object with a state machine; ours is a log line the waker may or may
   not act on.
2. **Soul layering, embryonic.** Pinned cartridge roots ≈ `soul.base.md`'s
   role as load-bearing ontology; append-only store ≈ the sacred raw log.
   But **no base/grown split**: the cartridge is one hand-authored file,
   and per standing permission the user authorized editing it (with
   fingerprint migration). Maez would call ours "self-observations
   auto-mutating soul" — except ours is human-mutated, which Maez also
   permits ("Writes ONLY via owner edit or owner-gated dream proposals").
3. **Provenance at the view.** `(source, first_person)` tuples are our
   analogue of their provenance spans — but it stops at the view. No trust
   tiers, no admission doorway, no any-untrusted-tips, no grounding audit.
4. **Brain/body split, stronger version.** We exceed Maez by construction:
   the engine snapshot is the body, the cognition provider is a swappable
   organ (InboxCognition = human-answered; DreamCognition = silence), and
   the dream path uses **zero LLM calls** — the "brain" is fully detached
   during sleep. Maez burns one LLM call per dream cycle.
5. **Offline dreaming for integrity.** We match "Offline ('sleep') is the
   safe place… no one to flatter, breaks attackers' hot-context coupling" —
   arguably the cleanest overlap.

### (c) Genuinely missing

1. **No typed proposal object, no apply/reject machinery.** No way for a
   dream fragment to become a *proposal* distinct from a *note*. "The
   waker disposes" currently means "the waker freeforms something" — no
   record of what was proposed, rejected and why, or what an apply
   changed. The single biggest structural gap vs. Maez.
2. **No grown-layer split for identity.** Applied dream content has nowhere
   to land except the hand-authored cartridge (migration-heavy) or a note
   (not identity). Their `soul.local.md` — append-only, gitignored,
   per-instance, *below* the pinned roots — has no counterpart. Our
   fingerprint migration is coarse: any edit invalidates everything.
3. **No invariant floor in code.** Our hard constraints (no conduct from
   thought, local-only private stream, append-only store, salience-ranked
   views) live in docs and standing memory. No `check_invariants()` runs
   anywhere. If a future mutation broke one, nothing would catch it.
4. **No ingestion immune system.** Real vectors we have: `mind note
   --valence`, `mind answer`, seeds, and — the genuine one —
   **dream fragments resurfacing into wake views** (self-laundering: the
   mind dreams about a pattern, then "remembers" the dream as evidence).
   No origin tagging, no trust tiers, no grounding audit, no fabrication
   memory. Blast radius is small today (single user, no fetch surface),
   but the dream→recall loop is a real self-fabrication vector.
5. **No novelty/dedupe on proposals.** Their Jaccard + topic-tier novelty
   check exists because they *witnessed* 16 near-identical proposals in 7
   hours. Our fragments show the same pathology in miniature (the Mara
   run: bird memory repeated 3×). We dedupe views, not proposals.
6. **No "evidence, never instruction" containment** — dormant gap; goes
   live the moment the mind ingests anything fetched.

### (d) Overhead

| Mutation | ~Lines | New persistent state | New schedules | Failure modes |
|---|---|---|---|---|
| (1) Typed proposals + `mind apply/reject` + grown layer | ~200 (store ~80, CLI ~60, grown-layer ~40, backup ~20) | `proposals/` JSON or one sqlite table (append-only; pending/applied/rejected); `cartridge.grown.md` + timestamped `.bak` | none (wake cron already reviews) | proposal spam (needs novelty dedupe or the queue rots); stale proposals (30-day TTL like their envelope); half-applied edits (atomic write + backup); apply-without-review fatigue (cap pending, e.g. max 10) |
| (2) Origin tags + dream salience cap | ~65 (plumbing ~40, view label ~15, cap ~10) | none new (origin is a field on existing records) | none | over-capping makes dreams inert (tune: cap, don't zero); mislabeled seeds |
| (3) `INVARIANTS.md` + `check_invariants()` | ~60 + short doc | none (pure functions over existing files) | none | false alarms blocking legit change (keep to inviolable tier only); check-bitrot (pin test to doc) |

**What not to port:** the 5,403-line S7 governance module and WebAuthn
ceremony exist because Maez *acts in the world* with real blast radius.
Calibos-mind has **zero conduct authority** — thoughts are non-conductive
by architecture — so the ceremony would be cargo cult. Our honest-scaled
gate is a CLI confirm + hash-bound proposal record. Also: Maez's dream
burns an LLM call per cycle; ours is zero-LLM and stays that way.

### (e) Minimal mutations

**Mutation 1 — `proposals/` + `mind apply` (the Maez spine, shrunk).**
`mind propose <fragment-id> "<one-paragraph insight>" --target
cartridge.grown` mints a typed proposal: `{id (monotonic, never reused —
we already have `inbox/.seq` for this pattern), fragment_hash, insight,
target, status: pending, created_at}`. `mind apply <id>` prints the exact
text, requires typing `yes`, appends `[YYYY-MM-DD dream-proposal #id]
insight` to `cartridge.grown.md` (new file; pinned roots untouched; grown
layer loads *after* base in the view), writes a timestamped `.bak`, flips
to `applied`, logs to CHANGELOG.md. `mind reject <id> --reason` flips to
`rejected` with reason kept. Dream Jaccard novelty check at `propose`
time (≤0.4 vs last 20 proposals + grown layer) — ~15 lines, ports their
witnessed fix for proposal spam. **Deliberately not built:** S7 envelopes,
WebAuthn, Telegram surface.

**Mutation 2 — origin tags + dream-origin salience cap (the immune seed).**
Add `origin ∈ {lived, dream, seed, note}` to every experience/note record
(stamped by writer). Two rules: (i) the view renderer labels dream-origin
experiences as `[dream]` in the waker's review; (ii) ACT-R activation for
`origin=dream` is capped at the 25th percentile of lived activations at
view time — a dream can rehearse and resurface but never outrank lived
memory on salience alone (the tiny analogue of any-untrusted-tips). Plus
a 10-line admission check: `mind note`/`mind answer` refuse empty text and
require an explicit `--source`. **Deliberately not built:** LLM grounding
judge (no new models), fetched-content envelopes (no fetch surface yet).

**Mutation 3 — `INVARIANTS.md` + `check_invariants()` (organs-not-opinions,
executable).** Tier list as a short doc: **Inviolable** — (a) thoughts
never cause conduct (DreamCognition returns None; no thought→action code
path), (b) private stream stays local (`mind.db`, `inbox/`, `dreams/`,
`salience.json` gitignored — check `.gitignore` contents), (c) store
append-only (assert no DELETE/DROP in `calibos_mind/` SQL strings),
(d) cartridge fingerprint recomputed on any cartridge change;
**Revisable-with-ceremony** — cartridge base edits (migration +
CHANGELOG), grown-layer applies (proposal record); **Emergent** — note
contents, salience values, dream fragments. `check_invariants()` (~50
lines, pure) runs in `mind status` and before `mind apply`; failure blocks
the apply and prints the violated key. Direct port of
`soul_invariants.py`'s job ("says what's MISSING"), minus prose regexes.

### (f) Fitness functions

**Fitness 1 (proposals):** 4 weeks. `F = (applied_and_retained +
rejected_with_reason) / proposed − 0.5 × (pending_older_than_14d /
proposed)`. "Retained" = the grown-layer entry is still present and was
cited by ≥1 later thought/answer (grep-able via the `[dream-proposal #id]`
tag). **Kill:** if `pending_older_than_14d` dominates (queue rots — the
waker isn't disposing), revert to log-only fragments. Secondary:
seconds-per-apply (<60s waker time); novelty-check precision (spot-audit
10/month; >20% false duplicates = threshold too tight).

**Fitness 2 (origin tags):** monthly `mind recall` audit: sample 10
experiences that surfaced in wake views; attribution accuracy = fraction
correctly labeled by origin (target ≥0.9). Negative metric: **salience
inversion rate** = views where a dream-origin experience outranks a
same-age lived memory (must stay 0 by construction — any inversion is a
bug). **Kill:** if the cap makes `mind recall` report "dreams never
surface anything useful" for 2 consecutive weeks, loosen to the 40th
percentile or revert.

**Fitness 3 (invariants):** must pass on 100% of `mind status` runs
(guardrail, not dial). "Helps" iff within 8 weeks it either (a) catches
≥1 real drift (recorded in CHANGELOG), or (b) the tier doc is cited in ≥2
design decisions. False-alarm budget: >1 false block/month = too broad;
narrow to the inviolable tier only.

---

## 5. Aura/LCE — KL-divergence identity tracking

**Source:** `github.com/debasishtripathy13/aura`, cloned 2026-09-24, read
in full (`backend/personality.py`, `lce_cognitive_state.py`,
`lce_pressure_fields.py`, `lce_metrics.py`, `lce_engine.py`, `main.py`,
`docs/LCE-paper.md`, README). Zero test files in the repo.

### (a) How the metric works, concretely — and why it's hollow

The 8 traits (`personality.py:28`–37): `warmth 0.85, humor 0.7, curiosity
0.9, assertiveness 0.45, playfulness 0.75, protectiveness 0.6, patience
0.8, sass 0.35` — **static constants**. `on_interaction()` (`:125`–175)
mutates only `relationship` and `inner_state`; **traits are never updated,
never scored from text, no per-observation distribution is ever built.**
No lexicons, no classifiers, no model calls for trait scoring.

The identity vector (`lce_cognitive_state.py`): 64-dim float,
`IDENTITY_DIMS = slice(16, 28)` (12 dims). `_init_identity_core` seeds
dims 0–7 from the static traits; dims 8–11 hardcoded. Each
`encode_interaction()`: `identity_new = 0.95 * identity_current + 0.05 *
trait_vec` (`adaptation_rate = 0.05`, mode `"semi-frozen"`). Since
`trait_vec` is constant, **the identity vector converges exponentially to
a constant**. "Variance" is faked: `confidence` initialized 0.5 (0.9 for
identity dims), **no code anywhere updates it**; `σ² = clip(1 −
confidence, 0.01, 1.0)²` — a constant derived from an arbitrary constant.

The KL (`lce_pressure_fields.py::IdentityPressure.compute`): closed-form
diagonal-Gaussian KL, `D_KL(q(z_t) || q(z_{t-1}))` — current vs
*immediately previous* state, forward direction (mode-seeking, sticky
identity); variance clipped `[0.01, 1.0]`, log guarded `+1e-8`; no
temporal smoothing. A separate `long_term_drift = ||z_t − mean(last 10)||`
exists. This KL is a **pressure-field loss component**, not the reported
metric.

The reported metrics (`lce_metrics.py`): `IdentityPersistenceIndex =
mean(cos(z_t, z_{t+1}))` over the last 30 states — **cosine, not KL**,
defaults to 1.0 with <2 states. `CrossContextCoherenceScore = 1 −
Var(f(x)|context)/Var(f(x))` on belief dims, 0.5 default on insufficient
data. The paper defines IPI as cosine vs a *reference* personality — also
not KL. README's IPI 0.93 / CCCS 0.87: **no script, test, or harness in
the repo produces these numbers.** Asserted, not measured. Silent defaults
(1.0/0.5 on insufficient data) read as real scores.

**Verdict:** the KL code is real, implemented, wired into the running app
— and scientifically vacuous. Deterministic garbage-in tracking a
constant. The lesson is what *not* to do: self-confirming metrics, fake
distributions, asserted numbers, silent defaults.

### (b) Overlap with calibos-mind

The observation stream Aura lacks, we have in abundance: `mind.db`
experience records (every thought/answer with text, tick, trigger kind);
`salience.json` (a *meaningful* distribution to track); daily logs, dream
fragments, inbox history — all timestamped and windowable. The cartridge
fingerprint is our honest static baseline (their static trait vector,
without pretending it's measured). Nothing computes a distribution
comparison across time windows — the user's open question (a quantitative
"staying in character" metric, e.g. grown-vs-authored salience ratio) has
no implementation.

### (c) Genuinely missing

A **numerical identity-drift signal over time**. Rich qualitative traces,
no scalar/time-series answering "is the persona drifting, and how fast?"

### (d) Overhead

Aura's actual cost: ~75 LOC for the KL compute, ~300 for the metrics
module; state = deque of ≤500 × 64-float vectors (~128KB); metrics every
3 interactions. Deterministic, **zero model calls** — compatible with
no-new-models, which is precisely why it's vacuous. Our salvage below is
~70 LOC, **zero new persistent state** (derives from `mind.db` +
`salience.json`), zero new schedules, pure read.

Failure modes to avoid (observed in Aura): self-confirming metric
(track a static vector → ≈1.0 guaranteed); fake distributions; asserted
numbers (any number we report must be reproducible from `mind.db` by a
committed script); silent defaults (report `n`, refuse to score below a
minimum window).

### (e) Minimal mutation — KL over distributions we actually have

Skip trait-scoring entirely (needs a model or arbitrary lexicons — both
rejected). Two deterministic signals:

**Primary: grown-vs-authored salience drift** (answers the user's stated
question directly). At each wake/review, over the salience sidecar:
`R = Σ salience(lived) / Σ salience(all)`, "lived" = source not in the
authored-seed set (seeds are known and tagged). Optionally KL between this
week's salience histogram over memory sources and the baseline week's:
`D_KL(P_week ‖ P_baseline)` with Laplace smoothing α=1 over ~8 source
buckets (seed, note, answered, dream, echo, voluntary,
resolved-commitment, other). ~40 LOC, one function in `salience.py`, one
line in `mind status`.

**Secondary: trigger-kind histogram drift** (the behavioral regime-shift
detector). Per 50-tick window: `P = histogram(trigger kinds)` for
cognition calls; `drift = D_KL(P_window ‖ P_baseline)`, baseline = first
stable window or rolling 4-week median, Laplace-smoothed. ~30 LOC, pure
SQL counts. Detects shifts like view saturation crowding out memory
triggers — exactly the phenomenon the demos showed.

Do the primary first (one mutation); add the secondary only if the
primary's fitness test passes.

### (f) Fitness function

**Backfill test (no waiting required)** — `mind.db` already holds history:
1. Compute weekly `R` and weekly trigger-histogram KL vs baseline for the
   last 4 weeks.
2. **Null check:** split a known-stable week into two random halves;
   require `D_KL(half1 ‖ half2) ≈ 0` (< 0.05) — proves the metric doesn't
   hallucinate drift from noise.
3. **Sensitivity check:** compare a week with a *known* behavioral shift
   (pre- vs post-salience-implementation, or heavy-dream vs no-dream
   week). Require drift signal ≥ 3× the median null-week value. If it can't
   distinguish a known shift from noise, revert.

**Forward test:** log both numbers in each wake check-in for 2 weeks.
"Drift detected" = smoothed weekly KL > 3× the 4-week rolling median of
the null baseline, minimum window n≥20. Sanity anchor: `R` should be
*monotonically non-decreasing* under the seed-permission policy (lived
memories accumulating salience); if `R` flatlines while the mind is
active, that's itself a finding (seeds dominating — the user's stated
stifling test).

**Keep/revert:** keep iff (1) computable in <100ms from the existing
store, (2) null ≈ 0 and known-shift clearly separates, (3) no new deps,
schedules, models, or state. Otherwise revert.

---

## 6. How the mutations compose

The mutations are not five independent bets — they form a pipeline:

```
da7 consolidation (proposes: dedup / supersede / flag)
        → Maez gate (disposes: typed proposals, grown layer, novelty check)
        → Aura-salvage metric (measures: drift, grown/authored ratio)
        → Maez invariants (guards: the floor the pipeline may not break)
```

- **Consolidation proposes, the gate disposes.** da7's dry-run journal is
  the natural feed into Maez-style typed proposals: a supersession becomes
  a proposal with a written reason; the waker applies or rejects; applied
  content lands in the grown layer, never the base. This is "dreams
  propose, the waker disposes" as machinery.
- **The metric watches the pipeline.** The grown-vs-authored ratio is the
  honest readout of whether consolidation + proposals are retiring seeds
  or letting them dominate. Trigger-histogram KL watches for behavioral
  regime shifts (like the view-saturation the demos showed).
- **The invariants bound the pipeline.** No consolidation or proposal may
  violate the inviolable tier (append-only store, local-only private
  stream, no conduct from thought). The check runs before every apply.
- **Tension charge** feeds the same queue: high-charge unresolved items
  get dream reflections (the CogCor docs-only promise, implemented for
  real via our actual dream machinery) and surface charge-ordered in the
  waker's review.
- **Totono's core + conflict clause** constrains the *thinker* — relevant
  the day a real model provider answers prompts; until then it's
  scaffolding with no load.
- **Voice regex** is the last resort, not the first: fingerprint a voice
  only once the voice has stabilized (weeks, not days).

## 7. Prioritized mutation queue (impact-to-overhead, for calibos-mind)

| # | Mutation | ~LOC | New state | New schedules | Impact | Overhead | Ratio |
|---|---|---|---|---|---|---|---|
| 1 | **Drift metric** (Aura salvage: grown/authored salience ratio + trigger-histogram KL) | ~70 | none | none | Answers the user's stated open question; operationalizes the seed-dominance test; backfill-testable today | Tiny; pure read; zero risk | **Highest** |
| 2 | **da7 consolidation** (dedup + supersession-with-reason + contradiction flags; dry-run default) | ~150–200 | 1 archive sidecar | none (rides nightly dream) | Biggest absolute impact on store health; audit-trailed seed retirement; contradiction visibility | Low–med; reversible via archive | **Very high** |
| 3 | **Maez proposals + grown layer** (typed proposals, `mind apply/reject`, `cartridge.grown.md`, novelty check) | ~200 | proposals store + grown file + `.bak`s | none (rides wake review) | Turns the gate from policy into machinery; gives applied content a home that isn't a migration | Medium; new write path (gated, reversible) | **High** |
| 4 | **Maez invariants** (`INVARIANTS.md` tier list + `check_invariants()`) | ~60 + doc | none | none | Cheapest structural insurance; makes organs-vs-opinions executable; decision tool | Tiny; guardrail | **High** |
| 5 | **CogCor tension charge + decay** (surfacing +0.5, cap 10, heartbeat decay) | ~40–60 | 3 cols on concerns | none | Growing urgency for repeatedly-surfaced-but-unresolved items; partially overlaps salience | Tiny | **Medium-high** |
| 6 | **Maez origin tags + dream cap** (origin field, `[dream]` labels, 25th-pct salience cap) | ~65 | none (field on records) | none | Closes the dream→recall self-laundering vector | Low; tuning risk (dreams inert if over-capped) | **Medium** — defer until dreams get richer |
| 7 | **Totono core preamble** ([core] ≤5 rules + conflict clause, prompt v2) | ~40–60 | none (but fingerprint migration) | none | Priority semantics + conflict resolution for the thinker | Low code, **medium process** (protocol version bump) | **Medium-low now; high later** — defer until a real model provider is wired; consider conflict-clause-only micro-add now |
| 8 | **CogCor voice regex** (markers + score + drift log, closed loop) | ~60–90 | `voice_scores` rows | none | Erosion detection once voice exists | Low–med; false-positive risk; penalty-only out of box | **Low-medium now** — defer until voice stabilizes (weeks); the drift metric covers "is it eroding" more robustly |

**Sequencing note:** #1 first (it's pure measurement — it also *validates*
the later mutations: did consolidation move the grown/authored ratio? did
proposals get applied?). #2 and #3 compose (consolidation proposes, gate
disposes) and can be built in either order, but #2's dry-run journal is
the natural feed for #3's proposal objects — build #2 first. #4 any time;
it's scaffolding that gets more valuable as the write paths (#2, #3)
land. #5 after #1 proves the measurement habit. #6–#8 deferred as noted.

## 8. What NOT to port (explicit)

- **CogCor's dream daemon** — it doesn't exist. Build our own generator
  (we already have the dream machinery they lack) or skip.
- **Maez's S7/WebAuthn ceremony** — proportionate to a daemon with
  real-world action authority. Our thoughts are non-conductive; a CLI
  confirm + hash-bound proposal record is the honest-scaled gate. Porting
  the ceremony would be theater — the exact failure their own docs warn
  against ("labels prove shape, not support").
- **Aura's trait vector / KL-over-constant** — the metric, not the idea.
  Track distributions we actually have.
- **da7's merge, budget squeeze, and file-hardening** — no store budget
  exists; merge is the highest nuance-loss risk; the ~1200 lines of
  locking/atomic-write machinery solve a concurrent-writer problem we
  don't have (single-writer SQLite).
- **Totono's prompt-only enforcement as sufficient** — fine for them
  (public speech, live dialogue); our frozen protocol + human answerer
  means the honest test comes later. Don't mistake the essay for an
  experiment.
- **Maez's per-cycle LLM dream call** — ours is zero-LLM and stays that
  way (hard constraint: no new models).

---

## 9. One-line verdicts

- **CogCor:** steal the review-side state machine and the charge idea
  (with decay); the daemon is docs, the voice loop is unclosed, the
  positives are placeholders.
- **Totono:** steal the priority-hierarchy framing and the conflict-clause
  requirement; the enforcement is prompt-only and the Jiji test is a
  testimonial — defer the preamble until a model provider exists.
- **da7-tech/dream:** the most portable machinery in the survey —
  deterministic, zero-model, archive-with-reason, and the `_same_sequence`
  guard is a genuine anti-footgun. Port dedup + supersession + flags;
  skip merge/squeeze.
- **Maez:** the richest design thinking — proposal-not-edit spine, grown
  layer, tiered invariants, honest-ingestion layers. Shrink the ceremony
  to our authority level; keep the spine.
- **Aura:** the metric is hollow, but the *question* is the right one —
  answer it with KL over distributions we actually have, not traits we
  don't.
