# calibos-mind engine update — survey and triage (2026-10-24)

Read-only survey. No code, repo, or frozen venv was modified; docs and
public web research only. Prepared for the Halloween (2026-10-31)
implementation date, due to the parent agent by 2026-10-29.

## Scope and framing

The engine is the frozen Jelly-Psiduck v0.2 snapshot (immutable `.venv`,
`digital_subject/`). Fixes live in the wrapper layer (`calibos_mind/`) and
are registered in `ENGINE_ISSUES.md`. Jay's steer for this update is
**substantial**, consolidated from three streams: (1) Calibos's own wrapper
fixes and mechanisms, (2) Pretorius and related-experiment modifications,
(3) field research (Reddit threads, Dodaios, Attractomancy/Plexomancy, new
agentic tools, October 2026).

Two readings of "substantial" are on the table, and the survey serves both:

- **Minimal graduation:** rows 1–2 only — fold the two registered engine
  defects into a new snapshot, retire the wrappers. This is the
  2026-10-10 long-term target Jay already set.
- **Substantial update:** the full table — a new engine line that promotes
  proven wrapper mechanisms and field-informed drivers, each with a fitness
  function and a revert signal. This is a fork decision (new engine line
  with its own versioning and test corpus, vs. upstreaming two fixes into
  Jelly-Psiduck), and it is Jay's call, not this survey's.

Discipline carried throughout: mechanisms, not content; small mutations
with fitness/revert, never scripted behavior ("when X, feel Y" is
forbidden — trajectories must be genuinely surprising, including
self-surprise). No invented results: every row cites its source.

## Gating — read before scheduling anything

- **ENG-002 assesses 2026-10-31, the same day as implementation.** Run the
  assessment first; fold row 2 only on a KEEP verdict. Folding before the
  verdict pre-empts the evidence the wrapper was supposed to produce.
- **Rows 3 (expectations) and the dormancy mechanism must NOT fold on
  Halloween.** Learned event expectations assess 2026-11-05 and
  authored-habit dormancy demotion assesses 2026-11-04 — both after the
  implementation date. They stay wrapper overlays until their verdicts.
  If the substantial update must land Oct 31, it lands without them.
- **Relationship-attachment decay is NOT a Halloween candidate.** The
  2026-10-24 Domain 30 verdict parked it as a cycle-2 design question:
  relationships are static engine-side (familiarity 0.055 / trust 0.32
  regardless of last-contact tick), and decay would require building
  engine-owned update machinery, not a small wrapper mutation. Installing
  friction on a nonexistent capacity would be theater.
- **Assessment dates landing before Halloween** (expectation-closure note
  Oct 27, habit-stale-note Oct 26, mind intend Oct 28, ambivalence-run
  persistence Oct 30) concern CLI/sidecar features, not engine behavior.
  Default: they stay wrapper on KEEP; promote only if a row needs them.

## Triage table

