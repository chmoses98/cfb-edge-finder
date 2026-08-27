"""Resolving each captured observation to its market's outcome, with
hypothetical research economics and closing linkage.

*** THIS IS MEASUREMENT, NOT BETTING ***
`research_unit_pnl` is deterministic arithmetic: a Kalshi contract settles
at exactly $1.00 or $0.00, so "what would one contract bought at the price
we actually observed have been worth?" has an exact answer. That number is
the primitive later calibration work needs -- it is not a wager, not a
position, not a recommendation, and nothing here sizes, aggregates, or
optimizes anything. The unit is fixed at one contract and never varies.

*** WHY THE STORED FEE CANNOT SIMPLY BE REUSED FOR THE NO SIDE ***
Each observation stores `estimated_taker_fee`, computed at ITS OWN YES
price. Kalshi's taker fee is proportional to P*(1-P), which is symmetric,
so it is tempting to reuse the same number for the NO side. That would be
wrong here for a concrete reason: `executable_no_price` is captured
INDEPENDENTLY from the order book and is not generally `1 - yes_price`
(mission section 11 is explicit about not assuming that). Two different
prices mean two different P*(1-P) values, so the NO-side fee is
recomputed from the fee schedule at the NO price rather than borrowed.

*** ONE ATTRIBUTION PER OBSERVATION, NEVER PER MARKET ***
See schemas/attribution.py. Collapsing checkpoints would destroy the
timing dimension the collection regime exists to capture.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cfb_edge_finder.kalshi.fee_schedule import (
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    calculate_fee_dollars,
    get_taker_multiplier,
)
from cfb_edge_finder.research.closing_capture import ClosingStatus
from cfb_edge_finder.schemas.attribution import (
    AttributionState,
    ClosingLink,
    ObservationAttribution,
    ResearchUnitEconomics,
)
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow
from cfb_edge_finder.schemas.settlement import GameFinalStatus, MarketSettlement, MarketSettlementStatus

ATTRIBUTION_CODE_VERSION = "attribution_v1"
RESEARCH_UNIT_CONTRACTS = 1
"""The standardized research unit: exactly one contract, always. Fixed so
that every P/L number in the corpus is directly comparable, and so that
nothing in this module can be mistaken for stake sizing."""

SUPPORTED_FAMILIES = frozenset({MarketFamily.MONEYLINE, MarketFamily.SPREAD, MarketFamily.TOTAL})


def attribution_key(observation_key: str, *, code_version: str = ATTRIBUTION_CODE_VERSION) -> str:
    """Canonical identity: the observation's own key plus the settlement
    code version.

    Including the code version means a genuine revision of settlement
    logic appends a NEW attribution alongside the old one rather than
    silently overwriting a previous research conclusion -- the amendment
    mechanism described in docs/SETTLEMENT.md. Re-running unchanged code
    produces the same key and is therefore a no-op."""
    return f"{observation_key}|{code_version}"


def _estimate_fee(price: float, series_ticker: str | None) -> float | None:
    """Entry fee in dollars for one contract at `price`, from the same
    verified schedule the pricing path uses. Returns None rather than a
    guess when the price is outside the tradeable range."""
    if not (0.0 < price < 1.0):
        return None
    multiplier, _label = get_taker_multiplier(series_ticker or "")
    fee = calculate_fee_dollars(
        int(round(price * 100)), RESEARCH_UNIT_CONTRACTS, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER, multiplier
    )
    return float(fee if isinstance(fee, Decimal) else fee)


def research_unit_economics(
    *,
    side: Side,
    entry_price: float | None,
    event_true: bool,
    series_ticker: str | None,
) -> ResearchUnitEconomics | None:
    """Hypothetical one-contract economics for one side.

    A YES unit is worth $1.00 when the contract's condition held; a NO
    unit is worth $1.00 when it did not. `entry_price` is the side's OWN
    captured executable price -- the NO price is never derived from the
    YES price."""
    if entry_price is None:
        return None
    if side is Side.YES:
        won = event_true
    elif side is Side.NO:
        won = not event_true
    else:
        raise ValueError(f"research unit economics is defined for YES/NO only, got {side!r}")

    settlement_value = 1.0 if won else 0.0
    pnl = settlement_value - entry_price
    fee = _estimate_fee(entry_price, series_ticker)
    return ResearchUnitEconomics(
        side=side,
        entry_price=entry_price,
        settlement_value=settlement_value,
        research_unit_pnl=pnl,
        estimated_fee=fee,
        fee_adjusted_research_unit_pnl=None if fee is None else pnl - fee,
        # Undefined at a zero entry price rather than infinite -- a free
        # contract has no capital base to return on.
        return_on_entry_price=None if entry_price == 0.0 else pnl / entry_price,
    )


def build_closing_link(closing_row: ResearchCorpusRow | None, missing_reason: str | None = None) -> ClosingLink:
    """Links this observation's market to its own CLOSING snapshot.

    `closing_row` must be a genuine CLOSING-labelled row for the SAME
    market ticker. A T_30 (or any other checkpoint) is never accepted as a
    stand-in: mission section 12 forbids fabricating a close, and a
    substituted neighbour would silently redefine the most price-sensitive
    field in the corpus."""
    if closing_row is None:
        return ClosingLink(
            closing_captured=False,
            closing_status=missing_reason or ClosingStatus.CLOSING_MISSING_NO_SCAN_IN_WINDOW.value,
        )
    label = closing_row.observation.snapshot_timing.label
    if label != "CLOSING":
        raise ValueError(
            f"build_closing_link received a {label!r} row -- only a genuine CLOSING snapshot may be linked"
        )
    obs = closing_row.observation
    return ClosingLink(
        closing_captured=True,
        closing_status=ClosingStatus.CLOSING_CAPTURED.value,
        closing_yes_price=obs.executable_yes_price,
        closing_no_price=obs.executable_no_price,
        closing_midpoint=obs.market_midpoint,
        closing_model_probability=obs.model_probability,
        closing_captured_at=obs.captured_at,
        closing_observation_key=closing_row.observation_key,
    )


def _state_from_settlement(settlement: MarketSettlement) -> tuple[AttributionState, bool | None]:
    """Maps a market-level settlement onto this observation's explicit
    state, plus whether the contract's own condition held."""
    status = settlement.status
    if status is MarketSettlementStatus.SETTLED:
        if settlement.settlement_mismatch_flagged:
            return AttributionState.SETTLEMENT_MISMATCH, None
        if settlement.derived_contract_settlement is Side.YES:
            return AttributionState.SETTLED_YES, True
        if settlement.derived_contract_settlement is Side.NO:
            return AttributionState.SETTLED_NO, False
        return AttributionState.SEMANTICS_UNRESOLVED, None
    if status is MarketSettlementStatus.PENDING_NOT_FINAL:
        return AttributionState.GAME_NOT_FINAL, None
    if status is MarketSettlementStatus.VOID_POSTPONED:
        return AttributionState.GAME_POSTPONED, None
    if status is MarketSettlementStatus.VOID_CANCELED:
        return AttributionState.GAME_CANCELLED, None
    if status is MarketSettlementStatus.VOID_NO_CONTEST:
        return AttributionState.GAME_CANCELLED, None
    if status is MarketSettlementStatus.UNSETTLEABLE_UNKNOWN_OPERATOR:
        return AttributionState.SEMANTICS_UNRESOLVED, None
    if status is MarketSettlementStatus.UNSETTLEABLE_MISSING_FIELDS:
        return AttributionState.SEMANTICS_UNRESOLVED, None
    return AttributionState.RESULT_UNAVAILABLE, None


