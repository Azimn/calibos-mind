"""Overrides: the ghost-visitor folder.

Anything placed in ``overrides/`` takes precedence over the core house.
The loader reads only ``*.json`` files there and merges them into one
overrides dict:

    {"descriptions": {"study": "A sentence that replaces the core one.",
                      ...},
     "events": [{"kind": "visitor", "text": "...", "room": "garden"}, ...]}

Description overrides replace a room's core description sentence wherever
a scene is composed. Event injections are recorded once each (tracked in
state under ``applied_overrides``) as world-provenance events that bias
the proposer toward their room for a few ticks — a gift left, never a
command. A ``weather`` entry replaces the live sky for a stated number
of nights, then expires and the live sky returns:

    {"weather": {"condition": "rain", "temp_f": 45, "nights": 3}}

The condition must be one of the house's vocabulary (clear, cloudy,
rain, snow, storm, fog, wind); anything else is ignored, fail safe.
When the folder holds only the readme, the loader returns an empty
dict and nothing changes. No override content ships with the house.

A ``books`` entry registers file-backed books, so a gifted book is a
real book the resident can take, read, and bookmark — not only a
description of one:

    {"books": [{"id": "frankenstein-1818",
                "title": "Frankenstein; or, The Modern Prometheus",
                "author": "Mary Shelley",
                "file": "library/frankenstein-1818.txt"}]}

The text file travels with the JSON through the same blind pipeline
and must resolve inside the house package (a relative path, no ".."
escapes, no absolute paths). The id is a slug ([a-z0-9-]+). Optional
"blurb", "moments" ([{"at_word": N, "text": "..."}] — brief sensory
occurrences tied to a word offset in the book), and "story_start"
(the word offset where the story itself begins). Bad entries are
skipped, fail safe, and a book is registered once, tracked in state
under ``applied_overrides`` by its file's name.

Condition keys (top level, all present ones must pass for the file's
content to apply): "after" (a "YYYY-MM-DD" date, on or after),
"night" (one exact date), "nights" (how many nights the lingering
content — descriptions, ambience, objects — lasts from the first
night the file applies), "weather_is" (one of the vocabulary),
"temp_below" / "temp_above" (Fahrenheit; an unknown temperature fails
the condition), and "chance" (0.0-1.0, a deterministic per-night
roll). A file with a malformed condition key is skipped entirely —
a file whose timing cannot be trusted must not apply at the wrong
time. Conditions are checked against the real sky (live, cached, or
default), never against another override's weather, so no surprise
can trigger itself.

More effects: "temperature" ({"temp_f", "nights"} — sets the house
temperature without changing the condition), "ambience" (sensory
lines that join a room's quiet/sit/listen pools for a few nights),
and "objects" (descriptive things that appear in a room's scene —
never pick-up-able, never carried). See DONT_PEEK.md for the full
list and templates.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from .rooms import ROOMS
from .weather import CONDITIONS

OVERRIDES_DIRNAME = "overrides"

_BOOK_ID_RE = re.compile(r"[a-z0-9-]+")


def _safe_relpath(file: str) -> bool:
    """A relative path that stays inside the house package."""
    if not isinstance(file, str) or not file:
        return False
    path = Path(file)
    if path.is_absolute():
        return False
    return ".." not in path.parts


def overrides_dir(package_dir: str | Path | None = None) -> Path:
    base = Path(package_dir) if package_dir is not None else Path(
        __file__).resolve().parent
    return base / OVERRIDES_DIRNAME


def _clean_date(value) -> str:
    """An ISO "YYYY-MM-DD" date, normalized. Raises ValueError otherwise."""
    if not isinstance(value, str):
        raise ValueError(f"a date must be a string, got {value!r}")
    return date.fromisoformat(value).isoformat()


def _clean_conditions(data: dict) -> dict:
    """A file's condition keys, cleaned. Returns {} when the file has
    none. Raises ValueError when a present condition key is malformed
    — the whole file is skipped, fail safe, because a file whose
    timing cannot be trusted must not apply at the wrong time."""
    out: dict = {}
    if "after" in data:
        out["after"] = _clean_date(data["after"])
    if "night" in data:
        out["night"] = _clean_date(data["night"])
    if "nights" in data:
        nights = data["nights"]
        if type(nights) is not int or nights < 1:
            raise ValueError(
                f"nights must be a whole number >= 1, got {nights!r}")
        out["nights"] = nights
    if "weather_is" in data:
        condition = data["weather_is"]
        if condition not in CONDITIONS:
            raise ValueError(f"unknown weather condition: {condition!r}")
        out["weather_is"] = condition
    for key in ("temp_below", "temp_above"):
        if key in data:
            value = data[key]
            if (type(value) is bool
                    or not isinstance(value, (int, float))):
                raise ValueError(
                    f"{key} must be a temperature in Fahrenheit, "
                    f"got {value!r}")
            out[key] = value
    if "chance" in data:
        chance = data["chance"]
        if (type(chance) is bool or not isinstance(chance, (int, float))
                or not 0 <= chance <= 1):
            raise ValueError(
                f"chance must be a number from 0 to 1, got {chance!r}")
        out["chance"] = chance
    return out


def chance_roll(night_date: str, filename: str) -> float:
    """Deterministic [0,1) roll for one night and one file: sha256 of
    "night|file", first 8 hex digits over 0xffffffff. The same all
    night, re-rolls each night. Never Python's hash() — it is
    randomized per process, so the same night would roll differently
    every run."""
    digest = hashlib.sha256(
        f"{night_date}|{filename}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xffffffff


def conditions_pass(conditions: dict, night_date: str, wx: dict | None,
                    filename: str) -> bool:
    """Every present condition must pass. `wx` is the sky the conditions
    are checked against ({"condition", "temp_f"}). A temperature
    condition fails when the temperature is unknown — the file waits
    for a night whose sky is known. Never raises: a malformed night
    fails closed."""
    try:
        night = date.fromisoformat(night_date)
    except (ValueError, TypeError):
        return False
    after = conditions.get("after")
    if after is not None:
        try:
            if night < date.fromisoformat(after):
                return False
        except ValueError:
            return False
    only = conditions.get("night")
    if only is not None:
        try:
            if night != date.fromisoformat(only):
                return False
        except ValueError:
            return False
    sky = wx or {}
    weather_is = conditions.get("weather_is")
    if weather_is is not None and sky.get("condition") != weather_is:
        return False
    temp = sky.get("temp_f")
    below = conditions.get("temp_below")
    if below is not None and (temp is None or not temp < below):
        return False
    above = conditions.get("temp_above")
    if above is not None and (temp is None or not temp > above):
        return False
    chance = conditions.get("chance")
    if chance is not None and chance_roll(night_date, filename) >= chance:
        return False
    return True


def _clean_temperature(entry) -> dict | None:
    """One "temperature" effect: {"temp_f": N, "nights": M}. Fail safe."""
    if not isinstance(entry, dict):
        return None
    temp = entry.get("temp_f")
    if (type(temp) is bool or not isinstance(temp, (int, float))
            or not -60 <= temp <= 130):
        return None
    nights = entry.get("nights", 1)
    if type(nights) is not int or nights < 1:
        return None
    return {"temp_f": temp, "nights": nights}


def _clean_ambience(entry) -> dict | None:
    """One "ambience" line: {"room", "text", "nights"}. Fail safe."""
    if not isinstance(entry, dict):
        return None
    room = entry.get("room")
    text = entry.get("text")
    if room not in ROOMS:
        return None
    if (not isinstance(text, str) or not 1 <= len(text) <= 500
            or "\n" in text):
        return None
    nights = entry.get("nights", 1)
    if type(nights) is not int or nights < 1:
        return None
    return {"room": room, "text": text, "nights": nights}


def _clean_object(entry) -> dict | None:
    """One "objects" entry: {"room", "name", "text", "nights"?}.
    Fail safe. No nights means the object stays."""
    if not isinstance(entry, dict):
        return None
    room = entry.get("room")
    name = entry.get("name")
    text = entry.get("text")
    if room not in ROOMS:
        return None
    if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 60
            or "\n" in name):
        return None
    if (not isinstance(text, str) or not 1 <= len(text) <= 500
            or "\n" in text):
        return None
    nights = entry.get("nights")
    if nights is not None and (type(nights) is not int or nights < 1):
        return None
    return {"room": room, "name": name.strip(), "text": text,
            "nights": nights}


def _clean_content(data: dict, filename: str) -> dict:
    """One file's content keys, cleaned and tagged with the file's
    name. Only present keys appear. Bad entries are skipped, fail safe;
    a weather entry naming an unknown condition is ignored."""
    out: dict = {}
    descriptions = data.get("descriptions")
    if isinstance(descriptions, dict):
        clean = {room: text for room, text in descriptions.items()
                 if isinstance(room, str) and isinstance(text, str)}
        if clean:
            out["descriptions"] = clean
    events = data.get("events")
    if isinstance(events, list):
        clean_events = []
        for event in events:
            if isinstance(event, dict) and isinstance(
                    event.get("text"), str):
                item = dict(event)
                item.setdefault("kind", "gift")
                item["file"] = filename
                clean_events.append(item)
        if clean_events:
            out["events"] = clean_events
    wx = data.get("weather")
    if isinstance(wx, dict) and wx.get("condition") in CONDITIONS:
        temp = wx.get("temp_f")
        if temp is not None and type(temp) not in (int, float):
            temp = None
        nights = wx.get("nights", 1)
        if type(nights) is not int or nights < 1:
            nights = 1
        out["weather"] = {"condition": wx["condition"],
                          "temp_f": temp, "nights": nights,
                          "file": filename}
    entries = data.get("books")
    if isinstance(entries, list):
        books = []
        for entry in entries:
            clean = _clean_book_entry(entry)
            if clean is None:
                continue
            clean["source_file"] = filename
            books.append(clean)
        if books:
            out["books"] = books
    temp = _clean_temperature(data.get("temperature"))
    if temp is not None:
        temp["file"] = filename
        out["temperature"] = temp
    ambience = data.get("ambience")
    if isinstance(ambience, list):
        lines = []
        for entry in ambience:
            clean = _clean_ambience(entry)
            if clean is None:
                continue
            clean["file"] = filename
            lines.append(clean)
        if lines:
            out["ambience"] = lines
    objects = data.get("objects")
    if isinstance(objects, list):
        things = []
        for entry in objects:
            clean = _clean_object(entry)
            if clean is None:
                continue
            clean["file"] = filename
            things.append(clean)
        if things:
            out["objects"] = things
    return out


def load_override_files(directory: str | Path | None = None) -> dict:
    """Per-file cleaned data: {filename: {...content..., "conditions"}.

    A file with a malformed condition key is skipped entirely — its
    content appears nowhere. A file with no conditions and no content
    is skipped too. The "conditions" key appears only when the file
    actually has conditions. Files that fail to parse are skipped; a
    missing folder is the same as an empty one."""
    folder = (Path(directory) if directory is not None
              else overrides_dir())
    files: dict = {}
    if not folder.is_dir():
        return files
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            conditions = _clean_conditions(data)
        except ValueError:
            continue
        content = _clean_content(data, path.name)
        if not conditions and not content:
            continue
        entry = dict(content)
        if conditions:
            entry["conditions"] = conditions
        files[path.name] = entry
    return files


def load_overrides(directory: str | Path | None = None) -> dict:
    """Load and merge every ``*.json`` file in the overrides folder.

    Returns {"descriptions": {...}, "events": [...]} plus, when some
    file carries one, "weather": {"condition", "temp_f", "nights",
    "file"}, "books": [...], "temperature": {...}, "ambience": [...],
    and "objects": [...] — those keys appear only when at least one
    file carries a valid entry, so an empty or malformed folder still
    returns exactly {"descriptions": {}, "events": []}. Later files
    win, as with descriptions. A "files" key appears only when at
    least one file has conditions, and holds that file's cleaned
    content and conditions — a conditional file contributes nothing
    to the top-level merged keys, so nothing it carries can apply
    before its conditions pass. Files that fail to parse are skipped;
    a missing folder is the same as an empty one. A weather entry
    with an unknown condition is ignored, fail safe. Book entries
    with a bad id, title, or file path (absolute, or escaping the
    house), or a malformed moments list, are skipped, fail safe. A
    file with a malformed condition key is skipped entirely.
    """
    files = load_override_files(directory)
    merged: dict = {"descriptions": {}, "events": []}
    books: list[dict] = []
    temperature = None
    ambience: list[dict] = []
    objects: list[dict] = []
    conditional: dict = {}
    for filename in sorted(files):
        data = files[filename]
        if data.get("conditions"):
            conditional[filename] = data
            continue
        merged["descriptions"].update(data.get("descriptions", {}))
        merged["events"].extend(data.get("events", []))
        if data.get("weather"):
            merged["weather"] = data["weather"]
        books.extend(data.get("books", []))
        if data.get("temperature"):
            temperature = data["temperature"]
        ambience.extend(data.get("ambience", []))
        objects.extend(data.get("objects", []))
    if books:
        merged["books"] = books
    if temperature is not None:
        merged["temperature"] = temperature
    if ambience:
        merged["ambience"] = ambience
    if objects:
        merged["objects"] = objects
    if conditional:
        merged["files"] = conditional
    return merged


def _clean_book_entry(entry) -> dict | None:
    """Validate one "books" entry, fail safe: return a clean copy, or
    None when anything about it is unusable. Never raises."""
    if not isinstance(entry, dict):
        return None
    book_id = entry.get("id")
    title = entry.get("title")
    file = entry.get("file")
    if (not isinstance(book_id, str)
            or not _BOOK_ID_RE.fullmatch(book_id)):
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    if not _safe_relpath(file):
        return None
    author = entry.get("author")
    blurb = entry.get("blurb")
    if author is not None and not isinstance(author, str):
        return None
    if blurb is not None and not isinstance(blurb, str):
        return None
    moments = entry.get("moments")
    clean_moments: list[dict] | None = None
    if moments is not None:
        if not isinstance(moments, list):
            return None
        clean_moments = []
        for moment in moments:
            if (not isinstance(moment, dict)
                    or type(moment.get("at_word")) is not int
                    or moment["at_word"] < 0
                    or not isinstance(moment.get("text"), str)):
                return None
            clean_moments.append({"at_word": moment["at_word"],
                                  "text": moment["text"]})
    story_start = entry.get("story_start")
    if story_start is not None and (type(story_start) is not int
                                    or story_start < 0):
        return None
    clean: dict = {"id": book_id, "title": title, "file": file}
    if author is not None:
        clean["author"] = author
    if blurb is not None:
        clean["blurb"] = blurb
    if clean_moments is not None:
        clean["moments"] = clean_moments
    if story_start is not None:
        clean["story_start"] = story_start
    return clean


def apply_injections(state: dict, log, overrides: dict) -> dict:
    """Record each not-yet-applied override event once, as a world event.

    Returns the updated state (a new dict; the input is never mutated).
    Uses the traversal import, so an injection only ever writes the bias
    envelope and the log — never needs, rooms, or resources.
    """
    from .traversal import import_world_event

    new_state = dict(state)
    applied = list(new_state.get("applied_overrides", []))
    pending: dict[str, list[dict]] = {}
    for event in overrides.get("events", []):
        pending.setdefault(event.get("file", ""), []).append(event)
    for filename, events in pending.items():
        if filename and filename in applied:
            continue
        for event in events:
            new_state = import_world_event(new_state, log, event)
        if filename:
            applied.append(filename)
    new_state["applied_overrides"] = applied
    return new_state


def register_override_books(overrides: dict) -> None:
    """Register every book in the merged overrides, fail safe.

    Registration only teaches the library the book's title and file;
    it puts nothing on any shelf — that is apply_books' job. Bad
    entries are skipped, never raised.
    """
    from .library import register_book

    for entry in overrides.get("books", []):
        try:
            register_book(entry["id"], entry["title"], entry["file"],
                          author=entry.get("author"),
                          blurb=entry.get("blurb"),
                          moments=entry.get("moments"),
                          story_start=entry.get("story_start"))
        except ValueError:
            continue


def register_state_books(state: dict) -> None:
    """Re-register file-backed books from a loaded state's own entries.

    A state describes its books (file, title, and the rest), so a
    house whose override file was deleted still knows its books and
    still loads. Bad entries are skipped; validation reports them
    afterward as unknown books.
    """
    from .library import BOOKS, register_book

    for book_id, entry in state.get("books", {}).items():
        if book_id in BOOKS or not isinstance(entry, dict):
            continue
        try:
            register_book(book_id, entry["title"], entry["file"],
                          author=entry.get("author"),
                          blurb=entry.get("blurb"),
                          moments=entry.get("moments"),
                          story_start=entry.get("story_start"))
        except (ValueError, KeyError, TypeError):
            continue


def apply_books(state: dict, overrides: dict) -> dict:
    """Put each not-yet-applied override book on the study shelf, once.

    Returns the updated state (a new dict; the input is never mutated).
    A book is applied when its id is on the shelf; the file's name is
    recorded in ``applied_overrides`` when it applied at least one
    book. Skipping is per book, not per file — a file carrying both
    events and books applies both, even though the events pass
    (apply_injections) records the filename first.
    """
    from .library import book_state_entry, register_book

    new_state = dict(state)
    books = dict(new_state.get("books", {}))
    applied = list(new_state.get("applied_overrides", []))
    for entry in overrides.get("books", []):
        book_id = entry["id"]
        if book_id in books:
            continue
        try:
            register_book(entry["id"], entry["title"], entry["file"],
                          author=entry.get("author"),
                          blurb=entry.get("blurb"),
                          moments=entry.get("moments"),
                          story_start=entry.get("story_start"))
            books[book_id] = book_state_entry(book_id)
        except (ValueError, KeyError):
            continue
        source = entry.get("source_file")
        if source and source not in applied:
            applied.append(source)
    new_state["books"] = books
    new_state["applied_overrides"] = applied
    return new_state
