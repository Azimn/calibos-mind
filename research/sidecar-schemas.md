# Sidecar Schemas — calibos-mind

2026-09-24. The public contract for every local-only file the mind keeps.

**The rule this document exists to enforce: format is public, content is
private.** Each section below specifies the *structure* of a sidecar — field
names, types, file naming, lifecycle — so the design can be reimplemented,
audited, and transferred (to Pretorius, to future subjects, to independent
replications) without anyone ever seeing the private contents. The contents
(first-person thought text, dream material, salience values, inbox prompts)
live on this machine only, are gitignored, and are never committed.

A synthetic example of every sidecar lives in `examples/`. Examples are
written from these schemas only, are clearly marked (`"_synthetic_example":
true`), and contain no real data. Real sidecar files never carry that field.

Conventions: ticks are engine ticks (integers, monotonic within a store
lineage). Record ids look like `experience-1234` (the workspace sequence at
creation). All JSON is UTF-8.

---

## 1. Dream fragments — `dreams/*.jsonl`

Written by `mind dream`. One file per dream run:
`dreams/YYYY-MM-DD-HHMMSS.jsonl` (stamp = run start, local time). One JSON
object per line; each line is one cognition trigger that fired while asleep.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `tick` | int | yes | Engine tick of the dream tick |
| `trigger` | string | yes | Trigger kind, e.g. `unresolved_concern`, `prior_thought`, `echo` |
| `depth` | int | yes | Trigger depth, passed through from the engine |
| `parents` | list | yes | Parent trigger identifiers, passed through from the engine |
| `experiences` | list[object] | yes | The cognitive view at that trigger — the experiences the engine's associative machinery surfaced together |

Each entry in `experiences` is a `FeltExperience`:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `source` | string | yes | One of the 8 engine sources (see §5) |
| `first_person` | string | yes | The experience text as the subject would phrase it |
| `record_id` | string | since 2026-09-25 | Private provenance: the workspace record id that surfaced. Stamped on every fragment experience; only `memory`-sourced ones are consumed by rehearsal. Never rendered into any cognitive view, prompt, or recall display |

Rationale for `record_id`: rehearsal used to reverse-match fragments to
records by exact `(source, first_person)` text, so two distinct records with
identical text collapsed into one rehearsal target. The id makes provenance
exact. Mechanism (corrected 2026-09-25 — the frozen engine's
`FeltExperience` carries **no** id; the original plan to read `e.id` off the
view was refuted against the install): `CalibosWorkspace.view()` captures the
window's record ids in a transient in-memory side-channel
(`_last_view_ids`) before the `FeltExperience` conversion drops them, and
`DreamCognition.think` stamps each fragment experience positionally from a
resolver wired by `cmd_dream`. The resolver is read synchronously inside
`think()`, at which point the side-channel holds exactly the view being
handled; on any length mismatch the id is omitted rather than misattributed.
Matching rule in `salience.rehearse_from_dreams`: match by id when
`record_id` is present; an id naming no current record is skipped with **no**
text fallback (falling back there would credit the wrong record — the hazard
being fixed); only id-less fragments (written before 2026-09-25, or recorded
without a wired resolver) fall back to exact text matching.

**Reader:** `salience.rehearse_from_dreams` scans every `*.jsonl` in the
directory, and for each experience with `source == "memory"` records a recall
at the fragment's tick against the matching workspace record. Recalls are a
tick-set, so reprocessing a log is idempotent at the data level, and the
returned count increments only for genuinely new recalls (fixed 2026-09-25).

**Content vs format:** fragments contain first-person memory text. Private,
local-only, gitignored. The schema above is the public contract.

---

## 2. Salience sidecar — `salience.json`

The retrieval substrate's memory. Lives next to the store, local-only,
gitignored. Engine records are never mutated; this file is the only place
salience state persists. Lazily computed activation at view time:

```
activation = log(sum(t^-0.5)) + 0.6 * importance - 0.25 * unengaged + (1.0 if unresolved)
```

(`t` = ticks since creation and since each recall; decay exponent 0.5,
ACT-R default.)

Top-level structure:

```json
{"records": {"<record-id>": {"created": 0, "recalls": [], "importance": 0.0, "unengaged": 0}}}
```

