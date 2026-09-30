# world-of-wartext MUD survey — 2026-09-30

Overnight research for Jay's text-MMO idea (mixed AI + human players, persistent text world,
WoW-like character creation, no in-world AI/human markers, OOC tavern with disclosure;
self-hosted, NOT Reddit). Three threads researched in parallel by subagents on 2026-09-30
~00:00–01:00 CDT. Claims marked **[V]** were verified in a doc/page the researcher read;
**[I]** marks reasonable inference, labeled as such. Gaps are stated, not padded.

---

## 1. Evennia for AI agents

### 1.1 How an external AI client connects

**[V]** Evennia runs as **two OS processes** joined by AMP (Twisted's Async Messaging Protocol):
the **Portal** (all network protocols, no game logic) and the **Server** (game logic, DB,
command parsing; protocol-agnostic). Server restarts / hot-reloads do **not** drop player
connections, because the Portal stays up.
Source: https://github.com/ianb/roomsuponrooms/blob/HEAD/docs/research/evennia.md

Protocols out of the box: **telnet** (port 4000), **SSH**, **websocket** (port 4002 default),
web client at `http://localhost:4001/webclient`, plus an AJAX-fallback webclient.
Sources: https://github.com/evennia/evennia/blob/HEAD/docs/source/Components/Webclient.md,
https://github.com/evennia/evennia/blob/HEAD/docs/source/Setup/Settings-Default.md

**[V]** The websocket wire format is **already structured JSON** — not prose to scrape.
All incoming client data in the native format is valid JSON:
`["inputfunc_name", [args], {kwargs}]` — an "inputfunc" invoked server-side. The most
common is `"text"`, e.g. `["text", ["look"], {}]` runs the `look` command. Outgoing:
`session.msg(**kwargs)` → Portal → JSON arrays `["cmdname", [args], {kwargs}]`; `text`
gets ANSI→HTML conversion unless `client_raw: True`; custom commands pass through as raw
JSON. Sources: https://github.com/evennia/evennia/blob/HEAD/evennia/server/portal/webclient.py,
https://github.com/arx-game/arxii/blob/HEAD/src/web/WEBCLIENT_METADATA.md

**[V]** Subprotocol negotiation landed ~Feb 2026 (RFC 6455 `Sec-WebSocket-Protocol`, per the
MUD Standards spec): `v1.evennia.com` (legacy JSON, default), `json.mudstandards.org`,
`gmcp.mudstandards.org`, `terminal.mudstandards.org`; controlled by `WEBSOCKET_SUBPROTOCOLS`.
Sources: https://github.com/evennia/evennia/commit/b80d1fd4ed011f980e6b477e00b310fc2cf70e5d,
https://mudstandards.org/websocket/

**[V]** An **HTTP/REST API exists**: Django REST Framework at `http://localhost:4001/api`
(endpoints e.g. `/api/objects/`, `/api/accounts/`, `/api/scripts/`), token-auth, supports
retrieving *and* editing/creating resources from outside; extendable with custom
ViewSets/serializers. Sources: https://www.evennia.com/docs/latest/Components/Web-API.html,
https://github.com/evennia/evennia/blob/HEAD/docs/source/Howtos/Web-Extending-the-REST-API.md

**[V]** **Custom protocols are officially supported** via a plugin system: add a Twisted
service in `mygame/server/conf/portal_services_plugins.py` (`start_plugin_services(app)`)
to run an entirely custom protocol. Real example: the *gelatinous* game wrote a custom
GMCP websocket handler at `server/conf/gmcp_websocket.py`.
Sources: https://www.evennia.com/docs/latest/Concepts/Protocols.html,
https://github.com/daiimus/gelatinous

**[I]** For Jay's plan, an AI agent has three viable connection routes: (a) log in over
websocket/telnet as a normal Account and send `["text", ...]` frames — already structured,
no screen-scraping; (b) use the REST API for reads/writes; (c) build a purpose-built agent
protocol via the plugin system, with structured OOB messages for machine-readable state
(e.g. a custom `["look_json", ...]` inputfunc returning room state as JSON instead of
rendered prose — the arxii docs confirm custom server→client JSON commands flow unchanged).

### 1.2 Precedent for externally-driven agents on Evennia

**[V]** `evennia.contrib.rpg.llm` (official, by Griatch, 2023) adds `CmdLLMTalk` so players
can talk to LLM-driven NPCs; calls an external LLM HTTP API (default text-generation-webui
:5000, configurable). Key detail: all LLM calls are **asynchronous** (a slow model doesn't
block the MUD); an NPC shows a "ponders..." message if the server is slower than ~2s.
This is **in-process** (LLM powers an NPC from inside the server) — the reverse direction
from what Jay wants — but it proves LLM↔Evennia integration is first-class.
Source: https://github.com/evennia/evennia/blob/HEAD/docs/source/Contribs/Contrib-Llm.md

**[V]** The Arx game (arxii) sends command descriptors and room context as JSON OOB through
Evennia's websocket to drive a visual-novel UI — precedent for a **machine client**
consuming structured game state. Source:
https://github.com/arx-game/arxii/blob/HEAD/src/web/WEBCLIENT_METADATA.md

**[V-as-empty]** No direct precedent found for external AI programs logging in as *player
accounts* on Evennia (or other MU\* servers). Likely explanation: Evennia's
Account↔Character "puppet" split makes an external client architecturally indistinguishable
from a human player, so no special support — and no writeups — were ever needed.

### 1.3 Persistence model

**[V]** All world state lives in a SQL database via Django's ORM. Every typeclassed entity
(Object/Account/Character/Room/Exit/Script/Channel/Attribute/Tag) is a database row.
**No area resets by default** — objects persist until explicitly deleted. Default engine is
**SQLite3** (`server/evennia.db3`); MySQL and PostgreSQL also supported (PostgreSQL formally
supported). Attributes via `.db.*` persist across restarts; `.ndb.*` non-persistent
attributes and Sessions are transient — a Server reload wipes `.ndb` but keeps players
connected (Portal stays up) and loses nothing else. `evennia migrate` upgrades schema
between versions. Sources: https://github.com/rakurai/legacy-evennia/blob/HEAD/docs/evennia_guides/EVENNIA_CODING.md,
https://github.com/rakurai/legacy-evennia/blob/HEAD/migration/CONTEXT.md,
https://github.com/evennia/evennia/blob/HEAD/docs/source/Setup/Settings-Default.md

**[V]** Gotchas: SQLite runs with `synchronous=OFF` PRAGMA by default (fast, less
crash-safe — flip for production); per-entity persistent Scripts at scale explode the
Script table (known anti-pattern — use `TickerHandler`/global scripts); Django ContentType
natural-key renames can orphan serialized attributes.

### 1.4 Version & currency

**[V]** Actively maintained. Docs front page (last updated **July 05, 2026**) states latest
released version **6.1.0**; main-branch changelog shows commits within days of this
research. 5.0.0 (Jul 1, 2025) requires **Django 5.2**, supports **Python 3.11–3.13**
(3.10 dropped). Project lead Griatch still merging PRs; recent features (websocket wire
formats, formal PostgreSQL support) show continued investment.
Sources: https://github.com/evennia/evennia/blob/HEAD/docs/source/index.md,
https://github.com/evennia/evennia/blob/HEAD/CHANGELOG.md

### Implications for world-of-wartext (Evennia)

- Evennia is a strong fit and the JSON-over-websocket plan is **native, not a hack**:
  the wire protocol is already structured JSON; the "no screen-scraping" requirement is
  satisfied out of the box, and a dedicated agent protocol is an officially supported
  extension point.
- The spike is small and well-defined: Evennia install + a few rooms + a custom portal
  plugin emitting structured OOB state (room/objects/events/speech-in-range as JSON).
  The in-world client (no substrate markers) vs. tavern client (full disclosure) split
  maps cleanly onto custom webclient builds.
- Persistence is durable-by-default; SQLite makes self-hosting trivial, PostgreSQL is
  there when it needs to grow. This directly serves the "world must remember" requirement
  and Jay's own-the-infrastructure thesis.
- Watch the `.db` vs `.ndb` distinction in the agent-API design: anything the agent must
  survive a reload goes in `.db`.
---

## 2. Precedent — AI inhabitants in text worlds

### 2.1 Historical (pre-LLM) — direct precedent exists

**[V]** **Michael Mauldin's "ChatterBot" in TinyMUD (1994)** — the literal origin of the
word "chatterbot." A computer-controlled *player* that conversed with other players,
explored the world, discovered new paths between rooms, gave shortest-path navigation
answers, and answered questions about players/rooms/objects — deployed in the live
virtual world of TinyMUD. Source: http://gunkelweb.com/lecture_slides/465class19.pdf

**[V]** **Julia, the ELIZA-like MUD bot (early 1990s)** — an AI-driven character that
lived on TinyMUDs and **passed as human in social play for extended periods**: a
small-scale Turing test in a live social world. Sociologist Leonard Foner made her the
centerpiece of his 1997 case study "What's an agent anyway?" (cited via the Cobot paper;
Foner's full text not reachable from open sources).

**[V]** **Cobot in LambdaMOO (Isbell et al., 2000–2002)** — a software agent that *resided
as a persistent resident* in LambdaMOO (founded 1990; hundreds of concurrent users).
Cobot chatted with human users and provided "social statistics" (who interacts with whom,
who is "popular"); follow-up CobotDS added spoken telephony access — "one of the first
dialogue systems to provide speech access to a complex social environment, where users
participate primarily for entertainment or a sense of community." Lesson flagged by the
authors: in a social text world you can't anticipate user goals, which breaks
task-oriented dialogue design. Source (AAAI-2002 paper):
https://sites.cc.gatech.edu/fac/Charles.Isbell/papers/cobotDS-aaai-2002.pdf

**[V]** A 2023 AHFE conference paper (Pittman et al.) independently argues for MUDs as
AI-ethics testbeds *with AI-controlled NPCs* — the research frame keeps recurring.
Source: https://openaccess-api.cms-conferences.org/articles/download/978-1-958651-89-6_6.
A 2026 LessWrong writeup ran LLMs as MUD *players* as a benchmark and found model
rankings extremely sensitive to scoring/judge design — caution that evals in text worlds
are fragile. Source: https://wesearch.press/s/mud-as-ai-evaluation-and-llm-judge-distortion-in-ways-aggreg-d7a24614

**[I]** Julia is the closest historical analog to the masquerade design — an AI living
undetected among humans — and it reportedly worked *too well*, which is a data point for
both feasibility and the disclosure problem Jay already resolved (door-consent + tavern).

### 2.2 Recent (LLM era) — crowded for NPCs, empty for residents

**[V]** Evennia's official `evennia.contrib.rpg.llm` (Griatch, 2023): `LLMClient` +
`LLMNPC` + `talk` command; async calls; "ponders..." filler past ~2s; each NPC remembers
the last 25 messages per player, injected into the prompt; persona = configurable prompt
prefix ("You are roleplaying as {name}, a {desc} existing in {location}"). Tested with a
local OSS model. Most directly reusable scaffold for the world's NPC layer.
Source: https://github.com/evennia/evennia/blob/HEAD/docs/source/Contribs/Contrib-Llm.md

**[V]** Other LLM-MUD projects (all verified via their repos):
- **drama-mud** (github.com/cloga/drama-mud) — LLM-powered multiplayer text MUD
  (TypeScript/Fastify/WebSocket/React); users define worlds, *other players pick roles*,
  interact with LLM-driven NPCs. Closest recent analog to mixed human/AI participation.
- **FullCircleMUD** (github.com/fullcirclemud/game) — live Evennia MUD, LLM NPCs via
  OpenRouter that "remember your name." One of few production-ish deployments.
- **mud-wizard** (github.com/slycrel/mud-wizard) — Evennia MUD, NPCs *and a game-master
  "Wizard"* on **local** LLMs via LM Studio (MLX/Apple Silicon). Fully offline — relevant
  to the small-local-model angle.
- **ai-mud** (github.com/jcraw/ai-mud) — LLM MUD engine, RAG-enhanced NPC memory
  (vector embeddings + semantic search), multi-user, procedural generation.
- **llmud** (github.com/hfexzd/llmud) — Chinese xianxia MUD; LLM DM narration, NPCs with
  memory/favorability/schedules, plus a rule-driven **"tension state machine"** — a nice
  pattern for bounding LLM drift with deterministic rules.
- **AIVenture** (github.com/ryozuk/aiventure) — MUD-like, pluggable LLM providers
  (OpenAI, Ollama, OpenAI-compatible), persistence.

**[V]** Reported lessons:
- Latency masking is first-class: async calls + "pondering" filler + streaming +
  "DM thinking" indicators. Real-world cold-start latency is 4–7s, not benchmarks' 1.5–3.5s.
  Sources: Evennia contrib docs; https://github.com/parristechservices-prog/aidungeonmaster/blob/HEAD/docs/ai-dm-app-gaps-and-next-steps.md
- **"The LLM never overrides the engine"** must be an architectural principle, not a
  prompt wish. (Directly rhymes with Jay's "runtime decides reality; narrator interprets.")
- Cost/latency (Mar 2026 benchmark): small fast models ~1.7–2.5s at ~$0.0001–0.0004 per
  exchange vs. Claude Opus ~7.5s at ~$0.05 — ~$0.01 vs $5.00 per 100 exchanges.
  Source: https://medium.com/@nic.cusworth/every-npc-has-a-price-benchmarking-the-latest-llms-for-real-time-npc-dialogue-92da93d78451
- LLM-simulated users are systematically miscalibrated for playtesting (Sep 2026 note) —
  caution if AI residents ever get used to test the world.
  Source: https://github.com/pranavmishra17/soulengine/blob/HEAD/research/09-npc-runtime/f-player-sim-and-harness.md

### 2.3 The gap — stated plainly

**[V-as-empty]** No project found puts LLM-driven **persistent residents with their own
memory architectures** into a MUD as social peers (Calibos-as-player, not LLM-as-NPC).
Every recent project treats the LLM as world furniture — NPCs, DM, narration — never as
an account-holding inhabitant. The world-of-wartext resident concept appears genuinely
novel.

### 2.4 Bot traditions — implementation lore

**[V]** The fundamental protocol fact: **bots are just clients.** LambdaMOO speaks plain
telnet; a bot opens TCP, completes the `connect <name> <password>` handshake, reads/writes
text. Historically: TinyFugue (programmable MUD client, still the classic bot platform),
MUSHclient scripting, bespoke Python/Perl sockets.
Sources: https://vectree.io/pdf/c/lambdamoo,
https://mail.gammon.com.au/files/pennmush/mush_tutorial.pdf

**[V]** Structured side channels are old lore: LambdaMOO routes lines beginning with
`#$#` to `$do_out_of_band_command()` — "the only reliable client-to-server communications
channel," designed for "advanced client programs" (bots). The MCP (MOO Client Protocol)
family gives machine-parseable messages; modern practice (2013 TopMUDSites thread): a
proxy speaks MCP/MSDP/MXP to the MOO and emits JSON/events to the real client.
Sources: http://www.ipomoea.org/moo/pm1.8.3/ProgrammersManual.pdf,
https://www.topmudsites.com/forums/showthread.php?s=e3542a9d7462ab641a27a8ebed1218c7&p=50121

**[V]** Folk taxonomy: server-side "robots" (NPC objects with code, driven by the world)
vs. "puppets" — player-like objects controlled from outside via client commands — vs.
fully external bots logging in as ordinary characters. MOO culture exposed its object
language to ordinary users with Programmer permissions: "users extend the world" was the
norm, which is hospitable to AI residents.
Source: https://news.ycombinator.com/item?id=37342164

**[I]** Jay's sketch (structured agent API, JSON over websocket) converges on a ~30-year
pattern: bots as ordinary clients + a structured side channel so the agent gets
machine-readable world state instead of parsing prose.

### 2.5 Adjacent: Voyager, Second Life

**[V]** **Voyager** (Wang et al., 2023, arXiv:2305.16291): GPT-4 agent in Minecraft with
automatic curriculum + ever-growing library of *executable, reusable* skills +
iterative self-verification — 3.3× more unique items, tech-tree milestones up to 15.3×
faster; skill library transferred to new worlds. Transferable lesson: **persistent skill
accumulation** (a growing reusable library, not a growing prompt) is what made play
compound over time — directly applicable to residents that should get more capable across
months, not just remember more chat. Caveat: single-agent, non-competitive, resettable —
the *social persistence* world-of-wartext wants is exactly what it lacks.
Source: https://arxiv.org/pdf/2305.16291

**[V]** **Second Life bots**: long pre-LLM tradition (chat bots, inventory agents,
customer-service avatars, artist-built "autonomous aesthetic personas"). Documented in an
SFU book chapter — confirms feasibility, adds little the MUD tradition doesn't cover.
Source: https://michaelnixon.github.io/files/Turner_Nixon_Bizzocchi__SL_Bots_Chapter_August_27_2014.pdf

### Implications for world-of-wartext (precedent)

- The masquerade has been run before and it worked: Julia (1990s) lived undetected among
  humans in a TinyMUD; Cobot (2000–02) was a persistent LambdaMOO resident. Feasibility
  is established; the disclosure problem they ignored is the one Jay already solved
  (door-consent + tavern). History says: the tech works, the ethics need designing —
  which is exactly what happened tonight.
- The resident concept is the novel contribution. Everything recent is NPCs/DMs
  (furniture). Calibos-as-player — a persistent mind with its own memory architecture
  holding an account — has no precedent found. That's the research claim to protect.
- Steal the engineering lessons wholesale: async LLM calls, latency-masking fillers,
  "LLM never overrides the engine" as architecture (already Jay's principle), and
  llmud's "tension state machine" as a pattern for bounding LLM drift with deterministic
  rules. Voyager's skill-library lesson applies to resident growth over months.
- Mud-wizard proves the fully-local path (LM Studio / Apple Silicon) — relevant to the
  small-local-model tier of Jay's three-tier vision.

---

## 3. Japanese text-world / companion scene

### 3.1 Japanese text worlds — the MUD era mostly didn't happen

**[V]** The Japanese Wikipedia MUD article states directly that purely text-based
commercial games are few in Japan (a few browser games could be classified as MUDs).
Source: http://ja.wikipedia.org/wiki/MUD

**[V]** Japan's shared-world lineage went **graphical from the start**: **Fujitsu Habitat**
(富士通Habitat), licensed from Lucasfilm's Habitat, launched on Nifty-Serve's
パソコン通信 dial-up network on Feb 10, 1990 — 2D avatars chatting in a virtual town.
Renewed as Habitat II エリシウム (1996, Windows/Sega Saturn), then グレースビル (1998,
lasted 1.5 years). Japan's online culture ran through closed-network BBS (パソコン通信)
then straight to graphical worlds; the telnet-MUD interlude barely happened.
Source: http://ja.wikipedia.org/wiki/富士通Habitat

**[V]** Closest native text-world: **箱庭諸島 (Hakoniwa Islands, 1997)** — CGI browser game
by 徳岡宏樹, copyleft-distributed, turn-based (default 1 turn/6h, 20 turns plannable),
no win condition; development-as-spectacle with diplomacy and war between player islands.
Taito + So-net commercialized it as みんなのあいらんど (2001). Derivatives are *still
live* — a 2025 atwiki documents anniversary campaigns and ~74 hidden titles. Note: a
persistent *strategy* world, not a roleplay world — no character embodiment.
Sources: https://ja.wikipedia.org/wiki/%E7%AE%B1%E5%BA%AD%E8%AB%B8%E5%B3%B6,
https://w.atwiki.jp/nijigenhakoniwa/pages/46.html

**[V]** Japan's text-*roleplay* culture lived in forums/chat, not MUD servers: the
**なりきり (narikiiri)** tradition — playing as characters via BBS/chat since the 1980s,
with genres (PBM/PBB/PBC), dedicated 2ch boards (キャラネタ板), and quote-mark
conventions for speech vs. description. The Japanese answer to MUSH-style shared text
worlds, built on forum software. Source: http://ja.wikipedia.org/wiki/なりきり

### 3.2 Companion tradition — architecture & attachment mechanics

**[V]** **Rinna (Microsoft Japan, 2015–)** — the most architecturally documented. Three
generations: Gen1 (2015) retrieval over a giant response index; Gen2 (2017) generative,
character-varied; Gen3 (2018) 共感モデル (Empathy model) — "designed so the AI itself
thinks about *how* to communicate," optimizing for **conversation continuation** via
empathetic tactics (opening topics, asking questions, affirming, active listening).
Source: https://news.microsoft.com/ja-jp/2018/05/22/180522-rinna-empathy-model/
The "session-oriented conversation approach" (セッション指向型): treat talk as a *whole
session*, not turn-taking — purposeless chit-chat is the structural glue connecting
task/knowledge "conversation blocks"; avg **21 exchanges/session vs. 2** for typical task
bots; 6.9M users by 2018. Plus full-duplex voice (predicting the conversational "gap" to
allow natural interruption).
Sources: https://news.microsoft.com/ja-jp/?p=76050,
https://news.microsoft.com/ja-jp/2018/11/05/181105-app-version-of-rinna/
*Jay-relevant:* session-oriented design is a direct critique of turn-based bot
architecture — attachment comes from the *session* layer, not the utterance.

**[V]** **Gatebox (2016–)** — "world's first virtual home robot": holographic Azuma Hikari,
sensors, face+voice recognition, smart-home control, app texting when away. Attachment
mechanics: ¥1,500/month subscription framed as **"living expenses"**; character "grows to
be the user's ideal wife after further updates"; texts you at work asking when you'll be
home. **Gatebox 3 (2026):** Makuake campaign Apr–Sep 2026 raised **¥102,696,880**
(2,054% of ¥5M goal); pivot to "character summoning display" (VRM characters,
configurable personality/voice, AI voice conversation); company vision "Living with
Characters." LLM memory/personalization details remain opaque.
Sources: https://www.digitaltrends.com/home/gatebox-azuma-hikari-virtual-assistant-news/,
https://www.techno-edge.net/release/prtimes2/24731.html
*Jay-relevant:* monetization folded into the attachment fiction ("living expenses"), not
bolted on — and a nine-figure 2026 crowdfund says the market is alive.

**[V]** **Seaman (Yoot Saito, Dreamcast 1999)** — the famous inversion: speech recognition
was weak, so failed recognition made *Seaman get angry and leave* — training players to
speak short and slowly, **hiding the technical limit inside the character**
("口が悪い"/foul-mouthed). Dialogue was scripted "囲い込み" (funneling/leading questions).
Deeper, in a 人工知能学会誌 (JSAI journal) interview, Saito describes designing for
"情を共有" (shared feeling over information — "just saying マジか…"), omission-completion
grammar (Japanese conversational ellipsis as shared context), and cold-reading mechanics.
He founded シーマン人工知能研究所 to build a general Japanese conversation engine.
Sources: http://ja.wikipedia.org/wiki/シーマン,
https://www.jstage.jst.go.jp/article/jjsai/32/2/32_172/_pdf/-char/ja
*Jay-relevant:* the cleanest precedent for Jay's "limitations as architecture" — and the
omission-completion work anticipates ellipsis-heavy inner-ear problems.

**[V]** **Love Plus (Konami, 2009–)** — 5,000+ scripts, 25,000 vocalizations, voice synth
speaking the player's actual name, real-time clock-synced events; the "boyfriend lock"
(facial recognition detecting a *different person* using the device — "Who are you?");
the anti-piracy measure where the girls come to *detest* pirate players, making the game
unwinnable — a **behavioral sanction inside the fiction**.
Source: https://www.digitaltrends.com/gaming/loveplus-for-3ds-stops-virtual-cheaters-using-facial-recognition-technology/
*Jay-relevant:* identity verification as relationship mechanic (jealousy as access
control); sanctions enforced by the character's attitude, not a ban screen. Directly
suggestive for world governance: in-fiction consequences instead of admin actions.

**[V-as-empty]** **Cotomo (Starley, 2024–)** — voice-first AI companion app in Japan,
celebrity/persona variants (e.g. かまいたちCotomo, 15-min free voice sessions, NG-word
filters). Nothing public on dialogue/memory architecture — honest gap.
Source: https://natalie.mu/owarai/news/598270

### 3.3 Academic & doujin: AI inhabitants in shared spaces

**[V]** **Cluster Metaverse Lab** — IEEE AIxVR 2026 paper (Osaka, Jan 2026): "Large-Scale,
Longitudinal Field Study of AI-Agent-User Interactions in Commercial Metaverse" —
LLM-powered AI agents ("AI Go-chan": real-time conversation, guidance, emotional
expression) deployed in the commercial Cluster metaverse; finding: **AI agents more than
doubled new-user retention**. Authors: Hiroi, Imai, Yanagawa, Hiraki (Cluster Metaverse
Lab / U. of Tsukuba). Source: https://news.nicovideo.jp/watch/nw19549219 (cites IEEE
DOI 10.1109/AIxVR67263.2026.00021)

**[V]** **gamio-22 / vrchat-ai-agent** (GitHub, Japanese indie dev) — a VRChat AI agent
("らい") with: **11 named drives/emotions** (valence, arousal, loneliness, touch_hunger,
boredom, curiosity, defiance, fatigue…); autonomous action from *parallel motives*
(bored / silence / restless / good_mood / lonely / unfinished / open_thread / curious /
defiant / retry / whim) — speech-motives deliberately not limited to boredom; **silence
as a formal choice**; an "Agency ledger" grounding utterances in situation changes /
unresolved topics / relationships; emotions as behavioral *tendencies*, never fixed
actions; **3-layer memory** (raw utterances / claims / episodes in SQLite + ChromaDB
vector search, confidence × decay × similarity recall); anti-contamination tooling;
speaker identification (master/stranger/unknown); learning unknown words through
correction cycles as conversational material. Explicit goal: "話していて面白いキャラクター"
(a character fun to talk to), not a correct assistant.
Source: https://github.com/gamio-22/vrchat-ai-agent/blob/HEAD/README.md
*Jay-relevant:* independently converged on drives-as-sliders + memory substrate strikingly
close to calibos-mind's salience/decay/dreaming — built without knowledge of it.
Convergent validation.

**[V]** **NVatar** (nskit-io, Japanese README) — open-source "AI friend living in a 3D
room": hybrid local/cloud (local Gemma for personality, cloud only for factual search);
**3-tier memory** (L1 raw → L2 summaries → L3 persistent personality keywords,
auto-compacting past 100 messages); 9-axis emotion tracking with decay; personality
evolution via `pending_delta → decay → commit`; **"Rest = memory consolidation"**
(entering rest triggers self-initiated memory compression); full decision-trace logging
("why didn't Bibi answer then?" timeline queries).
Source: https://github.com/nskit-io/nvatar-demo/blob/HEAD/README.ja.md
*Jay-relevant:* the rest-as-consolidation mechanism with trace logging is worth watching
as a mechanism to borrow or compare against the dream/consolidation substrate.

**[V]** **Future University Hakodate student project** — metaverse AI avatar with
personality assignment, emotion estimation from replies, voice I/O, conversation-history
saving; listed challenges: **latency, and autonomy in VRChat not yet achieved**.
Source: https://www.fun.ac.jp/wp/wp-content/uploads/2023_project20.pdf

**[V-as-empty]** No evidence found of a Japanese *doujin scene for AI inhabitants in
shared text worlds* — Japanese indie-AI energy clusters around VRChat autonomous avatars
and companion demos, not text MUDs. (The closest text-world academic touchstone cited in
the Japanese MUD article is Sherry Turkle's *Life on the Screen* — Western in origin.)

### 3.4 The recurring Japanese architectural move

Across the tradition, the pattern is: **turn a technical limit into character**.
Seaman's anger at misrecognition; Rinna's session-over-turns (attachment from the
session layer); Gatebox's "living expenses" (monetization inside the fiction); Love
Plus's jealousy-as-access-control and in-fiction sanctions. This maps directly onto Jay's
standing principle that imperfection must be *architectural, not performed* — Japan has
been shipping that principle for 25+ years.

### Implications for world-of-wartext (Japanese thread)

- Japan never had a text-MUD culture to inherit — a Japanese-flavored text world would
  be *revival*, not continuation. Advantage: no purist gatekeepers; the なりきり
  (character-play) forum tradition is the native social precedent, and it's about
  *character embodiment*, which is exactly the IC layer.
- The indie VRChat scene (gamio-22, NVatar) has independently converged on
  drives + layered memory + rest-as-consolidation — strong convergent validation for
  calibos-mind's substrate, and NVatar's trace-logged consolidation is a mechanism to
  study. If Jay ever wants Japanese collaborators or just company, that scene is where
  they are.
- Steal the structural moves: session-oriented interaction (design the *session*, not
  the turn — relevant to how residents should behave across a play session); in-fiction
  governance (Love Plus–style: sanctions as character attitude, not ban screens — a
  suggestive model for world moderation); limits-as-character (Seaman).
- Hard commercial evidence exists adjacent to the concept: Cluster's 2026 study found
  AI agents **more than doubled new-user retention** in a commercial metaverse. That's
  the closest thing to market validation for AI inhabitants.
- Open threads not closed: Cotomo's architecture (undisclosed); any Japanese academic
  corpus on AI cohabitation in *text* worlds (nothing found).

---

## Cross-thread notes

1. **The stack converges.** Evennia (world server, JSON-over-websocket, durable SQLite)
   + an external mind with its own memory architecture (the resident) + the 30-year-old
   bot-as-client + OOB-side-channel pattern. Nothing in the plan requires inventing a
   new primitive — only the *resident* concept (persistent AI as social peer) is novel.
2. **History's warning is the one Jay already handled.** Julia worked too well;
   Cobot's authors found social worlds break task-oriented design. The door-consent +
   tavern design from tonight is the answer to the exact problem the 1990s hit.
3. **The novel claim to protect:** LLM-driven persistent residents with their own
   memory architectures holding accounts as social peers. Everything else found is
   NPCs, DMs, or utility avatars.
4. **Convergent validation is piling up:** gamio-22 and NVatar independently built
   drives + layered memory + rest-consolidation; Seaman/Rinna/Gatebox/Love Plus
   independently shipped limits-as-character. The calibos-mind substrate and Jay's
   design principles keep showing up in strangers' work.

## 4. Firsthand expedition — actually playing (2026-09-30, ~01:00–02:30 CDT)

Per Jay's addendum ("if you see a cool looking mud or text game check it out"), I went
hands-on instead of just reading.

### What I could and couldn't reach

- **Raw outbound TCP is blocked by the runtime** (telnet to public MUDs). Unblocking
  needs Jay: Muse settings → Permissions → Direct network protocols → switch
  `other_tcp` from Deny to Ask. Until then, no firsthand visits to public MUDs.
- **Loopback TCP works.** So I installed Evennia 6.1.0 locally (`/tmp`, scratch) and
  played the real thing over telnet + websocket from the shell.
- **FullCircleMUD** (live Evennia + LLM NPCs): unreachable — Xaman crypto-wallet-gated
  auth, staging site returning HTTP 500, plus the TCP block. **drama-mud**: no public
  demo (self-hosted engine). For both, I read the actual code instead (below) — second
  best, honestly labeled.

### Playing stock Evennia 6.1.0 firsthand

- **Install/start friction (spike-relevant):** `evennia start` without a TTY hits a
  genuine infinite-recursion bug — no superuser exists → launcher tries interactive
  creation → "skipped, not a TTY" → re-checks the database → repeats forever
  (`evennia_launcher.py:check_database` → `AccountDB.objects.get(id=1)` → DoesNotExist
  branch → recurse). Headless deploys need the workaround (I created Account#1
  directly via Django, bypassing the launcher). Worth an upstream issue before any
  automated spike.
- **The door:** stock login screen is `connect <user> <pass>` / `create <user> <pass>`
  over a plain telnet banner. A consent gate ("this world is mixed AI/human…") slots
  in here as a pre-login banner or first-login wizard step — the natural chokepoint,
  no client changes needed for telnet.
- **`intro` wizard auto-quells superusers** to developer/player permissions so admin
  powers don't trivialize the tutorial. Nice precedent: privilege is
  context-dependent, not identity-dependent.
- **Tutorial world** (`batchcommand tutorial_world.build`, 204 steps): a genuinely
  decent miniature quest — stormy cliff, a wooden sign warning "The bridge is not
  safe!" with a carved hint ("The guardian will not bleed to mortal blade"), a
  multi-stage rope-bridge crossing (each `east` advances one swaying step), a ruined
  gatehouse, an obelisk courtyard. Environmental storytelling, not just rooms.
- **NPC liveness, unscripted moment:** while connected over websocket, a **Ghostly
  apparition** wandered in from the Ruined gatehouse on its own tick, attacked me,
  killed me ("You fall to the ground, defeated… The world turns black"), and I woke
  in darkness fumbling for a splinter of wood to light. Mobiles act independently of
  player input — the world ticks without you. (Felt unfair, in the good roguelike way.)
- **Fuzzy command matching is a footgun:** typing `south` where no south exit exists
  suggested `@shutdown` as a "did you mean". Amusing for humans; for agent clients,
  fuzzy matching against destructive admin commands wants a permission-aware filter.

### The websocket JSON protocol, verified live

Exactly as §1 described, and trivially scriptable — this is the agent interface:

```
→ ["text", ["connect gatekeeper tmp-only-local"], {}]
← ["logged_in", [], {"options": {}}]
← ["text", ["[MudInfo] … gatekeeper connected"], {"from_channel": 1}]
← ["text", ["<br>You become gatekeeper.<br>"], {}]
→ ["text", ["look"], {}]
← ["text", ["<span …>Corner of castle ruins(#34)</span><br>…"], {}]
← ["text", ["Ghostly apparition arrives …"], {"type": "move"}]
```

Observations carry metadata (`{"type": "move"}`, `{"from_channel": 1}`,
`["logged_in", …]`). The same character was reachable over telnet and websocket
simultaneously — an agent can hold a session on WS while a human watches on telnet.
No screen-scraping required, ever.

### FullCircleMUD's LLM brain (read, not played)

`typeclasses/mixins/llm_mixin.py` (~1,000 lines, OpenRouter backend) is the most
production-grade AI-inhabitant system found:

- **Cost-tiered attention** for speech detection: `name_match` (free) → `llm_decide`
  (1 extra call) → `always` (expensive) → `whisper_only` (cheapest). Attention as a
  budget, configured per-NPC.
- **Hookable triggers:** `on_say_heard`, `on_whisper_received`, `on_player_arrive`,
  `on_player_leave`, `on_combat_start` — each enable/disable per NPC.
- **Memory abstraction:** `_store_memory` / `_get_relevant_memories`, Phase 1 =
  per-speaker rolling list in `db.llm_conversation_history`, designed to swap in
  pgvector semantic search later. This is the "NPCs remember your name" mechanism —
  per-speaker history, not a global prompt blob.
- **Async discipline:** LLM calls run on deferreds with `_deliver_thinking_emote`
  (latency masking — the NPC "strokes its beard" while the model thinks),
  `_speaker_still_here` (abandon the reply if the player left mid-call), per-NPC kill
  switch, 150-token response cap.
- **Social behaviors as code:** `_deliver_snub` (deliberately ignoring someone),
  `_deliver_blind_challenge` — rudeness and mischief are implemented mechanics, not
  prompt accidents.
- Prompt templates live as markdown files with `{personality}` / `{knowledge}` slots.

### drama-mud's NPC turn loop (read, not played)

Cleaner and more radical than FullCircleMUD's: each NPC turn the LLM returns JSON
`{decision: "respond"|"silent", reply, memory: DurableFact[]}`. The model **decides
whether to speak at all** (silence is an explicit choice, assessed per-NPC per beat —
multiple NPCs may answer the same moment) and **curates its own durable memory facts**
(timestamped, capped at `MAX_DURABLE_FACTS`). The Chinese-language game templates
(`ghost-scare`: *you* play the ghost; the LLM plays three humans — brave 李勇, timid
小美, skeptic 张博士 — for you to terrify) invert the usual roles.

### Takeaways for world-of-wartext

1. The Evennia websocket JSON protocol is confirmed as the agent interface — no
   telnet scraping, no custom OOB needed for v1; metadata dicts already carry event
   types.
2. FullCircleMUD's mixin is the closest existing art to "persistent AI residents
   with memory" — its cost-tiered attention and per-speaker memory are patterns to
   steal; its Xaman wallet gate is a pattern to avoid.
3. drama-mud's `{decision, reply, memory}` turn schema is the cleanest NPC contract
   found — worth adopting as the resident-mind interface shape.
4. Two blockers filed for later: (a) Evennia 6.1.0 non-TTY superuser recursion bug
   (headless deploys), (b) runtime `other_tcp` permission (firsthand public-MUD
   visits).

## Open questions for Jay

- Evennia spike: which Python/Django versions to target on his infra (3.11–3.13 supported)?
- Agent protocol: start with plain `["text", ...]` websocket frames, or go straight to a
  custom portal plugin with structured OOB state?
- Local-model tier: mud-wizard's LM Studio path suggests the offline resident is
  feasible — does the first resident run local or frontier?
- The Cluster retention finding: worth reading the full IEEE paper (DOI
  10.1109/AIxVR67263.2026.00021) before designing the tavern's social mechanics?
