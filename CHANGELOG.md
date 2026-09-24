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
