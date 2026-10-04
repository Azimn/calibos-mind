"""Critic round 2: re-verification + fresh attacks on the NEW machinery of
the interoceptive-realization-records mutation (domain 10, builder
fix-ups 2026-10-04).

Round-1 reds (consolidation supersede of distinct episodes, crash-window
double-mint, impossible-swing phantom) are re-run here and must be green;
see tests/adversarial/test_critic_realization_r1.py which passes 12/12.

This file attacks only the round-2 additions:
  - anchored template parser (false positives / determinism / 13 needs)
  - _realization_pair() guard narrowness (realization-vs-lived pairs,
    authored template doppelgangers, exact-dup sweep interplay)
  - write-ahead marks (kill-window recovery end to end, registry bounds,
    measured take/ack contract)
  - the 0.250001 gap_max nudge (exact value, drop-rule boundary, soak
    invariant)
  - regression genome recheck for the new code (shadowing, reseed,
    import direction, read-only/dream isolation static pins)

Every objection is a failing test or a concretely violated invariant;
prose-only critique is rejected per research/builder-critic-loop.md.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/adversarial/test_critic_realization_r2.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/

import calibos_mind.cli as cli
from calibos_mind.consolidate import _Rec, _realization_pair, scan
from calibos_mind.interoception import (
    SWING_GAP,
    InteroceptionTracker,
    is_realization_text,
    mint_realization,
    parse_realization_text,
    realization_band,
    realization_text,
)
from jelly_psiduck.firewall import NEED_LANGUAGE
from test_realization import (  # builder's harness, reused not duplicated
    _patched_cli,
    _restore,
)


def _tracker(tmp: Path, name: str = "interoception.json", **kw) -> InteroceptionTracker:
    return InteroceptionTracker(tmp / name, **kw)


def _rec(rid: str, index: int, tick: int, text: str,
         gen: str = "cognition") -> _Rec:
    return _Rec(id=rid, index=index, source="temporal", text=text,
                tick=tick, generated_by=gen, available=True)


def _scan_props(recs):
    journal = {"seq": 0, "proposals": {}}
    return scan(recs, store_tick=500, journal=journal)["proposals"]


def _close_one(tr, need, felt_open, actual, felt_close, tick):
    """Drive one open->close episode on a bare tracker; return the event."""
    tr.data["needs"][need] = {"felt": felt_open, "last_tick": tick, "level": 3}
    assert tr.update({need: actual}, tick + 1) == []
    tr.data["needs"][need]["felt"] = felt_close
    evs = tr.update({need: actual}, tick + 2)
    assert len(evs) == 1, evs
    return evs[0]


# ---------------------------------------------------------------------------
# A. Template parser: determinism + full writer coverage + adversarial
# near-misses.
# ---------------------------------------------------------------------------

def test_parser_roundtrips_all_13_needs_all_band_combos():
    """The writer's output must always parse: 13 needs x 16 felt/actual
    value combos (band vocabulary is whatever the tracker's own math
    renders), ticks small and large. Also: no contractions (the similarity
    vetoes tokenize on raw text), ASCII-only, byte-identical on repeat."""
    assert len(NEED_LANGUAGE) == 13
    values = (0.05, 0.30, 0.55, 0.85)
    n = 0
    for need in sorted(NEED_LANGUAGE):
        for swing_felt in values:
            for swing_actual in values:
                for swing_tick, conv_tick in ((41, 52), (100000, 100412)):
                    event = {"need": need, "swing_tick": swing_tick,
                             "swing_felt": swing_felt,
                             "swing_actual": swing_actual,
                             "gap_max": 0.62, "conv_tick": conv_tick}
                    text = realization_text(event)
                    assert text == realization_text(event)  # deterministic
                    assert "'" not in text, text  # no contractions, ever
                    text.encode("ascii")  # byte-identical replay premise
                    parsed = parse_realization_text(text)
                    assert parsed is not None, text
                    assert parsed["need"] == need, text
                    assert parsed["swing_tick"] == swing_tick, text
                    assert parsed["conv_tick"] == conv_tick, text
                    assert parsed["felt_band"] == realization_band(
                        swing_felt, need, floored=True), text
                    assert parsed["actual_band"] == realization_band(
                        swing_actual, need, floored=False), text
                    n += 1
    assert n == 13 * 16 * 2, n


def test_parser_rejects_adversarial_near_templates():
    """Near-template texts must NOT fullmatch: a false positive exempts a
    pair from consolidation gates. Each of these is measured, not assumed."""
    event = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.28,
             "swing_actual": 0.9, "gap_max": 0.62, "conv_tick": 52}
    good = realization_text(event)
    assert is_realization_text(good)
    bad = [
        "At tick 41 I felt thirst as settled today.",          # lived prose
        good + " Honestly.",                                    # extra clause
        "Note: " + good,                                        # leading text
        good.replace("tick 41 I", "tick 41  I"),                # double space
        good.replace("; by", ";\nby"),                          # newline
        good.replace("tick 41", "tick #41"),                    # hash tick
        good.replace("tick 41", "tick 41.0"),                   # float tick
        good.replace("tick 41", "tick -41"),                    # neg tick
        good.replace("thirst", "Thirst"),                       # capital need
        good.replace("thirst", "thirstiness"),                   # need prefix
        good.replace("thirst", "sleepiness"),                   # other need
        good.replace("settled", "parched"),                     # band synonym
        good.replace("settled", "very settled"),                # band phrase
        good[:-1] + "!",                                        # wrong ender
        good.upper(),                                            # shouting
        "",                                                     # empty
    ]
    for text in bad:
        assert not is_realization_text(text), f"FALSE POSITIVE: {text!r}"
        assert parse_realization_text(text) is None
    assert not is_realization_text(None)
    assert not is_realization_text(42)
    # Measured quirk, documented not attacked: leading-zero ticks DO
    # fullmatch (\d+), normalizing to the integer. The writer never emits
    # them; an authored collision only ever exempts (miss direction).
    lz = good.replace("tick 41", "tick 041").replace("tick 52", "tick 052")
    parsed = parse_realization_text(lz)
    assert parsed is not None and parsed["swing_tick"] == 41
    assert parsed["conv_tick"] == 52


def test_authored_template_doppelganger_does_not_block_mint():
    """Idempotency is scoped to machine records: an authored record whose
    text happens to match the template does not block the real mint."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-r2-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        event = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.284,
                 "swing_actual": 0.9, "gap_max": 0.616, "conv_tick": 52}
        with subject._transaction():
            authored = subject._add(
                "temporal", realization_text(event), concepts=("thirst",),
                generated_by="authored", available_to_cognition=True,
                salience=0.5, intensity=0.5)
        with subject._transaction():
            rid = mint_realization(subject, event)
        assert rid != authored.id  # the authored doppelganger is not "the"
        # record: exactly one cognition realization for the episode exists.
        mine = [r for r in subject.workspace.records
                if r.generated_by == "cognition"
                and is_realization_text(r.first_person)
                and parse_realization_text(r.first_person)["swing_tick"] == 41]
        assert len(mine) == 1 and mine[0].id == rid
    finally:
        _restore(saved)


