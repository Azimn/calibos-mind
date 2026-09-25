"""Deterministic consolidation: dedup + supersession + contradiction flags.

Port of the da7-tech/dream consolidation ops selected in
research/mechanisms-deepdive-2026-09-24.md §3e ("the most portable
machinery in the survey"). Deterministic, zero models, zero new schedules.

Pipeline (proposal-first; the waker disposes):

    mind consolidate                        dry-run: scan the store READ-ONLY,
                                            emit structured proposals to the
                                            local-only proposal journal.
                                            Changes nothing else: no archive
                                            writes, no availability changes,
                                            no writes to mind.db — on ANY path.
    mind consolidate --list                 show pending proposals
    mind consolidate --accept <id> [...]    archive accepted losers
    mind consolidate --reject <id> --reason "..."
                                            reject a proposal, reason kept
    mind consolidate --quarantine <record-id> --reason "..."
                                            archive a record immediately
                                            (synthetic-test residue, etc.)

Archive-never-delete: losers are marked unavailable in the local-only
availability journal (archive/availability.json), which workspace views
consult — archived records stop surfacing in cognition but are never
deleted or rewritten. The full original text plus a written reason lands
in archive/memories.jsonl. Every archive action is restorable from that
sidecar. 100% of archived records carry a written reason + full text.

The store itself (mind.db) is opened read-only (SQLite ``mode=ro``) on
every consolidation path — even --accept never writes to it. Availability
is purely a sidecar concern, so the engine store stays append-only.

Ops (thresholds per the deep-dive §3e verdict table):
- exact dedup: md5 of normalized tokens (lowercased, punctuation-stripped,
  stemmed; stopwords kept). Earliest (created_tick, store order) survives.
- near-dup: Jaccard >= 0.85 OR containment >= 0.92 on stemmed content-token
  sets, AND _same_sequence() — shared tokens must appear in the same
  relative order ("A calls B" vs "B calls A" are opposites, not
  duplicates), AND negation-polarity agreement — "not"/"no" are stopwords,
  so opposite facts can have identical content-token sets; a mismatch
  reroutes the pair to a contradiction-flag instead of a dedup proposal.
  Richer entry survives (longer text; tie -> later
  created_tick/store order wins).
- supersession: subject (first 6 content tokens) Jaccard >= 0.50 AND body
  Jaccard in [0.40, 0.85) AND stopword-negation-polarity agreement (a
  "not"/"no"/"nor" mismatch reroutes to a contradiction-flag instead of
  minting a false "same subject stated again later" restatement; "never" is
  a content token and keeps the ordinary supersede path) -> same subject
  restated -> NEWER created_tick wins (seeds are oldest, so they lose by
  construction — desired).
- contradiction flags: subject Jaccard >= 0.50, body Jaccard in
  [0.25, 0.40) -> flagged, ZERO mutation, surfaced for waker review.
- merge/squeeze NOT ported: highest-risk op for silent nuance loss, and no
  store budget exists. The file-lock/two-phase-commit crash-recovery
  machinery is NOT ported either: single-writer SQLite doesn't need it.

Scale: candidate pairs are blocked on shared content tokens (sound for
these thresholds — any qualifying pair shares at least one) and total
pairwise comparisons are capped at MAX_PAIRWISE_COMPARISONS. Hitting the
cap is reported in the dry-run output, never silent.

created_tick is the record's store tick (same semantics as the salience
sidecar). Origin stamps (generated_by) are honored where relevant: the
identity root — the earliest cartridge-authored record, the load-bearing
self-anchor — can never be named the loser of an automatic proposal, and
records attributable to externally-authored content (see
calibos_mind/attribution.py) never take part in automatic dedup/supersede
at all — neither as winner (which would silently upgrade external content
over lived records) nor as loser (which would retire it under a "same
fact" rationale). Contradiction-flags still fire on those pairs: zero
mutation, the waker disposes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .attribution import external_attributed

# -- tuning (from the deep-dive §3e verdict table) ------------------------

JACCARD_NEAR_DUP = 0.85
CONTAINMENT_NEAR_DUP = 0.92
SUBJECT_OVERLAP_SUPERSEDE = 0.50
BODY_JACCARD_SUPERSEDE_LO = 0.40
BODY_JACCARD_SUPERSEDE_HI = 0.85
BODY_JACCARD_FLAG_LO = 0.25
BODY_JACCARD_FLAG_HI = 0.40
SUBJECT_TOKENS = 6          # subject = first ~6 content tokens
MIN_PAIR_TOKENS = 4         # per-record content tokens needed for pair ops
MIN_DUP_UNION = 6           # Jaccard union-size noise guard (workspace.py parity)
MAX_PAIRWISE_COMPARISONS = 200_000

KINDS = ("dedup", "supersede", "contradiction-flag")

ARCHIVE_OP_DEDUP = "dedup"
ARCHIVE_OP_SUPERSEDE = "supersede"
ARCHIVE_OP_QUARANTINE = "quarantine"


class ConsolidationError(Exception):
    """Clean, reportable failure (bad store, bad journal, bad arguments)."""


# -- text normalization ---------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset("""
i me my mine we our ours you your yours he him his she her hers it its
they them their theirs this that these those a an the and or but if then
else so as at by for of on in to with from into over after before about
is are was were be been being am have has had having do does did doing
not no nor just very can could would should will shall may might must
what which who whom how why when where there here all any some more most
other such than too also only own same once
""".split())

# Negation-polarity signal, counted on the RAW text. "not"/"no"/"never" are
# stopwords (shared with the workspace dedupe spec), so the polarity signal
# vanishes from content tokens: "is friendly" and "is not friendly" have
# IDENTICAL content-token sets. word_tokens() would also split "don't" into
# "don"+"t", so the polarity count runs on its own regex over the raw text
# rather than on token streams. A mismatch between a near-dup pair means
# opposite facts, never "the same fact worded twice".
#
# The trailing "n't" catch-all deliberately drops the LEADING \b: inside any
# contraction the "n" is always preceded by a word character ("o" in don't,
# "i" in ain't), so \bn't\b could never match there — dead code that let
# unlisted contractions ("ain't", "shan't") slip the veto. No standard
# English word ends in "n't" outside contractions, so n't\b is safe; the
# trailing \b still guards against over-matching (it is the \b discipline
# that keeps "knot"/"notable" at zero).
_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|none|nobody|nothing|neither|nor|cannot|"
    r"can't|couldn't|won't|don't|doesn't|didn't|isn't|aren't|wasn't|"
    r"weren't|haven't|hasn't|hadn't|wouldn't|shouldn't|mustn't|needn't)\b"
    r"|n't\b")


def _negation_count(text: str) -> int:
    return len(_NEGATION_RE.findall(text.lower()))


# Stopword-stripped negation: the markers that vanish from content tokens
# ("not"/"no"/"nor" are stopwords) PLUS contraction negation ("doesn't",
# "won't", ...), which tokenizes to content fragments ("doesn"/"t"). A
# mismatch here is invisible to the Jaccard machinery in the sense that
# matters: the similarity math sees token difference, not polarity, so it
# cannot distinguish a negated COMPLEMENT ("beautiful in morning light"
# vs "doesn't look beautiful in evening light" — both can be true) from a
# restatement. "never" stays excluded as a content token (arguably-correct
# belief update when it alone changes, and the math can see the
# difference).
_STOPWORD_NEGATION_RE = re.compile(r"\b(?:not|no|nor)\b")

# The "n't" catch-all runs on the raw text (like _NEGATION_RE above): no
# leading \b, since inside a contraction the "n" is always preceded by a
# word character. No standard English word ends in "n't" outside
# contractions, and the trailing \b guards against over-matching.
_CONTRACTION_NEGATION_RE = re.compile(r"\w+n't\b")


def _stopword_negation_count(text: str) -> int:
    lowered = text.lower()
    return (len(_STOPWORD_NEGATION_RE.findall(lowered))
            + len(_CONTRACTION_NEGATION_RE.findall(lowered)))


def _stem(token: str) -> str:
    """Light deterministic suffix stemmer (English-first, not Porter).

    Collapses the common inflectional endings so "gates"/"gate",
    "walking"/"walked"/"walk" compare equal. Deliberately conservative:
    short tokens pass through untouched.
    """
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    for suffix in ("ingly", "edly"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)]
    for suffix in ("ing", "ed"):
        if token.endswith(suffix) and len(token) > len(suffix) + 2:
            return token[: -len(suffix)]
    if token.endswith("es") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        return token[:-1]
    return token


def word_tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def content_tokens(text: str) -> list[str]:
    """Stemmed content tokens, order preserved, stopwords dropped."""
    return [_stem(t) for t in word_tokens(text) if t not in STOPWORDS]


def normalized_text(text: str) -> str:
    """Canonical form for exact-dup hashing: stopwords kept, so "the cat"
    and "cat" do NOT collide — only genuinely identical-after-normalization
    texts share a hash."""
    return " ".join(_stem(t) for t in word_tokens(text))


def exact_hash(text: str) -> str:
    return hashlib.md5(normalized_text(text).encode("utf-8")).hexdigest()


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set, b: set) -> float:
    """Overlap / smaller set. Catches 'same fact, one worded with extra
    detail' pairs that plain Jaccard misses."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _same_sequence(toks_a: list[str], toks_b: list[str]) -> bool:
    """The da7 anti-footgun: shared tokens must appear in the same relative
    order. 'A calls B' vs 'B calls A' are opposites, not duplicates."""
    shared = set(toks_a) & set(toks_b)
    if not shared:
        return False
    return ([t for t in toks_a if t in shared]
            == [t for t in toks_b if t in shared])


