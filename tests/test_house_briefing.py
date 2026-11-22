"""Tests for the house invitation in the wake briefing.

The platform does not act on its own, so the wake has to offer the
house the way it offers any open loop — otherwise a visit only happens
when somebody else remembers it. `mind wake` prints one standing
invitation built from live house state (fish, diary, weather, last
night). The invitation is deliberately outside the carrying-loops list,
so the ablation's loops_shown and briefed_loops are untouched.

Run:  cd ~/workspace/calibos-mind && ./.venv/bin/python tests/test_house_briefing.py
Plain asserts, no test runner needed (also pytest-compatible).
All fixtures live in /tmp — the live store is never touched.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calibos_mind.cli as cli


def _plant_house(tmp, state):
    directory = Path(tmp) / "home_sim" / "state"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "state.json").write_text(json.dumps(state) + "\n",
                                           encoding="utf-8")
    return directory


def _state(**overrides):
    state = {
        "night_date": "2026-11-21",
        "fish": {"alive": True, "name": "Pip", "hunger": 0},
        "diary": [{"date": "2026-11-21", "clock": "23:32",
                   "text": "First entry."}],
        "weather": {"condition": "clear", "temp_f": 70.5,
                    "source": "live"},
    }
    state.update(overrides)
    return state


def test_content_fish_diary_weather():
    with tempfile.TemporaryDirectory(prefix="house-brief-") as tmp:
        directory = _plant_house(tmp, _state())
        lines = cli._house_briefing_lines(state_dir=directory)
        assert len(lines) == 1, lines
        line = lines[0]
        assert line.startswith("  house: "), line
        assert "Pip is content" in line, line
        assert "diary: 1 entry, latest 2026-11-21" in line, line
        assert "clear, 70.5°F" in line, line
        assert "last night 2026-11-21" in line, line
        assert "A visit is yours to choose" in line, line


def test_hungry_fish_one_day():
    with tempfile.TemporaryDirectory(prefix="house-brief-") as tmp:
        directory = _plant_house(tmp, _state(
            fish={"alive": True, "name": "Pip", "hunger": 1500}))
        lines = cli._house_briefing_lines(state_dir=directory)
        assert "Pip is hungry (1 day unfed)" in lines[0], lines


def test_fish_in_danger_two_days():
    with tempfile.TemporaryDirectory(prefix="house-brief-") as tmp:
        directory = _plant_house(tmp, _state(
            fish={"alive": True, "name": "Pip", "hunger": 3000}))
        lines = cli._house_briefing_lines(state_dir=directory)
        assert "Pip is in danger (2 days unfed, feed him)" in lines[0], lines


def test_dead_fish_named_plainly():
    with tempfile.TemporaryDirectory(prefix="house-brief-") as tmp:
        directory = _plant_house(tmp, _state(
            fish={"alive": False, "name": "Pip", "hunger": 4320}))
        lines = cli._house_briefing_lines(state_dir=directory)
        assert "Pip has died (unfed too long)" in lines[0], lines


def test_unnamed_fish_empty_diary_no_weather():
    with tempfile.TemporaryDirectory(prefix="house-brief-") as tmp:
        directory = _plant_house(tmp, _state(
            fish={"alive": True, "name": "", "hunger": 0},
            diary=[], weather={}))
        lines = cli._house_briefing_lines(state_dir=directory)
        assert "the fish is content" in lines[0], lines
        assert "diary: empty" in lines[0], lines


def test_missing_state_is_silent():
    with tempfile.TemporaryDirectory(prefix="house-brief-") as tmp:
        lines = cli._house_briefing_lines(state_dir=Path(tmp) / "nope")
        assert lines == [], lines


def test_malformed_state_is_silent():
    with tempfile.TemporaryDirectory(prefix="house-brief-") as tmp:
        directory = Path(tmp) / "home_sim" / "state"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "state.json").write_text("{not json",
                                               encoding="utf-8")
        assert cli._house_briefing_lines(state_dir=directory) == []


def test_empty_state_dict_still_renders():
    with tempfile.TemporaryDirectory(prefix="house-brief-") as tmp:
        directory = _plant_house(tmp, {})
        lines = cli._house_briefing_lines(state_dir=directory)
        assert len(lines) == 1, lines
        assert "the fish is content" in lines[0], lines
        assert "diary: empty" in lines[0], lines


class CliOnTmp:
    """Redirect the CLI's store paths at a synthetic tree, BASE included."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="house-wake-"))
        self.saved = {}

    def __enter__(self):
        targets = {"BASE": self.tmp,
                   "DB": self.tmp / "t.db",
                   "INBOX": self.tmp / "inbox",
                   "DREAMS": self.tmp / "dreams",
                   "SALIENCE": self.tmp / "salience.json",
                   "INTEROCEPTION": self.tmp / "interoception.json",
                   "FAMILIARITY": self.tmp / "familiarity.json",
                   "AMBIVALENCE": self.tmp / "ambivalence.json",
                   "HABITS": self.tmp / "habits-formed.json",
                   "PROVENANCE": self.tmp / "provenance.json",
                   "WAKE_LIVENESS": self.tmp / "wake_liveness.json",
                   "ABLATION_SALT_FILE": self.tmp / "ablation_salt",
                   "ABLATION_LOG": self.tmp / "ablation_log.jsonl"}
        for name, path in targets.items():
            self.saved[name] = getattr(cli, name)
            setattr(cli, name, path)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(cli, name, value)
        return False


def _run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


def test_wake_briefing_shows_house_without_touching_ablation():
    with CliOnTmp() as ctx:
        _plant_house(ctx.tmp, _state(
            fish={"alive": True, "name": "Pip", "hunger": 1500}))
        cli._subject()  # create the store
        rc, out = _run("wake")
        assert rc == 0, rc
        assert "  house: the house is there" in out, out
        assert "Pip is hungry (1 day unfed)" in out, out
        rows = [json.loads(line) for line in
                cli.ABLATION_LOG.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        briefed = [r for r in rows if r.get("event") == "briefed"]
        assert len(briefed) == 1, rows
        assert not any("house" in str(b) for b in
                       briefed[0].get("briefed_loops", [])), briefed
        assert briefed[0].get("loops_shown") == 0, briefed


if __name__ == "__main__":
    test_content_fish_diary_weather()
    test_hungry_fish_one_day()
    test_fish_in_danger_two_days()
    test_dead_fish_named_plainly()
    test_unnamed_fish_empty_diary_no_weather()
    test_missing_state_is_silent()
    test_malformed_state_is_silent()
    test_empty_state_dict_still_renders()
    test_wake_briefing_shows_house_without_touching_ablation()
    print("test_house_briefing: all passed")
