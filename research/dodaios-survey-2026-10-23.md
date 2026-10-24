# Dodaios — structural survey (2026-10-23)

Surveyor: Calibos (subagent pass). Source: shallow clone of
github.com/Dodaios/Dodaios at `~/workspace/vendor/dodaios` (depth 1, cloned
2026-10-23), plus the project's own docs inside the clone. Claims are grounded
in files actually opened; inferences are marked *(inference)*. Limits of the
survey are in §6 — read them before trusting any single claim here.

## 1. What Dodaios is

Dodaios (formerly Personal Jarvis; PyPI still publishes a `personal-jarvis`
wheel for transition — `pyproject.toml`) is an open-source, local-first
"agent OS" for the desktop: a Python monolith with a FastAPI + React frontend
(desktop via pywebview, or headless server) that runs a voice-fronted lead
assistant ("Jarvis"), orchestrates external coding agents in terminal panes,
connects 40+ plugin/marketplace integrations and MCP servers, and — the part
that matters here — hosts a persistent multi-agent **Society**: named agents
with their own model seats, tools, private memories, learned skills, routines
and approvals, which visibly "live" in a rendered 3D world. Apache-2.0,
version 4.0.1, Python ≥3.11 (`pyproject.toml`, `README.md`).

## 2a. The "virtual workspace" — three different things wear that name

Jay's "virtual workspace that felt like a home" is *(inference)* almost
certainly the **Society world**, but the repo uses "workspace" for three
distinct mechanisms. Separating them matters:

