"""Tests for the Cozy Corner graft (rules version 2).

Stdlib unittest only. All state is synthetic and lives in /tmp — never
the live home. Covers custody, real reading, the fish, the wardrobe,
overrides, the narrative digest, and visit mode.
"""
from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from home_sim import library, rhythm
from home_sim.events import summarize_night
from home_sim.overrides import apply_injections, load_overrides
from home_sim.tick import Home
from home_sim.visit import parse_command, run_visit

PACKAGE_DIR = Path(__file__).resolve().parent.parent


def fresh(**overrides):
    s = rhythm.initial_state("2026-11-21")
    s.update(overrides)
    rhythm._validate(s)
    return s


class TestCustody(unittest.TestCase):
    def test_mug_carried_across_moves(self):
        s = fresh(room="kitchen")
        s, _ = rhythm.act(s, "brew_tea")
        self.assertEqual(s["mug_in"], "kitchen")
        s, text = rhythm.act(s, "take_mug")
        self.assertIn("mug", s["carried"])
        self.assertIsNone(s["mug_in"])
        s, _ = rhythm.act(s, "move", {"to": "study"})
        self.assertIn("mug", s["carried"])
        scene = rhythm.scene_text(s)
        self.assertIn("mug of tea", scene)
        s, text = rhythm.act(s, "sip_tea")
        self.assertIn("tannic", text)
        self.assertEqual(s["mug_sips"], 2)
        s, _ = rhythm.act(s, "put_mug")
        self.assertNotIn("mug", s["carried"])
        self.assertEqual(s["mug_in"], "study")

    def test_hands_hold_two_things(self):
        s = fresh(room="kitchen")
        s, _ = rhythm.act(s, "prepare_meal")
        s, _ = rhythm.act(s, "take_plate")
        s, _ = rhythm.act(s, "brew_tea")
        s, _ = rhythm.act(s, "take_mug")
        self.assertEqual(len(s["carried"]), 2)
        s, _ = rhythm.act(s, "move", {"to": "study"})
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "take_book")
        s, _ = rhythm.act(s, "put_mug")
        s, _ = rhythm.act(s, "take_book")
        self.assertIn("book:low-tide", s["carried"])

    def test_plate_meal_can_be_eaten_anywhere(self):
        s = fresh(room="kitchen", hunger=8)
        s, _ = rhythm.act(s, "prepare_meal")
        s, _ = rhythm.act(s, "take_plate")
        s, _ = rhythm.act(s, "move", {"to": "study"})
        s, text = rhythm.act(s, "eat")
        self.assertEqual(s["hunger"], 3)
        self.assertNotIn("plate", s["carried"])
        self.assertIn("less hungry", text)

    def test_eat_without_meal_still_fails(self):
        s = fresh(room="kitchen")
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "eat")


class TestRealReading(unittest.TestCase):
    def test_read_advances_real_word_position(self):
        s = fresh(room="study")
        s, _ = rhythm.act(s, "take_book")
        self.assertEqual(s["books"]["low-tide"]["location"], "carried")
        s, text = rhythm.act(s, "read")
        self.assertEqual(s["books"]["low-tide"]["position"], 250)
        self.assertIn("250 words", text)
        self.assertIn("The House at Low Tide", text)
        # The quote is the story opening, not the title block.
        self.assertIn("The house had never been handsome", text)
        self.assertNotIn("THE HOUSE AT LOW TIDE", text)
        self.assertIn("The house had never been handsome",
                      library.passage("low-tide", 0, 250))

    def test_bookmark_and_position_persist_across_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21")
            home.do("move", {"to": "study"})
            home.do("take_book", {})
            home.do("read")
            home.do("bookmark", {"name": "first real visit"})
            self.assertEqual(
                home.state["books"]["low-tide"]["position"], 250)
            self.assertEqual(
                home.state["books"]["low-tide"]["bookmark"],
                "first real visit")
            home2 = Home(tmp, seed=2)
            self.assertEqual(
                home2.state["books"]["low-tide"]["position"], 250)
            self.assertEqual(
                home2.state["books"]["low-tide"]["bookmark"],
                "first real visit")

    def test_read_without_a_book_stays_abstract(self):
        s = fresh(room="study")
        s, text = rhythm.act(s, "read")
        self.assertIn("branches into three", text)
        self.assertEqual(s["books"]["low-tide"]["position"], 0)

    def test_book_can_be_put_down_elsewhere(self):
        s = fresh(room="study")
        s, _ = rhythm.act(s, "take_book", {"book_id": "rain"})
        s, _ = rhythm.act(s, "move", {"to": "kitchen"})
        s, _ = rhythm.act(s, "put_book")
        self.assertEqual(s["books"]["rain"]["location"], "kitchen")
        self.assertNotIn("book:rain", s["carried"])


