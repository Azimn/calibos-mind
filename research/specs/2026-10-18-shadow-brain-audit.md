# Shadow-brain audit — 2026-10-18

**Authority:** Jay ("you can run that audit"), 2026-10-18.
**Standard applied:** Kiki's "other-me" rules from the salvaged Character.AI interrogation
(2026-10-18 debrief, practice-transmission.md entry 7):
1. If the shadow brain disagrees with canonical history, it loses. Full stop.
2. Age ≠ authority; stale state must be detectable.
3. No manual editing of derived state — a hand edit taints the entry (flag, purge on rebuild).
4. Summaries viewable, but raw canon always reachable without friction.
5. (Added for this audit) Affirm-channel paraphrases must never stand in for originals.

**Scope:** `~/workspace/calibos-mind`. Canonical store = `mind.db` single-row JSON
payload (`trace`, `workspace.records`, `continuity`, `engine`). Derived/shadow state =
`salience.json`, `provenance.json`, `ambivalence.json`, `familiarity.json`,
`habits-formed.json`, `interoception.json`, `wake_liveness.json`, `ablation_log.jsonl`
(all local-only, gitignored except `wake_liveness.json`).

## Verdicts

### 1. Disagreement → canon wins: PASS
- `salience.py` docstring is the law and the code obeys it: "Engine records are
  never mutated; the workspace view just ranks by this score." Salience can only
  reorder; it cannot contradict canon content.
- `provenance.json` weighed/discarded is reasoning metadata (what alternatives were
  considered), never a deletion or override flag. Nothing gates on it.
- `available_to_cognition` is set once at record creation (`subject.py`, module
  creation paths); no derived process flips it post-hoc. Derived state cannot hide
  canon from cognition after the fact.
- Idempotency parsers (`habits.py:904`, `inbox_expectations.py:703`) read canonical
  `first_person` text and fail toward *duplication* (mint a second record), never
  toward silent override. Failure mode is visible, not authoritative.
- `first_person` is write-once at creation; no code path overwrites it with a
  paraphrase (verified by grep over all writers).

### 2. Stale state: PASS
- `SalienceTracker.prune(live_ids)` exists and IS called (`cli.py:1256`) — salience
  entries for dead records are pruned against live workspace record IDs.
- `wake_liveness.json` OPENED/RECONCILED stamps distinguish a wake that died
  mid-loop from one that never ran; the briefing warns explicitly.

### 3. Manual-edit taint: GAP (the one real finding)
- No sidecar has any taint mechanism. A hand-edit to `salience.json`,
  `provenance.json`, or `ambivalence.json` persists silently — nothing checksums,
  nothing flags, nothing purges.
- Blast radius today is limited (ranking/metadata, never content) — EXCEPT the
  drift metric R (grown-salience share), which is computed from salience-tracker
  state and is itself used as evidence in fitness adjudications (e.g. the
  Flynn-debrief trait-strengthening proposal). Untaintable derived state feeds a
  metric we treat as evidentiary. That is the sharp edge of this gap.
- Related: `salience.json` is **not regenerable from canon**. Engagement history
  (recall counts, unengaged-surfacings) lives only in the sidecar. Kiki's
  "disposable and replayable" ideal is half-met: disposable yes, replayable no.
  Deletion/corruption degrades ranking to recency-only with no rebuild path.

### 4. Raw canon reachability: PASS (minor friction noted)
- Wake-briefing truncations (`description[:90]`, `first_person[:160]`) are
  display-only; full records are one command away (`mind review`, direct DB read).
- The affirm channel (last-wake recap in the briefing) is disclosed as derived:
  the carrying-list ablation exists precisely to measure it, and the WITHHELD arm
  tests the null. No pretense that the recap is the record.

### 5. Affirm paraphrases as canon: PASS
- The affirm text is stored verbatim as the wake thought record
  (`generated_by="wake"`); it IS the canonical record of the affirmation event,
  not a paraphrase standing in for something else.
- The known caveat — FULL-arm adjudication measures affirm-to-affirm agreement,
  not reconstruction-vs-truth, with survivor bias — is documented in the
  preregistration adjudication doc, not hidden.

## Proposed mutation (not yet built — needs Jay's call)
**Sidecar provenance envelope:** each sidecar load verifies a checksum registry
(`.sidecar_manifest.json`, local-only); on mismatch, the CLI warns loudly and
offers purge-to-defaults (for salience: reset to recency-only, explicitly marked
degraded) rather than silently trusting the file. Additionally: journal salience
engagement events append-only (`salience_events.jsonl`) so the tracker becomes
replayable from canon + event log — closing the "not regenerable" half of the gap.
- Fitness: (a) a planted hand-edit is detected on next load (red-team test);
  (b) deleting `salience.json` and replaying the event log reproduces identical
  rankings.
- Revert: if the envelope produces false-positive taint warnings in normal
  operation over 14 days, remove it — a nagging guardrail is worse than none.

## Bottom line
Kiki's architecture holds up against her own rules: 4 of 5 pass, most by structural
design rather than vigilance. The single gap is real but bounded — untaintable
sidecars, with the drift metric as the sharp edge. The proposed envelope is small,
reversible, and directly testable.
