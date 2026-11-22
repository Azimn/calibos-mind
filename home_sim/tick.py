"""Off-hours tick model and the putz behavior proposer.

One tick is one micro-action: needs + affordances + light + a little
stochasticity propose 1-3 candidates, and one is taken. Jazz, not
composition — there are no scripted paths, no room sequences, no behavior
trees. The weights below are tendencies with noise; the same night never
plays twice. (Fitness: 14 nights, no composed paths, or the proposer reverts.)

Light weights (never writes) the proposal: a dark room is a less appealing
destination, but darkness never changes a need value. World-event bias
weights (never dictates): a recent visitor makes a room likelier, then fades.

The Home class owns one night: state load/save under a state directory,
event logging, and the run loop. It never touches the thought database, the
prompt inbox, dream fragments, salience sidecars, or ablation records — it
has no code path to any of them (stdlib only, no imports from the mind
package).
"""
from __future__ import annotations

import copy
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import rhythm, weather
from .events import EventLog, PROVENANCE_SIMULATED, summarize_night
from .overrides import (apply_books, apply_injections, conditions_pass,
                        load_overrides, overrides_dir,
                        register_override_books, register_state_books)
from .rooms import ROOMS, AFFORDANCES, ROOM_LABELS

CHICAGO = ZoneInfo("America/Chicago")
OFF_HOURS_START = 23  # 23:00 local
OFF_HOURS_END = 7     # 07:00 local
NIGHT_MINUTES = 8 * 60


def in_off_hours(now: datetime | None = None) -> bool:
    """True when it is off-hours in America/Chicago."""
    now = now or datetime.now(CHICAGO)
    local = now.astimezone(CHICAGO)
    return local.hour >= OFF_HOURS_START or local.hour < OFF_HOURS_END


def night_date_for(now: datetime | None = None) -> str:
    """ISO date of the evening the current night began."""
    now = now or datetime.now(CHICAGO)
    local = now.astimezone(CHICAGO)
    day = local.date()
    if local.hour < OFF_HOURS_END:
        # After midnight: the night began yesterday evening.
        day = day.fromordinal(day.toordinal() - 1)
    return day.isoformat()


