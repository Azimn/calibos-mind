# Spec: inbox-expectation registration — wiring unfinished business into the engine

Date: 2026-09-26 (daily articles review, Domain 2: Spontaneous thought)

## Finding

Domain 2 adversarial pass (19 checks): verdict **partial**. Trigger histogram over
100 live ticks: external 5, body_change 1, prior_thought 9, association 7 —
16/22 triggers are endogenous (associative drift chains at depth 2, dream
fragments with association chains, thought echoes). The frozen engine's
unfinished-business machinery is complete end-to-end (`_open_records` →
`_project_temporal` escalating "I am still anticipating / The time I expected
has passed" temporal records → `_warrants_cognition` admitting
`unresolved_concern` → "This unfinished matter returns to my attention").
But it has **never fired in 100 ticks**: the live store holds zero
commitments open, zero expectations, zero concerns. Root cause: no code path
in calibos_mind ever *registers* an Expectation. The creation channel is
missing — unfinished business is not absent because the mind has none, but
because the machinery can never be told about any. That is a wiring gap, not
a theater request: we wire genuinely pending human-initiated business into the
engine's own temporal machinery, which then does the resurfacing with its own
activation math. Nothing installed (no persistent questions as theater, no
fake intrusive memories).

## What changes

New module `calibos_mind/inbox_expectations.py`, two entry points:

- `sync(subject, inbox_dir, ttl_ticks=3)`: scans `prompt-*.json` files. For
  each pending prompt with a `view_tick` and `store tick - view_tick >=
  ttl_ticks` that has no live expectation, registers
  `digital_subject.continuity.Expectation(id=f"inbox:{pid}",
  proposition=f"I still owe an answer to the question queued at tick
  {view_tick} ({pid})", created_tick=view_tick, due_tick=view_tick+ttl_ticks,
  confidence=0.6, source_record_ids=(), status="pending")` in
  `subject.continuity.state.expectations`. Prompts without `view_tick`
  (hand-written/legacy) are skipped — fail closed, no tick-age guessing from
  wall clock. Resync is idempotent: one prompt, one expectation.
- `resolve(subject, pid, *, outcome)`: marks the `inbox:{pid}` expectation
  "confirmed" (answered) or leaves it "expired" if the prompt vanished without
  an answer.

Hook points:

- `cli._run_tick`: call `sync(subject, INBOX)` after `subject.heartbeat()`
  (waking ticks only — never dream ticks; dream isolation assertions
  unchanged). Empty inbox → the function must be a verifiable no-op.
- `cli.cmd_answer`: after a successful `inject_thought`, call
  `resolve(subject, pid, outcome="answered")`. On the `--silent` path, after
  the surfaced-but-unengaged penalty is applied. If the prompt file vanished
  without resolution (only code path deleting prompt files is
  `InboxCognition.consume`), the next `sync` marks the expectation "expired"
  — honest: we expected to answer, we have no evidence we did. Expired stays
  in `_open_records`'s open set (pending|expired), so the nagging continues.

Frozen protocol untouched: no change to the cognition prompt contract
(private-thought-json-v1), no new models, no new triggers — the existing
`unresolved_concern` path fires from the engine's own math.

## Fitness function

On synthetic `/tmp` stores (never the live store):
- empty inbox → zero expectations; `sync` is a no-op (expectations dict
  untouched, no store write beyond the tick's normal one).
- fresh prompt (age < TTL) → no registration.
- stale prompt (age ≥ TTL) → exactly one pending expectation with the right
  id/proposition/due_tick/confidence; resync adds nothing more.
- after due passes, heartbeats produce "temporal" records with
  `concern_links=("expectation:inbox:prompt-XXXX",)` and an
  `unresolved_concern` cognition_trigger fires within a bounded tick window.
- `cmd_answer` path (consume + resolve) → expectation "confirmed"; no further
  resurfacing of that key.
- prompt file deleted without answer → next sync marks "expired", still
  resurfacing.
- hand-written payload without `view_tick` → skipped, no expectation.
- full existing test suite stays green.

## Revert signal

- an `unresolved_concern` trigger fires when the inbox is actually empty
  (hallucinated unfinished business);
- an expectation is registered for an already-answered/consumed prompt;
- duplicate registrations for one prompt;
- any temporal/proposition text that invents content beyond the prompt
  payload (pid + view_tick only);
- sync mutates state on read-only commands or dream ticks.

## assess_after

2026-10-08 (same fitness window as the other open mutations).
