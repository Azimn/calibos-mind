# Spec: state-congruent retrieval gain (Domain 9, mood-congruent recall analogue)

Date: 2026-11-02. Design lead: daily articles review (Domain 9, cycle 2).
Status: in builder/critic loop. Assess after: 2026-11-16.

## What

A pure, view-time, flat additive gain in the salience-ranked cognitive
window: a record whose `concepts` contain the name of a currently pressing
need gets +0.5 in the view sort key — the same scale as SELF_BOOST (0.5)
and FAMILIARITY_BOOST, meaningful against live activations (roughly
-2.7 .. +3.2) without being dominant.

- "Currently pressing" means felt level 2 (the 'pressing' band `mind status`
  reports), i.e. the *perceived* state, not the actual engine level —
  consistent with the interoception module's felt/actual distinction: what
  biases recall is the state as felt.
- Thread the felt levels into view ranking via a provider, following the
  existing `open_keys_provider` pattern in `workspace.py`. When no provider
  is supplied, the gain is 0 and windows are byte-identical to pristine.
- Pure function, no sidecar, no state, no writes, no clock, no RNG —
  the selfgain module (`calibos_mind/selfgain.py`) is the pattern to copy.
- Engine and cartridge byte-identical. No commit (the backup job handles
  that). Dated CHANGELOG.md entry required.

## Why

Domain 9 check 5 ("no mood-congruent recall") is absent: the mind has no
mood state, records carry no affect in vivo (all 1559 records have
`affect == []`), and the ranking has no current-state congruence term
(activation = recency + importance + unresolved boost; plus familiarity
boost and self-gain). Pressing needs are this mind's affective substrate,
and the neighboring gains (self-relevance, unresolved concerns,
familiarity) already establish that retrieval may be biased bottom-up by
what matters now. This is a mechanism, not theater: it changes which
records reach cognition; it scripts no content. It is NOT in the
install-a-quirk class (grudges, self-deception, suppression rebound,
defenses): state-congruent retrieval is a weighting, like selfgain.

Known thinness, stated honestly: need-name concepts are sparse in vivo
(hunger 43 records, thirst 26, curiosity 14, others <10 of 1559), so the
gain will fire rarely at first. Sparse firing is inert-not-broken, the
same precedent as habit-stale-note and expectation-closure-note. Concept
tokens are raw words, so a need name can appear incidentally; the gain is
flat and bounded, so incidental matches cannot dominate.

## Fitness function (synthetic /tmp stores only, never the live store)

1. Paired synthetic stores identical except one record's concepts
   (pressing-need name vs a neutral control word), with that need pressing
   → the need-matching record ranks strictly above the control, and is
   admitted to the 16-window in a case where the control is not.
2. Flatness: a record matching one pressing need vs three pressing needs
   → identical gain (one +0.5, never scaled).
3. Non-pressing need name in concepts → zero gain; felt level settled
   (0/1) → zero gain; no felt-level provider → zero gain and windows
   byte-identical to pristine.
4. Non-boosted relative order preserved (purely additive).
5. No sidecar created or modified by the gain path; a read-only-command
   sweep and dream ticks leave all sidecars byte-identical (gain is
   view-time only; scope no-write assertions to the operation, per the
   genome note that CLI construction itself rewrites the payload row).
6. Determinism: identical sequences → identical windows.
7. Full suite green; engine + cartridge byte-identical.

## Revert signal

- Gain admits a record that dedupe/per-class caps deliberately excluded,
  with an existing test regressing.
- Need-matching records exceed 50% of the unpinned window over a day
  (dominance — the selfgain tripwire analogue).
- Any write from the gain path (sidecar created/modified by a view).
- Gain fires for a need that is not pressing (felt level < 2), or fires
  when no provider is supplied.
- Any existing test regresses.
- In vivo across the window the gain never firing is inert, not a revert
  trigger (sparse concept signal); a gain firing on a non-pressing need
  IS a revert trigger.

## Regression genome (critic checks item by item)

See `research/builder-critic-loop.md`. Most relevant here: shadowing
definitions; sidecar/state reset on reseed (no sidecar, but prove it);
read-only violations (scoped as in fitness 5); silent defaults (provider
absent → gain 0 is the *defined* behavior, documented, not a quiet zero);
dream/conduct isolation (gain must not touch dream paths); test-premise
drift (prove any red test's premise against measured behavior before
calling it a code defect); CLI construction is never mtime-silent.