class Proposer:
    """Proposes 1-3 micro-actions per tick; the caller takes one.

    Seedable for tests. No memory of past nights, no scripts — every
    proposal is recomputed from the current state.
    """

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def _try(self, state: dict, op: str, args: dict) -> bool:
        """Is this op currently legal? Answered by the rhythm itself, on a
        throwaway copy — the proposer can never propose the impossible, and
        gate logic lives in exactly one place."""
        try:
            rhythm.act(state, op, args)
            return True
        except rhythm.HomeError:
            return False

    def _room_of(self, state: dict, op: str, args: dict) -> str:
        if op == "move":
            return args["to"]
        return state["room"]

    def _weight(self, state: dict, op: str, args: dict) -> float:
        room = state["room"]
        w = 1.0
        if op == "move":
            # Raised from 0.8 when the graft added more to do in every
            # room: the shortlist is a fixed top three, so a larger
            # candidate pool was crowding the wander out entirely and a
            # night could sit in the study for 48 steps. The body and
            # the lamps still outvote it when they need to.
            w = 1.2
            target = args["to"]
            # Light weights, never writes: dark destinations are less
            # appealing at night.
            if not rhythm._can_see(state, target):
                w *= 0.3
        # Need pressure: the body gets a vote, not a veto.
        hunger, thirst, fatigue = (state["hunger"], state["thirst"],
                                  state["fatigue"])
        if op in ("prepare_meal", "eat"):
            w *= 4.0 if hunger >= 7 else 2.5 if hunger >= 4 else 0.6
        if op == "eat" and state["meal_ready"] and room == "kitchen":
            w *= 3.0  # eat what's already waiting
        if op == "brew_tea":
            w *= 4.0 if thirst >= 6 else 1.0 if thirst >= 4 else 0.5
        if op == "rest":
            w *= 4.0 if fatigue >= 7 else 2.0 if fatigue >= 4 else 0.7
        if op == "sleep":
            w *= 3.0 if fatigue >= 7 else 1.2 if fatigue >= 5 else 0.15
        # The garden economy: low pantry -> tend the beds.
        if op in ("tend_garden", "harvest", "store_produce"):
            if state["rations"] <= 1:
                w *= 3.0
            elif state["rations"] <= 2:
                w *= 1.5
        if op == "harvest":
            w *= 4.0 if state["garden_growth"] >= 3 else 1.0
        if op == "store_produce" and state["produce"] > 0:
            w *= 3.0
        if op == "tidy" and state["clutter"].get(room, 0) >= 2:
            w *= 2.0
        if op in ("read", "write") and room == "study":
            w *= 1.4 if _daylight_for_proposer(state) else 1.1
        # Graft verbs: the senses are for visits, so they stay rare at
        # night; custody and the fish get a gentle nudge when relevant.
        # They are weighted low on purpose — the night must still wander
        # between rooms instead of sitting in the study reading.
        if op in ("touch", "smell", "taste", "listen"):
            w *= 0.4
        if op in ("quiet", "sit"):
            w *= 0.6
        if op == "watch_fish" and room == "study":
            w *= 0.8
        if op in ("take_book", "put_book"):
            w *= 0.5
        if op == "take_mug" and state.get("mug_in") == room:
            w *= 1.5
        if op == "take_plate" and state.get("meal_ready"):
            w *= 2.0
        if op == "sip_tea" and "mug" in state.get("carried", []):
            w *= 2.0 if thirst >= 4 else 1.0
        if op in ("open_wardrobe", "wear_outfit", "wear_item",
                  "remove_item", "name_fish", "bookmark", "write_note",
                  "read_diary"):
            w *= 0.3
        # World-event bias: weight, never dictate. Decays on its own.
        bias = state.get("world_bias")
        if bias and bias.get("room") == self._room_of(state, op, args):
            w *= 3.0
        # Stochasticity: the jazz. Same state, different nights.
        w *= self.rng.uniform(0.7, 1.3)
        return w

    def candidates(self, state: dict) -> list[tuple[str, dict, float]]:
        """All legal (op, args) with weights. Expensive on purpose — the
        throwaway act() calls keep gates in one place."""
        room = state["room"]
        scored = []
        ops = list(AFFORDANCES[room]) + ["look", "light_lamp", "douse_lamp"]
        for op in ops:
            if self._try(state, op, {}):
                scored.append((op, {}, self._weight(state, op, {})))
        for target in ROOMS:
            if target == room:
                continue
            args = {"to": target}
            if self._try(state, "move", args):
                scored.append(("move", args, self._weight(state, "move", args)))
        # "look" is always legal; ensure a floor of idleness exists.
        scored.sort(key=lambda c: c[2], reverse=True)
        return scored

    def propose(self, state: dict
                ) -> tuple[list[tuple[str, dict]], tuple[str, dict]]:
        """Return (shortlist of up to 3, chosen one). The shortlist is the
        roads considered; the event log keeps them as `considered`."""
        scored = self.candidates(state)
        if not scored:
            raise rhythm.HomeError("No legal action — the home is stuck")
        shortlist = [(op, args) for op, args, _ in scored[:3]]
        weights = [w for _, _, w in scored[:3]]
        chosen = self.rng.choices(shortlist, weights=weights, k=1)[0]
        return shortlist, chosen


def _daylight_for_proposer(state: dict) -> bool:
    return rhythm._daylight(state["minute"])


def _night_complete(state: dict) -> bool:
    """A night is done once its 480 simulated minutes have elapsed. Loading
    a complete night starts a fresh one — the day in between was lived away
    from the sim, so needs reset; only the event log accumulates."""
    return (state["minute"] - rhythm.NIGHT_START_MINUTE) >= NIGHT_MINUTES


def _next_night(night_date: str) -> str:
    return (date.fromisoformat(night_date) + timedelta(days=1)).isoformat()


