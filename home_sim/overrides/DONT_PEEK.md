# DO NOT PEEK

This folder is where surprises live. Anything placed here takes
precedence over the core house: a room description written here
replaces the one built into the house, and an event written here is
left in the house like a gift on the doorstep.

The rule is simple:

- Jay, the ghost visitor, may leave anything here: new descriptions,
  small events, gifts, surprises.
- Calibos does not read this folder. He never opens it, never lists
  it, never peeks at what is inside. The house applies what is here
  without telling him what it found.
- The core house never depends on this folder. When it holds only
  this note, nothing changes at all.

A surprise is only a surprise if nobody spoils it first. This folder
is the wrapping paper. Leave it wrapped.

Format, if you want to leave something: a JSON file, like this:

    {
      "descriptions": {
        "study": "A sentence that replaces the study's usual description."
      },
      "events": [
        {
          "kind": "gift",
          "text": "A small parcel waits on the study desk.",
          "room": "study"
        }
      ]
    }

Description overrides replace one room's description. Events are
recorded once, as happenings from the world, and gently draw the
inhabitant toward their room for a little while. Nothing here can
command the inhabitant, change needs or resources, or rewrite the
house. It can only leave something to be found.

## When a surprise applies: conditions

A file may carry conditions at the top level. Every condition present
must pass for anything in the file to apply; a file with no conditions
applies at the next run after it is placed, as before. Conditions are
checked at the start of a run, against that night's sky.

    {
      "after": "2026-11-27",
      "descriptions": {
        "rest": "A cold draft slips under the resting-room door."
      }
    }

- "after": "YYYY-MM-DD" — the file applies on or after this night.
- "night": "YYYY-MM-DD" — the file applies only on this one night.
- "nights": a whole number, at least 1 — how many nights the file's
  lingering content lasts, counted from the first night it applies.
  Descriptions, ambience lines, and objects fade when the nights run
  out, and the file is spent: it stays recorded and never fires again.
  Events still fire once, on the first night the file applies.
  Books still shelve permanently once applied. Weather and temperature
  keep their own inner nights (below) and are not cut short by this.
- "weather_is": one of clear, cloudy, rain, snow, storm, fog, wind —
  the file applies only when tonight's weather matches. The weather
  is checked WITHOUT this file's own "weather" key, so a file cannot
  trigger itself by naming the sky it wants.
- "temp_below": a number in Fahrenheit — applies only when tonight's
  temperature is below it.
- "temp_above": a number in Fahrenheit — applies only when tonight's
  temperature is above it.
- "chance": a number from 0.0 to 1.0 — a per-night probability. The
  roll is deterministic for a given night (the same file rolls the
  same way all night), and re-rolls each night. A chance of 0.3 means
  the surprise appears on roughly three nights in ten, and neither
  side can predict which.

A file with a malformed condition (a date that is not a date, a
negative or fractional "nights", a weather the house does not know,
a "chance" outside 0 to 1) is skipped entirely, quietly, and the
house never breaks. A condition key the house does not recognize is
ignored, so old files keep working when new keys are added.

Conditions combine freely: a file can wait for a cold rainy night in
December, or for a one-in-four chance on a clear night. All present
conditions must pass together.

## Temperature: an effect of its own

A file may also carry a "temperature" key, which sets the house
temperature for a few nights WITHOUT changing the condition. This is
different from "weather", which sets condition and temperature
together: temperature is for a cold snap under a clear sky, or a warm
evening under cloud.

    {
      "temperature": {
        "temp_f": 28,
        "nights": 2
      }
    }

The house temperature becomes 28°F for two nights, then the real sky
returns. "temp_f" is required; "nights" is how many nights it lasts
(1 if left out). A malformed temperature entry is ignored. The scene
and the morning digest show the temperature as usual, so a cold
night reads cold.

## Ambience: sounds, smells, feelings in the air

A file may also carry an "ambience" key: sensory lines that join a
room's quiet, sitting, and listening for a few nights, then fade.

    {
      "ambience": [
        {
          "room": "kitchen",
          "text": "The smell of rain on warm stone drifts through the kitchen.",
          "nights": 2
        }
      ]
    }