class TestFish(unittest.TestCase):
    def test_naming_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21")
            home.do("move", {"to": "study"})
            home.do("name_fish", {"name": "Pip"})
            self.assertEqual(home.state["fish"]["name"], "Pip")
            home2 = Home(tmp, seed=2)
            self.assertEqual(home2.state["fish"]["name"], "Pip")

    def test_fish_starts_unnamed(self):
        s = fresh()
        self.assertEqual(s["fish"]["name"], "")
        self.assertTrue(s["fish"]["alive"])

    def test_watch_observations_do_not_repeat(self):
        s = fresh(room="study")
        _, t1 = rhythm.act(s, "watch_fish", {"minutes": 10})
        s, _ = rhythm.act(s, "watch_fish", {"minutes": 10})
        _, t2 = rhythm.act(s, "watch_fish", {"minutes": 10})
        self.assertNotEqual(t1, t2)
        self.assertIn("aquarium", t1)
        self.assertIn("aquarium", t2)

    def test_quiet_in_study_shows_the_fish(self):
        s = fresh(room="study")
        _, text = rhythm.act(s, "quiet", {"minutes": 20})
        self.assertIn("I remained here without pursuing a task.", text)

    def test_quiet_minutes_argument(self):
        s = fresh(room="study", minute=1440)
        s, _ = rhythm.act(s, "quiet", {"minutes": 20})
        self.assertEqual(s["minute"], 1460)
        with self.assertRaises(rhythm.HomeError):
            rhythm.act(s, "quiet", {"minutes": 21})


class TestWardrobe(unittest.TestCase):
    def test_change_requires_free_hands(self):
        s = fresh(room="kitchen")
        s, _ = rhythm.act(s, "brew_tea")
        s, _ = rhythm.act(s, "take_mug")
        s, _ = rhythm.act(s, "move", {"to": "rest"})
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "wear_outfit", {"look_id": "waistcoat"})
        s, _ = rhythm.act(s, "put_mug")
        s, text = rhythm.act(s, "wear_outfit", {"look_id": "waistcoat"})
        self.assertIn("herringbone waistcoat", text)
        self.assertEqual(s["wardrobe"]["current_look"], "waistcoat")
        self.assertEqual(s["wardrobe"]["worn"]["waist"],
                         "tweed_waistcoat")
        scene = rhythm.scene_text(s)
        self.assertIn("herringbone waistcoat", scene)

    def test_mix_and_match_clears_the_look(self):
        s = fresh(room="rest")
        s, _ = rhythm.act(s, "wear_outfit", {"look_id": "waistcoat"})
        s, text = rhythm.act(s, "wear_item", {"item_id": "tartan_scarf"})
        self.assertIsNone(s["wardrobe"]["current_look"])
        self.assertEqual(s["wardrobe"]["worn"]["neckwear"], "tartan_scarf")
        self.assertEqual(s["wardrobe"]["worn"]["waist"],
                         "tweed_waistcoat")
        self.assertIn("tartan scarf", text)

    def test_remove_item_needs_free_hands_too(self):
        s = fresh(room="rest")
        s, _ = rhythm.act(s, "wear_outfit", {"look_id": "denim"})
        s, _ = rhythm.act(s, "move", {"to": "kitchen"})
        s, _ = rhythm.act(s, "brew_tea")
        s, _ = rhythm.act(s, "take_mug")
        s, _ = rhythm.act(s, "move", {"to": "rest"})
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "remove_item", {"slot": "outerwear"})
        s, _ = rhythm.act(s, "put_mug")
        s, text = rhythm.act(s, "remove_item", {"slot": "outerwear"})
        self.assertIn("denim jacket", text)
        self.assertNotIn("outerwear", s["wardrobe"]["worn"])

    def test_only_four_looks(self):
        from home_sim.wardrobe import LOOKS
        self.assertEqual(set(LOOKS), {"waistcoat", "denim", "plaid", "lab"})


class TestSenses(unittest.TestCase):
    def test_object_specific_descriptions(self):
        s = fresh(room="rest")
        _, text = rhythm.act(s, "touch", {"target": "blanket"})
        self.assertIn("soft and slightly heavy", text)
        s = fresh(room="kitchen")
        s, _ = rhythm.act(s, "brew_tea")
        _, text = rhythm.act(s, "taste", {"target": "tea"})
        self.assertIn("tannic", text)
        s = fresh(room="study")
        _, text = rhythm.act(s, "listen")
        self.assertIn("filter", text)

    def test_senses_fall_back_gracefully(self):
        s = fresh(room="garden")
        _, text = rhythm.act(s, "touch", {"target": "gate"})
        self.assertIn("solid and familiar", text)


