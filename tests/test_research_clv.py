"""Mission section 14: CLV / market movement research metrics, formulas."""

from __future__ import annotations

import pytest

from cfb_edge_finder.research.clv import (
    build_gap_record,
    closing_price_delta,
    compute_market_movement,
    fee_adjusted_clv,
    market_move_toward_model,
)


def test_closing_price_delta_is_signed_difference():
    assert closing_price_delta(0.50, 0.60) == pytest.approx(0.10)
    assert closing_price_delta(0.60, 0.50) == pytest.approx(-0.10)


def test_market_move_toward_model_positive_when_closer_to_model():
    # entry 0.50, model 0.65, close 0.60 -- moved closer to model.
    result = market_move_toward_model(0.50, 0.60, 0.65)
    assert result > 0


def test_market_move_toward_model_negative_when_further_from_model():
    result = market_move_toward_model(0.60, 0.50, 0.65)
    assert result < 0


def test_market_move_toward_model_none_without_model_probability():
    assert market_move_toward_model(0.50, 0.60, None) is None


def test_fee_adjusted_clv_subtracts_round_trip_fees():
    raw = 0.10
    result = fee_adjusted_clv(raw, estimated_taker_fee_entry=0.01, estimated_taker_fee_closing=0.02)
    assert result == pytest.approx(raw - 0.03)


def test_fee_adjusted_clv_none_when_fee_unknown():
    assert fee_adjusted_clv(0.10, None, 0.02) is None
    assert fee_adjusted_clv(0.10, 0.01, None) is None


def test_compute_market_movement_end_to_end():
    result = compute_market_movement(
        entry_snapshot_price=0.50,
        closing_price=0.58,
        model_probability_at_entry=0.62,
        estimated_taker_fee_entry=0.01,
        estimated_taker_fee_closing=0.015,
        time_to_kickoff_hours_at_entry=72.0,
    )
    assert result.raw_price_movement == 0.08 or abs(result.raw_price_movement - 0.08) < 1e-9
    assert result.fee_adjusted_movement is not None
    assert result.time_to_kickoff_hours_at_entry == 72.0


def test_build_gap_record_gross_and_fee_adjusted():
    record = build_gap_record(
        market_ticker="MKT-1", model_probability=0.62, executable_market_probability_at_capture=0.55,
        estimated_taker_fee=0.01, closing_market_probability=0.58, contract_settled_yes=True,
    )
    assert abs(record.gross_gap - 0.07) < 1e-9
    assert abs(record.fee_adjusted_gap - 0.06) < 1e-9
    assert record.actual_result_hit is True  # model favored YES (0.62>0.5) and it settled YES


def test_build_gap_record_hit_false_when_model_favored_side_lost():
    record = build_gap_record(
        market_ticker="MKT-1", model_probability=0.62, executable_market_probability_at_capture=0.55,
        estimated_taker_fee=None, closing_market_probability=None, contract_settled_yes=False,
    )
    assert record.actual_result_hit is False
    assert record.fee_adjusted_gap is None


def test_build_gap_record_hit_none_until_settled():
    record = build_gap_record(
        market_ticker="MKT-1", model_probability=0.62, executable_market_probability_at_capture=0.55,
        estimated_taker_fee=None, closing_market_probability=None, contract_settled_yes=None,
    )
    assert record.actual_result_hit is None
