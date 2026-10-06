"""An empty book must not surface as a 50% two-sided market.

*** THE DEFECT, AS THE LIVE SURFACE SHOWED IT ***
A live capture (section D of docs/evidence/kalshi_fee_and_override_probe.txt,
2026-09-18) found **269 of 15,444** contracts quoted:

    yes_bid $0.00   yes_bid_size 0
    yes_ask $1.00   yes_ask_size 0
    no_bid  $0.00   no_ask       $1.00
    liquidity_dollars 0

Both YES price fields are numerically present, so `has_two_sided_yes_quote`
(then: "both prices are not None") returned True and the mechanics block
published:

    yes_mid                      0.5
    implied_probability_yes_mid  0.5
    two_sided_quote              true
    yes_bid_ask_spread_cents     100.0

Nothing there is a lie about arithmetic and all of it is wrong as
information. No market said 50%. A RUN CFB session reading
`implied_probability_yes_mid` would take an empty book as a coin-flip
prior and find "edge" against a price nobody is showing -- on contracts
that look otherwise healthy, because those 269 all carry POSITIVE volume
and open interest (the sampled one had 29,913 OI and a $0.99 last price).
They are real traded contracts whose book has emptied.

*** THE DISTINCTION THE CODE NOW MAKES ***
    numeric price fields exist   !=   executable top-of-book liquidity exists

Quoted SIZE is the only field that separates them, and the live payload
settles how to read it: on ordinary contracts `yes_bid_size` and
`yes_ask_size` are positive (4,000/4,000 sampled), on the empty ones they
are exactly 0. `no_bid_size` / `no_ask_size` are null on EVERY contract in
the catalog, ordinary ones included, so the NO side's size carries no
signal and is not consulted -- a binary's YES and NO sides are the same
book.

*** WHAT IS NOT DONE ***
The contract stays in the menu. Discovery completeness and quote quality
are different concepts, and a contract with no bid today may have one at
kickoff. It is simply not price discovery until liquidity appears.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cfb_edge_finder.catalog.contract import build_contract, contract_to_dict
from cfb_edge_finder.catalog.fees import resolve_effective_fee

NOW = datetime(2026, 9, 18, 2, 0, tzinfo=UTC)
FEE = resolve_effective_fee({}, {"fee_type": "quadratic", "fee_multiplier": 1})

# The live 0/100 payload, field for field, from the transcript above.
LIVE_EMPTY_BOOK = {
    "ticker": "KXNCAAF1H-26SEP17SYRPITT-PITT",
    "event_ticker": "KXNCAAF1H-26SEP17SYRPITT",
    "title": "Pittsburgh wins the 1st half",
    "status": "active",
    "yes_bid_dollars": "0.0000",
    "yes_ask_dollars": "1.0000",
    "no_bid_dollars": "0.0000",
    "no_ask_dollars": "1.0000",
    "yes_bid_size_fp": "0.00",
    "yes_ask_size_fp": "0.00",
    "no_bid_size_fp": None,
    "no_ask_size_fp": None,
    "last_price_dollars": "0.9900",
    "previous_price_dollars": "0.6800",
    "volume": 31021,
    "volume_24h": 4120,
    "open_interest": 29913,
    "liquidity_dollars": "0.0000",
}

# A healthy book, for contrast.
LIVE_LIQUID_BOOK = {
    "ticker": "KXNCAAFGAME-26SEP19UGAARK-UGA",
    "event_ticker": "KXNCAAFGAME-26SEP19UGAARK",
    "title": "Georgia wins",
    "status": "active",
    "yes_bid_dollars": "0.8600",
    "yes_ask_dollars": "0.8800",
    "no_bid_dollars": "0.1200",
    "no_ask_dollars": "0.1400",
    "yes_bid_size_fp": "400.00",
    "yes_ask_size_fp": "4252.00",
    "volume": 91234,
    "open_interest": 55010,
}


def _publish(market: dict) -> dict:
    contract = build_contract(
        market,
        game_key="26SEP17SYRPITT",
        series_ticker=str(market["ticker"]).split("-")[0],
        settlement_sources=[],
        effective_fee=FEE,
        captured_at=NOW,
    )
    return contract_to_dict(contract, include_raw=False)


# =========================================================================
# THE REGRESSION ITSELF
# =========================================================================


def test_an_empty_0_100_book_publishes_no_midpoint_and_no_mid_probability():
    """*** THE LOAD-BEARING TEST ***
    The exact live shape must not yield a 50% anything."""
    mech = _publish(LIVE_EMPTY_BOOK)["mechanics"]

    assert mech["yes_mid"] is None, "an empty book published a midpoint"
    assert mech["no_mid"] is None
    assert mech["implied_probability_yes_mid"] is None, (
        "an empty book published a 50% implied probability -- a RUN CFB session "
        "would take that as a market prior"
    )
    assert mech["mid_is_published"] is False
    # And there is no 0.5 left anywhere in the block to be misread.
    assert 0.5 not in [v for v in mech.values() if isinstance(v, float)]


def test_an_empty_0_100_book_is_not_a_two_sided_quote():
    mech = _publish(LIVE_EMPTY_BOOK)["mechanics"]
    assert mech["two_sided_quote"] is False
    assert mech["yes_bid_is_executable"] is False
    assert mech["yes_ask_is_executable"] is False


def test_the_empty_book_is_identified_explicitly_and_machine_readably():
    """A consumer must be able to branch on this without pattern-matching
    prices, and without inferring it from a null."""
    mech = _publish(LIVE_EMPTY_BOOK)["mechanics"]
    assert mech["book_state"] == "empty_book"
    assert mech["is_sentinel_full_width_book"] is True
    assert mech["liquidity_basis"] == "positive quoted yes_bid_size / yes_ask_size"


def test_a_100_cent_spread_is_still_published_as_the_evidence_it_is():
    """Suppressing the spread would remove the very signal that says the
    book is empty. What must not happen is reading it as a wide market."""
    mech = _publish(LIVE_EMPTY_BOOK)["mechanics"]
    assert mech["yes_bid_ask_spread_cents"] == pytest.approx(100.0)
    assert mech["book_state"] == "empty_book"


def test_an_empty_book_contract_is_still_published_in_full():
    """Discovery completeness and quote quality are different concepts.
    The contract keeps its settlement semantics, its prices, its sizes and
    its volume -- it is a real traded contract whose book has emptied, and
    it may have a bid again by kickoff."""
    published = _publish(LIVE_EMPTY_BOOK)
    assert published["market_ticker"] == "KXNCAAF1H-26SEP17SYRPITT-PITT"
    assert published["yes_bid"] == pytest.approx(0.0)
    assert published["yes_ask"] == pytest.approx(1.0)
    assert published["yes_bid_size"] == pytest.approx(0.0)
    assert published["yes_ask_size"] == pytest.approx(0.0)
    assert published["open_interest"] == pytest.approx(29913)
    assert published["volume"] == pytest.approx(31021)
    assert published["title"]
    assert "fee" in published and "mechanics" in published


def test_positive_volume_and_open_interest_do_not_make_a_book_executable():
    """The trap inside the trap: these contracts look healthy by every
    activity measure. Volume is history; size is the offer."""
    mech = _publish(LIVE_EMPTY_BOOK)["mechanics"]
    published = _publish(LIVE_EMPTY_BOOK)
    assert published["open_interest"] > 0 and published["volume"] > 0
    assert mech["two_sided_quote"] is False
    assert mech["yes_mid"] is None


# =========================================================================
# THE CONTRAST CASES -- the gate must not swallow healthy books
# =========================================================================


def test_a_liquid_two_sided_book_is_unaffected():
    mech = _publish(LIVE_LIQUID_BOOK)["mechanics"]
    assert mech["book_state"] == "two_sided"
    assert mech["two_sided_quote"] is True
    assert mech["yes_bid_is_executable"] is True
    assert mech["yes_ask_is_executable"] is True
    assert mech["yes_mid"] == pytest.approx(0.87)
    assert mech["implied_probability_yes_mid"] == pytest.approx(0.87)
    assert mech["mid_is_published"] is True
    assert mech["is_sentinel_full_width_book"] is False


@pytest.mark.parametrize(
    ("bid_size", "ask_size", "expected"),
    [
        ("400.00", "4252.00", "two_sided"),
        ("400.00", "0.00", "bid_only"),
        ("0.00", "4252.00", "ask_only"),
        ("0.00", "0.00", "empty_book"),
        (None, None, "empty_book"),
    ],
)
def test_book_state_reports_which_sides_are_executable(bid_size, ask_size, expected):
    """Live counts: 401 contracts lack a positive yes_bid_size and 313
    lack a positive yes_ask_size, of which 269 lack both. The 176
    genuinely one-sided books are a real category, not an edge case."""
    market = dict(LIVE_LIQUID_BOOK, yes_bid_size_fp=bid_size, yes_ask_size_fp=ask_size)
    mech = _publish(market)["mechanics"]
    assert mech["book_state"] == expected


def test_a_one_sided_book_publishes_no_mid_either():
    """Averaging one real price with one nobody is showing is the same
    error in a less obvious costume."""
    market = dict(LIVE_LIQUID_BOOK, yes_ask_size_fp="0.00")
    mech = _publish(market)["mechanics"]
    assert mech["book_state"] == "bid_only"
    assert mech["yes_mid"] is None
    assert mech["implied_probability_yes_mid"] is None
    # The executable side is still reported -- one dead side does not
    # void the other.
    assert mech["implied_probability_yes_bid"] == pytest.approx(0.86)
    assert mech["yes_bid_is_executable"] is True


def test_a_contract_with_no_quoted_prices_at_all_says_no_quote():
    market = {k: v for k, v in LIVE_LIQUID_BOOK.items()
              if k not in ("yes_bid_dollars", "yes_ask_dollars")}
    mech = _publish(market)["mechanics"]
    assert mech["book_state"] == "no_quote"
    assert mech["yes_mid"] is None


def test_a_null_size_is_not_proven_liquidity():
    """Fail closed on the size field too. If Kalshi stopped publishing
    sizes, every book would read as not-executable -- visible and safe --
    rather than every book silently reading as liquid."""
    market = dict(LIVE_LIQUID_BOOK)
    market.pop("yes_bid_size_fp")
    market.pop("yes_ask_size_fp")
    mech = _publish(market)["mechanics"]
    assert mech["two_sided_quote"] is False
    assert mech["yes_mid"] is None


# =========================================================================
# THE FEE BLOCK ON AN EMPTY BOOK
# =========================================================================


def test_the_fee_block_on_an_empty_book_prices_only_the_sentinel_asks():
    """At a $1.00 YES ask the quadratic term P(1-P) is zero, so the trade
    fee is genuinely $0.00 -- correct, and meaningless, because there is
    nothing to buy. The book_state flags are what stop that $0.00 reading
    as a cheap trade."""
    published = _publish(LIVE_EMPTY_BOOK)
    block, mech = published["fee"], published["mechanics"]
    assert block["basis_yes_ask"] == pytest.approx(1.0)
    assert block["model_trade_fee_at_yes_ask"] == pytest.approx(0.0)
    # The mid basis is gone with the mid, so no $0.0175 phantom cost.
    assert block["basis_yes_mid"] is None
    assert block["model_trade_fee_at_yes_mid"] is None
    assert mech["book_state"] == "empty_book"
