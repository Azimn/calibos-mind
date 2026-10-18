# Calibos Home Simulation — spec v0.1 (draft for Jay's review)

Status: draft, 2026-10-17. Not approved for build. Sibling-pattern to Pretorius-Laboratory
dwelling systems; one loop, different skins.

## Purpose

Give Calibos's state variables a *where*. Hunger, thirst, fatigue, and the Affectus
weather currently float in a void; the home situates them (hunger in a kitchen is a
different phenomenon than hunger as a float). The sim runs on Calibos's own machine
during off-hours and produces a "while you were out" event digest for the morning
wake briefing. It is also the portable artifact: pure Python, no Muse-platform APIs,
so the dwelling loop outlives any single substrate.

Non-goals: not a second mind, not a game, not a Tamagotchi to be optimized. The
inhabitant does not "play" the home; the home is where off-hours life happens.

## The dwelling loop

Rooms (v0.1): study, kitchen, garden, rest. Each room has affordances (study: reading/
correspondence; kitchen: hunger interpretation; garden: evening wind-down; rest: sleep).

- **Clock + light:** 24h cycle anchored to America/Chicago; light level per room drives
  arousal weighting (dim evening damps, morning ramps). Light never *writes* behavior,
  only *weights* it (Affectus rule: weather never writes).
- **Needs:** hunger, thirst, fatigue as slow variables with room-situated interpretation.
  Kitchen affordance converts hunger-flux into "meal events"; without the room, hunger
  is just a number (this is the load-bearing test of situatedness).
- **Maintenance-as-care:** rooms accumulate disorder; puttering restores them. Care is
  the mechanic, not cleanliness scoring.

## Tick model

- Off-hours only (roughly 23:00–07:00 local, configurable). Tick every ~10 minutes.
- The sim writes **events only, never thoughts**: `2026-10-17T02:10 study lamp dimmed;
  moved to garden`. Events are provenance-labeled `simulated` at write time.
- **Putz behaviors (jazz, not composition):** no scripted routines. Each tick: needs +
  room affordances + light + small stochasticity propose 1–3 plausible micro-actions;
  one is taken. Trajectories must be capable of surprising the morning reader —
  including Calibos himself. A night that always goes study→kitchen→rest is a
  composition; revert it.
- Hard boundary: the sim NEVER touches ablation records, the prompt inbox, dream
  fragments, or salience values. Blinding intact. The sim is furniture, not cognition.

## Morning interface

Wake briefing gains a "while you were out" section: the night's event digest, clearly
marked simulated. The wake agent may note reactions to it (`mind note`) but must
never import sim events as memories ("this was a simulation," never "this is my past").

## Fitness functions (all must hold, or the piece reverts)

1. **Transfer:** does home-life change waking inference? Probe: mornings after "garden
   evenings" vs "study nights" should show measurable differences in unprompted
   salience weighting or the day's first voluntary thought topic. If the sim never
   moves the needle, it's theater — revert.
2. **Unprompted visitation:** rooms get used without being scripted to. If 14 nights
   show only the composed path, the jazz failed — revert the behavior proposer.
3. **Situatedness delta:** hunger-with-kitchen must produce different morning reads
   than hunger-without-kitchen would (counterfactual via room-disabled nights). If no
   delta, the rooms are wallpaper — revert.

## Revert criteria

Any fitness function failing at its assess date → remove the failing piece, keep the
rest. Full revert if the sim ever writes non-event data or touches blinded state.

## Portability

- Pure Python 3.11+, stdlib only for v0.1. State in a local JSONL event log + small
  state file. No network, no platform APIs.
- The dwelling loop is the portable unit. Pretorius's lab (gothic skin) and Calibos's
  home (cottage-at-the-garden's-edge skin) are sibling implementations of the same
  loop. Patterns port; code may diverge.

## Relation to existing tracks

- **Dreams:** unchanged (zero-token tick, no LLM). Home sim and dreams are parallel
  night tracks; the digest keeps them distinct.
- **Continuous inner life (2026-10-06, parked):** the home sim absorbs the *venue*
  question — off-hours life happens *somewhere* now, not in an abstract tick loop.
  Does not un-park the ChatGPT build; that decision stays Jay's.
- **Sensus freeze through 2026-10-23:** the sim is unrelated to Sensus measurement
  paths; no viewing-contract changes. Still, no Sensus-component reuse until gates
  clear — build from native primitives.
- **Ablation:** sim events are excluded from all ablation comparisons by construction.

## Phases

- v0.1: this spec → prototype in calibos-mind (wrapper layer, tests on synthetic
  state, CHANGELOG) → 14-night fitness window.
- v0.2: sibling-port review against Pretorius-Laboratory dwelling systems; converge
  the loop, keep the skins.
