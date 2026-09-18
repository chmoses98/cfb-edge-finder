"""Normalized contract record -- lossless by construction.

*** THE PRESERVATION RULE ***
Kalshi's raw market payload travels with every record (`raw`). Every
extracted field below is a CONVENIENCE for a reader, never a replacement
for the source. If Kalshi renames a field, adds one, or returns something
this module does not know about, the information is still in the artifact
and a handicapper (or ChatGPT) can read it. "Do not transform away
information that may later prove useful" is enforced structurally here
rather than by discipline.

*** WHY EVERY PRICE FIELD IS READ THROUGH A TOLERANT ACCESSOR ***
The live probe behind this pivot found Kalshi currently returns prices as
DECIMAL-DOLLAR STRINGS (`"yes_ask_dollars": "0.1200"`,
`"yes_bid_size_fp": "730.00"`), not the integer cents that older Kalshi
clients and this repository's own MLB-era code assume. Both spellings are
accepted, and a value that parses as neither is preserved raw and reported
as missing rather than silently coerced to 0 -- a 0 price and an absent
price are very different things to anyone deciding whether to bet.

*** WHAT IS DELIBERATELY ABSENT ***
No projection, no model probability, no fair value, no expected score, no
edge. `implied_probability` here is arithmetic on the executable price
(a mid, and the bid/ask it came from), which is market mechanics, not a
prediction. See mechanics.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cfb_edge_finder.catalog.classification import (
    ClassificationConfidence,
    GamePeriod,
    MarketClassification,
    MarketFamilyLabel,
)
from cfb_edge_finder.catalog.fees import EffectiveFee, fee_block

# Field-name candidates, in preference order. First present-and-parseable
# wins; all of them are kept in `raw` regardless.
_YES_BID = ("yes_bid_dollars", "yes_bid")
_YES_ASK = ("yes_ask_dollars", "yes_ask")
_NO_BID = ("no_bid_dollars", "no_bid")
_NO_ASK = ("no_ask_dollars", "no_ask")
_LAST = ("last_price_dollars", "last_price")
_PREV = ("previous_price_dollars", "previous_price")
_YES_BID_SIZE = ("yes_bid_size_fp", "yes_bid_size")
_YES_ASK_SIZE = ("yes_ask_size_fp", "yes_ask_size")
_NO_BID_SIZE = ("no_bid_size_fp", "no_bid_size")
_NO_ASK_SIZE = ("no_ask_size_fp", "no_ask_size")
_VOLUME = ("volume_fp", "volume")
_VOLUME_24H = ("volume_24h_fp", "volume_24h")
_OPEN_INTEREST = ("open_interest_fp", "open_interest")
_LIQUIDITY = ("liquidity_dollars", "liquidity")
_NOTIONAL = ("notional_value_dollars", "notional_value")


def _raw_number(payload: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, float] | None:
    """First key that parses as a number, with the key it came from."""
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if value is None or value == "":
            continue
        try:
            return key, float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_money(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """A MONEY field, normalized to dollars per $1 contract.

    A `*_dollars` string is already in dollars. A bare `yes_bid`-style key
    is integer CENTS in Kalshi's older representation and is divided by
    100 -- otherwise a 99c ask would be recorded as $99.00 and every
    mechanic derived from it would be nonsense."""
    found = _raw_number(payload, keys)
    if found is None:
        return None
    key, number = found
    if key.endswith("_dollars") or key.endswith("_fp"):
        return number
    return number / 100.0


def _first_count(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """A COUNT of contracts -- quoted size, volume, open interest.

    Never unit-converted. These are quantities, not money: a 730-contract
    bid is 730 whether Kalshi spells the key `yes_bid_size_fp` or
    `yes_bid_size`, and dividing the bare spelling by 100 would report it
    as 7.3 contracts."""
    found = _raw_number(payload, keys)
    return None if found is None else found[1]


def _first_strike(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """A STRIKE -- a contract parameter in the units of the underlying.

    Never unit-converted, and deliberately separate from the money path.
    A strike is POINTS (a 3.5-point spread, a 48.5-point total), not
    cents: routing it through the money accessor turned `floor_strike:
    3.5` into 0.035 and silently corrupted the line on every spread and
    total in the catalog. Caught by inspecting a rendered artifact against
    the contract title it came from."""
    found = _raw_number(payload, keys)
    return None if found is None else found[1]


def _first_value(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if payload.get(key) not in (None, ""):
            return payload[key]
    return None


def _parse_ts(value: Any) -> datetime | None:
    """Kalshi timestamps are RFC3339. A sentinel like
    '0001-01-01T00:00:00Z' (observed live on event.last_updated_ts) is
    treated as absent rather than as a real year-1 datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.year <= 1:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


