#!/usr/bin/env python3
"""Milestone D: genuine, live, read-only discovery sweep of Kalshi's
current college-football market universe.

    python scripts/validate_kalshi_cfb_live.py

*** WHY NO CREDENTIALS ***
Kalshi's REST API v2 serves market/event/series data (GET /series,
GET /events, GET /markets, GET /markets/{ticker}, GET /exchange/status)
without authentication -- these are the same "public read" endpoints
Kalshi's own market pages consume. This script never sends an
Authorization header, never calls an order-placement/portfolio/trading
endpoint, and never needs a secret.

*** REVISION 2 -- fixes found from revision 1's live run ***
Revision 1 (job 97700572773, base URL https://api.elections.kalshi.com/
trade-api/v2 confirmed working) found two real bugs, fixed here:
  1. GET /series was NOT paginated -- only the first page was swept,
     which could have missed a real winner/moneyline series ticker.
  2. Every market's status was compared against the literal string
     "open", but real markets returned status="active" -- every
     currently-tradeable market was miscounted as closed. This revision
     reports the actual DISTINCT status values observed rather than
     assuming a vocabulary, and treats "active" as the tradeable state
     (confirmed from real evidence: SUUMONT spread/total markets with
     status="active" and a close_time in the future).
Revision 1 also found that GET /markets?series_ticker=X's list response
does not include yes_bid/yes_ask/no_bid/no_ask/last_price/volume/
open_interest -- this revision fetches full per-market detail
(GET /markets/{ticker}) for a small, bounded sample per series to check
for and capture those pricing fields, rather than assuming they're absent
platform-wide from a list-endpoint omission.

*** WHY KXNCAAFGAME SHOWED ZERO EVENTS IN REVISION 1 ***
Confirmed via a DIRECT GET /series/KXNCAAFGAME call: the series ticker
itself resolves (HTTP 200), but has zero events right now, while
KXNCAAFSPREAD and KXNCAAFTOTAL for the SAME upcoming games (e.g.
Southern Utah at Montana, 2026-08-29) already have 200 active markets
each. This revision also tries additional plausible winner/moneyline
ticker variants and reports exactly what it finds -- a real, evidenced
answer either way, not an assumption.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import requests

CANDIDATE_BASE_URLS = (
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://trading-api.kalshi.com/trade-api/v2",
    "https://api.kalshi.com/trade-api/v2",
)

KNOWN_CFB_SERIES_TICKERS = (
    "KXNCAAFGAME",
    "KXNCAAFWINNER",
    "KXNCAAFML",
    "KXNCAAFMONEYLINE",
    "KXNCAAFSPREAD",
    "KXNCAAFTOTAL",
    "KXNCAAFWINS",
    "KXNCAAF",
    "KXNCAAFPLAYOFF",
    "KXHEISMAN",
    "KXNCAAFAPRANK",
    "KXNCAAFTOPAPRANK",
    "KXNCAAFCS",
    "KXNCAAFACC",
    "KXNCAAFSEC",
    "KXNCAAFCUSA",
    "KXNCAAFBIG12",
    "KXNCAAFBIGTEN",
    "KXNCAAF1HWINNER",
    "KXNCAAF1HSPREAD",
    "KXNCAAF1HTOTAL",
)

FOOTBALL_KEYWORDS = ("NCAAF", "FOOTBALL", "COLLEGE FOOTBALL", "CFB")

TIMEOUT_SECONDS = 20.0
PER_SERIES_DETAIL_SAMPLE = 3
"""How many markets per relevant series get a full GET /markets/{ticker}
detail fetch (for real bid/ask/volume) -- bounded to keep this a quick,
targeted probe rather than pricing an entire 200+-market ladder."""


def _get(base_url: str, path: str, params: dict | None = None) -> tuple[int, dict | list | None]:
    try:
        resp = requests.get(f"{base_url}{path}", params=params or {}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        print(f"    REQUEST_ERROR {base_url}{path}: {exc}")
        return -1, None
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body


def _find_working_base_url() -> str | None:
    print("=== Probing candidate Kalshi API base URLs (unauthenticated GET only) ===")
    for base_url in CANDIDATE_BASE_URLS:
        status, body = _get(base_url, "/exchange/status")
        print(f"  {base_url}/exchange/status -> HTTP {status}")
        if status == 200:
            print(f"  WORKING base URL found: {base_url}")
            print(f"  exchange/status body: {json.dumps(body)[:500]}")
            return base_url
    return None


def _paginate(
    base_url: str, path: str, params: dict, list_key: str, limit: int = 200, max_pages: int = 25
) -> list[dict]:
    items: list[dict] = []
    cursor: str | None = None
    for _page in range(max_pages):
        page_params = dict(params, limit=limit)
        if cursor:
            page_params["cursor"] = cursor
        status, body = _get(base_url, path, page_params)
        if status != 200 or not isinstance(body, dict):
            break
        page_items = body.get(list_key) or []
        items.extend(page_items)
        cursor = body.get("cursor") or None
        if not cursor or not page_items:
            break
    return items


def _print_market_summary(market: dict) -> None:
    fields = (
        "ticker",
        "event_ticker",
        "title",
        "yes_sub_title",
        "no_sub_title",
        "status",
        "close_time",
        "floor_strike",
        "cap_strike",
        "strike_type",
        "rules_primary",
    )
    summary = {k: market.get(k) for k in fields if k in market}
    print(f"    MARKET {json.dumps(summary, default=str)[:700]}")


def _print_market_detail(base_url: str, ticker: str) -> None:
    status, body = _get(base_url, f"/markets/{ticker}")
    if status != 200 or not isinstance(body, dict):
        print(f"    DETAIL_FETCH_FAILED {ticker} -> HTTP {status}")
        return
    m = body.get("market", body)
    pricing_fields = (
        "ticker",
        "status",
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "last_price",
        "volume",
        "open_interest",
        "liquidity",
    )
    summary = {k: m.get(k) for k in pricing_fields if k in m}
    print(f"    DETAIL {json.dumps(summary, default=str)}")


def main() -> int:
    base_url = _find_working_base_url()
    if base_url is None:
        print("\nRESULT: no candidate Kalshi API base URL responded with HTTP 200.", file=sys.stderr)
        return 2

    captured_at = datetime.now(UTC).isoformat()
    print(f"\nCapture timestamp: {captured_at}")

    football_series_tickers: set[str] = set()
    all_series_seen = 0

    print("\n=== Sweeping GET /series (paginated) for anything football/NCAAF-related ===")
    for category_param in (None, "Sports"):
        params = {"category": category_param} if category_param else {}
        series_list = _paginate(base_url, "/series", params, "series", limit=200)
        print(f"  category={category_param!r}: {len(series_list)} series returned (paginated)")
        all_series_seen += len(series_list)
        for s in series_list:
            ticker = str(s.get("ticker", ""))
            title = str(s.get("title", "")) + " " + str(s.get("category", ""))
            if any(kw in ticker.upper() or kw in title.upper() for kw in FOOTBALL_KEYWORDS):
                football_series_tickers.add(ticker)

    print(f"\n  Football/NCAAF-related series tickers found via paginated sweep: {sorted(football_series_tickers)}")

    print("\n=== Checking known/candidate CFB series tickers directly (GET /series/{ticker}) ===")
    for ticker in KNOWN_CFB_SERIES_TICKERS:
        status, body = _get(base_url, f"/series/{ticker}")
        print(f"  GET /series/{ticker} -> HTTP {status}")
        if status == 200 and isinstance(body, dict) and "series" in body:
            football_series_tickers.add(ticker)

    all_target_tickers = sorted(football_series_tickers)
    print(
        f"\n=== Target series tickers for event/market discovery "
        f"({len(all_target_tickers)}): {all_target_tickers} ==="
    )

    total_events = 0
    total_markets = 0
    distinct_status_values: set[str] = set()
    per_series_report: dict[str, dict] = {}

    for series_ticker in all_target_tickers:
        print(f"\n--- Series: {series_ticker} ---")
        events = _paginate(base_url, "/events", {"series_ticker": series_ticker}, "events")
        markets = _paginate(base_url, "/markets", {"series_ticker": series_ticker}, "markets")
        total_events += len(events)
        total_markets += len(markets)

        statuses_here = {str(m.get("status", "")) for m in markets}
        distinct_status_values |= statuses_here
        active_markets = [m for m in markets if str(m.get("status", "")).lower() == "active"]
        print(f"  {len(events)} events, {len(markets)} markets, distinct statuses seen: {sorted(statuses_here)}")
        print(f"  {len(active_markets)} status=active")

        distinct_event_tickers = {m.get("event_ticker") for m in markets}
        print(f"  {len(distinct_event_tickers)} distinct event_tickers among these markets")

        for m in markets[:15]:
            _print_market_summary(m)

        for m in markets[:PER_SERIES_DETAIL_SAMPLE]:
            ticker = m.get("ticker")
            if ticker:
                _print_market_detail(base_url, ticker)

        per_series_report[series_ticker] = {
            "n_events": len(events),
            "n_markets": len(markets),
            "n_active": len(active_markets),
            "n_distinct_games": len(distinct_event_tickers),
            "statuses": sorted(statuses_here),
        }

    print("\n=== SUMMARY ===")
    print(f"Base URL used: {base_url}")
    print(f"Total series seen in paginated sweep (both category calls): {all_series_seen}")
    print(f"Total series probed: {len(all_target_tickers)}")
    print(f"Total events discovered: {total_events}")
    print(f"Total markets discovered: {total_markets}")
    print(f"All distinct status values observed across every market: {sorted(distinct_status_values)}")
    for ticker, report in per_series_report.items():
        print(f"  {ticker}: {report}")

    print(f"\nMode: live. Captured at: {captured_at}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
