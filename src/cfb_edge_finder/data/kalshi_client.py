"""Kalshi REST API v2 client -- read-only, public-endpoint-only.

STATUS: `KalshiClient`'s base URL was live-verified from a GitHub Actions
runner (this dev environment's own network egress to Kalshi is blocked by
organization policy -- see .github/workflows/validate-kalshi-cfb-live.yml
and scripts/validate_kalshi_cfb_live.py for the discovery script this
client is built from). `DEFAULT_BASE_URL` below is the confirmed-working
one (HTTP 200 on GET /exchange/status); the other candidates tried and
rejected are recorded in that script's own docstring.

*** WHY NO AUTHENTICATION ***
Every method here calls one of Kalshi's public market-data endpoints
(GET /series, /series/{ticker}, /events, /markets, /markets/{ticker},
/exchange/status). These serve real, live market data with no
Authorization header required -- confirmed empirically (see the live
discovery run this client is built from). This client intentionally has
NO code path for Kalshi's signed-request authentication scheme (API key
ID + RSA-PSS-signed timestamp, used for portfolio/order-placement
endpoints) -- per this mission's explicit instruction, no trading
credentials are used or required anywhere in Milestone D, and adding that
signing logic would be a first step toward an execution surface this
milestone must not build.
"""

from __future__ import annotations

import requests

DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_PAGE_LIMIT = 200
MAX_PAGES = 25
"""A hard cap on pagination depth for any single call -- a discovery/
pricing sweep should never loop indefinitely against a live API, even if
a cursor were to (incorrectly) never terminate."""


class KalshiClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def _get(self, path: str, params: dict[str, object] | None = None) -> dict:
        response = requests.get(
            f"{self._base_url}{path}",
            params={k: v for k, v in (params or {}).items() if v is not None},
            headers={"Accept": "application/json"},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def _paginate(
        self, path: str, params: dict[str, object], list_key: str, limit: int = DEFAULT_PAGE_LIMIT
    ) -> list[dict]:
        """Follows Kalshi's cursor-based pagination (a `cursor` field in
        the response, echoed back as a `cursor` query param) until
        exhausted or `MAX_PAGES` is reached."""
        items: list[dict] = []
        cursor: str | None = None
        for _page in range(MAX_PAGES):
            page_params = dict(params, limit=limit)
            if cursor:
                page_params["cursor"] = cursor
            body = self._get(path, page_params)
            page_items = body.get(list_key) or []
            items.extend(page_items)
            cursor = body.get("cursor") or None
            if not cursor or not page_items:
                break
        return items

    def exchange_status(self) -> dict:
        return self._get("/exchange/status")

    def fetch_series(self, category: str | None = None) -> list[dict]:
        """Raw GET /series response (paginated). `category` (e.g.
        "Sports") narrows the sweep server-side when supported; pass None
        for the unfiltered listing."""
        return self._paginate("/series", {"category": category}, "series")

    def fetch_series_detail(self, series_ticker: str) -> dict | None:
        """Raw GET /series/{ticker}. Returns None (rather than raising) on
        any non-2xx status -- callers use this to PROBE whether a
        candidate series ticker exists at all, so a 404 is an expected,
        informative outcome, not an error."""
        try:
            body = self._get(f"/series/{series_ticker}")
        except requests.HTTPError:
            return None
        return body.get("series")

    def fetch_events(self, series_ticker: str, status: str | None = None) -> list[dict]:
        """Raw GET /events response (paginated), for one series ticker."""
        return self._paginate("/events", {"series_ticker": series_ticker, "status": status}, "events")

    def fetch_markets(
        self, series_ticker: str | None = None, event_ticker: str | None = None, status: str | None = None
    ) -> list[dict]:
        """Raw GET /markets response (paginated). At least one of
        series_ticker/event_ticker should be given -- an unfiltered sweep
        of every market on the exchange is never what a CFB-specific
        caller wants."""
        return self._paginate(
            "/markets", {"series_ticker": series_ticker, "event_ticker": event_ticker, "status": status}, "markets"
        )

    def fetch_market_detail(self, market_ticker: str) -> dict | None:
        """Raw GET /markets/{ticker} -- the single-market endpoint that
        carries live pricing fields (yes_bid/yes_ask/no_bid/no_ask/
        last_price/volume/open_interest) that the list endpoints
        (fetch_markets) do NOT include (confirmed via the live discovery
        script). Returns None on any non-2xx status rather than raising."""
        try:
            body = self._get(f"/markets/{market_ticker}")
        except requests.HTTPError:
            return None
        return body.get("market", body)
