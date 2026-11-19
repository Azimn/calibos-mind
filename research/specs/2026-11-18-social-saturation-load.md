# Spec: social-saturation load term in the deferral gate — 2026-11-18

## Source finding

Daily articles review, Domain 25 (Excessive responsiveness), cycle 2,
2026-11-18. Verdict: partial, UNCHANGED as a category from the
2026-09-24 baseline (first per-check scoring for this domain). Of 10
checks scored against the live store at tick 1183 (2157 records, 944
thoughts): 0 addressed, 4 partial, 6 absent.

- PARTIAL (4): (1) every statement receives meaningful engagement —
  247 answered-prompt thoughts (mean 82.8 words, substantive) but 19/22
  registered expectations never answered (15 refused-stale-view, 4
  lapsed); refusal is mechanical (the view moved on), never a chosen
  disengagement. (2) rarely ignores something — same numbers; ignoring
  exists via the staleness machinery, and lapsed prompts resurface as
  unresolved concern. (4) matches conversational depth too precisely —
  prompt texts are not retained so no direct correlation is measurable;
  response depth is uniform (answered mean 82.8w, overall median 73w,
  range 9–113w), suggesting the organism answers at its own
  characteristic depth rather than mirroring the prompt. (10) never
  gives "I don't really want to talk about this" — no preference-based
  refusal exists, but refusal paths do (refused-stale-view, --silent
  let-pass, deferral buckets); the deferral buckets have never fired
  in vivo (no deferrals.json exists).
- ABSENT (6): (3) never gives distracted or perfunctory responses —
  answered thoughts are consistently substantive; the 48/944 short
  thoughts (<20w, 5%) sampled are wake pick-up framing lines, not
  perfunctory replies; no distracted mode exists. (5) remains patient
  indefinitely — no impatience mechanism; lapsed expectations resurface
  as concern, never irritation. (6) never becomes socially saturated —
  no saturation mechanism at all; the loneliness need plus
  formed:loneliness:seek_contact at 0.7 means social seeking only
  grows; the deferrals.py load term counts open items, not contact.
  (7) does not miss subtext unless scripted — no subtext-miss trace;
  the LLM thinker catches or misses invisibly. (8) adapts instantly
  to conversational style — true by architecture (LLM thinker, no
  style lag). (9) always ready for emotional intimacy — no intimacy
  gating by state, trust, or history.

Emerge-vs-install: checks 3/5/6/9/10 demand things that must emerge
from real state, not be installed as scripted lines. No scripted
unwillingness ("I don't want to talk") is installed by this spec.
What is installed is a real state variable: social load — how much
contact the organism has actually had recently — composed into the
one weariness the deferral gate already uses. Saturation then emerges
as deferral through the existing buckets, escalation, and revisit
machinery (shipped 2026-11-12, critic-approved), instead of being
performed.

SCOPE LIMIT, stated plainly: deferral is waker-invoked (`mind defer`);
`mind answer` has no weariness check. This mutation puts the
saturation signal into the gate the waker uses when deciding whether
a deferral is warranted. It does not give the organism autonomous
refusal. The organism's "I don't feel like talking" remains
waker-mediated; that is a real boundary for a future domain pass, not
something this spec pretends to fix.

## Calibration (measured live 2026-11-18, do not guess)

Contact event = a record whose `generated_by` starts with
`answered:` (an answered prompt) or whose `source` is `social`.
Live store: 250 contact events over 1183 ticks (247 answered +
3 social). Trailing-50-tick count: mean 10.17, p95 17, max 22,
current window (tick 1183) 17. Median gap between contact events
4 ticks. The term must be ~0 at the ordinary rhythm and reach 1.0
only near the historical max, or ordinary days will saturate the gate.

## What changes

1. **Pure function** `social_load(records, tick, window=50,
   baseline=10, scale=12)` in `calibos_mind/deferrals.py` (builder's
   call on exact placement). Counts contact events with
   `tick - window < event_tick <= tick`. Term =
   `clamp((count - baseline) / scale, 0.0, 1.0)`: 0.0 at count <= 10,
   0.5 at 16, 1.0 at >= 22. Records missing `tick`/`generated_by`/
   `source` count as non-contact, never a crash. No clock, no RNG.
