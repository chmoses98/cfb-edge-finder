"""Read-only cross-check of our derived settlement against Kalshi's own
finalized market result (mission section 16).

*** READ-ONLY, ALWAYS ***
This module reads market metadata. It never places, cancels, or modifies
an order, never touches a portfolio or balance endpoint, and never
authenticates as a trader. `KalshiClient` in this repo has no order
surface at all -- see tests/test_no_recommendation_surface.py.

*** WHY A MISMATCH IS A DEFECT, NOT A DATA POINT ***
For a supported market where BOTH our derived settlement and Kalshi's
official result are known, they must agree. They are two independent
derivations of the same fact: ours from the official final score plus the
contract semantics we captured at observation time, Kalshi's from their
own settlement process. A disagreement means one of those is wrong --
most likely our stored semantics -- and every research conclusion drawn
from that contract is then suspect. So a mismatch is never written as a
normal settled record: it is classified SETTLEMENT_MISMATCH, retains both
values as evidence, and is escalated to HIGH severity so a human looks.

*** ABSENCE IS NOT DISAGREEMENT ***
A market Kalshi has not finalized (or that we could not fetch) yields
None, never a guessed side. None must never be treated as evidence of
either agreement or mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from cfb_edge_finder.data.kalshi_client import KalshiClient
from cfb_edge_finder.schemas.common import Side

FINALIZED_MARKET_STATUSES = frozenset({"finalized", "settled", "determined"})
"""Statuses that mean Kalshi has actually resolved the market. 'closed'
is deliberately NOT here: a closed market has stopped trading but may not
yet carry a settlement result."""

_YES_RESULT_TOKENS = frozenset({"yes"})
_NO_RESULT_TOKENS = frozenset({"no"})
_VOID_RESULT_TOKENS = frozenset({"void", "voided", "cancelled", "canceled", "all_no", "all_yes"})
"""`all_no`/`all_yes` are Kalshi's bulk-void encodings. Treated as
NOT a normal YES/NO outcome -- voiding is a different fact from settling,
and collapsing them would silently turn a voided market into a losing
contract."""


@dataclass(frozen=True)
class KalshiMarketOutcome:
    """What Kalshi says about one market. Every field may be absent; that
    is a legitimate answer, not a failure."""

    market_ticker: str
    status: str | None
    is_finalized: bool
    official_settlement: Side | None
    is_void: bool
    fetch_failed: bool = False
    detail: str = ""


def parse_market_outcome(market_ticker: str, raw_market: dict | None) -> KalshiMarketOutcome:
    """Interprets a raw Kalshi market payload. Never guesses: an
    unrecognised result string yields `official_settlement=None` with the
    reason preserved, rather than being coerced to a side."""
    if raw_market is None:
        return KalshiMarketOutcome(
            market_ticker=market_ticker,
            status=None,
            is_finalized=False,
            official_settlement=None,
            is_void=False,
            detail="market not returned by Kalshi",
        )

    status_raw = raw_market.get("status")
    status = status_raw.strip().lower() if isinstance(status_raw, str) else None
    is_finalized = status in FINALIZED_MARKET_STATUSES

    result_raw = raw_market.get("result")
    result = result_raw.strip().lower() if isinstance(result_raw, str) else None

    if result in _VOID_RESULT_TOKENS:
        return KalshiMarketOutcome(
            market_ticker=market_ticker,
            status=status,
            is_finalized=is_finalized,
            official_settlement=None,
            is_void=True,
            detail=f"Kalshi reported a void/bulk result {result!r}",
        )
    if result in _YES_RESULT_TOKENS:
        official = Side.YES
    elif result in _NO_RESULT_TOKENS:
        official = Side.NO
    else:
        official = None

    detail = ""
    if result is not None and official is None:
        detail = f"unrecognised Kalshi result {result!r} -- recorded as unknown rather than coerced to a side"
    elif official is not None and not is_finalized:
        # A result on a non-finalized market is not trusted as official.
        detail = f"Kalshi reported result {result!r} but status {status!r} is not finalized"
        official = None

    return KalshiMarketOutcome(
        market_ticker=market_ticker,
        status=status,
        is_finalized=is_finalized,
        official_settlement=official,
        is_void=False,
        detail=detail,
    )


def fetch_market_outcome(client: KalshiClient, market_ticker: str) -> KalshiMarketOutcome:
    """Read-only fetch of one market's current state.

    A fetch failure is reported as `fetch_failed=True`, never as "no
    settlement" -- the same distinction market discovery had to learn the
    hard way when an HTTP 429 was silently read as an empty series."""
    try:
        raw = client.fetch_market_detail(market_ticker)
    except requests.HTTPError as exc:
        return KalshiMarketOutcome(
            market_ticker=market_ticker,
            status=None,
            is_finalized=False,
            official_settlement=None,
            is_void=False,
            fetch_failed=True,
            detail=f"Kalshi market fetch failed: {exc}",
        )
    return parse_market_outcome(market_ticker, raw)


def detect_mismatch(derived: Side | None, official: Side | None) -> bool:
    """True ONLY when both sides are known and disagree. Absence on either
    side is never evidence of disagreement."""
    return derived is not None and official is not None and derived != official
