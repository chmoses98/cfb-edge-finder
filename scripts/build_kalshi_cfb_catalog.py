#!/usr/bin/env python3
"""Build the canonical Kalshi CFB market catalog.

    python scripts/build_kalshi_cfb_catalog.py --out-dir data/live

Answers one question, reliably: "For this physical college-football game,
what EXACTLY can I bet on Kalshi right now?"

*** NO CREDENTIALS OF ANY KIND ***
Kalshi's market-data endpoints are public reads, and game identity comes
from Kalshi's own `football_game` milestones -- so this script needs no
Kalshi key and, unlike every live path that preceded it, NO CFBD_API_KEY.
There is no order/portfolio endpoint anywhere in the call graph.

*** NO MODEL, ANYWHERE ***
This script imports nothing from cfb_edge_finder.modeling,
.projections, .recommendation, .decision or .sizing. That is asserted by
tests/test_catalog_no_model_imports.py rather than left to convention,
because the whole point of the pivot is that the model is off the live
path.

EXIT CODES
  0  capture complete
  0  capture incomplete, but written and clearly labelled INCOMPLETE
     (with --fail-on-incomplete, this becomes 2)
  1  capture failed outright -- nothing usable was produced
A partial capture is published WITH its diagnostics rather than
suppressed: a handicapper is better served by "here are 40 of 41 games,
and here is the one that failed" than by silence. It is never published as
if it were complete.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from cfb_edge_finder.catalog.artifacts import (
    build_catalog,
    build_flat_index,
    catalog_content_fingerprint,
    write_json,
)
from cfb_edge_finder.catalog.discovery import MarketDiscovery
from cfb_edge_finder.data.kalshi_client import KalshiClient


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default="data/live", help="Directory for the catalog artifacts")
    parser.add_argument(
        "--horizon-days",
        type=float,
        default=10.0,
        help=(
            "Only publish games kicking off within this many days. Bounds the request cost: the "
            "exchange carries ~239 live CFB games at once, and a full-season sweep is neither needed "
            "for a slate nor affordable on a schedule. Pass -1 for no horizon."
        ),
    )
    parser.add_argument(
        "--no-series-reconciliation",
        action="store_true",
        help="Skip the independent series sweep (faster, but a missed event becomes invisible again)",
    )
    parser.add_argument(
        "--no-multivariate", action="store_true", help="Skip the combo-eligibility pass"
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Embed each market's raw Kalshi payload in the flat index (larger, fully lossless)",
    )
    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help="Exit 2 when the capture is incomplete (for a gate that must not accept a partial menu)",
    )
    parser.add_argument(
        "--print-summary", action="store_true", help="Print a human-readable summary to stdout"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    horizon = None if args.horizon_days is not None and args.horizon_days < 0 else args.horizon_days

    client = KalshiClient()
    discovery = MarketDiscovery(client.get_json)

    started = datetime.now(UTC)
    try:
        run = discovery.run(
            as_of=started,
            horizon_days=horizon,
            include_series_reconciliation=not args.no_series_reconciliation,
            include_multivariate=not args.no_multivariate,
        )
    except Exception as exc:  # noqa: BLE001
        # A total failure must be loud and must NOT overwrite a good
        # catalog with an empty one -- a stale-but-complete menu beats a
        # fresh-but-empty one, and "0 markets" must never be publishable
        # as a result of an exception.
        print(f"FATAL: discovery raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not run.games:
        print(
            "FATAL: discovery produced ZERO physical games. Refusing to publish an empty catalog over "
            "a possibly-good one -- an empty result here is far more likely to be a filter/API change "
            "than a genuinely empty college-football slate.",
            file=sys.stderr,
        )
        for error in run.errors[:20]:
            print(f"  error: {error}", file=sys.stderr)
        return 1

    catalog = build_catalog(run, include_raw=False)
    flat = build_flat_index(run, include_raw=args.include_raw)
    fingerprint = catalog_content_fingerprint(catalog)
    catalog["capture"]["content_fingerprint"] = fingerprint

    out_dir = Path(args.out_dir)
    catalog_bytes = write_json(out_dir / "cfb_market_catalog.json", catalog)
    flat_bytes = write_json(out_dir / "cfb_markets_flat.json", flat)

    status_path = out_dir / "cfb_catalog_status.json"
    write_json(
        status_path,
        {
            "captured_at": catalog["capture"]["captured_at"],
            "content_fingerprint": fingerprint,
            "capture_complete": catalog["completeness"]["capture_complete"],
            "physical_games": catalog["totals"]["physical_games"],
            "events": catalog["totals"]["events"],
            "markets": catalog["totals"]["markets"],
            "unknown_family_markets": catalog["totals"]["unknown_family_markets"],
            "games_incomplete_count": catalog["completeness"]["games_incomplete_count"],
            "requests_made": catalog["discovery"]["requests_made"],
            "elapsed_seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
            "catalog_bytes": catalog_bytes,
            "flat_bytes": flat_bytes,
        },
    )

    totals = catalog["totals"]
    completeness = catalog["completeness"]
    verdict = "COMPLETE" if completeness["capture_complete"] else "INCOMPLETE"
    print(
        f"catalog {verdict}: {totals['physical_games']} games / {totals['events']} events / "
        f"{totals['markets']} markets / {totals['unknown_family_markets']} unknown-family "
        f"({catalog['discovery']['requests_made']} requests, "
        f"{round((datetime.now(UTC) - started).total_seconds(), 1)}s) fingerprint={fingerprint[:12]}"
    )

    if args.print_summary:
        print("\nfamily distribution:")
        for family, count in totals["family_distribution"].items():
            print(f"  {family:28} {count:6}")
        print("\nstatus distribution:")
        for status, count in totals["status_distribution"].items():
            print(f"  {status:28} {count:6}")
        if completeness["games_incomplete"]:
            print(f"\nINCOMPLETE games ({completeness['games_incomplete_count']}):")
            for key in completeness["games_incomplete"][:25]:
                print(f"  {key}")
        if catalog["discovery"]["errors"]:
            print("\nerrors:")
            for error in catalog["discovery"]["errors"][:25]:
                print(f"  {error}")
        print("\nrichest games:")
        for game in sorted(catalog["games"], key=lambda g: -g["market_count"])[:10]:
            print(f"  {game['game_key']:22} {game['market_count']:5} markets  {str(game['title'])[:44]}")

    if args.fail_on_incomplete and not completeness["capture_complete"]:
        print("exiting 2: capture incomplete and --fail-on-incomplete was set", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
