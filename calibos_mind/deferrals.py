"""Weariness-gated prompt deferral with categorized buckets.

Domain 19 (Agency), 2026-11-12, spec research/specs/2026-11-12-deferral-buckets.md.

The waker contract gains a third option. Today a wake must answer a prompt
or let it pass silently. After this change a wake may *defer* a prompt:
place it in a named bucket with a revisit time and a reminder to come
back. Answer / pass / defer.

Design rules (mirroring goals.py / personality.py):
- Sidecar ``deferrals.json`` (next to ``goals.json``)::
    ``{"buckets": {"<name>": bucket, ...},
       "items": {"<prompt-id>": item, ...},
       "releases": [release, ...],
       "decisions": [decision, ...]}``
  A bucket is ``{"gate": <float>, "revisit_window": [min_hours, max_hours],
  "escalation_threshold": <int>, "escalation_step": <float>,
  "forgotten_penalty": <float>, "forgotten_penalty_cap": <float>,
  "forgotten": <int>}``. An item is ``{"prompt_id", "bucket", "text",
  "urgency", "defer_count", "priority", "revisit_tick", "status",
  "deferred_tick", "reminder_id", "history": [...]}``. A release is
  ``{"prompt_id", "bucket", "tick", "reason", "reminder_id"}``.
  A decision is ``{"tick", "action", "bucket", "detail", "reason"}``.
  Default starting buckets: ``work`` and ``personal``. Nothing else
  seeded. Bucket config is per bucket: work and personal do not share
  one threshold — what is cheap to defer in one bucket may be expensive
  in the other.
- Categories are the mind's own decision (Jay, 2026-11-12): the
  inhabitant chooses, adds, renames, and removes its buckets as it
  sees fit; Jay may suggest, never prescribe. The config lives in
  this sidecar — the mind's own state — never in the cartridge and
  never in a parent setting, and every category change is recorded
  in ``decisions`` as the mind's own decision, with its reason. The
  ``mind bucket`` commands are the inhabitant's own management
  interface, used during wakes — not a parent settings panel.
- Weariness reuses the existing friction machinery (``friction.weariness``
  on the body's needs) and takes the larger of that and a load term from
  open commitments + pending expectations + inbox depth. There is one
  weariness, not two: this module composes the existing one, it does not
  invent a parallel concept.
- Time base is engine ticks. The revisit window is configured in hours
  and converted with TICKS_PER_HOUR = 0.5: one tick is treated as two
  hours of lived time, anchored to the wake cadence (``mind heartbeat
  --ticks 3`` every 6 hours). The revisit time is a time, not "next
  wake": it is drawn randomly inside the bucket's window. The draw is
  seeded from recorded state (bucket, prompt id, defer count, tick), so
  identical sequences produce byte-identical sidecars — the jitter feels
  like remembering, not like a cron, and it is still deterministic.
- The reminder is a real engine commitment, minted by the CLI (this
  module never touches the store): actor "self", description naming the
  item, the bucket, and the revisit tick, due at the revisit tick. A
  defer with no reminder is a bug, not a defer — the tracker records
  the reminder id on the item, and the CLI resolves the old reminder
  when an item is re-deferred, answered, or released.
- Escalation: each defer increments the item's defer count. Past the
  bucket's escalation threshold (default 3), the item's priority rises
  and the gate gets stricter for it — the mind becomes less willing to
  defer the same thing again.
- Forgotten-item feedback: an item that passes its revisit window
  without action counts against its bucket's deferral allowance. A
  bucket with a history of forgotten items defers less readily
  afterward. The gate at decision time counts both the bucket's
  recorded ``forgotten`` total and items currently past their window
  but not yet scanned, so the feedback bites even before a revisit
  runs; the revisit scan folds the unscanned ones into ``forgotten``
  exactly once (status moves deferred -> overdue).
- The revisit ritual (``revisit()``) surfaces a random thought — one
  item drawn at random from the bucket's contents — and a remembering
  pass over the whole bucket, not just the single item due. It presents;
  it never answers, releases, or mints a thought on its own.
- Corrupt sidecar fails loud: invalid JSON, a non-dict top level, a
  malformed bucket/item/release section, or a cross-field contradiction
  (an item naming a missing bucket, a revisit tick before its defer
  tick) raises ValueError at construction. Never silent-delete, never
  silent-zero. (``cmd_init --force`` wipes the sidecar before
  construction, so a corrupt file can never brick a reseed.)
- Read-only commands and dream ticks never construct this tracker: the
  only constructors live on the defer / revisit / settle / bucket write
  paths. ``mind init --force`` wipes the sidecar.
- Determinism: identical sequences produce byte-identical sidecars. No
  wall clock, no unseeded RNG. (Reminder commitment ids are store
  input, minted by the CLI: two runs that mint different ids
  legitimately differ — the same caveat goals.py records for
  commitment ids.)
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

from . import friction

#: Ticks per hour of lived time for revisit scheduling. One tick is
#: treated as two hours, anchored to the wake cadence: `mind heartbeat
#: --ticks 3` every 6 hours. An approximation, documented here rather
#: than hidden in a conversion.
TICKS_PER_HOUR = 0.5

#: Default revisit window in hours (spec: 4-24, configurable per bucket).
DEFAULT_REVISIT_WINDOW = (4, 24)

#: Default weariness gate per bucket. Work is cheaper to defer than
#: personal: Jay's own report is that work things are the ones that get
#: put off till later. Defaults, not law — configurable per bucket.
DEFAULT_GATES = {"work": 0.55, "personal": 0.70}

#: Defers past this count escalate: priority rises, gate gets stricter.
DEFAULT_ESCALATION_THRESHOLD = 3

#: Gate/priority step per defer past the escalation threshold.
DEFAULT_ESCALATION_STEP = 0.1

#: Gate increase per forgotten item in the bucket.
DEFAULT_FORGOTTEN_PENALTY = 0.05

#: Cap on the total forgotten-item gate increase.
DEFAULT_FORGOTTEN_PENALTY_CAP = 0.25

#: Priority of a never-deferred item; escalation adds to this.
BASE_PRIORITY = 0.5

#: Hard cap on any effective gate: a bucket must never become
#: un-deferrable-in-principle, only reluctant.
GATE_CAP = 0.95

#: Open items (commitments + expectations + inbox prompts) at which the
#: load term saturates at 1.0.
LOAD_SCALE = 10

#: The only urgency values a prompt may carry. High urgency is never
#: deferred on weariness alone.
URGENCIES = ("low", "high")

#: Item statuses. "deferred" = awaiting its revisit time; "overdue" =
#: past its revisit window without action (counted as forgotten);
#: "answered"/"released" = closed by the waker, kept in the record.
ITEM_STATUSES = ("deferred", "overdue", "answered", "released")


def _is_int(value) -> bool:
    """A genuine int count: bools (an int subclass) never count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value) -> bool:
    """A genuine finite number: bools never count, NaN/inf never count."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def default_bucket_config(name: str) -> dict:
    """The seeded config for a default bucket."""
    return {
        "gate": DEFAULT_GATES.get(name, 0.6),
        "revisit_window": [DEFAULT_REVISIT_WINDOW[0],
                           DEFAULT_REVISIT_WINDOW[1]],
        "escalation_threshold": DEFAULT_ESCALATION_THRESHOLD,
        "escalation_step": DEFAULT_ESCALATION_STEP,
        "forgotten_penalty": DEFAULT_FORGOTTEN_PENALTY,
        "forgotten_penalty_cap": DEFAULT_FORGOTTEN_PENALTY_CAP,
        "forgotten": 0,
    }


def weariness(needs: dict, open_commitments: int = 0,
              open_expectations: int = 0, inbox_depth: int = 0) -> float:
    """0.0 (rested) .. 1.0 (exhausted), from body + open load.

    The body term is the existing friction machinery
    (``friction.weariness`` on focus/fatigue). The load term is the
    count of open commitments, pending expectations, and waiting inbox
    prompts, saturating at LOAD_SCALE. The larger of the two wins:
    either a worn body or a full plate can gate a deferral. One
    weariness, composed — not a second weariness invented alongside
    the first.
    """
    body = friction.weariness(needs)
    load = max(0, open_commitments) + max(0, open_expectations) \
        + max(0, inbox_depth)
    load_term = min(1.0, load / LOAD_SCALE)
    return max(body, load_term)


def _window_ticks(window_hours) -> tuple[int, int]:
    """A [min, max] hour window as [min, max] ticks (at least 1 each)."""
    lo = max(1, round(window_hours[0] * TICKS_PER_HOUR))
    hi = max(lo, round(window_hours[1] * TICKS_PER_HOUR))
    return lo, hi


def draw_revisit_tick(bucket_name: str, prompt_id: str, defer_count: int,
                      tick: int, window_hours) -> int:
    """A revisit tick drawn randomly inside the bucket's window.

    Deterministic: the draw is seeded from recorded state (bucket,
    prompt id, defer count, tick), so identical sequences draw
    identical revisit times — and different ticks/counts draw
    (almost always) different ones. The jitter is real, not a fixed
    offset, and it is still replayable.
    """
    lo, hi = _window_ticks(window_hours)
    seed = f"{bucket_name}:{prompt_id}:{defer_count}:{tick}"
    return tick + random.Random(seed).randint(lo, hi)


def _escalation(bucket: dict, defer_count: int) -> float:
    """Gate/priority bonus for an item deferred this many times.

    Zero until the defer count passes the bucket's escalation
    threshold, then one step per further defer. At defer_count ==
    threshold the bonus is exactly one step: an item deferred 3
    times is strictly escalated over one deferred for the first time.
    """
    past = defer_count - bucket["escalation_threshold"] + 1
    if past <= 0:
        return 0.0
    return past * bucket["escalation_step"]


def priority_for(bucket: dict, defer_count: int) -> float:
    """An item's priority: base, rising past the escalation threshold."""
    return min(1.0, BASE_PRIORITY + _escalation(bucket, defer_count))