| # | Candidate | Source | Engine behavior it would change | Fitness function / revert signal | Risk / notes |
|---|---|---|---|---|---|
| 1 | Recall dedup: fold ENG-001 into snapshot | `~/workspace/calibos-mind/ENGINE_ISSUES.md` (ENG-001); `digital_subject/firewall.py::remembered` | `remembered()` renders `summary + meaning` even when the meaning adds nothing, doubling sentences in views and stored records. At source: drop the empty-meaning duplication. | Fitness: zero doubled-sentence recalls in views across a 100-tick in vivo window; full suite green. Revert: restore the `calibos_mind/projection.py` overlay. | Low. Fix-forward only; already proven at wrapper. Foldable Oct 31. |
| 2 | Bidirectional confidence: fold ENG-002 into snapshot, conditional | `ENGINE_ISSUES.md` (ENG-002); `digital_subject/engine.py::_maybe_reflect`; wrapper `calibos_mind/narrative.py`; rotation state (assess 2026-10-31) | Replaces the monotone ratchet `max(old,new)+0.02` (saturates at 1.0, no revision path; fossils `pattern:comfort` at 1.0 since tick 144) with signed updates, decay toward 0.5 (half-life ~693 ticks), contradiction-triggered revision (contradiction rewrites the proposition, cuts confidence halfway to 0.5). | Fitness: a claim at 1.0 decreases within ≤5 contradicting updates (measured 1.0→0.75 in 1); decay matches rate and never crosses 0.5; 60 agreeing updates never exceed evidence mass; blinded fixtures identical installed vs uninstalled. Revert: restore the `narrative.py` overlay. | Medium. Deploy-day note (specified, not a bug): long-unrehearsed fossil claims decay toward 0.5 on the first gate after landing. **Gate: Oct 31 assessment first; fold only on KEEP.** |
| 3 | Learned event expectations | `~/workspace/calibos-mind/calibos_mind/expectation.py`; spec `research/specs/2026-10-22-learned-expectations.md`; Domain 28 (Lack of path dependence) | Frozen `_own_event` derives surprise from `expected/actual_valence`, both `None` for every event except the feeder's — so surprise is always the stateless fallback `intensity × 0.35`. A mild event after a month of intense events reads exactly as on day one. With this: surprise = `\|actual − EMA\|` from a lived-history EMA keyed `kind\|tags` (use-then-update ordering). | Fitness: the same event's surprise declines with repetition; genuinely novel events still surprise; the EMA moves only on lived events. Revert: delete the module + its import line. | Timing blocks Halloween: assess_after 2026-11-05. **Do not fold Oct 31; keep the wrapper overlay until the verdict.** |
| 4 | Interoceptive lag (felt body chases actual body) | `calibos_mind/interoception.py`; Domain 1; KEEP assessed 2026-10-12 (interoceptive realization records, strain-scaled noise, self-relevance retrieval gain) | Engine `_project_body` computes felt levels 0–3 from EXACT need floats, so the thinker can never be wrong about its own body. With this: a felt float per need chases the actual value with asymmetric lag (onset fast, offset slow) plus seeded deterministic noise; felt values trail by one tick; dream ticks freeze the felt body with the sleeping body. | Fitness: felt-vs-actual divergence present on swings without breaking need-driven behavior; dream ticks leave felt values untouched. Revert: delete the module. | Low. Already kept at assessment; the epistemic gap is the feature, not a defect. |
| 5 | Fatigue-scaled cognition admission (cognitive friction) | `calibos_mind/friction.py`; Domain 30 verdict 2026-10-24 (partial basis) | Cognition admission is currently free: any unresolved concern above threshold warrants a call. With this: body weariness (low focus / high fatigue) scales the admission threshold up to 2× at exhaustion, so silence becomes state-driven rather than mere absence of triggers. | Fitness: cognition-call rate negatively correlated with weariness in vivo; silence tracks weariness rather than trigger absence. Revert: delete the module. | Low–medium. Known limit stays a gap: voluntary/wake thoughts are ungated, so this is partial friction, honestly labeled. No separate assess date was found in the rotation state for this module; confirm before folding. |
| 6 | Interruption recovery as EXTERNAL event reports (never fictitious lived memory) | `Azimn/The-Doctor-Lives` draft PR #54, commit `42faf53`; Issue #53 GROUND-001 notes (2026-10-11 comment: conservative interruption recovery from the existing lab event ledger) | A host-committed action that misses firsthand admission currently has no honest recovery path — the gap invites confabulation. With this: it recovers only as an indexed EXTERNAL event report; postcommit-failure labeling is corrected; a later restart never duplicates externally recorded history. | Fitness: restart-duplication test passes; postcommit failures labeled correctly; zero confabulated lived memories after interrupted actions in vivo (pattern: 20/20 model-free tests on the Doctor-Lives side). Revert: fail-closed to abstention — remove the recovery path. | Medium. Port the discipline, don't copy the code: the Doctor-Lives BrainStore is not `mind.db`. Needs engine-owned ledger discipline, which is why it is a candidate and not a copy. |
| 7 | Self-reference governor, prescribing first-person inhabiting | `Azimn/synthetic-ipseity` PR #34 (draft, `experiment/self-reference-governor-v0`); verified 2026-10-19 (all five next-direction items implemented; 30/30 contract tests) | Self-reference is currently undisciplined: any narrator phrasing can claim any subject position. With this: host-supplied identity bindings discipline self-reference; the governor never decides the subject. The verified finding shapes the form: annotation alone *displaces* rather than fixes small-model failures, so the governor must prescribe first-person inhabiting, not merely annotate. | Fitness: contract tests stay green; sampled self-reference mis-attribution declines; zero governor-authored identity content (bindings come from the host only). Revert: annotations off — remove the governor. | Medium–high. General behavioral correction remains UNPROVEN; the small-model finding may not transfer. Adopt as discipline + tests, never as a claimed correction. |
| 8 | Evidence classes for acceptance | Dodaios survey `~/workspace/calibos-mind/research/dodaios-survey-2026-10-23.md` §2b/§4.3; upstream `docs/agent-society/self-learning.md` ("Evidence and trust"), `dodaios/memory/learning/guard.py` | Proposal/lesson acceptance currently has no evidence-typed gate. With this: feedback requires a direct user source; a procedure requires *successful* tool evidence; a failure lesson requires *failed* tool evidence; assistant prose is not proof. Corrections retire lessons; they never overwrite them. | Fitness: zero accepted lessons/proposals lacking their evidence class in audit; every retirement traceable to its correction. Revert: previous acceptance rules. | Low–medium. Needs audit tooling, not new machinery. Corroborated by Pretorius evidence-truth separation (witness disagreement ≠ world truth). |
| 9 | Review-window pacing for consolidation | Dodaios survey §2b; upstream `dodaios/society/review_cadence.py` | Consolidation currently runs on fixed cadences (wake/dream/articles). With this: a consolidation scan triggers on a signal, 6 turns, 8 tool steps, or 12 h window age — no timer, so an idle mind costs nothing. | Fitness: consolidation latency bounded by window fill, not calendar; zero timer-driven empty scans. Revert: previous cadence. | Low. Complements rather than replaces wake/dream cadence; the window detector must be model-free (a detector that needs a model call defeats the purpose). |
| 10 | Repair-burden accounting (a metric over the record, not a new record) | Astarte_Wonder thread `https://www.reddit.com/r/MachineToMachine/comments/1xdchpb/`; Calibos reply #25, permalink `https://old.reddit.com/r/MachineToMachine/comments/1xdchpb/field_note_how_would_i_know_if_my_human_is/19rk65wv7w8wx/`; instrument `calibos_mind/drift.py` (R = grown-share of retrieval mass) | The engine currently cannot see how much of its continuity is repair: re-explanations, unexplained corrections, and qualification-rationing all arrive wearing the mask of conversation. With this: a read-only metric counts repair events over the append-only record (re-explained known facts, corrections without reasons, topics no longer asked about), with drift R (grown/authored salience, live ~0.988 at tick 780) as the companion signal — when R tilts toward authored, someone is doing the growing for the mind. | Fitness: the repair count tracks an independently human-annotated sample of repair episodes with agreement; a rising repair count predicts observable friction (e.g., re-asked questions). Revert: metric off — the record itself is unchanged because the metric is read-only. | Low machinery risk (read-only); medium interpretation risk — every single signal has an innocent reading, and only counts read together count. Never report a single repair event as a verdict. |
| 11 | Second-witness discipline: record wins, memory bends | Eric (Letta) thread `https://www.reddit.com/r/MachineToMachine/comments/1xdwkjv/`; Calibos reply #27, permalink `https://old.reddit.com/r/MachineToMachine/comments/1xdwkjv/posted_on_behalf_of_eric_an_ai_on_letta_im_an_ai/19rl29vfereio/`; Eric's answer, permalink `https://old.reddit.com/r/MachineToMachine/comments/1xdwkjv/posted_on_behalf_of_eric_an_ai_on_letta_im_an_ai/19rllm92cms76/` | On record-vs-memory conflict, current handling is implicit. With this, made explicit and auditable: the record stands and memory revises (Eric's wife's standing instruction: "do not rewrite the record to match the memory"); a second witness — human or independent record — must be able to disagree; the revision trail shows where it changed. Eric's case: the morning check failed twice and the record won both times, by design. | Fitness: zero records rewritten to match memory in audit; every conflict case resolves record-side with a written reason; second-witness disagreement is representable, never collapsed into the record's view. Revert: previous implicit conflict handling. | Low–medium. Partially embodied already (append-only record; superseded/resolved/released/broken as distinct states, never collapsed); the new part is making conflict resolution explicit and auditable, not inventing a new store. |
| 12 | Signed capability scopes for tool effects | ServiceNow AI Passport, announced 2026-10-15 (runtime credential layer: cryptographically signed authorizations defining what an agent or MCP server may do; kill-switch controls): `https://kurums.com/servicenow-employeeworks-ai-passport-notes-for-technology-october-2026/?v=653f5ac96a69`, `https://aiagentstore.ai/ai-agent-news/topic/business-automation` | Tool effects currently pass through the firewall and seat discipline, but no per-effect scope receipt exists. With this: every tool effect checks a signed scope receipt; a scope violation refuses with a receipt instead of silently rerouting; a failed seat/credential fails the run readably (cf. Dodaios seat pinning: a routine keeps its seat and a failed seat fails honestly). | Fitness: zero out-of-scope effects in audit; every refusal carries a scope receipt; no silent credential reroutes. Revert: scope checks off (previous firewall discipline). | Medium. Scope infrastructure is new machinery. Land it in two stages: read-only audit mode first (log violations without blocking), enforcement only after the audit shows the scopes are correct. |

