"""Tests for the home simulation prototype (spec v0.2a).

unittest, stdlib only. All state is synthetic and lives in /tmp — never the
live mind store. The sim under test never touches mind.db by construction
(test_hard_boundary_files proves it at the source level).
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from home_sim import rhythm
from home_sim.events import EventLog, summarize_night
from home_sim.rooms import ROOMS
from home_sim.tick import Home, Proposer, in_off_hours
from home_sim.traversal import (
    assert_no_leakage,
    import_world_event,
    snapshot,
)

PACKAGE_DIR = Path(__file__).resolve().parent.parent


def fresh(**overrides):
    s = rhythm.initial_state("2026-10-19")
    s.update(overrides)
    rhythm._validate(s)
    return s


class TestNeedTickMath(unittest.TestCase):
    """Lab parity: hunger +1/180min, thirst +1/120min, fatigue +1/240min."""

    def _aligned(self, **kw):
        # Lab math ticks on absolute-minute multiples; align the clock to a
        # multiple of all three intervals so durations read exactly.
        s = fresh(minute=1440, **kw)
        return s

    def test_hunger_interval(self):
        s = self._aligned(hunger=2)
        rhythm._pass_time(s, 179)
        self.assertEqual(s["hunger"], 2)  # not yet
        rhythm._pass_time(s, 1)
        self.assertEqual(s["hunger"], 3)

    def test_thirst_interval(self):
        s = self._aligned(thirst=2)
        rhythm._pass_time(s, 119)
        self.assertEqual(s["thirst"], 2)
        rhythm._pass_time(s, 1)
        self.assertEqual(s["thirst"], 3)

    def test_fatigue_interval(self):
        s = self._aligned(fatigue=2)
        rhythm._pass_time(s, 239)
        self.assertEqual(s["fatigue"], 2)
        rhythm._pass_time(s, 1)
        self.assertEqual(s["fatigue"], 3)

    def test_caps_at_ten(self):
        s = fresh(hunger=10, thirst=10, fatigue=10)
        rhythm._pass_time(s, 1440)
        self.assertEqual((s["hunger"], s["thirst"], s["fatigue"]), (10, 10, 10))

    def test_time_only_moves_forward(self):
        s = fresh()
        with self.assertRaises(rhythm.HomeError):
            rhythm._pass_time(s, -5)


class TestSatisfaction(unittest.TestCase):
    """eat/tea/sleep/rest reduce needs by the lab's amounts."""

    def test_eat_reduces_hunger_by_5(self):
        s = fresh(room="kitchen", meal_ready=True, hunger=8)
        s2, text = rhythm.act(s, "eat")
        self.assertEqual(s2["hunger"], 3)
        self.assertFalse(s2["meal_ready"])
        self.assertIn("less hungry", text)
        self.assertEqual(s["hunger"], 8)  # input not mutated

    def test_brew_tea_reduces_thirst_by_4(self):
        s = fresh(room="kitchen", thirst=8)
        s2, _ = rhythm.act(s, "brew_tea")
        self.assertEqual(s2["thirst"], 4)

    def test_sleep_reduces_fatigue_by_8(self):
        # A 240-minute sleep always crosses exactly one fatigue tick (+1),
        # then the lab's -8: net -7. (Lab parity: pass time, then reduce.)
        s = fresh(room="rest", fatigue=9)
        s2, _ = rhythm.act(s, "sleep")
        self.assertEqual(s2["fatigue"], 2)

    def test_rest_reduces_fatigue_by_3(self):
        s = fresh(room="rest", fatigue=9, minute=1440)  # aligned: no tick
        s2, _ = rhythm.act(s, "rest")
        self.assertEqual(s2["fatigue"], 6)

    def test_needs_floor_at_zero(self):
        s = fresh(room="kitchen", meal_ready=True, hunger=2)
        s2, _ = rhythm.act(s, "eat")
        self.assertEqual(s2["hunger"], 0)


