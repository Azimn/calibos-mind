# examples/ — synthetic sidecars

Everything in this directory is **synthetic**: hand-written from the schemas
in `research/sidecar-schemas.md`, containing no real data from any live
mind. Every file carries `"_synthetic_example": true` (real sidecar files
never have this field).

Purpose: let anyone reimplement the architecture — the dream process, the
salience substrate, the inbox queue, consolidation — and validate their
parsers and tooling against realistic shapes without ever touching private
content. Format is public; content is private. These files are the format,
with the content replaced by obvious fiction (Example McExampleface and
associates).

Files:
- `dream-fragments.example.jsonl` — two dream-fragment lines (§1)
- `salience.example.json` — a salience sidecar with two records (§2)
- `inbox-prompt.example.json` — one queued cognition prompt (§3)
- `proposal-journal.example.json` — one pending dedup proposal (§4a)
- `archive-memories.example.jsonl` — one archive entry (§4b)

The matching availability-journal shape (`archive/availability.json`,
§4c) is `{"excluded": {"experience-102": {"archived_tick": 4300,
"op": "dedup", "reason": "...", "proposal": 3}}}` — derivable from the
archive entry, so no separate example file is kept.