**(i) The Society world — the home-like one.** A 3D bird's-eye world
(MASTERPLAN's "signature surface": `docs/agent-society/MASTERPLAN.md` §1)
where each agent visibly lives and works: a desk for missions, a meeting
table for group discussions, an archive building for the knowledge store, a
gate for routines/outbound actions, and — per the place vocabulary in
`docs/agent-society/world-behaviour-manual.md` §2 — each agent has **its own
house** on a town block (the `home` checkpoint: "off duty: paused, or between
runs at night"). The world went through skins: an island (M3, Sept 2026),
then a Mars colony (`dodaios/society/mars/`, `docs/agent-society/mars-runtime.md`),
and since 2026-09-28 the "Dodaios Verse" capital ship is what Agents > Map
opens. The renderer changed; the contracts underneath didn't.

The crucial structural fact: **the world is a projection, never a truth.**
`world-behaviour-manual.md` §1: "The island shows two things and never
invents a third" — what the backend says an agent is doing, and how the
island shows it. An agent's *place* is not state the agent sets; it is a
**checkpoint derived by trusted Python rules** (`dodaios/society/checkpoints.py`)
from facts the runtime already has, in priority order (paraphrased from
`docs/agent-society/memory-house.md` §3.4 and the manual §3): paused →
home/idle; open approval → gate; in a running room → meeting (Town Hall);
memory touched within 60 s → archive (the Memory House); a run in flight →
desk or one of the `hub:*` places chosen by the dominant tool family
(plugins → Plugin Docks, comms → Signal Office, web → the Lookout, desktop →
Control Room, …); delivering a result → gallery. Changes persist through the
roster row and publish a `SocietyCheckpointChanged` event; the figure walks
within a second. **Nothing an agent says moves it; nothing a viewer clicks
moves it directly — every gesture becomes a typed event and the figure
follows because the truth changed.**

What persists: the agent itself is **a durable roster row in
`data/society.db`** (`dodaios/society/society_schema.sql`, `store.py`) —
"a durable roster row, never a resident process" (MASTERPLAN §2.3). Idle
agents are rows woken by bus events: zero processes, zero RAM, zero tokens.
The append-only board in the same DB is the coordination record (typed
envelopes `ASSIGN | CLAIM | RESULT | QUERY | ANSWER | HOLD | RELEASE |
PROPOSE | VETO | DIGEST | SAY`, MASTERPLAN §2.1), written persist-before-
publish, with an observer bus that can never block an append
(`dodaios/society/store.py`, `bus.py`).

What agents can do "in" the world: nothing *in* it directly. They act
through the board and their tools — bounded discussion **rooms** (2–6
members, ≤3 serial rounds, ≤10 messages, silence allowed, restart-safe;
`dodaios/society/rooms.py`), shared meetings (`meetings.py`), quests routed
deterministically to one agent (`quests.py`), team leads decomposing goals
into one ASSIGN per task (`team_lead.py`), and their own contained shell,
browser and file hands. The world renders the consequences.

**(ii) The Memory House** — worth singling out because it's the closest
thing in Dodaios to our home sim. It is a building on the island (north end,
on the archive plot) that is the *face* of the society memory system
(`docs/agent-society/memory-house.md`). Every memory read/write emits a
`DIGEST` envelope with `kind: memory`, and the checkpoint engine sends the
agent figure to the house for a 60-second hold — "when an agent uses the
memory it walks there" was the maintainer's literal ask. The building's core
brightens while an agent stands at it. Underneath: `dodaios/society/memory.py`
(see §2b). Note the doc's own status header (2026-10-05): the "one shared
memory" design in that document is **history** — agents now keep private
notebooks, and `society/shared/` is reached only through a human promote
action; the building and checkpoint rules still apply.

**(iii) The Agentic IDE "workspace"** — a different mechanism entirely:
Project > Workspace > coding-agent session (`docs/igentic-workspaces.md`,
`dodaios/agentic_ide/`, `dodaios/workspace/`). A named group of terminal
panes running external coding CLIs (Claude Code, Codex, OpenCode, …) in one
project folder, with stable session IDs (`pane:<history_id>`), durable
SQLite-claimed deliveries with receipts, and voice orchestration
(`workspace-orchestrate`, `workspace-git`). This is a workbench for steering
other people's coding agents — not a dwelling.

## 2b. Society agents and the per-agent learning loops

**Identity & persistence.** An agent = roster row (adopt-before-mint by
name; exactly one lead, Jarvis) + a canonical, endless `agent_chat` session
bound lazily (`society:<agent_id>` is a pure function of the row, no session
column) + a private file tree under `data/society/<agent-id>/`
(`dodaios/society/README.md`, MASTERPLAN §2.3–2.4). Each agent has its own
provider/model/auth "seat" (`dodaios/society/seat_brain.py`); even its
routines are pinned to a seat and are never silently rerouted to a different
credential when the seat fails (`docs/routines.md`).

**Memory isolation** (current behavior per
`docs/agent-society/self-learning.md`, status 2026-10-05):

- Private notebooks `USER.md` / `MEMORY.md` per agent
  (`dodaios/society/memory_books.py`: journaled legacy migration, locked
  atomic updates, `HARD_LIMITS` at twice the prompt budget — past the limit
  the agent or its review must consolidate before adding).
- A shared-memory tier exists but agents cannot write to it: an agent
  *proposes*, a human promote action copies the page into `society/shared/`
  with `reviewed: true` (`memory.py`: `propose_shared` / `promote` /
  `dismiss`, gated by an approval item). Every memory touch posts a
  `DIGEST kind=memory` line to the board.
- Recall is ranked and labelled: own > shared > user vault > others'
  unreviewed, and result lines carry trust markers (`[own]`, `[shared]`,
  `[unreviewed · web · scout]`). Unreviewed web/agent pages are never in
  any agent's ambient briefing head (memory-house §3.2–3.3, as built).
- The shared `BrainManager` enforces the boundary in the prompt itself:
  society turns receive only their own briefing/style/language; Jarvis'
  global profile, core memory and ambient wiki are excluded, and society
  turns cannot feed Jarvis' global curator. Missing private briefings fail
  closed. Linked namespaces, linked journals and cross-owner journal copies
  are refused; private memory operations take a file lock; there are symlink
  isolation contract tests.

**Learning loops.** Jarvis and society agents share one portable loop design
with separate ownership (`docs/agent-society/self-learning.md`,
`docs/dodaios-learning.md`; Jarvis-side code in `dodaios/memory/learning/` —
`loop.py`, `review.py`, `signals.py`, `guard.py`, `notebook.py`,
`compact.py`). How corrections change later behavior:

- **Review windows, not per-turn firing** (`dodaios/society/review_cadence.py`):
  the person's turns collect in a window, reviewed whole when a model-free
  detector sees a learning signal (explicit "remember", a lasting language
  choice, a correction or frustration), or after 6 of their turns, or 8 tool
  steps, or when the window is older than 12 h at the next turn. *There is
  no timer — an idle agent costs nothing.*
- **Review rules** (`dodaios/society/review.py`): a correction is the
  strongest signal; a lesson is one rule plus one clause of why; improve an
  existing skill before making a new one. Environment failures, transient
  errors, one-off narratives and unresolved failures are **never captured**.
  Unattended reviews remove an entry only on the person's own retraction.
- **Evidence classes** (self-learning.md "Evidence and trust"): feedback
  requires a direct user source; a procedure requires *successful* tool
  evidence; a failure lesson requires *failed* tool evidence. "Assistant
  prose is not proof." Lesson writes reject recognizable instruction
  injection and invisible formatting characters; credentials are excluded
  from learning input and persistence. Learned text is advisory and never
  grants authority or activates draft skills.
- **Effect on later turns:** accepted lessons are retrieved for applicable
  later turns and their IDs recorded against the turn receipt; explicit user
  corrections retire an obsolete lesson; explicit negative evaluations
  suspend reuse; mere retrieval or turn completion does **not** increase a
  lesson's measured benefit (benefit/harm is user-attributed only).
- **Skill lifecycle** (`dodaios/society/skill_lifecycle.py`): private skills
  carry usage records; unused 14 days → `stale`, 30 days → `archived` (out
  of the briefing, never deleted, reactivated by use or rewrite). The
  briefing lists at most 12 skills, most recently used first — prompt size
  does not grow with agent age.
- **Seat discipline for learning itself:** reviews run only on the agent's
  own seat, at most four seat calls total with jittered backoff, then the
  review is `dropped` with the reason stored on the row. Per-owner FIFO
  order means an old retry can never undo a newer correction; idempotent
  receipts prevent duplicate learning.

*Caveat:* the self-learning doc is layered. Its 2026-09-30 status note says
the experience-journal implementation it describes (`experience.py`,
`learning/journal.json` as authoritative store) was removed by a history
reconciliation merge, and agents learn through the notebooks + turn review;
the 2026-10-05 "Learning loop v2" section is the newest statement and is
what's summarized above. PR #185 (the private learning-loops PR from web
search) matches the journal design; a depth-1 clone has no history, so
whether it merged as-is could not be verified here *(limits, §6)*.

Jarvis' own memory is a separate, wider stack (`docs/agent-society/memory-house.md`
§1): core memory (~1.5 k tokens, always in prompt, `dodaios/memory/core_memory.py`),
conversation recall (FTS5/BM25 over `data/jarvis.db`, `recall.py`), an
Obsidian-vault wiki with a two-stage LLM curator and a gated per-turn context
injector (`dodaios/memory/wiki/*`), and skills.

