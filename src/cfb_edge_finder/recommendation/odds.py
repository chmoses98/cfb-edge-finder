"""American-odds formatting for contract prices. Presentation only.

A $1 binary contract at price p is a decimal payout of 1/p. American odds
express the same number in the convention US bettors read:

    p  < 0.5  ->  positive: +100 * (1 - p) / p     (profit per $100 risked)
    p >= 0.5  ->  negative: -100 * p / (1 - p)     ($ risked per $100 profit)

*** THIS FORMATS, IT DOES NOT ADVISE ***
Converting a price into another notation adds no information and implies
no opinion. The module is included now precisely because it is inert: it
has no eligibility logic, reads no thresholds, and touches no candidate.
The `0.5` below is the arithmetic pivot between the two conventions, not a
probability cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass

AMERICAN_ODDS_BASE = 100.0
EVEN_MONEY_PIVOT = 0.5
"""The point where the American convention flips sign. A property of the
notation, not a decision boundary."""


@dataclass(frozen=True)
class AmericanOdds:
    valid: bool
    value: int | None
    formatted: str
    reason: str = ""


def price_to_american_odds(price: float | None) -> AmericanOdds:
    """Format a contract price as American odds.

    Prices of exactly 0 or 1 are rejected: they imply infinite or zero
    payout, which has no American representation. Returning a huge number
    would look like a quote."""
    if price is None:
        return AmericanOdds(False, None, "-", "no price")
    if not (0.0 < price < 1.0):
        return AmericanOdds(False, None, "-", f"price {price} is outside the representable range (0, 1)")

    if price >= EVEN_MONEY_PIVOT:
        value = -round(AMERICAN_ODDS_BASE * price / (1.0 - price))
    else:
        value = round(AMERICAN_ODDS_BASE * (1.0 - price) / price)
    return AmericanOdds(True, value, f"{value:+d}")