class TestNoOpenDrains(unittest.TestCase):
    """Fitness 4: every need has an agent-accessible satisfaction path under
    resource exhaustion. Drain provisions to zero; recovery must be possible
    through the inhabitant's own actions."""

    def test_empty_pantry_blocks_meal_but_not_recovery(self):
        s = fresh(room="kitchen", rations=0)
        with self.assertRaises(rhythm.HomeConflict):
            rhythm.act(s, "prepare_meal")

    def test_garden_recovery_from_zero_provisions(self):
        # The exhaustion scenario: nothing stored, nothing growing, hungry.
        s = fresh(room="garden", rations=0, produce=0, garden_growth=0,
                  hunger=8, thirst=7, fatigue=3, meal_ready=False)
        s, _ = rhythm.act(s, "tend_garden")   # needs no provisions
        self.assertEqual(s["garden_growth"], 1)
        s, _ = rhythm.act(s, "tend_garden")
        self.assertEqual(s["garden_growth"], 2)
        s, _ = rhythm.act(s, "harvest")
        self.assertEqual(s["produce"], 2)
        self.assertEqual(s["garden_growth"], 0)
        s, _ = rhythm.act(s, "move", {"to": "kitchen"})
        s, _ = rhythm.act(s, "store_produce")
        self.assertEqual(s["rations"], 2)
        self.assertEqual(s["produce"], 0)
        s, _ = rhythm.act(s, "prepare_meal")
        self.assertTrue(s["meal_ready"])
        self.assertEqual(s["rations"], 1)
        s, text = rhythm.act(s, "eat")
        # 8 -> 9 via one hunger tick across the 110-minute sequence, then -5.
        self.assertLessEqual(s["hunger"], 5)
        self.assertLess(s["hunger"], 8)
        self.assertIn("less hungry", text)

    def test_recovery_from_max_fatigue(self):
        # Exhausted at zero provisions: rest is always available, then tend.
        s = fresh(room="rest", rations=0, fatigue=10, garden_growth=0)
        s, _ = rhythm.act(s, "rest")
        self.assertEqual(s["fatigue"], 7)
        s, _ = rhythm.act(s, "move", {"to": "garden"})
        s, _ = rhythm.act(s, "tend_garden")  # gate is >= 9; 7 passes
        self.assertEqual(s["garden_growth"], 1)

    def test_tea_needs_no_provisions(self):
        s = fresh(room="kitchen", rations=0, produce=0, thirst=9)
        s, _ = rhythm.act(s, "brew_tea")  # fixtures, not supplies
        self.assertEqual(s["thirst"], 5)

    def test_lamp_can_always_be_relit(self):
        # Darkness must never become an unsatisfiable precondition: lamps
        # burn no fuel, so relighting is always available.
        s = fresh(room="kitchen",
                  lamps={"study": False, "kitchen": False,
                         "garden": False, "rest": False})
        s, _ = rhythm.act(s, "light_lamp")
        s, _ = rhythm.act(s, "prepare_meal")
        self.assertTrue(s["meal_ready"])

    def test_full_night_never_dead_ends(self):
        # A whole night from empty: the proposer must never raise.
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=7)
            home.state = fresh(rations=0, produce=0, garden_growth=0,
                               hunger=6, thirst=6, fatigue=5)
            home.save()
            for _ in range(60):
                home.step()  # raises if any proposed action is illegal
            rhythm._validate(home.state)
            self.assertLessEqual(home.state["hunger"], 10)


class TestSnapshot(unittest.TestCase):
    def test_shape(self):
        snap = snapshot(fresh())
        self.assertEqual(set(snap),
                         {"identity", "presence", "needs", "activity",
                          "provenance"})
        self.assertEqual(snap["identity"], "calibos")
        self.assertEqual(snap["provenance"], "simulated")
        self.assertEqual(set(snap["needs"]), {"hunger", "thirst", "fatigue"})

    def test_coarse_bands(self):
        self.assertEqual(snapshot(fresh(hunger=2))["needs"]["hunger"],
                         "comfortable")
        self.assertEqual(snapshot(fresh(hunger=5))["needs"]["hunger"],
                         "peckish")
        self.assertEqual(snapshot(fresh(hunger=9))["needs"]["hunger"], "hungry")
        self.assertEqual(snapshot(fresh(thirst=9))["needs"]["thirst"],
                         "parched")
        self.assertEqual(snapshot(fresh(fatigue=9))["needs"]["fatigue"],
                         "worn-out")

    def test_no_leakage_across_need_space(self):
        for h in range(11):
            for t in range(11):
                for f in range(11):
                    snap = snapshot(fresh(hunger=h, thirst=t, fatigue=f))
                    assert_no_leakage(snap)
                    # JSON-serializable and still clean after the round trip
                    assert_no_leakage(json.loads(json.dumps(snap)))

    def test_away_snapshot(self):
        snap = snapshot(fresh(), away=True)
        self.assertEqual(snap["presence"], "away")
        self.assertEqual(snap["activity"], "out in the day")
        assert_no_leakage(snap)

    def test_snapshot_is_pure(self):
        s = fresh()
        before = copy.deepcopy(s)
        snapshot(s)
        self.assertEqual(s, before)


