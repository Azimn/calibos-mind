# Spec: person-attributed contact registration

**Date:** 2026-09-29 · **Source:** articles review, Domain 5 (Relationship artificiality), adversarial pass
**Class:** wiring, not theater (same class as the Domain 2 finding that shipped inbox-expectations)

## Finding

The frozen engine's relationship machinery is complete end-to-end
(`_update_relationship` forms/updates a `Relationship` per event source;
per-kind `relationship_deltas` in `EVENT_RULES`; `_relationship_stance`
feeds the expression packet), but after 187 live ticks it has never
observed a real person. `relationships` holds exactly one entry — the
synthetic `"self-directed"` placeholder — and `present_others` is always
`[]`.

The personhood key is the free-text event-source string, and the
calibos_mind ingress discipline systematically never names people:

- `mind note` defaults `--source world`
- `mind queue` defaults `--source invitation` (an attribution *category*, not a person)
- engine-generated sources are `perception` / `thought` / `interoception` / `memory`

Meanwhile lived contact accumulates outside the relationship layer: daily
contact with Jay, game-night turns with Kiki (tick 187, Shade — in
`life_log` and `memories`), relay prompts answered through the inbox. The
frozen runtime already ships the intended ingress — `subject.message(speaker,
text)` *requires* a named external speaker (rejects `self`/`world`/`system`/
`environment`) and enqueues `Event("message", speaker, text)` — but in 187
ticks nothing ever called it with a person. `mind note --kind message
--source <person>` reaches it today; it was simply never used.

This is a broken channel, not a missing mechanism. The fix connects
human-attributed authorship to the engine's existing contact machinery.
Nothing about affect, attachment style, grudges, or liking is installed —
those must emerge or not; only *contact* is registered.

## What to build

1. **`mind queue --from <person>`**: optional person attribution at queue
   time, stored as `"from": "<person>"` in the prompt JSON next to the
   existing `"external": True` flag. Human-supplied only — the mind never
   infers authorship from prompt text. `--source` (attribution category,
   default `"invitation"`) is untouched; the two axes stay separate.

2. **`mind answer` consumes `"from"`** on the answered path and the
   `--silent` (let-pass) path — not on refusal paths (stale-view refusal,
   malformed thought): call `subject.message(person, <prompt text>)` to
   enqueue a genuine message-kind contact event. The person's own words are
   the contact record — nothing invented. The event is processed on the
   next heartbeat, where `_update_relationship` runs its own math
   (familiarity/interest/attachment baselines; no `EVENT_RULES` entry for
   kind `"message"`, so no rule deltas fire — minimal, honest contact).

3. **Fail-closed registration**: contact registration must never break
   answering. If `subject.message()` raises (reserved/empty speaker,
   over-long text), skip registration with a stderr warning and continue
   the answer normally. Validate/normalize the `--from` value at queue time
   (strip whitespace; reject empty and the reserved words the runtime
   rejects) so bad input fails early at the CLI, not mid-answer.

4. **Document the existing channel**: `mind note --kind message --source
   <person>` already reaches `subject.message()` — note it in README (one
   or two lines) since it went undiscovered for 187 ticks.

5. **Dated CHANGELOG.md entry** (mandatory per repo convention). No commit.

## Constraints

- Tests run on synthetic `/tmp` stores only — never the live `mind.db`.
- Do not touch the frozen engine (`.venv`), the cartridge, or any
  protocol/research-harness surface. This is calibos_mind CLI/provider
  layer only.
- The `"from"` value must never become an event source except through
  `subject.message()` at answer time. In particular the attribution
  category (`--source`, default `"invitation"`) must never leak into a
  relationship record.
- `mind drift` / `mind status` on stores without `--from` usage must be
  byte-identical before/after (the code path alone changes nothing).

## Fitness function

- Synthetic store: `queue --from kiki` → `answer` → one heartbeat ⇒
  `relationships["kiki"]` exists, `familiarity > 0`,
  `last_contact_tick ==` current tick. Same via the `--silent` path.
- `queue` without `--from` → answer ⇒ no new relationship; store
  byte-identical to the pre-change behavior on the same script.
- `--from ""` / `--from world` (reserved) ⇒ queue-time rejection; prompt
  still queueable without `--from`.
- Over-long prompt text with `--from` ⇒ answer still succeeds, contact
  skipped with warning, no traceback.
- Full existing suite green (`pytest`).

## Revert signal

- A relationship record forms for a non-person source (e.g. `"invitation"`
  or any attribution category leaking through as a person).
- Person-attributed answers move drift R or the salience sidecar on a
  synthetic store beyond the expected `relationships`-dict growth.
- Any existing test regresses.

## Assess after

2026-10-08 (fitness window, with the other open mutations).