# ---------------------------------------------------------------------------
# B. Guard narrowness.
# ---------------------------------------------------------------------------

def test_realization_pair_guard_is_narrow():
    """_realization_pair fires only when BOTH texts fullmatch the anchored
    template — never for a realization-vs-lived pair, never lived-vs-lived."""
    e1 = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.28,
          "swing_actual": 0.9, "gap_max": 0.62, "conv_tick": 52}
    e2 = {"need": "thirst", "swing_tick": 100, "swing_felt": 0.30,
          "swing_actual": 0.9, "gap_max": 0.60, "conv_tick": 131}
    real1 = _rec("r1", 0, 52, realization_text(e1))
    real2 = _rec("r2", 1, 131, realization_text(e2))
    lived = _rec("l1", 2, 52,
                 "Around tick 41 I noticed I had misread my thirst badly; "
                 "by tick 52 the feeling finally caught up with my body.",
                 gen="authored")
    lived2 = _rec("l2", 3, 90, "The rain over the garden wall.", gen="authored")
    assert _realization_pair(real1, real2) is True
    assert _realization_pair(real1, lived) is False
    assert _realization_pair(lived, real1) is False  # symmetric
    assert _realization_pair(lived, lived2) is False


def test_lived_paraphrase_of_episode_still_consolidates():
    """A lived close-paraphrase that does NOT fullmatch the template is not
    shielded: the pair flows through the normal gates and the near-dup
    pass honestly proposes it (measured: dedup fires at jaccard 92%).
    The guard is narrow — only true template pairs skip."""
    e1 = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.28,
          "swing_actual": 0.9, "gap_max": 0.62, "conv_tick": 52}
    lived_text = ("At tick 41 I felt thirst as settled, but my body was "
                  "only urgent, and by tick 52 the feeling had finally "
                  "caught up with me.")
    assert not is_realization_text(lived_text)
    recs = [_rec("r1", 0, 52, realization_text(e1)),
            _rec("l1", 1, 52, lived_text, gen="authored")]
    props = _scan_props(recs)
    touching = [p for p in props
                if {p["loser"], p["winner"]} == {"r1", "l1"}]
    assert touching, f"lived paraphrase escaped the gates: {props}"
    assert touching[0]["kind"] in ("dedup", "near-dup", "supersede"), touching


