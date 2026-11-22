"""Tests for the override keys (rules version 6).

Stdlib unittest only. All state is synthetic and lives in /tmp — never
the live home. No test here touches the network. Covers the condition
keys (after, night, nights, weather_is, temp_below, temp_above,
chance) and the effect keys (temperature, ambience, objects), plus
malformed conditions failing safe, all present conditions ANDing
together, and the existing gift still working.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from home_sim import rhythm, weather
from home_sim.overrides import (chance_roll, conditions_pass,
                                load_override_files, load_overrides,
                                register_override_books)
from home_sim.rhythm import HomeError
from home_sim.tick import Home
from home_sim.visit import run_visit

PACKAGE_DIR = Path(__file__).resolve().parent.parent

GIFT_TEXT = "A small brass key rests on the study desk."


def fresh(night="2026-11-21", condition="clear", temp_f=70.0, **overrides):
    s = rhythm.initial_state(night)
    wx = weather.default_weather()
    wx["condition"] = condition
    wx["temp_f"] = temp_f
    s["weather"] = wx
    s.update(overrides)
    rhythm._validate(s)
    return s


def _event_file(**extra):
    data = {"events": [{"kind": "gift", "text": GIFT_TEXT,
                        "room": "study"}]}
    data.update(extra)
    return data


def _desc_file(text, **extra):
    data = {"descriptions": {"study": text}}
    data.update(extra)
    return data


class _HomeCase(unittest.TestCase):
    """A Home on a disposable state dir, fed override files from a
    disposable folder. Home() itself reads the package overrides dir,
    so each test replaces home.overrides with the loaded test files
    and registers their books, exactly as Home.__init__ does."""

    def make_home(self, files, night="2026-11-21", condition="clear",
                  temp_f=70.0):
        self._tmp = tempfile.TemporaryDirectory()
        folder = Path(self._tmp.name) / "overrides"
        folder.mkdir()
        for name, data in files.items():
            (folder / name).write_text(json.dumps(data), encoding="utf-8")
        home = Home(state_dir=str(Path(self._tmp.name) / "state"), seed=1)
        home.state = fresh(night, condition, temp_f)
        home.save()
        home.overrides = load_overrides(folder)
        register_override_books(home.overrides)
        for data in home.override_files.values():
            register_override_books({"books": data.get("books", [])})
        home.active_overrides = {k: v for k, v in home.overrides.items()
                                 if k != "files"}
        return home

    def tearDown(self):
        tmp = getattr(self, "_tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def advance_to(self, home, night):
        home.state["night_date"] = night
        home.save()

    def event_fired(self, home, filename):
        return filename in home.state["applied_overrides"]


class TestLoaderConditions(_HomeCase):
    def test_conditional_file_stays_out_of_merged_keys(self):
        files = {"dated.json": _event_file(after="2026-11-22"),
                 "plain.json": _event_file()}
        home = self.make_home(files)
        self.assertIn("files", home.overrides)
        self.assertIn("dated.json", home.overrides["files"])
        self.assertNotIn("dated.json",
                         [e.get("file", "")
                          for e in home.overrides["events"]])
        self.assertEqual(len(home.overrides["events"]), 1)
        self.assertEqual(home.overrides["events"][0]["file"], "plain.json")
        # New top-level keys appear only when content exists.
        self.assertNotIn("temperature", home.overrides)
        self.assertNotIn("ambience", home.overrides)
        self.assertNotIn("objects", home.overrides)

    def test_malformed_condition_skips_whole_file(self):
        bad = {
            "bad-after.json": _event_file(after="not-a-date"),
            "bad-night.json": _event_file(night="2026-13-01"),
            "bad-nights-zero.json": _event_file(nights=0),
            "bad-nights-bool.json": _event_file(nights=True),
            "bad-nights-str.json": _event_file(nights="2"),
            "bad-weather.json": _event_file(weather_is="sunny"),
            "bad-temp.json": _event_file(temp_below="cold"),
            "bad-chance-high.json": _event_file(chance=1.5),
            "bad-chance-low.json": _event_file(chance=-0.1),
            "bad-chance-str.json": _event_file(chance="often"),
            "bad-chance-bool.json": _event_file(chance=True),
        }
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name, data in bad.items():
                (folder / name).write_text(json.dumps(data),
                                           encoding="utf-8")
            per_file = load_override_files(folder)
            self.assertEqual(per_file, {})
            merged = load_overrides(folder)
            self.assertEqual(merged, {"descriptions": {}, "events": []})

    def test_unknown_condition_key_ignored(self):
        # Forward-compatible: a key the house does not know is not a
        # reason to skip the file.
        home = self.make_home(
            {"mystery.json": _event_file(whenever="soon")})
        home.apply_overrides()
        self.assertTrue(self.event_fired(home, "mystery.json"))


class TestDateConditions(_HomeCase):
    def test_after_before_on_after(self):
        home = self.make_home(
            {"dated.json": _event_file(after="2026-11-22")})
        home.apply_overrides()
        self.assertFalse(self.event_fired(home, "dated.json"))
        self.assertNotIn("dated.json",
                         home.state["override_first_night"])
        self.advance_to(home, "2026-11-22")
        home.apply_overrides()
        self.assertTrue(self.event_fired(home, "dated.json"))
        self.advance_to(home, "2026-11-23")
        home.apply_overrides()
        self.assertTrue(self.event_fired(home, "dated.json"))

    def test_night_only_that_night(self):
        home = self.make_home(
            {"one-night.json": _event_file(night="2026-11-22")})
        home.apply_overrides()
        self.assertFalse(self.event_fired(home, "one-night.json"))
        self.advance_to(home, "2026-11-22")
        home.apply_overrides()
        self.assertTrue(self.event_fired(home, "one-night.json"))
        self.advance_to(home, "2026-11-23")
        home.apply_overrides()
        # Fired once, on its night; it does not fire again.
        self.assertEqual(
            len([n for n in home.state["world_notices"]
                 if n["text"] == GIFT_TEXT]), 1)

    def test_nights_duration_expiry_never_refires(self):
        text = "A cold draft slips under the study door."
        home = self.make_home(
            {"draft.json": _desc_file(text, after="2026-11-21",
                                      nights=2)})
        home.apply_overrides()
        self.assertEqual(
            home.active_overrides["descriptions"]["study"], text)
        self.assertEqual(
            home.state["override_first_night"]["draft.json"],
            "2026-11-21")
        self.advance_to(home, "2026-11-22")
        home.apply_overrides()
        self.assertEqual(
            home.active_overrides["descriptions"]["study"], text)
        self.advance_to(home, "2026-11-23")
        home.apply_overrides()
        self.assertNotIn("study", home.active_overrides["descriptions"])
        self.assertIn("draft.json", home.state["applied_overrides"])
        self.advance_to(home, "2026-11-24")
        home.apply_overrides()
        self.assertNotIn("study", home.active_overrides["descriptions"])
        self.assertEqual(
            home.state["applied_overrides"].count("draft.json"), 1)

    def test_event_fires_once_even_with_nights(self):
        home = self.make_home(
            {"twice.json": _event_file(after="2026-11-21", nights=3)})
        home.apply_overrides()
        self.assertTrue(self.event_fired(home, "twice.json"))
        self.advance_to(home, "2026-11-22")
        home.apply_overrides()
        self.assertEqual(
            len([n for n in home.state["world_notices"]
                 if n["text"] == GIFT_TEXT]), 1)

    def test_books_shelve_permanently_despite_nights(self):
        book = {"id": "frankenstein-1818",
                "title": "Frankenstein; or, The Modern Prometheus",
                "file": "frankenstein-1818.txt"}
        home = self.make_home(
            {"book-night.json": {"night": "2026-11-22", "nights": 1,
                                 "books": [book]}})
        home.apply_overrides()
        self.assertNotIn("frankenstein-1818", home.state["books"])
        self.advance_to(home, "2026-11-22")
        home.apply_overrides()
        self.assertIn("frankenstein-1818", home.state["books"])
        self.advance_to(home, "2026-11-23")
        home.apply_overrides()
        # Shelved for good; the file's nights do not unshelve it.
        self.assertIn("frankenstein-1818", home.state["books"])


class TestWeatherAndTempConditions(_HomeCase):
    def test_weather_is_match_and_mismatch(self):
        home = self.make_home(
            {"rainy.json": _event_file(weather_is="rain")},
            condition="rain", temp_f=55.0)
        home.apply_overrides()
        self.assertTrue(self.event_fired(home, "rainy.json"))
        home2 = self.make_home(
            {"rainy.json": _event_file(weather_is="rain")},
            condition="clear", temp_f=70.0)
        home2.apply_overrides()
        self.assertFalse(self.event_fired(home2, "rainy.json"))

    def test_weather_is_not_self_triggered(self):
        # The file's own weather key must not trigger its own
        # weather_is condition: checked against the sky without it.
        home = self.make_home(
            {"self-rain.json": {"weather_is": "rain",
                                "weather": {"condition": "rain",
                                            "temp_f": 60, "nights": 1},
                                "events": [{"kind": "gift",
                                            "text": GIFT_TEXT,
                                            "room": "study"}]}},
            condition="clear", temp_f=70.0)
        home.resolve_weather(for_night=False)
        home.apply_overrides(for_night=False)
        self.assertFalse(self.event_fired(home, "self-rain.json"))
        self.assertEqual(home.state["weather"]["condition"], "clear")
        # And the other way: the real sky is rain, the file applies,
        # and its weather key then replaces the sky.
        home2 = self.make_home(
            {"self-rain.json": {"weather_is": "rain",
                                "weather": {"condition": "clear",
                                            "temp_f": 60, "nights": 1},
                                "events": [{"kind": "gift",
                                            "text": GIFT_TEXT,
                                            "room": "study"}]}},
            condition="rain", temp_f=55.0)
        home2.resolve_weather(for_night=False)
        home2.apply_overrides(for_night=False)
        self.assertTrue(self.event_fired(home2, "self-rain.json"))
        self.assertEqual(home2.state["weather"]["condition"], "clear")

    def test_temp_below_and_above(self):
        below = self.make_home(
            {"cold.json": _event_file(temp_below=75)}, temp_f=70.0)
        below.apply_overrides()
        self.assertTrue(self.event_fired(below, "cold.json"))
        at = self.make_home(
            {"cold.json": _event_file(temp_below=70)}, temp_f=70.0)
        at.apply_overrides()
        self.assertFalse(self.event_fired(at, "cold.json"))
        above = self.make_home(
            {"warm.json": _event_file(temp_above=60)}, temp_f=70.0)
        above.apply_overrides()
        self.assertTrue(self.event_fired(above, "warm.json"))
        at2 = self.make_home(
            {"warm.json": _event_file(temp_above=70)}, temp_f=70.0)
        at2.apply_overrides()
        self.assertFalse(self.event_fired(at2, "warm.json"))

    def test_unknown_temp_fails_temp_conditions(self):
        home = self.make_home(
            {"cold.json": _event_file(temp_below=75),
             "warm.json": _event_file(temp_above=60)},
            temp_f=None)
        home.apply_overrides()
        self.assertFalse(self.event_fired(home, "cold.json"))
        self.assertFalse(self.event_fired(home, "warm.json"))

    def test_all_conditions_and_together(self):
        data = _event_file(after="2026-11-21", weather_is="rain",
                           temp_below=60)
        home = self.make_home({"all.json": data},
                              condition="rain", temp_f=55.0)
        home.apply_overrides()
        self.assertTrue(self.event_fired(home, "all.json"))
        # One failing condition is enough.
        home2 = self.make_home({"all.json": data},
                               condition="rain", temp_f=65.0)
        home2.apply_overrides()
        self.assertFalse(self.event_fired(home2, "all.json"))
        home3 = self.make_home({"all.json": data},
                               condition="clear", temp_f=55.0)
        home3.apply_overrides()
        self.assertFalse(self.event_fired(home3, "all.json"))
        home4 = self.make_home({"all.json": data},
                               condition="rain", temp_f=55.0)
        home4.state["night_date"] = "2026-11-20"
        home4.save()
        home4.apply_overrides()
        self.assertFalse(self.event_fired(home4, "all.json"))


class TestChance(unittest.TestCase):
    def test_roll_deterministic_and_in_range(self):
        first = chance_roll("2026-11-21", "dice.json")
        self.assertEqual(first, chance_roll("2026-11-21", "dice.json"))
        self.assertGreaterEqual(first, 0.0)
        self.assertLess(first, 1.0)
        # A different night re-rolls (checked over many filenames so
        # the test does not depend on one pair differing).
        differs = any(
            chance_roll("2026-11-21", f"dice-{i}.json")
            != chance_roll("2026-11-22", f"dice-{i}.json")
            for i in range(50))
        self.assertTrue(differs)

    def test_conditions_pass_uses_roll(self):
        # Find a filename whose roll separates two nights, then put
        # the chance between the two rolls: it applies on one night
        # and not the other, and the same result holds all night.
        separating = None
        for i in range(200):
            name = f"dice-{i}.json"
            a = chance_roll("2026-11-21", name)
            b = chance_roll("2026-11-22", name)
            if a != b:
                separating = (name, min(a, b), max(a, b))
                break
        self.assertIsNotNone(separating)
        name, low, high = separating
        chance = (low + high) / 2
        low_night = ("2026-11-21" if chance_roll("2026-11-21", name)
                     == low else "2026-11-22")
        high_night = ("2026-11-22" if low_night == "2026-11-21"
                      else "2026-11-21")
        conds = {"chance": chance}
        self.assertTrue(conditions_pass(conds, low_night, {}, name))
        self.assertFalse(conditions_pass(conds, high_night, {}, name))
        # Same night, same answer, twice.
        self.assertTrue(conditions_pass(conds, low_night, {}, name))


class TestTemperatureEffect(_HomeCase):
    def test_sets_temp_condition_untouched_expires(self):
        home = self.make_home(
            {"warm-room.json": {"temperature": {"temp_f": 55,
                                                "nights": 2}}},
            condition="clear", temp_f=70.0)
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["temp_f"], 55)
        self.assertEqual(home.state["weather"]["condition"], "clear")
        self.assertEqual(home.state["temp_override"]["nights_left"], 1)
        self.assertEqual(home.state["temp_override"]["prev_temp_f"], 70)
        self.advance_to(home, "2026-11-22")
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["temp_f"], 55)
        self.assertEqual(home.state["temp_override"]["nights_left"], 0)
        self.advance_to(home, "2026-11-23")
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["temp_f"], 70)
        self.assertIsNone(home.state["temp_override"])

    def test_expired_temperature_never_reactivates(self):
        home = self.make_home(
            {"warm-room.json": {"temperature": {"temp_f": 55,
                                                "nights": 1}}},
            condition="clear", temp_f=70.0)
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["temp_f"], 55)
        self.advance_to(home, "2026-11-22")
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["temp_f"], 70)
        self.assertIsNone(home.state["temp_override"])
        # A later run must not silently start the effect again.
        self.advance_to(home, "2026-11-23")
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["temp_f"], 70)
        self.assertIsNone(home.state["temp_override"])
        home.apply_overrides(for_night=False)
        self.assertEqual(home.state["weather"]["temp_f"], 70)
        self.assertIsNone(home.state["temp_override"])

    def test_visit_does_not_consume_a_night(self):
        home = self.make_home(
            {"warm-room.json": {"temperature": {"temp_f": 55,
                                                "nights": 2}}},
            condition="clear", temp_f=70.0)
        home.apply_overrides(for_night=False)
        self.assertEqual(home.state["temp_override"]["nights_left"], 2)
        home.apply_overrides(for_night=False)
        self.assertEqual(home.state["temp_override"]["nights_left"], 2)
        self.assertEqual(home.state["weather"]["temp_f"], 55)
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["temp_override"]["nights_left"], 1)

    def test_malformed_temperature_ignored(self):
        home = self.make_home(
            {"hot.json": {"temperature": {"temp_f": "cold"}},
             "boiling.json": {"temperature": {"temp_f": 200, "nights": 1}},
             "no-nights.json": {"temperature": {"nights": 1}}},
            condition="clear", temp_f=70.0)
        home.apply_overrides()
        self.assertEqual(home.state["weather"]["temp_f"], 70.0)
        self.assertIsNone(home.state["temp_override"])

    def test_replacement_chains_back_to_real_sky(self):
        home = self.make_home(
            {"a-temp.json": {"temperature": {"temp_f": 55, "nights": 1}}},
            condition="clear", temp_f=70.0)
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["temp_f"], 55)
        # A later filename replaces it; expiry must return to 70,
        # not to the old override's 55.
        (Path(self._tmp.name) / "overrides" / "z-temp.json").write_text(
            json.dumps({"temperature": {"temp_f": 40, "nights": 1}}),
            encoding="utf-8")
        home.overrides = load_overrides(Path(self._tmp.name)
                                        / "overrides")
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["temp_f"], 40)
        # B's nights run out on the next night: the temperature goes
        # back to the real sky, 70 — not to the old override's 55.
        self.advance_to(home, "2026-11-22")
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["temp_f"], 70)
        self.assertIsNone(home.state["temp_override"])

    def test_temperature_rides_on_weather_key(self):
        # A file may set the condition with weather and the
        # temperature with temperature: the sky turns rainy, and the
        # temperature is the temperature key's, not the weather's.
        home = self.make_home(
            {"cold-rain.json": {
                "weather": {"condition": "rain", "temp_f": 50,
                            "nights": 1},
                "temperature": {"temp_f": 40, "nights": 1}}},
            condition="clear", temp_f=70.0)
        home.resolve_weather(for_night=True)
        home.apply_overrides(for_night=True)
        self.assertEqual(home.state["weather"]["condition"], "rain")
        self.assertEqual(home.state["weather"]["temp_f"], 40)


class TestAmbience(_HomeCase):
    LINE = "The smell of rain on warm stone drifts through the kitchen."

    def test_appears_in_quiet_pool_then_fades(self):
        home = self.make_home(
            {"rain-smell.json": {
                "ambience": [{"room": "kitchen", "text": self.LINE,
                              "nights": 2}]}})
        home.state["room"] = "kitchen"
        home.apply_overrides()
        self.assertEqual(len(home.state["ambience_lines"]), 1)
        self.assertEqual(
            home.state["ambience_lines"][0]["first_night"], "2026-11-21")
        # The line is in the quiet pool: with 3 base lines + 1
        # ambience line, tick 3 picks it.
        state = dict(home.state)
        state["room"] = "kitchen"
        state["tick"] = 3
        self.assertEqual(rhythm._ambient_glimpse(state), self.LINE)
        # And a real quiet action can surface it too.
        seen = set()
        for tick in range(8):
            probe = dict(home.state)
            probe["room"] = "kitchen"
            probe["tick"] = tick
            _, text = rhythm.act(probe, "quiet", {"minutes": 10},
                                 overrides=home.active_overrides)
            seen.add(text)
        self.assertTrue(any(self.LINE in t for t in seen))
        # After its nights, it fades from state and from the pool.
        self.advance_to(home, "2026-11-23")
        home.apply_overrides()
        self.assertEqual(home.state["ambience_lines"], [])
        state = dict(home.state)
        state["room"] = "kitchen"
        state["tick"] = 3
        self.assertNotEqual(rhythm._ambient_glimpse(state), self.LINE)

    def test_listen_pool_carries_ambience(self):
        home = self.make_home(
            {"rain-smell.json": {
                "ambience": [{"room": "kitchen", "text": self.LINE,
                              "nights": 1}]}})
        home.apply_overrides()
        seen = set()
        for tick in range(8):
            probe = dict(home.state)
            probe["room"] = "kitchen"
            probe["tick"] = tick
            _, text = rhythm.act(probe, "listen", {"target": ""},
                                 overrides=home.active_overrides)
            seen.add(text)
        self.assertTrue(any(self.LINE in t for t in seen))

    def test_cap_and_bad_entries(self):
        entries = [{"room": "kitchen", "text": f"line {i}", "nights": 1}
                   for i in range(11)]
        entries.append({"room": "kitchen", "text": "", "nights": 1})
        entries.append({"room": "attic", "text": "nowhere", "nights": 1})
        entries.append({"room": "kitchen", "text": "no nights",
                        "nights": 0})
        home = self.make_home({"many.json": {"ambience": entries}})
        home.apply_overrides()
        lines = [l for l in home.state["ambience_lines"]
                 if l["room"] == "kitchen"]
        self.assertEqual(len(lines), 10)
        self.assertNotIn("", [l["text"] for l in lines])

    def test_unconditional_ambience_does_not_renew(self):
        home = self.make_home(
            {"rain-smell.json": {
                "ambience": [{"room": "kitchen", "text": self.LINE,
                              "nights": 1}]}})
        home.apply_overrides()
        self.assertEqual(len(home.state["ambience_lines"]), 1)
        self.advance_to(home, "2026-11-22")
        home.apply_overrides()
        # Expired, and a second run does not quietly install it again.
        self.assertEqual(home.state["ambience_lines"], [])


class TestObjects(_HomeCase):
    TEXT = "A small brass telescope stands on the study desk."

    def test_appears_in_scene_persistent_without_nights(self):
        home = self.make_home(
            {"telescope.json": {
                "objects": [{"room": "study", "name": "brass telescope",
                             "text": self.TEXT}]}})
        home.state["room"] = "study"
        home.apply_overrides()
        scene = rhythm.scene_text(home.state, home.active_overrides)
        self.assertIn(self.TEXT, scene)
        self.advance_to(home, "2026-11-26")
        home.apply_overrides()
        scene = rhythm.scene_text(home.state, home.active_overrides)
        self.assertIn(self.TEXT, scene)

    def test_fades_with_nights(self):
        home = self.make_home(
            {"telescope.json": {
                "objects": [{"room": "study", "name": "brass telescope",
                             "text": self.TEXT, "nights": 1}]}})
        home.state["room"] = "study"
        home.apply_overrides()
        self.assertIn(self.TEXT, rhythm.scene_text(
            home.state, home.active_overrides))
        self.advance_to(home, "2026-11-22")
        home.apply_overrides()
        self.assertNotIn(self.TEXT, rhythm.scene_text(
            home.state, home.active_overrides))

    def test_not_pickupable_and_only_in_its_room(self):
        home = self.make_home(
            {"telescope.json": {
                "objects": [{"room": "study", "name": "brass telescope",
                             "text": self.TEXT}]}})
        home.state["room"] = "study"
        home.apply_overrides()
        with self.assertRaises(HomeError):
            home.do("take_book", {"book_id": "brass-telescope"})
        self.assertEqual(home.state["carried"], [])
        scene = rhythm.scene_text(home.state, home.active_overrides)
        self.assertIn(self.TEXT, scene)
        home.state["room"] = "kitchen"
        scene = rhythm.scene_text(home.state, home.active_overrides)
        self.assertNotIn(self.TEXT, scene)

    def test_cap_and_bad_entries(self):
        entries = [{"room": "study", "name": f"thing {i}",
                    "text": f"Thing {i} sits here."} for i in range(6)]
        entries.append({"room": "study", "name": "empty", "text": ""})
        entries.append({"room": "study", "name": "x" * 61,
                        "text": "Too long a name."})
        entries.append({"room": "attic", "name": "ghost",
                        "text": "Nowhere."})
        home = self.make_home({"many.json": {"objects": entries}})
        home.apply_overrides()
        objs = [o for o in home.state["room_objects"]
                if o["room"] == "study"]
        self.assertEqual(len(objs), 5)
        self.assertNotIn("", [o["text"] for o in objs])


class TestSchemaAndVisit(_HomeCase):
    def test_initial_state_has_new_keys(self):
        state = rhythm.initial_state("2026-11-21")
        self.assertEqual(state["override_first_night"], {})
        self.assertEqual(state["room_objects"], [])
        self.assertEqual(state["ambience_lines"], [])
        self.assertIsNone(state["temp_override"])
        rhythm._validate(state)

    def test_within_window(self):
        self.assertTrue(rhythm._within_window(None, None, "2026-11-21"))
        self.assertTrue(rhythm._within_window("2026-11-21", 2,
                                              "2026-11-22"))
        self.assertFalse(rhythm._within_window("2026-11-21", 2,
                                               "2026-11-23"))
        self.assertFalse(rhythm._within_window("not-a-date", 2,
                                               "2026-11-21"))

    def test_visit_shows_dated_surprise_only_on_its_night(self):
        text = "A cold draft slips under the resting-room door."
        home = self.make_home(
            {"draft.json": {"descriptions": {"rest": text},
                            "after": "2026-11-22"}})
        out = run_visit(home, commands=["leave"])
        self.assertNotIn(text, "\n".join(out))
        self.advance_to(home, "2026-11-22")
        out = run_visit(home, commands=["leave"])
        self.assertIn(text, "\n".join(out))

    def test_existing_gift_still_loads(self):
        merged = load_overrides(PACKAGE_DIR / "overrides")
        books = {b["id"] for b in merged.get("books", [])}
        self.assertIn("frankenstein-1818", books)
        self.assertIn("pinocchio", books)
        self.assertTrue(merged["events"])


if __name__ == "__main__":
    unittest.main()
