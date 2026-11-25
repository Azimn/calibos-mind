"""Tests for the atomic state save (crash-window fix).

unittest, stdlib only. All state is synthetic and lives in /tmp — never the
live mind store.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from home_sim import rhythm
from home_sim.tick import Home


def fresh(**overrides):
    s = rhythm.initial_state("2026-10-19")
    s.update(overrides)
    rhythm._validate(s)
    return s


class TestAtomicSave(unittest.TestCase):
    """A failure anywhere in save() leaves the previous state.json
    intact and parseable. state.json is the authority; the log is the
    audit trail."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name)
        (self.state_dir / "state.json").write_text(
            json.dumps(fresh(), indent=2), encoding="utf-8")
        self.home = Home(self.state_dir)
        # Whatever load did, this is the state on disk before the test.
        self.old = self._saved_state()

    def tearDown(self):
        self.tmp.cleanup()

    def _saved_state(self):
        return json.loads(
            (self.state_dir / "state.json").read_text(encoding="utf-8"))

    def _tmp_path(self):
        return self.state_dir / "state.json.tmp"

    def test_save_replaces_state_and_leaves_no_temp(self):
        self.home.state["tick"] = 7
        self.home.save()
        self.assertEqual(self._saved_state()["tick"], 7)
        self.assertFalse(self._tmp_path().exists())

    def test_failure_during_dump_leaves_old_state(self):
        # json.dump raises mid-write: the temp file may be partial, but
        # state.json must still parse to the old state.
        with mock.patch("home_sim.tick.json.dump",
                        side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.home.save()
        self.assertEqual(self._saved_state(), self.old)

    def test_failure_during_fsync_leaves_old_state(self):
        # The temp file is fully written but never synced: nothing has
        # been renamed into place yet.
        with mock.patch("home_sim.tick.os.fsync",
                        side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.home.save()
        self.assertEqual(self._saved_state(), self.old)

    def test_failure_during_replace_leaves_old_state(self):
        # The temp file is fully written and synced; the rename fails.
        # The stray temp file is the evidence the write got that far.
        with mock.patch("home_sim.tick.os.replace",
                        side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.home.save()
        self.assertEqual(self._saved_state(), self.old)
        self.assertTrue(self._tmp_path().exists())

    def test_fsync_is_called_on_save(self):
        with mock.patch("home_sim.tick.os.fsync") as fsync:
            self.home.save()
        self.assertTrue(fsync.called)

    def test_failed_save_then_good_save_recovers(self):
        # A failed save must not poison the next one: the stale temp
        # file is simply overwritten.
        with mock.patch("home_sim.tick.os.replace",
                        side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                self.home.save()
        self.home.state["tick"] = 3
        self.home.save()
        self.assertEqual(self._saved_state()["tick"], 3)
        self.assertFalse(self._tmp_path().exists())


if __name__ == "__main__":
    unittest.main()
