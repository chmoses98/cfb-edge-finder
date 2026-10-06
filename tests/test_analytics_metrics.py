"""Mission sections 25, 26: CLV semantics and research-unit economics.

The load-bearing property is SIGN CORRECTNESS. A CLV number with the
wrong sign is worse than no CLV number: it reads as evidence pointing the
opposite way, and every downstream conclusion inverts.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.analytics.metrics import (
    CLOSING_CAPTURED_STATUS,
    closing_line_value,
    probability_gaps,
)
from cfb_edge_finder.research.attribution import research_unit_economics
from cfb_edge_finder.schemas.common import Side

CAPTURED = CLOSING_CAPTURED_STATUS


def _clv(side, entry, close, status=CAPTURED):
    return closing_line_value(side=side, entry_price=entry, closing_price=close, closing_status=status)


# --- Gap metrics (section 3) ---------------------------------------------


def test_yes_and_no_gaps_use_their_own_prices():
    """The NO gap is (1 - model) - no_price, NOT the YES gap negated."""
    g = probability_gaps(model_probability=0.60, executable_yes_price=0.55, executable_no_price=0.50)
    assert g.yes_probability_gap == pytest.approx(0.05)
    assert g.no_probability_gap == pytest.approx(-0.10)


def test_gaps_do_not_assume_complementary_quotes():
    """Real corpus case: yes=0.74 with no=0.93 (sum 1.67). Both gaps must
    be computed from their own quote, and here both are negative."""
    g = probability_gaps(model_probability=0.475, executable_yes_price=0.74, executable_no_price=0.93)
    assert g.yes_probability_gap == pytest.approx(0.475 - 0.74)
    assert g.no_probability_gap == pytest.approx((1 - 0.475) - 0.93)
    assert g.yes_probability_gap < 0 and g.no_probability_gap < 0


def test_missing_side_quote_yields_none_for_that_side_only():
    g = probability_gaps(model_probability=0.6, executable_yes_price=0.5, executable_no_price=None)
    assert g.yes_probability_gap is not None and g.no_probability_gap is None
    assert g.max_signed_gap == g.yes_probability_gap


def test_missing_model_probability_yields_no_gaps():
    g = probability_gaps(model_probability=None, executable_yes_price=0.5, executable_no_price=0.5)
    assert g.yes_probability_gap is None and g.no_probability_gap is None and g.max_signed_gap is None


# --- CLV sign semantics (section 25) -------------------------------------


def test_yes_price_rises_is_favorable_for_yes():
    c = _clv(Side.YES, 0.40, 0.55)
    assert c.available and c.raw_price_movement == pytest.approx(0.15) and c.favorable is True


def test_yes_price_falls_is_unfavorable_for_yes():
    c = _clv(Side.YES, 0.55, 0.40)
    assert c.raw_price_movement == pytest.approx(-0.15) and c.favorable is False


def test_no_price_rises_is_favorable_for_no():
    """The sign trap: the same underlying move that hurts YES helps NO.
    Measured against NO's own price, a rise is favorable."""
    c = _clv(Side.NO, 0.30, 0.45)
    assert c.raw_price_movement == pytest.approx(0.15) and c.favorable is True


def test_no_price_falls_is_unfavorable_for_no():
    c = _clv(Side.NO, 0.45, 0.30)
    assert c.raw_price_movement == pytest.approx(-0.15) and c.favorable is False


def test_opposite_sides_of_the_same_move_disagree():
    """One market move; the two sides must reach opposite verdicts."""
    yes = _clv(Side.YES, 0.40, 0.55)   # YES got dearer
    no = _clv(Side.NO, 0.60, 0.45)     # ...so NO got cheaper
    assert yes.favorable is True and no.favorable is False


def test_asymmetric_quotes_are_handled_independently():
    yes = _clv(Side.YES, 0.74, 0.80)
    no = _clv(Side.NO, 0.93, 0.88)
    assert yes.favorable is True and no.favorable is False
    assert yes.raw_price_movement != -no.raw_price_movement, "sides were treated as complements"


def test_identical_close_is_neither_favorable_nor_unfavorable():
    c = _clv(Side.YES, 0.50, 0.50)
    assert c.available and c.raw_price_movement == 0.0
    assert c.favorable is None, "a flat close was recorded as unfavorable"


@pytest.mark.parametrize(
    "status",
    ["CLOSING_MISSING_MARKET_CLOSED", "CLOSING_MISSING_API_FAILURE",
     "CLOSING_MISSING_NO_EXECUTABLE_QUOTE", "CLOSING_MISSING_MAPPING_FAILURE",
     "CLOSING_MISSING_NO_SCAN_IN_WINDOW", "CLOSING_NOT_APPLICABLE"],
)
def test_missing_close_is_unavailable_never_zero(status):
    """Mission section 6: a missing close must never enter an aggregate
    as 0.0 -- that is a real CLV value meaning 'did not move'."""
    c = closing_line_value(side=Side.YES, entry_price=0.4, closing_price=None, closing_status=status)
    assert c.available is False
    assert c.raw_price_movement is None, "missing close produced a numeric CLV"
    assert c.reason == status


