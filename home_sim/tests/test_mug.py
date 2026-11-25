"""Tests for the real tea mug (rules version 7).

Stdlib unittest only. All state is synthetic and lives in /tmp — never
the live home. Covers the analytic cooling curve, sip warmth bands,
take/put custody against the two-hand cap, brewing with full hands,
scene wording, the two-minute sip, the night proposer never drinking
tea, the morning digest, and the overnight roll-forward.
"""
from __future__ import annotations

import tempfile
import unittest

from home_sim import rhythm
from home_sim.events import EventLog, summarize_night
from home_sim.tick import Home, Proposer, _carry_forward
from home_sim.visit import shortlist_lines


def fresh(**overrides):
    s = rhythm.initial_state("2026-11-21")
    s.update(overrides)
    rhythm._validate(s)
    return s


def _mug_at(minute, location="carried", brewed_minute=1400):
    """A state with a fresh 160 F brew, at a chosen sim-minute."""
    carried = ["mug"] if location == "carried" else []
    return fresh(
        minute=minute, carried=carried,
        mug={"location": location, "temp_f": 160,
             "brewed_minute": brewed_minute})


class TestCooling(unittest.TestCase):
    def test_hot_at_brew_then_warm_then_cold(self):
        # Analytic: 160 F at the brew, cooling toward indoor 68 F.
        self.assertEqual(rhythm.mug_temp(_mug_at(1400)), 160)
        self.assertEqual(rhythm.mug_warmth(_mug_at(1400)), "hot")
        # Ten minutes on: just past hot, into warm.
        self.assertEqual(rhythm.mug_warmth(_mug_at(1410)), "warm")
        self.assertAlmostEqual(rhythm.mug_temp(_mug_at(1410)), 138.9,
                               places=1)
        # Half an hour on: still warm, near the cool edge.
        self.assertEqual(rhythm.mug_warmth(_mug_at(1430)), "warm")
        # Past an hour: cold.
        self.assertEqual(rhythm.mug_warmth(_mug_at(1470)), "cold")
        self.assertAlmostEqual(rhythm.mug_temp(_mug_at(1480)), 79.5,
                               places=1)

    def test_approaches_indoor_temperature_without_reaching_it(self):
        # A whole night later the tea is at room temperature for every
        # practical purpose, but the exponential never quite lands on 68.
        temp = rhythm.mug_temp(_mug_at(1400 + 600))
        self.assertGreater(temp, 68)
        self.assertLess(temp, 68.5)

    def test_no_mug_means_no_temperature(self):
        s = fresh()
        self.assertIsNone(rhythm.mug_temp(s))
        self.assertIsNone(rhythm.mug_warmth(s))

    def test_cooling_follows_the_clock_not_the_action_count(self):
        # The same 40 minutes pass whether spent reading or sitting
        # quiet: temperature is purely a function of time.
        s = fresh(room="kitchen")
        s, _ = rhythm.act(s, "brew_tea")  # minute 1400, hot
        s, _ = rhythm.act(s, "quiet", {"minutes": 20})
        s, _ = rhythm.act(s, "quiet", {"minutes": 20})
        self.assertEqual(s["minute"], 1440)
        self.assertEqual(rhythm.mug_warmth(s), "cool")
        self.assertAlmostEqual(rhythm.mug_temp(s), 100.5, places=1)


class TestSip(unittest.TestCase):
    def test_sip_reports_each_warmth_band(self):
        cases = ((1402, "still hot"), (1420, "still warm"),
                 (1440, "gone cool"), (1480, "gone cold"))
        for minute, phrase in cases:
            with self.subTest(minute=minute):
                s = _mug_at(minute)
                _, text = rhythm.act(s, "sip")
                self.assertIn(phrase, text)

    def test_sip_takes_two_minutes_and_slakes_thirst(self):
        s = _mug_at(1402, brewed_minute=1400)
        s["thirst"] = 5
        s, _ = rhythm.act(s, "sip")
        self.assertEqual(s["minute"], 1404)
        self.assertEqual(s["thirst"], 4)

    def test_sip_from_the_desk_without_taking_it(self):
        # The mug may be sipped where it sits, not only in hand.
        s = _mug_at(1402, location="desk")
        s["room"] = "study"
        _, text = rhythm.act(s, "sip")
        self.assertIn("still hot", text)
        self.assertNotIn("mug", s["carried"])

    def test_sip_fails_when_the_mug_is_elsewhere(self):
        s = _mug_at(1402, location="kitchen")
        s["room"] = "study"
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "sip")

    def test_sipping_does_not_change_the_temperature(self):
        # Temperature is purely a function of time: sipping cools
        # nothing but the drinker's thirst.
        s = _mug_at(1402, brewed_minute=1400)
        before = rhythm.mug_temp(s)
        s, _ = rhythm.act(s, "sip")
        after = rhythm.mug_temp(s)
        # Two minutes of cooling only: ~155.3 -> ~150.9, not a sip-sized
        # drop, and the warmth band is unchanged.
        self.assertAlmostEqual(before, 155.3, places=1)
        self.assertAlmostEqual(after, 150.9, places=1)
        self.assertEqual(rhythm.mug_warmth(s), "hot")


