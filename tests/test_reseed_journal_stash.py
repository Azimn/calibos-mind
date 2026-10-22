"""Regression: reseed must stash the consolidation journal, never annihilate it.

2026-10-21: the proposals/ journal vanished for the fourth time. Mechanism:
`cli.cmd_init --force` (the reseed path) unlinked every file in proposals/ by
design (stale proposal ids must never attach to recycled experience ids —
2026-10-18). The invariant only requires the journal to be *unloaded*, not
*destroyed*: cmd_init now copies the journal to BASE/journal-stash/reseed-<ts>
before clearing proposals/. These tests pin that behavior.
"""

import argparse
import json
import tempfile
from pathlib import Path

from digital_subject.cartridge import load_cartridge

from calibos_mind import cli
from calibos_mind.provider import InboxCognition
from calibos_mind.subject import CalibosSubject


def _tmp():
    d = tempfile.mkdtemp(prefix="reseed-stash-")
    return Path(d)


def _patched_cli(tmp: Path):
    """Redirect every module-level path cmd_init touches at tmp, including BASE.

    BASE is redirected (unlike the older helpers) because the journal stash
    is written under BASE/journal-stash — leaving BASE live would create
    dated stash directories in the live checkout during routine test runs.
    """
    db = tmp / "mind.db"
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    salience = tmp / "salience.json"
    interoception = tmp / "interoception.json"
    familiarity = tmp / "familiarity.json"
    ambivalence = tmp / "ambivalence.json"
    habits = tmp / "habits-formed.json"
    provenance = tmp / "provenance.json"
    proposals = tmp / "proposals"
    archive = tmp / "archive"
    names = ("BASE", "DB", "INBOX", "SALIENCE", "INTEROCEPTION", "FAMILIARITY",
             "AMBIVALENCE", "HABITS", "PROVENANCE", "PROPOSALS", "ARCHIVE",
             "_subject")
    saved = tuple(getattr(cli, n) for n in names)
    setattr(cli, "BASE", tmp)
    cli.DB = db
    cli.INBOX = inbox
    cli.SALIENCE = salience
    cli.INTEROCEPTION = interoception
    cli.FAMILIARITY = familiarity
    cli.AMBIVALENCE = ambivalence
    cli.HABITS = habits
    cli.PROVENANCE = provenance
    cli.PROPOSALS = proposals
    cli.ARCHIVE = archive
    cartridge = load_cartridge(cli.CARTRIDGE_PATH)

    def make_subject(provider=None):
        if provider is None:
            provider = InboxCognition(inbox)
        return CalibosSubject(str(db), cartridge, cognition=provider,
                              salience_path=str(salience),
                              interoception_path=str(interoception))

    cli._subject = make_subject

    def restore():
        for n, v in zip(names, saved):
            setattr(cli, n, v)

    return restore


def _seed_journal(proposals: Path):
    proposals.mkdir(parents=True, exist_ok=True)
    journal = {"proposals": {str(i): {"id": i, "status": "pending"} for i in range(3)},
               "seq": 3}
    (proposals / "proposals.json").write_text(json.dumps(journal))
    (proposals / "journal.seq").write_text(json.dumps({"seq": 3}))


def test_reseed_stashes_journal_instead_of_annihilating():
    tmp = _tmp()
    restore = _patched_cli(tmp)
    try:
        proposals = tmp / "proposals"
        _seed_journal(proposals)
        before = {(proposals / "proposals.json").read_bytes(),
                  (proposals / "journal.seq").read_bytes()}

        assert cli.cmd_init(argparse.Namespace(force=True)) == 0

        # The live journal is cleared (stale ids must not attach).
        assert sorted(p.name for p in proposals.iterdir()) == []
        # ... but a dated stash outside the wiped set holds identical bytes.
        stash_root = tmp / "journal-stash"
        stashes = [d for d in stash_root.iterdir() if d.is_dir()]
        assert len(stashes) == 1, f"expected one stash dir, got {stashes}"
        stash_files = {p.name: p.read_bytes() for p in stashes[0].iterdir()}
        assert set(stash_files) == {"proposals.json", "journal.seq"}
        assert set(stash_files.values()) == before
    finally:
        restore()


def test_stash_is_never_loaded_as_live_journal():
    from calibos_mind import consolidate as C
    tmp = _tmp()
    restore = _patched_cli(tmp)
    try:
        proposals = tmp / "proposals"
        _seed_journal(proposals)
        assert cli.cmd_init(argparse.Namespace(force=True)) == 0

        journal = C.load_journal(proposals, restore=False)
        pending = C.pending_proposals(journal)
        assert pending == [], f"reseeded journal must be empty, got {pending}"
    finally:
        restore()
