"""Kalshi fee arithmetic, against the exchange's CURRENT published rules.

*** THE DEFECT THESE TESTS EXIST TO PIN DOWN ***
The catalog's first fee helper rounded the model fee UP TO A WHOLE CENT.
Kalshi's actual rule -- https://docs.kalshi.com/getting_started/fee_rounding,
read live from a runner on 2026-09-18 and committed verbatim to
docs/evidence/kalshi_fee_and_override_probe.txt -- is that fees are
six-decimal dollar amounts and the trade fee is the model fee rounded up
to the nearest $0.000001.

On the exchange's own worked example the difference is not cosmetic:

    model fee  $0.00363825
    trade fee  ceil_6dp  -> $0.003639      <- correct
    old helper ceil_1c   -> $0.01          <- 2.75x overstatement

*** WHAT THE PUBLISHED CATALOG MAY AND MAY NOT CLAIM ***
Kalshi separates four things, and only the first is account-independent:

    trade fee    from the fee model, ceil to $0.000001
    rounding fee restores the member's target balance precision
    rebate       refund of accumulated rounding overpayment
    net fee      trade fee + rounding fee - rebate   (>= $0.00)

The last three depend on the member's balance precision ($0.0001 direct,
$0.01 non-direct) and on the per-ORDER fee accumulator carried across
fills. A public catalog knows none of that, so it publishes the trade fee
and says explicitly that it is not a net fee. The rounding and rebate
mechanics are implemented here and exercised against the official
examples -- proving we read the rules correctly -- but they are
deliberately NOT published.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.catalog.fees import (
    DIRECT_MEMBER_PRECISION_DOLLARS,
    FEE_GRANULARITY_DOLLARS,
    NON_DIRECT_MEMBER_PRECISION_DOLLARS,
    FeeModelSupport,
    FeeSource,
    ceil_to_granularity,
    fee_block,
    floor_to_precision,
    model_trade_fee,
    quadratic_model_fee,
    rebate_schedule,
    resolve_effective_fee,
    rounding_fee_components,
)

# =========================================================================
# THE OFFICIAL WORKED EXAMPLES
#
# Quoted from docs.kalshi.com/getting_started/fee_rounding as fetched on
# 2026-09-18. Every number below appears in that page.
# =========================================================================


def test_fees_are_six_decimal_dollar_amounts_not_whole_cents():
    """"Fees are six-decimal dollar amounts ($0.000001 granularity)"."""
    assert FEE_GRANULARITY_DOLLARS == 0.000001
    assert ceil_to_granularity(0.00363825) == pytest.approx(0.003639, abs=1e-12)
    # A value already on the grid must not be pushed up a tick.
    assert ceil_to_granularity(0.003639) == pytest.approx(0.003639, abs=1e-12)
    assert ceil_to_granularity(0.0) == pytest.approx(0.0)


def test_the_old_whole_cent_rounding_is_proven_wrong_on_kalshis_own_example():
    """The regression itself, stated as a number rather than a worry."""
    correct = ceil_to_granularity(0.00363825)
    old_behaviour = 0.01  # ceil to the nearest whole cent
    assert correct == pytest.approx(0.003639, abs=1e-12)
    assert old_behaviour / correct == pytest.approx(2.748, rel=1e-3)


def test_the_quadratic_model_fee_reproduces_the_official_example_exactly():
    """The docs' FCM example carries a model fee of $0.00363825. That is
    the quadratic schedule at a 5.5c price on one contract:

        0.07 * 1 * 0.055 * (1 - 0.055) = 0.00363825
    """
    assert quadratic_model_fee(0.055, contracts=1, fee_multiplier=1) == pytest.approx(
        0.00363825, abs=1e-11
    )
    assert model_trade_fee(0.055, contracts=1, fee_multiplier=1) == pytest.approx(
        0.003639, abs=1e-12
    )


def test_the_fcm_cleared_fill_example_reproduces_component_by_component():
    """Verbatim from the docs, for a NON-DIRECT member ($0.01 precision):

        signed revenue -$0.055000, model fee $0.00363825
        trade fee     = ceil_6dp($0.00363825)        = $0.003639
        aligned change= floor_cent(-0.055 - 0.003639) = -$0.060000
        rounding fee  = (-0.055 - 0.003639) - (-0.06) = $0.001361
        "the trade fee plus rounding fee is exactly $0.005"
    """
    components = rounding_fee_components(
        signed_revenue=-0.055,
        model_fee=0.00363825,
        target_precision=NON_DIRECT_MEMBER_PRECISION_DOLLARS,
    )
    assert components.trade_fee == pytest.approx(0.003639, abs=1e-9)
    assert components.aligned_change == pytest.approx(-0.060000, abs=1e-9)
    assert components.rounding_fee == pytest.approx(0.001361, abs=1e-9)
    assert components.trade_fee + components.rounding_fee == pytest.approx(0.005, abs=1e-9)


def test_the_accumulator_example_reproduces_row_for_row():
    """Verbatim from the docs, non-direct member ($0.01 precision):

        Fill  Added   Before Rebate   Rebate   Carried Forward
        1     $0.004  $0.004          -        $0.004
        2     $0.004  $0.008          -        $0.008
        3     $0.004  $0.012          $0.010   $0.002
    """
    rows = rebate_schedule(
        [0.004, 0.004, 0.004], target_precision=NON_DIRECT_MEMBER_PRECISION_DOLLARS
    )
    expected = [(0.004, 0.0, 0.004), (0.008, 0.0, 0.008), (0.012, 0.010, 0.002)]
    assert len(rows) == len(expected)
    for got, want in zip(rows, expected, strict=True):
        assert got[0] == pytest.approx(want[0], abs=1e-9)
        assert got[1] == pytest.approx(want[1], abs=1e-9)
        assert got[2] == pytest.approx(want[2], abs=1e-9)


def test_direct_and_non_direct_balance_precisions_are_both_carried():
    """"Direct member balances are aligned to $0.0001 (0.01c); non-direct
    member balances are aligned to $0.01 (1c)." A direct member's rebate
    follows the same mechanics in $0.0001 increments."""
    assert DIRECT_MEMBER_PRECISION_DOLLARS == 0.0001
    assert NON_DIRECT_MEMBER_PRECISION_DOLLARS == 0.01
    assert floor_to_precision(-0.058639, NON_DIRECT_MEMBER_PRECISION_DOLLARS) == pytest.approx(
        -0.06, abs=1e-9
    )
    # The same fill on a direct member's grid rounds far less far.
    assert floor_to_precision(-0.058639, DIRECT_MEMBER_PRECISION_DOLLARS) == pytest.approx(
        -0.0587, abs=1e-9
    )
    # "Direct-member rebates follow the same mechanics in $0.0001
    # increments." Same mechanics, finer grid: scale the official
    # accumulator example down by 100 and it reproduces row for row.
    direct = rebate_schedule([0.00004] * 3, DIRECT_MEMBER_PRECISION_DOLLARS)
    for got, want in zip(
        direct,
        [(0.00004, 0.0, 0.00004), (0.00008, 0.0, 0.00008), (0.00012, 0.0001, 0.00002)],
        strict=True,
    ):
        assert got == pytest.approx(want, abs=1e-12)
    # And a rounding overpayment already at or above the grid is rebated on
    # the fill itself rather than carried -- the finer the precision, the
    # less the accumulator has to carry.
    assert rebate_schedule([0.0004], DIRECT_MEMBER_PRECISION_DOLLARS)[0] == pytest.approx(
        (0.0004, 0.0004, 0.0), abs=1e-12
    )


def test_the_quadratic_schedule_peaks_at_the_middle_and_scales_linearly():
    at_mid = quadratic_model_fee(0.50, 1, 1)
    at_edge = quadratic_model_fee(0.02, 1, 1)
    assert at_mid == pytest.approx(0.07 * 0.25, abs=1e-12)
    assert at_mid > at_edge
    # Contracts and multiplier are both linear factors.
    assert quadratic_model_fee(0.50, 10, 1) == pytest.approx(10 * at_mid, rel=1e-9)
    assert quadratic_model_fee(0.50, 1, 2) == pytest.approx(2 * at_mid, rel=1e-9)


def test_no_price_and_no_multiplier_both_yield_no_fee():
    """*** FAIL CLOSED ***
    A missing multiplier is a data failure. Substituting 1 -- the value
    every live CFB series happens to carry -- would make the failure
    invisible precisely where it matters, on the series that differs."""
    assert quadratic_model_fee(None, 1, 1) is None
    assert quadratic_model_fee(0.50, 1, None) is None
    assert model_trade_fee(0.50, 1, None) is None


# =========================================================================
# EFFECTIVE FEE RESOLUTION: EVENT OVERRIDE, ELSE SERIES
#
# Kalshi layers `fee_type_override` / `fee_multiplier_override` on the
# EVENT above the parent series' `fee_type` / `fee_multiplier`.
#
# The live survey (docs/evidence/kalshi_fee_and_override_probe.txt,
# 2026-09-18) found the override keys on 0 of 1,937 open CFB events --
# they are not present in the payload at all -- and 0 CFB events whose
# effective fee differs from its series. 145 CFB series: fee_type
# quadratic 136 / quadratic_with_maker_fees 9, fee_multiplier 1 on all
# 145, 0 series missing fee_type. Markets carry no fee keys of their own.
#
# Precedence is implemented and tested anyway. A rule that is correct only
# because nothing exercises it is not a rule, and the day Kalshi sets one
# override is not the day to discover the catalog ignores it. The event
# object is already in hand during capture, so honouring it costs no
# additional request.
# =========================================================================

SERIES = {"fee_type": "quadratic", "fee_multiplier": 1}


def test_with_no_override_the_series_fee_applies():
    fee = resolve_effective_fee({}, SERIES)
    assert fee.fee_type == "quadratic"
    assert fee.fee_multiplier == pytest.approx(1.0)
    assert fee.source is FeeSource.SERIES
    assert fee.support is FeeModelSupport.SUPPORTED


def test_an_event_override_wins_over_the_series():
    fee = resolve_effective_fee(
        {"fee_type_override": "quadratic_with_maker_fees", "fee_multiplier_override": 2},
        SERIES,
    )
    assert fee.fee_type == "quadratic_with_maker_fees"
    assert fee.fee_multiplier == pytest.approx(2.0)
    assert fee.source is FeeSource.EVENT_OVERRIDE
    assert fee.maker_fee_applies is True


def test_a_partial_override_inherits_the_field_it_does_not_set():
    """An override may set one field only. The other is not thereby lost."""
    mult_only = resolve_effective_fee({"fee_multiplier_override": 3}, SERIES)
    assert mult_only.fee_type == "quadratic"
    assert mult_only.fee_multiplier == pytest.approx(3.0)
    assert mult_only.source is FeeSource.EVENT_OVERRIDE

    type_only = resolve_effective_fee({"fee_type_override": "quadratic_with_maker_fees"}, SERIES)
    assert type_only.fee_type == "quadratic_with_maker_fees"
    assert type_only.fee_multiplier == pytest.approx(1.0)
    assert type_only.source is FeeSource.EVENT_OVERRIDE


def test_a_cleared_override_falls_back_to_the_series():
    """null / "" / absent are all the ABSENCE of an override -- not an
    instruction to discard the series fee."""
    for event in (
        {},
        {"fee_type_override": None, "fee_multiplier_override": None},
        {"fee_type_override": "", "fee_multiplier_override": ""},
    ):
        fee = resolve_effective_fee(event, SERIES)
        assert fee.fee_type == "quadratic", event
        assert fee.fee_multiplier == pytest.approx(1.0), event
        assert fee.source is FeeSource.SERIES, event


def test_an_override_to_an_unknown_fee_type_produces_no_guessed_fee():
    fee = resolve_effective_fee({"fee_type_override": "some_future_schedule"}, SERIES)
    assert fee.fee_type == "some_future_schedule"
    assert fee.source is FeeSource.EVENT_OVERRIDE
    assert fee.support is FeeModelSupport.UNSUPPORTED_MODEL
    assert fee.computable is False
    assert fee.maker_fee_applies is None
    assert "some_future_schedule" in fee.unavailable_reason
    # Specifically: it does NOT silently price the series' quadratic fee.
    assert fee_block(fee, yes_ask=0.51, yes_mid=0.50)["model_trade_fee_at_yes_ask"] is None


# =========================================================================
# FAIL CLOSED ON MISSING METADATA
#
# "Do not convert a data failure into a plausible default." A missing
# fee_type is not quadratic and a missing fee_multiplier is not 1, even
# though both are what every live CFB series carries today. Those
# defaults would be right in the ordinary case and silently wrong in
# exactly the case a consumer needs to be warned about.
# =========================================================================


def test_a_missing_fee_type_is_unknown_not_quadratic():
    fee = resolve_effective_fee({}, {"fee_multiplier": 1})
    assert fee.fee_type is None
    assert fee.support is FeeModelSupport.METADATA_UNAVAILABLE
    assert fee.computable is False
    assert fee.maker_fee_applies is None
    assert fee.unavailable_reason


def test_a_missing_fee_multiplier_is_unknown_not_one():
    fee = resolve_effective_fee({}, {"fee_type": "quadratic"})
    assert fee.fee_multiplier is None
    assert fee.support is FeeModelSupport.METADATA_UNAVAILABLE
    assert fee.computable is False
    assert fee.unavailable_reason


def test_a_series_that_could_not_be_resolved_is_distinct_from_one_without_fees():
    """Operationally different failures, reported differently: a request
    that failed, versus a series that answered without fee metadata."""
    failed = resolve_effective_fee({}, None, series_lookup_succeeded=False)
    assert failed.source is FeeSource.UNAVAILABLE
    assert failed.support is FeeModelSupport.METADATA_UNAVAILABLE
    assert failed.computable is False
    assert failed.unavailable_reason != resolve_effective_fee({}, {}).unavailable_reason


def test_an_unavailable_fee_still_publishes_a_block_that_explains_itself():
    """A null fee is only safe if the consumer is told WHY it is null. A
    bare null is indistinguishable from 'this trade is free'."""
    block = fee_block(
        resolve_effective_fee({}, None, series_lookup_succeeded=False),
        yes_ask=0.51,
        yes_mid=0.50,
    )
    assert block["model_trade_fee_at_yes_ask"] is None
    assert block["model_trade_fee_at_yes_mid"] is None
    assert block["formula"] is None
    assert block["unavailable_reason"]
    assert block["is_net_fee"] is False


# =========================================================================
# WHAT THE PUBLISHED BLOCK REPRESENTS
# =========================================================================


def test_the_published_headline_is_the_executable_price_not_the_mid():
    """For a betting consumer the fee that matters is the fee on the order
    it can actually send. A YES purchase executes at the ASK. The mid
    figure stays available as a market mechanic, under a name that cannot
    be mistaken for the other."""
    block = fee_block(resolve_effective_fee({}, SERIES), yes_ask=0.51, yes_mid=0.50)
    assert block["basis_yes_ask"] == pytest.approx(0.51)
    assert block["basis_yes_mid"] == pytest.approx(0.50)
    assert block["model_trade_fee_at_yes_ask"] == pytest.approx(
        model_trade_fee(0.51, 1, 1), abs=1e-12
    )
    assert block["model_trade_fee_at_yes_mid"] == pytest.approx(
        model_trade_fee(0.50, 1, 1), abs=1e-12
    )
    # Neither key is named in a way that could pass for a net fee.
    assert all("net" not in key for key in block if key != "is_net_fee")


def test_every_published_fee_field_states_what_it_represents():
    block = fee_block(resolve_effective_fee({}, SERIES), yes_ask=0.51, yes_mid=0.50)
    # the schedule and where it came from
    assert block["model"] == "quadratic"
    assert block["multiplier"] == pytest.approx(1.0)
    assert block["source"] == str(FeeSource.SERIES)
    assert block["support"] == str(FeeModelSupport.SUPPORTED)
    # what it is NOT
    assert block["is_net_fee"] is False
    assert set(block["excludes"]) == {
        "rounding_fee",
        "rebate",
        "fee_accumulator",
        "user_balance_precision",
    }
    # the size it is quoted for, and the arithmetic used
    assert block["per_contracts"] == pytest.approx(1.0)
    assert "0.07" in block["formula"] and "0.000001" in block["formula"]
    assert block["maker_fee_applies"] is False


def test_the_fee_is_quoted_per_a_stated_number_of_contracts():
    """A fee that does not say how many contracts it covers is unusable,
    and the quadratic schedule is linear in contracts, so a consumer that
    assumes 1 on a block quoted for 10 is out by 10x."""
    ten = fee_block(resolve_effective_fee({}, SERIES), yes_ask=0.51, yes_mid=0.50, contracts=10)
    one = fee_block(resolve_effective_fee({}, SERIES), yes_ask=0.51, yes_mid=0.50, contracts=1)
    assert ten["per_contracts"] == pytest.approx(10.0)
    assert ten["model_trade_fee_at_yes_ask"] == pytest.approx(
        10 * one["model_trade_fee_at_yes_ask"], rel=1e-4
    )
