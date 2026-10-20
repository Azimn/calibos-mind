"""Calibos's home simulation — off-hours life, somewhere.

Standalone furniture, not cognition. Hard boundaries (non-negotiable):
- stdlib only, Python 3.11+.
- NEVER touches the thought database, the prompt inbox, dream fragments,
  salience sidecars, or ablation records. No imports from the mind package
  anywhere in this package.
- Writes only: <state_dir>/state.json and <state_dir>/events.jsonl.
- Events are labeled `simulated` at write time; world imports `world`.
  The wake briefing may digest them; it must never import them as memories.

Spec: research/specs/2026-10-19-home-simulation-v0.2.md
Substrate: Pretorius-Laboratory's daily_rhythm.py need mechanics (ported,
not copied blindly — see rhythm.py for divergences and reasons).
"""
from __future__ import annotations

from .events import EventLog, summarize_night
from .rooms import ROOMS, ROOM_LABELS
from .rhythm import (
    HomeConflict,
    HomeError,
    act,
    initial_state,
)
from .tick import Home, Proposer, in_off_hours, night_date_for, run_night
from .traversal import (
    IDENTITY_HANDLE,
    assert_no_leakage,
    import_world_event,
    snapshot,
)

__version__ = "0.2a"
__all__ = [
    "Home", "Proposer", "EventLog",
    "act", "initial_state", "HomeError", "HomeConflict",
    "snapshot", "import_world_event", "assert_no_leakage",
    "summarize_night", "in_off_hours", "night_date_for", "run_night",
    "IDENTITY_HANDLE", "ROOMS", "ROOM_LABELS",
]
