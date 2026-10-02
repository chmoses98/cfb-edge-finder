#!/usr/bin/env python3
"""Summarize a catalog capture into the Actions job log.

    python scripts/report_catalog_status.py [--status-file data/live/cfb_catalog_status.json]

*** WHY A FILE AND NOT AN INLINE SNIPPET ***
This began as a heredoc nested inside a shell `if` inside a YAML block
scalar -- three quoting layers deep, unrunnable outside CI, and untestable.
A script on disk has none of that: it runs locally, it is covered by tests,
and a change to it cannot be broken by YAML indentation rules.

*** WHY IT NEVER FAILS THE RUN ***
An INCOMPLETE capture is published WITH its diagnostics and reported here
as a warning annotation, not an error. Failing a scheduled job for a
transient Kalshi blip is what made the retired research collector's
notifications worthless -- a red run per reset socket trained everyone to
ignore all of them. The next tick recovers on its own. Only a genuinely
unusable capture (zero games, or the builder crashing) fails, and that is
decided by the builder's own exit code, not here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _summary(line: str) -> None:
    """Append one line to the Actions step summary, when there is one."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"- {line}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-file", default="data/live/cfb_catalog_status.json")
    parser.add_argument(
        "--build-outcome",
        default=os.environ.get("CATALOG_BUILD_OUTCOME", ""),
        help="the build step's outcome; anything but success means the status file on disk is stale",
    )
    args = parser.parse_args(argv)

    if args.build_outcome and args.build_outcome != "success":
        # The builder exits before writing a status file when it fails, so
        # what is on disk is the PREVIOUSLY PUBLISHED status. Reporting it
        # would print "catalog complete" on a red run. The build step has
        # already failed the job; this only keeps the summary honest.
        print(f"catalog build {args.build_outcome}; the status file on disk is from an earlier run")
        _summary(f"**Kalshi catalog FAILED**: build step {args.build_outcome}; nothing new was published")
        return 0

    path = Path(args.status_file)
    if not path.is_file():
        print(f"no status file at {path}; nothing to report")
        return 0

    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"::warning title=Catalog status unreadable::{type(exc).__name__}: {exc}")
        return 0

    games = status.get("physical_games")
    markets = status.get("markets")
    # health_state is the builder's machine-readable verdict; a status file
    # written before it existed falls back to the flag it was derived from.
    state = status.get("health_state") or ("HEALTHY" if status.get("capture_complete") else "DEGRADED")
    if not status.get("capture_complete", False):
        print(
            f"::warning title=Catalog capture incomplete::"
            f"{status.get('games_incomplete_count')} game(s) incomplete; published with "
            f"diagnostics. games={games} markets={markets}"
        )
        _summary(
            f"**Kalshi catalog {state}**: {status.get('games_incomplete_count')} game(s) incomplete; "
            f"published with diagnostics ({games} games / {markets} markets)"
        )
    else:
        print(f"catalog complete: {games} games / {markets} markets in {status.get('elapsed_seconds')}s")
        _summary(f"Kalshi catalog {state}: {games} games / {markets} markets")

    print(f"  events                 {status.get('events')}")
    print(f"  unknown-family markets {status.get('unknown_family_markets')}")
    print(f"  requests made          {status.get('requests_made')}")
    print(f"  content fingerprint    {str(status.get('content_fingerprint'))[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