def test_captured_status_but_absent_side_quote_is_unavailable():
    c = closing_line_value(side=Side.NO, entry_price=0.3, closing_price=None, closing_status=CAPTURED)
    assert c.available is False and c.reason == "MISSING_SIDE_QUOTE"


@pytest.mark.parametrize("bad", [-0.01, 1.01, 5.0])
def test_impossible_prices_are_rejected(bad):
    assert _clv(Side.YES, bad, 0.5).available is False
    assert _clv(Side.YES, 0.5, bad).available is False


def test_boundary_prices_are_valid_and_logit_is_clamped():
    """0 and 1 are real quotes; they must not be dropped, and must not
    produce infinite logit movement."""
    c = _clv(Side.YES, 0.01, 0.99)
    assert c.available is True
    assert c.logit_movement is not None and abs(c.logit_movement) < 100

    edge = _clv(Side.YES, 0.0, 1.0)
    assert edge.available is True and edge.raw_price_movement == pytest.approx(1.0)
    assert edge.logit_movement is not None and abs(edge.logit_movement) < 100


# --- Research-unit economics (section 26) --------------------------------

SERIES = "KXNCAAFGAME"


def test_yes_win_and_loss():
    win = research_unit_economics(side=Side.YES, entry_price=0.40, event_true=True, series_ticker=SERIES)
    assert win.settlement_value == 1.0 and win.research_unit_pnl == pytest.approx(0.60)
    loss = research_unit_economics(side=Side.YES, entry_price=0.40, event_true=False, series_ticker=SERIES)
    assert loss.settlement_value == 0.0 and loss.research_unit_pnl == pytest.approx(-0.40)


def test_no_win_and_loss_are_the_inverse_event():
    win = research_unit_economics(side=Side.NO, entry_price=0.35, event_true=False, series_ticker=SERIES)
    assert win.settlement_value == 1.0 and win.research_unit_pnl == pytest.approx(0.65)
    loss = research_unit_economics(side=Side.NO, entry_price=0.35, event_true=True, series_ticker=SERIES)
    assert loss.settlement_value == 0.0 and loss.research_unit_pnl == pytest.approx(-0.35)


def test_fee_reduces_pnl_on_both_a_win_and_a_loss():
    win = research_unit_economics(side=Side.YES, entry_price=0.5, event_true=True, series_ticker=SERIES)
    loss = research_unit_economics(side=Side.YES, entry_price=0.5, event_true=False, series_ticker=SERIES)
    for e in (win, loss):
        assert e.estimated_fee is not None and e.estimated_fee > 0
        assert e.fee_adjusted_research_unit_pnl == pytest.approx(e.research_unit_pnl - e.estimated_fee)


def test_fees_are_computed_per_side_price():
    y = research_unit_economics(side=Side.YES, entry_price=0.74, event_true=True, series_ticker=SERIES)
    n = research_unit_economics(side=Side.NO, entry_price=0.93, event_true=False, series_ticker=SERIES)
    assert y.estimated_fee != n.estimated_fee


@pytest.mark.parametrize("price", [0.01, 0.99])
def test_extreme_prices_stay_finite(price):
    e = research_unit_economics(side=Side.YES, entry_price=price, event_true=True, series_ticker=SERIES)
    assert e.research_unit_pnl == pytest.approx(1.0 - price)
    assert e.return_on_entry_price is not None


def test_zero_price_return_is_undefined_and_fee_unknown():
    e = research_unit_economics(side=Side.YES, entry_price=0.0, event_true=True, series_ticker=SERIES)
    assert e.return_on_entry_price is None, "zero-cost entry produced an infinite return"
    assert e.estimated_fee is None
    assert e.fee_adjusted_research_unit_pnl is None, "missing fee was silently treated as zero"


def test_price_of_one_has_no_upside():
    e = research_unit_economics(side=Side.YES, entry_price=1.0, event_true=True, series_ticker=SERIES)
    assert e.research_unit_pnl == pytest.approx(0.0)
    assert e.estimated_fee is None  # outside the tradeable range


def test_missing_entry_price_yields_no_economics():
    assert research_unit_economics(
        side=Side.YES, entry_price=None, event_true=True, series_ticker=SERIES
    ) is None


def test_economics_is_defined_for_yes_and_no_only():
    with pytest.raises(ValueError):
        research_unit_economics(side=Side.HOME, entry_price=0.5, event_true=True, series_ticker=SERIES)
