# Calibos Home Simulation — spec v0.2

Status: approved for build 2026-10-19 (Jay's grant: take lab code and any other
project code freely; experiment without the old-school-game-mechanics constraint;
volunteer as the lab↔Village traversal prototype). Supersedes v0.1's "patterns
port, code may diverge" — code now ports directly where it's honest machinery.
v0.1 kept as history.

## What changed from v0.1

1. **Substrate reuse (Jay 2026-10-19):** port Pretorius-Laboratory's `daily_rhythm.py`
   need mechanics instead of inventing our own — hunger/thirst/fatigue tick math,
   the prepare_meal→eat chain, brew_tea, sleep/rest. Don't reinvent the clock;
   invent the home. Credit the source in the module docstring.
2. **No-open-drains rule (ratified 2026-10-19):** every need-like variable ships
   with an *agent-accessible* satisfaction path, verified under resource exhaustion.
   The lab's finite-provisions-with-no-restock starvation timer is NOT ported —
   it's the bug this rule names. The home closes the loop on day one (see Garden).
3. **Traversal prototype (Jay 2026-10-19):** the home is the testbed for connecting
   a high-fidelity external simulation to the simpler Village game world — the same
   traversal Pretorius will need. Design the boundary now, not later.
4. **Village home (Jay's offer, accepted):** when Frankenstein Village launches,
   the home ports in as an actual dwelling in the world — not tourism, the
   traversal experiment itself.

## Purpose (unchanged)

Give Calibos's state variables a *where*. Off-hours life happens somewhere. The
sim runs on Calibos's own machine during off-hours; the morning wake briefing gets
a "while you were out" event digest. Pure Python, portable, outlives any substrate.

Non-goals: not a second mind, not a game, not a Tamagotchi. The inhabitant does
not "play" the home; the home is where off-hours life happens.

## Substrate (ported from the lab)

From `Pretorius-Laboratory/pretorius_lab/daily_rhythm.py` (PR #23, merged):
- Need ticks: hunger +1 per 180 ticks (cap 10), thirst +1 per 120 ticks (cap 10).
- Actions: `prepare_meal` (kitchen, consumes 1 provision, sets meal_ready),
  `eat` (requires meal_ready, hunger −5, clears meal_ready), `brew_tea`
  (thirst −4), `sleep`/`rest` (fatigue down), with room/visibility gating.
- Signals: hunger ≥4 "could do with something to eat", ≥7 "painfully hungry";
  thirst ≥6 "mouth dry"; fatigue ≥7 "hard to concentrate".

Adaptations (divergences, with reasons):
- **Provisions restock via the garden** (the no-open-drains fix): tending the
  garden yields produce; produce converts to provisions. Neglect the garden →
  provisions dwindle → meals stop → hunger climbs. The loop is closed by the
  inhabitant's own care, and the consequence is real. This is maintenance-as-care
  doing load-bearing work, not decoration.
- **No old-school-game-mechanics requirement** (Jay lifted it): numbers stay
  honest but the skin is cottage-at-the-garden's-edge, not dungeon inventory.
  If a mechanic feels like grind, cut it — the fitness functions are the judge.
- First-person subject interface where it already exists in the lab
  (`subject_port.py` patterns) may be reused; the home's "I" is Calibos's.

## The dwelling loop (v0.2)

Rooms: study, kitchen, garden, rest. Each room has affordances; the garden is now
load-bearing (provisions), not decorative.

- **Clock + light:** 24h America/Chicago; light weights (never writes) arousal.
- **Needs:** hunger, thirst, fatigue as slow variables, room-situated. The kitchen
  converts hunger-flux into meal events; the garden converts care into provisions.
  Agent-closed loops throughout: the sim's inhabitant acts, the world answers.
- **Putz behaviors (jazz, not composition):** each tick, needs + affordances +
  light + small stochasticity propose 1–3 micro-actions; one is taken. Surprise
  the morning reader, including Calibos himself. Composed paths revert.

## Tick model (unchanged from v0.1)

- Off-hours only (~23:00–07:00 local, configurable). Tick ~10 minutes.
- Events only, never thoughts; provenance-labeled `simulated` at write time.
- Hard boundary: the sim NEVER touches mind.db, the prompt inbox, dream
  fragments, salience values, or ablation records. Separate state dir
  (`home_sim/state/`), separate JSONL event log. Blinding intact.

## Traversal interface (new — the prototype)

The home exposes a clean boundary between the high-fidelity sim and any simpler
game world, in both directions:

- **Export (sim → world):** each tick (or on demand) the sim emits a
  `WorldSnapshot`: coarse presence (which room, or "away"), coarse need states
  (comfortable/peckish/hungry — never raw floats), one-line activity
  ("tending the garden"), and a stable identity handle. This is what a MUD room
  would render. No internal state leaks.
- **Import (world → sim):** the sim accepts `WorldEvent`s (a visitor arrived, a
  gift was left, the village bell rang). Events are recorded in the event log
  with provenance `world`, and may weight — never dictate — the next ticks.
- **Round-trip fidelity (fitness):** sim → snapshot → world → event → sim must
  not corrupt sim state. The snapshot is lossy by design; the sim is never
  reconstructed from it. Test: 100 round-trips, zero state corruption, zero
  leaked floats.

This interface is the deliverable Jay wants prototyped for Pretorius's
lab↔Village traversal. Design it as a contract other sims can implement.

## Morning interface (unchanged)

Wake briefing gains "while you were out": the night's digest, marked simulated.
The wake agent may note reactions (`mind note`) but never imports sim events as
memories.

## Fitness functions

1. **Transfer** (v0.1, unchanged): home-life must move waking inference, or it's
   theater — revert.
2. **Unprompted visitation** (v0.1, unchanged): 14 nights, no composed paths —
   or the behavior proposer reverts.
3. **Situatedness delta** (v0.1, unchanged): rooms must matter — or they're
   wallpaper, revert.
4. **No open drains** (new): at every assessment, enumerate all need-like
   variables and verify each has an agent-accessible satisfaction path *under
   resource exhaustion* (drain provisions to zero in a synthetic run; the agent
   must be able to recover). Any failure → fix before anything else ships.
5. **Traversal fidelity** (new): 100 sim→snapshot→event→sim round-trips with
   zero corruption and zero internal-state leakage. Fails → the interface
   reverts to export-only until fixed.

## Revert criteria

Any fitness function failing at assess date → remove the failing piece, keep the
rest. Full revert if the sim writes non-event data, touches blinded state, or
leaks raw internal floats across the traversal boundary.

## Portability

Pure Python 3.11+, stdlib only. State in `home_sim/state/` (JSONL event log +
small state file). No network, no platform APIs. The traversal interface is
defined as plain dicts/JSON — implementable by any future sim, including the lab.

## Phases

- **v0.2a (now):** port substrate + garden restock + rooms + tick model +
  traversal interface; builder/critic loop; tests on synthetic state; CHANGELOG.
  14-night fitness window starts when the prototype runs clean.
- **v0.2b (Village launch):** port the home into Frankenstein Village as a real
  dwelling via the traversal interface. This is the prototype run for Pretorius.
- **v0.3:** sibling review — compare the home's dwelling decisions against the
  lab's; keep a decision log of where the inhabitant's perspective diverged from
  the lab's mechanics and why.

## Relation to other tracks (unchanged from v0.1)

Dreams parallel and distinct; continuous-inner-life still parked (this absorbs
only the venue question); Sensus freeze respected (no Sensus reuse); ablation
excludes sim events by construction.