def test_exact_dup_double_mint_proposed_for_cleanup():
    """A genuine crash-window double-mint (byte-identical text) is still
    caught by the untouched exact-dup hash sweep: one dedup proposal,
    later copy loses, rationale 'one copy is enough' — no supersede."""
    e1 = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.28,
          "swing_actual": 0.9, "gap_max": 0.62, "conv_tick": 52}
    recs = [_rec("r1", 0, 52, realization_text(e1)),
            _rec("r2", 1, 60, realization_text(e1))]
    props = _scan_props(recs)
    assert len(props) == 1, props
    p = props[0]
    assert p["kind"] == "dedup", p
    assert (p["winner"], p["loser"]) == ("r1", "r2"), p
    assert "one copy is enough" in p["rationale"], p


def test_authored_template_pair_skip_is_miss_direction():
    """An authored record shaped EXACTLY like the template (different
    ticks, so not byte-identical) pairs as (_realization_pair True) and is
    skipped — a missed consolidation, the safe direction per the genome
    (a wrong archive is the hazard). Byte-identical pairs still collapse
    via the exact-dup sweep (previous test)."""
    e1 = {"need": "thirst", "swing_tick": 41, "swing_felt": 0.28,
          "swing_actual": 0.9, "gap_max": 0.62, "conv_tick": 52}
    e2 = {"need": "thirst", "swing_tick": 100, "swing_felt": 0.30,
          "swing_actual": 0.9, "gap_max": 0.60, "conv_tick": 131}
    recs = [_rec("r1", 0, 52, realization_text(e1)),
            _rec("a1", 1, 131, realization_text(e2), gen="authored")]
    assert _realization_pair(recs[0], recs[1]) is True
    assert _scan_props(recs) == []


# ---------------------------------------------------------------------------
# C. Write-ahead marks.
# ---------------------------------------------------------------------------

def test_kill_between_mint_commit_and_save_recovers_exactly_once():
    """The crash window the write-ahead design exists for: update() closed
    (mark durable in the file), the mint transaction COMMITTED, then the
    process died before the cli's tracker.save(). A fresh process must
    re-take the mark and mint idempotently — exactly one record, and the
    mark is then forgotten."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-r2-"))
    make_subject, saved, intero, db = _patched_cli(tmp)
    try:
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0
        subject = make_subject()
        tr = subject.workspace.interoception_tracker
        tr.data["params"]["noise_scale"] = 0.0
        ev = _close_one(tr, "thirst", 0.9, 0.2, 0.21, 1)
        # Write-ahead mark durable in the file BEFORE the event left update.
        file_swing = json.loads(intero.read_text(encoding="utf-8")
                                )["realization"]["swing"]
        assert file_swing["thirst"]["closed_tick"] == 3, file_swing
        # MINT COMMITS; kill before tracker.save() — the file keeps the mark.
        with subject._transaction():
            rid = mint_realization(subject, ev)
        # "Restart": fresh subject AND fresh tracker from the same files.
        subject2 = make_subject()
        tr2 = subject2.workspace.interoception_tracker
        assert "thirst" in tr2.data["realization"]["swing"]  # mark recovered
        pend = tr2.take_pending_closings()
        assert len(pend) == 1 and pend[0]["swing_tick"] == ev["swing_tick"]
        with subject2._transaction():
            rid2 = mint_realization(subject2, pend[0])
        assert rid2 == rid  # idempotent: the committed record is returned
        mine = [r for r in subject2.workspace.records
                if r.generated_by == "cognition"
                and is_realization_text(r.first_person)
                and parse_realization_text(
                    r.first_person)["swing_tick"] == ev["swing_tick"]]
        assert len(mine) == 1, [r.id for r in mine]
        # The mint committed: ack + save forget the mark durably.
        tr2.ack_closings(pend)
        tr2.save()
        assert json.loads(intero.read_text(
            encoding="utf-8"))["realization"]["swing"] == {}
        assert tr2.take_pending_closings() == []
    finally:
        _restore(saved)


def test_pending_registry_bounded_without_ack():
    """Marks never accumulate: one slot per need, overwritten on the next
    close; take() drains exactly the pending set. ack_closings can never
    be asked to forget an unbounded backlog."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-r2-"))
    tr = _tracker(tmp, params={"noise_scale": 0.0})
    tick = 0
    for need in ("thirst", "thirst", "thirst", "hunger", "hunger"):
        _close_one(tr, need, 0.9, 0.2, 0.21, tick)
        tick += 10
    assert set(tr._pending_marks) == {"thirst", "hunger"}, tr._pending_marks
    assert len(tr._pending_marks) == 2
    pend = tr.take_pending_closings()
    assert len(pend) == 2, pend
    assert {p["need"] for p in pend} == {"thirst", "hunger"}
    assert tr.take_pending_closings() == []


