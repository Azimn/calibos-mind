"""Tests for `mind intend` — self-authored commitment registration (Domain 19).

Spec: research/specs/2026-10-14-self-commitment-intend.md
Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_intend.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.

Genome notes worked in here:
- `cli._subject()` construction rewrites the SQLite payload row on every
  invocation (2026-09-26: CLI construction is never mtime-silent), so all
  no-write assertions below are scoped to the operation under test (the
  continuity ledger), never to byte-identical DB content.
- `mind intend` is a deliberate WRITE command; the read-only sweep asserts
  that the read-only commands mint nothing, not that this one doesn't.
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject
from calibos_mind.unresolved import live_open_keys
from digital_subject.cartridge import load_cartridge


def _patched_cli(tmp: Path):
    """Redirect every module-level path a CLI command could touch to tmp.

    Built from tests/test_init.py's _patched_cli, extended with DREAMS
    (cmd_dream writes timestamped logs there) and COUNTERFACTUAL (cmd_init
    unlinks it; test_init's fixture does not redirect it).
    """
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    dreams = tmp / "dreams"
    dreams.mkdir(exist_ok=True)
    saved = (cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE, cli.INTEROCEPTION,
             cli.FAMILIARITY, cli.AMBIVALENCE, cli.COUNTERFACTUAL, cli.HABITS,
             cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE, cli._subject)
    (cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE, cli.INTEROCEPTION,
     cli.FAMILIARITY, cli.AMBIVALENCE, cli.COUNTERFACTUAL, cli.HABITS,
     cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE) = (
        db, inbox, dreams, tmp / "salience.json", tmp / "interoception.json",
        tmp / "familiarity.json", tmp / "ambivalence.json",
        tmp / "counterfactuals.json", tmp / "habits-formed.json",
        tmp / "provenance.json", tmp / "proposals", tmp / "archive")
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(tmp / "salience.json"),
                              interoception_path=str(tmp / "interoception.json"),
                              familiarity_path=str(tmp / "familiarity.json"),
                              ambivalence_path=str(tmp / "ambivalence.json"),
                              counterfactual_path=str(tmp / "counterfactuals.json"),
                              habits_path=str(tmp / "habits-formed.json"))

    cli._subject = make_subject
    return saved


def _restore(saved):
    (cli.DB, cli.INBOX, cli.DREAMS, cli.SALIENCE, cli.INTEROCEPTION,
     cli.FAMILIARITY, cli.AMBIVALENCE, cli.COUNTERFACTUAL, cli.HABITS,
     cli.PROVENANCE, cli.PROPOSALS, cli.ARCHIVE, cli._subject) = saved


def _fresh(tmp):
    """An initialized synthetic store; returns nothing (CLI-patched)."""
    assert cli.main(["init"]) == 0


def _commitments():
    return cli._subject().continuity.state.commitments


def _run(argv):
    """Run a CLI command, capturing stdout; returns (rc, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


# --- Fitness 1: exact ledger fields, printed id resolves via `mind resolve` ---

def test_intend_exact_ledger_fields():
    tmp = Path(tempfile.mkdtemp(prefix="intend-fields-"))
    saved = _patched_cli(tmp)
    try:
        _fresh(tmp)
        before = cli._subject().engine.state.tick
        rc, out, _ = _run(["intend", "revisit the duck-theory notes",
                           "--due", "48", "--importance", "0.6"])
        assert rc == 0, out
        comms = _commitments()
        assert len(comms) == 1, "one invocation must mint exactly one commitment"
        c = next(iter(comms.values()))
        assert c.status == "open"
        assert c.actor == "self"
        assert c.beneficiary == "self"
        assert c.created_tick == before
        assert c.due_tick == before + 48
        assert c.importance == 0.6
        # The printed id (unique-prefix form) resolves through `mind resolve`.
        prefix = out.split("intended ", 1)[1].split(":")[0]
        rc, out2, _ = _run(["resolve", prefix])
        assert rc == 0, out2
        assert _commitments()[c.id].status == "kept"
    finally:
        _restore(saved)


# --- Fitness 2: defaults ---

def test_intend_defaults():
    tmp = Path(tempfile.mkdtemp(prefix="intend-defaults-"))
    saved = _patched_cli(tmp)
    try:
        _fresh(tmp)
        rc, _, _ = _run(["intend", "a plan with no deadline"])
        assert rc == 0
        c = next(iter(_commitments().values()))
        assert c.due_tick is None
        assert c.importance == 0.5
    finally:
        _restore(saved)


def test_intend_importance_clamped_by_engine():
    """--importance passes through to the engine's own _clamp: one clamp
    rule, not two. The wrapper does not shadow-clamp (genome: shadowing)."""
    tmp = Path(tempfile.mkdtemp(prefix="intend-clamp-"))
    saved = _patched_cli(tmp)
    try:
        _fresh(tmp)
        assert _run(["intend", "too important", "--importance", "1.5"])[0] == 0
        c = next(iter(_commitments().values()))
        assert c.importance == 1.0
    finally:
        _restore(saved)


# --- Fitness 3: empty description refuses loudly ---

def test_intend_empty_description_refuses_loudly():
    tmp = Path(tempfile.mkdtemp(prefix="intend-empty-"))
    saved = _patched_cli(tmp)
    try:
        _fresh(tmp)
        for bad in ("", "   ", "\t\n "):
            rc, _, err = _run(["intend", bad])
            assert rc != 0, f"empty description {bad!r} must fail"
            assert err.strip(), "refusal must say something on stderr"
        assert _commitments() == {}, "ledger must be untouched by refusals"
    finally:
        _restore(saved)


def test_intend_nonfinite_importance_refuses_loudly():
    """NaN/inf is not a clampable value — loud refusal, not a quiet zero."""
    tmp = Path(tempfile.mkdtemp(prefix="intend-nan-"))
    saved = _patched_cli(tmp)
    try:
        _fresh(tmp)
        rc, _, err = _run(["intend", "impossible weight", "--importance", "nan"])
        assert rc != 0
        assert err.strip()
        assert _commitments() == {}
    finally:
        _restore(saved)


# --- Fitness 4: kept / released closure through existing resolve paths ---

def test_intend_resolve_released_keeps_first_class_semantics():
    """`mind resolve --released` on a self-commitment stores "released"
    (never "broken"), with the same insight weight the engine's resolve
    uses: 0.45 + importance*0.45."""
    tmp = Path(tempfile.mkdtemp(prefix="intend-release-"))
    saved = _patched_cli(tmp)
    try:
        _fresh(tmp)
        rc, out, _ = _run(["intend", "a plan I will drop",
                           "--importance", "0.6"])
        assert rc == 0
        prefix = out.split("intended ", 1)[1].split(":")[0]
        rc, _, _ = _run(["resolve", prefix, "--released", "--note", "no longer mine"])
        assert rc == 0
        comms = _commitments()
        c = next(iter(comms.values()))
        assert c.status == "released"
        assert c.status != "broken"
        insights = [i for i in cli._subject().continuity.state.insights
                    if i.kind == "commitment_result"]
        assert insights, "release must record a commitment_result insight"
        assert abs(insights[-1].confidence - (0.45 + 0.6 * 0.45)) < 1e-9
    finally:
        _restore(saved)


# --- Fitness 5: endogenous parity with world-actor commitments ---

def test_endogenous_parity_open_records_and_temporal():
    """A self-commitment minted via `mind intend` is a first-class citizen
    of the endogenous machinery: _open_records yields it with honest
    urgency, and a due one crossing a grade boundary mints a temporal
    record with exactly the same grade and link shape as a world-actor
    commitment at the same due/importance (the control)."""
    tmp = Path(tempfile.mkdtemp(prefix="intend-parity-"))
    saved = _patched_cli(tmp)
    try:
        _fresh(tmp)
        rc, out, _ = _run(["intend", "the mind's own plan", "--importance", "0.6"])
        assert rc == 0
        sub = cli._subject()
        comms = sub.continuity.state.commitments
        assert len(comms) == 1
        c = next(iter(comms.values()))
        keys = [k for k, *_ in sub._open_records()]
        assert "commitment:" + c.id in keys
        u = sub._urgency(c.due_tick, c.importance, c.actor)
        assert 0 < u <= 1, f"urgency out of honest range: {u}"
        # relationships.get("self") is None: urgency must degrade to the
        # no-attachment form, not invent a relationship.
        assert sub.engine.state.relationships.get("self") is None

        # Grade-boundary crossing: --due -6 puts the commitment 6 ticks
        # past due (grade 2: "I have been waiting..."). The world-actor
        # control is minted at the engine level with identical due/importance.
        rc, _, _ = _run(["intend", "an overdue self-plan", "--due", "-6",
                         "--importance", "0.6"])
        assert rc == 0
        # The intends above ran in their own subject instances; re-open the
        # store so every assertion below reads one consistent, fresh state.
        sub = cli._subject()
        comms = sub.continuity.state.commitments
        tick = sub.engine.state.tick
        with sub._transaction():
            control = sub.continuity.create_commitment(
                "world", "an overdue world plan",
                tick=tick, due_tick=tick - 6, importance=0.6)
            sub._project_temporal()

        def temporal_for(cid):
            return [r for r in sub.workspace.records
                    if r.source == "temporal" and r.generated_by == "commitment:" + cid]

        self_c = next(x for x in comms.values()
                      if x.description == "an overdue self-plan")
        t_self = temporal_for(self_c.id)
        t_world = temporal_for(control.id)
        assert t_self and t_world, "both must mint a temporal record"
        grade2 = "I have been waiting and still have no confirmation: "
        assert t_self[0].first_person.startswith(grade2), t_self[0].first_person
        assert t_world[0].first_person.startswith(grade2), t_world[0].first_person
        assert t_self[0].concern_links == ("commitment:" + self_c.id,)
        assert t_world[0].concern_links == ("commitment:" + control.id,)
    finally:
        _restore(saved)


# --- Fitness 6: unresolved.live_open_keys parity ---

def test_live_open_keys_includes_self_commitment():
    tmp = Path(tempfile.mkdtemp(prefix="intend-keys-"))
    saved = _patched_cli(tmp)
    try:
        _fresh(tmp)
        rc, out, _ = _run(["intend", "keeps the Zeigarnik boost honest"])
        assert rc == 0
        sub = cli._subject()
        c = next(iter(sub.continuity.state.commitments.values()))
        keys = live_open_keys(sub.engine.state, sub.continuity.state)
        assert c.id in keys
        assert "commitment:" + c.id in keys
        # Closing removes it: the boost follows liveness, not history.
        # (Re-open the subject: resolve ran in its own instance.)
        prefix = out.split("intended ", 1)[1].split(":")[0]
        assert _run(["resolve", prefix])[0] == 0
        sub = cli._subject()
        keys = live_open_keys(sub.engine.state, sub.continuity.state)
        assert c.id not in keys and "commitment:" + c.id not in keys
    finally:
        _restore(saved)


# --- Fitness 7: read-only commands and dream ticks mint nothing ---

def test_readonly_commands_and_dream_mint_no_commitments():
    tmp = Path(tempfile.mkdtemp(prefix="intend-readonly-"))
    saved = _patched_cli(tmp)
    try:
        _fresh(tmp)
        assert _commitments() == {}
        for argv in (["drift"], ["status"], ["consolidate", "--list"],
                     ["recall"], ["review"], ["inbox"]):
            rc, out, err = _run(argv)
            assert rc == 0, (argv, out, err)
            assert _commitments() == {}, f"{argv} minted a commitment"
        # Dream ticks: body/tick/conduct frozen — and the continuity
        # ledger untouched.
        rc, out, err = _run(["dream", "--ticks", "3"])
        assert rc == 0, (out, err)
        assert _commitments() == {}, "dream ticks minted a commitment"
    finally:
        _restore(saved)


# --- Fitness 8: init --force clears the commitment ledger ---

def test_init_force_clears_commitment_ledger():
    """Reseed must not carry dead bindings: the ledger lives in the DB
    payload and `init --force` deletes it wholesale. Verify the current
    behavior holds — no commitment survives."""
    tmp = Path(tempfile.mkdtemp(prefix="intend-reseed-"))
    saved = _patched_cli(tmp)
    try:
        assert _run(["init"])[0] == 0
        assert _run(["intend", "a binding from a dead incarnation"])[0] == 0
        assert len(_commitments()) == 1
        assert _run(["init", "--force"])[0] == 0
        assert _commitments() == {}, "init --force left a stale commitment behind"
    finally:
        _restore(saved)


# --- Genome: no shadowing definitions ---

def test_no_shadowing_cmd_intend():
    """Exactly one cmd_intend definition; exactly one argparse registration."""
    import inspect as _inspect
    src = _inspect.getsource(cli)
    assert src.count("def cmd_intend(") == 1
    assert src.count('add_parser("intend"') == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - test harness reports plainly
            failed += 1
            print(f"FAIL {fn.__name__}: {exc!r}")
        else:
            print(f"ok   {fn.__name__}")
    print(f"{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