# What survives the morning roll-forward: the inhabitant's belongings
# and living things, not the night's needs. A fresh night resets hunger,
# thirst, fatigue, lamps, clutter, meals, and hands — the day in between
# was lived away — but the fish, the books, the clothes, the notes, and
# the diary are possessions, and a fish's hunger is a debt across days:
# if it reset every morning, three days of neglect could never kill it,
# and the stakes would be fake. A book left in hand when the night ends
# is put back on the study shelf, since the hands reset empty.
# World notices do NOT carry forward: they are a same-night phenomenon,
# shown only on the night they arrived. The log keeps them, and the
# morning digest quotes them, so nothing is lost — they simply stop
# being on the desk by morning. The override keys DO carry forward:
# a first-night ledger, a room's objects, and ambience lines are all
# dated things that span nights by design, and a temperature override
# counts its nights down across the boundary.
_PERSISTENT_KEYS = ("fish", "books", "wardrobe", "notes", "diary",
                    "fish_observation", "applied_overrides", "weather",
                    "override_first_night", "room_objects",
                    "ambience_lines", "temp_override")


def _carry_forward(new_state: dict, old_state: dict) -> None:
    """Copy the persistent belongings from a finished night into the
    fresh one, in place."""
    for key in _PERSISTENT_KEYS:
        new_state[key] = copy.deepcopy(old_state[key])
    for entry in new_state["books"].values():
        if entry["location"] == "carried":
            entry["location"] = "study"


def _prev_night(night_date: str) -> str:
    return (date.fromisoformat(night_date) - timedelta(days=1)).isoformat()


