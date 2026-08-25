#!/usr/bin/env python3
"""Milestone D: prints the COMPLETE, unfiltered raw JSON for a small,
fixed set of real Kalshi CFB markets, to settle exactly which fields
(especially yes_bid/yes_ask/no_bid/no_ask/volume/open_interest) the
market-detail endpoint actually returns.

    python scripts/validate_kalshi_market_detail_live.py

Follow-up to scripts/validate_kalshi_cfb_live.py's discovery runs: those
found real KXNCAAFSPREAD/KXNCAAFTOTAL markets but the per-market summary
printed there was field-filtered against a guessed field list, so it
could not distinguish "field absent from the API" from "field present but
not in my guessed list." This script prints raw, complete JSON (still
bounded to a handful of tickers -- not a full sweep) so that question is
answered by direct, unfiltered evidence.
"""

from __future__ import annotations

import json

import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT_SECONDS = 20.0

# Real tickers observed in prior live discovery runs.
PROBE_TICKERS = (
    "KXNCAAFSPREAD-26AUG29SUUMONT-SUU5",
    "KXNCAAFTOTAL-26AUG29SUUMONT-81",
    # KXNCAAFGAME (winner/moneyline) ticker observed live in the first
    # successful snapshot capture (job 97710429233) -- probed here to
    # settle its rules_primary phrasing, since extract_matchup_from_
    # rules_primary() has only been confirmed against SPREAD/TOTAL text
    # so far and every KXNCAAFGAME market in that capture landed in
    # PARSE_UNRESOLVED.
    "KXNCAAFGAME-26SEP19CORCOLG-COR",
)
PROBE_EVENT_TICKERS = ("KXNCAAFSPREAD-26AUG29SUUMONT",)
PROBE_SERIES_TICKERS = ("KXNCAAFSPREAD",)


def main() -> int:
    for ticker in PROBE_TICKERS:
        print(f"=== GET /markets/{ticker} (full raw JSON) ===")
        resp = requests.get(f"{BASE_URL}/markets/{ticker}", timeout=TIMEOUT_SECONDS)
        print(f"HTTP {resp.status_code}")
        try:
            print(json.dumps(resp.json(), indent=2, default=str))
        except ValueError:
            print(resp.text[:2000])
        print()

    for event_ticker in PROBE_EVENT_TICKERS:
        print(f"=== GET /events/{event_ticker} (full raw JSON, with nested markets if any) ===")
        resp = requests.get(
            f"{BASE_URL}/events/{event_ticker}", params={"with_nested_markets": "true"}, timeout=TIMEOUT_SECONDS
        )
        print(f"HTTP {resp.status_code}")
        try:
            print(json.dumps(resp.json(), indent=2, default=str)[:6000])
        except ValueError:
            print(resp.text[:2000])
        print()

    for series_ticker in PROBE_SERIES_TICKERS:
        print(f"=== GET /markets?series_ticker={series_ticker}&limit=2 (full raw JSON, list endpoint) ===")
        resp = requests.get(
            f"{BASE_URL}/markets", params={"series_ticker": series_ticker, "limit": 2}, timeout=TIMEOUT_SECONDS
        )
        print(f"HTTP {resp.status_code}")
        try:
            print(json.dumps(resp.json(), indent=2, default=str)[:4000])
        except ValueError:
            print(resp.text[:2000])
        print()

    print("=== GET /markets/{ticker}/orderbook (dedicated orderbook endpoint) ===")
    resp = requests.get(f"{BASE_URL}/markets/{PROBE_TICKERS[0]}/orderbook", timeout=TIMEOUT_SECONDS)
    print(f"HTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2, default=str)[:3000])
    except ValueError:
        print(resp.text[:2000])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
