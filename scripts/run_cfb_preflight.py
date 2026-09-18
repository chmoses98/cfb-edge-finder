#!/usr/bin/env python3
"""RUN CFB preflight: is the catalog fresh, and which games may be used?

    python scripts/run_cfb_preflight.py
    python scripts/run_cfb_preflight.py --game 26SEP19UGAARK
    python scripts/run_cfb_preflight.py --last-successful-run-at 2026-09-18T00:23:11Z

This is the one command a RUN CFB session runs before anything else. It
answers two questions that must not be guessed at:

  1. CATALOG FRESH or CATALOG STALE
  2. which games may be handicapped, and which must be refused

*** WHY FRESHNESS NEEDS THE RUN HISTORY ***
The catalog does not commit when the market surface has not changed, so
the committed `captured_at` is the timestamp of the last CHANGE, not the
last OBSERVATION. An old timestamp does not mean the collector stopped,
and a recent one does not prove it is alive. Liveness comes from the last
SUCCESSFUL production run; the artifact corroborates it.

Pass the last successful run's completion time with
`--last-successful-run-at`. Get it from:

    GitHub -> Actions -> "Kalshi CFB Market Catalog" -> latest successful run

or via the API:

    /repos/chmoses98/cfb-edge-finder/actions/workflows/kalshi-market-catalog.yml/runs
      ?status=success&per_page=1        -> .workflow_runs[0].updated_at

Without it, this script reports STALE and says why, rather than quietly
falling back to the timestamp and calling it fresh.

*** WHAT THIS SCRIPT WILL NEVER DO ***
It never projects a game, prices a fair value, or recommends a wager. It
reports what was captured and whether it can be trusted. The handicap
happens outside this repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cfb_edge_finder.catalog.consumer import assess_game, assess_slate
from cfb_edge_finder.catalog.freshness import assess_freshness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live-dir", default="data/live")
    parser.add_argument(
        "--last-successful-run-at",
        default=None,
        help="Completion time (RFC3339) of the most recent SUCCESSFUL Kalshi CFB Market Catalog run on main",
    )
    parser.add_argument(
        "--last-run-fingerprint",
        default=None,
        help="content_fingerprint that run reported; matching the published one proves a re-observation",
    )
    parser.add_argument("--game", action="append", default=[], help="Game key(s) to gate explicitly")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args(argv)

    live = Path(args.live_dir)
    index_path = live / "cfb_market_catalog.json"
    status_path = live / "cfb_catalog_status.json"
    if not index_path.is_file():
        print(f"CATALOG STALE: no index at {index_path}", file=sys.stderr)
        return 2

    catalog = json.loads(index_path.read_text(encoding="utf-8"))
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}

    freshness = assess_freshness(
        last_successful_run_at=args.last_successful_run_at,
        published_status=status,
        last_run_fingerprint=args.last_run_fingerprint,
    )
    slate = assess_slate(catalog)

    requested = [assess_game(_find(catalog, key), game_key=key) for key in args.game]

    if args.json:
        print(
            json.dumps(
                {
                    "freshness": freshness.freshness.value,
                    "fresh": freshness.is_fresh,
                    "freshness_reason": freshness.reason,
                    "minutes_since_last_success": freshness.minutes_since_last_success,
                    "fingerprint_matched": freshness.fingerprint_matched,
                    "captured_at": slate.captured_at,
                    "capture_complete": slate.capture_complete,
                    "usable_games": len(slate.usable),
                    "unavailable_games": len(slate.unavailable),
                    "unavailable_game_keys": [v.game_key for v in slate.unavailable],
                    "requested": [
                        {
                            "game_key": v.game_key,
                            "may_handicap": v.may_handicap,
                            "usability": str(v.usability),
                            "reason": v.reason,
                            "markets_file": v.markets_file,
                            "market_count": v.market_count,
                            "explain": v.explain(),
                        }
                        for v in requested
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(freshness.explain())
        print()
        print(slate.summary())
        if requested:
            print("\nrequested games:")
            for verdict in requested:
                print(f"  {verdict.explain()}")
                if verdict.may_handicap:
                    print(f"      open: {args.live_dir}/{verdict.markets_file}")

    # Exit code is the machine-readable verdict: 0 only when the catalog is
    # fresh AND every explicitly requested game may be used.
    if not freshness.is_fresh:
        return 2
    if any(not v.may_handicap for v in requested):
        return 3
    return 0


def _find(catalog: dict, game_key: str) -> dict | None:
    for entry in catalog.get("games") or []:
        if entry.get("game_key") == game_key:
            return entry
    return None


if __name__ == "__main__":
    sys.exit(main())
