"""Tests for the graft's second round (rules version 3).

Stdlib unittest only. All state is synthetic and lives in /tmp — never
the live home. Covers the five rough-edge fixes (digest dedup, story
quote, leave line, outfit naming, garden at night) and the two new
features (feeding the fish, the diary), including the morning
roll-forward that lets fish hunger span days.
"""
from __future__ import annotations

import io
import re
import tempfile
import unittest

from home_sim import library, rhythm
from home_sim.events import summarize_night
from home_sim.tick import Home
from home_sim.visit import parse_command, run_visit, shortlist_lines


def fresh(**overrides):
    s = rhythm.initial_state("2026-11-21")
    s.update(overrides)
    rhythm._validate(s)
    return s


def _record(op, text, minute=1400):
    return {"op": op, "text": text, "provenance": "simulated",
            "night_date": "2026-11-21", "tick": 0, "minute": minute}


class TestDigestDedup(unittest.TestCase):
    def test_no_repeated_sentence(self):
        """The seed-7 night repeated 'I am getting tired' twice and
        'I could do with something to eat' three times. The same moment
        with different need-status suffixes is one moment, and the
        suffixes themselves do not appear in the digest at all — they
        are body state, not moments, and the same suffix can trail two
        different moments."""
        records = [
            _record("write", "I write a few pages — working notes, mostly, "
                    "and one letter I may never send. I am getting tired."),
            _record("read", "I read for a while, following one thought down "
                    "until it branches into three. I am getting tired."),
            _record("write", "I write a few pages — working notes, mostly, "
                    "and one letter I may never send. I feel worn out and "
                    "find it hard to concentrate."),
            _record("read", "I read for a while, following one thought down "
                    "until it branches into three. I could do with "
                    "something to eat."),
            _record("write", "I write a few pages — working notes, mostly, "
                    "and one letter I may never send. I could do with "
                    "something to eat."),
        ]
        digest = summarize_night(records)
        self.assertEqual(digest.count("I write a few pages"), 1)
        self.assertEqual(digest.count("I read for a while"), 1)
        self.assertNotIn("I am getting tired", digest)
        self.assertNotIn("I could do with something to eat", digest)

    def test_seed_seven_night_has_no_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=7, night_date="2026-11-21")
            home.run(max_minutes=480)
            digest = home.digest()
            # No sentence (split on ". ") appears twice in the digest.
            sentences = [s.strip() for s in
                         re.split(r"\.\s+", digest) if s.strip()]
            self.assertEqual(len(sentences), len(set(sentences)),
                             f"repeated sentence in digest: {digest!r}")

    def test_dead_fish_leads_the_digest(self):
        # Three preferred moments fill the digest without the fallback,
        # and the death rides on a plain move — it must still lead.
        records = [
            _record("read", "I read for a while, following one thought down "
                    "until it branches into three."),
            _record("write", "I write a few pages — working notes, mostly, "
                    "and one letter I may never send."),
            _record("brew_tea", "I brew tea and drink it slowly. The "
                    "warmth is welcome."),
            _record("move", "I go to the study. Pip lies still at the "
                    "bottom of the aquarium. The water is quiet."),
        ]
        digest = summarize_night(records)
        self.assertIn("lies still at the bottom of the aquarium", digest)
        self.assertLess(digest.index("lies still at the bottom"),
                        digest.index("I read for a while"))


class TestStoryQuote(unittest.TestCase):
    def test_quote_is_the_story_opening(self):
        for book_id, opening in (
                ("low-tide", "The house had never been handsome"),
                ("rain", "Rain seems simple at a distance"),
                ("window", "The sound of rain changed")):
            self.assertIn(opening,
                          library.passage(book_id,
                                          library.story_start(book_id), 12))

    def test_first_read_quotes_story_not_title_block(self):
        s = fresh(room="study")
        s, _ = rhythm.act(s, "take_book")
        s, text = rhythm.act(s, "read")
        self.assertIn("The house had never been handsome", text)
        self.assertNotIn("THE HOUSE AT LOW TIDE", text)
        # Positions stay honest: words 0 to 250 of the file.
        self.assertEqual(s["books"]["low-tide"]["position"], 250)


