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
endpoint, and never needs a secret. If any request below returns 401/403,
the workflow reports that explicitly as a genuine finding (public access
insufficient) rather than silently working around it with credentials --
see mission section 22 ("do not require trading credentials merely to
read market data if public access exists").

*** WHY MULTIPLE CANDIDATE BASE URLS ***
This dev environment's own network egress to Kalshi is blocked by
organization policy (confirmed via the agent proxy's status endpoint),
so the exact current production API hostname cannot be verified from
here beforehand -- this script tries the plausible candidates from a
GitHub-hosted runner (unrestricted egress) and reports which one(s)
actually respond, rather than assuming one is correct.

*** WHY BOTH A CATEGORY SWEEP AND KNOWN SERIES TICKERS ***
Milestone B.5's historical audit (docs/KALSHI_CFB_MARKET_AUDIT.md,
src/cfb_edge_finder/kalshi/cfb_market_family_registry.py) already
identified several real CFB series tickers from secondary evidence
(KXNCAAFGAME, KXNCAAFWINS, KXNCAAF, KXNCAAFPLAYOFF, KXHEISMAN,
KXNCAAFAPRANK, KXNCAAFTOPAPRANK, conference-champion boards, KXNCAAFCS).
This script queries those directly (fast, targeted) AND separately sweeps
GET /series for anything football/NCAAF-related that audit might have
missed -- per this mission's explicit instruction not to assume ticker
patterns solely from the historical audit.

Prints structured discovery output for a human (or a follow-up commit) to
turn into real client code, fixtures, and coverage-ledger entries. Does
NOT write anything back to this repository, does NOT commit large raw
payloads -- output is truncated per-market to keep logs a reasonable size.
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
)

FOOTBALL_KEYWORDS = ("NCAAF", "FOOTBALL", "COLLEGE FOOTBALL", "CFB")

TIMEOUT_SECONDS = 20.0


def _get(base_url: str, path: str, params: dict | None = None) -> tuple[int, dict | list | None]:
    """A single unauthenticated GET. Returns (status_code, parsed_json_or_None).
    Never raises on a non-2xx status -- callers decide what a given status
    means (404/400 for a bad series ticker guess is expected and not fatal)."""
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


def _paginate(base_url: str, path: str, params: dict, list_key: str, limit: int = 200) -> list[dict]:
    """Follows Kalshi's cursor-based pagination (a `cursor` field in the
    response, echoed back as a `cursor` query param) until exhausted or a
    non-200 status is hit."""
    items: list[dict] = []
    cursor: str | None = None
    for _page in range(25):  # hard cap -- a discovery sweep should never loop forever
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
        "series_ticker",
        "title",
        "subtitle",
        "yes_sub_title",
        "no_sub_title",
        "status",
        "close_time",
        "expiration_time",
        "market_type",
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "last_price",
        "volume",
        "open_interest",
        "cap_strike",
        "floor_strike",
        "strike_type",
        "rules_primary",
        "rules_secondary",
        "category",
    )
    summary = {k: market.get(k) for k in fields if k in market}
    print(f"    MARKET {json.dumps(summary, default=str)[:900]}")


def main() -> int:
    base_url = _find_working_base_url()
    if base_url is None:
        print("\nRESULT: no candidate Kalshi API base URL responded with HTTP 200 to an", file=sys.stderr)
        print("unauthenticated /exchange/status request. This is a genuine finding -- do", file=sys.stderr)
        print("not fabricate market data. Report this as the blocker.", file=sys.stderr)
        return 2

    captured_at = datetime.now(UTC).isoformat()
    print(f"\nCapture timestamp: {captured_at}")

    all_series: list[dict] = []
    football_series_tickers: set[str] = set()

    print("\n=== Sweeping GET /series for anything football/NCAAF-related ===")
    for category_param in (None, "Sports"):
        params = {"category": category_param} if category_param else {}
        status, body = _get(base_url, "/series", params)
        print(f"  GET /series category={category_param!r} -> HTTP {status}")
        if status == 200 and isinstance(body, dict):
            series_list = body.get("series") or []
            print(f"    {len(series_list)} series returned")
            for s in series_list:
                all_series.append(s)
                ticker = str(s.get("ticker", ""))
                title = str(s.get("title", "")) + " " + str(s.get("category", ""))
                if any(kw in ticker.upper() or kw in title.upper() for kw in FOOTBALL_KEYWORDS):
                    football_series_tickers.add(ticker)

    print(f"\n  Football/NCAAF-related series tickers found via sweep: {sorted(football_series_tickers)}")

    print("\n=== Checking known CFB series tickers directly (GET /series/{ticker}) ===")
    confirmed_series: dict[str, dict] = {}
    for ticker in KNOWN_CFB_SERIES_TICKERS:
        status, body = _get(base_url, f"/series/{ticker}")
        print(f"  GET /series/{ticker} -> HTTP {status}")
        if status == 200 and isinstance(body, dict) and "series" in body:
            confirmed_series[ticker] = body["series"]
            football_series_tickers.add(ticker)

    all_target_tickers = sorted(football_series_tickers)
    print(f"\n=== Target series tickers for event/market discovery: {all_target_tickers} ===")

    total_events = 0
    total_markets = 0
    per_series_report: dict[str, dict] = {}

    for series_ticker in all_target_tickers:
        print(f"\n--- Series: {series_ticker} ---")
        events = _paginate(base_url, "/events", {"series_ticker": series_ticker}, "events")
        print(f"  {len(events)} events")
        total_events += len(events)

        markets = _paginate(base_url, "/markets", {"series_ticker": series_ticker}, "markets")
        print(f"  {len(markets)} markets (any status)")
        total_markets += len(markets)

        open_markets = [m for m in markets if str(m.get("status", "")).lower() == "open"]
        closed_markets = [m for m in markets if str(m.get("status", "")).lower() != "open"]
        print(f"  {len(open_markets)} open, {len(closed_markets)} closed/other status")

        for m in markets[:30]:  # bounded -- avoid an enormous log for a huge series like KXNCAAFWINS
            _print_market_summary(m)

        per_series_report[series_ticker] = {
            "n_events": len(events),
            "n_markets": len(markets),
            "n_open": len(open_markets),
            "n_closed_or_other": len(closed_markets),
        }

    print("\n=== SUMMARY ===")
    print(f"Base URL used: {base_url}")
    print(f"Total series probed: {len(all_target_tickers)}")
    print(f"Total events discovered: {total_events}")
    print(f"Total markets discovered: {total_markets}")
    for ticker, report in per_series_report.items():
        print(f"  {ticker}: {report}")

    print(f"\nMode: live. Captured at: {captured_at}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