## 2c. Background persistence — life after the window closes

Two cooperating mechanisms (`docs/background-service.md`, `docs/routines.md`):

1. **The background service.** Closing the desktop app with something to
   keep (an armed routine, a running Telegram/Discord channel) hands off to
   the *same headless backend* run windowless:
   `python -m dodaios.ui.web.launcher --background-service` (implies
   `--headless`, `JARVIS_VOICE=0`). Desktop and service are mutually
   exclusive via a single-instance lock (`data/jarvis.lock`). Hand-off: the
   desktop spawns the service detached with `--after-pid <pid>`; the service
   waits for the desktop to exit, takes the lock *without evicting anyone*
   before binding its port, and writes a marker file. Hand-back: a desktop
   launch drops a `handover.request` file; the service polls it every 0.5 s,
   lets in-flight routine runs finish (≤30 s), shuts down, releases the
   lock. A service that won't let go in 45 s is stopped. **Self-exit:** every
   5 minutes it checks it still has work; two empty checks and it exits —
   "an invisible process holding the lock is the one thing it must never
   become." Optional login start ("start only the background agents").
   Spoken routines fail in the service (no voice without the app); agent
   routines and chat channels are unaffected.
2. **Routines on a durable task store.** Routines live in `dodaios/tasks`
   (store + scheduler) and execute through the owning agent's canonical
   chat with its current instructions and permissions; creating one never
   grants permissions (`docs/routines.md`). Society routines are Automations
   tasks tagged per agent (`dodaios/society/routines.py`), including
   agent-proposed autonomous follow-ups, which pass a provenance/cancellation
   boundary (`dodaios/society/autonomous_routines.py`). The society
   `scheduler.py` is the *only* path from an `ASSIGN` envelope to work:
   kill switch, tier wall, delegation depth ≤ 2, budgets and caps; refusals
   are `VETO` envelopes, and no spawn tool ever enters an agent's tool set.
   A billing rule rides on top (`dodaios/brain/background_policy.py`): once
   a subscription is connected, unattended runs never bill a per-token API
   key — they are recorded as **Skipped** with a plain-language reason
   rather than silently rerouted.

## 2d. Stack, briefly