class DeferralRefused(ValueError):
    """The gate refused a deferral: the reason is the message."""


class DeferralTracker:
    """Deferral-bucket sidecar (``deferrals.json``, local-only).

    ``data`` is ``{"buckets": {...}, "items": {...}, "releases": [...]}``.
    The constructor only reads the sidecar; state moves exclusively
    via the methods below on the defer / revisit / settle / bucket
    write paths. ``save()`` writes deterministically (sorted keys,
    fixed form).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict = {
            "buckets": {name: default_bucket_config(name)
                        for name in ("work", "personal")},
            "items": {},
            "releases": [],
            "decisions": [],
        }
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise ValueError(
                    f"corrupt deferrals sidecar {self.path}: "
                    f"invalid JSON ({exc})"
                ) from exc
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"corrupt deferrals sidecar {self.path}: top level is "
                    f"not an object")
            for key in ("buckets", "items", "releases", "decisions"):
                if key not in loaded:
                    raise ValueError(
                        f"corrupt deferrals sidecar {self.path}: missing "
                        f"'{key}' section — the writer always writes all "
                        f"four, so a missing section is malformed, not "
                        f"empty")
            buckets = loaded.get("buckets", {})
            if not isinstance(buckets, dict):
                raise ValueError(
                    f"corrupt deferrals sidecar {self.path}: 'buckets' is "
                    f"not an object")
            for name, cfg in buckets.items():
                self._validate_bucket(name, cfg)
            items = loaded.get("items", {})
            if not isinstance(items, dict):
                raise ValueError(
                    f"corrupt deferrals sidecar {self.path}: 'items' is "
                    f"not an object")
            for pid, item in items.items():
                self._validate_item(pid, item, set(buckets))
            releases = loaded.get("releases", [])
            if not isinstance(releases, list):
                raise ValueError(
                    f"corrupt deferrals sidecar {self.path}: 'releases' is "
                    f"not a list")
            for rel in releases:
                self._validate_release(rel)
            decisions = loaded.get("decisions", [])
            if not isinstance(decisions, list):
                raise ValueError(
                    f"corrupt deferrals sidecar {self.path}: 'decisions' "
                    f"is not a list")
            for dec in decisions:
                self._validate_decision(dec)
            self.data["buckets"] = {
                name: {key: cfg.get(key, default_bucket_config(name)[key])
                       for key in default_bucket_config(name)}
                for name, cfg in buckets.items()}
            self.data["items"] = {pid: dict(item)
                                  for pid, item in items.items()}
            self.data["releases"] = [dict(rel) for rel in releases]
            self.data["decisions"] = [dict(dec) for dec in decisions]

    # -- validation -------------------------------------------------------

    def _validate_bucket(self, name, cfg) -> None:
        path = self.path

        def bad(why: str):
            raise ValueError(
                f"corrupt deferrals sidecar {path}: bucket {name!r} {why}")

        if not isinstance(name, str) or not name.strip():
            bad("has a non-string or empty name")
        if not isinstance(cfg, dict):
            bad("is not an object")
        gate = cfg.get("gate")
        if not _is_num(gate) or not 0.0 <= gate <= 1.0:
            bad("has no 'gate' in [0, 1]")
        window = cfg.get("revisit_window")
        if (not isinstance(window, (list, tuple)) or len(window) != 2
                or not _is_num(window[0]) or not _is_num(window[1])
                or not 0 < window[0] <= window[1]):
            bad("has no 'revisit_window' of [min_hours, max_hours] with "
                "0 < min <= max")
        threshold = cfg.get("escalation_threshold")
        if not _is_int(threshold) or threshold < 0:
            bad("has no non-negative integer 'escalation_threshold'")
        step = cfg.get("escalation_step")
        if not _is_num(step) or not 0.0 <= step <= 1.0:
            bad("has no 'escalation_step' in [0, 1]")
        penalty = cfg.get("forgotten_penalty")
        if not _is_num(penalty) or penalty < 0:
            bad("has no non-negative 'forgotten_penalty'")
        cap = cfg.get("forgotten_penalty_cap")
        if not _is_num(cap) or cap < 0:
            bad("has no non-negative 'forgotten_penalty_cap'")
        forgotten = cfg.get("forgotten")
        if not _is_int(forgotten) or forgotten < 0:
            bad("has no non-negative integer 'forgotten'")

    def _validate_item(self, pid, item, bucket_names) -> None:
        path = self.path

        def bad(why: str):
            raise ValueError(
                f"corrupt deferrals sidecar {path}: item {pid!r} {why}")

        if not isinstance(pid, str) or not pid:
            bad("has a non-string or empty id")
        if not isinstance(item, dict):
            bad("is not an object")
        if item.get("prompt_id") != pid:
            bad("'prompt_id' does not match its key")
        bucket = item.get("bucket")
        if item.get("status") in ("answered", "released"):
            # A closed item's bucket is history — where it was parked.
            # The bucket may since have been removed; the record stays
            # valid. Only open items must name a live bucket.
            if not isinstance(bucket, str) or not bucket:
                bad("has no string 'bucket'")
        elif bucket not in bucket_names:
            bad(f"names missing bucket {bucket!r}")
        if not isinstance(item.get("text"), str):
            bad("has no string 'text'")
        if item.get("urgency") not in URGENCIES:
            bad(f"has no 'urgency' in {URGENCIES}")
        count = item.get("defer_count")
        if not _is_int(count) or count < 1:
            bad("has no positive integer 'defer_count'")
        priority = item.get("priority")
        if not _is_num(priority) or not 0.0 <= priority <= 1.0:
            bad("has no 'priority' in [0, 1]")
        revisit = item.get("revisit_tick")
        if not _is_int(revisit) or revisit < 0:
            bad("has no non-negative integer 'revisit_tick'")
        if item.get("status") not in ITEM_STATUSES:
            bad(f"has no 'status' in {ITEM_STATUSES}")
        deferred = item.get("deferred_tick")
        if not _is_int(deferred) or deferred < 0:
            bad("has no non-negative integer 'deferred_tick'")
        if revisit < deferred:
            bad("'revisit_tick' precedes 'deferred_tick'")
        reminder = item.get("reminder_id")
        if reminder is not None and not isinstance(reminder, str):
            bad("has a non-string 'reminder_id'")
        history = item.get("history")
        if not isinstance(history, list):
            bad("has no 'history' list")

    def _validate_release(self, rel) -> None:
        path = self.path

        def bad(why: str):
            raise ValueError(
                f"corrupt deferrals sidecar {path}: release {rel!r} {why}")

        if not isinstance(rel, dict):
            bad("is not an object")
        if not isinstance(rel.get("prompt_id"), str) \
                or not rel["prompt_id"]:
            bad("has no string 'prompt_id'")
        if not isinstance(rel.get("bucket"), str) or not rel["bucket"]:
            bad("has no string 'bucket'")
        if not _is_int(rel.get("tick")) or rel["tick"] < 0:
            bad("has no non-negative integer 'tick'")
        if not isinstance(rel.get("reason"), str) or not rel["reason"]:
            bad("has no string 'reason'")
        reminder = rel.get("reminder_id")
        if reminder is not None and not isinstance(reminder, str):
            bad("has a non-string 'reminder_id'")

    def _validate_decision(self, dec) -> None:
        path = self.path

        def bad(why: str):
            raise ValueError(
                f"corrupt deferrals sidecar {path}: decision {dec!r} {why}")

        if not isinstance(dec, dict):
            bad("is not an object")
        if dec.get("action") not in ("add", "rename", "set", "remove"):
            bad("has no 'action' in ('add', 'rename', 'set', 'remove')")
        if not isinstance(dec.get("bucket"), str) or not dec["bucket"]:
            bad("has no string 'bucket'")
        if not isinstance(dec.get("detail"), str):
            bad("has no string 'detail'")
        if not isinstance(dec.get("reason"), str):
            bad("has no string 'reason'")
        if not _is_int(dec.get("tick")) or dec["tick"] < 0:
            bad("has no non-negative integer 'tick'")

    # -- read access ------------------------------------------------------

    @property
    def buckets(self) -> dict:
        """A copy of the bucket map."""
        return {name: dict(cfg)
                for name, cfg in self.data["buckets"].items()}

    @property
    def items(self) -> dict:
        """A copy of the deferred-item map."""
        return {pid: dict(item)
                for pid, item in self.data["items"].items()}

    @property
    def releases(self) -> list:
        """A copy of the written release record."""
        return [dict(rel) for rel in self.data["releases"]]

    @property
    def decisions(self) -> list:
        """A copy of the category-decision log (the mind's own record
        of choosing its buckets, with reasons)."""
        return [dict(dec) for dec in self.data["decisions"]]

    def _record_decision(self, tick: int, action: str, bucket: str,
                         detail: str, reason: str) -> None:
        """Append one category decision, the mind's own, with its reason."""
        self.data["decisions"].append({
            "action": action,
            "bucket": bucket,
            "detail": detail,
            "reason": reason,
            "tick": tick,
        })

    def _overdue_now(self, bucket_name: str, now_tick: int) -> int:
        """Deferred items in this bucket already past their window.

        Counted live at decision time, so forgotten-item feedback bites
        even before any revisit scan has run. Items the scan already
        folded into ``forgotten`` carry status "overdue" and are not
        counted twice.
        """
        return sum(
            1 for item in self.data["items"].values()
            if item["bucket"] == bucket_name
            and item["status"] == "deferred"
            and item["revisit_tick"] <= now_tick)

    def effective_gate(self, bucket_name: str, defer_count: int,
                       now_tick: int) -> float:
        """The weariness threshold for deferring into this bucket now.

        Base gate + escalation for repeat defers + forgotten-item
        penalty, capped at GATE_CAP. A bucket with forgotten items
        defers less readily than an identical bucket without them,
        at the same weariness and the same prompt.
        """
        bucket = self.data["buckets"][bucket_name]
        forgotten = bucket["forgotten"] \
            + self._overdue_now(bucket_name, now_tick)
        penalty = min(bucket["forgotten_penalty_cap"],
                      bucket["forgotten_penalty"] * forgotten)
        return min(GATE_CAP,
                   bucket["gate"] + _escalation(bucket, defer_count)
                   + penalty)

    def decide(self, *, bucket_name: str, urgency: str, weariness_value: float,
               defer_count: int, now_tick: int) -> tuple[bool, str, float]:
        """The gate, as a pure decision: (allowed, reason, effective_gate).

        High urgency is never deferred on weariness alone. Otherwise
        the deferral is allowed exactly when weariness meets the
        bucket's effective gate — the gate actually gates.
        """
        if bucket_name not in self.data["buckets"]:
            raise ValueError(f"no bucket {bucket_name!r}")
        if urgency not in URGENCIES:
            raise ValueError(f"unknown urgency {urgency!r}")
        gate = self.effective_gate(bucket_name, defer_count, now_tick)
        if urgency == "high":
            return False, "high urgency — never deferred on weariness alone", gate
        if weariness_value < gate:
            return False, (f"weariness {weariness_value:.2f} below the "
                           f"{bucket_name} gate {gate:.2f}"), gate
        return True, "weariness meets the gate", gate

    # -- bucket operations ------------------------------------------------

    def add_bucket(self, name: str, tick: int, reason: str = "",
                   **overrides) -> dict:
        """Add a bucket with the default config plus any overrides.

        Overrides are the config keys (gate, revisit_window,
        escalation_threshold, escalation_step, forgotten_penalty,
        forgotten_penalty_cap); each is validated the same way a
        loaded sidecar is. A duplicate name or bad config raises
        ValueError — fail loud, never silently reconfigure. The add
        is recorded in the decision log as the mind's own decision,
        with its reason: categories are chosen by the inhabitant,
        never prescribed.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("bucket name must be a non-empty string")
        name = name.strip()
        if name in self.data["buckets"]:
            raise ValueError(f"bucket {name!r} already exists")
        if not _is_int(tick) or tick < 0:
            raise ValueError(
                f"add_bucket needs a non-negative tick, got {tick!r}")
        if not isinstance(reason, str):
            raise ValueError("bucket reason must be a string")
        cfg = default_bucket_config(name)
        unknown = sorted(set(overrides) - set(cfg))
        if unknown:
            raise ValueError(f"unknown bucket config keys: {unknown}")
        cfg.update(overrides)
        self._validate_bucket(name, cfg)
        self.data["buckets"][name] = cfg
        lo, hi = cfg["revisit_window"]
        self._record_decision(
            tick, "add", name,
            f"added (gate {cfg['gate']:.2f}, revisit {lo}-{hi}h)",
            reason)
        return dict(cfg)

    def rename_bucket(self, old: str, new: str, tick: int,
                      reason: str = "") -> None:
        """Rename a bucket; its items move with it, history intact.

        The release log is history and keeps the old name as written.
        Recorded in the decision log as the mind's own decision.
        """
        if old not in self.data["buckets"]:
            raise ValueError(f"no bucket {old!r}")
        if not isinstance(new, str) or not new.strip():
            raise ValueError("bucket name must be a non-empty string")
        new = new.strip()
        if new in self.data["buckets"]:
            raise ValueError(f"bucket {new!r} already exists")
        if not _is_int(tick) or tick < 0:
            raise ValueError(
                f"rename_bucket needs a non-negative tick, got {tick!r}")
        if not isinstance(reason, str):
            raise ValueError("bucket reason must be a string")
        self.data["buckets"][new] = self.data["buckets"].pop(old)
        for item in self.data["items"].values():
            if item["bucket"] == old:
                item["bucket"] = new
                item["history"].append(
                    {"tick": tick,
                     "action": f"bucket renamed {old} -> {new}"})
        self._record_decision(tick, "rename", new, f"{old} -> {new}",
                              reason)

    def set_bucket(self, name: str, tick: int, reason: str = "",
                   **overrides) -> dict:
        """Reconfigure a bucket (gate, window, escalation, penalties).

        Only the given keys change; the rest stand. Unknown keys or
        out-of-range values raise ValueError — fail loud, never
        silently reconfigure. Recorded in the decision log as the
        mind's own decision, with its reason.
        """
        if name not in self.data["buckets"]:
            raise ValueError(f"no bucket {name!r}")
        if not _is_int(tick) or tick < 0:
            raise ValueError(
                f"set_bucket needs a non-negative tick, got {tick!r}")
        if not isinstance(reason, str):
            raise ValueError("bucket reason must be a string")
        cfg = dict(self.data["buckets"][name])
        unknown = sorted(set(overrides) - set(cfg))
        if unknown:
            raise ValueError(f"unknown bucket config keys: {unknown}")
        cfg.update(overrides)
        self._validate_bucket(name, cfg)
        self.data["buckets"][name] = cfg
        detail = ", ".join(f"{k}={overrides[k]}"
                           for k in sorted(overrides))
        self._record_decision(tick, "set", name, detail or "no change",
                              reason)
        return dict(cfg)

    def remove_bucket(self, name: str, tick: int, reassign_to: str | None = None,
                      reason: str = "") -> list:
        """Remove a bucket. Its contents are never silently dropped.

        With ``reassign_to``: every open item moves to that bucket
        (revisit times, counts, and priorities stand; the move is
        written into each item's history). A moved item that is
        already overdue carries its forgotten-item feedback into the
        destination bucket's ``forgotten`` count, so the gate still
        counts it until it is acted on. Without it: every open
        item is released, each with a written entry in the release
        log naming the item, the bucket, the tick, and the reason.
        Already-closed items (answered/released) are never
        re-closed: they keep their status, gain a history note, and
        move with a reassign (their bucket is history otherwise).
        Returns the affected open items (moved or released) so the
        caller can settle their reminder commitments. Removing a
        bucket with no items just drops it.
        The removal is recorded in the decision log as the mind's own
        decision, with its reason.
        """
        if name not in self.data["buckets"]:
            raise ValueError(f"no bucket {name!r}")
        if reassign_to is not None and reassign_to not in self.data["buckets"]:
            raise ValueError(f"no bucket {reassign_to!r} to reassign to")
        if reassign_to == name:
            raise ValueError("cannot reassign a bucket to itself")
        if not _is_int(tick) or tick < 0:
            raise ValueError(f"remove_bucket needs a non-negative tick, got {tick!r}")
        if not isinstance(reason, str):
            raise ValueError("bucket reason must be a string")
        moved = [item for item in self.data["items"].values()
                 if item["bucket"] == name]
        open_items = [item for item in moved
                      if item["status"] in ("deferred", "overdue")]
        for item in open_items:
            if reassign_to is not None:
                if item["status"] == "overdue":
                    # Carry the forgotten-item feedback into the
                    # destination bucket: this item was already folded
                    # into the old bucket's `forgotten` count by the
                    # revisit scan (status "overdue" exists only after
                    # a scan fold), and that count dies with the old
                    # bucket. Exactly once: the live _overdue_now
                    # count excludes "overdue" items, and the scan
                    # never re-folds one, so this is never
                    # double-counted.
                    self.data["buckets"][reassign_to]["forgotten"] += 1
                    item["history"].append(
                        {"tick": tick,
                         "action": f"forgotten count carried to "
                                   f"{reassign_to} (bucket {name} "
                                   f"removed)"})
                item["bucket"] = reassign_to
                item["history"].append(
                    {"tick": tick,
                     "action": f"moved {name} -> {reassign_to} (bucket removed)"})
            else:
                item["status"] = "released"
                item["history"].append(
                    {"tick": tick,
                     "action": f"released (bucket {name} removed)"})
                self.data["releases"].append({
                    "prompt_id": item["prompt_id"],
                    "bucket": name,
                    "tick": tick,
                    "reason": f"bucket {name!r} removed",
                    "reminder_id": item["reminder_id"],
                })
        # Closed items keep their verdict; only their bucket pointer
        # moves (with a reassign) or stands as history (without one).
        for item in moved:
            if item["status"] in ("answered", "released"):
                if reassign_to is not None:
                    item["bucket"] = reassign_to
                item["history"].append(
                    {"tick": tick,
                     "action": f"bucket {name} removed; item already "
                               f"{item['status']}"})
        del self.data["buckets"][name]
        detail = (f"removed, {len(open_items)} item(s) moved to {reassign_to}"
                  if reassign_to is not None else
                  f"removed, {len(open_items)} item(s) released")
        self._record_decision(tick, "remove", name, detail, reason)
        return [dict(item) for item in open_items]

    # -- deferral ---------------------------------------------------------

    def defer(self, prompt_id: str, bucket_name: str, text: str,
              urgency: str, weariness_value: float, tick: int,
              reminder_id: str | None) -> dict:
        """Defer a prompt into a bucket, minting its reminder linkage.

        The caller mints the reminder commitment first (the CLI does,
        with the revisit tick drawn here) and passes its id; this
        method records the item and returns it. A first defer creates
        the item at defer_count 1; deferring an already-deferred item
        (re-defer) increments the count, redraws the revisit time, and
        recomputes the priority — the old reminder id is returned by
        the caller-visible path via the pre-call item, see
        ``previous_reminder()``. Deferring an already-closed item
        (answered/released) is refused outright: closed is terminal,
        and a settled item never comes back as deferred. The gate is
        enforced here as well as in ``decide()``: a call that would
        violate the gate raises DeferralRefused instead of recording
        anything. A defer with
        no reminder id is refused — a defer with no reminder is a bug,
        not a defer.
        """
        if bucket_name not in self.data["buckets"]:
            raise ValueError(f"no bucket {bucket_name!r}")
        if urgency not in URGENCIES:
            raise ValueError(f"unknown urgency {urgency!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("deferred item needs non-empty text")
        if reminder_id is None:
            raise ValueError(
                "defer needs a reminder id — a defer with no reminder "
                "is a bug, not a defer")
        if not _is_int(tick) or tick < 0:
            raise ValueError(f"defer needs a non-negative tick, got {tick!r}")
        existing = self.data["items"].get(prompt_id)
        if existing is not None \
                and existing["status"] in ("answered", "released"):
            # Closed is terminal: a settled item's answered record and
            # its release/close history are the written record, and
            # resurrecting it as deferred with a fresh reminder would
            # contradict them. Refuse before the gate is even consulted.
            raise DeferralRefused(
                f"item {prompt_id!r} is already "
                f"{existing['status']} — closed items are terminal")
        defer_count = 1 if existing is None else existing["defer_count"] + 1
        allowed, reason, _gate = self.decide(
            bucket_name=bucket_name, urgency=urgency,
            weariness_value=weariness_value, defer_count=defer_count - 1,
            now_tick=tick)
        if not allowed:
            raise DeferralRefused(
                f"defer refused for {prompt_id}: {reason}")
        bucket = self.data["buckets"][bucket_name]
        revisit_tick = draw_revisit_tick(
            bucket_name, prompt_id, defer_count, tick,
            bucket["revisit_window"])
        # The gate judged the item as it stood (defer_count - 1, the
        # count before this defer); the recorded priority reflects the
        # count after it — an item deferred 3 times carries strictly
        # higher priority than one deferred once.
        priority = priority_for(bucket, defer_count)
        history = [] if existing is None else list(existing["history"])
        history.append({"tick": tick,
                        "action": f"deferred to {bucket_name} "
                                  f"(#{defer_count}), revisit tick "
                                  f"{revisit_tick}"})
        item = {
            "prompt_id": prompt_id,
            "bucket": bucket_name,
            "text": text.strip(),
            "urgency": urgency,
            "defer_count": defer_count,
            "priority": priority,
            "revisit_tick": revisit_tick,
            "status": "deferred",
            "deferred_tick": tick,
            "reminder_id": reminder_id,
            "history": history,
        }
        self.data["items"][prompt_id] = item
        return dict(item)

    def previous_reminder(self, prompt_id: str) -> str | None:
        """The reminder id an item currently carries, or None."""
        item = self.data["items"].get(prompt_id)
        return None if item is None else item["reminder_id"]

    def plan_revisit_tick(self, bucket_name: str, prompt_id: str,
                          defer_count: int, tick: int) -> int:
        """The revisit tick a defer at this tick would draw.

        The CLI draws it before minting the reminder commitment, so
        the commitment can name the revisit time. Identical to the
        draw ``defer()`` performs for the same inputs.
        """
        bucket = self.data["buckets"][bucket_name]
        return draw_revisit_tick(bucket_name, prompt_id, defer_count,
                                 tick, bucket["revisit_window"])

    # -- revisit ----------------------------------------------------------

    def scan_overdue(self, now_tick: int, bucket_name: str | None = None) -> list:
        """Fold past-window items into their buckets' forgotten counts.

        Every item with status "deferred" whose revisit tick has
        passed becomes "overdue" and increments its bucket's
        ``forgotten`` by one. Exactly once per item: the status move
        is what prevents a second count. Returns the newly overdue
        items.
        """
        newly = []
        for item in self.data["items"].values():
            if item["status"] != "deferred":
                continue
            if bucket_name is not None and item["bucket"] != bucket_name:
                continue
            if item["revisit_tick"] > now_tick:
                continue
            item["status"] = "overdue"
            item["history"].append(
                {"tick": now_tick,
                 "action": "passed revisit window without action"})
            self.data["buckets"][item["bucket"]]["forgotten"] += 1
            newly.append(dict(item))
        return newly

    def revisit(self, now_tick: int, bucket_name: str | None = None) -> dict:
        """The revisit ritual: a random thought + a remembering pass.

        Scans the bucket(s) for overdue items first (folding them into
        the forgotten counts), then surfaces one item drawn at random
        from the bucket's open contents as the thought to consider
        now, plus the full contents for the remembering pass. The draw
        is seeded from the bucket, its open item ids, and the tick —
        deterministic on replay, varied across buckets and ticks.
        Returns ``{"tick", "buckets": {name: {"items": [...],
        "random_thought": item-or-None, "forgotten": n,
        "newly_overdue": [...]}}}``. Pure presentation: nothing is
        answered, released, or minted here.
        """
        if bucket_name is not None and bucket_name not in self.data["buckets"]:
            raise ValueError(f"no bucket {bucket_name!r}")
        if not _is_int(now_tick) or now_tick < 0:
            raise ValueError(
                f"revisit needs a non-negative tick, got {now_tick!r}")
        newly = self.scan_overdue(now_tick, bucket_name)
        newly_by_bucket: dict[str, list] = {}
        for item in newly:
            newly_by_bucket.setdefault(item["bucket"], []).append(item)
        names = [bucket_name] if bucket_name is not None else \
            sorted(self.data["buckets"])
        out: dict = {"tick": now_tick, "buckets": {}}
        for name in names:
            open_items = [dict(item) for item in self.data["items"].values()
                          if item["bucket"] == name
                          and item["status"] in ("deferred", "overdue")]
            open_items.sort(key=lambda i: (i["revisit_tick"], i["prompt_id"]))
            thought = None
            if open_items:
                seed = (f"revisit:{name}:"
                        f"{','.join(i['prompt_id'] for i in open_items)}:"
                        f"{now_tick}")
                thought = dict(random.Random(seed).choice(open_items))
            out["buckets"][name] = {
                "items": open_items,
                "random_thought": thought,
                "forgotten": self.data["buckets"][name]["forgotten"],
                "newly_overdue": newly_by_bucket.get(name, []),
            }
        return out

    # -- settle -----------------------------------------------------------

    def settle(self, prompt_id: str, action: str, tick: int,
               note: str = "") -> dict:
        """Close a deferred item as answered or released, with a record.

        Marks the item, appends the closing to its history, and — for
        a release — writes the release log entry. Returns the closed
        item. The caller settles the reminder commitment alongside.
        Already-closed items and unknown ids raise ValueError.
        """
        if action not in ("answered", "released"):
            raise ValueError(f"settle action must be 'answered' or "
                             f"'released', got {action!r}")
        item = self.data["items"].get(prompt_id)
        if item is None:
            raise ValueError(f"no deferred item {prompt_id!r}")
        if item["status"] in ("answered", "released"):
            raise ValueError(
                f"item {prompt_id!r} is already {item['status']}")
        if not _is_int(tick) or tick < 0:
            raise ValueError(f"settle needs a non-negative tick, got {tick!r}")
        item["status"] = action
        entry = {"tick": tick, "action": action}
        if note:
            entry["note"] = note
        item["history"].append(entry)
        if action == "released":
            self.data["releases"].append({
                "prompt_id": prompt_id,
                "bucket": item["bucket"],
                "tick": tick,
                "reason": note or "released at revisit",
                "reminder_id": item["reminder_id"],
            })
        return dict(item)

    # -- persistence ------------------------------------------------------

    def save(self) -> None:
        """Write the sidecar deterministically (sorted keys, fixed form)."""
        ordered = {
            "buckets": {
                name: {
                    "escalation_step": cfg["escalation_step"],
                    "escalation_threshold": cfg["escalation_threshold"],
                    "forgotten": cfg["forgotten"],
                    "forgotten_penalty": cfg["forgotten_penalty"],
                    "forgotten_penalty_cap": cfg["forgotten_penalty_cap"],
                    "gate": cfg["gate"],
                    "revisit_window": [cfg["revisit_window"][0],
                                       cfg["revisit_window"][1]],
                }
                for name, cfg in sorted(self.data["buckets"].items())
            },
            "items": {
                pid: {
                    "bucket": item["bucket"],
                    "defer_count": item["defer_count"],
                    "deferred_tick": item["deferred_tick"],
                    "history": [dict(h) for h in item["history"]],
                    "priority": item["priority"],
                    "prompt_id": item["prompt_id"],
                    "reminder_id": item["reminder_id"],
                    "revisit_tick": item["revisit_tick"],
                    "status": item["status"],
                    "text": item["text"],
                    "urgency": item["urgency"],
                }
                for pid, item in sorted(self.data["items"].items())
            },
            "releases": [dict(rel) for rel in self.data["releases"]],
            "decisions": [dict(dec) for dec in self.data["decisions"]],
        }
        self.path.write_text(
            json.dumps(ordered, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8")
