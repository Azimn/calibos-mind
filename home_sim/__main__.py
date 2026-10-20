"""Run one off-hours night of the home simulation, then print the digest.

Usage:
    python -m home_sim [--state-dir DIR] [--seed N] [--minutes M]
                       [--off-hours-only] [--digest-only]

--off-hours-only: do nothing unless it is 23:00-07:00 America/Chicago.
--digest-only: print the morning digest without running any ticks.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .tick import Home, in_off_hours, run_night


def main() -> None:
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
    print(run_night(state_dir, seed=args.seed, max_minutes=args.minutes))


if __name__ == "__main__":
    main()
