# Spec: thinker-identity stamp on thinker-authored thought records

Date: 2026-11-16. Domain 23 (LLM leakage), check 11: "Model swaps noticeably
change identity" — baseline verdict: true, untested. There is no record
anywhere of which thinker answered which prompt, so a host-model swap is
invisible to the store and its effect on voice/content cannot be measured.
This mutation makes swaps observable. It is an instrument, not a behavior:
it records provenance, it changes no conduct, no retrieval, no rendering.

## What changes

1. **Stamp.** `CalibosSubject.inject_thought()` accepts an optional
   `thinker: str | None` label. When supplied, the injected thought record
   carries it as a `thinker` field alongside `generated_by`. When absent,
   the record carries no stamp (legacy behavior, byte-identical to today).
   Only thinker-authored paths stamp: `mind answer`, `mind think`,
   `mind wake --affirm` (all flow through `inject_thought`). Engine,
   dream-derived, interoception, and all other record sources never carry
   a stamp; a stamp on a non-thinker-authored record is a defect.
2. **CLI.** `mind answer --thinker LABEL` and `mind think --thinker LABEL`.
   Label validation: stripped, non-empty, max 80 chars, no newline or
   control characters; anything else is rejected at the CLI with a clear
   error and no record is written. The label is free text supplied by the
   answering host (e.g. a model identifier); there is no registry.
3. **Report.** New read-only command `mind thinkers [--window N]`
   (default N=100): over the most recent N thinker-authored thought
   records (sources `thought` with `generated_by` in the self-authored set:
   `voluntary`, `voluntary:inspection`, `wake`, `answered:*`,
   `answered-external:*`), counts per thinker label plus an `unstamped`
   count for legacy records with no stamp, and the tick range covered.
   No stamp is ever defaulted: unstamped reads as unstamped, never as a
   quiet zero or an assumed identity.
4. **Persistence.** The stamp survives save/load round-trip and `_restore`
   exactly (byte-identical record payloads apart from the stamp itself).
   Legacy records without the field load as unstamped, never crash.
   `mind init --force` behavior unchanged (records are wiped with the
   store; no separate sidecar exists for this mutation).

## What does NOT change

- View construction, salience, dedupe, per-class caps, drift, consolidation,
  dream isolation, conduct selection, and the provider boundary are untouched.
  The stamp is provenance metadata in the same class as `generated_by`: it
  is never read by any ranking, retrieval, or conduct path.
- The cartridge and the frozen engine are byte-identical (no migration).
- No new sidecar. No new scheduled behavior.

## Fitness function (synthetic /tmp stores only)

- `mind answer --thinker A` stamps the injected record `thinker == "A"`;
  `mind think --thinker B` stamps `thinker == "B"`; two answers with
  different labels produce distinct stamps on their records.
- Answering/thinking with no `--thinker` flag leaves the record unstamped
  and the record payload byte-identical to a pre-mutation record of the
  same text/tick (modulo the stamp field's absence).
- Invalid labels (empty, whitespace-only, >80 chars, containing a newline)
  are rejected with no record written and no store mutation.
- `mind thinkers` counts exactly: seeded stores with known label mixes
  report exact per-label counts, exact unstamped counts, and the exact
  tick range; `--window` bounds the scan to the most recent N
  thinker-authored thoughts; a store with no thinker-authored thoughts
  reports zero counts and an undefined/empty range, never a quiet zero.
- Stamp survives save/load round-trip and `_restore` byte-identically.
- Legacy record dicts without a `thinker` key load as unstamped; the
  report and all existing commands run cleanly on them.
- Windows byte-identical with and without stamps on the same store
  (view/salience/drift/conduct unaffected): same admitted ids, same order,
  same drift R, same conduct trace.
- Read-only sweep (`drift`, `status`, `review`, `thinkers`, `inbox`,
  `wake` briefing paths) leaves the store payload byte-identical apart
  from the known CLI-construction rewrite (genome: construction is never
  mtime-silent; scope assertions to payload content).
- Dream ticks never stamp: no dream-path record carries a `thinker` field.
- Full suite green.

## Revert signal

- A stamp appears on any record not authored through `inject_thought`
  (engine, dream-derived, interoception, or any other source).
- A stamp changes any retrieval, salience, drift, or conduct outcome
  (window/drift/conduct differs from the unstamped twin).
- `mind thinkers` miscounts against a seeded store, or reports a stamped
  identity for an unstamped record (silent default).
- Any existing test regresses.
- In vivo (assessor's note): labels are free text, so a mislabeled stamp
  (host passes the wrong identifier) is a data-quality note, not a revert
  trigger; only mechanism failure reverts.

## Assess after

2026-11-30.

## Provenance

Proposed by the daily articles review 2026-11-16 (domain 23, LLM leakage).
Builder/critic loop per research/builder-critic-loop.md. No commit; dated
CHANGELOG.md entry mandatory.