def test_take_without_ack_forgets_memory_file_recovers():
    """Measured contract (characterization, not aspiration): take() is
    destructive in memory — a same-process re-take after an unacked mint
    returns []. The write-ahead FILE is the recovery path: a fresh tracker
    re-takes the mark. This is why the mint transaction must never swallow
    failures (it re-raises) — recovery rides the next process load."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-r2-"))
    tr = _tracker(tmp, params={"noise_scale": 0.0})
    ev = _close_one(tr, "thirst", 0.9, 0.2, 0.21, 1)
    pend1 = tr.take_pending_closings()
    assert len(pend1) == 1 and pend1[0]["swing_tick"] == ev["swing_tick"]
    # Mint "failed": no ack. Same process, next tick: nothing to re-take.
    assert tr.take_pending_closings() == []
    # Fresh process: the file's write-ahead mark is recovered.
    tr2 = _tracker(tmp)
    pend2 = tr2.take_pending_closings()
    assert len(pend2) == 1 and pend2[0]["swing_tick"] == ev["swing_tick"]
    tr2.ack_closings(pend2)
    tr2.save()
    assert _tracker(tmp).take_pending_closings() == []


# ---------------------------------------------------------------------------
# D. The 0.250001 nudge.
# ---------------------------------------------------------------------------

def test_nudge_value_exact_and_drop_rule_boundary():
    """The writer preserves the strict gap_max > SWING_GAP invariant
    exactly: a true gap in (0.25, 0.2500005] stores 0.250001 (not 0.25)
    and survives the compat-read. Conversely a well-typed entry with
    gap_max exactly 0.25 is impossible for a real episode and is dropped."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-r2-"))
    tr = _tracker(tmp, params={"noise_scale": 0.0})
    tr.data["needs"]["thirst"] = {"felt": 0.8, "last_tick": 1, "level": 2}
    assert tr.update({"thirst": 0.5159089}, 2) == []  # gap 0.2500001: opens
    entry = tr.data["realization"]["swing"]["thirst"]
    assert entry["gap_max"] == 0.250001, entry  # the exact nudge, not ~0.25
    assert entry["gap_max"] > SWING_GAP
    tr.save()
    assert "thirst" in _tracker(tmp).data["realization"]["swing"]
    # Drop-rule boundary: gap_max == 0.25 exactly is corrupt, never trusted.
    tmp2 = Path(tempfile.mkdtemp(prefix="crit-r2b-"))
    (tmp2 / "interoception.json").write_text(json.dumps({
        "needs": {}, "params": {}, "seed": 0,
        "realization": {"swing": {"thirst": {
            "swing_tick": 3, "swing_felt": 0.5,
            "swing_actual": 0.51, "gap_max": 0.25}}}}), encoding="utf-8")
    assert _tracker(tmp2).data["realization"]["swing"] == {}


