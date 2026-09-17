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
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-file", default="data/live/cfb_catalog_status.json")
    args = parser.parse_args(argv)

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
    if not status.get("capture_complete", False):
        print(
            f"::warning title=Catalog capture incomplete::"
            f"{status.get('games_incomplete_count')} game(s) incomplete; published with "
            f"diagnostics. games={games} markets={markets}"
        )
    else:
        print(f"catalog complete: {games} games / {markets} markets in {status.get('elapsed_seconds')}s")

    print(f"  events                 {status.get('events')}")
    print(f"  unknown-family markets {status.get('unknown_family_markets')}")
    print(f"  requests made          {status.get('requests_made')}")
    print(f"  content fingerprint    {str(status.get('content_fingerprint'))[:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
