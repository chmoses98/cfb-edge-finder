"""A dict-backed Kalshi stand-in for catalog tests.

Shapes mirror real payloads captured live on 2026-09-17 (see
docs/evidence/kalshi_milestone_probe.txt and
docs/evidence/kalshi_milestone_reconciliation.txt): prices are
decimal-dollar STRINGS, market status reads "active" rather than "open",
markets carry no series_ticker of their own, and milestones put the league
at details.league == "NCAAFB" with no `competition` field anywhere.
"""

from __future__ import annotations

from typing import Any


def make_milestone(
    game_key: str = "26SEP19UGAARK",
    event_tickers: tuple[str, ...] = ("KXNCAAFGAME-26SEP19UGAARK",),
    *,
    milestone_id: str = "ms-1",
    title: str = "Georgia vs Arkansas",
    start_date: str = "2026-09-19T19:00:00Z",
    status: str = "scheduled",
    league: str = "NCAAFB",
    division: str = "FBS",
    conference: str = "SEC",
    tier: str = "A_MINUS",
    week: int = 4,
    primary: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    main = event_tickers[0] if event_tickers else None
    return {
        "id": milestone_id,
        "title": title,
        "start_date": start_date,
        "category": "Sports",
        "type": "football_game",
        "details": {
            "league": league,
            "division": division,
            "conference": conference,
            "tier": tier,
            "status": status,
            "main_game_event_ticker": main,
            "season": {"year": 2026, "type": "REG", "week": week},
        },
        "primary_event_tickers": list(primary if primary is not None else event_tickers),
        "related_event_tickers": list(event_tickers),
        "source_ids": {},
    }


def make_event(
    event_ticker: str,
    *,
    title: str = "Georgia vs Arkansas",
    sub_title: str = "UGA vs ARK (Sep 19)",
    competition: str = "NCAA Football",
    competition_scope: str = "Game",
    mutually_exclusive: bool = True,
) -> dict[str, Any]:
    return {
        "event_ticker": event_ticker,
        "series_ticker": event_ticker.split("-")[0],
        "title": title,
        "sub_title": sub_title,
        "category": "Sports",
        "mutually_exclusive": mutually_exclusive,
        "product_metadata": {"competition": competition, "competition_scope": competition_scope},
        "settlement_sources": [
            {"name": "Kalshi using information originating from the NCAA", "url": "https://www.ncaa.com"}
        ],
        "strike_period": "",
        "collateral_return_type": "MECNET",
        "exchange_index": 0,
    }


def make_market(
    ticker: str,
    *,
    event_ticker: str | None = None,
    status: str = "active",
    title: str = "Arkansas wins 1H by over 4.5 points",
    floor_strike: float | None = 4.5,
    yes_bid: str = "0.0100",
    yes_ask: str = "0.1200",
    no_bid: str = "0.8800",
    no_ask: str = "0.9900",
    yes_bid_size: str = "730.00",
    yes_ask_size: str = "10.00",
    volume: str = "0.00",
    open_interest: str = "0.00",
    strike_type: str = "greater",
    custom_strike: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ticker": ticker,
        "event_ticker": event_ticker if event_ticker is not None else ticker.rsplit("-", 1)[0],
        "status": status,
        "market_type": "binary",
        "strike_type": strike_type,
        "title": title,
        "yes_sub_title": title,
        "no_sub_title": title,
        "rules_primary": f"If {title}, then the market resolves to Yes.",
        "rules_secondary": "Note: Only points scored during the stated period count.",
        "yes_bid_dollars": yes_bid,
        "yes_ask_dollars": yes_ask,
        "no_bid_dollars": no_bid,
        "no_ask_dollars": no_ask,
        "yes_bid_size_fp": yes_bid_size,
        "yes_ask_size_fp": yes_ask_size,
        "last_price_dollars": "0.0000",
        "previous_price_dollars": "0.0000",
        "volume_fp": volume,
        "volume_24h_fp": "0.00",
        "open_interest_fp": open_interest,
        "liquidity_dollars": "0.0000",
        "notional_value_dollars": "1.0000",
        "open_time": "2026-09-14T16:20:00Z",
        "close_time": "2026-09-21T16:00:00Z",
        "expected_expiration_time": "2026-09-19T19:00:00Z",
        "expiration_time": "2026-09-21T16:00:00Z",
        "latest_expiration_time": "2026-09-21T16:00:00Z",
        "occurrence_datetime": "2026-09-19T19:00:00Z",
        "updated_time": "2026-09-17T22:00:00Z",
        "created_time": "2026-09-14T16:17:18Z",
        "can_close_early": True,
        "early_close_condition": "This market will close and expire after the event occurs.",
        "settlement_timer_seconds": 180,
        "exchange_index": 0,
        "price_level_structure": "linear_cent",
        "result": "",
        "expiration_value": "",
    }
    if floor_strike is not None:
        payload["floor_strike"] = floor_strike
    if custom_strike is not None:
        payload["custom_strike"] = custom_strike
    return payload


def make_series(ticker: str, title: str, *, tags: tuple[str, ...] = ("Football",)) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "title": title,
        "category": "Sports",
        "categories": ["Sports"],
        "tags": list(tags),
        "fee_type": "quadratic",
        "fee_multiplier": 1,
        "settlement_sources": [{"name": "ESPN", "url": "https://www.espn.com"}],
    }


