"""Market mechanics: arithmetic ON the quote, never a view ABOUT the game.

*** FEES LIVE IN fees.py, NOT HERE ***
Fee fields used to be computed in this block with a helper that rounded
the quadratic model fee UP TO A WHOLE CENT. Kalshi's current documentation
rounds the trade fee up to $0.000001, so that helper overstated the fee by
~2.75x on the docs' own worked example -- and it published the result under
a name ("estimated_fee_per_contract_at_mid") that read as the fee actually
charged, which no credential-free catalog can know. Fee resolution and
arithmetic now live in `catalog/fees.py`, and the artifact publishes a
self-describing `fee` block beside this one.

*** THE LINE THIS MODULE MUST NOT CROSS ***
Everything here is derivable from the order book alone and would be
identical no matter which teams were playing: the mid of the quoted
spread, the width of that spread, the probability the price itself
implies, the round-trip fee, how stale the quote is. None of it is a
projection, a fair value, an expected score, or an edge, and this module
imports nothing that could produce one.

The distinction matters because this catalog exists precisely BECAUSE the
prior projection model was not trusted for wagering decisions. A field
like `implied_probability` is safe -- it is the price restated in
probability units, which is what a binary contract's price already is.
A field like "fair probability" or "edge" would be the model creeping back
in through the artifact, so neither exists anywhere in this package.

*** WHY MID AND NOT LAST ***
`last_price` is whatever someone last agreed to, possibly hours ago and
possibly far from the current quote. The mid of the live bid/ask is the
honest answer to "what is this worth right now", and the raw bid, ask and
last are all published alongside so a reader can disagree.

*** AND WHY THE MID IS SOMETIMES NOT PUBLISHED AT ALL ***
That answer holds only while both quoted sides are real. A live capture
found 269 of 15,444 contracts quoted $0.00 bid / $1.00 ask with ZERO
quoted size on both sides -- an empty book wearing a full-width spread.
The mid of that is $0.50, which this module published as an implied
probability of 50%. No market ever said that. Price fields existing and
executable liquidity existing are different facts, and this module now
keeps them apart: see `contract.ContractQuote.book_state` and the note
above `contract_mechanics`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cfb_edge_finder.catalog.contract import CatalogContract


def mid_price(bid: float | None, ask: float | None) -> float | None:
    """Midpoint of a two-sided quote. None unless BOTH sides exist: a
    one-sided book has no mid, and inventing one (by treating the missing
    side as 0 or 1) would fabricate a price no one is showing."""
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2.0, 6)


def bid_ask_spread(bid: float | None, ask: float | None) -> float | None:
    """Ask minus bid, in dollars per $1 contract. Can be negative in a
    crossed or stale book; reported as observed rather than clamped, since
    a negative spread is real information about the quote's quality."""
    if bid is None or ask is None:
        return None
    return round(ask - bid, 6)


def implied_probability(price: float | None) -> float | None:
    """A $1 binary contract's price IS its implied probability. Only the
    unit changes, so this is a restatement, not an estimate.

    A price outside [0, 1] is returned as None rather than clipped -- it
    means the input was not a dollar price (a cents value that escaped
    normalization, say), and silently clipping it to 1.0 would publish a
    confident-looking 100% that nothing supports."""
    if price is None:
        return None
    if not (0.0 <= price <= 1.0):
        return None
    return round(price, 6)


def quote_age_seconds(updated_time: datetime | None, as_of: datetime) -> float | None:
    """How stale the quote is at capture time. A 6-hour-old quote on a
    market closing in 20 minutes is a materially different proposition
    from a 6-second-old one, and nothing else in the artifact reveals
    that."""
    if updated_time is None:
        return None
    return round((as_of - updated_time).total_seconds(), 3)


def seconds_until(moment: datetime | None, as_of: datetime) -> float | None:
    if moment is None:
        return None
    return round((moment - as_of).total_seconds(), 3)