def test_writer_gap_max_invariant_soak():
    """Across 200 randomized ticks, every gap_max the writer ever stores —
    at open, on growth, and on close events — strictly exceeds SWING_GAP,
    and a save/reload round-trips the swing byte-identically."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-r2-"))
    tr = _tracker(tmp)  # default seeded noise: the realistic path
    rng = random.Random(20261004)
    actuals = {k: 0.5 for k in ("hunger", "thirst", "fatigue", "energy")}
    seen_max = []
    for tick in range(1, 201):
        for k in actuals:
            actuals[k] = round(min(1.0, max(0.0,
                actuals[k] + (rng.random() - 0.5) * 0.3)), 4)
        for e in tr.update(dict(actuals), tick):
            seen_max.append(e["gap_max"])
        for key, ep in tr.data["realization"]["swing"].items():
            assert ep["gap_max"] > SWING_GAP, (key, ep)
            seen_max.append(ep["gap_max"])
    assert seen_max, "soak produced no episodes to check"
    assert all(g > SWING_GAP for g in seen_max), min(seen_max)
    before = (tmp / "interoception.json").read_bytes() if (
        tmp / "interoception.json").exists() else None
    tr.save()
    after_save = (tmp / "interoception.json").read_bytes()
    tr2 = _tracker(tmp)
    assert tr2.data["realization"]["swing"] == tr.data["realization"]["swing"]
    tr2.save()
    assert (tmp / "interoception.json").read_bytes() == after_save


# ---------------------------------------------------------------------------
# E. Regression genome recheck for the new code.
# ---------------------------------------------------------------------------

def test_no_shadowed_definitions():
    """Genome: shadowing definitions — each new def appears exactly once."""
    intero = (Path(__file__).resolve().parents[2]
              / "calibos_mind" / "interoception.py").read_text()
    for name in ("parse_realization_text", "is_realization_text",
                 "realization_text", "realization_band", "mint_realization",
                 "_read_swing", "take_pending_closings", "ack_closings"):
        assert len(re.findall(rf"^\s*def {name}\(", intero, re.M)) == 1, name
    consol = (Path(__file__).resolve().parents[2]
              / "calibos_mind" / "consolidate.py").read_text()
    assert len(re.findall(r"^\s*def _realization_pair\(", consol, re.M)) == 1


def test_import_direction_no_cycle():
    """Genome: consolidate -> interoception, never the reverse."""
    root = Path(__file__).resolve().parents[2] / "calibos_mind"
    intero = (root / "interoception.py").read_text()
    assert "consolidate" not in intero
    assert "from .cli import" not in (root / "consolidate.py").read_text()
    assert "from .interoception import" in (root / "consolidate.py").read_text()


def test_reset_clears_pending_marks():
    """Genome: sidecar/state reset on reseed — reset() wipes open episodes
    AND the in-memory pending-mark registry (stale marks must never attach
    to recycled state)."""
    tmp = Path(tempfile.mkdtemp(prefix="crit-r2-"))
    tr = _tracker(tmp, params={"noise_scale": 0.0})
    _close_one(tr, "thirst", 0.9, 0.2, 0.21, 1)  # mark pending in memory
    assert tr._pending_marks, "expected a pending mark before reset"
    tr.reset()
    assert tr._pending_marks == {}
    assert tr.data["realization"] == {"swing": {}}
    assert json.loads((tmp / "interoception.json").read_text(
        encoding="utf-8"))["realization"] == {"swing": {}}
    assert _tracker(tmp).take_pending_closings() == []


def test_update_called_only_from_waking_tick():
    """Genome: read-only/dream isolation — the interoception update() seam
    exists only in cli._run_tick (the waking path). Dream ticks and
    read-only commands never call it (behaviorally pinned by the
    builder's test_readonly_and_dream_never_mint, green)."""
    cli_src = (Path(__file__).resolve().parents[2]
               / "calibos_mind" / "cli.py").read_text()
    assert cli_src.count("tracker.update(") == 1
    m = re.search(r"def _run_tick\(subject\):.*?tracker\.update\(",
                  cli_src, re.S)
    assert m is not None


def _main():
    red, green = [], []
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        try:
            fn()
        except AssertionError as exc:
            red.append((fn.__name__, str(exc)[:300]))
            print(f"RED  {fn.__name__}: {str(exc)[:300]}")
        else:
            green.append(fn.__name__)
            print(f"PASS {fn.__name__}")
    print(f"{len(green)} green, {len(red)} red of {len(fns)}")
    if red:
        raise SystemExit(1)


if __name__ == "__main__":
    _main()