class TestWorldEventImport(unittest.TestCase):
    def test_import_touches_nothing_but_bias_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            s = fresh(room="study", hunger=5, rations=3)
            s2 = import_world_event(
                s, log,
                {"kind": "visitor",
                 "text": "A neighbor stopped by the garden.",
                 "room": "garden"})
            for k in ("hunger", "thirst", "fatigue", "room", "rations",
                      "produce", "garden_growth", "meal_ready", "lamps",
                      "clutter", "minute", "tick"):
                self.assertEqual(s2[k], s[k], f"import mutated {k}")
            self.assertEqual(s, fresh(room="study", hunger=5, rations=3))
            self.assertEqual(s2["world_bias"],
                             {"room": "garden", "ticks_left": 6})
            last = log.read_all()[-1]
            self.assertEqual(last["provenance"], "world")
            self.assertIn("neighbor", last["text"])

    def test_bias_weights_never_dictates(self):
        s = fresh(room="study")
        biased = dict(s)
        biased["world_bias"] = {"room": "garden", "ticks_left": 6}
        # Same seed -> identical stochastic draw; the ratio is exact.
        w_plain = Proposer(seed=1)._weight(s, "move", {"to": "garden"})
        w_biased = Proposer(seed=1)._weight(biased, "move", {"to": "garden"})
        self.assertAlmostEqual(w_biased / w_plain, 3.0, places=9)
        # ...but the room itself is untouched: weighting, not teleportation.
        self.assertEqual(biased["room"], "study")

    def test_bias_decays_with_lived_time(self):
        s = fresh(world_bias={"room": "garden", "ticks_left": 2})
        s, _ = rhythm.act(s, "look")
        self.assertEqual(s["world_bias"]["ticks_left"], 1)
        s, _ = rhythm.act(s, "look")
        self.assertIsNone(s["world_bias"])

    def test_bad_room_hint_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                import_world_event(fresh(), EventLog(tmp),
                                   {"kind": "x", "room": "dungeon"})


class TestRoundTripFidelity(unittest.TestCase):
    """Fitness 5: 100 sim->snapshot->event->sim round-trips, zero corruption,
    zero leakage. Plus determinism: the same run twice is bit-identical."""

    def _run_trips(self, tmp, seed, n=100):
        home = Home(tmp, seed=seed)
        for i in range(n):
            snap = snapshot(home.state)
            assert_no_leakage(snap)
            before = copy.deepcopy(home.state)
            home.state = import_world_event(
                home.state, home.log,
                {"kind": "bell",
                 "text": f"The village bell rang ({i}).",
                 "room": ROOMS[i % len(ROOMS)]})
            for k in ("hunger", "thirst", "fatigue", "room", "rations",
                      "produce", "garden_growth", "meal_ready", "lamps",
                      "clutter", "minute", "tick"):
                self.assertEqual(home.state[k], before[k])
            home.step()
            rhythm._validate(home.state)
        return home

    def test_hundred_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = self._run_trips(tmp, seed=1234)
            # The night actually lived: time passed, needs moved, log grew.
            self.assertGreater(home.state["minute"], 23 * 60)
            self.assertGreater(len(home.log.read_all()), 100)

    def test_deterministic_replay(self):
        with tempfile.TemporaryDirectory() as t1, \
                tempfile.TemporaryDirectory() as t2:
            h1 = self._run_trips(t1, seed=99, n=50)
            h2 = self._run_trips(t2, seed=99, n=50)
            self.assertEqual(h1.state, h2.state)
            texts1 = [r["text"] for r in h1.log.read_all()]
            texts2 = [r["text"] for r in h2.log.read_all()]
            self.assertEqual(texts1, texts2)


