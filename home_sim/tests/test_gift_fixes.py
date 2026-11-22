"""Tests for the gift fixes (rules version 5).

Stdlib unittest only. All state is synthetic and lives in /tmp — never
the live home. Covers: world-event notices shown in the scene where
they were left, the morning digest quoting world events, and the
"books" override key that puts real file-backed books on the study
shelf. The real gifted texts (Frankenstein 1818, Pinocchio) are read
from the package's library/ — local files, no network in tests.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from home_sim import library, rhythm
from home_sim.events import EventLog, summarize_night
from home_sim.overrides import apply_books, apply_injections, load_overrides
from home_sim.tick import Home
from home_sim.traversal import import_world_event
from home_sim.visit import run_visit

PACKAGE_DIR = Path(__file__).resolve().parent.parent


def fresh(**overrides):
    s = rhythm.initial_state("2026-11-21")
    s.update(overrides)
    rhythm._validate(s)
    return s


def _books_folder(tmp, entries, name="books.json"):
    folder = Path(tmp) / "overrides"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps({"books": entries}),
                               encoding="utf-8")
    return folder


FRANKENSTEIN = {
    "id": "frankenstein-1818",
    "title": "Frankenstein; or, The Modern Prometheus",
    "author": "Mary Shelley",
    "file": "library/frankenstein-1818.txt",
    "story_start": 697,
}

PINOCCHIO = {
    "id": "pinocchio",
    "title": "The Adventures of Pinocchio",
    "author": "Carlo Collodi",
    "file": "library/pinocchio.txt",
    "story_start": 40,
}


class TestWorldNotices(unittest.TestCase):
    def test_notice_stored_and_shown_in_its_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            s = fresh(room="study")
            s2 = import_world_event(
                s, log, {"kind": "gift",
                         "text": "A small parcel waits on the desk.",
                         "room": "study"})
            self.assertEqual(len(s2["world_notices"]), 1)
            notice = s2["world_notices"][0]
            self.assertEqual(notice["room"], "study")
            self.assertEqual(notice["night_date"], "2026-11-21")
            scene = rhythm.scene_text(s2)
            self.assertIn("A small parcel waits on the desk.", scene)

    def test_notice_not_shown_in_another_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            s = fresh(room="study")
            s2 = import_world_event(
                s, log, {"kind": "gift",
                         "text": "A small parcel waits on the desk.",
                         "room": "study"})
            s2["room"] = "kitchen"
            scene = rhythm.scene_text(s2)
            self.assertNotIn("A small parcel waits on the desk.", scene)

    def test_notice_not_shown_on_a_prior_night(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            s = fresh(room="study")
            s2 = import_world_event(
                s, log, {"kind": "gift",
                         "text": "A small parcel waits on the desk.",
                         "room": "study"})
            s2["night_date"] = "2026-11-22"
            scene = rhythm.scene_text(s2)
            self.assertNotIn("A small parcel waits on the desk.", scene)

    def test_roomless_notice_shows_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            s = fresh(room="study")
            s2 = import_world_event(
                s, log, {"kind": "notice",
                         "text": "The village bell rings, far off.",
                         "room": None})
            self.assertIn("The village bell rings, far off.",
                          rhythm.scene_text(s2))
            s2["room"] = "garden"
            self.assertIn("The village bell rings, far off.",
                          rhythm.scene_text(s2))

    def test_notices_cap_at_five_most_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            s = fresh(room="study")
            for i in range(6):
                s = import_world_event(
                    s, log, {"kind": "gift", "text": f"Gift {i}.",
                             "room": "study"})
            self.assertEqual(len(s["world_notices"]), 5)
            texts = [n["text"] for n in s["world_notices"]]
            self.assertNotIn("Gift 0.", texts)
            self.assertIn("Gift 5.", texts)

    def test_bias_still_works_alongside_notices(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            s = fresh(room="rest")
            s2 = import_world_event(
                s, log, {"kind": "gift",
                         "text": "Something waits in the garden.",
                         "room": "garden"})
            self.assertEqual(s2["world_bias"],
                             {"room": "garden", "ticks_left": 6})
            self.assertEqual(len(log.read_all()), 1)
            self.assertEqual(log.read_all()[0]["provenance"], "world")

    def test_digest_quotes_world_events(self):
        records = [
            {"provenance": "simulated", "night_date": "2026-11-21",
             "op": "read", "text": "I read for a while."},
            {"provenance": "world", "night_date": "2026-11-21",
             "op": "world:gift",
             "text": "Two books rest on the study desk."},
        ]
        digest = summarize_night(records)
        self.assertIn("(from the world: Two books rest on the study desk.)",
                      digest)


class TestFileBackedBooks(unittest.TestCase):
    def tearDown(self):
        # Test-only registrations must not leak into other tests.
        for book_id in ("pinocchio-moment-test", "ghost-book",
                        "missing-book"):
            library.EXTRA_BOOKS.pop(book_id, None)

    def test_books_key_registers_a_file_backed_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [PINOCCHIO])
            overrides = load_overrides(folder)
            self.assertEqual(len(overrides.get("books", [])), 1)
            s = fresh(room="study")
            s2 = apply_books(s, overrides)
            self.assertIn("pinocchio", s2["books"])
            entry = s2["books"]["pinocchio"]
            self.assertEqual(entry["position"], 0)
            self.assertIsNone(entry["bookmark"])
            self.assertEqual(entry["location"], "study")
            self.assertEqual(entry["file"], "library/pinocchio.txt")
            self.assertEqual(entry["title"], "The Adventures of Pinocchio")
            self.assertIn("books.json", s2["applied_overrides"])

    def test_registration_applies_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [PINOCCHIO, FRANKENSTEIN])
            overrides = load_overrides(folder)
            s = fresh(room="study")
            s2 = apply_books(s, overrides)
            s3 = apply_books(s2, overrides)
            self.assertEqual(s3["books"]["pinocchio"]["position"], 0)
            self.assertEqual(s3["applied_overrides"].count("books.json"), 1)
            self.assertEqual(len(s3["books"]), 5)

    def test_read_returns_real_words_from_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [PINOCCHIO])
            overrides = load_overrides(folder)
            s = fresh(room="study")
            s = apply_books(s, overrides)
            s, _ = rhythm.act(s, "take_book", {"book_id": "pinocchio"})
            s, text = rhythm.act(s, "read")
            self.assertIn("Centuries ago there lived", text)
            self.assertIn("250 words", text)
            self.assertEqual(s["books"]["pinocchio"]["position"], 250)

    def test_frankenstein_read_quotes_the_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [FRANKENSTEIN])
            overrides = load_overrides(folder)
            s = fresh(room="study")
            s = apply_books(s, overrides)
            s, _ = rhythm.act(s, "take_book",
                              {"book_id": "frankenstein-1818"})
            s, text = rhythm.act(s, "read")
            self.assertIn("You will rejoice", text)
            self.assertEqual(
                s["books"]["frankenstein-1818"]["position"], 250)

    def test_bookmark_works_on_a_file_backed_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [PINOCCHIO])
            overrides = load_overrides(folder)
            s = fresh(room="study")
            s = apply_books(s, overrides)
            s, _ = rhythm.act(s, "take_book", {"book_id": "pinocchio"})
            s, text = rhythm.act(s, "bookmark", {"name": "shark"})
            self.assertIn("shark", text)
            self.assertEqual(s["books"]["pinocchio"]["bookmark"], "shark")

    def test_take_and_put_work_for_a_file_backed_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [PINOCCHIO])
            overrides = load_overrides(folder)
            s = fresh(room="study")
            s = apply_books(s, overrides)
            s, _ = rhythm.act(s, "take_book", {"book_id": "pinocchio"})
            self.assertIn("book:pinocchio", s["carried"])
            s, _ = rhythm.act(s, "move", {"to": "garden"})
            s, _ = rhythm.act(s, "put_book", {"book_id": "pinocchio"})
            self.assertEqual(s["books"]["pinocchio"]["location"], "garden")
            self.assertNotIn("book:pinocchio", s["carried"])

    def test_desk_line_lists_gifted_books_in_the_study(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [FRANKENSTEIN, PINOCCHIO])
            overrides = load_overrides(folder)
            s = fresh(room="study")
            s = apply_books(s, overrides)
            scene = rhythm.scene_text(s)
            self.assertIn(
                "On the desk: Frankenstein; or, The Modern Prometheus, "
                "The Adventures of Pinocchio.", scene)
            # Carrying one takes it off the desk line.
            s, _ = rhythm.act(s, "take_book", {"book_id": "pinocchio"})
            scene = rhythm.scene_text(s)
            self.assertIn("On the desk: Frankenstein; or, "
                          "The Modern Prometheus.", scene)
            self.assertNotIn(
                "On the desk: Frankenstein; or, The Modern Prometheus, "
                "The Adventures of Pinocchio.", scene)

    def test_moment_fires_inside_the_read_window(self):
        entry = dict(PINOCCHIO)
        entry["id"] = "pinocchio-moment-test"
        entry["moments"] = [{"at_word": 50,
                             "text": "TEST MOMENT: salt water."}]
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [entry])
            overrides = load_overrides(folder)
            s = fresh(room="study")
            s = apply_books(s, overrides)
            s, _ = rhythm.act(s, "take_book",
                              {"book_id": "pinocchio-moment-test"})
            s, text = rhythm.act(s, "read")
            self.assertIn("TEST MOMENT: salt water.", text)

    def test_missing_file_fails_safe_with_an_honest_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [{
                "id": "missing-book",
                "title": "A Book That Is Not There",
                "file": "library/no-such-book.txt"}])
            overrides = load_overrides(folder)
            s = fresh(room="study")
            s = apply_books(s, overrides)
            self.assertIn("missing-book", s["books"])
            s, _ = rhythm.act(s, "take_book", {"book_id": "missing-book"})
            with self.assertRaises(rhythm.HomeError) as caught:
                rhythm.act(s, "read")
            self.assertIn("the book is not here", str(caught.exception))

    def test_state_books_reregister_without_the_override_file(self):
        # A state's own entries re-register its books, so deleting the
        # JSON does not strand the house.
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [PINOCCHIO])
            overrides = load_overrides(folder)
            s = fresh(room="study")
            s = apply_books(s, overrides)
            library.EXTRA_BOOKS.pop("pinocchio", None)
            self.assertNotIn("pinocchio", library.EXTRA_BOOKS)
            from home_sim.overrides import register_state_books
            register_state_books(s)
            self.assertIn("pinocchio", library.EXTRA_BOOKS)
            rhythm._validate(s)  # must not raise

    def test_malformed_books_entries_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [
                {"id": "Bad ID!", "title": "Nope",
                 "file": "library/pinocchio.txt"},
                {"title": "No id at all",
                 "file": "library/pinocchio.txt"},
                {"id": "no-title", "file": "library/pinocchio.txt"},
                {"id": "bad-moments", "title": "Bad moments",
                 "file": "library/pinocchio.txt",
                 "moments": [{"at_word": "soon", "text": "Nope."}]},
                {"id": "bad-start", "title": "Bad start",
                 "file": "library/pinocchio.txt", "story_start": -1},
                "not even a dict",
                PINOCCHIO,
            ])
            overrides = load_overrides(folder)
            books = overrides.get("books", [])
            self.assertEqual([b["id"] for b in books], ["pinocchio"])

    def test_path_traversal_in_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = _books_folder(tmp, [
                {"id": "escape-one", "title": "Escape",
                 "file": "../secret.txt"},
                {"id": "escape-two", "title": "Escape",
                 "file": "library/../../etc/passwd"},
                {"id": "absolute", "title": "Escape",
                 "file": "/etc/passwd"},
                {"id": "empty", "title": "Escape", "file": ""},
            ])
            overrides = load_overrides(folder)
            self.assertNotIn("books", overrides)

    def test_roll_forward_drops_notices_but_keeps_the_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = EventLog(tmp)
            s = fresh(room="study")
            s = import_world_event(
                s, log, {"kind": "gift",
                         "text": "A small parcel waits on the desk.",
                         "room": "study"})
            folder = _books_folder(tmp, [PINOCCHIO])
            overrides = load_overrides(folder)
            s = apply_books(s, overrides)
            self.assertEqual(len(s["world_notices"]), 1)
            self.assertIn("pinocchio", s["books"])
            # Simulate the morning: a fresh night carries the books,
            # not the notices; the log keeps the gift.
            from home_sim.tick import _carry_forward
            fresh_night = rhythm.initial_state("2026-11-22")
            _carry_forward(fresh_night, s)
            self.assertEqual(fresh_night["world_notices"], [])
            self.assertIn("pinocchio", fresh_night["books"])
            # And the new night's study shows no old gift.
            fresh_night["room"] = "study"
            self.assertNotIn("A small parcel waits on the desk.",
                             rhythm.scene_text(fresh_night))


class TestGiftEndToEnd(unittest.TestCase):
    def test_visit_shows_the_notice_and_reads_frankenstein(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Home(tmp, seed=1, night_date="2026-11-21")
            out = io.StringIO()
            run_visit(home, ["move study", "look",
                             "take_book frankenstein-1818", "read",
                             "leave"], out=out)
            text = out.getvalue()
            # The gift's notice is in the study scene.
            self.assertIn("Two books rest side by side", text)
            # The desk line names both gifted books.
            self.assertIn("On the desk: Frankenstein; or, "
                          "The Modern Prometheus, "
                          "The Adventures of Pinocchio.", text)
            # And Frankenstein reads real words.
            self.assertIn("You will rejoice", text)
            self.assertEqual(
                home.state["books"]["frankenstein-1818"]["position"], 250)
            self.assertIn("books-frankenstein-pinocchio.json",
                          home.state["applied_overrides"])


if __name__ == "__main__":
    unittest.main()