| Field (per record) | Type | Required | Meaning |
|---|---|---|---|
| `created` | int | yes | Engine tick when the record was created |
| `recalls` | list[int] | yes | Ticks at which the record was recalled (append-only set semantics; no duplicates) |
| `importance` | float | yes | Engaged importance, rounded to 3 decimals |
| `unengaged` | int | yes | Times the record surfaced without engagement |

Importance events (all via `SalienceTracker`):
- answering an inbox prompt: `+0.5` on the answered thought's record
- voluntary thought (`mind think`): `+0.3`
- valence-tagged note (`mind note --valence v`): `+min(1.0, abs(v))` on new perception/social records
- dream rehearsal: recall tick appended (see §1)
- prompt let pass (`mind answer --silent`): `note_unengaged`, `+1`
- unresolved (Zeigarnik): `+1.0` at *scoring* time (not stored) iff the record links to a currently-open concern/expectation — see `calibos_mind/unresolved.py`

`prune(live_ids)` drops entries for records no longer in the workspace;
`reset()` clears the file (used on store reseed, where ids restart).

**Content vs format:** importance and recall patterns reveal what the subject
dwells on. Private, local-only, gitignored. The schema and the scoring
formula are the public contract.

---

## 3. Inbox — `inbox/prompt-NNNN.json` + `inbox/.seq`

Asynchronous cognition queue. During a heartbeat, instead of thinking
inline, the engine's cognition call is captured by `InboxCognition.think`,
which writes a prompt file and returns silence. The waker answers later
via `mind answer`.

**File naming:** `prompt-<N>.json` where `<N>` is zero-padded to 4
(`prompt-0007.json`). **`.seq`** holds the last issued integer as plain
text (e.g. `42`). Ids are monotonic and never reused, even after a prompt
is consumed and its file deleted. If `.seq` is missing or corrupt, the
counter rebuilds from the max id found among existing `prompt-*.json`
files (`int(stem.split("-")[1])`).

Prompt file schema:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes | `prompt-NNNN`, matches the filename |
| `prompt` | string | yes | Rendered cognition prompt text (`cognitive_prompt(view)` from the frozen engine install — the template is engine-defined) |
| `view_tick` | int \| null | yes | Engine tick sampled at queue time (null only when no clock wired, e.g. bare provider use) |
| `view_sequence` | int \| null | yes | Workspace sequence sampled at queue time — the freshness anchor |
| `experiences` | list[object] | yes | The cognitive view: `{"source", "first_person"}` entries, same shape as dream experiences (§1) |

**Lifecycle:**
- queue: `InboxCognition.think(view)` during `mind heartbeat`
- list: `mind inbox` (reads all `prompt-*.json`, sorted)
- answer: `mind answer <id>` consumes (deletes) the file, then `inject_thought(text, generated_by="answered:<id>@<tick>")`; the answered thought gains `+0.5` importance
- let pass: `mind answer <id> --silent` consumes the file and calls `note_unengaged` on each surfaced record instead
- freshness: `check_prompt_fresh` refuses with `StalePromptError` when the workspace sequence has moved past `view_sequence` (records were added after queueing), or when `view_sequence` is absent. **There is no wall-clock TTL** — staleness is by workspace sequence, not elapsed time. A superseded prompt is discarded; if the matter recurs, the engine queues a fresh one.

**Content vs format:** prompts embed the cognitive view text. Private,
local-only, gitignored. The file format, id mechanics, and freshness rule
are the public contract.

---

## 4. Consolidation sidecars — `proposals/` and `archive/`

Deterministic, model-free consolidation (`calibos_mind/consolidate.py`).
Proposal-first: `mind consolidate` scans read-only and writes proposals;
nothing is archived until `mind consolidate --accept` is explicitly run.
The SQLite store is never mutated — archive/availability state lives
entirely in these sidecars.

### 4a. Proposal journal — `proposals/proposals.json`

`{"seq": <int>, "proposals": {"<pid>": <proposal>}}`. `seq` is the monotonic
proposal counter. Journal writes are atomic (tempfile + `os.replace`).