class TestPutzProposer(unittest.TestCase):
    """Jazz, not composition: no hardcoded room sequences. Unprompted
    visitation must emerge from weights + noise."""

    def _night_rooms(self, seed, steps=48):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=seed)
            rooms = []
            for _ in range(steps):
                rooms.append(home.step()["room"])
            return rooms

    def test_visits_rooms_without_a_script(self):
        seqs = [tuple(self._night_rooms(seed)) for seed in range(10)]
        for seq in seqs:
            self.assertGreaterEqual(len(set(seq)), 3,
                                   "a night should wander, not sit still")
        self.assertGreater(len(set(seqs)), 1,
                           "every seed played the same night — composed?")

    def test_proposed_actions_are_always_legal(self):
        # 200 steps across seeds: the _try filter and act() gates must agree.
        for seed in range(4):
            with tempfile.TemporaryDirectory() as tmp:
                home = Home(tmp, seed=seed)
                for _ in range(50):
                    home.step()  # raises on any illegal proposal

    def test_needs_shape_behavior(self):
        # Starving in the kitchen with a meal ready: eat dominates the menu.
        p = Proposer(seed=5)
        s = fresh(room="kitchen", meal_ready=True, hunger=9)
        scored = p.candidates(s)
        top = max(scored, key=lambda c: c[2])[0]
        self.assertEqual(top, "eat")