def attribute_observation(
    row: ResearchCorpusRow,
    settlement: MarketSettlement | None,
    *,
    settled_at: datetime,
    closing_row: ResearchCorpusRow | None = None,
    closing_missing_reason: str | None = None,
    series_ticker: str | None = None,
    result_fetched_at: datetime | None = None,
    run_id: str | None = None,
    require_market_final: bool = False,
    market_is_final: bool | None = None,
) -> ObservationAttribution:
    """Resolve ONE captured observation. Never mutates `row`.

    Ordering of the guards below is deliberate: population eligibility
    first (an FCS-vs-FCS market was never settleable and should not be
    reported as a failure), then mapping, then the market outcome."""
    obs = row.observation
    base = dict(
        attribution_key=attribution_key(row.observation_key),
        observation_key=row.observation_key,
        game_id=obs.game_id,
        kalshi_market_ticker=obs.kalshi_market_ticker,
        family=obs.family,
        timing_label=obs.snapshot_timing.label,
        season=row.season,
        captured_at=obs.captured_at,
        entry_yes_price=obs.executable_yes_price,
        entry_no_price=obs.executable_no_price,
        entry_midpoint=obs.market_midpoint,
        entry_model_probability=obs.model_probability,
        hours_before_kickoff=obs.snapshot_timing.hours_before_kickoff,
        fee_schedule_version=obs.fee_schedule_version,
        fee_status=obs.fee_status,
        market_status_at_capture=obs.market_status,
        model_version=obs.model_version.model_version if obs.model_version else None,
        training_cutoff=obs.training_cutoff,
        semantics_version=obs.parse_status,
        result_fetched_at=result_fetched_at,
        settlement_code_version=ATTRIBUTION_CODE_VERSION,
        settled_at=settled_at,
        run_id=run_id,
        closing=build_closing_link(closing_row, closing_missing_reason),
    )

    if obs.family not in SUPPORTED_FAMILIES or obs.pricing_status != "model_priced":
        return ObservationAttribution(
            **base,
            state=AttributionState.NOT_APPLICABLE_UNSUPPORTED_POPULATION,
            detail=f"family={obs.family!r} pricing_status={obs.pricing_status!r} is not a settleable population",
        )
    if not obs.game_id:
        return ObservationAttribution(
            **base,
            state=AttributionState.MAPPING_UNRESOLVED,
            detail="observation never mapped to a game, so no result can be attributed",
        )
    if settlement is None:
        return ObservationAttribution(
            **base,
            state=AttributionState.RESULT_UNAVAILABLE,
            detail="no authoritative game result available for this game yet",
        )
    if require_market_final and market_is_final is False:
        # Kalshi has not finalized the market even though the game looks
        # final. Recorded explicitly rather than settled optimistically.
        return ObservationAttribution(
            **base,
            state=AttributionState.MARKET_NOT_FINAL,
            detail="game appears final but the Kalshi market is not finalized",
            final_home_points=settlement.game_result.home_points,
            final_away_points=settlement.game_result.away_points,
        )

    state, event_true = _state_from_settlement(settlement)
    result = settlement.game_result
    outcome = dict(
        final_home_points=result.home_points,
        final_away_points=result.away_points,
        final_home_margin=settlement.actual_home_margin,
        final_total_points=settlement.actual_total_points,
        went_to_overtime=result.went_to_overtime,
        event_true=event_true,
        derived_contract_settlement=settlement.derived_contract_settlement,
        official_kalshi_settlement=settlement.official_kalshi_settlement,
        settlement_mismatch=settlement.settlement_mismatch_flagged,
        result_source=result.source,
    )

    if event_true is None:
        # Unresolved (not final, void, mismatch, semantics): no economics.
        # Deliberately NOT zero -- a zero P/L would read as "broke even"
        # when the truth is "we do not know".
        return ObservationAttribution(**base, **outcome, state=state, detail=settlement.detail)

    return ObservationAttribution(
        **base,
        **outcome,
        state=state,
        detail=settlement.detail,
        yes_economics=research_unit_economics(
            side=Side.YES, entry_price=obs.executable_yes_price, event_true=event_true, series_ticker=series_ticker
        ),
        no_economics=research_unit_economics(
            side=Side.NO, entry_price=obs.executable_no_price, event_true=event_true, series_ticker=series_ticker
        ),
    )


def is_settleable_population(row: ResearchCorpusRow) -> bool:
    """Whether this observation is one settlement should ever produce a
    YES/NO for -- used by the incremental index to size the eligible
    population without re-deriving a full attribution."""
    obs = row.observation
    return bool(obs.family in SUPPORTED_FAMILIES and obs.pricing_status == "model_priced" and obs.game_id)


def game_is_final(status: GameFinalStatus) -> bool:
    return status is GameFinalStatus.FINAL


def summarize_states(attributions: list[ObservationAttribution]) -> dict[str, int]:
    counts: dict[str, int] = {state.value: 0 for state in AttributionState}
    for a in attributions:
        counts[a.state.value] += 1
    return counts