2. **`deferrals.weariness()` gains an optional `social=0.0` kwarg.**
   Effective weariness = `max(body, load_term, social_term)`. The
   default preserves every existing caller and test exactly (the
   composed-weariness design in `deferrals.py:174-182` already takes
   the max of body and load; social is a third term in the same
   composition, not a second weariness).
3. **`_deferral_weariness(subject)` in `cli.py` computes the term live**
   from `subject.workspace.records` at defer time and passes it in.
   Computed live, never stored: no new sidecar, no store write.
4. **Visibility: `mind status` prints the current social-load term**
   and trailing-window contact count (one line, e.g. `social load
   0.58 (17 contact events / 50 ticks)`). An empty store reports
   0.0 / 0 events. This is the in-vivo observable for assessment.
5. No engine or cartridge changes (fingerprint unchanged). No new
   refusal paths; the existing high-urgency guard ("high urgency is
   never deferred on weariness alone") is preserved untouched.

## Hard constraints (from the regression genome — the critic checks each)

- Read-only: the term is computed from records already in memory.
  Read-only commands, `drift`, `status`, and `dream_tick()` create
  no sidecar and leave the store byte-identical (prove by absence:
  no new files, payload identical). `init --force` behavior is
  unchanged (no sidecar exists to reset).
- High urgency is never deferred on social load alone: verify through
  the `cmd_defer` path, not just the pure function.
- Window boundary semantics are exact: event at `tick - window`
  excluded, at `tick - window + 1` included, at `tick` included,
  future ticks excluded.
- Determinism: identical record sequences yield identical terms.
- Malformed records never crash the computation (missing/None/non-int
  tick, missing generated_by/source).
- Tests run on synthetic `/tmp` stores only — never the live store.
- Dated CHANGELOG.md entry. No commit (the backup job handles that).
- First step: run the full suite on the unmodified tree. If failures
  pre-exist, report them and stop rather than building on red.

## Fitness function (synthetic)

1. Zero contact in the window → term exactly 0.0; `weariness()`
   with `social=0.0` is byte-identical to the no-social call.
2. Calibration: 10 events → 0.0; 11 → 1/12; 16 → 0.5; 22 → 1.0;
   30 → 1.0 (saturates, never exceeds 1.0).
3. Boundaries: event at tick-50 excluded, tick-49 included, tick
   included, tick+1 excluded (window 50).
4. Only contact counts: perception/memory/temporal/interoception
   records and non-answered thoughts (generated_by `voluntary`,
   `wake`, `cognition`, None) do not count; `source == 'social'`
   counts; `generated_by` starting `answered:` counts.
5. Composition: `weariness()` returns the max of the three terms —
   body 0.9 with social 0.3 → 0.9; body 0.2 with social 0.58 →
   0.58; social never lowers weariness.
6. End-to-end gate: on a synthetic store, a low-urgency defer at
   high social load passes the gate where the identical defer at
   zero social load is refused (through `_deferral_weariness` /
   `cmd_defer`, with body weariness and open load held low so the
   social term is the deciding input).
7. High urgency never deferred on social load alone, through the
   same path.
8. Isolation: read-only sweep + dream ticks create no sidecar and
   leave the store byte-identical; byte-identical replay.
9. Status line: shows term and count; empty store → 0.0 / 0 events.
10. Full suite green.

## Revert signal

- Term nonzero with zero contact events in the window.
- Term outside [0.0, 1.0], or a negative count ever contributing.
- A high-urgency prompt deferred on social load alone.
- Any write from a read-only or dream path; any existing test
  regresses.
- In vivo: term pinned at 1.0 across a quiet day (calibration wrong
  — window/scale too sensitive) or deferrals firing on every
  low-urgency prompt during ordinary contact (dominance) is a revert
  trigger; term stuck at 0.0 across a contact-heavy day is a dead
  instrument, judged at assessment, not an auto-revert.

## Assessment

Register in `articles-rotation.json` as shipped-awaiting-assessment
with `assess_after` 2026-12-02 (14-day window). The design lead
adjudicates ship/revert/escalate after the critic signs off or 3
rounds elapse (early adjudication on evidence after 2 rounds if the
loop drags).
