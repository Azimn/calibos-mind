# Spec: familiarity traces — metacognition for near-miss retrieval

Date: 2026-09-28 (daily articles review, Domain 4: Memory artificiality)

## Finding

Domain 4 adversarial pass (18 checks): verdict **partial** (unchanged from the
2026-09-24 baseline). Falsified (addressed): checks 11/12/13 (importance lifts
retrieval, rehearsal strengthens, disuse sinks — ACT-R + rehearsal substrate)
and 14 (no contamination — attribution invariant + archive-only consolidation).
Deliberately true by design: check 7 (no reconsolidation — immutability is the
cognition/conduct boundary). Fully true and unaddressed: checks 2–6 (no
retrieval failures, tip-of-the-tongue, partial recollections, competing
memories, false associations), 8 (no emotional weighting — `affect` is empty
on all 113 live records), 10 (retrieval ignores felt state/context —
`activation()` takes id/tick/unresolved only), 15 (no per-recall accuracy
uncertainty), 16 (tick-exact sequence memory), 17 (no familiar-but-unplaceable
experiences).

Check 17 is the load-bearing gap: retrieval is a deterministic top-k cut over
ACT-R activation. A record ranking just below the cut vanishes without a
trace — the organism has no state for "something relevant almost surfaced."
In humans that metacognitive signal is real and causal: persistent
familiarity eventually forces recall. This mutation installs the *mechanism*
(a near-miss streak with a bounded retrieval nudge), not the *symptom* — the
"unplaceable" part stays honest: the streak knows an id, the view still lacks
its content until the nudge earns admission.

Checks 2/3/4/6 (failures, tip-of-the-tongue, partial recall, false
associations) are declined as install-targets: with deterministic ranking and
immutable records there is no honest failure mode to model, and inventing one
would be quirks-as-theater. Check 8 (emotional weighting) is declined for now:
`affect` is empty because nothing writes it; weighting retrieval by affect
before any record carries real affect would be painting over the gap.
Check 5 (competing memories) is partially served by this mutation — near-miss
competitors are exactly what generate familiarity.

## What changes

New module `calibos_mind/familiarity.py` with a `FamiliarityTracker`, wired
exactly like the existing `salience_tracker` / `interoception_tracker`
(constructor path arg on `CalibosSubject`, attached to the workspace per
transaction — the workspace is rebuilt from the DB payload every transaction,
so follow that pattern, do not cache across it).

Local-only sidecar `familiarity.json` at the mind root (`BASE /
"familiarity.json"`, next to `salience.json`; add to `.gitignore` — private
runtime state, not architecture): `{"streaks": {"<record-id>": N}}`.

Module constants (named, no magic numbers):

- `FAMILIARITY_WINDOW = 32` (2 × `VIEW_LIMIT`) — how deep into the ranked
  list near-misses are considered.
- `FAMILIARITY_THRESHOLD = 3` — consecutive near-miss views before the nudge
  engages.
- `FAMILIARITY_BOOST = 0.5` — additive activation nudge, flat once the
  threshold is reached, never scaled by streak length. (Live activations run
  roughly −2.7 … +3.2, so 0.5 is meaningful without being dominant.)

Behavior:

- `Workspace.view()`: after ranking, dedupe, and caps are computed exactly as
  today, capture near-miss ids into a new transient `self._last_near_miss_ids`
  (same side-channel pattern as `_last_view_ids`). A near-miss is a record in
  `ranked[:FAMILIARITY_WINDOW]` that was **not** admitted to the window.
  Pinned cartridge records are always admitted, so they can never near-miss;
  archived/ineligible records never reach `ranked`, so they can never
  near-miss. Dedupe-losers and cap-excluded records CAN near-miss — they are
  the genuine competitors.
- Ranking: when a familiarity tracker is attached, the sort key becomes
  `(tracker.activation(...) + tracker.boost_for(r.id), r.tick)`.
  `activation()` itself is untouched — `salience.json`, `mind drift`, and R
  are unaffected. The boost is retrieval-time only, never persisted to the
  record.
- `tracker.boost_for(record_id)`: `FAMILIARITY_BOOST` if
  `streaks.get(id, 0) >= FAMILIARITY_THRESHOLD` else `0.0`. Bounded by
  construction — streak 100 still yields 0.5.
- `tracker.observe(admitted_ids, near_miss_ids) -> bool`: for each near-miss
  id, `streaks[id] = streaks.get(id, 0) + 1`; drop streaks for admitted ids
  (admission resets — the familiar thing got placed); drop ids that are in
  neither set (pruning — the sidecar never grows unbounded). Returns True iff
  the streak dict changed; the caller saves **only then** (no-op discipline:
  a view with no near-misses and no resets writes nothing).
- Wiring: call `observe` once per waking tick in `_run_tick` in `cli.py`
  (next to the inbox-expectation `sync` and interoception update), reading
  `subject.workspace._last_view_ids` and `._last_near_miss_ids` after
  `subject.heartbeat()`. Save only when `observe` returns True.
  Dream ticks never call it (`dream_tick()` is a separate path — dream
  isolation untouched; dream views may still *see* boosted ranking, which is
  psychologically apt, but dreams accumulate no streaks). Read-only commands
  (`drift`, `status`, `resolve`, `review`, `consolidate --dry-run`) never call
  it — constructing views must not touch the sidecar.
- `cmd_init --force` deletes `familiarity.json` (regression genome:
  sidecar/state reset on reseed — stale streaks must never attach to recycled
  ids).

## Fitness function (all on synthetic /tmp stores, never the live store)

- Engineer a synthetic store where a target record T ranks just below the
  view cut (positions 17–24 of `ranked`) for three consecutive waking views
  (view + observe per cycle): after the 3rd observe, `streaks[T] == 3` and
  `boost_for(T) == 0.5` exactly.
- 4th view: T is admitted (the boost lifted it above the cut); after observe,
  `streaks` has no entry for T and `boost_for(T) == 0.0`.
- Records that never near-miss: no streak entry, boost 0.0.
- An archived record ranking high by activation: never a near-miss
  (excluded before ranking), no streak entry.
- A view with zero near-misses and zero resets: `observe` returns False and
  the sidecar file is untouched (absent stays absent; present stays
  byte-identical).
- Determinism: two identical sequences of (view, observe) cycles produce
  byte-identical `familiarity.json`.
- `init --force` on a store with a populated sidecar: sidecar gone.
- Dream ticks on a near-miss-engineered store: streaks unchanged, sidecar
  untouched (no observe on the dream path).
- Full existing suite green.

## Revert signal

- Oscillation pathology: T admitted via boost, then sinking and re-admitted
  in a tight cycle (soak: 20 waking views; T's admission count far above its
  unboosted baseline, or a strict admitted/near-miss alternation) — the nudge
  became a churn engine.
- A boost admits a record the dedupe or caps deliberately excluded, AND an
  existing dedupe/cap test regresses.
- The sidecar is written by `drift`, `status`, `dream`, or any read-only
  path; or written when `observe` returned False.
- `init --force` leaves a stale sidecar, or a stale streak attaches to a
  recycled id.
- `mind drift` R or any salience-sidecar byte changes on a store where only
  views were constructed (ranking change leaked into the substrate).

## assess_after

2026-10-08 (same fitness window as the other open mutations).