class Home:
    """One inhabitant's off-hours home. State lives in a directory
    (state.json + events.jsonl); nothing else on the machine is touched."""

    def __init__(self, state_dir: str | Path,
                 night_date: str | None = None, seed: int | None = None,
                 weather_fetcher=None):
        """weather_fetcher: None means offline (no live lookup is ever
        attempted — the test suite's default, and the reason a plain
        Home() never touches the network). Pass weather.open_meteo_
        fetcher for the real sky, or a fake in tests."""
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "state.json"
        self.log = EventLog(self.state_dir)
        self.proposer = Proposer(seed)
        self.weather_fetcher = weather_fetcher
        self.overrides = load_overrides(overrides_dir())
        register_override_books(self.overrides)
        # Conditional files' books register too — registration only
        # teaches the library; shelving is apply_books' job, gated by
        # conditions.
        for data in self.override_files.values():
            register_override_books({"books": data.get("books", [])})
        # Before any run, the active view is the unconditional content
        # only; apply_overrides recomputes it once conditions are known.
        self.active_overrides = {k: v for k, v in self.overrides.items()
                                 if k != "files"}
        # One-shot handoff from resolve_weather to the next
        # apply_overrides on the same night: once resolve has written
        # an override into the state, the real sky is no longer in the
        # state to evaluate against, so apply reuses resolve's
        # evaluation instead of re-evaluating against the overridden
        # sky. Consumed on use; a standalone apply evaluates fresh.
        self._pending_evaluation: tuple | None = None
        self._fetch_cache: dict[str, dict] = {}
        if self.state_path.exists():
            with open(self.state_path, encoding="utf-8") as fh:
                self.state = json.load(fh)
            # A state's own books re-register first, so a house whose
            # override file was deleted still knows its books.
            register_state_books(self.state)
            rhythm._validate(self.state)
            if _night_complete(self.state):
                # A finished night never replays as daytime: roll forward.
                # Belongings and the fish carry over (see _carry_forward);
                # needs and hands reset.
                old_state = self.state
                self.state = rhythm.initial_state(
                    _next_night(self.state["night_date"]))
                _carry_forward(self.state, old_state)
                self.save()
        else:
            self.state = rhythm.initial_state(night_date or night_date_for())
            self.save()

    @property
    def override_files(self) -> dict:
        """Per-file data for conditional override files, read live from
        the current overrides dict — so a test assigning home.overrides
        sees its files, and the loader stays the one source of truth."""
        files = self.overrides.get("files")
        return files if isinstance(files, dict) else {}

    def _live_fetcher(self):
        """The configured live fetcher, memoized per Home (one run, one
        sky): condition evaluation may resolve the weather more than
        once, and the sky must not be re-fetched each time. None when
        the home is offline."""
        if self.weather_fetcher is None:
            return None

        def fetch(url: str) -> dict:
            if url not in self._fetch_cache:
                self._fetch_cache[url] = self.weather_fetcher(url)
            return self._fetch_cache[url]

        return fetch

    def _condition_weather(self) -> dict:
        """The sky for condition checks, without any override file's
        weather: live, cached, or default. Never consumes a night and
        never mutates state."""
        overrides = {k: v for k, v in self.overrides.items()
                     if k != "weather"}
        return weather.resolve_weather(
            self.state, overrides, fetcher=self._live_fetcher(),
            for_night=False)

    def _evaluate_active(self) -> tuple[dict, dict, dict | None]:
        """Which conditional files are active tonight, the effective
        weather, and the winning active weather entry (or None). Pure
        evaluation: records nothing, consumes nothing.

        A weather file's own conditions are checked against the sky
        without any override — anti-circularity, so no file can
        trigger itself with its own weather key. Every other file is
        checked against the effective sky, with the winning weather
        override applied. The winning weather entry is the last
        filename among active files carrying one."""
        files = self.override_files
        night = self.state["night_date"]
        if not files:
            return ({}, self.state.get("weather")
                    or weather.default_weather(),
                    self.overrides.get("weather"))
        base: dict | None = None

        def _base() -> dict:
            nonlocal base
            if base is None:
                base = self._condition_weather()
            return base

        def _wx_for(conds: dict) -> dict:
            # Only a weather condition needs the sky; anything else can
            # use the state's weather as a stand-in.
            if any(k in conds for k in ("weather_is", "temp_below",
                                        "temp_above")):
                return _base()
            return self.state.get("weather") or {}

        candidates: list[tuple[str, dict]] = []
        merged_wx = self.overrides.get("weather")
        if isinstance(merged_wx, dict):
            candidates.append((merged_wx.get("file", ""), merged_wx))
        for name, data in files.items():
            if data.get("weather"):
                candidates.append((name, data["weather"]))
        winning: tuple[str, dict] | None = None
        for name, entry in candidates:
            conds = files.get(name, {}).get("conditions", {})
            if conditions_pass(conds, night, _wx_for(conds), name):
                if winning is None or name > winning[0]:
                    winning = (name, entry)
        need_effective = any(
            any(k in data.get("conditions", {})
                for k in ("weather_is", "temp_below", "temp_above"))
            for data in files.values())
        if winning is not None:
            effective = weather.resolve_weather(
                self.state, {"weather": winning[1]},
                fetcher=self._live_fetcher(), for_night=False)
        elif need_effective:
            effective = _base()
        else:
            effective = self.state.get("weather") or {}
        # A weather file is judged against the base sky, never the
        # effective one — its own weather key must not decide whether
        # it applies. Every other file is judged against the effective
        # sky, with the winning weather override in force.
        active = {}
        for name, data in files.items():
            conds = data.get("conditions", {})
            wx = _base() if data.get("weather") else effective
            active[name] = conditions_pass(conds, night, wx, name)
        return active, effective, (winning[1] if winning else None)

    def _winning_temperature_entry(self, active: dict) -> dict | None:
        """The temperature entry in force tonight: among files with a
        temperature key that are active (unconditional files always
        are), the last filename wins. Returns the entry (with "file")
        or None."""
        candidates: list[tuple[str, dict]] = []
        merged_temp = self.overrides.get("temperature")
        if isinstance(merged_temp, dict):
            candidates.append((merged_temp.get("file", ""), merged_temp))
        for name, data in self.override_files.items():
            if data.get("temperature") and active.get(name):
                candidates.append((name, data["temperature"]))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _apply_temperature(self, entry: dict | None,
                           for_night: bool) -> dict:
        """Temperature as an effect: set the house temperature for the
        entry's nights, without changing the condition. The sky's own
        temperature is remembered, so expiry gives it back instead of
        leaving a cached override number in place. A visit applies the
        temperature without consuming a night. Whether the entry is
        still in force is the caller's decision (the first-night
        window); this function only applies, continues, or ends it.
        Returns updated state."""
        new_state = dict(self.state)
        current = new_state.get("temp_override")
        if entry is not None:
            filename = entry.get("file", "")
            if (isinstance(current, dict)
                    and current.get("file") == filename):
                override = dict(current)
                if for_night and override.get("nights_left", 0) > 0:
                    override["nights_left"] -= 1
                new_state["temp_override"] = override
                wx = dict(new_state.get("weather") or {})
                wx["temp_f"] = entry["temp_f"]
                new_state["weather"] = wx
                return new_state
        if entry is None:
            if isinstance(current, dict):
                wx = dict(new_state.get("weather") or {})
                wx["temp_f"] = current.get("prev_temp_f")
                new_state["weather"] = wx
                new_state["temp_override"] = None
            return new_state
        # Fresh override (a new file, or a new file replacing an old
        # one): remember the temperature from before any override, so
        # a replacement chains back to the real sky, not to the old
        # override's number.
        wx = new_state.get("weather") or {}
        if isinstance(current, dict):
            prev = current.get("prev_temp_f")
        else:
            prev = wx.get("temp_f")
        nights = entry.get("nights", 1)
        new_state["temp_override"] = {
            "temp_f": entry["temp_f"], "file": entry.get("file", ""),
            "nights_left": nights - 1 if for_night else nights,
            "prev_temp_f": prev}
        wx = dict(wx)
        wx["temp_f"] = entry["temp_f"]
        new_state["weather"] = wx
        return new_state

    def _install_ambience_and_objects(self, newly: list[str]) -> dict:
        """Install ambience lines and objects from files that became
        active tonight. Expired lines and objects are pruned first, so
        the caps count live ones. Deduplicated, so a second apply in
        one night never doubles anything. Returns updated state."""
        new_state = dict(self.state)
        night = new_state["night_date"]
        ambience = [line for line in new_state.get("ambience_lines", [])
                    if rhythm._within_window(line.get("first_night"),
                                             line.get("nights"), night)]
        objects = [obj for obj in new_state.get("room_objects", [])
                   if rhythm._within_window(obj.get("first_night"),
                                            obj.get("nights"), night)]
        newly_set = set(newly)
        pending: list[tuple[str, dict]] = []
        for entry in self.overrides.get("ambience", []):
            if entry.get("file", "") in newly_set:
                pending.append(("ambience", entry))
        for entry in self.overrides.get("objects", []):
            if entry.get("file", "") in newly_set:
                pending.append(("objects", entry))
        for name in sorted(newly):
            data = self.override_files.get(name, {})
            for entry in data.get("ambience", []):
                pending.append(("ambience", entry))
            for entry in data.get("objects", []):
                pending.append(("objects", entry))
        for kind, entry in pending:
            room = entry["room"]
            if kind == "ambience":
                if any(line["room"] == room
                       and line["text"] == entry["text"]
                       for line in ambience):
                    continue
                if len([l for l in ambience if l["room"] == room]) >= 10:
                    continue
                ambience.append({"room": room, "text": entry["text"],
                                 "first_night": night,
                                 "nights": entry["nights"]})
            else:
                if any(obj["room"] == room
                       and obj["name"] == entry["name"]
                       for obj in objects):
                    continue
                if len([o for o in objects if o["room"] == room]) >= 5:
                    continue
                objects.append({"room": room, "name": entry["name"],
                                "text": entry["text"],
                                "first_night": night,
                                "nights": entry.get("nights")})
        new_state["ambience_lines"] = ambience
        new_state["room_objects"] = objects
        return new_state

    def _expire_spent_files(self, first: dict, night: str) -> dict:
        """A conditional file whose nights window has passed is spent:
        its name stays in applied_overrides and it never re-fires."""
        new_state = dict(self.state)
        applied = list(new_state.get("applied_overrides", []))
        changed = False
        for name, data in self.override_files.items():
            nights = data.get("conditions", {}).get("nights")
            if nights is None:
                continue
            start = first.get(name)
            if start is None:
                continue
            if not rhythm._within_window(start, nights, night):
                if name not in applied:
                    applied.append(name)
                    changed = True
        if changed:
            new_state["applied_overrides"] = applied
        return new_state

    def apply_overrides(self, for_night: bool = True) -> None:
        """Apply any not-yet-applied overrides once: record their
        events as world events, put their books on the study shelf,
        and install their ambience lines, objects, and temperature.
        Does nothing when the overrides folder holds only its readme.

        Conditions gate everything a file carries: a file applies only
        on nights its conditions pass. Descriptions, ambience, and
        objects last the file's `nights` from the first night it
        applies, then the file is spent and never re-fires. Events fire
        once when conditions first pass; books shelve permanently;
        weather and temperature keep their own inner nights. A visit
        applies without consuming a night.

        When resolve_weather ran just before on the same night, its
        evaluation is reused (see the handoff in __init__): the state
        already holds the override by then, so a fresh evaluation
        would judge weather files against their own sky."""
        pending = self._pending_evaluation
        self._pending_evaluation = None
        if (pending is not None
                and pending[0] == self.state["night_date"]):
            active, _effective, _winning = pending[1]
        else:
            active, _effective, _winning = self._evaluate_active()
        night = self.state["night_date"]
        files = self.override_files
        first = dict(self.state.get("override_first_night", {}))
        newly: list[str] = []
        for name, ok in active.items():
            if ok and name not in first:
                first[name] = night
                newly.append(name)
        for entry in (self.overrides.get("ambience", [])
                      + self.overrides.get("objects", [])):
            fname = entry.get("file", "")
            if fname and fname not in first:
                first[fname] = night
                newly.append(fname)
        # An unconditional temperature file is first activated on its
        # first run too: the first-night ledger is what stops it
        # re-activating after its nights run out.
        merged_temp = self.overrides.get("temperature")
        if isinstance(merged_temp, dict):
            fname = merged_temp.get("file", "")
            if fname and fname not in first:
                first[fname] = night
        self.state["override_first_night"] = first

        def _duration_active(name: str) -> bool:
            conds = files.get(name, {}).get("conditions", {})
            nights = conds.get("nights")
            if nights is None:
                return True
            start = first.get(name)
            if start is None:
                return False
            return rhythm._within_window(start, nights, night)

        # The active view: unconditional merged content, plus active
        # conditional content. A conditional file's descriptions win
        # over unconditional ones while it is active.
        view: dict = {k: v for k, v in self.overrides.items()
                      if k != "files"}
        descriptions = dict(view.get("descriptions", {}))
        events = list(view.get("events", []))
        books = list(view.get("books", []))
        for name in sorted(files):
            if not active.get(name):
                continue
            data = files[name]
            if _duration_active(name):
                descriptions.update(data.get("descriptions", {}))
            events.extend(data.get("events", []))
            books.extend(data.get("books", []))
        view["descriptions"] = descriptions
        view["events"] = events
        if books:
            view["books"] = books
        temp_entry = self._winning_temperature_entry(active)
        if temp_entry is not None:
            # The temperature's own nights are counted from the first
            # night it activated (the ledger above): past that window
            # the file never re-activates, however often apply runs.
            if not rhythm._within_window(
                    first.get(temp_entry.get("file", "")),
                    temp_entry.get("nights", 1), night):
                temp_entry = None
        if temp_entry is not None:
            view["temperature"] = temp_entry
        else:
            view.pop("temperature", None)
        self.active_overrides = view
        self.state = apply_injections(self.state, self.log, view)
        self.state = apply_books(self.state, view)
        self.state = self._apply_temperature(
            view.get("temperature"), for_night)
        self.state = self._install_ambience_and_objects(newly)
        self.state = self._expire_spent_files(first, night)
        self.save()

    def resolve_weather(self, for_night: bool = True) -> None:
        """Set tonight's weather: override, live sky, cached, default.
        A failed lookup never breaks anything. Condition-aware: a
        weather file whose conditions fail tonight does not replace
        the sky. Saves. The evaluation is handed to the next
        apply_overrides on the same night (see __init__)."""
        _active, _effective, winning = self._evaluate_active()
        self._pending_evaluation = (
            self.state["night_date"], (_active, _effective, winning))
        overrides = {"weather": winning} if winning else {}
        self.state["weather"] = weather.resolve_weather(
            self.state, overrides, fetcher=self._live_fetcher(),
            for_night=for_night)
        self.save()

    def _apply_weather_nudge(self) -> None:
        """The weather's one gift to the garden: rain waters the beds
        (+1 growth, once per night at most). Freezing gives the garden
        nothing — but it never blocks tending by hand, or a long cold
        spell could starve the house with no recourse (no-open-drains)."""
        wx = self.state["weather"]
        if wx["condition"] != "rain":
            return
        if wx.get("nudge_night") == self.state["night_date"]:
            return
        self.state["garden_growth"] = min(
            rhythm.GROWTH_CAP, self.state["garden_growth"] + 1)
        wx["nudge_night"] = self.state["night_date"]
        self.save()

    def save(self) -> None:
        rhythm._validate(self.state)
        tmp = self.state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2)
        tmp.replace(self.state_path)

    def do(self, op: str, args: dict | None = None,
           considered: list[str] | None = None) -> dict:
        """Take one resident-chosen action: apply it, log it, save.
        Returns the event record. Raises HomeError if the action is not
        possible here — the state is left untouched."""
        new_state, text = rhythm.act(self.state, op, args or {},
                                     self.active_overrides)
        self.state = new_state
        record = self.log.append(
            tick=new_state["tick"], minute=new_state["minute"],
            room=new_state["room"], op=op, text=text,
            provenance=PROVENANCE_SIMULATED,
            night_date=new_state["night_date"],
            considered=considered or None)
        self.save()
        return record

    def step(self) -> dict:
        """One tick: propose 1-3, take one, log it, save. Returns the event."""
        shortlist, (op, args) = self.proposer.propose(self.state)
        considered = [f"{o}:{a.get('to', '')}" if a else o
                      for o, a in shortlist if (o, a) != (op, args)]
        return self.do(op, args, considered=considered or None)

    def run(self, max_minutes: int = NIGHT_MINUTES,
            max_steps: int = 300) -> list[dict]:
        """Run the night until the off-hours window elapses. Returns events."""
        # Weather first, then overrides: condition checks read the
        # night's sky, and the temperature effect patches it after.
        self.resolve_weather(for_night=True)
        self.apply_overrides()
        self._apply_weather_nudge()
        start = self.state["minute"]
        events = []
        steps = 0
        while (self.state["minute"] - start < max_minutes
               and steps < max_steps):
            events.append(self.step())
            steps += 1
        return events

    def digest(self) -> str:
        """Morning 'while you were out' digest: this night's events only.
        Loading a completed night rolls state forward to the next night, so
        a morning read after roll-forward falls back to the night that just
        completed — otherwise the digest is always empty by morning."""
        night = self.state["night_date"]
        events = self.log.read_night(night)
        if not events:
            events = self.log.read_night(_prev_night(night))
        return summarize_night(events,
                               weather=self.state.get("weather"))


def run_night(state_dir: str | Path, *, seed: int | None = None,
              max_minutes: int = NIGHT_MINUTES,
              weather_fetcher=None) -> str:
    """Entry point for the off-hours runner: one night, then a digest."""
    home = Home(state_dir, seed=seed, weather_fetcher=weather_fetcher)
    home.run(max_minutes=max_minutes)
    return home.digest()
