"""Milestone E, Part E: postgame settlement records.

Deliberately separate from `ResearchCorpusRow`/`KalshiResearchObservation`
(never mutates a captured row) -- a settlement is keyed by
(game_id, kalshi_market_ticker) once per market (not once per snapshot),
since a market's final outcome is a single fact, unlike its many
pregame price observations. See research/settlement.py for the derivation
logic that populates this schema.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from cfb_edge_finder.schemas.common import MarketFamily, Side

SETTLEMENT_SCHEMA_VERSION = "settlement_v1"


class GameFinalStatus(StrEnum):
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELED = "canceled"
    NO_CONTEST = "no_contest"
    NOT_YET_FINAL = "not_yet_final"


class GameResult(BaseModel):
    """Genuine authoritative final score -- from CFBD (primary; see
    research.settlement.extract_game_result()) or, only when CFBD is
    recoverably unavailable, from the strictly-validated ESPN fallback
    (research/result_provider.py). Never inferred from generic sportsbook
    rules; ties are structurally impossible in CFB (overtime resolves
    them), so `status == FINAL` implies a strict winner."""

    model_config = ConfigDict(frozen=True)

    game_id: str
    season: int
    home_points: int | None = None
    away_points: int | None = None
    status: GameFinalStatus
    went_to_overtime: bool | None = Field(
        default=None, description="True/False if determinable from the source; None if genuinely unknown."
    )
    source: str = Field(
        default="cfbd",
        description="'cfbd' (primary) or 'espn_fallback' -- which provider supplied THIS result fact.",
    )
    source_game_id: str | None = None
    fallback_reason: str | None = Field(
        default=None,
        description="Set only on fallback-sourced results: why the primary source was unavailable "
        "(e.g. the exact CFBD HTTP failure). None on primary-sourced results.",
    )
    status_evidence: str | None = Field(
        default=None,
        description="Set only on fallback-sourced results: the provider's verbatim finality evidence "
        "(e.g. ESPN status.type name/state/completed/detail) this fact's status was derived from.",
    )
    captured_at: AwareDatetime


class MarketSettlementStatus(StrEnum):
    SETTLED = "settled"
    VOID_POSTPONED = "void_postponed"
    VOID_CANCELED = "void_canceled"
    VOID_NO_CONTEST = "void_no_contest"
    PENDING_NOT_FINAL = "pending_not_final"
    UNSETTLEABLE_UNKNOWN_OPERATOR = "unsettleable_unknown_operator"
    UNSETTLEABLE_MISSING_FIELDS = "unsettleable_missing_fields"


class MarketSettlement(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = Field(default=SETTLEMENT_SCHEMA_VERSION)
    settlement_version: str = Field(default=SETTLEMENT_SCHEMA_VERSION)
    game_id: str
    kalshi_market_ticker: str
    family: MarketFamily | None
    status: MarketSettlementStatus

    actual_winner: Side | None = Field(default=None, description="Side.HOME or Side.AWAY once the game is FINAL.")
    actual_home_margin: float | None = Field(default=None, description="home_points - away_points.")
    actual_total_points: float | None = None

    derived_contract_settlement: Side | None = Field(
        default=None, description="Side.YES or Side.NO this specific contract settled to, derived from the same "
        "operator/threshold/team semantics used to price it (contract_semantics.py) -- never a generic "
        "sportsbook rule."
    )
    official_kalshi_settlement: Side | None = Field(
        default=None,
        description="Kalshi's own reported settlement outcome, when/if available. None means not yet observed "
        "(read-only market access never queries a settlement endpoint in this codebase) -- absence is not "
        "evidence of disagreement.",
    )
    settlement_mismatch_flagged: bool = Field(
        default=False,
        description="True only when BOTH derived_contract_settlement and official_kalshi_settlement are set "
        "and disagree -- never inferred from one side alone.",
    )

    detail: str = ""
    settled_at: AwareDatetime
    game_result: GameResult
