"""Per-OBSERVATION settlement attribution.

*** WHY THIS EXISTS ALONGSIDE MarketSettlement ***
`schemas/settlement.py`'s `MarketSettlement` records a MARKET's outcome:
one fact per (game_id, kalshi_market_ticker), because how a contract
settled is a single truth no matter how many times we looked at it. That
is correct and unchanged.

But a market's outcome is not the research record. The same contract is
captured at EARLY_OPEN, T_7D, T_3D, T_24H, T_6H, T_90, T_60, T_30 and
CLOSING, and each of those snapshots has its own entry price, its own
model probability, its own fee, and therefore its own hypothetical
economics. Collapsing them onto one settlement row would destroy exactly
the timing dimension the prospective collection regime was built to
capture -- you could no longer ask "did the T_24H price beat the close?"
because there would be only one row per market.

So: one `ObservationAttribution` per captured observation, each carrying
its own entry state and its own research-unit economics, all pointing at
the same market outcome.

*** THIS IS NOT BETTING ***
`research_unit_pnl` is a hypothetical one-contract arithmetic result for
research measurement. It is not a wager, not a position, not a
recommendation, and it is never aggregated into a bankroll or used to
size anything. See research/attribution.py's module docstring.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from cfb_edge_finder.schemas.common import MarketFamily, Side

ATTRIBUTION_SCHEMA_VERSION = "attribution_v1"


class AttributionState(StrEnum):
    """Every eligible research observation resolves to exactly one of
    these. There is no silent missing settlement."""

    SETTLED_YES = "SETTLED_YES"
    SETTLED_NO = "SETTLED_NO"

    GAME_NOT_FINAL = "GAME_NOT_FINAL"
    MARKET_NOT_FINAL = "MARKET_NOT_FINAL"
    GAME_CANCELLED = "GAME_CANCELLED"
    GAME_POSTPONED = "GAME_POSTPONED"
    RESULT_UNAVAILABLE = "RESULT_UNAVAILABLE"
    SEMANTICS_UNRESOLVED = "SEMANTICS_UNRESOLVED"
    MAPPING_UNRESOLVED = "MAPPING_UNRESOLVED"
    SETTLEMENT_MISMATCH = "SETTLEMENT_MISMATCH"
    NOT_APPLICABLE_UNSUPPORTED_POPULATION = "NOT_APPLICABLE_UNSUPPORTED_POPULATION"


TERMINAL_ATTRIBUTION_STATES = frozenset(
    {
        AttributionState.SETTLED_YES,
        AttributionState.SETTLED_NO,
        AttributionState.GAME_CANCELLED,
        AttributionState.NOT_APPLICABLE_UNSUPPORTED_POPULATION,
    }
)
"""States that will not change on a later run. GAME_NOT_FINAL,
MARKET_NOT_FINAL, RESULT_UNAVAILABLE and GAME_POSTPONED are all
transient -- a later settlement run can and should revisit them.
SETTLEMENT_MISMATCH is deliberately NOT terminal: it is a defect to be
investigated, and must be allowed to resolve once the disagreement is
understood."""

PENDING_ATTRIBUTION_STATES = frozenset(
    {
        AttributionState.GAME_NOT_FINAL,
        AttributionState.MARKET_NOT_FINAL,
        AttributionState.RESULT_UNAVAILABLE,
        AttributionState.GAME_POSTPONED,
    }
)


class ResearchUnitEconomics(BaseModel):
    """Hypothetical economics for ONE research unit (one contract) held
    from the captured entry price to settlement.

    NOT a bet, NOT a position, NOT a recommendation. A contract settles at
    exactly $1.00 or $0.00, so this is deterministic arithmetic over a
    price we actually observed -- the measurement primitive later
    calibration work needs, nothing more."""

    model_config = ConfigDict(frozen=True)

    side: Side = Field(description="Side.YES or Side.NO -- which side this hypothetical unit was entered on.")
    entry_price: float = Field(ge=0.0, le=1.0)
    settlement_value: float = Field(ge=0.0, le=1.0, description="$1.00 if this side won, $0.00 otherwise.")
    research_unit_pnl: float = Field(description="settlement_value - entry_price, per unit, before fees.")
    estimated_fee: float | None = Field(
        default=None,
        description=(
            "Entry fee for THIS side at THIS side's own price, recomputed from the fee schedule rather than "
            "reused from the YES side -- see research/attribution.py for why the stored observation fee "
            "cannot simply be borrowed."
        ),
    )
    fee_adjusted_research_unit_pnl: float | None = Field(
        default=None, description="research_unit_pnl - estimated_fee. None when the fee is unknown."
    )
    return_on_entry_price: float | None = Field(
        default=None,
        description="research_unit_pnl / entry_price. None at entry_price == 0 (undefined, never infinity).",
    )


class ClosingLink(BaseModel):
    """The CLOSING snapshot for this observation's own market, if one was
    captured. Deliberately a link to a real CLOSING row -- never a
    substituted T_30 or any other checkpoint."""

    model_config = ConfigDict(frozen=True)

    closing_captured: bool
    closing_status: str = Field(description="ClosingStatus value: CLOSING_CAPTURED or a CLOSING_MISSING_* reason.")
    closing_yes_price: float | None = Field(default=None, ge=0.0, le=1.0)
    closing_no_price: float | None = Field(default=None, ge=0.0, le=1.0)
    closing_midpoint: float | None = Field(default=None, ge=0.0, le=1.0)
    closing_model_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    closing_captured_at: AwareDatetime | None = None
    closing_observation_key: str | None = None


class ObservationAttribution(BaseModel):
    """One captured observation, resolved to its market's outcome, with
    its own entry state and hypothetical economics preserved."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=ATTRIBUTION_SCHEMA_VERSION)
    attribution_key: str = Field(
        description=(
            "Canonical identity of this attribution. Derived from the observation's own canonical key plus "
            "the settlement code version, so re-running is idempotent while a genuine settlement-logic "
            "revision produces a NEW row rather than silently overwriting the old conclusion."
        )
    )
    observation_key: str = Field(description="The research observation this attributes. Never mutated.")

    game_id: str | None
    kalshi_market_ticker: str
    family: MarketFamily | None
    timing_label: str
    season: int

    state: AttributionState
    detail: str = ""

    # --- Outcome -----------------------------------------------------
    final_home_points: int | None = None
    final_away_points: int | None = None
    final_home_margin: float | None = None
    final_total_points: float | None = None
    went_to_overtime: bool | None = None
    event_true: bool | None = Field(
        default=None,
        description="Did this contract's own condition hold? True -> the YES side won. None when unresolved.",
    )
    derived_contract_settlement: Side | None = None
    official_kalshi_settlement: Side | None = None
    settlement_mismatch: bool = False

    # --- Entry state (per checkpoint, never collapsed) ----------------
    captured_at: AwareDatetime
    entry_yes_price: float | None = Field(default=None, ge=0.0, le=1.0)
    entry_no_price: float | None = Field(default=None, ge=0.0, le=1.0)
    entry_midpoint: float | None = Field(default=None, ge=0.0, le=1.0)
    entry_model_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    hours_before_kickoff: float | None = None

    # --- Hypothetical research economics ------------------------------
    research_unit_size: int = Field(default=1, description="Fixed at 1 contract. Never varied, never optimized.")
    yes_economics: ResearchUnitEconomics | None = None
    no_economics: ResearchUnitEconomics | None = None

    # --- Closing linkage ----------------------------------------------
    closing: ClosingLink | None = None

    # --- Provenance ----------------------------------------------------
    fee_schedule_version: str | None = None
    fee_status: str | None = None
    market_status_at_capture: str | None = None
    model_version: str | None = None
    training_cutoff: str | None = None
    semantics_version: str | None = None
    result_source: str = "cfbd"
    result_fetched_at: AwareDatetime | None = None
    settlement_code_version: str
    settled_at: AwareDatetime
    run_id: str | None = None