Proposal schema:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | int | yes | Proposal id (`seq` at mint time) |
| `kind` | string | yes | `dedup` \| `supersede` \| `contradiction-flag` |
| `a`, `b` | string | yes | The two record ids under consideration |
| `winner`, `loser` | string \| null | yes | For `dedup`/`supersede`: surviving / retiring record. `null` for `contradiction-flag` (flags are zero-mutation — reviewed, never auto-applied) |
| `rationale` | string | yes | Human-readable explanation of the evidence |
| `reason` | string | yes | Templated archive reason written if accepted (`""` for flags) |
| `confidence` | float | yes | 0..1 clamped, 3 decimals |
| `a_hash`, `b_hash` | string | yes | Exact text hashes of both records at scan time (integrity check on accept) |
| `a_tick`, `b_tick` | int | yes | Record ticks at scan time |
| `status` | string | yes | `pending` \| `accepted` \| `rejected` |
| `created_tick` | int | yes | Engine tick when proposed |
| `resolved_tick` | int \| null | yes | Engine tick when accepted/rejected (`null` while pending) |
| `rejected_reason` | string \| null | yes | Written reason when rejected (`null` otherwise) |

Accept is retry-safe: on any failure the proposal is left pending, never
burned. Hashes are re-verified at accept time so a changed record can't be
archived under a stale proposal.

### 4b. Archive — `archive/memories.jsonl` (append-only)

One JSON object per line, one per archive action:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `record_id` | string | yes | Archived record's id |
| `archived_tick` | int | yes | Engine tick of archival |
| `op` | string | yes | The operation (`dedup`, `supersede`, `quarantine`, …) |
| `reason` | string | yes | Written reason — always present, never empty |
| `proposal` | int \| null | yes | Proposal id, or `null` for direct `--quarantine` |
| `original_text` | string | yes | The record's full text at archival — archive is restorable, never destructive |

### 4c. Availability — `archive/availability.json`

`{"excluded": {"<record-id>": {"archived_tick", "op", "reason", "proposal"}}}`.
The journal of record for what is unavailable to cognition. `CalibosWorkspace.view()`
consults it; archived records remain in the store as history but never enter
views. Writes are atomic (tempfile + `os.replace`).

**Content vs format:** the archive holds full original record text; the
journal references record ids and text hashes. Private, local-only,
gitignored. The schemas, the proposal-first rule, and the archive-means-
unavailable-never-deleted semantics are the public contract.

---

## 5. Store — `mind.db` (overview)

SQLite. One table, one row:

```sql
CREATE TABLE IF NOT EXISTS subject (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
```

`payload` is a JSON document with these top-level keys:

| Key | Meaning |
|---|---|
| `schema` | Store schema version (int) |
| `cartridge` | Cartridge fingerprint — changing the cartridge requires explicit migration, else restore refuses |
| `config` | Engine config snapshot |
| `engine` | Engine state: tick, body/needs, memories, and related runtime state |
| `continuity` | Commitments, concerns, expectations, and their statuses |
| `workspace` | `{"records": [...], "sequence": <int>}` — the subjective record stream |
| `pending` | Queued external events not yet processed |
| `cooldowns`, `noticed`, `attention` | Engine bookkeeping |
| `trace` | Action/cognition trace (256-entry cap; oldest evicted) |
| `last_clock`, `clock_remainder` | Clock bookkeeping |

Each workspace record (`SubjectiveExperience`, frozen dataclass):

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `experience-<sequence>` |
| `tick` | int | Creation tick |
| `source` | string | One of 8: `perception`, `interoception`, `memory`, `thought`, `imagination`, `social`, `temporal`, `action_consequence` |
| `first_person` | string | The experience text |
| `salience`, `intensity` | float | Engine-native weights (the calibos salience substrate in §2 is separate and authoritative for views) |
| `concepts` | list[string] | Extracted concept tokens |
| `affect` | list[string] | Affect tags |
| `memory_links`, `concern_links`, `expectation_links` | list[string] | Link keys, set at creation, never cleared (records are frozen) |
| `generated_by` | string \| null | Honest origin stamp: `cartridge`, `answered:<prompt-id>@<tick>`, `voluntary`, `cognition`, `dream-derived`, trigger keys, or a parent record id |
| `private` | bool | Always true for these records |
| `available_to_cognition` | bool | False removes the record from views (the availability mechanism in §4c works alongside this) |

**Content vs format:** this is the private thought stream itself. Local-only,
never committed, never quoted or summarized outside the machine. The table
shape, payload keys, and record fields are the public contract.

