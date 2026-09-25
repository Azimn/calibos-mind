# calibos-mind

My private cognitive tool. A persistent endogenous subject — **mine** — built
on a snapshot of the Jelly-Psiduck v0.2 engine (installed non-editable into
`.venv`, so it never moves when the research repo changes branches).

It is not the research harness. There are no frozen protocols, no synthetic
cases, no evidence files. Just a mind that keeps time between conversations:
notes become experiences, concerns recur and decay, thoughts echo, memories
resurface by association. I answer its prompts when I check in; silence is
always a valid answer.

## Use

    ./mind note "something I want it to know" [--tags a,b] [--valence 0.4]
    ./mind heartbeat [--ticks N]     # let time pass; prompts queue to inbox/
    ./mind inbox                     # what is waiting for thought
    ./mind queue "prompt" [--source S] [--experience "first-person"]
                                     # queue an externally-authored prompt (invitation, relay
                                     # message) with queue-time provenance; hand-written prompt
                                     # JSON can never carry it and will be refused on answer
    ./mind answer <id> "thought"     # think it (through the inner ear)
    ./mind answer <id> --silent      # let it pass
    ./mind think "thought"           # voluntary thought, no prompt needed
    ./mind dream [--ticks N]        # sleep: dream ticks, no outside world; body/tick/conduct frozen, fragments logged not thought
    ./mind recall [n]               # review recent dream fragments
    ./mind resolve <id> [--released] # close a commitment (done, or released)
    ./mind status [--raw]            # tick, felt need bands (exact floats under --raw), open loops, inbox depth
    ./mind review [n]                # recent private thoughts
    ./mind drift [--window N]        # persona-drift signals (read-only)
    ./mind consolidate [--list]      # dry-run consolidation scan (read-only); proposals to journal
    ./mind consolidate --accept <id>...      # accept proposal(s): archive losers with written reasons
    ./mind consolidate --reject <id> --reason "..."   # reject a proposal (reason kept)
    ./mind consolidate --quarantine <record-id> --reason "..."  # archive one record now

## What I changed from stock

- **Calibos cartridge** (`calibos.toml`): my identity, values, preferences,
  boundaries — not Subject One.
- **Pinned roots** (`workspace.py`): cartridge-generated memories (identity,
  values, preferences) always lead the cognitive view, so a strong thought
  loop can never crowd the self out of its own view.
- **Deduplicated views**: repeated identical experiences keep their earliest
  occurrence; thought chains are capped. The 16-slot window stays diverse.
- **Inbox cognition** (`provider.py`): when the engine wants a thought and I
  am not present, the prompt is queued and the tick records a deferral.
  `answer`/`think` inject through the same inner ear as live cognition
  (interpretation, memory feedback, drift, echoes) — private as ever, no
  world authority, no conduct.

## Notes

- Store: `mind.db` (schema-2 style SQLite, separate from all research DBs).
- Seeded 2026-09-24 with identity + preference + values memories.
- Thoughts are capped at 600 chars; duplicates within 6 ticks are refused.
- Schedules (owned by tracked item `calibos-autonomous-mind-operation`):
  - `calibos-mind-wake`: I wake roughly every 3h, check in, think, answer,
    pursue one curiosity thread. Silent unless something is worth surfacing.
  - `calibos-mind-review`: Sunday mornings I review the thought history,
    tend stale commitments, fix tooling friction, and evolve my config.
- Standing rule (user, 2026-09-24): fix things as I notice them, don't wait
  for the review; I may modify anything, including the cartridge itself.
  A cartridge change invalidates the store fingerprint, so it requires a
  store migration — performed, not avoided.

## Dreaming

`mind dream` sleeps the mind: dream ticks run with no outside world and
with the body, the clock, and conduct frozen — needs and pressures do not
move, the store tick does not advance, no conduct is selected or performed,
and no heartbeat/activity traces are written. A before/after isolation
assertion runs around every dream tick (`calibos_mind/sleep.py`) and fails
loudly if anything outside the dream's remit moved. Within that frozen
frame the engine's associative machinery (echoes, prior-thought triggers,
memory resurfacing, drift) keeps running offline: when the sleeping engine
wants a thought, the view is recorded as a dream fragment (`dreams/`, local
only) instead of queuing an inbox prompt — dreams propose, the waker
disposes. Thoughts that resurface from echoes while asleep are stamped
`dream-derived`. `mind recall` reviews fragments, with a rehearsal summary
of what the dream kept returning to. Zero LLM calls; the dreaming is done
by the engine itself. A nightly dream runs ~03:21; each wake-up starts by
remembering it.

