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

## 2026-09-24 — dreaming

### Added
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
