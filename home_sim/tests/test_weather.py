"""Tests for the weather system (rules version 4).

Stdlib unittest only. All state is synthetic and lives in /tmp — never
the live home. No test here touches the network: every lookup goes
through an injected fake fetcher. Covers the WMO mapping, offline
fallbacks, the override countdown and expiry, scene and digest text,
the bounded garden nudge, and the schema.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from home_sim import rhythm, weather
from home_sim.events import summarize_night
from home_sim.overrides import load_overrides
from home_sim.tick import Home
from home_sim.visit import run_visit


def fresh(**overrides):
    s = rhythm.initial_state("2026-11-21")
    s.update(overrides)
    rhythm._validate(s)
    return s


def _fake_rain(url):
    return {"current": {"temperature_2m": 45.2, "weather_code": 61,
                        "wind_speed_10m": 8.0}}


def _fake_clear(url):
    return {"current": {"temperature_2m": 71.2, "weather_code": 0,
                        "wind_speed_10m": 9.4}}


def _fake_freezing(url):
    return {"current": {"temperature_2m": 28.0, "weather_code": 0,
                        "wind_speed_10m": 5.0}}


def _fake_boom(url):
    raise ConnectionError("no network")


def _weather(condition, temp_f=None, source="live", nights_left=0):
    return {"condition": condition, "temp_f": temp_f, "source": source,
            "as_of": "2026-11-21T15:45:00+00:00",
            "nights_left": nights_left, "override_file": None,
            "nudge_night": None}


def _record(op, text, minute=1400):
    return {"op": op, "text": text, "provenance": "simulated",
            "night_date": "2026-11-21", "tick": 0, "minute": minute}


class TestConditionMapping(unittest.TestCase):
    def test_one_code_per_condition(self):
        cases = {0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy",
                 45: "fog", 48: "fog", 51: "rain", 55: "rain",
                 61: "rain", 65: "rain", 66: "rain", 80: "rain",
                 71: "snow", 75: "snow", 85: "snow", 95: "storm",
                 99: "storm"}
        for code, want in cases.items():
            with self.subTest(code=code):
                self.assertEqual(
                    weather.condition_from_code(code), want)

    def test_unknown_code_is_cloudy(self):
        """An unknown code is an honest shrug: overcast, neither clear
        nor wet. Never clear, never a storm the sky did not have."""
        self.assertEqual(weather.condition_from_code(999), "cloudy")
        self.assertEqual(weather.condition_from_code(None), "cloudy")
        self.assertEqual(weather.condition_from_code("nonsense"), "cloudy")

    def test_wind_earns_its_own_condition(self):
        self.assertEqual(
            weather.condition_from_code(0, wind_mph=30.0), "wind")
        self.assertEqual(
            weather.condition_from_code(0, wind_mph=9.4), "clear")
        # Precipitation wins over wind: a rainy gale is rainy.
        self.assertEqual(
            weather.condition_from_code(61, wind_mph=30.0), "rain")


class TestFetchLive(unittest.TestCase):
    def test_fake_fetch_maps_to_rain(self):
        live = weather.fetch_live_weather(_fake_rain)
        self.assertEqual(live["condition"], "rain")
        self.assertEqual(live["temp_f"], 45.2)

    def test_fake_fetch_failure_raises(self):
        """fetch_live_weather never invents weather: it raises and the
        caller decides the fallback."""
        with self.assertRaises(ConnectionError):
            weather.fetch_live_weather(_fake_boom)

    def test_missing_fields_raise(self):
        def _empty(url):
            return {"current": {}}
        with self.assertRaises(ValueError):
            weather.fetch_live_weather(_empty)


class TestResolve(unittest.TestCase):
    def test_offline_fresh_state_is_default(self):
        """No fetcher at all (Home's default) means no live lookup is
        attempted: the house falls back to its neutral default."""
        state = rhythm.initial_state("2026-11-21")
        resolved = weather.resolve_weather(state, {})
        self.assertEqual(resolved["condition"], "clear")
        self.assertEqual(resolved["source"], "default")
        self.assertIsNone(resolved["temp_f"])

    def test_live_lookup_wins_when_it_works(self):
        state = rhythm.initial_state("2026-11-21")
        resolved = weather.resolve_weather(state, {}, fetcher=_fake_rain)
        self.assertEqual(resolved["condition"], "rain")
        self.assertEqual(resolved["temp_f"], 45.2)
        self.assertEqual(resolved["source"], "live")

    def test_failed_lookup_keeps_cached_weather(self):
        state = fresh(weather=_weather("rain", 45.2, source="live"))
        resolved = weather.resolve_weather(state, {}, fetcher=_fake_boom)
        self.assertEqual(resolved["condition"], "rain")
        self.assertEqual(resolved["source"], "cached")

    def test_override_beats_live_and_expires(self):
        """An override for 2 nights: snow, snow, then the live sky.
        The expired file never re-activates while it still sits there."""
        state = rhythm.initial_state("2026-11-21")
        overrides = {"weather": {"condition": "snow", "temp_f": 30,
                                 "nights": 2, "file": "snow.json"}}
        first = weather.resolve_weather(state, overrides,
                                        fetcher=_fake_clear)
        self.assertEqual(first["condition"], "snow")
        self.assertEqual(first["source"], "override")
        self.assertEqual(first["nights_left"], 1)
        state["weather"] = first
        second = weather.resolve_weather(state, overrides,
                                         fetcher=_fake_clear)
        self.assertEqual(second["condition"], "snow")
        self.assertEqual(second["nights_left"], 0)
        state["weather"] = second
        third = weather.resolve_weather(state, overrides,
                                        fetcher=_fake_clear)
        self.assertEqual(third["condition"], "clear")
        self.assertEqual(third["source"], "live")

    def test_override_nights_default_to_one(self):
        state = rhythm.initial_state("2026-11-21")
        overrides = {"weather": {"condition": "rain", "file": "r.json"}}
        first = weather.resolve_weather(state, overrides,
                                        fetcher=_fake_clear)
        self.assertEqual(first["condition"], "rain")
        self.assertEqual(first["nights_left"], 0)
        state["weather"] = first
        second = weather.resolve_weather(state, overrides,
                                         fetcher=_fake_clear)
        self.assertEqual(second["condition"], "clear")

    def test_visit_does_not_consume_a_night(self):
        state = rhythm.initial_state("2026-11-21")
        overrides = {"weather": {"condition": "rain", "temp_f": 45,
                                 "nights": 2, "file": "r.json"}}
        first = weather.resolve_weather(state, overrides,
                                        fetcher=_fake_clear,
                                        for_night=False)
        self.assertEqual(first["nights_left"], 2)
        state["weather"] = first
        second = weather.resolve_weather(state, overrides,
                                         fetcher=_fake_clear,
                                         for_night=False)
        self.assertEqual(second["nights_left"], 2)

    def test_deleting_the_file_ends_the_override_early(self):
        state = fresh(weather=_weather("snow", 30, source="override",
                                       nights_left=2))
        state["weather"]["override_file"] = "snow.json"
        resolved = weather.resolve_weather(state, {},
                                           fetcher=_fake_clear)
        self.assertEqual(resolved["condition"], "clear")
        self.assertEqual(resolved["source"], "live")

    def test_newer_override_file_replaces_an_active_one(self):
        state = fresh(weather=_weather("snow", 30, source="override",
                                       nights_left=2))
        state["weather"]["override_file"] = "snow.json"
        overrides = {"weather": {"condition": "rain", "temp_f": 50,
                                 "nights": 1, "file": "rain.json"}}
        resolved = weather.resolve_weather(state, overrides,
                                           fetcher=_fake_clear)
        self.assertEqual(resolved["condition"], "rain")
        self.assertEqual(resolved["override_file"], "rain.json")


class TestLoadOverridesWeather(unittest.TestCase):
    def test_valid_weather_file_loads_with_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "rain.json").write_text(json.dumps(
                {"weather": {"condition": "rain", "temp_f": 45,
                             "nights": 3}}), encoding="utf-8")
            merged = load_overrides(folder)
            self.assertEqual(merged["weather"]["condition"], "rain")
            self.assertEqual(merged["weather"]["temp_f"], 45)
            self.assertEqual(merged["weather"]["nights"], 3)
            self.assertEqual(merged["weather"]["file"], "rain.json")

    def test_unknown_condition_is_ignored_fail_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "bad.json").write_text(json.dumps(
                {"weather": {"condition": "banana"}}), encoding="utf-8")
            merged = load_overrides(folder)
            self.assertNotIn("weather", merged)
            self.assertEqual(merged,
                             {"descriptions": {}, "events": []})

    def test_bad_temp_and_nights_are_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "x.json").write_text(json.dumps(
                {"weather": {"condition": "fog", "temp_f": "warm",
                             "nights": 0}}), encoding="utf-8")
            merged = load_overrides(folder)
            self.assertIsNone(merged["weather"]["temp_f"])
            self.assertEqual(merged["weather"]["nights"], 1)

    def test_malformed_files_still_load_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "notes.txt").write_text("not json at all",
                                              encoding="utf-8")
            merged = load_overrides(folder)
            self.assertEqual(merged, {"descriptions": {}, "events": []})


class TestSceneAndDigest(unittest.TestCase):
    def test_scene_names_the_weather(self):
        state = fresh(weather=_weather("rain", 45.2))
        text = rhythm.scene_text(state)
        self.assertIn("rain", text.lower())

    def test_scene_varies_by_night(self):
        """The same condition reads differently on different nights:
        the sentence rotates by night date, not a repeated boilerplate."""
        seen = set()
        for night in ("2026-11-21", "2026-11-22", "2026-11-23",
                      "2026-11-24", "2026-11-25"):
            state = rhythm.initial_state(night)
            state["weather"] = _weather("rain", 45.2)
            seen.add(weather.weather_sentence(state["weather"], night))
        self.assertGreater(len(seen), 1)

    def test_scene_varies_within_a_night(self):
        """And within one night the sentence rotates as the ticks pass,
        so a visit that shows the scene after every action does not
        repeat one line ten times in a row."""
        seen = set()
        for tick in range(6):
            seen.add(weather.weather_sentence(_weather("rain", 45.2),
                                              "2026-11-21", tick))
        self.assertGreater(len(seen), 1)

    def test_garden_scene_gets_a_weather_clause(self):
        state = fresh(room="garden",
                      weather=_weather("rain", 45.2))
        self.assertIn("Rain soaks the beds.", rhythm.scene_text(state))

    def test_quiet_in_the_rain_sounds_like_rain(self):
        state = fresh(room="garden",
                      weather=_weather("rain", 45.2))
        _new, text = rhythm.act(state, "quiet", {"minutes": 5})
        self.assertIn("rain", text.lower())

    def test_digest_header_names_the_condition(self):
        records = [_record("read", "I read for a while.")]
        digest = summarize_night(
            records, weather=_weather("rain", 45.2))
        self.assertIn("a rainy night", digest)
        self.assertIn("45", digest)

    def test_digest_header_unchanged_without_weather(self):
        records = [_record("read", "I read for a while.")]
        digest = summarize_night(records)
        self.assertIn("the night of 2026-11-21:", digest)
        self.assertNotIn("a rainy night", digest)

    def test_clear_freezing_night_says_so(self):
        state = fresh(weather=_weather("clear", 28.0))
        self.assertIn("cold", rhythm.scene_text(state).lower())


class TestGardenNudge(unittest.TestCase):
    def test_rain_waters_once_per_night(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21",
                        weather_fetcher=_fake_rain)
            before = home.state["garden_growth"]
            home.resolve_weather(for_night=True)
            home._apply_weather_nudge()
            self.assertEqual(home.state["garden_growth"],
                             min(rhythm.GROWTH_CAP, before + 1))
            home._apply_weather_nudge()
            self.assertEqual(home.state["garden_growth"],
                             min(rhythm.GROWTH_CAP, before + 1))

    def test_freezing_gives_the_garden_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21",
                        weather_fetcher=_fake_freezing)
            before = home.state["garden_growth"]
            home.resolve_weather(for_night=True)
            home._apply_weather_nudge()
            self.assertEqual(home.state["garden_growth"], before)

    def test_clear_night_gives_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21",
                        weather_fetcher=_fake_clear)
            before = home.state["garden_growth"]
            home.resolve_weather(for_night=True)
            home._apply_weather_nudge()
            self.assertEqual(home.state["garden_growth"], before)

    def test_full_night_applies_nudge_and_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=7, night_date="2026-11-21",
                        weather_fetcher=_fake_rain)
            home.run(max_minutes=480)
            self.assertEqual(home.state["weather"]["condition"], "rain")
            self.assertEqual(home.state["weather"]["nudge_night"],
                             "2026-11-21")
            digest = home.digest()
            self.assertIn("a rainy night", digest)


class TestSchemaAndRollForward(unittest.TestCase):
    def test_rules_version_six_and_weather_key(self):
        self.assertEqual(rhythm.RULES, "calibos-home-rhythm/6")
        state = rhythm.initial_state("2026-11-21")
        self.assertIn("weather", state)
        self.assertEqual(state["weather"]["condition"], "clear")
        self.assertIsNone(state["weather"]["temp_f"])
        self.assertIn("world_notices", state)
        self.assertEqual(state["world_notices"], [])
        rhythm._validate(state)

    def test_state_without_weather_is_rejected(self):
        """The live state will need a migration adding the weather
        key; an old-schema state must fail loudly, never silently."""
        state = rhythm.initial_state("2026-11-21")
        del state["weather"]
        with self.assertRaises(rhythm.HomeConflict):
            rhythm._validate(state)

    def test_weather_survives_the_morning_roll_forward(self):
        """An override countdown and a cached sky span nights: the
        morning roll-forward carries the weather with the fish, the
        books, and the diary — it is a possession, not a need."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21")
            home.overrides = {
                "weather": {"condition": "snow", "temp_f": 30,
                            "nights": 3, "file": "snow.json"},
                "descriptions": {}, "events": []}
            home.run(max_minutes=480)
            self.assertEqual(home.state["weather"]["condition"], "snow")
            self.assertEqual(home.state["weather"]["nights_left"], 2)
            home2 = Home(tmp, seed=2)  # rolls to 2026-11-22
            self.assertEqual(home2.state["night_date"], "2026-11-22")
            self.assertEqual(home2.state["weather"]["condition"], "snow")
            self.assertEqual(home2.state["weather"]["nights_left"], 2)

    def test_visit_uses_the_live_sky(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=3, night_date="2026-11-21",
                        weather_fetcher=_fake_rain)
            log = run_visit(home, ["look", "leave"], out=io.StringIO())
            self.assertTrue(any("rain" in line.lower() for line in log))
            self.assertEqual(home.state["weather"]["condition"], "rain")
            self.assertEqual(home.state["weather"]["source"], "live")


class TestGardenMoonlight(unittest.TestCase):
    """Night garden has no lamp: the scene must say moonlight, not lamp."""

    def _night_state(self, room, lamps):
        st = rhythm.initial_state("2026-11-21")
        st["minute"] = 1380  # 23:00, night
        st["room"] = room
        st["lamps"] = lamps
        return st

    def test_garden_at_night_says_moonlight(self):
        st = self._night_state("garden", {"study": True, "kitchen": True,
                                          "garden": False, "rest": True})
        text = rhythm.scene_text(st)
        self.assertIn("Moonlight is enough to see by.", text)
        self.assertNotIn("lamp gives a warm light", text)

    def test_lit_room_at_night_says_lamp(self):
        st = self._night_state("study", {"study": True, "kitchen": True,
                                         "garden": False, "rest": True})
        text = rhythm.scene_text(st)
        self.assertIn("The lamp gives a warm light.", text)

    def test_lit_garden_lamp_says_lamp(self):
        st = self._night_state("garden", {"study": True, "kitchen": True,
                                          "garden": True, "rest": True})
        text = rhythm.scene_text(st)
        self.assertIn("The lamp gives a warm light.", text)