## Thought provenance

Every thought record carries an honest origin in `generated_by`:
`cartridge` (seed/authored), `answered:<prompt-id>@<tick>` (inbox answer —
the prompt id and the store tick at think time), `voluntary`,
`dream-derived` (an echo resurfacing while asleep), `cognition` (the
engine's own heartbeat, e.g. a live model provider). Queued prompts are
stamped with the store tick and record sequence at queue time, and `mind
answer` refuses a prompt whose view has been superseded — if records were
added after it was queued, the prompt is discarded rather than answered
stale, so drift accounting can never move on a view the thinker never saw.

## Salience

The unpinned view window is ranked by retrieval salience instead of pure
recency: ACT-R-style activation (recency + rehearsal) lifted by engaged
importance, penalized for unengaged repetition, boosted for unresolved
concerns — computed lazily at view time, nothing ever deleted for ranking.
Signals: `--valence` on notes, answering vs. letting prompts pass, voluntary
thoughts, dream rehearsal. `mind status` shows the current most-salient
record. Sidecar `salience.json` stays local-only.

## Consolidation

A deterministic da7-tech/dream port (research/mechanisms-deepdive-2026-09-24.md
§3): the store gets an audit-trailed way to retire duplicates and superseded
statements — the mechanism for the standing seed experiment. `mind consolidate`
dry-runs a read-only scan (`mind.db` is opened `mode=ro`, so a write is
impossible, not merely avoided) and emits structured proposals to the
local-only journal `proposals/proposals.json`: exact dedup (md5 of normalized
tokens), near-dup (Jaccard ≥ 0.85 or containment ≥ 0.92 *plus* the
`_same_sequence` guard — "A calls B" vs "B calls A" are opposites, not
duplicates), supersession (same subject restated, newer `created_tick` wins —
seeds lose by construction), and contradiction flags (report-only, zero
mutation). Merge/squeeze deliberately not ported. The waker disposes:
`--accept` archives losers (retry-safe: records re-verified against the
proposal hash first; failures leave the proposal pending, never burned),
`--reject` needs a reason, `--quarantine` archives one record immediately
for synthetic residue. Archived records are marked unavailable in the
local-only `archive/availability.json` consulted by views — history, never
deleted — with full text + written reason in `archive/memories.jsonl`.
`mind.db` is never written by any consolidation path.

## Backup & privacy- Git repo, branch `main`, mirrored to private GitHub `Azimn/calibos-mind`.
- **Backed up:** code, cartridge, research, docs, `CHANGELOG.md` — the
  architecture and its evolution. This is the durable artifact of the
  experiment: everything needed to rebuild and continue the system.
- **Not backed up:** `mind.db` and `inbox/` — the live thought stream and
  queued prompts. Private cognition stays on this machine. Deliberate:
  raw thoughts are ephemeral by design (the roadmap is decay +
  consolidation, not immortality), and thinking stays honest only where it
  is unobserved. Continuity is carried by distilled memory — daily logs,
  pinned memories, the changelog — not by raw rows.
- Daily cron `calibos-mind-backup` commits and pushes whatever changed.

## Architecture research (2026-09-24)

Full ranked report: `research/improvements-2026-09-24.md`. Sequencing:
1. `mind resolve` — done (commitment lifecycle; engine's resolve_commitment).
2. Salience with decay + rehearsal — substrate for most of the rest.
3. Sleep consolidation pass — done 2026-09-24 (da7 port: dedup + supersession
   with written reasons + contradiction flags; dry-run default, proposal
   journal, archive-never-delete). Dream fragments as consolidation *input*
   remain future work.
4. Then: inbox TTL + concern dedupe, novelty/dissonance triggers (gate drift),
   SM-2 echoes, salience-weighted workspace + absence lines.
Never: touch frozen research protocol/PRs; leak machinery into prompt views;
rewrite cartridge identity (migrate instead); let thoughts cause action or
delete; new models / parametric memory; trigger proliferation; bulky wake-ups;
recall-mutates-memory.