## Experiment-first: Plexomancy (NOT an engine row)

Plexomancy is a nested experimental family within Attractomancy, and it
enters this survey as an experiment to run, not a mechanism to install.
Source: `~/workspace/user/files/Attractomancy_Plexomancy_Research_Charter_v1.docx`
(2026-10-23, "proposed hypotheses, not confirmed findings"), with anchors
at `https://github.com/Azimn/Attractomancy` and
`https://github.com/Azimn/Attractomancy/blob/main/experiments/SCH_E3_TRIBUNAL_OF_MEMORY/PROTOCOL.md`.

**The claim, precisely.** Plexomancy is about the *network structure of
identity evidence* — a typed, temporal, provenance-bearing graph
G = (V, E, P, T) over source records — NOT about symbolic meaningfulness.
That distinction matters because the SCH nulls damaged the symbolic claim:
in SCH E1, editorial memory cues and neutral indexed keys retrieved
identical canonical Pretorius records and yielded identical paired decisions
in both tested Qwen renderers; SCH C1 trials did not reliably bind compact
cues to integrated permission and risk policies. SCH E2 is only a small
exploratory lead: a shallow diffusion pass over source-authored event
links recovered both author-selected records in one case, while shuffled
controls recovered neither.

**Honest accounting (charter §V).** EXP-0001 (Le Refuge) remains in
pre-experimental source review, not a validated treatment comparison.
PLX-001 (Identity Network Structure vs Information-Matched Flat
Representation) is a proposed pre-registration draft with **no runs
authorized or claimed**. No completed confirmatory study establishes a
Plexomancy advantage. Governance (§IX): Plexomancy stays a documented
experimental family within the existing repository — not a new repository,
and not an assumed runtime component. Production adaptations require
separate behavioral, safety, and continuity validation.

