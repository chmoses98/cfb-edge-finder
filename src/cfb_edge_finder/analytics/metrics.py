"""Per-observation research metrics: model-market gaps, side-aware
closing-line value, and fee-adjusted one-contract economics.

*** SIDE-AWARENESS IS THE WHOLE POINT ***
A naive "the price went up, so the market moved our way" is wrong half
the time. A YES position and a NO position on the same contract move in
OPPOSITE directions as the YES price rises. So every metric here is
computed against the side's OWN price series:

    YES CLV = closing_yes_price - entry_yes_price
    NO  CLV = closing_no_price  - entry_no_price

Both are "did the thing I bought get more expensive?", which is the
side-correct question. Deriving the NO series from the YES series would
be both wrong (see below) and sign-inverted.

*** YES AND NO ARE INDEPENDENT QUOTES, NOT COMPLEMENTS ***
`executable_no_price` is captured independently off the order book and is
NOT `1 - executable_yes_price`. The live corpus proves it: a real
captured contract quotes yes=0.74 alongside no=0.93, summing to 1.67 --
the bid/ask spread on both sides plus microstructure. Every function here
therefore takes each side's own price and never derives one from the
other.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfb_edge_finder.schemas.common import Side

ANALYTICS_CODE_VERSION = "analytics_v1"


# --- Model-market gap (mission section 3) --------------------------------


@dataclass(frozen=True)
class ProbabilityGaps:
    """Signed disagreement between the model and each executable side.

    Positive `yes_probability_gap` means the model thinks YES is worth
    MORE than it costs. Positive `no_probability_gap` means the same for
    NO. Both can be positive at once only if the two quotes sum to less
    than 1, which real spreads make unusual but not impossible to
    observe -- so this is not asserted away."""

    yes_probability_gap: float | None
    no_probability_gap: float | None

    @property
    def max_signed_gap(self) -> float | None:
        """The larger of the two side gaps, whichever side it favours.
        Deliberately NOT called "best edge": it is a descriptive maximum,
        not a selection."""
        present = [g for g in (self.yes_probability_gap, self.no_probability_gap) if g is not None]
        return max(present) if present else None


def probability_gaps(
    *, model_probability: float | None, executable_yes_price: float | None, executable_no_price: float | None
) -> ProbabilityGaps:
    """Each side's gap against its OWN executable price.

    `model_probability` is the model's probability that the contract's
    condition holds -- i.e. the YES side. The NO side's fair value is its
    complement, `1 - model_probability`, but its PRICE is the separately
    captured NO quote."""
    if model_probability is None:
        return ProbabilityGaps(None, None)
    yes_gap = None if executable_yes_price is None else model_probability - executable_yes_price
    no_gap = None if executable_no_price is None else (1.0 - model_probability) - executable_no_price
    return ProbabilityGaps(yes_probability_gap=yes_gap, no_probability_gap=no_gap)


# --- Closing-line value (mission sections 5, 6) --------------------------

CLOSING_CAPTURED_STATUS = "CLOSING_CAPTURED"


@dataclass(frozen=True)
class ClosingLineValue:
    """Side-aware CLV for one observation.

    `available` is False whenever no genuine CLOSING quote exists for this
    side. A missing close is NEVER represented as 0.0: zero is a real,
    meaningful CLV value ("the price did not move"), and conflating it
    with "we do not know" would silently drag every aggregate toward zero
    by exactly the number of markets we failed to capture."""

    available: bool
    reason: str
    side: Side | None = None
    entry_price: float | None = None
    closing_price: float | None = None
    raw_price_movement: float | None = None
    logit_movement: float | None = None
    favorable: bool | None = None


def _logit(p: float, *, eps: float = 1e-6) -> float:
    """Clamped logit. A 0 or 1 price is a real quote (a market can trade
    at the boundary) but has infinite logit, so it is clamped rather than
    dropped -- dropping would bias the sample toward the middle."""
    import math

    clamped = min(max(p, eps), 1.0 - eps)
    return math.log(clamped / (1.0 - clamped))


def closing_line_value(
    *,
    side: Side,
    entry_price: float | None,
    closing_price: float | None,
    closing_status: str,
) -> ClosingLineValue:
    """CLV for ONE side, measured against that side's own price series.

    Positive means the side we entered became MORE expensive by the
    close -- the market moved toward our position. That reading holds
    identically for YES and NO precisely because each is compared to its
    own price."""
    if closing_status != CLOSING_CAPTURED_STATUS:
        return ClosingLineValue(available=False, reason=closing_status, side=side)
    if entry_price is None or closing_price is None:
        return ClosingLineValue(
            available=False,
            reason="MISSING_SIDE_QUOTE",
            side=side,
            entry_price=entry_price,
            closing_price=closing_price,
        )
    for label, value in (("entry", entry_price), ("closing", closing_price)):
        if not (0.0 <= value <= 1.0):
            return ClosingLineValue(
                available=False, reason=f"INVALID_{label.upper()}_PRICE", side=side,
                entry_price=entry_price, closing_price=closing_price,
            )

    raw = closing_price - entry_price
    return ClosingLineValue(
        available=True,
        reason=CLOSING_CAPTURED_STATUS,
        side=side,
        entry_price=entry_price,
        closing_price=closing_price,
        raw_price_movement=raw,
        logit_movement=_logit(closing_price) - _logit(entry_price),
        # Exactly zero is neither favorable nor unfavorable; recording it
        # as False would overstate how often the market moved against us.
        favorable=None if raw == 0.0 else raw > 0.0,
    )