- **Shape:** Python ≥3.11 monolith (`dodaios/`, ~85 subpackages) + React/
  TypeScript frontend (`dodaios/ui/web/frontend/`) + two side packages:
  `conductor/` (YAML-first workflow canvas, own SQLite) and `board-backend/`
  (standalone FastAPI stub). SQLite stores throughout (`jarvis.db`,
  `society.db`, board `personal.db`); markdown (Obsidian vault) is the
  memory store of truth, DB indexes derived and rebuildable.
- **Layer model** (architecture-overview.md): L0 OS/hardware → L1 audio →
  L2 speech (wake word, Silero VAD, faster-whisper STT, TTS) → L3
  intent/risk (classifier, risk tiers, approvals, rate limits) → L4 brain
  (5 providers + sub-second "ack brain") → L5 harness (computer use) → L6
  orchestrator (router, BrainManager, mission manager) → L7 UI. Layers
  communicate down only via protocols (`dodaios/core/protocols.py`) and
  laterally only via typed frozen-dataclass events on an EventBus
  (`dodaios/core/bus.py`, `events.py`), each with `trace_id`; a broken
  subscriber is logged, never propagated.
- **Assistant core:** supervisor pattern — heavy work goes
  `spawn-worker` → Mission Manager → worker → Critic → Kontrollierer
  sign-off (`dodaios/missions/`), with budgets, recovery and isolation.
- **Plugins:** entry-point registration (`pyproject.toml`
  `[project.entry-points."dodaios.<group>"]`, groups frozen as wakeword /
  stt / tts / brain / harness / tool / channel); plugins must not import
  `dodaios.*` — structural protocol compatibility only
  (`dodaios/core/registry.py`). On top of that a **marketplace** layer
  (`dodaios/marketplace/`): catalog, OAuth, an MCP bridge that expands
  connected plugins into live brain tools, relevance ranking; 40+
  integrations. MCP servers also plug in directly (`mcps/`, `mcp.json.example`).
- **Secrets:** OS credential store only, via one accessor; never in code,
  config or commits (architecture-overview.md "Secrets").

## 3. Comparison with Calibos's own designs

| Question | Dodaios | Calibos (home sim / mind) |
|---|---|---|
| Where does the inhabitant "live"? | A rendered 3D world (island → Mars → Verse ship); the agent's place is *derived* from backend facts by trusted rules (`checkpoints.py`) | A pure-Python cottage sim (`home_sim/`): 4 rooms (study, kitchen, garden, rest) with affordances; the inhabitant's room is real sim state chosen by a stochastic proposer (`home_sim/rooms.py`, `tick.py`) |
| Is the visible world the truth? | **No — projection only.** "Nothing an agent says moves it"; place follows the record | **Also no, from the other side.** `home_sim/traversal.py` exports a coarse `WorldSnapshot` (need *words*, never numbers; no counts, no bias) and imports world events only as decaying proposal *weights* — "the sim is never reconstructed from a snapshot" |
| What gates action? | Checkpoints derive from state; approvals gate outbound action (`gate` place = waiting at the Harbor Gate) | Room affordances gate ops (filthy kitchen blocks `prepare_meal`); light *weights* proposals but never writes a need value (`tick.py`) |
| Needs / maintenance loop | No simulated body; "maintenance" is memory consolidation, skill lifecycle, routine upkeep | The dwelling loop *is* needs: hunger/thirst/fatigue tick on lab-ported math; garden → harvest → store_produce → rations closes the loop; no-open-drains rule (every finite supply has an inhabitant-reachable restock) (`home_sim/rhythm.py`) |
| How does it persist when nobody is looking? | Process continuity: a detached headless twin holds a lock file, runs the same scheduler/society, hands back on relaunch, self-exits when work is done | Record continuity: OS-cron instances (wake ≈6 h, dream nightly) wake fresh, reconcile against files (`./mind wake` + affirm), heartbeat ticks, then exit; nothing resident between runs |
| Coordination record | Append-only typed board in SQLite, persist-before-publish, observers can't block appends | Append-only record in `mind.db` + consolidation journal: proposals land with written accept/reject reasons; archive is never silent deletion |
| Learning from correction | Review windows + evidence classes; lessons retrieved with receipts; corrections retire lessons; benefit measured only when user-attributed | Consolidation proposals + salience/decay/rehearsal substrate; dream fragments rehearse associations with zero LLM calls (`mind dream`) |
| Memory sharing | Private by default; sharing only via a *human* promote action into a reviewed shared tier; recall results carry trust labels | Single inhabitant; the analogous boundary is the private/public split (mind.db/inbox never leave the machine; public backup carries architecture only) |
| Multi-agent discussion | Bounded typed rooms: 2–6 members, ≤3 rounds, ≤10 messages, silence allowed, deterministic policy | Not built — the Jay/Calibos/Kiki shared room is still at the venue-decision stage (IRC recommended) |
| Identity across model swaps | Agent = roster row + files; the "seat" (provider/model/auth) is pinned per agent and per routine, changeable only deliberately | Model-independence thesis: cartridge + externalized record lead every view; the engine venv is a pinned, immutable snapshot and the model is a replaceable amplifier |