# -- store loading (read-only, provably) -----------------------------------

@dataclass
class _Rec:
    id: str
    index: int          # store order; tie-breaks equal ticks deterministically
    source: str
    text: str
    tick: int           # created_tick
    generated_by: str
    available: bool


def load_records(db_path: str | Path) -> tuple[list[_Rec], int]:
    """Load workspace records + store tick through a read-only SQLite open.

    ``mode=ro`` means any write attempt raises sqlite3.OperationalError, so
    this function provably touches no write path: the dry-run guarantee
    rests on the open mode, not on byte comparisons after the fact.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise ConsolidationError(f"no store at {db_path} — run `mind init` first")
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise ConsolidationError(f"cannot open store read-only: {exc}")
    try:
        try:
            row = con.execute("SELECT payload FROM subject WHERE id = 1").fetchone()
        except sqlite3.Error as exc:
            raise ConsolidationError(f"store at {db_path} has no readable snapshot: {exc}")
    finally:
        con.close()
    if row is None:
        raise ConsolidationError("store has no subject snapshot (id=1)")
    try:
        raw = json.loads(row[0])
    except ValueError as exc:  # JSONDecodeError subclasses ValueError
        raise ConsolidationError(f"store payload is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        raise ConsolidationError("store payload is not a JSON object")
    # The engine always writes workspace.records, so a missing section is
    # malformed input, not an empty store: raising here is the honest path,
    # and the quiet zero ("0 records / no proposals" on a broken snapshot)
    # the regression genome forbids. A store that genuinely holds an EMPTY
    # records list still scans as zero records, honestly.
    if "workspace" not in raw:
        raise ConsolidationError("store payload is missing the 'workspace' section")
    workspace = raw["workspace"]
    if not isinstance(workspace, dict):
        raise ConsolidationError("store payload 'workspace' section is not an object")
    if "records" not in workspace:
        raise ConsolidationError("store payload is missing the workspace.records section")
    raw_records = workspace["records"]
    if not isinstance(raw_records, list):
        raise ConsolidationError("store payload workspace.records is not a list")
    engine = raw.get("engine", {})
    tick = engine.get("tick", 0) if isinstance(engine, dict) else 0
    recs = []
    for i, r in enumerate(raw_records):
        if not isinstance(r, dict):
            raise ConsolidationError(
                f"record index {i} is not an object: {type(r).__name__}")
        try:
            rid = r["id"]
        except KeyError:
            raise ConsolidationError(
                f"record index {i} is missing required field 'id'")
        recs.append(_Rec(
            id=rid, index=i, source=r.get("source", ""),
            text=r.get("first_person", "") or "",
            tick=r.get("tick", 0), generated_by=r.get("generated_by", ""),
            available=bool(r.get("available_to_cognition", True)),
        ))
    return recs, tick


def identity_root_id(records: list[_Rec]) -> str | None:
    """The load-bearing self-anchor: earliest cartridge-authored record.

    Automatic proposals may retire seeds (that is the point of the seed
    experiment) but may never name the identity root as the loser.
    """
    authored = [r for r in records if r.generated_by == "cartridge"]
    if not authored:
        return None
    return min(authored, key=lambda r: (r.tick, r.index)).id


# -- proposal journal -------------------------------------------------------

def _journal_path(base: str | Path) -> Path:
    return Path(base) / "proposals.json"


def load_journal(base: str | Path) -> dict:
    """Load (or initialize) the proposal journal. Never creates files."""
    path = _journal_path(base)
    if not path.exists():
        return {"seq": 0, "proposals": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ConsolidationError(f"proposal journal unreadable at {path}: {exc}")
    if not isinstance(data, dict) or "proposals" not in data:
        raise ConsolidationError(f"proposal journal malformed at {path}")
    data.setdefault("seq", 0)
    return data


def _save_journal(base: str | Path, journal: dict) -> None:
    """Atomic write (tmp + os.replace): single-writer, no torn journal."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    path = _journal_path(base)
    fd, tmp = tempfile.mkstemp(dir=str(base), prefix="proposals.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(journal, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def pending_proposals(journal: dict) -> list[dict]:
    return [p for p in journal["proposals"].values() if p["status"] == "pending"]


# -- availability journal + archive sidecar ----------------------------------

def _availability_path(base: str | Path) -> Path:
    return Path(base) / "availability.json"


def _read_availability(base: str | Path) -> dict:
    """The journal of record for archived ids. Missing file -> empty."""
    path = _availability_path(base)
    if not path.exists():
        return {"excluded": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ConsolidationError(f"availability journal unreadable at {path}: {exc}")
    if not isinstance(data, dict) or "excluded" not in data:
        raise ConsolidationError(f"availability journal malformed at {path}")
    return data


def _write_availability(base: str | Path, data: dict) -> None:
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    path = _availability_path(base)
    fd, tmp = tempfile.mkstemp(dir=str(base), prefix="availability.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # Keep the view-path cache coherent with what we just wrote.
    _avail_cache[str(path)] = (path.stat().st_mtime_ns,
                               frozenset(data["excluded"].keys()))


_avail_cache: dict[str, tuple[int, frozenset]] = {}


def excluded_ids(availability_path: str | Path | None) -> frozenset:
    """Ids archived out of cognition views. Hot path: mtime-keyed cache.

    Missing journal -> empty set. This is what CalibosWorkspace.view()
    consults; archived records are history, never deleted.
    """
    if availability_path is None:
        return frozenset()
    path = Path(availability_path)
    base = path.parent
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return frozenset()
    key = str(path)
    hit = _avail_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    ids = frozenset(_read_availability(base)["excluded"].keys())
    _avail_cache[key] = (mtime, ids)
    return ids


def _archive_entry(archive_dir: str | Path, record_id: str, archived_tick: int,
                   op: str, reason: str, original_text: str,
                   proposal: int | None) -> None:
    """Append one restorable archive record. Full text + written reason,
    always — archive_integrity is binary."""
    base = Path(archive_dir)
    base.mkdir(parents=True, exist_ok=True)
    entry = {
        "record_id": record_id,
        "archived_tick": archived_tick,
        "op": op,
        "reason": reason,
        "proposal": proposal,
        "original_text": original_text,
    }
    with (base / "memories.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    avail = _read_availability(base)
    avail["excluded"][record_id] = {
        "archived_tick": archived_tick,
        "op": op,
        "reason": reason,
        "proposal": proposal,
    }
    _write_availability(base, avail)


def read_archive(archive_dir: str | Path) -> list[dict]:
    """Every archived record, for audit. Missing file -> empty."""
    path = Path(archive_dir) / "memories.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


# -- the scan -----------------------------------------------------------------

@dataclass
class _Cand:
    rec: _Rec
    toks: list[str]      # content tokens, order preserved
    tokset: frozenset    # content token set
    subject: frozenset   # first SUBJECT_TOKENS content tokens
    neg: int            # negation-polarity count on the raw text
    sneg: int           # stopword-stripped negation count (invisible to Jaccard)


def _candidates(records: list[_Rec], excluded: frozenset) -> list[_Cand]:
    out = []
    for r in records:
        if not r.available or r.id in excluded or not r.text.strip():
            continue
        toks = content_tokens(r.text)
        if len(toks) < MIN_PAIR_TOKENS:
            continue
        out.append(_Cand(r, toks, frozenset(toks),
                         frozenset(toks[:SUBJECT_TOKENS]),
                         _negation_count(r.text),
                         _stopword_negation_count(r.text)))
    return out


def _newer(a: _Rec, b: _Rec) -> _Rec:
    """Newer created_tick wins; store order breaks ties deterministically."""
    return a if (a.tick, a.index) >= (b.tick, b.index) else b


def _richer(a: _Rec, b: _Rec) -> _Rec:
    """Richer wording survives: longer text; tie -> later wins."""
    if len(a.text) != len(b.text):
        return a if len(a.text) > len(b.text) else b
    return _newer(a, b)


def _mint(journal: dict, kind: str, a: _Rec, b: _Rec, winner: _Rec | None,
          loser: _Rec | None, rationale: str, reason: str,
          confidence: float, created_tick: int) -> dict:
    journal["seq"] += 1
    pid = journal["seq"]
    prop = {
        "id": pid,
        "kind": kind,
        "a": a.id,
        "b": b.id,
        "winner": winner.id if winner else None,
        "loser": loser.id if loser else None,
        "rationale": rationale,
        "reason": reason,                      # templated archive reason ("" for flags)
        "confidence": round(min(1.0, max(0.0, confidence)), 3),
        "a_hash": exact_hash(a.text),
        "b_hash": exact_hash(b.text),
        "a_tick": a.tick,
        "b_tick": b.tick,
        "status": "pending",
        "created_tick": created_tick,
        "resolved_tick": None,
        "rejected_reason": None,
    }
    journal["proposals"][str(pid)] = prop
    return prop


def scan(records: list[_Rec], store_tick: int, journal: dict,
         excluded: frozenset = frozenset(),
         max_pairs: int = MAX_PAIRWISE_COMPARISONS) -> dict:
    """Pairwise consolidation scan. Pure function over loaded records.

    Returns a report dict; newly minted proposals are added to `journal`
    in place (the caller decides whether to persist them). Records in
    `excluded` (the availability journal) never become candidates.
    """
    root_id = identity_root_id(records)
    cands = _candidates(records, excluded)
    # Subjective-transduction boundary: ids attributable to
    # externally-authored content. Automatic dedup/supersede never touches
    # them (see emit()); contradiction-flags still fire (zero mutation).
    ext = external_attributed(records)
    seen_pairs = {(p["kind"], min(p["a"], p["b"]), max(p["a"], p["b"]))
                  for p in journal["proposals"].values()}
    slated: set[str] = set()   # ids named loser by a proposal this run
    new_props: list[dict] = []
    stats = {"records": len(records), "candidates": len(cands),
             "pairs": 0, "capped": False,
             "exact": 0, "near": 0, "supersede": 0, "flags": 0,
             "external_skipped": 0}

    def emit(kind, a, b, winner, loser, rationale, reason, confidence):
        if loser is not None and loser.id == root_id:
            return  # the identity root is never an automatic loser
        if kind in ("dedup", "supersede") and (a.id in ext or b.id in ext):
            # Never silently upgrade external-attributed content to
            # autobiographical standing: it may neither be crowned winner
            # over a lived record (newer-tick-wins would do exactly that)
            # nor retired as loser under a "same fact" rationale (the
            # destruction over-correction). The pair is skipped, counted,
            # and left for the waker — contradiction-flags below still
            # surface genuine conflicts for review.
            stats["external_skipped"] += 1
            return
        key = (kind, min(a.id, b.id), max(a.id, b.id))
        if key in seen_pairs:
            return
        seen_pairs.add(key)
        prop = _mint(journal, kind, a, b, winner, loser, rationale,
                     reason, confidence, store_tick)
        new_props.append(prop)
        if loser is not None:
            slated.add(loser.id)
        return prop

    # -- light pass: exact dedup (hash sweep, no pairwise cost) -------------
    by_hash: dict[str, list[_Cand]] = {}
    for c in cands:
        by_hash.setdefault(exact_hash(c.rec.text), []).append(c)
    for group in by_hash.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda c: (c.rec.tick, c.rec.index))
        survivor = ordered[0]
        for dup in ordered[1:]:
            if dup.rec.id in slated or survivor.rec.id in slated:
                continue
            prop = emit("dedup", survivor.rec, dup.rec, survivor.rec, dup.rec,
                        f"{dup.rec.id} is identical to {survivor.rec.id} "
                        f"after normalization; one copy is enough",
                        "identical after normalization; one copy is enough",
                        1.0)
            if prop is not None:
                stats["exact"] += 1

    # -- blocked pairwise passes -------------------------------------------
    # A pair is compared only if it shares >= 1 content token. Sound for
    # every threshold below: qualifying pairs always share at least one.
    postings: dict[str, list[int]] = {}
    for i, c in enumerate(cands):
        for t in c.tokset:
            postings.setdefault(t, []).append(i)
    pair_set: set[tuple[int, int]] = set()
    for lst in postings.values():
        for x in range(len(lst)):
            for y in range(x + 1, len(lst)):
                pair_set.add((lst[x], lst[y]))
    ordered_pairs = sorted(pair_set)

    def pairs_up_to():
        n = 0
        for i, j in ordered_pairs:
            if n >= max_pairs:
                stats["capped"] = True
                break
            n += 1
            yield i, j
        stats["pairs"] += n

    # near-dup: Jaccard >= 0.85 OR containment >= 0.92, same sequence.
    for i, j in pairs_up_to():
        a, b = cands[i], cands[j]
        if a.rec.id in slated or b.rec.id in slated:
            continue
        union = a.tokset | b.tokset
        if len(union) < MIN_DUP_UNION:
            continue
        jac = jaccard(a.tokset, b.tokset)
        con = containment(a.tokset, b.tokset)
        if not (jac >= JACCARD_NEAR_DUP or con >= CONTAINMENT_NEAR_DUP):
            continue
        if not _same_sequence(a.toks, b.toks):
            continue
        if a.neg != b.neg:
            # Negation-blindness veto: "not"/"no" are stopwords, so opposite
            # facts ("is friendly" vs "is not friendly") can share identical
            # content-token sets with identical order — everything above
            # fires, but "the same fact worded twice" would be a lie. Veto
            # the dedup and reroute to a contradiction-flag: the honest
            # category for opposites. Zero mutation either way; the waker
            # decides.
            prop = emit(
                "contradiction-flag", a.rec, b.rec, None, None,
                f"{a.rec.id} and {b.rec.id} look identical after stopword "
                f"stripping (jaccard {jac:.0%}, same token order) but differ "
                f"in negation polarity ({a.neg} vs {b.neg} negation markers): "
                f"possible opposite facts, not the same fact worded twice",
                "",
                jac)
            if prop is not None:
                stats["flags"] += 1
            continue
        winner = _richer(a.rec, b.rec)
        loser = b.rec if winner is a.rec else a.rec
        prop = emit(
            "dedup", a.rec, b.rec, winner, loser,
            f"{loser.id} restates {winner.id} "
            f"(jaccard {jac:.0%}, containment {con:.0%}, same token order); "
            f"kept the richer wording",
            f"same fact worded twice (jaccard {jac:.0%}, containment {con:.0%}); "
            f"kept the richer wording",
            max(jac, con))
        if prop is not None:
            stats["near"] += 1

    # deep pass: supersession vs contradiction flags.
    for i, j in pairs_up_to():
        a, b = cands[i], cands[j]
        if a.rec.id in slated or b.rec.id in slated:
            continue
        if len(a.subject) < 2 or len(b.subject) < 2:
            continue
        subj = jaccard(a.subject, b.subject)
        if subj < SUBJECT_OVERLAP_SUPERSEDE:
            continue
        body = jaccard(a.tokset, b.tokset)
        if BODY_JACCARD_SUPERSEDE_LO <= body < BODY_JACCARD_SUPERSEDE_HI:
            if a.sneg != b.sneg:
                # Stopword-negation veto, supersede band. "not" is a
                # stopword, so "I do not love evening walks" vs "I love
                # morning walks" lands here (body 0.60) with the Jaccard
                # machinery blind to the polarity flip. Minting a
                # supersede would archive the affirmative under the false
                # rationale "same subject stated again later" — the
                # deterministic machinery cannot distinguish retraction
                # from complement, so it cannot honestly claim restatement.
                # Reroute to a contradiction-flag, mirroring the near-dup
                # veto: zero mutation, the waker disposes.
                # ("never" is a CONTENT token, so a "never"-mismatched pair
                # keeps the ordinary supersede path: arguably-correct
                # belief update, and the similarity math can see the
                # difference.)
                prop = emit(
                    "contradiction-flag", a.rec, b.rec, None, None,
                    f"{a.rec.id} and {b.rec.id} share a subject (overlap "
                    f"{subj:.0%}, body {body:.0%}) but differ in stopword "
                    f"negation polarity ({a.sneg} vs {b.sneg} "
                    f"'not'/'no'/'nor' markers or n't contractions): possible "
                    f"retraction or complementary facts, not a restatement",
                    "",
                    body)
                if prop is not None:
                    stats["flags"] += 1
                continue
            winner = _newer(a.rec, b.rec)
            loser = b.rec if winner is a.rec else a.rec
            prop = emit(
                "supersede", a.rec, b.rec, winner, loser,
                f"{loser.id} states the same subject as {winner.id} "
                f"(subject overlap {subj:.0%}, body {body:.0%}); newer "
                f"record {winner.id} wins",
                f"same subject stated again later (subject overlap {subj:.0%}, "
                f"body {body:.0%}); newer record {winner.id} wins, older "
                f"{loser.id} archived",
                body)
            if prop is not None:
                stats["supersede"] += 1
        elif BODY_JACCARD_FLAG_LO <= body < BODY_JACCARD_FLAG_HI:
            prop = emit(
                "contradiction-flag", a.rec, b.rec, None, None,
                f"{a.rec.id} and {b.rec.id} share a subject "
                f"(overlap {subj:.0%}) but disagree in the body "
                f"({body:.0%}): possible conflict, not auto-resolved, "
                f"overlap too low to be safe",
                "",
                body)
            if prop is not None:
                stats["flags"] += 1

    return {"stats": stats, "proposals": new_props}


# -- dry-run / accept / reject / quarantine -----------------------------------

def dry_run(db_path: str | Path, journal_base: str | Path,
            archive_dir: str | Path) -> dict:
    """Scan the store read-only; persist new proposals to the journal.

    The ONLY write this path performs is the proposal journal append.
    No archive writes, no availability changes, no writes to mind.db —
    load_records() opens the database ``mode=ro``, so a write is not
    merely avoided but impossible.
    """
    records, tick = load_records(db_path)
    journal = load_journal(journal_base)
    avail = _read_availability(archive_dir)
    report = scan(records, tick, journal,
                  excluded=frozenset(avail["excluded"]))
    fresh = report["proposals"]
    if fresh:
        _save_journal(journal_base, journal)
    report["journal_new"] = len(fresh)
    report["store_tick"] = tick
    return report


def _verify_pair(records: list[_Rec], prop: dict,
                 archived: set[str]) -> tuple[_Rec, _Rec, str | None]:
    """Retry-safe preconditions for accept. Returns (a, b, failure_reason)."""
    by_id = {r.id: r for r in records}
    a = by_id.get(prop["a"])
    b = by_id.get(prop["b"])
    if a is None or b is None:
        missing = prop["a"] if a is None else prop["b"]
        return a, b, f"record {missing} no longer in the store"
    if a.id in archived or b.id in archived:
        gone = a.id if a.id in archived else b.id
        return a, b, f"record {gone} already archived"
    if exact_hash(a.text) != prop["a_hash"] or exact_hash(b.text) != prop["b_hash"]:
        return a, b, "record text no longer matches the proposal hash"
    return a, b, None


def accept(db_path: str | Path, journal_base: str | Path,
           archive_dir: str | Path, ids: list[int]) -> list[dict]:
    """Apply accepted proposals. Retry-safe: on ANY failure the proposal is
    left pending — a proposal is never burned by a failed apply."""
    records, tick = load_records(db_path)   # read-only
    journal = load_journal(journal_base)
    avail = _read_availability(archive_dir)
    archived = set(avail["excluded"])
    outcomes = []
    for raw_id in ids:
        try:
            pid = int(raw_id)
        except (TypeError, ValueError):
            outcomes.append({"id": raw_id, "ok": False,
                             "error": f"not a proposal id: {raw_id!r}"})
            continue
        prop = journal["proposals"].get(str(pid))
        if prop is None:
            outcomes.append({"id": pid, "ok": False, "error": "no such proposal"})
            continue
        if prop["status"] != "pending":
            outcomes.append({"id": pid, "ok": False,
                             "error": f"not pending (status={prop['status']})"})
            continue
        # Idempotent retry: a previous attempt archived the loser under this
        # proposal id but died before flipping the status. Finish the flip;
        # archive nothing twice. (Without this, _verify_pair's archived
        # check would fail the retry forever on "already archived".)
        loser_id = prop.get("loser")
        if loser_id is not None:
            prior = avail["excluded"].get(loser_id)
            if prior is not None and prior.get("proposal") == pid:
                prop["status"] = "accepted"
                prop["resolved_tick"] = tick
                _save_journal(journal_base, journal)
                outcomes.append({"id": pid, "ok": True, "kind": prop["kind"],
                                 "loser": loser_id,
                                 "note": "already archived under this proposal; status flipped"})
                continue
        a, b, failure = _verify_pair(records, prop, archived)
        if failure is not None:
            outcomes.append({"id": pid, "ok": False, "error": failure})
            continue  # left pending, never burned
        try:
            if prop["kind"] == "contradiction-flag":
                # Flags mutate nothing; accepting = waker reviewed it.
                pass
            else:
                loser = next(r for r in records if r.id == loser_id)
                _archive_entry(archive_dir, loser_id, tick,
                               ARCHIVE_OP_DEDUP if prop["kind"] == "dedup"
                               else ARCHIVE_OP_SUPERSEDE,
                               prop["reason"], loser.text, pid)
                avail = _read_availability(archive_dir)
                archived = set(avail["excluded"])
            prop["status"] = "accepted"
            prop["resolved_tick"] = tick
            _save_journal(journal_base, journal)
        except Exception as exc:  # noqa: BLE001 — any failure leaves it pending
            outcomes.append({"id": pid, "ok": False,
                             "error": f"{type(exc).__name__}: {exc}"})
            continue
        outcomes.append({"id": pid, "ok": True, "kind": prop["kind"],
                         "loser": prop["loser"]})
    return outcomes


def reject(journal_base: str | Path, pid: int, reason: str,
           store_tick: int = 0) -> dict:
    """Reject a proposal; the reason is kept. Fail closed without one."""
    if not reason or not reason.strip():
        raise ConsolidationError("--reject needs --reason \"...\" (fail closed)")
    journal = load_journal(journal_base)
    prop = journal["proposals"].get(str(pid))
    if prop is None:
        raise ConsolidationError(f"no such proposal: {pid}")
    if prop["status"] != "pending":
        raise ConsolidationError(
            f"proposal {pid} is not pending (status={prop['status']})")
    prop["status"] = "rejected"
    prop["rejected_reason"] = reason.strip()
    prop["resolved_tick"] = store_tick
    _save_journal(journal_base, journal)
    return prop


def quarantine(db_path: str | Path, archive_dir: str | Path,
               record_id: str, reason: str) -> dict:
    """Archive one record immediately with a written audit reason.

    The escape hatch for synthetic test residue (garden-gate salience
    probes, prompt-9999, resolve/history probes). Explicit and audited;
    no proposal needed. Fail closed without a reason.
    """
    if not reason or not reason.strip():
        raise ConsolidationError("--quarantine needs --reason \"...\" (fail closed)")
    records, tick = load_records(db_path)   # read-only
    rec = next((r for r in records if r.id == record_id), None)
    if rec is None:
        raise ConsolidationError(f"no such record: {record_id}")
    avail = _read_availability(archive_dir)
    if record_id in avail["excluded"]:
        raise ConsolidationError(f"record {record_id} is already archived")
    _archive_entry(archive_dir, record_id, tick, ARCHIVE_OP_QUARANTINE,
                   reason.strip(), rec.text, None)
    return {"record_id": record_id, "archived_tick": tick,
            "op": ARCHIVE_OP_QUARANTINE, "reason": reason.strip()}