@dataclass(frozen=True)
class ContractQuote:
    """Executable prices and quoted sizes, in dollars per $1 contract."""

    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    yes_bid_size: float | None = None
    yes_ask_size: float | None = None
    no_bid_size: float | None = None
    no_ask_size: float | None = None
    last_price: float | None = None
    previous_price: float | None = None

    @property
    def has_two_sided_yes_quote(self) -> bool:
        return self.yes_bid is not None and self.yes_ask is not None


@dataclass(frozen=True)
class ContractLiquidity:
    volume: float | None = None
    volume_24h: float | None = None
    open_interest: float | None = None
    liquidity_dollars: float | None = None


@dataclass(frozen=True)
class ContractSemantics:
    """Everything needed to understand what the contract MEANS, verbatim
    from Kalshi. This is the part an external handicapper actually reads."""

    title: str | None = None
    yes_sub_title: str | None = None
    no_sub_title: str | None = None
    rules_primary: str | None = None
    rules_secondary: str | None = None
    market_type: str | None = None
    strike_type: str | None = None
    floor_strike: float | None = None
    cap_strike: float | None = None
    functional_strike: str | None = None
    custom_strike: dict[str, Any] | None = None
    early_close_condition: str | None = None
    can_close_early: bool | None = None
    settlement_timer_seconds: int | None = None
    settlement_sources: list[dict[str, Any]] = field(default_factory=list)
    """Copied down from the parent event: which authority settles this
    contract. A market's meaning is incomplete without it."""


@dataclass(frozen=True)
class CatalogContract:
    """One Kalshi contract, as the catalog publishes it."""

    market_ticker: str
    event_ticker: str | None
    series_ticker: str | None
    game_key: str | None
    status: str | None
    classification: MarketClassification
    semantics: ContractSemantics
    quote: ContractQuote
    liquidity: ContractLiquidity
    open_time: datetime | None
    close_time: datetime | None
    expected_expiration_time: datetime | None
    expiration_time: datetime | None
    occurrence_datetime: datetime | None
    updated_time: datetime | None
    exchange_index: int | None
    effective_fee: EffectiveFee
    """The fee metadata that actually applies: an event override when
    present, else the parent series. Carries its own source and
    unavailability reason so a missing fee is never mistaken for a
    computed one -- see catalog/fees.py."""
    notional_value: float | None
    captured_at: datetime
    raw: dict[str, Any]
    """The unmodified Kalshi market payload. Never pruned."""

    @property
    def is_tradeable(self) -> bool:
        """Kalshi reports a live market as status 'active' (NOT 'open' --
        an earlier revision of this repository's own live probe compared
        against 'open' and counted every tradeable market as closed)."""
        return (self.status or "").lower() in ("active", "open")


def build_contract(
    market: dict[str, Any],
    *,
    game_key: str | None,
    series_ticker: str | None,
    settlement_sources: list[dict[str, Any]] | None,
    effective_fee: EffectiveFee,
    captured_at: datetime,
    classification: MarketClassification | None = None,
) -> CatalogContract:
    """Normalize one raw Kalshi market payload, losslessly.

    `series_ticker` is passed in because the live market payload does NOT
    carry one (verified against 303 real markets): it is derivable from the
    event ticker's prefix, and the event is what knows it for certain.
    """
    from cfb_edge_finder.catalog.classification import classify_market

    ticker = str(market.get("ticker") or "")
    event_ticker = market.get("event_ticker") or None

    semantics = ContractSemantics(
        title=market.get("title"),
        yes_sub_title=market.get("yes_sub_title"),
        no_sub_title=market.get("no_sub_title"),
        rules_primary=market.get("rules_primary"),
        rules_secondary=market.get("rules_secondary"),
        market_type=market.get("market_type"),
        strike_type=market.get("strike_type"),
        floor_strike=_first_strike(market, ("floor_strike",)),
        cap_strike=_first_strike(market, ("cap_strike",)),
        functional_strike=market.get("functional_strike"),
        custom_strike=market.get("custom_strike") or None,
        early_close_condition=market.get("early_close_condition"),
        can_close_early=market.get("can_close_early"),
        settlement_timer_seconds=market.get("settlement_timer_seconds"),
        settlement_sources=list(settlement_sources or []),
    )

    quote = ContractQuote(
        yes_bid=_first_money(market, _YES_BID),
        yes_ask=_first_money(market, _YES_ASK),
        no_bid=_first_money(market, _NO_BID),
        no_ask=_first_money(market, _NO_ASK),
        yes_bid_size=_first_count(market, _YES_BID_SIZE),
        yes_ask_size=_first_count(market, _YES_ASK_SIZE),
        no_bid_size=_first_count(market, _NO_BID_SIZE),
        no_ask_size=_first_count(market, _NO_ASK_SIZE),
        last_price=_first_money(market, _LAST),
        previous_price=_first_money(market, _PREV),
    )

    liquidity = ContractLiquidity(
        volume=_first_count(market, _VOLUME),
        volume_24h=_first_count(market, _VOLUME_24H),
        open_interest=_first_count(market, _OPEN_INTEREST),
        liquidity_dollars=_first_money(market, _LIQUIDITY),
    )

    return CatalogContract(
        market_ticker=ticker,
        event_ticker=event_ticker,
        series_ticker=series_ticker,
        game_key=game_key,
        status=market.get("status"),
        classification=classification
        or classify_market(series_ticker, market.get("title"), market.get("rules_primary")),
        semantics=semantics,
        quote=quote,
        liquidity=liquidity,
        open_time=_parse_ts(market.get("open_time")),
        close_time=_parse_ts(market.get("close_time")),
        expected_expiration_time=_parse_ts(market.get("expected_expiration_time")),
        expiration_time=_parse_ts(market.get("expiration_time")),
        occurrence_datetime=_parse_ts(market.get("occurrence_datetime")),
        updated_time=_parse_ts(market.get("updated_time")),
        exchange_index=market.get("exchange_index"),
        effective_fee=effective_fee,
        notional_value=_first_money(market, _NOTIONAL),
        captured_at=captured_at,
        raw=dict(market),
    )


