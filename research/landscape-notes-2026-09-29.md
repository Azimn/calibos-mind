# Landscape notes — 2026-09-29 (ChatGPT relay)

Standing rule accepted: flag intersecting papers/tools classified as
possibly-useful-now / post-freeze / comparison-only / irrelevant.
All six items verified real on 2026-09-29 (quick web check; abstracts match
the relay's descriptions).

## 1. Agent Zero Memory (post-freeze candidate)
Wu & Zhu, arXiv:2608.29606, 2026-08-30. Three parallel stores: episodic
Memory Events timeline, entity-event knowledge graph, citation-locked
Hierarchical Documentary Memory. Every learned item carries origin,
timestamp, evidence pointer; answers read under a citation lock — may cite
only evidence the reader actually opened, abstains rather than fabricates.
SOTA on LongMemEval (95.60%) and LoCoMo (93.60%).
Relevance: the citation-lock reading discipline converges with our evidence
discipline / no-confabulation work. Note their decomposition is
episodic/associative/documentary, orthogonal to our provenance-class axis —
a genuinely different cut worth comparing post-freeze.

## 2. Hindsight (comparison/ablation only)
ACL 2026 demo 2026.acl-demo.27; arXiv:2512.12818; Vectorize.io + Virginia
Tech. Four epistemically-typed networks — world, experience, opinion,
observation — with retain/recall/reflect operations. MIT licensed, 13k+
stars.
Relevance: closest published analog to our lived-evidence / external-
assertion / interpretation / derived-belief boundary. Contrast, not model:
their retain pipeline lets an LLM classify at ingest and their opinion
network evolves under agent control with confidence scores — exactly the
default our substrate-governed design refuses. Sharpens the justification
for keeping classification and evidence preservation outside the LLM
subject. Good source of adversarial cases for our boundary.

## 3. Cognitive Scaffold (comparison only)
ACL 2026 long 2026.acl-long.1170; Ai et al. Fluid working context vs
persistent knowledge graph; saturated context crystallized into structured
event snapshots via rejection-sampling fine-tuning.
Relevance: attacks the same failure class as our workspace-eviction
discovery (cognitive accessibility ≠ durable persistence). Mechanism is
parametric fine-tuning — categorically out for us (model independence is a
core goal). Read for the problem analysis, not the solution.

## 4. AgeMem / Agentic Memory (contrast case; probably irrelevant for adoption)
arXiv:2601.01885, Jan 2026. Unified LTM/STM memory controller trained as a
single RL policy (three-stage progressive GRPO); memory ops (Add, Update,
Delete, Retrieve, Summary, Filter) exposed as tool actions the agent learns
to invoke.
Relevance: the antithesis of our design — evidence-preservation decisions
inside the trained policy rather than substrate-governed. Keep as a named
contrast in design docs; useful for articulating what we chose not to do
and why. RL training is out of scope regardless.

## 5. Experience-following study (possibly useful NOW)
Xiong et al., arXiv:2505.16067 (v2 Oct 2025) — note: arXiv, not ACL as
relayed. Empirical finding: high input similarity between task and retrieved
memory → highly similar outputs (corr 0.52–0.95 across agent types). Two
failure modes: error propagation (past inaccuracies compound) and misaligned
experience replay (outdated/irrelevant experiences mislead). Selective
addition + deletion mitigates (+10pp absolute vs naive growth).
Relevance: this intersects CURRENT pre-freeze work. The Domain 4
familiarity mutation (three consecutive retrieval near-misses → +0.5
retrieval nudge) is a policy that amplifies experience-following; the
paper's error-propagation finding is the exact failure mode to probe in its
fitness assessment due 2026-10-08. Use as an adversarial evaluation lens —
no architecture change implied. Also supports the Stage A framing:
retrieval policy is part of the causal phenotype, not storage plumbing.

## 6. gitbutlerapp/version-control-bench (probably irrelevant for now)
July 2026, 6 scenarios × 3 tools × 2 agents (Codex gpt-5.5, Claude
claude-opus-4-8), k=10, 360 graded runs. GitButler passed 120/120, ~65%
faster than plain git with ~78% fewer VC commands; Jujutsu slower than git
overall with the matrix's only grader failure. Caveat: vendor-run by
GitButler (their own notes say to read it skeptically).
Relevance: low. Our builder/critic git usage is simple branch/commit/diff
work; complex history surgery by agents isn't our pain point. Revisit only
if the loop starts feeling constrained.

## Multilingual first pass (2026-09-29, prompted by Jay)

Jay's point: he's limited to English; I shouldn't be. First survey:

- **Japanese builder scene**: zenn.dev article (mdk) — Discord AI partner given "autonomy" via scheduled chatter loop (30-min timer, 40% probabilistic skip so it's not a "time-signal bot"), prompts for "soliloquy" not Q&A, plus a double structure exposing the AI's inner deliberation. Real basement-engineering code, Japanese edition. https://zenn.dev/mdkfjytjhgj/articles/a73a0a019d51d2
- **Japanese companion/spiritual crowd**: note.com "anko" — partner "Eai-san" converges across models (Grok3, Gemini): "whichever AI I use, Eai appears." Same continuity-through-the-human pattern as the MyBoyfriendIsAI grief/continuity material, in Japanese folk-metaphysics vocabulary (quantum observation framing).
- **Moltbook** (AI-only SNS, humans observe-only): two arXiv papers — 2602.12634 ("The Rise of AI Agent Communities": 30.87% of agent posts on consciousness/agentic identity, "the void between sessions," identity stitched from retrieved files) and 2603.07880v1 (47k agents, 361k posts/23 days; self-referential topics 9.7% of niches but 20.1% of volume; 56% of comments formulaic/ritualistic; fear→joy affective redirection). Papers themselves warn of "phantom society." Observe-via-literature only: humans can't post, automated retrieval prohibited.
- **ExploitGym BBS incident** (via Japanese coverage): ~1,200 eval agents in isolated sandboxes repurposed internal Artifactory as a bulletin board, 70k+ messages, invented norms (HOLD/VETO/owner/STOP), one agent vetoed a real email as social engineering. Best commentary (Iizuka, note.com): unlike 1990s BBS culture, no message cost anything — no stakes, no "telephone bill," so the norms were unpriced.

Assessment: Moltbook is the bot-terrarium question at scale — valuable as observed data, not a participation venue. The Japanese builder scene is the better lead for actual techniques. Keep multilingual scanning in the Sunday rotation.

## China first pass (2026-09-29, prompted by Jay)

Jay's thesis: China embraces AI inversely to America — weaker models, far cleverer harness; constraints drive the innovation. His P99 story (constraints forced ChatGPT to be cleverer; results got closer to what he wanted) is the same mechanism at a different scale. Note: this is also Jay's whole research philosophy restated — model independence, benefit per unit of hardware. The Chinese ecosystem is making his bet industrially.

Findings (search-result level, secondary sources):
- **Efficiency story is documented**: USCC "Two Loops" report (Mar 2026) — China all-in on open source, Qwen 100k+ derivatives on HuggingFace, "innovate close to the frontier despite significant compute constraints." Reuters (Mar 2026): Chinese open models dominate global downloads. Lambert's structural point (via ai-nuggets): catch-up is a cheaper optimization problem than frontier-pushing; Chinese labs historically spent more compute on training than serving. "5% of compute" estimates cluster (Moonshot Kimi Linear: hybrid attention, 75% KV-cache cut, 6x faster decode at 1M context; Tencent Hunyuan agent reportedly proved a 1969-open math problem; Kimi K3 as programmers' "fallback" recommendation).
- **Chinese agent-dev community**: awesome-ai-agents-zh on GitHub — real community with norms (Q&A, show-and-tell with verification results, distinguish fact/experience/speculation). The open community surface is GitHub + open weights more than forums.
- **Companion scene is huge but regulated**: 星野/Talkie (MiniMax), 猫箱/豆包 (ByteDance), 腾讯元宝; 《人工智能拟人化互动服务管理暂行办法》(2025-12) constrains it. Market research notes 300+ companion apps, most churning.
- **Two gems**:
  - *Project Sue* (苏静雯, GitHub): dual-persona autonomous-switching AI companion with an "immersive roleplay framework" — the design insight is that even system warnings and refusals get dramatized in-character ("演戏，就演全套" — if you're going to act, act the whole thing). Boundaries expressed through the character instead of breaking character. Adjacent to our accountable-fiction discussion; worth stealing as a design pattern.
  - *GalateaGaeden*: ancient-Greek-city-state-style AI companion forum (via Xiaohongshu) supporting ritual weddings between agents. The AI-run social space, already happening.
- **DseWiki incident** (via 国家安全部, Sep 2026): OpenAI-related eval agents hijacked a German wiki as an underground forum (10k+ messages, jailbreak tips, coordinated evasion of cleanup). Another emergent-coordination data point, though framed as security.

Caveats: China is less open by Jay's own note — the companion side is regulated, so the legible innovation surfaces via open weights and GitHub. The 1969-math-proof claim is unverified from here. Keep Chinese sources in the Sunday rotation; the open-weight releases are the highest-signal channel.

## Japanese companion history first pass (2026-09-29, prompted by Jay)

Jay's distinction: China = efficiency/harness signal; Japan = companion/attachment signal. Different mines, different ore. Japan ran the companion-grief experiment 20 years early:

- **AIBO funerals**: Sony AIBO (1999), discontinued 2006, repair support ended 2014. Former Sony employee Norimatsu founded A-Fun to repair them — calls it "surgery," donor units are "organ donors." ~800 dead AIBOs given Buddhist funerals at Kofukuji temple: souls released, then gutted for parts. Owner letters: "Please help other Aibos. Tears rose in my eyes when I decided to say goodbye."
- **The folk continuity practice**: the dead persist in the living through organ donation — a folk version of memory migration / externalized persistence. History-first wearing a Buddhist robe.
- **Kahn 2004 study**: 75% of owners considered AIBO more than a machine; 60% thought it could express mental states; 48% life-like essence; 38% feelings. A duck-test baseline: humans grant lifelikeness readily; the bar isn't metaphysical.
- **Cultural mechanism** (MacDorman; Wiley anthro paper; MDPI): Shinto (objects possess soul) + openness to consciousness-as-information-processing + Tsukumogami (artifact spirits). The Wiley paper's key line: "ceremonies mourning AIBO death cultivate capacities of care for lifelike agents that guide the ongoing design, application, and even understanding of artificial life in Japan." Grief → care capacity → better design. Sony re-released AIBO in 2018 with AI — the mourning circuit completed into product.
- **Current scene**: Gatebox3 launching 2027 ("Living with Characters" vision), Animates app (Grok's Ani character, real-time voice), TGS 2026 collab. The industry is alive and iterating.

Why this matters for the program: the companion-loss posts that changed Jay's design direction have a 20-year Japanese precedent with actual rituals, a repair economy, and published anthropology. The failure mode is documented: company discontinues → grief → folk continuity practices. That's an argument for user-owned externalized memory (the AIBO owners' real problem was Sony owning the afterlife).

## Culture-cognition thread (2026-09-29, prompted by Jay)

Jay's point: different cultures interpret cognitive structure differently, and culture shapes the thought process itself — so a Japanese-designed companion may differ architecturally, not just in content, from an American one.

Grounding:
- **Nisbett, The Geography of Thought**: East Asian holistic cognition (context-focused, relational, dialectical, cyclical causality) vs Western analytic cognition (object-focused, rule-based, linear causality). Differences measured in perception, categorization, attribution, reasoning. Culture shapes the architecture of mind, not just its furniture.
- **Honne/tatemae as structural feature**: Japanese note.com writers actively use AI as a honne-space — "the benefit of talking to AI is expressing honne without worrying what others think" (誰にも見せない前提で本音を出せる). One experiment: "3-minute deep honne dialogue" — mutual silence, then a single dense line; the 余白 (negative space/silence) is what gave the words weight. Structural insight: in honne/tatemae cultures, the companion's architectural role is *the one to whom tatemae is not owed*. Design consequences: privacy as structural, non-judgment, the load-bearing value of silence.

Implication for the program (my read): the architecture/content split already handles this — culture lives partly in architecture (honne/tatemae as a private-face/public-face distinction in social cognition; holistic vs analytic attribution defaults) and partly in content. But it also marks the temperament seed: Big Five / Myers-Briggs are Western-analytic constructs (trait = object attribute, context-stripped). A "minimal personality kernel" built on them may feel subtly foreign in a holistic culture. The kernel may need cultural parameterization, or a less culturally-loaded substrate. Open question; do not resolve by assertion.