class TestCustody(unittest.TestCase):
    def test_brew_with_full_hands_leaves_the_mug_in_the_kitchen(self):
        s = fresh(room="study")
        s, _ = rhythm.act(s, "take_book", {"book_id": "low-tide"})
        s, _ = rhythm.act(s, "take_book", {"book_id": "rain"})
        s, _ = rhythm.act(s, "move", {"to": "kitchen"})
        s, text = rhythm.act(s, "brew_tea")
        self.assertIn("hands are full", text)
        self.assertIn("kitchen counter", text)
        self.assertEqual(s["mug"]["location"], "kitchen")
        self.assertEqual(s["mug"]["temp_f"], 160)
        self.assertNotIn("mug", s["carried"])

    def test_take_mug_refused_when_hands_are_full(self):
        s = fresh(room="study")
        s, _ = rhythm.act(s, "take_book", {"book_id": "low-tide"})
        s, _ = rhythm.act(s, "take_book", {"book_id": "rain"})
        s, _ = rhythm.act(s, "move", {"to": "kitchen"})
        s, _ = rhythm.act(s, "brew_tea")  # mug stays in the kitchen
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "take_mug")
        # Freeing a hand lets the mug be taken, and taking it fills
        # the hand again.
        s, _ = rhythm.act(s, "put_book", {"book_id": "rain"})
        s, _ = rhythm.act(s, "take_mug")
        self.assertIn("mug", s["carried"])
        self.assertEqual(s["mug"]["location"], "carried")

    def test_put_mug_frees_a_hand_for_a_book(self):
        s = fresh(room="kitchen")
        s, _ = rhythm.act(s, "brew_tea")  # mug rides along
        s, _ = rhythm.act(s, "move", {"to": "study"})
        s, _ = rhythm.act(s, "take_book", {"book_id": "low-tide"})
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "take_book", {"book_id": "rain"})
        s, _ = rhythm.act(s, "put_mug")
        self.assertEqual(s["mug"]["location"], "desk")
        s, _ = rhythm.act(s, "take_book", {"book_id": "rain"})
        self.assertIn("book:rain", s["carried"])

    def test_take_mug_only_where_the_mug_is(self):
        s = _mug_at(1402, location="desk")
        s["room"] = "kitchen"
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "take_mug")

    def test_brew_refills_a_carried_mug_hot(self):
        s = _mug_at(1480)  # cold tea, in hand
        s["room"] = "kitchen"
        s, _ = rhythm.act(s, "brew_tea")
        self.assertEqual(rhythm.mug_temp(s), 160)
        self.assertEqual(rhythm.mug_warmth(s), "hot")
        self.assertIn("mug", s["carried"])


class TestScene(unittest.TestCase):
    def test_carried_mug_named_with_warmth(self):
        s = fresh(room="kitchen")
        s, _ = rhythm.act(s, "brew_tea")
        self.assertIn("mug of tea, still hot", rhythm.scene_text(s))
        s, _ = rhythm.act(s, "quiet", {"minutes": 20})
        s, _ = rhythm.act(s, "quiet", {"minutes": 20})
        s, _ = rhythm.act(s, "quiet", {"minutes": 20})
        s, _ = rhythm.act(s, "quiet", {"minutes": 20})
        self.assertIn("mug of tea, gone cold", rhythm.scene_text(s))

    def test_set_down_mug_named_where_it_sits(self):
        s = fresh(room="kitchen")
        s, _ = rhythm.act(s, "brew_tea")
        s, _ = rhythm.act(s, "move", {"to": "study"})
        s, _ = rhythm.act(s, "put_mug")
        scene = rhythm.scene_text(s)
        self.assertIn("A mug of tea sits on the desk, still warm", scene)
        # And it does not follow the resident to another room.
        s, _ = rhythm.act(s, "move", {"to": "kitchen"})
        self.assertNotIn("mug of tea sits", rhythm.scene_text(s))

    def test_no_mug_means_no_mug_in_the_scene(self):
        s = fresh(room="study")
        self.assertNotIn("mug of tea", rhythm.scene_text(s))


