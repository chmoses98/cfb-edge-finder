"""Milestone E, Part F: closing-line-value / market-movement research
metrics. Neutral terminology only -- see kalshi/research_ledger.py's
module docstring on why "edge" is avoided; the same discipline applies
here. Nothing in this module is a recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketMovementResult:
    entry_snapshot_price: float
    closing_price: float
    raw_price_movement: float
    """closing_price - entry_snapshot_price, in probability points."""
    probability_movement_toward_model: float | None
    """Positive means the market moved TOWARD the model's entry-time fair
    probability between entry and close (i.e. the model's implied signal
    was borne out by later market pricing) -- purely descriptive."""
    fee_adjusted_movement: float | None
    time_to_kickoff_hours_at_entry: float | None


def closing_price_delta(entry_snapshot_price: float, closing_price: float) -> float:
    return closing_price - entry_snapshot_price


def market_move_toward_model(
    entry_snapshot_price: float, closing_price: float, model_probability: float | None
) -> float | None:
    """Signed: positive means the close moved closer to the model's
    entry-time fair probability than the entry price was, i.e.
    |entry - model| - |close - model|, in probability points."""
    if model_probability is None:
        return None
    return abs(entry_snapshot_price - model_probability) - abs(closing_price - model_probability)


def fee_adjusted_clv(
    raw_price_movement: float, estimated_taker_fee_entry: float | None, estimated_taker_fee_closing: float | None
) -> float | None:
    """research_clv, fee-adjusted: raw movement minus the round-trip
    (entry + closing) estimated taker fee drag, both already computed by
    Milestone D's fee_schedule closure pass. None if either fee is
    unknown -- never silently treated as zero."""
    if estimated_taker_fee_entry is None or estimated_taker_fee_closing is None:
        return None
    return raw_price_movement - (estimated_taker_fee_entry + estimated_taker_fee_closing)


def compute_market_movement(
    *,
    entry_snapshot_price: float,
    closing_price: float,
    model_probability_at_entry: float | None,
    estimated_taker_fee_entry: float | None,
    estimated_taker_fee_closing: float | None,
    time_to_kickoff_hours_at_entry: float | None,
) -> MarketMovementResult:
    raw = closing_price_delta(entry_snapshot_price, closing_price)
    return MarketMovementResult(
        entry_snapshot_price=entry_snapshot_price,
        closing_price=closing_price,
        raw_price_movement=raw,
        probability_movement_toward_model=market_move_toward_model(
            entry_snapshot_price, closing_price, model_probability_at_entry
        ),
        fee_adjusted_movement=fee_adjusted_clv(raw, estimated_taker_fee_entry, estimated_taker_fee_closing),
        time_to_kickoff_hours_at_entry=time_to_kickoff_hours_at_entry,
    )


@dataclass(frozen=True)
class ModelMarketGapRecord:
    """Mission section 15: the core prospective evaluation corpus row --
    preserved per settled observation, never overwritten."""

    market_ticker: str
    model_probability: float
    executable_market_probability_at_capture: float
    gross_gap: float
    fee_adjusted_gap: float | None
    closing_market_probability: float | None
    actual_result_hit: bool | None
    """True if the model-favored side (model_probability > 0.5 => YES)
    actually settled YES; None until settled."""


def build_gap_record(
    *,
    market_ticker: str,
    model_probability: float,
    executable_market_probability_at_capture: float,
    estimated_taker_fee: float | None,
    closing_market_probability: float | None,
    contract_settled_yes: bool | None,
) -> ModelMarketGapRecord:
    gross_gap = model_probability - executable_market_probability_at_capture
    fee_adjusted_gap = gross_gap - estimated_taker_fee if estimated_taker_fee is not None else None
    model_favors_yes = model_probability > 0.5
    actual_result_hit = None if contract_settled_yes is None else (contract_settled_yes == model_favors_yes)
    return ModelMarketGapRecord(
        market_ticker=market_ticker,
        model_probability=model_probability,
        executable_market_probability_at_capture=executable_market_probability_at_capture,
        gross_gap=gross_gap,
        fee_adjusted_gap=fee_adjusted_gap,
        closing_market_probability=closing_market_probability,
        actual_result_hit=actual_result_hit,
    )
