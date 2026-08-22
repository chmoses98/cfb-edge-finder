"""Coverage-ledger data model. Invariant-checking logic lives in
cfb_edge_finder.kalshi.coverage_ledger.CoverageLedger; this module only
defines the append-only record shape.

Two orthogonal axes, deliberately kept separate (see
cfb_edge_finder.schemas.common for the full rationale):

* CoverageOutcome (current_outcome / history) -- did the pipeline manage to
  evaluate this market, tracked as a full append-only audit trail.
* RecommendationReadiness (recommendation_readiness) -- is it worth
  betting, tracked as a single current value. It is intentionally NOT part
  of `history`: a market's recommendation readiness can flip repeatedly as
  price moves (e.g. WATCH -> EARLY_VALUE -> WATCH) without that being a
  coverage-pipeline event worth auditing at the same granularity, and
  changing it must never be able to alter `current_outcome` or vice versa.
"""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from cfb_edge_finder.schemas.common import CoverageOutcome, RecommendationReadiness


class StatusTransition(BaseModel):
    outcome: CoverageOutcome
    at: AwareDatetime
    reason: str | None = None


class CoverageLedgerEntry(BaseModel):
    """Append-only coverage-outcome history for one discovered market ticker,
    plus an orthogonal, independently-mutable recommendation-readiness value.

    current_outcome must always equal history[-1].outcome -- this is the
    schema-level half of the "no silently dropped market" invariant; the
    ledger-level half (every discovered ticker has an entry at all) is
    enforced by CoverageLedger.assert_no_missing(). recommendation_readiness
    is validated only for internal consistency with current_outcome (it must
    be unset unless current_outcome is EVALUATED), never as a substitute for
    coverage accounting.
    """

    market_ticker: str
    game_id: str | None = None
    current_outcome: CoverageOutcome
    history: list[StatusTransition] = Field(default_factory=list)
    recommendation_readiness: RecommendationReadiness | None = Field(
        default=None,
        description="Only meaningful (and only ever set) once current_outcome is EVALUATED.",
    )

    @model_validator(mode="after")
    def _history_matches_current(self) -> CoverageLedgerEntry:
        if not self.history:
            raise ValueError("history must not be empty: every entry starts with a DISCOVERED transition")
        if self.history[-1].outcome != self.current_outcome:
            raise ValueError(
                f"current_outcome {self.current_outcome!r} does not match the last history entry "
                f"({self.history[-1].outcome!r})"
            )
        return self

    @model_validator(mode="after")
    def _readiness_requires_evaluated(self) -> CoverageLedgerEntry:
        if self.recommendation_readiness is not None and self.current_outcome != CoverageOutcome.EVALUATED:
            raise ValueError(
                f"recommendation_readiness {self.recommendation_readiness!r} is set but "
                f"current_outcome is {self.current_outcome!r}, not EVALUATED -- a market with no "
                f"fair probability cannot have a recommendation readiness"
            )
        return self
