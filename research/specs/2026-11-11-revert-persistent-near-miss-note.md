# Spec: revert the persistent-near-miss note (Domain 17 follow-up, 2026-11-11)

## What
Remove the persistent-near-miss note-minting path shipped 2026-10-28
(`calibos_mind/familiarity.py`, `calibos_mind/cli.py`, `calibos_mind/workspace.py`
— see the 2026-10-28 CHANGELOG entry for exactly what shipped). Keep the
streak tracking and the near-miss retrieval boost from the 2026-09-28
familiarity-trace mutation (a kept mutation): streaks still accumulate,
the boost still fires at the designed rhythm, admission still happens.
Only the note minting goes away.

## Why
Assessment at window close (2026-11-11, tick 1072, 1923 records): the
steady-state revert signal FIRED. 135 near-miss notes minted ticks
849-1070, all at exactly streak 10, 0 duplicates, 0 notes-about-notes,
0 below-10 mints — the mechanism works exactly as specified. But the
note rate is 57 notes per 100 waking ticks in [872,972] and 56 per 100
in [972,1072] (56%, 11x the 5% steady-state tripwire), with no decay
across two consecutive full windows ~220 ticks post-ship: this is steady
state, not the seeding transient exempted by the 2026-10-28 adjudication.
In [972,1072] near-miss notes are 56 of 58 cognition records (97%) and
22.8% of all 246 new records. The per-record-per-episode design is
correct; at the mind's record-growth rate it floods the store. A revert
is a success, not a failure.

## Components
1. Remove the note-minting call path: no temporal record is minted when
   a streak reaches 10 (or any streak), on any path (waking tick,
   read-only command, dream tick — none of which may mint by
   construction, but verify).
2. Remove the `noted` tracking from `familiarity.json` (the per-target
   noted-at-streak record). The loader must tolerate a sidecar that still
   carries a stale `noted` key (ignore it, do not crash, do not resurrect
   minting). `init --force` still wipes the sidecar.
3. Keep, byte-for-byte in behavior: streak accumulation, the near-miss
   boost (`boost_for == 0.5` at the designed streak, 4th-view admission),
   the designed 3:1 admission rhythm, archived/pinned exclusion,
   determinism, read-only/dream isolation of the sidecar.
4. Tests: remove or rewrite the tests that assert note minting (they now
   assert the opposite: streak 10 / streak 25 -> zero notes, no temporal
   record, sidecar `noted`-free). Keep every streak/boost test green.
   Tests on synthetic /tmp stores only, never the live store.
5. Dated CHANGELOG.md entry (2026-11-11) recording the revert, the
   evidence (note counts and rates above), and what stays.
6. No commit (the backup job handles that). No engine or cartridge
   change. The 135 existing near-miss notes in the live store stay as
   history (never deleted; consolidation may archive them later) — the
   revert stops future minting only.

## Fitness function (synthetic /tmp stores only, never the live store)
- Synthetic store, streak driven to 10, then to 25: zero temporal records
  minted, zero `noted` entries, sidecar contains streaks only.
- Streak/boost behavior unchanged: 3 consecutive near-miss views ->
  streak 3, boost_for == 0.5 exactly; 4th view admits target, streak
  resets; boost flat at streak 100; archived/pinned records never
  near-miss; no-op observe writes nothing; byte-identical determinism.
- A sidecar carrying a stale `noted` key loads without error and the next
  write drops the key (or ignores it — state the chosen behavior in the
  CHANGELOG entry and test it).
- Read-only sweep + dream ticks leave the sidecar byte-identical;
  `init --force` wipes it; full suite green.

## Revert signal (for the revert itself — the loop's critic round is the check)
- Any temporal record minted from a streak on a synthetic store.
- Any streak/boost regression (an existing familiarity test goes red for a
  behavioral reason, not a test-premise update).
- Loader crash on a stale `noted` key.

## assess_after
N/A — this is the execution of a fired revert signal, not a new mutation.
Record the revert in
`workspace/goals/calibos-autonomous-mind-operation/hidden_files/articles-rotation.json`
(the design lead does this; the builder does not touch the rotation state).

## Process
Follow `research/builder-critic-loop.md` (read it first, including the
regression genome — check every item). Establish the pre-change full-suite
baseline first (run it yourself; the design lead's background run was
still in flight). Tests on synthetic /tmp stores only, never the live
store. Dated CHANGELOG.md entry. No commit.