class TestLeaveLine(unittest.TestCase):
    def test_no_remaining_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21")
            out = io.StringIO()
            run_visit(home, ["leave"], out=out)
            text = out.getvalue()
            self.assertIn("I left the house.", text)
            self.assertNotIn("remaining time", text)


class TestOutfitNaming(unittest.TestCase):
    def test_scene_names_the_look_not_the_items(self):
        s = fresh(room="rest")
        s, _ = rhythm.act(s, "wear_outfit", {"look_id": "waistcoat"})
        scene = rhythm.scene_text(s)
        self.assertIn("herringbone waistcoat and satchel outfit", scene)
        self.assertNotIn("brown crossbody leather satchel", scene)
        self.assertNotIn("wine-red bow tie", scene)

    def test_open_wardrobe_spells_out_the_outfit(self):
        s = fresh(room="rest")
        s, _ = rhythm.act(s, "wear_outfit", {"look_id": "waistcoat"})
        _, text = rhythm.act(s, "open_wardrobe")
        self.assertIn("brown herringbone waistcoat", text)
        self.assertIn("wine-red bow tie", text)

    def test_mix_and_match_still_lists_pieces(self):
        s = fresh(room="rest")
        s, _ = rhythm.act(s, "wear_outfit", {"look_id": "waistcoat"})
        s, _ = rhythm.act(s, "wear_item", {"item_id": "tartan_scarf"})
        scene = rhythm.scene_text(s)
        self.assertIn("tartan scarf", scene)
        self.assertNotIn("outfit", scene)


