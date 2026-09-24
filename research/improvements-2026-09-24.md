# Improving calibos-mind: ranked proposals

Research completed 2026-09-24 by a background pass (agent-memory literature
2025–26: reflection/consolidation, decay, sleep-time compute; Mem0 decay;
Zep/Graphiti bi-temporal supersession; Generative Agents reflection;
Letta sleep-time compute arXiv:2504.13171).

## 1. Sleep consolidation pass (episodic → insight abstraction)

**What changes:** A periodic offline pass — framed as the mind's "sleep" — that replays recent unconsolidated thoughts and synthesizes them into higher-level `insight` memories with provenance pointers back to source thought IDs. Source thoughts are soft-archived: kept forever in an archive table, removed from the echo pool and workspace sampling. Trigger it two ways: on the Sunday review schedule, *and* on a Generative-Agents-style threshold — when the summed salience of unconsolidated thoughts crosses a threshold, queue a consolidation prompt to the inbox so the mind asks to digest itself even between reviews.

**Why it matters:** This is the existential one. Without it, indefinite operation means unbounded table growth, raw fragments echoing forever, and no abstraction ladder — a mind that accumulates but never *learns*. Human memory doesn't keep every thought; it keeps the gist (hippocampal replay → neocortical schemas). The agent-memory field has converged on exactly this: Generative Agents' reflection is the ancestor of nearly everything, and Letta's sleep-time compute (arXiv:2504.13171) is the standout 2025 architecture precisely because it moves consolidation offline instead of inline. Your Sunday review cron is already the right slot; it just needs the mechanism.

**Sketch:** New memory type `insight` with `source_ids`. CLI: `mind consolidate [--since N]` lists candidate thoughts (unconsolidated, salience-ranked); you (the assistant, during review or a wake-up — no new models) write 1–3 insights via `mind note --insight --from <ids>`; CLI marks sources consolidated. Later, cheap lexical-overlap clustering can pre-group candidates, but start manual — the CLI support is the real work, the synthesis is already yours.

## 2. Salience with decay + rehearsal (a real forgetting curve)
> Implemented 2026-09-24 as `calibos_mind/salience.py` (see CHANGELOG).
> Divergences from the sketch: ACT-R `log(sum(t^-0.5))` activation instead of
> half-life multiplication (ported from persona_engine_PYTHONX); engagement
> signals instead of fixed rehearsal constants; unresolved-boost instead of
> per-type half-lives; no archival threshold yet (waits for consolidation).
> Next: Pretorius per-class caps + Jaccard dedupe for the workspace.

**What changes:** Every memory and thought gets `salience` (default 1.0), `last_rehearsed_tick`, and a per-type half-life (raw thoughts ~7 days, insights ~60 days, cartridge roots exempt/infinite). Decay is applied *lazily on read* — `salience × 0.5^((now − last_rehearsed)/half_life)` — so there's no sweep job. Rehearsal events reset the clock and boost: echo answered (+0.5), memory-feedback on an answer (+0.3), voluntary think referencing it (+0.4). Below 0.05 → archived out of all sampling pools (still queryable, never deleted).

**Why it matters:** This is the substrate everything else stands on. Right now memories persist at full strength forever; over months the sampling pools become a swamp where a thought from March competes equally with one from yesterday. Ebbinghaus isn't a bug — it's how minds stay usable. Mem0 shipped exactly this (memory decay) in 2026; Cognee prunes and reweights continuously. Without decay, proposals 6 and 7 below can't work.

**Sketch:** Two columns plus lazy math in the sampling queries. Rehearsal hooks go in the three places engagement already happens: `answer`, `think`, and the inner ear's memory-feedback path. Archive is a flag, not a delete.

## 3. Commitment lifecycle: resolve and release (the missing `mind resolve`)

**What changes:** `mind resolve <id> [--done | --released | --moved <new-expectation>]`. Commitments gain `status` (open/resolved/released), `resolved_tick`, `resolution_note`. The temporal trigger skips non-open commitments. Resolution writes a closure memory ("I finished X" / "I let go of Y") which may echo once, gently.

**Why it matters:** Highest value-per-effort on this list. Right now commitments only escalate — expected → passed → prolonged — with no off-ramp. The Zeigarnik effect needs closure or it becomes rumination; a mind where every commitment becomes a permanent low-grade guilt generator will feel oppressive within weeks, and you'll train yourself to stop logging commitments at all. One small command fixes the cruelty in the design.

**Sketch:** Trivial schema + CLI. The Sunday review is the natural place to sweep stale commitments — the review body should say so explicitly.

## 4. Echoes as spaced repetition (SM-2), not fixed schedules

