from __future__ import annotations

import argparse
import sys

from faults.catalog import load_scenarios
from faults.runner import execute, gate


def main(argv: list[str] | None = None) -> None:
    """List scenarios, run one, or mark a finished scenario run as failed."""
    parser = argparse.ArgumentParser(description="Fault catalog")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="Print scenario ids")
    execute_parser = sub.add_parser("execute", help="Apply one scenario and check the failure")
    execute_parser.add_argument("scenario")
    execute_parser.add_argument(
        "--ci",
        action="store_true",
        help="Exit 1 after the scenario so the CI run stays red",
    )
    sub.add_parser("gate", help="Exit 1 so a completed scenario stays a failed CI run")
    args = parser.parse_args(argv)
    if args.cmd == "list":
        for scenario in load_scenarios():
            print(scenario.id)
        return
    try:
        if args.cmd == "execute":
            code = execute(args.scenario)
            raise SystemExit(1 if args.ci else code)
        raise SystemExit(gate())
    except ValueError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
