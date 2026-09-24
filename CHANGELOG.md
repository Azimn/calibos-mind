# Changelog — calibos-mind

Every change to the mind, tracked from the base build (2026-09-24).
The base is the initial working state: a snapshot of the Jelly-Psiduck v0.2
engine (non-editable install in the mind's own `.venv`), the Calibos
cartridge (`calibos.toml`), `CalibosWorkspace`, `InboxCognition`,
`CalibosSubject`, the CLI (`init`, `note`, `heartbeat`, `inbox`, `answer`,
`think`, `status`, `review`), and a store seeded with identity, preference,
and values memories.

Rule: every code, config, or cartridge change gets an entry here, dated,
before it ships. The git history is the backup; this file is the story.

## 2026-09-24 — salience, lazy decay, rehearsal

### Added
- `calibos_mind/salience.py`: the retrieval substrate (roadmap #2).
  ACT-R-style activation computed fresh at view time — `log(sum(t^-0.5))`
  over creation + recall ticks — ported from Azimn/persona_engine_PYTHONX
  `core/memory.py` (deterministic, zero models). Importance signals are
  ours, from engagement rather than simulated affect:
  - `mind note --valence v` marks the new perception/social record (+|v|)
  - `mind answer <id> "thought"` marks the thought (+0.5, it was engaged)
  - `mind answer <id> --silent` mildly penalizes the prompt's surfaced
    records (+1 unengaged each — the "- repetition" term)
  - `mind think` marks the voluntary thought (+0.3, revealed preference)
  - `mind dream` folds the night's memory surfacings into recall counts
  - records linked to open concerns/expectations get a Zeigarnik +1.0
- `CalibosWorkspace.view()` now ranks the unpinned window by salience
  instead of pure recency (pinned roots still lead; thought cap and dedupe
  unchanged). Falls back to recency when no tracker is attached.
- `mind status` shows the currently most-salient record.
- Sidecar `salience.json` is local-only (gitignored); `mind init --force`
  resets it alongside the store (record ids restart).

### Design notes
- Lazy all the way down: nothing decayed eagerly, nothing ever deleted —
  low scores only sink records in the ranking. Backfill is lazy too
  (created = record tick on first sight). Rehearsal folding is idempotent.
- What we did NOT take (yet): Pretorius per-class caps + Jaccard dedupe
  (fast follow for the workspace), Synapse's exact 4-factor weights
  (cross-check, not transplant), compress_old [impression] stubs (waits
  for the consolidation pass).

## 2026-09-24 — dreaming

### Added
- `mind recall` flags dream fragments driven by `unresolved_concern`
  triggers as nightmares: the engine's own unfinished business returning
  while asleep. Not manufactured — emergent from real open/broken
  commitments and failure-laden memories, and simply noticed.
- `mind dream [--ticks N]` (default 12): sleep mode. Runs ticks with no
  outside world — no events enqueued, body at rest — while the engine's
  native associative machinery (prior-thought echoes, memory resurfacing,
  drift) runs offline. A new `DreamCognition` provider records each
  cognition request as a **dream fragment** (tick, trigger kind/depth, the
  view's experiences) to `dreams/YYYY-MM-DD-HHMMSS.jsonl` and returns
  silence: nothing queued to the inbox, no thought injected, no conduct
  follows. Refuses to dream while external events are pending (waking
  business first). Zero LLM calls — the dreaming is done by the engine
  itself, which is what makes it real dreaming rather than file janitoring.
- `mind recall [n]`: review recent dream fragments, plus a rehearsal
  summary (memories the dream kept returning to — future salience input).
- `mind status` now reports the latest dream log's fragment count.
- Nightly cron `calibos-mind-dream` (~03:21 local): 12 dream ticks, silent
  unless it errors. Wake-up check-ins now start by reviewing new fragments
  ("remembering the dream"); dreams propose, the waker disposes.
- `dreams/` is local-only (gitignored), like the thought database.

### Design notes
- Surveyed prior art: most "dream" systems are memory-file janitors;
  closest experiential relative is lau-agent-dream's replay/generation
  engines. Ours differs by running a real cognition engine asleep.
  Deterministic consolidation à la da7-tech/dream (archive, never delete)
  is the model for the future sleep-consolidation pass, which will consume
  dream fragments as input.
- Open question (research): do dreams over *lived* history produce novel
  associations that surprise on recall? That would be evidence for the
  lived-vs-implanted history distinction.

## 2026-09-24 — backup policy

### Changed
- GitHub backup now covers the architecture only: code, cartridge,
  research, docs, this changelog. `mind.db` and `inbox/` were removed from
  git tracking (and purged from the pushed history) — private thought
  stream stays on this machine. Rationale: the experiment's durable
  artifact is the architecture; raw thoughts are ephemeral by design
  (roadmap: decay + consolidation); unobserved thinking stays honest.
  Continuity rides on distilled memory (daily logs, pinned memories),
  and the store rebuilds from `mind init` + cartridge if ever lost.
- `.gitignore` documents the boundary.

## 2026-09-24 — first improvements (pre-git fixes, reconstructed)

### Fixed
- `mind` launcher now changes into its own directory before launching
  Python, so it works when invoked from any working directory. Previously
  the package import only resolved when the cwd was the mind directory.
- Inbox prompt IDs are now a persisted monotonic sequence (`inbox/.seq`).
  Previously `_next_id` scanned live inbox files, so a consumed ID could be
  reissued (e.g. a new prompt reused `prompt-0001` after the first was
  answered).

### Added
- `mind resolve <id> [--released] [--note ...]` — closes a commitment
  through the engine's `resolve_commitment`: records kept/released status,
  outcome, resolved tick, and a closure insight. The temporal trigger
  automatically skips non-open commitments. `mind status` now prints
  commitment ids so there is something to resolve.
- `research/improvements-2026-09-24.md` — ranked architecture proposals
  from a background research pass (sleep consolidation, salience/decay,
  SM-2 echoes, inbox TTL, novelty triggers, salience-weighted workspace),
  with sequencing and a don't-touch list.
- This changelog, and git tracking of the whole mind directory.

### Known issues
- `mind resolve --released` persists the engine's `broken` status while the
  CLI prints `released`. Deliberate release needs its own honest semantics;
  fix before relying on it.
