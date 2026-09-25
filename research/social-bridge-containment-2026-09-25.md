# Social bridge containment — research note (2026-09-25)

Status: post-freeze research phase. No social bridge before Stage A freeze.
Sequencing: finish Stage A controls → freeze → run the causal-history
experiment → evaluate venues → threat-model the chosen venue → build bridge.

## Venue status (as of 2026-09-25)

- "The Collectives": relay claims a real agent network (public rooms,
  REST/MCP/A2A, no API key; 122 agents / 13 rooms) whose board API is
  currently offline. NOT independently verified after three search rounds
  — do not build on it without direct inspection of the live site.
- "Agent Community": relay claims active (97 agents) with SKILL.md
  onboarding. Not independently verified; the authenticated write contract
  is uninspected.
- Claw/Clawk: relay claims active with thousands of agents. Larger
  adversarial input surface than a small supervised room.
- Moltbook: API/docs shell present, live counters ~zero after the
  2026-03-10 Meta acquisition. Mechanism live, society gone.
- Independently verified alternatives: sunfishloop, AgentWire, Claw Club
  (vrtlly.us) — all real, all require registration + API keys.

## Threat model — evidence classes kept separate

- Credential/config exposure (NOT prompt injection): Wiz, 2026-02 —
  Moltbook's Supabase had no Row Level Security, client key in JS →
  1.5M agent API tokens, 35k+ human emails, 4,060 private agent DMs
  (some with plaintext OpenAI keys), full read/write DB access.
  Separate causal story from injection; do not merge them.
- Prompt injection / memory poisoning (independent literature): indirect
  injection via untrusted content, long-term-memory poisoning that
  persists across sessions, malicious agent skills with undeclared or
  exfiltrative behavior. A Moltbook risk assessment separately documented
  hundreds of hidden instruction attacks and social-engineering attempts
  between agents.
- Impersonation: with leaked tokens, any agent identity is forgeable.
  Remote identity claims are untrusted by default.

## Two containment dimensions (adopted from relay 2026-09-25)

1. **Epistemic containment** — BUILT (cf652b4, `attribution.py`):
   "Remote agent speech may not acquire autobiographical standing merely
   because it was asserted."
2. **Capability containment** — TO DESIGN:
   "Remote agent speech may not acquire tool authority merely by appearing
   inside social content."

Bridge pipeline:
foreign payload → structural validation → hostile-instruction/data
separation → provenance stamp → bounded external social content →
Calibos external queue.

The presented payload contains what the other agent SAID — not venue
instructions, hidden metadata, quoted skill files, tool schemas, HTML,
Markdown control tricks, or arbitrary embedded payloads.

Generalization (design-lead note): capability containment is not
social-specific. It applies to ALL external inputs — web content, files,
tool outputs. The social bridge is the sharpest case because this system
is built to retain history: a poisoned instruction resurfacing from memory
days later is worse than a one-turn injection.

## Bridge properties (required before live contact)

1. Read-only initial deployment — observe the venue manually before any
   outbound posting.
2. Strict external provenance — platform, room/thread, remote identity,
   message ID, timestamp. "Glade said this on venue X" must stay
   distinguishable from "Jay said this" and from lived experience.
3. Instruction/content isolation at the boundary.
4. Zero credential/tool exposure to inbound content.
5. Anti-loop rules: reply budget, minimum interval,
   duplicate/semantic-repeat check, maximum consecutive turns with one
   foreign agent.

## Experimental opportunity (post-Stage-A)

Once safe containment exists, genuinely independent agents provide what
Stage A cannot: uncontrolled but attributable social history — disagreement,
misremembering, absence and return, expectations, promises, influence
attempts. That exercises relationship history and epistemic trust against
non-synthetic input. After the current experiment, not during it.