def contract_mechanics(contract: CatalogContract) -> dict[str, Any]:
    """The full mechanics block published per contract.

    Every value here is a pure function of the QUOTE, with no dependence on
    when it was computed -- which is what lets a game's detail file stay
    byte-identical across captures while its market surface is unchanged.
    See the note on countdowns at the bottom of the returned block.

    Deliberately excluded: anything about the GAME. If a future reader
    wants a model view, it belongs in a separate artifact that joins to
    this one by market_ticker -- not in here."""
    quote = contract.quote

    # *** THE MIDPOINT IS PUBLISHED ONLY WHEN BOTH SIDES ARE EXECUTABLE ***
    # A mid is the market's own estimate only if both numbers being
    # averaged are prices someone is actually showing. Live, 269 of
    # 15,444 contracts are quoted $0.00/$1.00 with zero size on both
    # sides; averaging those gave $0.50 and the artifact published it as
    # an implied probability. That is not a market saying "coin flip" --
    # it is a market saying nothing at all, and the two are
    # indistinguishable to a consumer once the number is printed.
    #
    # So `yes_mid`, `no_mid` and every mid-derived probability are null
    # unless `book_state == "two_sided"`. The raw `yes_bid`, `yes_ask`,
    # `no_bid`, `no_ask` and all four size fields are still published on
    # the contract, so nothing is hidden -- a reader who wants the
    # arithmetic can do it, knowing what it rests on. 445 of 15,444 live
    # contracts (2.9%) lose a published mid this way: 269 empty books,
    # plus 176 genuinely one-sided ones.
    #
    # The bid- and ask-based implied probabilities are KEPT: each
    # restates a price Kalshi published, and `yes_bid_is_executable` /
    # `yes_ask_is_executable` say whether that price can be traded on.
    # The spread is kept for the same reason -- a 100-cent spread is the
    # signal that the book is empty, so suppressing it would remove
    # evidence rather than add safety.
    executable_both_sides = quote.has_two_sided_yes_quote
    yes_mid = mid_price(quote.yes_bid, quote.yes_ask) if executable_both_sides else None
    no_mid = mid_price(quote.no_bid, quote.no_ask) if executable_both_sides else None
    yes_spread = bid_ask_spread(quote.yes_bid, quote.yes_ask)

    return {
        "yes_mid": yes_mid,
        "no_mid": no_mid,
        "yes_bid_ask_spread": yes_spread,
        "yes_bid_ask_spread_cents": None if yes_spread is None else round(yes_spread * 100, 4),
        "implied_probability_yes_mid": implied_probability(yes_mid),
        "implied_probability_yes_ask": implied_probability(quote.yes_ask),
        "implied_probability_yes_bid": implied_probability(quote.yes_bid),
        "implied_probability_last": implied_probability(quote.last_price),
        # --- what is actually executable, and why ---
        "two_sided_quote": executable_both_sides,
        "yes_bid_is_executable": quote.yes_bid_is_executable,
        "yes_ask_is_executable": quote.yes_ask_is_executable,
        "book_state": quote.book_state,
        "is_sentinel_full_width_book": quote.is_sentinel_full_width_book,
        "mid_is_published": executable_both_sides,
        "liquidity_basis": "positive quoted yes_bid_size / yes_ask_size",
        # *** WHY NO CLOCK-DERIVED COUNTDOWNS ARE PUBLISHED ***
        # quote_age_seconds / seconds_until_close / seconds_until_occurrence
        # used to be published here, and it was a real production defect:
        # they tick on every capture, so EVERY game file's bytes changed on
        # EVERY run even when not one price had moved. Measured live, that
        # rewrote all 239 detail files (43 MB) per run, defeating the
        # per-game change detection the split artifact exists for and
        # putting 43 MB into every commit instead of only what moved.
        #
        # They carried no information either: each is a subtraction of two
        # ABSOLUTE timestamps this artifact already publishes --
        # `updated_time`, `close_time`, `occurrence_datetime` on the
        # contract and `captured_at` on the capture. A consumer computes
        # them exactly, against its own clock rather than the capture's,
        # which is the more correct number anyway.
        #
        # Keeping them while skipping the rewrite would have been worse: an
        # unrewritten file would then advertise a countdown that had
        # silently expired. The helpers above remain for callers that want
        # these values live.
    }