class TestOverrides(unittest.TestCase):
    def test_empty_folder_changes_nothing(self):
        # A genuinely empty folder — not the package's overrides
        # folder, which now holds real gifts and is empty no longer.
        with tempfile.TemporaryDirectory() as tmp:
            overrides = load_overrides(Path(tmp))
            self.assertEqual(overrides, {"descriptions": {}, "events": []})
        s = fresh()
        self.assertEqual(s["applied_overrides"], [])

    def test_description_override_takes_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "gift.json").write_text(json.dumps({
                "descriptions": {
                    "study": "The study is full of late light and the "
                             "smell of rain."}}), encoding="utf-8")
            overrides = load_overrides(folder)
            s = fresh(room="study")
            scene = rhythm.scene_text(s, overrides)
            self.assertIn("late light and the smell of rain", scene)
            self.assertNotIn("I am in the study.", scene)

    def test_event_injection_applies_once(self):
        from home_sim.events import EventLog

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "gift.json").write_text(json.dumps({
                "events": [{"kind": "gift",
                            "text": "A small parcel waits on the desk.",
                            "room": "study"}]}), encoding="utf-8")
            overrides = load_overrides(folder)
            log = EventLog(tmp)
            s = fresh(room="rest")
            s2 = apply_injections(s, log, overrides)
            last = log.read_all()[-1]
            self.assertEqual(last["provenance"], "world")
            self.assertIn("parcel", last["text"])
            self.assertEqual(s2["world_bias"],
                             {"room": "study", "ticks_left": 6})
            self.assertEqual(s2["applied_overrides"], ["gift.json"])
            n = len(log.read_all())
            s3 = apply_injections(s2, log, overrides)
            self.assertEqual(len(log.read_all()), n)
            self.assertEqual(s3["applied_overrides"], ["gift.json"])

    def test_malformed_override_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "broken.json").write_text("{not json",
                                                encoding="utf-8")
            (folder / "notes.txt").write_text("not json at all",
                                              encoding="utf-8")
            overrides = load_overrides(folder)
            self.assertEqual(overrides, {"descriptions": {}, "events": []})


class TestNarrativeDigest(unittest.TestCase):
    def test_no_tally_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=7, night_date="2026-11-21")
            home.run(max_minutes=480)
            d = home.digest()
            self.assertIn("simulated", d)
            self.assertIn("While you were out", d)
            self.assertNotIn("did:", d)
            self.assertNotRegex(d, r" x\d+")
            self.assertRegex(d, r"\d+ moments in all")

    def test_empty_digest_still_marked(self):
        self.assertIn("simulated", summarize_night([]))


class TestVisitMode(unittest.TestCase):
    def test_parse_commands(self):
        self.assertEqual(parse_command("quiet 20"),
                         ("quiet", {"minutes": 20}))
        self.assertEqual(parse_command("wear_outfit waistcoat"),
                         ("wear_outfit", {"look_id": "waistcoat"}))
        self.assertEqual(parse_command("name_fish Pip"),
                         ("name_fish", {"name": "Pip"}))
        self.assertEqual(parse_command("write_note The fish is Pip now."),
                         ("write_note", {"text": "The fish is Pip now."}))
        self.assertEqual(parse_command("move study"),
                         ("move", {"to": "study"}))
        self.assertIsNone(parse_command("  "))
        with self.assertRaises(ValueError):
            parse_command("dance")

    def test_scripted_visit(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21")
            out = io.StringIO()
            # Name the book: the shelf now holds gifted books too, and
            # a bare take_book takes the first by id.
            run_visit(home, ["move study", "take_book low-tide", "read",
                             "bookmark test-mark", "leave"], out=out)
            self.assertEqual(
                home.state["books"]["low-tide"]["position"], 250)
            self.assertEqual(
                home.state["books"]["low-tide"]["bookmark"], "test-mark")
            text = out.getvalue()
            self.assertIn("250 words", text)
            self.assertIn("I left the house.", text)

    def test_visit_shortlist_pick(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=3, night_date="2026-11-21")
            out = io.StringIO()
            run_visit(home, ["1", "leave"], out=out)
            self.assertGreater(home.state["tick"], 0)
            self.assertIn("The house suggests:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
