from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from cfb_edge_finder.schemas.common import CoverageOutcome, MarketFamily, RecommendationReadiness, Side


class MarketRecord(BaseModel):
    """One discovered Kalshi contract. Exists for EVERY discovered ticker,
    including ones that end up UNSUPPORTED_MARKET or PASS -- markets are
    archived even when never recommended (mission spec section 1.5).

    coverage_outcome and recommendation_readiness are the same two
    orthogonal axes as CoverageLedgerEntry (see schemas/coverage.py) --
    this record is the persisted-market view, the ledger entry is the
    audit-trail view of the same underlying facts.

    `team` (Side.HOME/Side.AWAY) is required for, and only for,
    MarketFamily.TEAM_TOTAL, orthogonal to `side` (Side.OVER/Side.UNDER),
    matching cfb_edge_finder.projections.distribution.price_market()'s
    dimensional model exactly -- see that module's docstring for why team
    identity is never encoded into `side` itself.
    """

    market_ticker: str = Field(..., description="Kalshi's own ticker; the primary external key")
    event_ticker: str | None = None
    series_ticker: str | None = None
    game_id: str | None = Field(default=None, description="None until the ticker is mapped to a game")
    market_family: MarketFamily | None = None
    line: float | None = Field(default=None, description="Spread/total line value, if applicable")
    side: Side | None = None
    team: Side | None = Field(
        default=None, description="Side.HOME or Side.AWAY; required for MarketFamily.TEAM_TOTAL only"
    )
    discovered_at: AwareDatetime
    last_seen_at: AwareDatetime
    coverage_outcome: CoverageOutcome
    coverage_reason: str | None = None
    recommendation_readiness: RecommendationReadiness | None = Field(
        default=None,
        description="Only meaningful (and only ever set) once coverage_outcome is EVALUATED.",
    )

    @model_validator(mode="after")
    def _readiness_requires_evaluated(self) -> MarketRecord:
        if self.recommendation_readiness is not None and self.coverage_outcome != CoverageOutcome.EVALUATED:
            raise ValueError(
                f"recommendation_readiness {self.recommendation_readiness!r} is set but "
                f"coverage_outcome is {self.coverage_outcome!r}, not EVALUATED"
            )
        return self

    @model_validator(mode="after")
    def _team_required_only_for_team_total(self) -> MarketRecord:
        if self.market_family == MarketFamily.TEAM_TOTAL and self.team not in (Side.HOME, Side.AWAY):
            raise ValueError("team_total markets require team=Side.HOME or Side.AWAY")
        if self.market_family is not None and self.market_family != MarketFamily.TEAM_TOTAL and self.team is not None:
            raise ValueError(f"team is only meaningful for team_total markets, not {self.market_family!r}")
        return self