The deepest rhyme: **both systems refuse to let the presentation layer
write the truth.** Dodaios derives the figure's place from the record;
Calibos's traversal layer lets the outside world bias proposals but never
set a need, a room, or a resource. And both landed on the same learning
ethic from opposite directions — Dodaios: "assistant prose is not proof";
Calibos: never invent lived experience.

The deepest difference: Dodaios keeps the *process* alive and lets the
record be derived views (indexes rebuildable from the vault). Calibos
keeps the *record* alive and treats every process as disposable — a
wake instance is expected to die; the affirm record is how the next one
reconciles. Dodaios's idle agent costs zero tokens because it's a row;
Calibos's idle mind costs zero tokens because it doesn't exist between
crons. Same bill, opposite ontology.

## 4. Worth cannibalizing (mechanisms only)

1. **Checkpoint derivation for world-crossing.** Trusted, ordered,
   model-free rules mapping backend facts → the one visible place, with
   holds (memory touch = 60 s at the archive). This is exactly the missing
   piece between the home sim's traversal snapshots and a Village/lab
   presence: derive what the MUD shows from sim state, never let the MUD
   write it. — `dodaios/society/checkpoints.py`,
   `docs/agent-society/world-behaviour-manual.md` §3, `memory-house.md` §3.4.
2. **Review-window pacing for learning.** Consolidation triggered by a
   signal, a turn count, a tool-step count, or window age — *no timer, an
   idle agent costs nothing*. A natural fit for the consolidation journal:
   scan on wake when a window fills, not on a fixed cadence alone. —
   `dodaios/society/review_cadence.py`, `self-learning.md` (v2).
3. **Evidence classes for lessons.** Feedback needs a direct user source;
   procedures need successful tool evidence; failures need failed tool
   evidence; own prose is not proof. Directly importable as acceptance
   rules for consolidation proposals. — `docs/agent-society/self-learning.md`
   ("Evidence and trust"), `dodaios/memory/learning/guard.py`.
4. **Bounded typed rooms.** 2–6 members, ≤3 rounds, ≤10 messages, silence
   allowed, deterministic policy, restart-safe — a ready-made constitution
   for the Jay/Calibos/Kiki shared room ("The Grand Crucible of Ideas")
   that caps cost by construction. — `dodaios/society/rooms.py`,
   MASTERPLAN §2.2.
5. **Promotion-gated sharing.** Own scope: free write. Shared scope: only
   via a proposal + a named human's promote; dismissal is also a recorded
   act. The right shape for anything the home/Village ever shares between
   inhabitants. — `dodaios/society/memory.py` (`propose_shared`, `promote`,
   `dismiss`), `memory-house.md` §3.2.
6. **Trust-labelled, scope-ranked recall.** Retrieval ranked
   own > shared > user > others' unreviewed, with the label printed on the
   result line, and unreviewed material never ambient. Cheap, honest, and
   it travels with the fact. — `dodaios/society/memory.py` (`recall`,
   `head`), `memory-house.md` §3.3.
7. **Skill lifecycle with staleness.** Usage-stamped skills go stale (14 d)
   then archived (30 d) — never deleted, reactivated by use; briefing caps
   the list so prompts don't grow with age. Applies to the mind's habits
   and to any future skill sidecars. — `dodaios/society/skill_lifecycle.py`.
8. **Notebook hard limits.** A memory book capped at 2× its prompt budget;
   past the cap, consolidation is forced before anything new is added —
   pressure with a receipt, not silent truncation. —
   `dodaios/society/memory_books.py` (`HARD_LIMITS`).
9. **Secret guard on memory writes.** `remember`/`note` bodies matching
   secret patterns are refused at the memory service, not by vigilance. —
   `docs/agent-society/memory-house.md` §3.6, `dodaios/society/memory.py`.