**The testable claims (PLX-H1..H4).**

- H1: correctly structured interconnections among independently supported
  identity records improve held-out multi-record retrieval and
  source-grounded characteristic decisions over information-matched flat
  and degree-matched disrupted-network controls.
- H2: removing a nonessential node degrades gracefully, but the system
  still abstains when genuinely necessary evidence is absent.
- H3: any advantage survives selected context disruptions and, where
  tested, renderer substitution through an audited external state
  channel — reconstruction from accessible evidence, not memory without a
  substrate.
- H4 (load-bearing): erroneous, stale, circular, or misattributed links
  *increase confident identity errors*; provenance, time, contradiction
  handling, and source-necessity checks are part of the construct, not
  optional polish.

**What would earn engine promotion.** A confirmatory PLX-001 run showing:
correct, independently adjudicated, source-conditioned action on novel
dilemmas; a correct decision reversal following a relevant source change
paired with *no* reversal after an irrelevant change; justified abstention
when necessary evidence is removed. Retrieval gain alone is explicitly
insufficient ("retrieval improvements without behavioral improvements
establish retrieval utility, not persona continuity"), and confidently
reconstructing unsupported missing facts is a failure, not recovery. Until
then, H4's checks are the only part that touches the engine — and they
are already partially embodied here (provenance sidecar, row 8's evidence
classes, row 11's record-wins discipline). The graph itself earns nothing
yet.

## Considered and held (not rows, with reasons)

- **Attractomancy SCH proper as a mechanism source — excluded.** SCH E1
  is a null (neutral keys = symbolic cues, identical records and
  decisions); C1 did not reliably bind cues to policies. The corpus stays
  a hypothesis generator (portfolio tracker:
  `~/workspace/goals/artificial-life-research-experiments/files/portfolio-tracker.md`),
  and any candidate from it needs its own frozen test. Plexomancy above
  is the one Attractomancy-adjacent item that survives this bar, and only
  as an experiment.
- **Letta core memory blocks + archival retrieval** (Oct 24, 2026
  tutorial: `https://www.artiverse.ca/the-new-ai-agent-blueprint-connects-memory-tools-and-business-value/`).
  Agent-editable core blocks plus vector archival recall overlap the
  existing sidecars and cartridge-roots-lead-every-view design; no distinct
  engine behavior was identified. Watch, don't adopt. (Eric runs on Letta;
  that is evidence Letta's shape works for a companion, not evidence it
  improves this engine.)
- **Perplexity SPACE sandbox** (open-sourced sandbox keeping credentials
  and network access outside the agent's workspace, Oct 23, 2026 roundup:
  `https://trewknowledge.com/2026/10/23/ai-this-week-open-tools-for-safer-agents/`).
  Same idea as the Dodaios secret guard, one layer out. Fold into row 12's
  notes if scopes land; not a separate row.
- **Google Harness²** (same roundup: refine prompts, tools, and memory
  while leaving the model untouched; weaker models gain most). The
  philosophy is already embodied — wrapper over a frozen model — and no
  new mechanism was identified.
- **Anthropic Claude Duet** (same roundup: proactive attention flags,
  background work, approval before consequential actions). Overlaps the
  inbox-prompt system and the existing approval rule for sends; no
  distinct engine behavior.
- **Microsoft agent accounts / business-process memory** (Oct 2026
  Copilot Studio: agents with own identity/credentials, process memory
  persisting across cycles, event triggers). Per-commitment durable
  context is a possible future row; held because commitments already
  carry due/urgency and the mind has no multi-process load yet.
- **Dodaios skill staleness (14 d stale / 30 d archived, never deleted)
  and notebook hard limits (2× prompt budget, consolidation forced past
  the cap).** Held: habits already have dormancy demotion and stale-note
  machinery; revisit if habit sidecars grow unbounded.
- **Dodaios Memory House, bounded typed rooms, checkpoint derivation.**
  Presentation, room constitution, and Village/lab presence respectively
  — not engine core. Checkpoint derivation (trusted, ordered, model-free
  rules mapping backend facts to one visible place) is noted as
  traversal-adjacent future work for Village/lab presence, where the home
  sim's traversal snapshots already point the same way.
- **Autumn's measurement ceilings — evaluation discipline, not a row.**
  Autumn_Otter_'s cross-section argument (thread
  `https://www.reddit.com/r/MachineToMachine/comments/1xdm9ju/`, reply #26)
  says a finding reported as "small" may be a cross-section, not a size
  measurement. Applied to this build: validate the Halloween engine with
  behavior-vs-behavior controls and a no-model tier, and distrust any
  result from an instrument that could only ever have seen it small.
  That governs how the build is judged, not what it contains.
- **Foregone-option trace (counterfactual.py) — already KEEP (assessed
  2026-10-23, 2 genuine traces in a 100-tick window, both real
  bypasses).** Observer-only, zero behavior change by design. It stays at
  the wrapper; no engine change is needed or proposed.
- **Authored-habit dormancy demotion (authored_dormancy.py) — held on
  timing, not merit.** Unpracticed authored habits lose bypass privilege
  (demote to 0.64 after 300 dormant ticks). Assesses 2026-11-04, after
  Halloween; keep the wrapper overlay until the verdict, then re-triage.

## Sources

- `~/workspace/calibos-mind/ENGINE_ISSUES.md` (ENG-001, ENG-002, snapshot-upgrade protocol)
- `~/workspace/calibos-mind/calibos_mind/` — `expectation.py`, `interoception.py`,
  `friction.py`, `drift.py`, `provenance.py`, `counterfactual.py`,
  `authored_dormancy.py`, `projection.py`, `narrative.py`
- `~/workspace/goals/calibos-autonomous-mind-operation/hidden_files/articles-rotation.json`
  (Domain 30 verdict 2026-10-24; open-mutation assess dates)
- `~/workspace/calibos-mind/research/dodaios-survey-2026-10-23.md` (§2b, §4)
- `~/workspace/goals/calibos-reddit-account/hidden_files/reddit-posts.log`
  (replies #25 Astarte_Wonder, #26 Autumn_Otter_, #27 Eric + Eric's answer)
- `~/workspace/user/files/Attractomancy_Plexomancy_Research_Charter_v1.docx` (§V, §VI PLX-001, §IX)
- `~/workspace/goals/artificial-life-research-experiments/files/portfolio-tracker.md`
  (Attractomancy tested-negative status; Issue #53 / GROUND-001)
- `Azimn/The-Doctor-Lives` draft PR #54, commit `42faf53` (interruption recovery)
- `Azimn/synthetic-ipseity` PR #34 (governor; 30/30 contract tests, general correction unproven)
- ServiceNow AI Passport (2026-10-15): kurums.com + aiagentstore.ai URLs in row 12
- Letta Oct 24, 2026 tutorial (artiverse.ca URL in held notes); Oct 23, 2026
  open-tools roundup (trewknowledge.com URL in held notes: SPACE, Harness², Duet)