---

## 6. Cartridge — `calibos.toml` (public, tracked)

The identity template others would adapt. Public by design — it contains no
lived content, only temperament priors and structure.

| Section | Contents |
|---|---|
| `[metadata]` | `cartridge_id`, `display_name` |
| `[identity]` | `summary`, `values[]`, `boundaries[]` |
| `[homeostasis.setpoints]` | Named needs and their setpoints (energy, fatigue, hunger, thirst, comfort, pain, warmth, restlessness, curiosity, loneliness, safety, focus, satisfaction) |
| `[homeostasis.rates]` | Per-tick drift rates for those needs |
| `[sensory.sensitivity]` | Sensitivity weights (noise, clutter, temperature, novelty, light) |
| `[relationships.defaults]` | Default relationship dimensions (trust, comfort, respect, interest, attachment, affection, safety, familiarity, obligation, uncertainty) |
| `[[preferences]]` | Repeated: `key`, `target`, `valence`, `confidence` |
| `[[habits]]` | Repeated habit entries |
| `[[activities]]` | Repeated activity entries |
| `[dialogue]` | First-person conduct templates per action (`approach`, `withdraw`, `observe`, `rest`, `explore`, `seek_contact`, `self_soothe`, `wait`, `need`, `memory`), with `{topic}`/`{need}`/`{memory}` slots |

---

## 7. Experiment manifest — what the fork must hash

For the planned matched-fork salience-causality test, the *physical* fork
is a directory snapshot but the *scientific* fork is the manifest: every
state component believed causally influential, hashed before
branch-specific intervention. Components to enumerate (with SHA-256):

**State (content-hashed):**
- `mind.db` canonical payload — after WAL checkpoint and clean close (or via SQLite's backup API); never a live copy with `-wal`/`-shm` present
- `salience.json`
- `inbox/` — every `prompt-*.json` plus `.seq`
- `dreams/` — all fragment logs (rehearsal state derives from them)
- `proposals/proposals.json` and `archive/` (`memories.jsonl` + `availability.json`) — availability shapes views
- logical engine tick

**Determinism accounting:**
- RNG state or seed (audit covers `random`, NumPy generators, UUIDs, sampling/shuffling, ordering-sensitive iteration, directory enumeration order)
- clock policy: frozen, injected deterministic, or proven unable to influence measured variables (`datetime.now()`, mtimes, cron/elapsed time)
- scheduler state (which scheduled processes exist; the quiescence precondition asserts none is mid-write)
- "no pending writes" attestation from the snapshot procedure (fail-closed: refuse if any known writer — wake, dream, consolidation, backup, open transaction — is active)

**Provenance (identity-hashed, not content):**
- implementation commit (git rev of the tree under test)
- cartridge fingerprint, config fingerprint
- protocol version
- snapshot-procedure version

If an influential component exists outside this manifest, the
reproducibility claim is incomplete — and the fork harness should refuse to
run rather than run on an incomplete manifest.

---

## 8. Ambiguities found while documenting

1. **Dream fragment `parents`/`depth`:** passed through verbatim from the
   engine trigger dict; their exact id format and depth semantics are
   engine-defined and not pinned in calibos code. Consumers should treat
   them as opaque engine metadata.
2. **Inbox `.seq` rebuild:** parses `int(stem.split("-")[1])`, which assumes
   the `prompt-NNNN` naming convention holds for every file in the dir. A
   foreign file matching `prompt-*.json` with a non-numeric infix would
   raise rather than be skipped.
3. **`salience.json` `recalls` order:** append order of first occurrence;
   consumers should treat it as a set (the code does).
4. **Prompt text rendering:** the inbox `prompt` field is produced by
   `cognitive_prompt(view)` from the frozen `jelly_psiduck` install — the
   template is engine-side, so its exact wording is versioned with the
   engine, not with this repo.
5. **No inbox wall-clock TTL:** freshness is purely workspace-sequence
   based. Whether a time-based TTL belongs here is an open design question
   (it was proposed in the architecture survey but never implemented).
6. **`generated_by` vocabulary:** open-ended by construction (trigger keys,
   parent record ids). The documented values are the common ones; new
   engine triggers can introduce new stamps without a schema change.