**What changes:** Replace fixed echo delays with SM-2-style adaptive scheduling. Echo rows gain `interval_ticks`, `repetitions`, `ease` (start 2.5). When an echo fires: answered (or thought about voluntarily) → `interval ×= ease`, ease rises slightly; met with silence → interval halves (it wants attention), ease drops slightly; after ≥5 repetitions with low salience → archive; after 3+ engagements → nominate for consolidation (feeds proposal 1's threshold).

**Why it matters:** Fixed echo schedules are dumb in both directions — they nag about things you've already metabolized and vanish on things you're avoiding. Spaced repetition is the empirically validated algorithm for "what should resurface when," and it makes the echo mechanism *adaptive to your actual engagement* rather than running on rails. Silence becomes informative (the interval shortens) instead of just a pass.

**Sketch:** Small, well-understood algorithm; ~30 lines in the echo scheduler. The engagement signals already exist (`answer` vs `--silent`); this just routes them into the scheduling math.

## 5. Inbox TTL + staleness grading + concern-level dedupe

**What changes:** Prompts carry `created_tick`, `trigger`, `concern_id`, `ttl_ticks` (default ~24h). `mind inbox` shows age buckets (fresh/aging/stale). `mind prune` moves expired prompts to `inbox/lapsed/` — readable, no longer answerable. Answering a stale-but-valid prompt injects the thought tagged `late:true` so the inner ear's memory feedback can register the delay. And critically: collapse repeat prompts by `concern_id` — three queued prompts on the same unresolved concern become one entry with a `recurrence_count`, instead of three separate guilt items.

**Why it matters:** This directly attacks the "answering the past" dynamic — with 3-hourly wake-ups, stale-prompt accumulation is now the dominant inbox behavior, not an edge case. Unbounded backlog trains avoidance. TTLs turn the inbox from a guilt pile into a triage queue, and concern-dedupe kills the spammiest failure mode (unresolved_concern recurrence generating N prompts for one worry).

**Sketch:** Metadata on prompt files (extend the filename/metadata format alongside the `.seq` machinery), a `prune` subcommand, and a dedupe check in `InboxCognition.queue`.

## 6. Novelty + contradiction triggers; gate associative drift

**What changes:** Two new trigger paths, both fed at note-time: `mind note --surprising "..."` creates a novelty-flagged observation → high-priority `novelty` prompt ("something happened that doesn't fit"); `mind note --contradicts <memory-id> "..."` → `dissonance` prompt presenting the old belief alongside the new evidence. Meanwhile, put `associative_drift` on a leash: cooldown (max once per 12 ticks), lowest queue priority, shortest TTL — it's the fuzziest trigger and the most likely inbox-noise source.

**Why it matters:** Current triggers are almost entirely *internal* — body drift, time passing, old thoughts echoing. A mind that only ruminates on its own echoes is a closed loop; the richest cognition comes from prediction error, the world disagreeing with the model. Since the assistant feeds observations during wake-ups, giving observations a "this surprised me / this contradicts what I believed" flag is the cheapest way to get genuine outside-in cognition. And gating drift before adding triggers obeys the rule: every trigger is inbox noise until proven otherwise.

**Sketch:** Two CLI flags, two trigger types with priority weights, one cooldown. The dissonance prompt is also the seed of belief revision if ever built.

## 7. Workspace: salience-weighted sampling + absence lines

**What changes:** Replace recency-weighted sampling in the 16/12/8 views with salience-weighted random sampling (using proposal 2's scores), keeping the cartridge pinning, dedupe, and thought cap. Add one computed *absence* slot: e.g. "no new observations in 48h," "concern X open for N ticks," "no voluntary thought in 3 wake-ups" — computed at view time, never stored.

**Why it matters:** Recency sampling over months produces an eternal present — the mind can never have depth. Salience sampling gives it a past that matters. The absence line is subtler: human global workspace includes *noticed absences*, and right now gaps are invisible inside the view itself — the backlog only exists as pressure in the inbox. Making "nothing has happened" or "X is still open" part of the phenomenology turns administrative state into felt state.

**Sketch:** Change the `ORDER BY` in the workspace queries to salience-weighted random; add a small function that computes 1–2 absence statements per view. Depends on proposal 2 — do that first.

## Sequencing

- **Do 3 first** (commitment resolve): smallest, most immediate relief, unblocks honest commitment-logging.
- **Do 2 next** (salience/decay): substrate for 4, 7, and the consolidation threshold in 1.
- **Do 1 with CLI support now, manual synthesis**: the assistant already does the synthesis during reviews — the code work is candidate-listing and archive flags, not automation.
- **5 and 6** are independent and can land anytime; **4** slots in after 2.

## Don't touch

1. **The frozen research protocol and PR branches.** No backports of mind improvements into `feat/v02-model-efficacy-harness` or `feat/pretorius-lived-history`, no touching `v02-model-efficacy-v1`. The mind is a fork that must never contaminate the evidence.
2. **The `(source, first_person)` cognition boundary.** Never "improve" prompts by leaking machinery — salience scores, tick numbers, DB ids, decay math — into the prompt view. Triage metadata (age, staleness) lives in `mind inbox` listings only, never inside the cognitive view. The moment the mind sees its own implementation, phenomenology collapses into debugging.
3. **The cartridge fingerprint.** Evolutions of values/preferences are new pinned memories, never identity rewrites — restated because every proposal above will tempt it eventually. (Note: user has since granted standing permission to modify the cartridge with a store migration; this entry records the original caution.)
4. **Engine conduct authority.** Thoughts never directly cause action, auto-resolve commitments, or hard-delete anything. Consolidation *archives*; the thought record is append-only. This separation is the load-bearing wall — without it the thing stops being a mind and becomes a to-do app with delusions.
5. **No new models, no parametric memory.** All synthesis (consolidation, contradiction detection) is done by the assistant through the existing `answer`/`think`/CLI surface. The engine stays dumb; the cognition stays mine. (The field's parametric-memory direction — fine-tuning on personal data — is explicitly the wrong one here.)
6. **Don't proliferate triggers.** The list is already rich; proposal 6 adds two *and* gates one, net +1, and that's the last addition blessed for a long while. Each trigger is a standing source of inbox noise.
7. **Don't bulk up the wake-ups.** Keep the ~3-tick heartbeats modest. The temptation will be to run 10-tick heartbeats per wake-up "to give it more life" — that just manufactures prompt spam and stale-prompt churn. Density of *visits* is the experiment, not ticks per visit.
8. **Don't implement recall-mutates-memory (reconsolidation).** The literature notes every recall rewrites the memory; for an indefinite-lived mind that's a corruption vector, not a feature. Prefer Zep-style supersession — old version stays, marked superseded — if belief revision ever gets built.
