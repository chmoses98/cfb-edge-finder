"""Milestone D: extracts executable prices from a raw Kalshi market dict.

*** FIELD NAMES, CONFIRMED FROM A REAL LIVE PAYLOAD ***
`GET /markets?series_ticker=X` (the LIST endpoint) and `GET /markets/
{ticker}` (the single-market DETAIL endpoint) were both confirmed, via a
live capture (scripts/validate_kalshi_market_detail_live.py), to return
IDENTICAL pricing fields -- there is no need to fetch per-market detail
separately from the list sweep, contradicting an earlier, incomplete
reading of this session's own discovery output (see
contract_semantics.py's module docstring history for that correction).
The real field names all carry unit suffixes, not the bare names an
initial guess assumed:

  yes_bid_dollars, yes_ask_dollars, no_bid_dollars, no_ask_dollars,
  last_price_dollars, volume_fp, volume_24h_fp, open_interest_fp,
  liquidity_dollars

Every `_dollars` field is a decimal STRING already denominated in dollars
on a $1 notional binary contract (e.g. "0.3500" = $0.35 = a 35%
probability), not cents -- read directly as a probability, no /100
needed. `_fp` fields are decimal-string contract counts.

*** EXECUTABLE VS MIDPOINT (mission section 11) ***
This module is deliberately explicit about which price a caller is
asking for:
  - `executable_yes_price`/`executable_no_price`: the best ASK a taker
    could actually cross right now (yes_ask_dollars / no_ask_dollars) --
    this is what "buying YES/NO" actually costs, the correct number for
    a model-vs-market RESEARCH comparison per this mission's own
    instruction not to substitute a non-tradeable midpoint for it.
  - `midpoint`: (yes_bid + yes_ask) / 2, offered ONLY as a clearly
    labeled separate research metric -- never returned or used in place
    of an executable price.
  - `has_any_volume`: real evidence found a genuinely fresh market can
    have populated bid/ask AND a real resting orderbook (confirmed via
    the dedicated /markets/{ticker}/orderbook endpoint) while
    volume_fp/volume_24h_fp are still exactly "0.00" -- i.e. quoted and
    orderbook-backed, but never yet traded. Both facts matter for
    research honesty and are surfaced separately, not conflated into one
    "is this real" boolean.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedMarketPrice:
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    last_price: float | None
    volume: float | None
    open_interest: float | None
    liquidity: float | None

    @property
    def executable_yes_price(self) -> float | None:
        """The best ASK a taker could cross right now to buy YES -- the
        correct "executable market price" for a model-vs-market
        comparison, never the midpoint."""
        return self.yes_ask

    @property
    def executable_no_price(self) -> float | None:
        return self.no_ask

    @property
    def midpoint(self) -> float | None:
        """A SEPARATE, explicitly-labeled research metric -- (yes_bid +
        yes_ask) / 2. Never substituted for executable_yes_price."""
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def has_any_volume(self) -> bool:
        return bool(self.volume) and self.volume > 0.0

    @property
    def has_quoted_market(self) -> bool:
        """True if there is a real bid AND ask to trade against, even if
        `has_any_volume` is False (a fresh, quoted-but-untraded market --
        a real, observed state, not a hypothetical edge case)."""
        return self.yes_bid is not None and self.yes_ask is not None


def _parse_dollars(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def extract_market_price(market: dict) -> ExtractedMarketPrice:
    """The single entry point -- takes one raw Kalshi market dict (from
    either the list or detail endpoint; both confirmed to carry the same
    fields) and returns its prices, or None for any field genuinely
    absent from the payload (never a fabricated 0.0 standing in for
    "missing")."""
    return ExtractedMarketPrice(
        yes_bid=_parse_dollars(market.get("yes_bid_dollars")),
        yes_ask=_parse_dollars(market.get("yes_ask_dollars")),
        no_bid=_parse_dollars(market.get("no_bid_dollars")),
        no_ask=_parse_dollars(market.get("no_ask_dollars")),
        last_price=_parse_dollars(market.get("last_price_dollars")),
        volume=_parse_dollars(market.get("volume_fp")),
        open_interest=_parse_dollars(market.get("open_interest_fp")),
        liquidity=_parse_dollars(market.get("liquidity_dollars")),
    )
