"""Opt-in deterministic household rhythm for the persistent Pretorius Laboratory.

Same authoritative SQLite file and embodied pose. This has no autonomous
scheduler, no lived-memory claims and no Village-facing write interface.
Numerical physiology and resources are ENGINEER-ONLY; returned experience
is natural-language first-person text.
"""
from __future__ import annotations

import copy
import json
import uuid

from .embodied_access import EmbodiedAccess, STATIONS
from .habitat_v2 import SpatialHabitat
from .home_life import HomeLife, ROOM_LABELS
from .physics import Conflict, LabError, canonical, digest
from .store import GENESIS_HASH, _utc

RULES = "pretorius-daily-rhythm/1"
LAMPS = ("room:sanctum", "room:experiment", "room:hall", "room:kitchen",
         "room:bedroom", "room:parlor", "room:shop")
DAYLIT = frozenset(("room:sanctum", "room:kitchen",
                    "room:bedroom", "room:parlor", "room:shop"))
SUPPLY_KEYS = ("oil", "water", "fuel", "tea", "rations",
               "reagents", "spares")
SUPPLY_INITIAL = {"oil": 5, "water": 7, "fuel": 4, "tea": 3,
                  "rations": 4, "reagents": 3, "spares": 2}
CLEANABLE = ("room:sanctum", "room:kitchen", "room:shop")
STATION_WORK = "station:bench"
STATION_KITCHEN = "station:kitchen"
STATION_BEDROOM = "station:bedroom"


def initial_state() -> dict:
    return {
        "minute": 7 * 60,
        "fatigue": 2, "hunger": 2, "thirst": 2,
        "supplies": copy.deepcopy(SUPPLY_INITIAL),
        "lamps": {room: 0 for room in LAMPS},
        "cleanliness": {room: 1 for room in CLEANABLE},
        "apparatus": 2,
        "meal_ready": False,
        "experiments": 0,
        "repairs": 0,
        "chores": 0,
    }


INITIAL = initial_state()
INITIAL_DIGEST = digest(INITIAL)
OPERATIONS = {"look", "advance", "light", "extinguish", "prepare_meal",
              "eat", "brew_tea", "sleep", "rest", "clean", "experiment",
              "repair"}


def _metadata(db):
    return {k: json.loads(v) for k, v in db.execute(
        "SELECT key,value FROM rhythm_meta")}


def _set(db, key, value):
    db.execute("INSERT OR REPLACE INTO rhythm_meta(key,value) VALUES (?,?)",
               (key, canonical(value)))


def _current(db, table: str):
    return {k: json.loads(v) for k, v in db.execute(
        f"SELECT key,value FROM {table}")}


def _cursor(db, table: str, rev: int):
    if type(rev) is not int or rev < 0:
        return None
    if rev == 0:
        return GENESIS_HASH
    row = db.execute("SELECT event_hash FROM " + table + " WHERE revision=?",
                     (rev,)).fetchone()
    return row[0] if row else None


def _pose(db, body_revision: int, initial_station: str):
    if body_revision == 0:
        return initial_station
    row = db.execute("SELECT envelope FROM embodied_events WHERE revision=?",
                     (body_revision,)).fetchone()
    if row is None:
        raise Conflict("No embodied event supports claimed pose")
    return json.loads(row[0])["after_station"]


def _validate(state: dict) -> None:
    if set(state) != set(INITIAL):
        raise Conflict("Daily state schema has drifted")
    if type(state["minute"]) is not int or state["minute"] < INITIAL["minute"]:
        raise Conflict("Invalid simulated time")
    for k in ("fatigue", "hunger", "thirst"):
        if type(state[k]) is not int or not 0 <= state[k] <= 10:
            raise Conflict("Invalid simulated regulation value")
    if set(state["supplies"]) != set(SUPPLY_INITIAL) or any(
        type(v) is not int or v < 0 or v > 200
        for v in state["supplies"].values()
    ):
        raise Conflict("Invalid finite domestic supplies")
    if set(state["lamps"]) != set(LAMPS) or any(
        type(v) is not int or not 0 <= v <= 60
        for v in state["lamps"].values()
    ):
        raise Conflict("Invalid lamp duration")
    if set(state["cleanliness"]) != set(CLEANABLE) or any(
        type(v) is not int or not 0 <= v <= 3
        for v in state["cleanliness"].values()
    ):
        raise Conflict("Invalid household condition")
    if type(state["apparatus"]) is not int or not 0 <= state["apparatus"] <= 3:
        raise Conflict("Invalid apparatus condition")
    if type(state["meal_ready"]) is not bool:
        raise Conflict("Invalid prepared food state")
    if any(type(state[k]) is not int or state[k] < 0
           for k in ("experiments", "repairs", "chores")):
        raise Conflict("Invalid work counters")


