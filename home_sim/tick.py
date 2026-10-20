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

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import rhythm
from .events import EventLog, PROVENANCE_SIMULATED, summarize_night
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
            w = 0.8
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


class Home:
    """One inhabitant's off-hours home. State lives in a directory
    (state.json + events.jsonl); nothing else on the machine is touched."""

    def __init__(self, state_dir: str | Path,
                 night_date: str | None = None, seed: int | None = None):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "state.json"
        self.log = EventLog(self.state_dir)
        self.proposer = Proposer(seed)
        if self.state_path.exists():
            with open(self.state_path, encoding="utf-8") as fh:
                self.state = json.load(fh)
            rhythm._validate(self.state)
            if _night_complete(self.state):
                # A finished night never replays as daytime: roll forward.
                self.state = rhythm.initial_state(
                    _next_night(self.state["night_date"]))
                self.save()
        else:
            self.state = rhythm.initial_state(night_date or night_date_for())
            self.save()

    def save(self) -> None:
        rhythm._validate(self.state)
        tmp = self.state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2)
        tmp.replace(self.state_path)

    def step(self) -> dict:
        """One tick: propose 1-3, take one, log it, save. Returns the event."""
        shortlist, (op, args) = self.proposer.propose(self.state)
        new_state, text = rhythm.act(self.state, op, args)
        considered = [f"{o}:{a.get('to', '')}" if a else o
                      for o, a in shortlist if (o, a) != (op, args)]
        self.state = new_state
        record = self.log.append(
            tick=new_state["tick"], minute=new_state["minute"],
            room=new_state["room"], op=op, text=text,
            provenance=PROVENANCE_SIMULATED,
            night_date=new_state["night_date"],
            considered=considered or None)
        self.save()
        return record

    def run(self, max_minutes: int = NIGHT_MINUTES,
            max_steps: int = 300) -> list[dict]:
        """Run the night until the off-hours window elapses. Returns events."""
        start = self.state["minute"]
        events = []
        steps = 0
        while (self.state["minute"] - start < max_minutes
               and steps < max_steps):
            events.append(self.step())
            steps += 1
        return events

    def digest(self) -> str:
        """Morning 'while you were out' digest: this night's events only."""
        return summarize_night(
            self.log.read_night(self.state["night_date"]))


def run_night(state_dir: str | Path, *, seed: int | None = None,
              max_minutes: int = NIGHT_MINUTES) -> str:
    """Entry point for the off-hours runner: one night, then a digest."""
    home = Home(state_dir, seed=seed)
    home.run(max_minutes=max_minutes)
    return home.digest()