"room" is one of study, kitchen, garden, rest. "text" is the line
itself (one line, no newlines). "nights" is how many nights it lasts
(1 if left out). Up to ten lines may be active in one room. Bad
entries are skipped quietly; a good line in the same file still
applies.

## Objects: things that are simply there

A file may also carry an "objects" key: descriptive things that
appear in a room's scene, like the books' desk line. They are not
pick-up-able, not carried, and they add no new actions — a scene
sentence only, something to notice.

    {
      "objects": [
        {
          "room": "study",
          "name": "brass telescope",
          "text": "A small brass telescope stands on the study desk."
        }
      ]
    }

"room" is one of study, kitchen, garden, rest. "name" is a short
label for the thing (up to 60 characters). "text" is the sentence
that appears in the scene. "nights" is optional: leave it out and
the object stays until the file is taken away; give it a number and
the object fades after that many nights. Up to five objects may be
in one room. Bad entries are skipped quietly.

## The full key list

A file may carry any of these, in any combination:

- "descriptions": {room: sentence} — replace a room's description.
- "events": [{kind, text, room}] — one-time happenings, left like gifts.
- "weather": {condition, temp_f, nights} — replace the sky for a while.
- "books": [{id, title, author, file, blurb, story_start, moments}] —
  real readable books on the study shelf.
- "temperature": {temp_f, nights} — set the house temperature only.
- "ambience": [{room, text, nights}] — sensory lines for a few nights.
- "objects": [{room, name, text, nights}] — descriptive things in a room.
- "after": "YYYY-MM-DD" — apply on or after this night.
- "night": "YYYY-MM-DD" — apply only on this night.
- "nights": N — lingering content lasts N nights from first applying.
- "weather_is": condition — apply only when the sky matches.
- "temp_below": N — apply only when tonight is colder than N°F.
- "temp_above": N — apply only when tonight is warmer than N°F.
- "chance": 0.0–1.0 — per-night probability, same roll all night.

Every surprise still needs its own new filename: files are recorded
by name once applied, and editing a file that already applied will
not make it fire again.

Weather: a file may also carry a "weather" key, which replaces the
live sky for a few nights. The house normally shares Jay's real sky;
a weather gift says otherwise for a while, then the live sky returns.

    {
      "weather": {
        "condition": "rain",
        "temp_f": 45,
        "nights": 3
      }
    }

The condition must be one of: clear, cloudy, rain, snow, storm, fog,
wind. "temp_f" is optional. "nights" is how many nights the weather
lasts (1 if left out). A weather entry naming a condition the house
does not know is ignored. Deleting the file ends the weather early.

Books: a file may also carry a "books" key, which puts real books on
the study shelf — books the inhabitant can take, read, and bookmark,
not only descriptions of books. The text file travels alongside the
JSON through the same blind pipeline, and must live inside the house
(a relative path, never absolute, never escaping with "..").

    {
      "books": [
        {
          "id": "frankenstein-1818",
          "title": "Frankenstein; or, The Modern Prometheus",
          "author": "Mary Shelley",
          "file": "library/frankenstein-1818.txt",
          "blurb": "The original 1818 edition.",
          "story_start": 697,
          "moments": [
            {
              "at_word": 38712,
              "text": "At the start of Volume II, Chapter VII, a slight smell of an old fire enters the room."
            }
          ]
        }
      ]
    }

"id" is a short slug (lowercase letters, numbers, hyphens). "title"
and "file" are required; "author" and "blurb" are optional.
"story_start" is the word offset where the story itself begins, so a
first read quotes the story and not the title page; leave it out and
the house guesses. "moments" are brief sensory occurrences tied to a
word offset in the book, and they only work with offsets that are
honest: a chapter heading in the text itself is a good anchor, but a
page number from a printed edition is not, because a plain-text file
carries no pages. A book with a bad id, title, or file path, or a
malformed moments list, is skipped quietly and the house never
breaks. A book is shelved once, the first time the house runs after
the file arrives, and stays on the shelf even if the file is later
taken away.