class FakeKalshi:
    """Routes a `(path, params)` call to canned data.

    `fail_paths` maps a path substring to an Exception to raise, which is
    how "a request failure must never look like zero markets" is tested.
    `page_size` forces multi-page cursor chains so pagination is exercised
    end-to-end rather than only in isolation.
    """

    def __init__(
        self,
        *,
        milestones: list[dict[str, Any]] | None = None,
        events: dict[str, dict[str, Any]] | None = None,
        markets_by_event: dict[str, list[dict[str, Any]]] | None = None,
        series: list[dict[str, Any]] | None = None,
        events_by_series: dict[str, list[dict[str, Any]]] | None = None,
        multivariate: list[dict[str, Any]] | None = None,
        fail_paths: dict[str, Exception] | None = None,
        page_size: int = 200,
    ) -> None:
        self.milestones = milestones or []
        self.events = events or {}
        self.markets_by_event = markets_by_event or {}
        self.series = series or []
        self.events_by_series = events_by_series or {}
        self.multivariate = multivariate or []
        self.fail_paths = fail_paths or {}
        self.page_size = page_size
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        self.calls.append((path, params))
        for fragment, error in self.fail_paths.items():
            if fragment in path or fragment in str(params.get("event_ticker") or ""):
                raise error

        if path == "/milestones":
            return self._page(self.milestones, "milestones", params)
        if path == "/markets":
            event_ticker = str(params.get("event_ticker") or "")
            return self._page(list(self.markets_by_event.get(event_ticker, [])), "markets", params)
        if path == "/series":
            return self._page(self.series, "series", params)
        if path == "/events":
            series_ticker = str(params.get("series_ticker") or "")
            return self._page(list(self.events_by_series.get(series_ticker, [])), "events", params)
        if path == "/multivariate_event_collections":
            return self._page(self.multivariate, "multivariate_contracts", params)
        if path.startswith("/events/"):
            ticker = path.split("/events/", 1)[1]
            if ticker not in self.events:
                raise KeyError(f"no such event {ticker}")
            return {"event": self.events[ticker]}
        raise AssertionError(f"FakeKalshi got an unexpected path: {path}")

    def _page(self, items: list[dict[str, Any]], key: str, params: dict[str, Any]) -> dict[str, Any]:
        """Cursor pagination over a list, honouring `page_size`. The cursor
        is the index of the next item, so an explicit cursor is required to
        walk the chain -- a caller that fails to echo it back re-reads page
        one and the test sees duplicates."""
        size = min(int(params.get("limit") or self.page_size), self.page_size)
        start = int(params.get("cursor") or 0)
        window = items[start : start + size]
        body: dict[str, Any] = {key: window}
        next_index = start + len(window)
        if next_index < len(items):
            body["cursor"] = str(next_index)
        return body
