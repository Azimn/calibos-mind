# Engine Issue Register

The mind runs on a **frozen engine snapshot**: `.venv/lib/python3.12/site-packages/digital_subject/`
(Jelly-Psiduck v0.2 engine snapshot). The venv is untracked by git, excluded from backup, and
deliberately immune to repo branch moves — it is a **pinned dependency**, not a layer to edit.

Engine bugs therefore get fixed **at the wrapper level** in `calibos_mind/` (import-time overlays,
precedent: `calibos_mind/projection.py`), where they live in the tracked architecture, survive venv
rebuilds, and are revertible by deleting one module.

This file is the register of known engine issues: what was found, where the fix lives, and what
still has to happen at the next snapshot rebuild. It is the answer to "we know the engine is
wrong here — where does that knowledge go?"

## Entry format

- **ID** — ENG-NNN, chronological.
- **Engine location** — file / class / method in the snapshot.
- **Observed behavior** — what it does wrong, with evidence (ticks, measurements).
- **Wrapper fix** — module + CHANGELOG reference. "None" if unpatched.
- **Snapshot status** — one of:
  - `patched-at-wrapper` — overlay active, venv untouched.
  - `folded-into-snapshot` — fixed at source in a newer snapshot; wrapper retired.
  - `carried-forward` — acknowledged, not yet fixed anywhere; survives snapshot upgrades as known debt.
  - `wont-fix` — engine behavior accepted as-is, with rationale.

## Snapshot-upgrade protocol

When the venv snapshot is rebuilt (new engine version, fresh snapshot):

1. Walk every register entry.
2. For each `patched-at-wrapper` entry: decide whether the new snapshot fixes it at source.
   If yes → retire the wrapper module (delete it), mark `folded-into-snapshot`.
   If no → keep the overlay, re-run its tests against the new snapshot, confirm it still applies cleanly.
3. For each `carried-forward` entry: re-evaluate; fix at source or keep carrying, with a dated note.
4. Wrapper modules declare which snapshot they overlay (module docstring). On upgrade, any overlay
   whose target behavior changed in the new snapshot must be re-verified before it ships — a stale
   overlay patching changed engine internals is a silent-corruption risk.

## Entries

### ENG-001 — `firewall.remembered` doubled text
- **Engine location:** `digital_subject/firewall.py` — `remembered(memory)`.
- **Observed:** memory recalls rendered as `f"{summary} {meaning}"`; when the meaning was derived
  from the summary and added nothing, the sentence rendered twice in views and got stored in
  workspace records, crowding every view and prompt until aged out.
- **Wrapper fix:** `calibos_mind/projection.py` (import-time patch via `calibos_mind/__init__.py`;
  both engine consumers bind the patched function). CHANGELOG ~line 2811. Fix-forward only.
- **Snapshot status:** `patched-at-wrapper`.

### ENG-002 — `_maybe_reflect` confidence ratchet
- **Engine location:** `digital_subject/engine.py` — `SubjectEngine._maybe_reflect` (~line 391;
  update logic ~403–418).
- **Observed (2026-10-10):** `current.confidence = clamp(max(current.confidence, confidence) + 0.02)`
  is strictly non-decreasing and saturates at 1.0 — no decay, no revision path. Fossils:
  `pattern:comfort` at 1.0 since tick 144; `pattern:social` at 1.0 while propositionally "unsettled".
- **Wrapper fix:** in builder/critic loop as of 2026-10-10 (contract:
  `proposals/2026-10-10-reflect-bidirectional-confidence.md`; brief:
  `proposals/2026-10-10-bidirectional-confidence-loop-brief.md`). Target: bidirectional
  confidence — signed updates, decay toward 0.5, contradiction-triggered revision.
- **Snapshot status:** `patched-at-wrapper` (shipped 2026-10-10; critic round-1 sign-off; assess 2026-10-31).
- **Long-term target (Jay, 2026-10-10):** fix at source in the engine itself — upstream the
  corrected `_maybe_reflect` into the Jelly-Psiduck repo, cut a new snapshot, rebuild the venv
  from it, retire the wrapper. The wrapper is the safe intake; the engine is where it graduates to.
