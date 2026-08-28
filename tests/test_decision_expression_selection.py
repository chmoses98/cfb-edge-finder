"""Expression selection: cheapest ALL-IN cost, with deterministic ties."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from cfb_edge_finder.decision.expression_selection import (
    ExpressionRejection,
    build_option,
    estimate_taker_fee,
    select_expression,
)
from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.expression.taxonomy import ContractSemantics
from cfb_edge_finder.kalshi.fee_schedule import (
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    calculate_fee_cents,
)
from cfb_edge_finder.schemas.common import MarketFamily, Side

NOW = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)


def snap(
    ticker: str = "T1",
    *,
    yes: float | None = 0.50,
    no: float | None = 0.52,
    market_status: str | None = "active",
    fee_status: str | None = "VERIFIED_CURRENT",
    captured_at: str | None = "__default__",
    parse_status: str = "confirmed_live",
) -> ContractSnapshot:
    return ContractSnapshot(
        semantics=ContractSemantics(
            market_ticker=ticker,
            game_id="g1",
            family=MarketFamily.MONEYLINE,
            team=Side.HOME,
            side=None,
            threshold=None,
            semantic_operator=">",
            parse_status=parse_status,
        ),
        timing_label="T_24H",
        captured_at=(
            (NOW - timedelta(seconds=30)).isoformat() if captured_at == "__default__" else captured_at
        ),
        model_probability=0.6,
        executable_yes_price=yes,
        executable_no_price=no,
        market_status=market_status,
        fee_status=fee_status,
        fee_schedule_version="kalshi_fee_schedule_2026_07_07_taker",
        model_version="m1",
        pricing_status="ok",
        series_ticker="KXNCAAFGAME",
        schema_version="research_corpus_v2",
        capture_mode="PROSPECTIVE",
    )


def option(**kwargs):
    return build_option(snap(**kwargs), Side.YES, now=NOW, max_quote_age_seconds=300)


# --------------------------------------------------------------- fee


def test_fee_agrees_with_the_verified_schedule_at_every_cent():
    for cents in range(1, 100):
        expected = calculate_fee_cents(cents, 1, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER) / 100
        assert estimate_taker_fee(cents / 100) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("price", [0.0, 1.0, 0.005, 0.995, -0.1, 1.5])
def test_untradeable_price_returns_none(price):
    """None, not zero. A zero fee would let an unbuyable quote win a
    cheapest-all-in comparison."""
    assert estimate_taker_fee(price) is None


# ------------------------------------------------------- rejections


def test_a_clean_snapshot_is_selectable():
    assert option().selectable


def test_missing_executable_price_is_refused():
    opt = build_option(snap(yes=None), Side.YES, now=NOW, max_quote_age_seconds=300)
    assert ExpressionRejection.NO_EXECUTABLE_SIDE in opt.rejections
    assert not opt.selectable


@pytest.mark.parametrize("status", [None, "", "closed", "settled", "unopened"])
def test_non_active_market_is_refused(status):
    opt = build_option(snap(market_status=status), Side.YES, now=NOW, max_quote_age_seconds=300)
    assert ExpressionRejection.MARKET_NOT_EXECUTABLE in opt.rejections


@pytest.mark.parametrize("status", [None, "", "UNVERIFIED", "PLACEHOLDER"])
def test_unverified_fee_is_refused(status):
    opt = build_option(snap(fee_status=status), Side.YES, now=NOW, max_quote_age_seconds=300)
    assert ExpressionRejection.FEE_UNVERIFIED in opt.rejections


def test_unresolved_semantics_are_refused():
    opt = build_option(snap(parse_status="heuristic"), Side.YES, now=NOW, max_quote_age_seconds=300)
    assert ExpressionRejection.SEMANTICS_UNRESOLVED in opt.rejections


def test_unconfigured_quote_age_fails_closed():
    """An uncertified quote age is not a fresh quote. Treating None as
    'no limit' is how a stale price gets used at a CLOSING window."""
    opt = build_option(snap(), Side.YES, now=NOW, max_quote_age_seconds=None)
    assert ExpressionRejection.QUOTE_STALE in opt.rejections


def test_stale_quote_is_refused_and_the_boundary_is_exact():
    fresh = build_option(
        snap(captured_at=(NOW - timedelta(seconds=300)).isoformat()),
        Side.YES,
        now=NOW,
        max_quote_age_seconds=300,
    )
    assert ExpressionRejection.QUOTE_STALE not in fresh.rejections

    stale = build_option(
        snap(captured_at=(NOW - timedelta(seconds=301)).isoformat()),
        Side.YES,
        now=NOW,
        max_quote_age_seconds=300,
    )
    assert ExpressionRejection.QUOTE_STALE in stale.rejections


def test_unparseable_capture_time_is_stale_not_fresh():
    opt = build_option(snap(captured_at="not-a-timestamp"), Side.YES, now=NOW, max_quote_age_seconds=300)
    assert ExpressionRejection.QUOTE_STALE in opt.rejections


def test_missing_capture_time_is_stale():
    opt = build_option(snap(captured_at=""), Side.YES, now=NOW, max_quote_age_seconds=300)
    assert ExpressionRejection.QUOTE_STALE in opt.rejections


def test_every_rejection_reason_is_reported_together():
    opt = build_option(
        snap(yes=None, market_status="closed", fee_status=None, parse_status="guess"),
        Side.YES,
        now=NOW,
        max_quote_age_seconds=None,
    )
    assert set(opt.rejections) == set(ExpressionRejection)


# -------------------------------------------------------- selection


def test_selection_ranks_on_all_in_cost_not_displayed_price():
    """The headline property. A 48c quote whose fee is 2c costs 50c
    all-in; a 49c quote whose fee is 1c costs 50c too. Ranking on the
    displayed price alone picks the wrong contract whenever the fee
    curve is steeper than the price gap."""
    cheap_display = build_option(
        snap("A", yes=0.50), Side.YES, now=NOW, max_quote_age_seconds=300
    )  # 0.50 + 0.02 = 0.52
    cheap_all_in = build_option(
        snap("B", yes=0.51), Side.YES, now=NOW, max_quote_age_seconds=300
    )  # 0.51 + 0.02 = 0.53
    assert cheap_display.all_in_cost < cheap_all_in.all_in_cost
    assert select_expression("k", [cheap_all_in, cheap_display]).selected is cheap_display


def test_selection_prefers_a_genuinely_cheaper_all_in_option():
    expensive = build_option(snap("A", yes=0.60), Side.YES, now=NOW, max_quote_age_seconds=300)
    cheap = build_option(snap("B", yes=0.30), Side.YES, now=NOW, max_quote_age_seconds=300)
    assert select_expression("k", [expensive, cheap]).selected is cheap


def test_unselectable_options_never_win():
    good = build_option(snap("A", yes=0.60), Side.YES, now=NOW, max_quote_age_seconds=300)
    cheap_but_closed = build_option(
        snap("B", yes=0.10, market_status="closed"), Side.YES, now=NOW, max_quote_age_seconds=300
    )
    assert select_expression("k", [cheap_but_closed, good]).selected is good


def test_no_selectable_option_selects_nothing_and_says_why():
    closed = build_option(snap("A", market_status="closed"), Side.YES, now=NOW, max_quote_age_seconds=300)
    selection = select_expression("k", [closed])
    assert selection.selected is None
    assert "no selectable expression" in selection.reason
    assert selection.selectable_options == ()


def test_empty_option_list_selects_nothing():
    assert select_expression("k", []).selected is None


def test_ties_break_deterministically_by_ticker():
    a = build_option(snap("AAA", yes=0.50), Side.YES, now=NOW, max_quote_age_seconds=300)
    b = build_option(snap("BBB", yes=0.50), Side.YES, now=NOW, max_quote_age_seconds=300)
    assert select_expression("k", [b, a]).selected.market_ticker == "AAA"
    assert select_expression("k", [a, b]).selected.market_ticker == "AAA"


def test_ties_break_yes_before_no_at_equal_cost_and_ticker():
    yes = build_option(snap("T", yes=0.50), Side.YES, now=NOW, max_quote_age_seconds=300)
    no = build_option(snap("T", no=0.50), Side.NO, now=NOW, max_quote_age_seconds=300)
    assert yes.all_in_cost == no.all_in_cost
    assert select_expression("k", [no, yes]).selected.side is Side.YES


def test_a_tie_is_declared_in_the_reason():
    a = build_option(snap("AAA", yes=0.50), Side.YES, now=NOW, max_quote_age_seconds=300)
    b = build_option(snap("BBB", yes=0.50), Side.YES, now=NOW, max_quote_age_seconds=300)
    assert "tie broken deterministically" in select_expression("k", [a, b]).reason


def test_selection_is_stable_under_input_reordering():
    options = [
        build_option(snap(t, yes=p), Side.YES, now=NOW, max_quote_age_seconds=300)
        for t, p in [("A", 0.50), ("B", 0.50), ("C", 0.40), ("D", 0.55)]
    ]
    expected = select_expression("k", options).selected
    rng = random.Random(7)
    for _ in range(20):
        shuffled = options[:]
        rng.shuffle(shuffled)
        assert select_expression("k", shuffled).selected == expected


def test_selection_produces_no_stake_or_recommendation_fields():
    selection = select_expression("k", [option()])
    for banned in ("stake", "bet", "wager", "units", "size"):
        assert not any(banned in field for field in selection.__dataclass_fields__)
        assert not any(banned in field for field in selection.selected.__dataclass_fields__)
