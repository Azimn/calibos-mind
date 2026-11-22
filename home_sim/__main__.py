"""Run one off-hours night of the home simulation, then print the digest.

Usage:
    python -m home_sim [--state-dir DIR] [--seed N] [--minutes M]
                       [--off-hours-only] [--digest-only]

    python -m home_sim visit [--state-dir DIR] [--seed N]
                             [--script FILE] [--log FILE]

--off-hours-only: do nothing unless it is 23:00-07:00 America/Chicago.
--digest-only: print the digest without running any ticks.

visit: a resident session. Each step shows the scene plus the house's
shortlist of up to three suggestions, and the resident picks one by
number or types an action of their own. The visit ends on `leave`.
--script runs a list of commands from a file instead of prompting.
There is no fixed visit length and no real-time correlation.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .tick import Home, in_off_hours, run_night
from .weather import open_meteo_fetcher


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "visit":
        _visit(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description="Calibos home simulation")
    parser.add_argument("--state-dir", default=None,
                        help="state directory (default: <package>/state)")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for the putz proposer")
    parser.add_argument("--minutes", type=int, default=8 * 60,
                        help="simulated minutes to run (default 480)")
    parser.add_argument("--off-hours-only", action="store_true",
                        help="only run during 23:00-07:00 America/Chicago")
    parser.add_argument("--digest-only", action="store_true",
                        help="print the digest without running ticks")
    args = parser.parse_args()

    state_dir = Path(args.state_dir) if args.state_dir else \
        Path(__file__).resolve().parent / "state"

    if args.digest_only:
        if not (state_dir / "state.json").exists():
            print("(no off-hours activity recorded — simulated)")
            return
        print(Home(state_dir, seed=args.seed).digest())
        return
    if args.off_hours_only and not in_off_hours():
        print("Daytime — the inhabitant is away. Nothing simulated.")
        return
    print(run_night(state_dir, seed=args.seed, max_minutes=args.minutes,
                    weather_fetcher=open_meteo_fetcher))


def _visit(argv: list[str]) -> None:
    from .visit import run_visit

    parser = argparse.ArgumentParser(description="Resident visit")
    parser.add_argument("--state-dir", default=None,
                        help="state directory (default: <package>/state)")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for the proposer")
    parser.add_argument("--script", default=None,
                        help="file of commands, one per line, instead of "
                             "prompting")
    parser.add_argument("--log", default=None,
                        help="also write the visit log to this file")
    args = parser.parse_args(argv)

    state_dir = Path(args.state_dir) if args.state_dir else \
        Path(__file__).resolve().parent / "state"
    home = Home(state_dir, seed=args.seed,
                weather_fetcher=open_meteo_fetcher)
    commands = None
    if args.script:
        commands = Path(args.script).read_text(
            encoding="utf-8").splitlines()
    log_file = None
    if args.log:
        log_file = open(args.log, "w", encoding="utf-8")

    class Tee:
        def __init__(self, *streams):
            self.streams = streams

        def write(self, text):
            for stream in self.streams:
                stream.write(text)

        def flush(self):
            for stream in self.streams:
                stream.flush()

    out = Tee(sys.stdout, log_file) if log_file else None
    try:
        run_visit(home, commands, out=out)
    finally:
        if log_file:
            log_file.close()


if __name__ == "__main__":
    main()
