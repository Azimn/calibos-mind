# Builder/Critic Loop — standing procedure for shipping mutations

Adopted 2026-09-24. Every mutation to calibos-mind ships through this loop.
The loop is the review process made structural: no change lands without an
adversary trying to break it first.

## Why separate instances, not hat-switching

The critic must read the work cold. A single context switching hats inherits
the builder's framing and blind spots — the exact failure mode a critic exists
to catch. So the builder and critic are separate subagent instances; the critic's
brief contains only the spec and the diff, never the builder's reasoning.

## The loop

1. **Spec.** The design lead (main agent, or the daily articles review) writes a
   short spec: what changes, why, the fitness function, the revert signal, and
   the assess_after date. The spec is the contract both sides work from.
2. **Build.** A builder subagent implements the spec with tests. Tests run on
   synthetic `/tmp` stores, never the live store. Every change gets a dated
   CHANGELOG.md entry. No commits (the backup job handles that).
3. **Critique.** A separate critic subagent receives only the spec + the diff.
   It attacks the work:
   - Every objection must arrive as a **failing test** or a concretely violated
     invariant. Prose-only critique is not accepted.
   - It works the code, not the builder's summary. It runs the tests itself.
   - It checks the **regression genome** below explicitly, item by item.
   - It signs off, or returns concrete failures. No middle ground.
4. **Fix.** The builder addresses the failures — by fixing the code, not by
   weakening the tests. Then the critic re-verifies.
5. **Terminate.** At most 3 build→critique rounds. Then the design lead
   adjudicates: ship, revert, or escalate to the user. A revert is a success —
   the loop caught it before it became history.

## The regression genome

Every bug class we have ever actually hit. The critic checks each one, every
time. This list grows; nothing is ever removed.

- **Shadowing definitions** (2026-09-24): duplicate `cmd_init` in cli.py — the
  second definition silently won and dropped the salience-sidecar reset.
- **Sidecar/state reset on reseed** (2026-09-24): `mind init --force` must reset
  salience.json; stale importance must never attach to recycled ids.
- **Read-only violations** (2026-09-24): a read-only command (`mind drift`)
  changed the SQLite header change-counter. Byte-identical payload is not
  enough — prove no write path is touched.
- **Silent defaults** (2026-09-24): the first drift metric collapsed a fresh
  store to 0/0 by clamping. Report "undefined", never a quiet zero.
- **Unrestored swapped config** (2026-09-24): friction.py swaps the frozen
  engine config via dataclasses.replace — the `finally` restore must hold on
  every path, including exceptions.
- **Dream/conduct isolation** (fixed 2026-09-24): sleep.py freezes body, conduct,
  and tick during dream ticks with per-tick before/after assertions. Any
  dream-path change must assert before/after isolation of body, action history,
  pending events, and store tick. The full-trace equality assertion is
  eviction-aware (suffix compare) because the engine caps its trace at 256 entries.
- **Negation-blind similarity** (2026-09-24): stopword-stripped token sets erase
  polarity — an affirmative and its negation can score Jaccard 1.0 with the same
  token order, minting a "same fact worded twice" dedup proposal for opposite
  facts. Fix: a negation-polarity veto counting negation words on RAW text
  (pre-stripping), rerouting mismatched pairs to a contradiction-flag
  (zero mutation). The veto must cover every archive-bearing pass (near-dup AND
  supersede), not just one.
- **Dead regex branches** (2026-09-24): `\bn't\b` can never match inside a
  contraction (the "n" is always preceded by a word character, so the leading
  `\b` never holds) — "ain't"/"shan't" silently slipped the negation veto.
  Test regex inventories directly (every listed alternative must match its
  target; canaries must not), not just end-to-end.
- **Contraction tokenization hides polarity** (2026-09-24): "doesn't" tokenizes
  to `doesn`/`t`, so standalone-word matchers (`\bnot\b`) read 0 vs 0 while
  polarity differs. Polarity counts for veto purposes must be taken on raw
  text (e.g. `\w+n't\b`), OR'd with the standalone-word count.
- **Silent zero on missing sections** (2026-09-24): `load_records` defaulted a
  missing `workspace`/`records` section to an empty scan — a quiet zero. The
  engine always writes `workspace.records`, so a missing section is malformed
  and must raise; only a genuinely empty records list may report zero.
- **Negative-activation collapse** (2026-09-24): ACT-R `log(Σt^-0.5)` goes
  negative for old unrehearsed records — any aggregation over activations must
  handle this honestly.
- **Contraction-negation blindness** (2026-09-24): similarity math sees token
  *difference*, never *polarity* — "doesn't" tokenizes into fragments the
  standalone-negation veto can't see, minting false SUPERSEDE proposals that
  archive affirmatives under a "restatement" rationale. Three variants caught
  across three critic rounds (near-dup, supersede-standalone,
  supersede-contraction). Every archive-bearing pass must veto on polarity
  independently, counting `\w+n't` contractions on raw text — and a *missed*
  consolidation (gates don't fire) is the safe direction; a *wrong* archive is
  the hazard.
- **Test-premise drift** (2026-09-24): a critic test can encode measurements
  that don't match the code's actual tokenization (claimed containment 1.0,
  measured 0.833). A red test whose premise contradicts measured behavior is a
  test-side correction, not a code defect — prove it with a pre-fix baseline
  before adjudicating.

## Fitness discipline

Each shipped mutation is registered in
`workspace/goals/calibos-autonomous-mind-operation/hidden_files/articles-rotation.json`
with its fitness function, revert signal, and assess_after date. The daily
articles review evaluates closed windows and keeps or reverts on evidence.
