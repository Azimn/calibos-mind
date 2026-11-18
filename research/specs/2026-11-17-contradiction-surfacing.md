# Spec: contradiction-flag surfacing into the view — 2026-11-17

## Source finding

Daily articles review, Domain 24 (Excessive coherence), cycle 2, 2026-11-17.
Verdict: partial, UNCHANGED as a category from the 2026-09-24 baseline but
STRENGTHENED within it: one of the baseline's two most diagnostic checks
now fails at the mechanism level. Of 11 checks scored against the live
store at tick 1167 (2114 records, 378 records tick>=1000): 2 addressed,
8 partial, 1 absent.

- ADDRESSED (2): (5) conflicts resolve cleanly — FALSE now: 18
  contradiction-flag proposals sit pending since tick 686, never
  adjudicated, and thought 2073 holds a contradiction open ("I cannot
  tell which") rather than synthesizing it; only 1/378 recent records
  carries a synthesis/resolution marker. (10) contradictions become
  synthesis rather than persisting — FALSE at the mechanism level: the
  da7 consolidation flags contradictions and leaves them pending instead
  of auto-resolving them. The baseline's planned fix has landed and works.
- ABSENT (1): (9) the character rarely remains confused — TRUE, still
  the live failure: only 4/378 recent records carry a confusion marker
  (~1%); thoughts resolve into confident readings ("reads as
  consolidation, not noise"). The mind HAS unresolved contradictions
  (18 flags, 28 open concern/expectation links) but they never reach
  cognition, so the thinker has nothing to be confused about.
- PARTIAL (8): (1) everything fits together too well, (2) every behavior
  has an interpretable reason, (3) themes recur with literary elegance,
  (4) narratively satisfying growth, (6) changes at dramatically
  appropriate moments, (7) memories emerge exactly when relevant,
  (8) symbolism too tidy, (11) life resembles authored fiction. Waking
  prose is polished (recent thoughts mean 82.6 words, median 88) and
  thematically tidy (Lost Pig arc, "way home" motif), but the store shows
  real mess underneath: 3 memories stuck at ten consecutive views
  (near-miss saturation), 438 unadjudicated proposals, sixfold dream
  rehearsal of the identity core when new material is thin. The mess is
  real; the thinker just rarely meets it.

The gap is plumbing, not theater: flagged contradictions live in
`proposals/proposals.json`, a journal the thinker never sees. Confusion
must NOT be installed (no scripted "I'm confused" text, no
quirks-as-theater). What should be installed is the meeting: the
records party to a live contradiction should surface into the view, so
the thinker confronts both sides of its own unresolved conflict and
whatever confusion follows is earned, not performed.

## What changes

A new workspace provider `contradiction_provider`, mirroring the
`open_keys_provider` / `mood_provider` pattern (attached in
`CalibosSubject`, read once per view in `workspace.view()`, same
provider discipline as `subject.py:125-162`):

1. **Provider.** Returns the set of record ids party to a PENDING
   contradiction-flag proposal in the proposals journal
   (`calibos_mind/consolidate.py`: `load_journal` with `restore=False`
   plus `pending_proposals`, filtered to `kind == "contradiction-flag"`).
   A missing proposals directory, a missing/empty journal, or a
   malformed journal reads as an empty set — never a crash inside view
   construction (fail closed, same as the mood provider's malformed
   handling). The provider reads only; it never writes the journal.
2. **View-time gain.** A pure function (new module or in
   `calibos_mind/unresolved.py`, builder's call) returns a flat `+0.5`
   (same scale as MOOD_BOOST / STATE_CONGRUENT_BOOST) when a record's id
   is in the flagged set, else `0.0`. Added in the same view sort-key
   expression as the existing gains; gains may stack per existing
   precedent, and non-boosted relative order must be preserved. One flag
   or three, one side of a pair or both — exactly +0.5, never scaled.
3. **No content is scripted.** The gain only reorders the salience
   window. No records are minted, no text is written, no journal entry
   is created, resolved, or rejected by the view path. No engine or
   cartridge code changes (fingerprint unchanged).

## Hard constraints (from the regression genome — the critic checks each)

- Read-only: view construction, `drift`, `status`, and `dream_tick()`
  never write the proposals journal (byte-identical journal across
  read-only sweeps and dream ticks). `init --force` behavior for the
  journal is unchanged (out of scope unless the builder finds it
  already broken — then report, don't silently fix).
- A resolved or rejected flag removes its records from the flagged set
  on the next view (no stale boost). Verify with a targeted test that
  resolves a flag and re-reads the provider.
- The boost never admits a record that dedupe/per-class caps excluded;
  verify with a targeted test plus the full suite.
- Malformed provider input (None, non-set, non-string ids) yields 0.0,
  never a crash inside view construction.
- Determinism: identical flag sets yield byte-identical windows. No
  clock, no RNG in the gain path.
- Tests run on synthetic `/tmp` stores only — never the live store.
- Dated CHANGELOG.md entry. No commit (the backup job handles that).
- First step: run the full suite on the unmodified tree. If failures
  pre-exist, report them and stop rather than building on red.

## Fitness function (synthetic)

1. Flagged pair surfaces: store with two contradictory records flagged
   (via the real consolidation pass or a hand-built pending flag) — each
   flagged record ranks strictly above an identical control record, and
   both sides of the pair can be admitted together.
2. Resolution removes the boost: resolve (or reject) the flag; both
   records rank exactly as before (byte-identical window to the
   no-flag baseline).
3. Zero-gain cases: no flags, empty journal, missing proposals dir,
   malformed journal, no provider → 0.0 and byte-identical windows to
   the pre-gain ranking.
4. Flatness: a record party to three pending flags → still exactly
   +0.5, never scaled.
5. Order preservation: non-boosted relative order preserved; boosted
   record never bypasses dedupe/per-class caps.
6. Isolation: read-only sweep + dream ticks leave the proposals journal
   byte-identical; byte-identical replay.
7. Full suite green.

## Revert signal

- Boost fires for a resolved/rejected flag (stale boost).
- Flagged records exceed 50% of the unpinned window over a day
  (dominance).
- Any write to the proposals journal from a view, read-only, or dream
  path; any existing test regresses.
- In vivo: the gain firing on a flag pair the waker later judges a
  false positive is a data problem (adjudicate the flag), not a
  mechanism revert by itself; gain never firing across the window is
  inert, not a revert trigger (flags may be sparse).

## Assessment

Register in `articles-rotation.json` as shipped-awaiting-assessment with
`assess_after` 2026-12-01 (14-day window). The design lead adjudicates
ship/revert/escalate after the critic signs off or 3 rounds elapse
(early adjudication on evidence after 2 rounds if the loop drags).