class TestEventsAndDigest(unittest.TestCase):
    def test_log_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            rec = log.append(tick=1, minute=1400, room="kitchen", op="eat",
                             text="I eat.", provenance="simulated")
            self.assertEqual(rec["provenance"], "simulated")
            self.assertEqual(rec["clock"], "23:20")
            self.assertEqual(len(log.read_all()), 1)

    def test_bad_provenance_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                EventLog(tmp).append(tick=0, minute=0, room="rest", op="x",
                                     text="x", provenance="real")

    def test_digest_marks_simulated(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            log.append(tick=1, minute=1400, room="kitchen", op="eat",
                       text="I eat the meal I prepared.",
                       provenance="simulated")
            d = summarize_night(log.read_all())
            self.assertIn("simulated", d)
            self.assertIn("eat x1", d)

    def test_empty_digest(self):
        self.assertIn("simulated", summarize_night([]))


class TestOffHours(unittest.TestCase):
    def test_window(self):
        chi = ZoneInfo("America/Chicago")
        self.assertTrue(in_off_hours(datetime(2026, 10, 19, 23, 30,
                                              tzinfo=chi)))
        self.assertTrue(in_off_hours(datetime(2026, 10, 20, 3, 0,
                                              tzinfo=chi)))
        self.assertTrue(in_off_hours(datetime(2026, 10, 20, 6, 59,
                                              tzinfo=chi)))
        self.assertFalse(in_off_hours(datetime(2026, 10, 20, 7, 0,
                                               tzinfo=chi)))
        self.assertFalse(in_off_hours(datetime(2026, 10, 19, 12, 0,
                                               tzinfo=chi)))


class TestHardBoundary(unittest.TestCase):
    """The sim is standalone furniture. Prove it at the source level:
    no module may reference the mind's private stores or import the mind
    package, and importing this package must not pull the mind package
    into sys.modules."""

    FORBIDDEN = ("calibos_mind", "mind.db", "salience.json", "ablation_",
                 "dreams/", "inbox/")

    def test_no_forbidden_references_in_source(self):
        # This test module must name the tokens to forbid them, so it is
        # excluded from the substring scan — but it IS covered by the
        # import-statement scan below, which no file escapes.
        offenders = []
        for path in sorted(PACKAGE_DIR.rglob("*.py")):
            if "__pycache__" in str(path) or path.name == "test_home_sim.py":
                continue
            text = path.read_text(encoding="utf-8")
            hits = [f for f in self.FORBIDDEN if f in text]
            if hits:
                offenders.append(f"{path.name}: {hits}")
        self.assertEqual(offenders, [])

    def test_no_mind_import_statements(self):
        import ast
        offenders = []
        for path in sorted(PACKAGE_DIR.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    mods = [node.module or ""]
                else:
                    continue
                if any(m == "calibos_mind" or m.startswith("calibos_mind.")
                       for m in mods):
                    offenders.append(str(path))
        self.assertEqual(offenders, [])

    def test_import_pulls_no_mind_package(self):
        self.assertTrue(all(not m.startswith("calibos_mind")
                            for m in sys.modules),
                        "home_sim pulled calibos_mind into sys.modules")


class TestStatePersistence(unittest.TestCase):
    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=3)
            for _ in range(10):
                home.step()
            home2 = Home(tmp, seed=999)  # seed must not matter on load
            self.assertEqual(home.state, home2.state)


class TestNightRollover(unittest.TestCase):
    """A finished night never replays as daytime: loading a complete night
    rolls forward to a fresh 23:00 start with the next night_date."""

    def test_second_night_starts_at_nightfall(self):
        with tempfile.TemporaryDirectory() as tmp:
            h1 = Home(tmp, seed=1, night_date="2026-10-19")
            h1.run(max_minutes=480)
            self.assertGreaterEqual(h1.state["minute"] - 23 * 60, 480)
            h2 = Home(tmp, seed=2)
            self.assertEqual(h2.state["minute"], 23 * 60)
            self.assertEqual(h2.state["night_date"], "2026-10-20")
            self.assertFalse(rhythm._daylight(h2.state["minute"]))
            self.assertEqual(h2.state["hunger"], 2)  # the day was lived away

    def test_partial_night_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            h1 = Home(tmp, seed=1, night_date="2026-10-19")
            h1.run(max_minutes=60)
            mid = h1.state["minute"]
            self.assertLess(mid - 23 * 60, 480)
            h2 = Home(tmp, seed=2)
            self.assertEqual(h2.state["minute"], mid)
            self.assertEqual(h2.state["night_date"], "2026-10-19")


class TestDigestPerNight(unittest.TestCase):
    """The morning digest covers the night's events, not the whole log."""

    def test_digest_covers_only_current_night(self):
        import re
        with tempfile.TemporaryDirectory() as tmp:
            h1 = Home(tmp, seed=1, night_date="2026-10-19")
            h1.run(max_minutes=480)
            n1 = len(h1.log.read_night("2026-10-19"))
            self.assertGreater(n1, 0)
            h2 = Home(tmp, seed=2)  # rolls to 2026-10-20
            h2.run(max_minutes=480)
            n2 = len(h2.log.read_night("2026-10-20"))
            self.assertGreater(n2, 0)
            d = h2.digest()
            did = next(l for l in d.splitlines()
                       if l.strip().startswith("did:"))
            total = sum(int(n) for n in re.findall(r"x(\d+)", did))
            self.assertEqual(total, n2)

    def test_digest_after_rollforward_shows_completed_night(self):
        """A morning read after the state rolled forward must digest the
        completed night, not the fresh (event-less) one. Regression test:
        --digest-only printed the empty message every morning."""
        import re
        with tempfile.TemporaryDirectory() as tmp:
            h1 = Home(tmp, seed=1, night_date="2026-10-19")
            h1.run(max_minutes=480)
            n1 = len(h1.log.read_night("2026-10-19"))
            self.assertGreater(n1, 0)
            h2 = Home(tmp, seed=2)  # rolls to 2026-10-20, no run yet
            self.assertEqual(h2.state["night_date"], "2026-10-20")
            d = h2.digest()
            self.assertNotIn("no off-hours activity", d)
            did = next(l for l in d.splitlines()
                       if l.strip().startswith("did:"))
            total = sum(int(n) for n in re.findall(r"x(\d+)", did))
            self.assertEqual(total, n1)


class TestValidateStrict(unittest.TestCase):
    """_validate is the gate against hand-edited state: last_op and
    night_date are validated, so downstream code never sees bad types."""

    def test_rejects_non_string_last_op(self):
        for bad in ({"not": "a string"}, ["not", "a", "string"], 42):
            s = fresh()
            s["last_op"] = bad
            with self.assertRaises(rhythm.HomeConflict):
                rhythm._validate(s)

    def test_rejects_bad_night_date(self):
        for bad in ("yesterday", "2026-13-99", 20261019, None):
            s = fresh()
            s["night_date"] = bad
            with self.assertRaises(rhythm.HomeConflict):
                rhythm._validate(s)

    def test_snapshot_safe_on_any_validated_state(self):
        s = fresh(last_op="eat")
        rhythm._validate(s)
        snapshot(s)  # must never TypeError past the gate


if __name__ == "__main__":
    unittest.main()
