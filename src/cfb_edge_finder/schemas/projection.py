"""Game-level projection distribution and the record that wraps it.

This is the schema half of the "GAME INPUTS -> GAME-LEVEL PROJECTION ->
DISTRIBUTION -> MANY MARKET PROBABILITIES" pattern. The math that derives
market probabilities from GameDistribution lives in
cfb_edge_finder.projections.distribution -- kept separate on purpose so the
football engine (whatever eventually populates GameDistribution's fields)
never has to know about Kalshi market shapes.
"""

from __future__ import annotations

from math import isfinite

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion


class GameDistribution(BaseModel):
    """A coherent, correlated bivariate projection of (home_score, away_score).

    *** PROVISIONAL / RESEARCH-ONLY. NOT A VALIDATED BETTING MODEL. ***
    This is a placeholder parametric approximation (see the modeling
    assumptions below), not a backtested or calibrated projection engine.
    No recommendation, qualification tier, staking, or real-money
    eligibility may be derived from this class or from
    `cfb_edge_finder.projections.distribution.price_market()` -- no such
    logic exists anywhere in this codebase yet (see `betting/__init__.py`
    and docs/ROADMAP.md Milestone H), and this docstring is the contract
    that it must not be added by routing around GameDistribution's
    provisional status.

    Modeling assumption (V1, explicit -- see docs/ARCHITECTURE.md
    "Uncertainty & modeling assumptions"): each team's score is treated as
    approximately Normal, and margin/total are derived analytically from
    home/away mean, standard deviation, and their correlation. This is a
    deliberately simple parametric form chosen so ANY number of market
    probabilities can be derived cheaply (closed-form) from ONE set of five
    numbers, without a Monte-Carlo simulation per contract. It is expected
    to be refined (e.g. a discretized joint score grid, or a skew-adjusted
    distribution) once real backtests show where the Normal approximation
    breaks down -- that refinement is out of scope for this foundation
    phase.
    """

    home_mean: float = Field(..., ge=0.0, description="Projected mean home team score")
    away_mean: float = Field(..., ge=0.0, description="Projected mean away team score")
    home_sd: float = Field(..., gt=0, description="Standard deviation of home team score")
    away_sd: float = Field(..., gt=0, description="Standard deviation of away team score")
    correlation: float = Field(
        default=0.0,
        ge=-1.0,
        le=1.0,
        description="Correlation between home and away score. Default 0.0 (independence) is a "
        "documented placeholder assumption, not an empirical finding -- see ARCHITECTURE.md.",
    )

    @model_validator(mode="after")
    def _all_values_finite(self) -> GameDistribution:
        for field_name in ("home_mean", "away_mean", "home_sd", "away_sd", "correlation"):
            value = getattr(self, field_name)
            if not isfinite(value):
                raise ValueError(f"{field_name} must be a finite number, got {value!r}")
        return self


class UncertaintyProfile(BaseModel):
    """Distinguishes 'confident projection' from 'apparent edge on thin inputs'.

    Required by mission spec section 7: uncertainty must be first-class, not
    folded silently into generic variance. data_completeness and
    early_season_prior_weight are both in [0, 1] and are inputs to the
    eventual recommendation ranking formula (net edge x confidence x
    completeness x calibration x market quality x uncertainty penalty),
    which is NOT implemented in this foundation phase.
    """

    data_completeness: float = Field(..., ge=0.0, le=1.0)
    qb_status_confirmed: bool
    early_season_prior_weight: float = Field(
        ..., ge=0.0, le=1.0, description="Fraction of the projection driven by a preseason prior vs in-season data"
    )
    notes: list[str] = Field(default_factory=list)


class ProjectionRecord(BaseModel):
    projection_id: str
    game_id: str
    model_version: ModelVersion
    provenance: DataProvenance
    projection_timestamp: AwareDatetime
    distribution: GameDistribution
    uncertainty: UncertaintyProfile

    @model_validator(mode="after")
    def _timestamps_consistent(self) -> ProjectionRecord:
        if self.provenance.data_timestamp > self.projection_timestamp:
            raise ValueError("data_timestamp cannot be after projection_timestamp")
        return self