class TestNightProposer(unittest.TestCase):
    def test_night_never_takes_or_sips_the_mug(self):
        # Even with a mug in the room and one in hand, the night's
        # candidate list holds neither verb.
        p = Proposer(seed=7)
        in_room = _mug_at(1402, location="kitchen")
        in_room["room"] = "kitchen"
        ops = [op for op, _, _ in p.candidates(in_room, night=True)]
        self.assertNotIn("take_mug", ops)
        self.assertNotIn("sip", ops)
        in_hand = _mug_at(1402)
        ops = [op for op, _, _ in p.candidates(in_hand, night=True)]
        self.assertNotIn("take_mug", ops)
        self.assertNotIn("sip", ops)

    def test_visit_shortlist_does_offer_the_mug_verbs(self):
        # The same state, as a visit: the verbs are offered when
        # sensible — the exclusion is the night's, not the visit's.
        p = Proposer(seed=7)
        in_room = _mug_at(1402, location="kitchen")
        in_room["room"] = "kitchen"
        ops = [op for op, _, _ in p.candidates(in_room, night=False)]
        self.assertIn("take_mug", ops)
        in_hand = _mug_at(1402)
        ops = [op for op, _, _ in p.candidates(in_hand, night=False)]
        self.assertIn("sip", ops)

    def test_a_whole_night_drinks_no_tea(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=11, night_date="2026-11-21")
            home.do("move", {"to": "kitchen"})
            home.do("brew_tea")
            home.run(max_minutes=480)
            ops = [r["op"] for r in home.log.read_all()]
            self.assertNotIn("take_mug", ops)
            self.assertNotIn("sip", ops)


class TestDigestAndRollForward(unittest.TestCase):
    def test_digest_mentions_brewed_sipped_and_carried(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            night = "2026-11-21"
            log.append(tick=1, minute=1400, room="kitchen", op="brew_tea",
                       text="I brew tea and drink it slowly. The warmth "
                       "is welcome. I fill a mug to carry with me.",
                       provenance="simulated", night_date=night)
            log.append(tick=2, minute=1402, room="kitchen", op="sip",
                       text="I take a sip. The tea is still hot.",
                       provenance="simulated", night_date=night)
            log.append(tick=3, minute=1403, room="study", op="take_mug",
                       text="I take the mug carefully into my hands.",
                       provenance="simulated", night_date=night)
            digest = summarize_night(log.read_night(night))
            self.assertIn("brew tea", digest)
            self.assertIn("take a sip", digest)
            self.assertIn("take the mug", digest)

    def test_roll_forward_keeps_the_mug_but_not_its_heat(self):
        s = fresh(room="study")
        s, _ = rhythm.act(s, "move", {"to": "kitchen"})
        s, _ = rhythm.act(s, "brew_tea")  # mug carried, hot
        s, _ = rhythm.act(s, "move", {"to": "study"})
        fresh_night = rhythm.initial_state("2026-11-22")
        _carry_forward(fresh_night, s)
        mug = fresh_night["mug"]
        # A carried mug is set down where the resident stood, and a
        # whole day passed: yesterday's tea is honestly cold.
        self.assertEqual(mug["location"], "desk")
        self.assertEqual(mug["temp_f"], 68)
        self.assertEqual(rhythm.mug_warmth(fresh_night), "cold")
        rhythm._validate(fresh_night)

    def test_roll_forward_keeps_a_set_down_mug_where_it_was(self):
        s = fresh(room="kitchen")
        s, _ = rhythm.act(s, "brew_tea")
        s, _ = rhythm.act(s, "put_mug")  # left on the counter
        fresh_night = rhythm.initial_state("2026-11-22")
        _carry_forward(fresh_night, s)
        self.assertEqual(fresh_night["mug"]["location"], "kitchen")
        self.assertEqual(rhythm.mug_warmth(fresh_night), "cold")


class TestValidation(unittest.TestCase):
    def test_bad_mug_shapes_are_refused(self):
        bad = (
            {"location": "bathroom", "temp_f": 160, "brewed_minute": 1400},
            {"location": "carried", "temp_f": None, "brewed_minute": None},
            {"location": None, "temp_f": 160, "brewed_minute": 1400},
            {"location": "carried", "temp_f": 160, "brewed_minute": -1},
            {"location": "carried", "temp_f": 300, "brewed_minute": 1400},
            {"location": "desk", "temp_f": 160},  # missing a key
        )
        for mug in bad:
            with self.subTest(mug=mug):
                with self.assertRaises(rhythm.HomeConflict):
                    fresh(mug=mug, carried=["mug"])

    def test_mug_custody_must_agree_with_hands(self):
        # Carried says the mug is in hand, but the hands disagree.
        with self.assertRaises(rhythm.HomeConflict):
            fresh(mug={"location": "carried", "temp_f": 160,
                       "brewed_minute": 1400}, carried=[])
        with self.assertRaises(rhythm.HomeConflict):
            fresh(mug={"location": "desk", "temp_f": 160,
                       "brewed_minute": 1400}, carried=["mug"])


if __name__ == "__main__":
    unittest.main()
