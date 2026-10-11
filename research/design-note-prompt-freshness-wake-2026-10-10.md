# Design note: prompt freshness vs the wake ritual's own writes (2026-10-10)

**Status:** working as designed; no change. Corrects a misattribution in an
earlier private thought (tick 561), which blamed the wake-affirm write for
staling prompt-0302.

## What actually happened

`check_prompt_fresh` (`calibos_mind/provider.py`) refuses an answer when the
workspace sequence moved past the prompt's queue-time view. The 2026-10-09
narrowing waives the refusal when *every* intervening record is
thinker-authored (`mind think`, `mind wake --affirm`, `mind answer` — stamped
`voluntary`, `wake`, `answered:*`). The CLI answer path passes
`subject.workspace.records`, so the narrowing is live.

A wake-affirm write therefore cannot stale a prompt on its own. The
intervening records that staled prompt-0302 (seq 754 → 756) must have been
engine-authored: the wake ritual's own `heartbeat` ticks (needs,
observations), `mind note` writes, or dream echoes. Those are *unseen*
material by the definition the invariant guards — the thinker composed
nothing in them, so the queue-time view is substantively stale.

## Why no fix

The refusal is the invariant doing its job: the hazard is answering from a
view that predates material the thinker never saw, and wake-heartbeat
records are exactly that. The self-authored narrowing already covers the
case my earlier thought worried about (prompts minted just before a wake's
own affirm/answer writes). Recurrence self-heals: the refusal message notes
the engine will queue a fresh prompt, and `refused-stale-view` settles the
debt as a first-class outcome rather than letting it resurface forever.

## Residual rough edge (accepted)

A prompt minted just before a wake's heartbeat ticks will die unread, and a
drafted answer composed against it has no recovery path — the thinker must
re-compose from the fresh prompt. Mitigation is behavioral, not code: the
wake ritual answers queued prompts *before* running its heartbeat ticks
(current body order already does this), and a lost draft survives if the
thinker records it as a voluntary thought, as happened here. Deliberately
not smoothed further: silently re-minting the prompt against the new view
would trade a visible refusal for an invisible view substitution.
