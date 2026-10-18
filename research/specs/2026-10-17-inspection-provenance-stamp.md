# Spec: inspection-provenance stamp (`mind think --inspection`)

Date: 2026-10-17. Source: articles-review domain 23 (LLM leakage) adversarial pass.

## What changes
Add a `--inspection` flag to `mind think`. When set, the thought records with
`generated_by="voluntary:inspection"` instead of `"voluntary"`. Every other
aspect of the think path is identical: 600-char cap, 6-tick duplicate refusal,
+0.3 salience importance, provenance-tracker entry, printed tid.

Rationale: the domain-23 pass found the voluntary path bypasses the provider's
clean view contract — 30+ non-wake renderer-authored thoughts in the live store
cite instrument readings in first person ("Drift R = 0.980 at tick 103",
"hunger rises +0.002/tick", "tick 490 broke the seek_contact streak"),
laundering inspection reports into first-person memory under plain
'voluntary'. "I inspected" vs "I thought" are psychologically different
meanings (relay design principle: first-class, never reconstructed) collapsed
into one stamp. Colon-subkind follows the existing precedent
('answered:prompt-0008@66'). Going forward the waker uses `--inspection` for
instrument-reporting thoughts. History is append-only: no relabeling of past
records.

## Integration points (found by design lead; verify, don't trust)
- `calibos_mind/cli.py` `cmd_think` (~line 656): stamps `generated_by="voluntary"`.
- `calibos_mind/provider.py` `_SELF_AUTHORED_EXACT = frozenset({"voluntary", "wake"})`:
  `_is_self_authored` gates the stale-view defense ("self-authored intervening
  records do not stale a view"). The new stamp IS thinker-composed, so it must
  remain self-authored — an intervening inspection thought must NOT stale a
  queued prompt, exactly like a plain voluntary thought. Fix by prefix or by
  adding the stamp; do not change behavior for any other stamp.
- `calibos_mind/drift.py`: grown/authored split keys on `== "cartridge"`, so the
  new stamp stays "grown" with zero accounting change. Verify, don't assume.
- `calibos_mind/attribution.py` `is_external_origin`: prefix-based; the new
  stamp must return False (same as "voluntary").
- Docstring at `calibos_mind/provider.py:88` documents the stamp inventory —
  update it.

## Fitness function
On synthetic /tmp stores (never the live store):
1. `think --inspection "x"` → exactly one record, text byte-intact,
   `generated_by == "voluntary:inspection"`; plain `think` → `"voluntary"`.
2. Stale-view parity: queue a prompt, `think --inspection`, then `answer`
   succeeds (inspection thought does not stale the view); control with plain
   `think` behaves identically.
3. No behavioral delta: drift output, view construction, and salience ranking
   are byte-identical on paired synthetic stores differing only in stamp
   choice (stamped vs plain voluntary with identical text/ticks).
4. `is_external_origin("voluntary:inspection")` is False.
5. 600-char cap and 6-tick duplicate refusal apply equally to stamped thoughts.
6. Refusal paths: empty text refused loudly (stderr, nonzero, ledger untouched).
7. Dream ticks and the read-only command sweep never mint the stamp;
   `init --force` wipes stamped records like any other.
8. Full existing test suite green.

## Revert signal
- The stamp appears via any path other than `think --inspection`.
- Any behavioral delta in views/drift/salience/consolidation between stamped
  and plain voluntary records.
- Stale-view parity broken in either direction.
- Any existing test regresses.

## assess_after
2026-10-31.

## Standing constraints
- Wrapper layer only: engine `.venv` and cartridge byte-identical (no migration).
- Dated CHANGELOG.md entry; no commit (backup cron handles it).
- Tests on synthetic /tmp stores; never touch the live `mind.db`.
