#!/usr/bin/env python3
"""Deterministic Kalshi price arithmetic for an ANALYST'S probability and uncertainty range.

Not a model and not a recommendation: you supply the probability (of the side you would BUY) and a low/high range, the
script supplies the all-in cost, the fee-inclusive break-even and the bet-up-to ceiling. Sizing is ADVISORY and yours:
pass
--units to check a stake you propose against the owner's mechanical limits; with no --units no stake is assigned.

    # a ticker from a packet's markets.json: ask, series and quoted size are read from it
    python scripts/cfb_price_limits.py --game 26NOV06UNMNEV --ticker KXNCAAFTEAMTOTAL-26NOV06UNMNEV-NEV20 \
        --side NO --low 0.74 --point 0.79 --high 0.84

    # or fully manual
    python scripts/cfb_price_limits.py --side YES --ask 0.29 --series KXNCAAFTEAMTOTAL --low 0.36 --point 0.41 --high
    0.46

Prices are CAPTURED quotes. A stated range is an opinion, so the result is an ANALYST RECOMMENDATION WITH STATED
UNCERTAINTY and ``qualified_positive_ev`` is always false: only a probability calibrated on settled outcomes could be
called a validated positive-EV wager, and none exists. Nothing here places an order or touches the approval gates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.research_packet import pricing  # noqa: E402


def _contract(game: str, ticker: str) -> dict:
    gdir = ROOT / "data/research_packets/games" / game
    for path in sorted(gdir.glob("markets*.json")):
        for c in json.loads(path.read_text()).get("contracts", []):
            if c["ticker"] == ticker:
                return c
    raise SystemExit(f"ticker {ticker} not found in {gdir}/markets*.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game")
    ap.add_argument("--ticker")
    ap.add_argument("--side", required=True, choices=["YES", "NO"])
    ap.add_argument("--ask", type=float)
    ap.add_argument("--series")
    ap.add_argument("--quoted-contracts", type=float)
    ap.add_argument("--low", type=float, required=True, help="lower bound of P(the side you BUY wins)")
    ap.add_argument("--point", type=float, required=True)
    ap.add_argument("--high", type=float, required=True)
    ap.add_argument("--required-margin", type=float, default=0.0)
    ap.add_argument("--units", type=float, help="a stake YOU propose, in units (1U = $75), to be checked, not chosen")
    args = ap.parse_args(argv)

    ask, series, size = args.ask, args.series, args.quoted_contracts
    if args.ticker:
        if not args.game:
            raise SystemExit("--ticker needs --game")
        c = _contract(args.game, args.ticker)
        block = c["yes" if args.side == "YES" else "no"]
        ask, size, series = block["ask"], block["ask_size"], args.ticker.split("-", 1)[0]
        if not block.get("exec"):
            print(
                f"WARNING: {args.side} had no positive quoted size at capture; this price is not an offer",
                file=sys.stderr,
            )
    if ask is None or series is None:
        raise SystemExit("need --ask and --series (or --game and --ticker)")
    out = pricing.evaluate(
        side=args.side,
        ask=ask,
        series_ticker=series,
        probability_low=args.low,
        probability_point=args.point,
        probability_high=args.high,
        quoted_contracts=size,
        proposed_units=args.units,
        required_margin=args.required_margin,
    )
    out["price_provenance"] = "CAPTURED_NOT_LIVE_VERIFIED"
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