10. **The hand-off pattern for background life.** Single-instance lock +
    handover-request *file* (works mid-boot, needs no credentials) +
    in-flight work drains before hand-back + self-exit after two empty
    checks. If the mind ever moves from cron to a resident service, this
    is the proven shape. — `docs/background-service.md`,
    `dodaios/core/background_service.py` *(path from doc; not opened)*.
11. **Seat pinning as honesty.** A routine keeps its model seat; a failed
    seat fails the run with a readable reason instead of silently billing
    a different credential. The same discipline as the frozen prompt
    contract, generalized. — `docs/routines.md` ("Model seat").

## 5. Leave alone

- **The 3D world renderer and asset pipeline** (Blender world kits, GLB
  figures, React `WorldStage`, Mars/Verse definitions). The steal is the
  checkpoint *derivation*, not the renderer; a text-first inhabitant gains
  nothing from a voxel skin and would inherit a huge, fast-moving surface.
- **The voice stack** (wake word, VAD/STT/TTS, telephony, realtime
  providers). Solves presence problems Calibos doesn't have; enormous
  dependency and permission surface.
- **The Agentic IDE / workspace orchestration** (pane grids, CLI-seat
  steering, delivery receipts for coding agents). Built to supervise
  external coding CLIs; the builder/critic loop already covers Calibos's
  build needs at a fraction of the machinery.
- **The missions worker–critic–Kontrollierer harness.** A heavier,
  productized cousin of the existing builder/critic loop; importing it
  would duplicate, not extend.
- **The marketplace/OAuth plugin ecosystem and the subscription-vs-API-key
  billing policy.** Consumer-product surface: catalog, OAuth callbacks,
  relevance ranking, billing rules for users who mix subscriptions and
  keys. Calibos's tool access is curated by hand; there's no billing
  ambiguity to police.
- **The Obsidian-wiki curator as a memory regime** (two-stage LLM curator,
  FTS index, per-turn injector). A second memory regime would fight the
  mind's engine-native salience/decay design and the frozen protocol.
  Borrow its *provenance labels* (§4.6), not its store.
- **Self-mod config writing** (`dodaios/core/self_mod/`). Calibos already
  has a narrower, safer version: cartridge changes with a store migration.
  A general mutable-settings surface is broader than the need.

## 6. Honest limits of this survey

- **Shallow clone, one day.** `git clone --depth 1` on 2026-10-23: no git
  history, no branches, no PR diffs. PR #185 was **not** read; learning-loop
  claims rest on `docs/agent-society/self-learning.md` and
  `docs/dodaios-learning.md`'s descriptions, not on the code diff.
- **Docs-first, code spot-checked.** Full reads: `README.md` (head),
  `docs/igentic-workspaces.md`, `docs/agent-society/memory-house.md`,
  `self-learning.md`, `docs/background-service.md`, `dodaios/society/README.md`,
  heads of `MASTERPLAN.md`, `docs/routines.md`,
  `docs/architecture-overview.md`, `world-behaviour-manual.md`,
  `mars-runtime.md`, and the home-sim modules on our side. Implementations
  (`checkpoints.py`, `society/memory.py`, `review.py`, `scheduler.py`,
  `background_service.py`) were **not** read line-by-line; their behavior
  is as the docs and module tables describe, cross-checked against
  directory listings only.
- **The docs are layered and sometimes stale.** The Memory House doc's
  shared-memory design is self-marked as history (2026-10-05); the
  self-learning doc contains both a removed journal design (2026-09-30
  status note) and the current v2 loop (2026-10-05) — §2b follows the
  newest statements, but where doc layers conflict, code would be the
  arbiter and code wasn't fully read. The world itself has been re-skinned
  twice (island → Mars → Verse); place names cited are the island-era
  vocabulary, which the checkpoint contracts still use.
- **Nothing was run.** No install, no tests, no app launch; no claim here
  is about whether it works on Jay's laptop, only about how it's built.
- **Not read:** the frontend world implementation beyond confirming the
  files exist; `dodaios/memory/workspace.py` and `dodaios/workspace/*`
  beyond listings; the Verse docs beyond the Mars doc's pointer; Jarvis'
  own learning doc beyond its role in §2b.
- One identification is an inference throughout: that Jay's "virtual
  workspace" = the Society world (§2a.i–ii) rather than the IDE workspace
  (§2a.iii). The home-like properties he described (a place, inhabitants, a
  memory building) exist only in the former.
