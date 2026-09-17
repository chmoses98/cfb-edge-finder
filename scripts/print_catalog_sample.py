#!/usr/bin/env python3
"""Print a compact, human-readable slice of a built catalog.

    python scripts/print_catalog_sample.py --catalog data/live/cfb_market_catalog.json

Deliberately COMPACT. This exists to be read in an Actions job log, and a
job log is returned as a bounded tail -- an earlier version of the audit
workflow dumped several kilobytes of raw game JSON here and pushed the
audit's own verdict out of the window, making the result of the audit
unreadable in the place people actually read results.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/live/cfb_market_catalog.json")
    parser.add_argument("--games", type=int, default=8)
    parser.add_argument("--markets", type=int, default=5)
    args = parser.parse_args(argv)

    path = Path(args.catalog)
    if not path.is_file():
        print(f"no catalog at {path}")
        return 0
    catalog = json.loads(path.read_text(encoding="utf-8"))
    games = sorted(catalog.get("games") or [], key=lambda g: -g.get("market_count", 0))

    print(f"\n=== richest {min(args.games, len(games))} of {len(games)} games ===")
    for game in games[: args.games]:
        identity = game.get("identity") or {}
        complete = (game.get("completeness") or {}).get("native_game_markets_complete")
        print(
            f"  {game['game_key']:22} {game.get('market_count', 0):5} markets  "
            f"{len(game.get('events') or []):3} events  tier={str(identity.get('tier')):8} "
            f"div={str(identity.get('division')):5} complete={complete}  {str(game.get('title'))[:38]}"
        )
    if games:
        print(f"\n=== thinnest game: {games[-1]['game_key']} ({games[-1].get('market_count')} markets) ===")

    if games:
        sample = games[0]
        print(f"\n=== {sample['game_key']}: first {args.markets} contracts as a reader sees them ===")
        for market in (sample.get("markets") or [])[: args.markets]:
            mechanics = market.get("mechanics") or {}
            print(
                f"  {market['market_ticker']:44} {str(market.get('family')):22} "
                f"strike={str(market.get('floor_strike')):7} "
                f"yes {market.get('yes_bid')}/{market.get('yes_ask')} "
                f"size {market.get('yes_bid_size')}/{market.get('yes_ask_size')} "
                f"p={mechanics.get('implied_probability_yes_mid')}"
            )
            print(f"      {str(market.get('title'))[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
