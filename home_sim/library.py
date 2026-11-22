"""Small library of source books for real reading.

Plain-text books live in the package's ``library/`` directory. A read
advances a word position through the actual text, so "I read 250 words"
means 250 words of the book, not a summary of them. Stdlib only.

File-backed books: a ghost-visitor override may register further books
(see overrides.py "books" key). A registered book is a plain-text file
under the house package (e.g. "library/frankenstein-1818.txt"), with a
title and optional author, blurb, reading moments, and an explicit
story-start word offset. Registered books read exactly like the
embedded three: same word positions, same bookmarks, same take/put.
A registered book whose file is missing at read time is an honest
error ("the book is not here"), never a crash — the file may have been
moved after the gift arrived.
"""
from __future__ import annotations

import re
from pathlib import Path

LIBRARY_DIR = Path(__file__).resolve().parent / "library"
PACKAGE_DIR = Path(__file__).resolve().parent

BOOKS = {
    "low-tide": {
        "title": "The House at Low Tide",
        "file": "the_house_at_low_tide.txt",
    },
    "rain": {
        "title": "The Practical Book of Rain",
        "file": "the_practical_book_of_rain.txt",
    },
    "window": {
        "title": "The Night Window",
        "file": "the_night_window.txt",
    },
}

# Books registered from override files. Keyed by id, in registration
# order; a registered book's file is relative to the house package.
EXTRA_BOOKS: dict[str, dict] = {}

_BOOK_ID_RE = re.compile(r"[a-z0-9-]+")


def register_book(book_id: str, title: str, file: str, *,
                  author: str | None = None, blurb: str | None = None,
                  moments: list[dict] | None = None,
                  story_start: int | None = None) -> None:
    """Register a file-backed book. Fail safe: bad input raises
    ValueError and registers nothing.

    `file` must be a relative path under the house package — no
    absolute paths, no ".." escapes. The file itself is not read here,
    so a gift may register a book whose text arrives with it; a
    missing file is reported honestly at read time, not at load.
    """
    if not isinstance(book_id, str) or not _BOOK_ID_RE.fullmatch(book_id):
        raise ValueError(f"bad book id: {book_id!r}")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("a book needs a non-empty title")
    if not isinstance(file, str) or not _safe_relpath(file):
        raise ValueError(f"bad book file: {file!r}")
    if author is not None and not isinstance(author, str):
        raise ValueError("author must be a string")
    if blurb is not None and not isinstance(blurb, str):
        raise ValueError("blurb must be a string")
    clean_moments: list[dict] = []
    for moment in moments or []:
        if (not isinstance(moment, dict)
                or type(moment.get("at_word")) is not int
                or moment["at_word"] < 0
                or not isinstance(moment.get("text"), str)):
            raise ValueError(f"bad reading moment: {moment!r}")
        clean_moments.append({"at_word": moment["at_word"],
                              "text": moment["text"]})
    if story_start is not None and (type(story_start) is not int
                                    or story_start < 0):
        raise ValueError(f"bad story start: {story_start!r}")
    EXTRA_BOOKS[book_id] = {
        "title": title,
        "file": file,
        "path": PACKAGE_DIR / file,
        "author": author,
        "blurb": blurb,
        "moments": clean_moments,
        "story_start": story_start,
    }


def _safe_relpath(file: str) -> bool:
    """A relative path that stays inside the house package."""
    if not file:
        return False
    path = Path(file)
    if path.is_absolute():
        return False
    return ".." not in path.parts


def all_books() -> dict:
    """Every known book: the embedded three, then registered ones."""
    return {**BOOKS, **EXTRA_BOOKS}


def is_file_backed(book_id: str) -> bool:
    return book_id in EXTRA_BOOKS


def registered_books() -> dict:
    """The file-backed books, in registration order."""
    return dict(EXTRA_BOOKS)


def _entry(book_id: str) -> dict:
    if book_id in BOOKS:
        return BOOKS[book_id]
    if book_id in EXTRA_BOOKS:
        return EXTRA_BOOKS[book_id]
    raise KeyError(f"unknown book: {book_id!r}")


def _text(book_id: str) -> str:
    """The book's raw text. Raises FileNotFoundError when a registered
    book's file is not where the gift said it would be."""
    entry = _entry(book_id)
    if book_id in BOOKS:
        return (LIBRARY_DIR / entry["file"]).read_text(encoding="utf-8")
    return entry["path"].read_text(encoding="utf-8")


_cache: dict[tuple[str, str], list[str]] = {}


def words(book_id: str) -> list[str]:
    """The book's words, in order. Cached; the files are small.

    The cache is keyed by book and file, so re-registering an id with
    a different text in the same process never serves stale words."""
    entry = _entry(book_id)
    path = (str(LIBRARY_DIR / entry["file"]) if book_id in BOOKS
            else str(entry["path"]))
    key = (book_id, path)
    if key not in _cache:
        _cache[key] = _text(book_id).split()
    return _cache[key]


def total_words(book_id: str) -> int:
    return len(words(book_id))


def story_start(book_id: str) -> int:
    """Word index where the story itself begins — past the title block.

    A registered book may carry an explicit story start (a word offset
    computed from the text when the gift was registered); otherwise the
    same heuristic as the embedded books: skip lines up to the first
    blank line (the title block), then skip any further blank or short
    lines (section headings) until a line with at least 12 words — the
    story opening. Word positions stay honest (words 0..N of the file);
    only the displayed quote window starts here, so a first read quotes
    the story, not the metadata.
    """
    entry = _entry(book_id)
    registered = entry.get("story_start")
    if registered is not None:
        return registered
    text = _text(book_id)
    lines = text.split("\n")
    index = 0
    # Phase 1: the title block, up to the first blank line.
    while index < len(lines) and lines[index].strip():
        index += 1
    # Phase 2: blank lines and short section headings.
    while index < len(lines):
        count = len(lines[index].split())
        if count >= 12:
            break
        index += 1
    return sum(len(line.split()) for line in lines[:index])


def title(book_id: str) -> str:
    return _entry(book_id)["title"]


def passage(book_id: str, start: int, count: int) -> str:
    """The next `count` words from `start`, as running text."""
    all_words = words(book_id)
    return " ".join(all_words[start:start + count])


def moments_in(book_id: str, start: int, end: int) -> list[str]:
    """Reading moments whose word offset falls inside [start, end):
    brief sensory occurrences tied to a place in the book, in order.
    Embedded books have none."""
    entry = _entry(book_id)
    return [m["text"] for m in entry.get("moments", [])
            if start <= m["at_word"] < end]


def initial_book_state() -> dict:
    """Fresh per-book reading state: word position, bookmark, location."""
    return {
        book_id: {"position": 0, "bookmark": None, "location": "study"}
        for book_id in BOOKS
    }


def book_state_entry(book_id: str) -> dict:
    """Fresh reading state for one registered book: the shared shape
    (position, bookmark, location) plus the file and title the gift
    carried, so the state describes its own books and a fresh process
    can re-register them from the state alone."""
    entry = _entry(book_id)
    state_entry: dict = {
        "position": 0,
        "bookmark": None,
        "location": "study",
        "file": entry["file"],
        "title": entry["title"],
    }
    if entry.get("author") is not None:
        state_entry["author"] = entry["author"]
    if entry.get("blurb") is not None:
        state_entry["blurb"] = entry["blurb"]
    if entry.get("moments"):
        state_entry["moments"] = list(entry["moments"])
    if entry.get("story_start") is not None:
        state_entry["story_start"] = entry["story_start"]
    return state_entry