class TestGardenAtNight(unittest.TestCase):
    def test_garden_not_offered_at_night(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21")
            # 23:00 — night. Many proposals on the same state; the
            # garden must never appear in the visit shortlist.
            for _ in range(50):
                _, shortlist = shortlist_lines(home)
                for op, args in shortlist:
                    self.assertFalse(
                        op == "move" and args.get("to") == "garden",
                        f"garden offered at night: {shortlist!r}")
            # The resident can still walk there freely; only the
            # suggestion is withheld.
            home.do("move", {"to": "garden"})
            self.assertEqual(home.state["room"], "garden")

    def test_garden_offered_in_daylight(self):
        with tempfile.TemporaryDirectory() as tmp1, \
                tempfile.TemporaryDirectory() as tmp2:
            h1 = Home(tmp1, seed=5, night_date="2026-11-21")
            h2 = Home(tmp2, seed=5, night_date="2026-11-21")
            h1.state["minute"] = 600  # 10:00 sim time, daylight
            h2.state["minute"] = 600
            # Same seed, same state: the filter must be a no-op in
            # daylight, so the visit shortlist equals the raw proposal.
            # The raw proposal is the visit-mode one (night=False):
            # the visit shortlist is a visit proposal, not a night's.
            raw, _ = h1.proposer.propose(h1.state, night=False)
            _, filtered = shortlist_lines(h2)
            self.assertEqual(raw, filtered)
            # And the garden does get offered in daylight sometimes.
            offered = False
            for _ in range(200):
                _, shortlist = shortlist_lines(h2)
                if any(op == "move" and args.get("to") == "garden"
                       for op, args in shortlist):
                    offered = True
                    break
            self.assertTrue(offered, "garden never offered in daylight")


class TestFeedingTheFish(unittest.TestCase):
    def test_feeding_resets_hunger(self):
        s = fresh(room="study")
        s["fish"]["hunger"] = 1500
        s, text = rhythm.act(s, "feed_fish")
        self.assertEqual(s["fish"]["hunger"], 2)  # fed, then 2 min pass
        self.assertIn("scatter a pinch of food", text)

    def test_feeding_needs_the_study(self):
        s = fresh(room="kitchen")
        with self.assertRaises(rhythm.HomeError):
            rhythm.act(s, "feed_fish")

    def test_hunger_warnings_appear_as_hunger_grows(self):
        s = fresh(room="study")
        s["fish"]["hunger"] = 1430
        s, text = rhythm.act(s, "quiet", {"minutes": 20})
        self.assertIn("eager for food", text)
        s = fresh(room="study")
        s["fish"]["hunger"] = 2870
        s, text = rhythm.act(s, "quiet", {"minutes": 20})
        self.assertIn("barely moving", text)
        # And the scene itself shows it.
        scene = rhythm.scene_text(s)
        self.assertIn("barely moving", scene)

    def test_watch_shows_hunger_too(self):
        s = fresh(room="study")
        s["fish"]["hunger"] = 1500
        _, text = rhythm.act(s, "watch_fish", {"minutes": 10})
        self.assertIn("surface", text)

    def test_one_missed_night_does_not_kill(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=7, night_date="2026-11-21")
            home.run(max_minutes=480)
            # Hunger equals the simulated minutes that passed.
            elapsed = home.state["minute"] - 23 * 60
            self.assertEqual(home.state["fish"]["hunger"], elapsed)
            self.assertTrue(home.state["fish"]["alive"])
            self.assertLess(home.state["fish"]["hunger"],
                            rhythm.FISH_HUNGRY_AT)

    def test_fish_dies_only_after_sustained_neglect(self):
        s = fresh(room="study")
        s["fish"]["hunger"] = rhythm.FISH_DEATH_AT - 10
        s, text = rhythm.act(s, "quiet", {"minutes": 20})
        self.assertFalse(s["fish"]["alive"])
        self.assertIn("lies still at the bottom of the aquarium", text)
        # Feeding a dead fish fails; watching it fails too.
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "feed_fish")
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "watch_fish", {"minutes": 10})

    def test_hunger_and_name_survive_the_morning(self):
        """Fish hunger is a debt across days: it must persist through
        the roll-forward, or three days of neglect could never kill."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21")
            home.do("move", {"to": "study"})
            home.do("name_fish", {"name": "Pip"})
            home.run(max_minutes=480)
            elapsed = home.state["minute"] - 23 * 60
            self.assertEqual(home.state["fish"]["hunger"], elapsed)
            home2 = Home(tmp, seed=2)  # rolls to 2026-11-22
            self.assertEqual(home2.state["night_date"], "2026-11-22")
            self.assertEqual(home2.state["fish"]["name"], "Pip")
            self.assertEqual(home2.state["fish"]["hunger"], elapsed)
            self.assertTrue(home2.state["fish"]["alive"])


class TestDiary(unittest.TestCase):
    def test_entries_persist_and_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21")
            home.do("move", {"to": "study"})
            home.do("write_diary", {"text": "The rain kept coming."})
            home.do("write_diary", {"text": "Pip ate well today."})
            self.assertEqual(len(home.state["diary"]), 2)
            self.assertEqual(home.state["diary"][0]["text"],
                             "The rain kept coming.")
            self.assertEqual(home.state["diary"][0]["date"], "2026-11-21")
            _, text = rhythm.act(home.state, "read_diary")
            # Newest first.
            self.assertLess(text.index("Pip ate well today."),
                            text.index("The rain kept coming."))
            home2 = Home(tmp, seed=2)
            self.assertEqual(len(home2.state["diary"]), 2)

    def test_empty_diary_reads_blank(self):
        s = fresh(room="study")
        _, text = rhythm.act(s, "read_diary")
        self.assertIn("still blank", text)

    def test_diary_lives_on_the_desk(self):
        s = fresh(room="kitchen")
        with self.assertRaises(rhythm.HomeError):
            rhythm.act(s, "write_diary", {"text": "Not in the study."})
        with self.assertRaises(rhythm.HomeError):
            rhythm.act(s, "read_diary")
        scene = rhythm.scene_text(fresh(room="study"))
        self.assertIn("diary rests on the desk", scene)

    def test_notes_and_diary_stay_separate(self):
        s = fresh(room="study")
        s, _ = rhythm.act(s, "write_note", {"text": "A private note."})
        s, _ = rhythm.act(s, "write_diary", {"text": "A diary entry."})
        self.assertEqual(s["notes"], ["A private note."])
        self.assertEqual(len(s["diary"]), 1)
        self.assertEqual(s["diary"][0]["text"], "A diary entry.")

    def test_visit_commands(self):
        self.assertEqual(parse_command("write_diary The rain kept coming."),
                         ("write_diary", {"text": "The rain kept coming."}))
        self.assertEqual(parse_command("read_diary"), ("read_diary", {}))
        self.assertEqual(parse_command("feed_fish"), ("feed_fish", {}))


if __name__ == "__main__":
    unittest.main()