def contract_to_dict(contract: CatalogContract, include_raw: bool) -> dict[str, Any]:
    """Serialize one contract. `include_raw` is the size/fidelity dial:
    the primary catalog omits the raw payload (it is reconstructible from
    the flat index and provenance, and ChatGPT must be able to ingest the
    primary artifact efficiently), while the audit/flat artifact can carry
    it. Every betting-relevant field is present either way."""
    from cfb_edge_finder.catalog.mechanics import contract_mechanics

    mechanics = contract_mechanics(contract)
    payload: dict[str, Any] = {
        "market_ticker": contract.market_ticker,
        "event_ticker": contract.event_ticker,
        "series_ticker": contract.series_ticker,
        "status": contract.status,
        "family": str(contract.classification.family),
        "period": str(contract.classification.period),
        "classification_confidence": str(contract.classification.confidence),
        "is_alternate_line": contract.classification.is_alternate_line,
        "title": contract.semantics.title,
        "yes_sub_title": contract.semantics.yes_sub_title,
        "no_sub_title": contract.semantics.no_sub_title,
        "market_type": contract.semantics.market_type,
        "strike_type": contract.semantics.strike_type,
        "floor_strike": contract.semantics.floor_strike,
        "cap_strike": contract.semantics.cap_strike,
        "functional_strike": contract.semantics.functional_strike,
        "custom_strike": contract.semantics.custom_strike,
        "yes_bid": contract.quote.yes_bid,
        "yes_ask": contract.quote.yes_ask,
        "no_bid": contract.quote.no_bid,
        "no_ask": contract.quote.no_ask,
        "yes_bid_size": contract.quote.yes_bid_size,
        "yes_ask_size": contract.quote.yes_ask_size,
        "no_bid_size": contract.quote.no_bid_size,
        "no_ask_size": contract.quote.no_ask_size,
        "last_price": contract.quote.last_price,
        "previous_price": contract.quote.previous_price,
        "volume": contract.liquidity.volume,
        "volume_24h": contract.liquidity.volume_24h,
        "open_interest": contract.liquidity.open_interest,
        "liquidity_dollars": contract.liquidity.liquidity_dollars,
        "notional_value": contract.notional_value,
        "open_time": _iso(contract.open_time),
        "close_time": _iso(contract.close_time),
        "expected_expiration_time": _iso(contract.expected_expiration_time),
        "expiration_time": _iso(contract.expiration_time),
        "occurrence_datetime": _iso(contract.occurrence_datetime),
        "updated_time": _iso(contract.updated_time),
        "rules_primary": contract.semantics.rules_primary,
        "rules_secondary": contract.semantics.rules_secondary,
        "early_close_condition": contract.semantics.early_close_condition,
        "can_close_early": contract.semantics.can_close_early,
        "settlement_timer_seconds": contract.semantics.settlement_timer_seconds,
        "settlement_sources": contract.semantics.settlement_sources,
        "exchange_index": contract.exchange_index,
        # The EFFECTIVE values (event override if present, else series).
        "fee_type": contract.effective_fee.fee_type,
        "fee_multiplier": contract.effective_fee.fee_multiplier,
        # Everything a reader needs to know what the fee figures ARE, and
        # what they deliberately are not. See catalog/fees.py.
        "fee": fee_block(
            contract.effective_fee,
            yes_ask=contract.quote.yes_ask,
            yes_mid=mechanics.get("yes_mid"),
        ),
        "mechanics": mechanics,
        "classification_rationale": contract.classification.rationale,
    }
    if include_raw:
        payload["raw"] = contract.raw
    return payload


__all__ = [
    "CatalogContract",
    "ContractLiquidity",
    "ContractQuote",
    "ContractSemantics",
    "build_contract",
    "contract_to_dict",
    "ClassificationConfidence",
    "GamePeriod",
    "MarketFamilyLabel",
]