def _daylight(s: dict, room: str) -> bool:
    within_day = s["minute"] % 1440
    return room in DAYLIT and 360 <= within_day < 1080


def _can_see(s: dict, room: str) -> bool:
    return _daylight(s, room) or s["lamps"][room] > 0


def _pass_time(s: dict, minutes: int) -> None:
    if type(minutes) is not int or minutes < 0:
        raise LabError("Time must be a positive whole number of minutes")
    before, after = s["minute"], s["minute"] + minutes
    s["minute"] = after
    for room in LAMPS:
        s["lamps"][room] = max(0, s["lamps"][room] - minutes)
    s["hunger"] = min(10, s["hunger"] + after // 180 - before // 180)
    s["thirst"] = min(10, s["thirst"] + after // 120 - before // 120)
    s["fatigue"] = min(10, s["fatigue"] + after // 240 - before // 240)
    for room in CLEANABLE:
        s["cleanliness"][room] = min(
            3, s["cleanliness"][room] + after // 720 - before // 720
        )


def _perceive(s: dict, station: str) -> str:
    room = STATIONS[station][0]
    sentence = f"I am in {ROOM_LABELS[room]}."
    if not _can_see(s, room):
        sentence += " It is too dark to make out the furnishings clearly."
    elif _daylight(s, room):
        sentence += " There is daylight enough to see."
    else:
        sentence += " The lamp gives me enough light to see."
    if s["fatigue"] >= 7:
        sentence += " I feel worn out and find it hard to concentrate."
    elif s["fatigue"] >= 4:
        sentence += " I am getting tired."
    if s["hunger"] >= 7:
        sentence += " I am painfully hungry."
    elif s["hunger"] >= 4:
        sentence += " I could do with something to eat."
    if s["thirst"] >= 6:
        sentence += " My mouth is dry."
    if room == "room:sanctum" and _can_see(s, room):
        if s["apparatus"] <= 1:
            sentence += " My instruments look worn and will need attention."
        if s["cleanliness"][room] >= 3:
            sentence += " My work area is cluttered."
    if room == "room:kitchen" and _can_see(s, room):
        sentence += (" There is a prepared meal to eat." if s["meal_ready"]
                     else " I have no meal prepared.")
        if s["supplies"]["rations"] <= 0:
            sentence += " I have run out of stored food provisions."
    if room == "room:shop" and _can_see(s, room):
        sentence += " The little shop is hardly fit for trade."
    return sentence


def transition(state: dict, op: str, args: dict, station: str):
    """Pure replayable transition: no raw numbers are returned to the subject."""
    if op not in OPERATIONS or station not in STATIONS or not isinstance(args, dict):
        raise LabError("Unsupported daily-life action or bodily station")
    new = copy.deepcopy(state)
    room = STATIONS[station][0]
    said = None
    if op == "look":
        if args:
            raise LabError("Looking takes no hidden metadata")
        inner = _perceive(new, station)
    elif op == "advance":
        minutes = args.get("minutes")
        if set(args) != {"minutes"} or type(minutes) is not int or (
                minutes < 10 or minutes > 240 or minutes % 10):
            raise LabError("Authorized advance must be 10-240 minutes in 10-minute steps")
        _pass_time(new, minutes)
        inner = "Time passes. " + _perceive(new, station)
    elif op == "light":
        if args:
            raise LabError("Lighting needs no arbitrary arguments")
        if new["lamps"][room] > 0 or new["supplies"]["oil"] <= 0:
            raise Conflict("Lamp already lit or oil unavailable")
        new["supplies"]["oil"] -= 1
        new["lamps"][room] = 60
        inner = "I light the lamp. The room is easier to see."
    elif op == "extinguish":
        if args or new["lamps"][room] <= 0:
            raise Conflict("The lamp is not lit or command is malformed")
        new["lamps"][room] = 0
        inner = "I put out the lamp."
    elif op == "prepare_meal":
        if args or station != STATION_KITCHEN:
            raise LabError("A meal must be prepared in the kitchen")
        if not _can_see(new, room) or new["fatigue"] >= 9:
            raise Conflict("Too dark or exhausted to prepare food")
        if new["cleanliness"][room] >= 3:
            raise Conflict("The kitchen requires cleaning before food preparation")
        if new["meal_ready"]:
            raise Conflict("A meal is already prepared")
        for item in ("water", "fuel", "rations"):
            if new["supplies"][item] == 0:
                raise Conflict("Insufficient real provisions for a meal")
            new["supplies"][item] -= 1
        _pass_time(new, 30)
        new["fatigue"] = min(10, new["fatigue"] + 1)
        new["meal_ready"] = True
        inner = "I prepare a simple meal from my stored provisions."
    elif op == "eat":
        if args or station != STATION_KITCHEN or not new["meal_ready"]:
            raise Conflict("I cannot eat an unprepared meal or eat it elsewhere")
        new["meal_ready"] = False
        _pass_time(new, 10)
        new["hunger"] = max(0, new["hunger"] - 5)
        inner = "I eat the meal I prepared. I feel less hungry."
    elif op == "brew_tea":
        if args or station != STATION_KITCHEN:
            raise LabError("Tea must be made in the kitchen")
        if not _can_see(new, room) or new["fatigue"] >= 9:
            raise Conflict("Too dark or exhausted to brew tea")
        if any(new["supplies"][k] == 0 for k in ("water", "tea", "fuel")):
            raise Conflict("I have run out of what I need for tea")
        for item in ("water", "tea", "fuel"):
            new["supplies"][item] -= 1
        _pass_time(new, 20)
        new["thirst"] = max(0, new["thirst"] - 4)
        inner = "I prepare and drink tea. The warmth is welcome."
    elif op == "sleep":
        if args or station != STATION_BEDROOM:
            raise LabError("Sleep requires the actual bedroom")
        _pass_time(new, 480)
        new["fatigue"] = max(0, new["fatigue"] - 8)
        inner = "I have slept for the night. I feel less exhausted."
    elif op == "rest":
        if args or station != STATION_BEDROOM:
            raise LabError("Rest requires the actual bedroom")
        _pass_time(new, 60)
        new["fatigue"] = max(0, new["fatigue"] - 3)
        inner = "I sit quietly and rest awhile."
    elif op == "clean":
        if args or room not in CLEANABLE or new["cleanliness"][room] <= 0:
            raise Conflict("Nothing here needs cleaning")
        if station not in (STATION_KITCHEN, STATION_WORK,
                           "station:shop:private", "station:shop:public"):
            raise LabError("The work surface is not within reach")
        if new["fatigue"] >= 9 or not _can_see(new, room):
            raise Conflict("Too exhausted or dark to clean carefully")
        _pass_time(new, 30)
        new["cleanliness"][room] = max(0, new["cleanliness"][room] - 2)
        new["fatigue"] = min(10, new["fatigue"] + 1)
        new["chores"] += 1
        inner = "I tidy and clean the area. It looks more orderly."
    elif op == "experiment":
        if args or station != STATION_WORK:
            raise LabError("Experiments must be carried out at the laboratory bench")
        if not _can_see(new, room) or new["fatigue"] >= 7 or new["thirst"] >= 8:
            raise Conflict("Insufficient light, alertness or hydration for careful work")
        if new["cleanliness"][room] >= 3 or new["apparatus"] < 1:
            raise Conflict("The workbench or apparatus needs maintenance")
        if new["supplies"]["reagents"] < 1:
            raise Conflict("Reagents are exhausted")
        new["supplies"]["reagents"] -= 1
        new["apparatus"] -= 1
        new["experiments"] += 1
        _pass_time(new, 60)
        new["fatigue"] = min(10, new["fatigue"] + 2)
        new["cleanliness"][room] = min(3, new["cleanliness"][room] + 1)
        inner = "I complete a careful experiment at my workbench. The apparatus shows wear."
    elif op == "repair":
        if args or station != STATION_WORK:
            raise LabError("Only the nearby workbench may be serviced")
        if not _can_see(new, room) or new["fatigue"] >= 8:
            raise Conflict("Too dark or exhausted to repair the apparatus")
        if new["supplies"]["spares"] <= 0 or new["apparatus"] >= 3:
            raise Conflict("I cannot repair without parts or needlessly replace sound pieces")
        new["supplies"]["spares"] -= 1
        new["apparatus"] = min(3, new["apparatus"] + 2)
        new["repairs"] += 1
        _pass_time(new, 30)
        new["fatigue"] = min(10, new["fatigue"] + 1)
        inner = "I replace the worn component in my apparatus."
    else:
        raise LabError("Unsupported daily operation")
    _validate(new)
    return new, {"inner": inner, "spoken_aloud": said}


class DailyRhythm:
    """Bounded day-to-day experimental life, under local host authority."""

    def __init__(self, habitat: SpatialHabitat):
        self.habitat = habitat
        self.home = HomeLife(habitat)

    def initialize(self, *, initial_state: dict | None = None, parent=None):
        self.home.verify()
        seed = copy.deepcopy(initial_state if initial_state is not None else INITIAL)
        _validate(seed)
        with self.habitat._open() as db:
            db.execute("BEGIN IMMEDIATE")
            host = SpatialHabitat._metadata(db)
            body = _current(db, "embodied_meta")
            home = _current(db, "domestic_meta")
            db.execute("""CREATE TABLE IF NOT EXISTS rhythm_meta(
                key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS rhythm_events(
                revision INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                envelope TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE)""")
            old = _metadata(db)
            if not old:
                vals = {
                    "rules": RULES, "lab_id": host["lab_id"],
                    "world_epoch": host["world_epoch"], "home_id": host["home_id"],
                    "genesis": seed, "genesis_digest": digest(seed),
                    "parent": parent, "revision": 0, "head_hash": GENESIS_HASH,
                    "state": seed, "body_at_install": body["revision"],
                    "home_at_install": home["revision"],
                    "initial_station": body["station"],
                }
                for k, value in vals.items():
                    _set(db, k, value)
            elif (old["rules"], old["lab_id"], old["world_epoch"]) != (
                    RULES, host["lab_id"], host["world_epoch"]):
                raise Conflict("Daily-life state is not from the current Laboratory")
            elif initial_state is not None or parent is not None:
                raise Conflict("An inhabited day-to-day state cannot be reseeded")
        return self.verify()

    def verify(self):
        self.home.verify()
        bodyproof = EmbodiedAccess(self.habitat).verify()
        world = self.habitat.verify()
        with self.habitat._open() as db:
            if db.execute("SELECT name FROM sqlite_master WHERE name='rhythm_meta'").fetchone() is None:
                raise LabError("Daily-life addon has not been initialized")
            m = _metadata(db)
            if (m["rules"], m["lab_id"], m["world_epoch"], m["home_id"]) != (
                    RULES, world["lab_id"], world["world_epoch"], world["home_id"]):
                raise Conflict("Daily life uses the wrong authority")
            if digest(m["genesis"]) != m["genesis_digest"]:
                raise Conflict("Daily-life genesis modified without source")
            if m["parent"] is None and m["genesis_digest"] != INITIAL_DIGEST:
                raise Conflict("Changed canonical daily-life genesis")
            seed = copy.deepcopy(m["genesis"])
            _validate(seed)
            home = _current(db, "domestic_meta")
            if (m["home_at_install"] > home["revision"] or
                    m["body_at_install"] > bodyproof["revision"]):
                raise Conflict("Daily-life origin cannot postdate its current world")
            prev, rev = GENESIS_HASH, 0
            last_body, last_home = m["body_at_install"], m["home_at_install"]
            for n, event_id, encoded, prior, h in db.execute(
                "SELECT revision,event_id,envelope,prev_hash,event_hash "
                "FROM rhythm_events ORDER BY revision"):
                e = json.loads(encoded)
                if (n != rev + 1 or event_id != e["event_id"] or
                        n != e["revision"] or prior != prev or
                        e["previous_hash"] != prev or h != digest(e)):
                    raise Conflict("Corrupted day-to-day event history")
                if (e["lab_id"], e["world_epoch"], e["home_id"]) != (
                        m["lab_id"], m["world_epoch"], m["home_id"]):
                    raise Conflict("Daily-life event belongs to another world")
                br, hr = e["body_revision"], e["home_revision"]
                if (type(br) is not int or type(hr) is not int or
                        br < last_body or hr < last_home or
                        br > bodyproof["revision"] or hr > home["revision"]):
                    raise Conflict("Day-to-day event has invalid source revisions")
                if (_cursor(db, "embodied_events", br) != e["body_cursor"] or
                        _cursor(db, "domestic_events", hr) != e["home_cursor"]):
                    raise Conflict("Daily-life event cites unverifiable body/home receipts")
                station = _pose(db, br, m["initial_station"])
                if e["station"] != station or e["before_digest"] != digest(seed):
                    raise Conflict("Experience is not supported by the recorded pose/state")
                result, subjective = transition(seed, e["op"], e["args"], station)
                if (e["subject"] != subjective or
                        e["after_digest"] != digest(result)):
                    raise Conflict("Daily-life consequences or first-person result altered")
                seed, prev, rev = result, h, n
                last_body, last_home = br, hr
            if (m["state"], m["head_hash"], m["revision"]) != (seed, prev, rev):
                raise Conflict("Materialized daily state cannot be replayed")
            return {"verified": True, "revision": rev, "head_hash": prev,
                    "world_epoch": m["world_epoch"], "minute": seed["minute"],
                    "body_revision": bodyproof["revision"],
                    "home_revision": home["revision"]}

    def act(self, op: str, *, event_id: str | None = None, **args):
        self.verify()
        self.home._catch_up()
        event_id = event_id or uuid.uuid4().hex
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
            raise LabError("Invalid event ID")
        with self.habitat._open() as db:
            db.execute("BEGIN IMMEDIATE")
            m = _metadata(db)
            body = _current(db, "embodied_meta")
            home = _current(db, "domestic_meta")
            prior = db.execute(
                "SELECT envelope,event_hash FROM rhythm_events WHERE event_id=?",
                (event_id,)).fetchone()
            if prior:
                e = json.loads(prior[0])
                if e["op"] != op or e["args"] != args:
                    raise Conflict("Event ID cannot be reused for a different action")
                return copy.deepcopy(e["subject"])
            after, subjective = transition(m["state"], op, args, body["station"])
            event = {
                "event_id": event_id, "revision": m["revision"] + 1,
                "op": op, "args": args,
                "lab_id": m["lab_id"], "home_id": m["home_id"],
                "world_epoch": m["world_epoch"],
                "body_revision": body["revision"], "body_cursor": body["head_hash"],
                "home_revision": home["revision"], "home_cursor": home["head_hash"],
                "station": body["station"],
                "before_digest": digest(m["state"]),
                "after_digest": digest(after), "subject": subjective,
                "previous_hash": m["head_hash"], "utc": _utc(),
            }
            h = digest(event)
            db.execute("INSERT INTO rhythm_events VALUES (?,?,?,?,?)",
                       (event["revision"], event_id, canonical(event),
                        m["head_hash"], h))
            _set(db, "state", after)
            _set(db, "revision", event["revision"])
            _set(db, "head_hash", h)
            return copy.deepcopy(subjective)

    def look(self, *, event_id=None):
        return self.act("look", event_id=event_id)

    def advance(self, minutes: int, *, event_id=None):
        return self.act("advance", event_id=event_id, minutes=minutes)

    def audit_state(self):
        """Engineer-only state; never supply this to Pretorius's frame."""
        self.verify()
        with self.habitat._open() as db:
            return copy.deepcopy(_metadata(db)["state"])

    def _fork_into(self, clone: SpatialHabitat) -> None:
        """Copy present condition, not the parent's prior life events."""
        self.verify()
        with self.habitat._open() as db:
            m = _metadata(db)
            copied = copy.deepcopy(m["state"])
            parent = {"lab_id": m["lab_id"], "world_epoch": m["world_epoch"],
                      "revision": m["revision"], "head_hash": m["head_hash"],
                      "status": "cloned_state_not_subject_experience"}
        DailyRhythm(clone).initialize(initial_state=copied, parent=parent)
