# Spec: `mind intend` — self-authored commitment registration

Date: 2026-10-14. Source: articles review, Domain 19 (Agency) adversarial pass.

## What

A new wrapper CLI command:

```
mind intend "description" [--due TICKS] [--importance 0.5]
```

It registers a commitment with **actor `"self"`** in the continuity ledger via the
frozen engine's `continuity.create_commitment(...)` (digital_subject/continuity.py —
read-only inspection confirmed the API; the venv is never edited). It prints the new
commitment's id (unique-prefix form, usable by the existing `mind resolve`).

- `--due TICKS`: relative offset; `due_tick = current_tick + TICKS`. Default: none.
- `--importance`: float, clamped to [0,1] by the engine. Default 0.5.
- `beneficiary` defaults to `"self"`.
- Empty/whitespace-only description → clean error on stderr, nonzero exit, no
  ledger write (genome: silent defaults — report, never a quiet zero).

## Why

Agency domain checks, scored 2026-10-14:

- Check 4 "No self-initiated projects" — ABSENT, still true. The ledger has held
  exactly two commitments in 617 ticks, both `actor: "world"` test artifacts, both
  closed. The mind has never bound itself to a plan; self-authored intentions live
  only in thought text (same gap filed from the 2026-10-13 Prospection pass,
  check 1: "expectations register future intent ... but only for externally-sourced
  inbox prompts — no self-authored future intentions").
- Checks 1/3 ("waits for the user", "nothing meaningful happens without prompting")
  — partial; the schedule is parent-owned, but what the mind does *inside* a wake
  can now carry its own causal weight.

This is structural, not theater: the engine's endogenous machinery already treats
open commitments as first-class cognitive citizens — `_open_records` yields
`commitment:<id>` inner-ear records with `_urgency(due, importance, actor)`;
`_project_temporal` mints escalating-grade temporal records for due commitments;
`unresolved.live_open_keys` counts open commitments toward the UNRESOLVED_BOOST
link keys. Actor `"self"` plugs the mind's own plans into that economy with zero
engine changes. `relationships.get("self")` is None, so urgency degrades honestly
to `importance*(.25 + .45*age/(age+6)) + .05` — modest, no invented attachment.
Existing `mind resolve` (kept/released/broken semantics) already closes them.

Deliberately NOT included: auto-promotion of thought text into commitments
(false-positive risk on private cognition; the waker binds itself deliberately,
mirroring `mind think`), and any engine/venv change.

## Fitness function (all on synthetic /tmp stores, never the live store)

1. `mind intend "revisit the duck-theory notes" --due 48 --importance 0.6` →
   exactly one commitment: status `open`, actor `"self"`, beneficiary `"self"`,
   `created_tick` == current tick, `due_tick` == current tick + 48,
   importance 0.6; the printed id resolves via `mind resolve <prefix>`.
2. No `--due` → `due_tick` None; no `--importance` → 0.5.
3. Empty description → stderr error, nonzero exit, ledger unchanged.
4. `mind resolve <id>` closes as kept with outcome; `mind resolve <id> --released`
   closes as released — through the existing resolve paths, insight weights
   unchanged.
5. Endogenous parity: an open self-commitment surfaces in `_open_records` as
   `commitment:<id>` with 0 < urgency ≤ 1; a due self-commitment crossing a grade
   boundary mints a temporal record via `_project_temporal` exactly as a
   world-actor commitment does (same grades, same concern_links).
6. `unresolved.live_open_keys` includes the open self-commitment's id
   (UNRESOLVED_BOOST eligibility parity with world commitments).
7. Read-only sweep (`drift`, `status`, `consolidate --list`, `recall`, `review`,
   `inbox`) creates no commitment; dream ticks create none.
8. `init --force` clears the commitment ledger (genome: sidecar/state reset on
   reseed — a reseed must not carry dead bindings; verify current `cmd_init`
   behavior and extend it if it preserves commitments).
9. Full existing suite green; engine + cartridge byte-identical.

## Revert signal

- A commitment minted by any path other than `mind intend`.
- A self-commitment with wrong actor/beneficiary, or a duplicate mint for one
  invocation.
- `init --force` leaving a stale commitment behind.
- Any existing test regresses.
- In vivo: self-commitments opened but never resolved/released across the window
  (binding without follow-through is theater — the fitness window judges).

## assess_after

2026-10-28.

## Constraints

- Wrapper layer only (`calibos_mind/`). The venv is an immutable pinned snapshot.
- Dated CHANGELOG.md entry (2026-10-14). No commit (the backup job handles that).
- Tests run on synthetic /tmp stores, never the live store.
