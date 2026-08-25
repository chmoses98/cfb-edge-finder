"""Milestone D: one research ledger row -- everything captured about ONE
Kalshi CFB market at ONE snapshot instant.

*** WHY A NEW MODEL, NOT JUST MarketRecord/ProspectiveSnapshot REUSED AS-IS ***
`MarketRecord` (schemas/market.py, Milestone A) tracks a market's
COVERAGE state over time (append-only via CoverageLedger) -- it has no
slot for a specific price observation. `ProspectiveSnapshot`
(schemas/snapshot.py, Milestone A) tracks a point-in-time (model
probability, executable price) pair -- close, but missing several fields
this mission's research ledger explicitly asks for (the parsed contract
semantics themselves, the research probability gap, training cutoff,
fee status, coverage/parse/pricing status). Rather than overload either
existing model with fields outside its own stated purpose,
`KalshiResearchObservation` is the single, explicit, superset row this
milestone's ledger actually needs -- `to_prospective_snapshot()` below
produces a real `ProspectiveSnapshot` from it, so nothing about the
existing schema is duplicated or bypassed; this is additive.

*** IMMUTABILITY ***
Every field is set at construction and never mutated (`model_config =
{"frozen": True}`, mirroring KalshiMarketFamilyRecord's own frozen
config) -- a later observation of the SAME market is a NEW instance with
a NEW snapshot_id, never an in-place edit. See kalshi/research_ledger.py
for the append-only store this model is designed to live in.
"""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from cfb_edge_finder.schemas.common import CoverageOutcome, MarketFamily, Side
from cfb_edge_finder.schemas.projection import UncertaintyProfile
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion
from cfb_edge_finder.schemas.snapshot import ProspectiveSnapshot


class SnapshotTiming(BaseModel):
    """A closed label for WHEN, relative to kickoff, a snapshot was taken
    -- mission section 14. Purely descriptive metadata; nothing in this
    codebase currently schedules captures automatically at these
    horizons (see docs/MILESTONE_D.md "Snapshot timing" for the manual-
    capture-first rationale)."""

    model_config = ConfigDict(frozen=True)

    label: str = Field(
        ...,
        description=(
            "One of EARLY_OPEN/T_7D/T_3D/T_24H/T_6H/T_90/T_60/T_30/CLOSING, "
            "or another explicit, documented label -- never left blank."
        ),
    )
    hours_before_kickoff: float | None = Field(
        default=None, description="Actual elapsed hours before kickoff at capture time, if kickoff is known"
    )


class KalshiResearchObservation(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    captured_at: AwareDatetime
    snapshot_timing: SnapshotTiming

    game_id: str | None = Field(default=None, description="None if this market was never successfully mapped")
    kalshi_event_ticker: str
    kalshi_market_ticker: str
    family: MarketFamily | None = None
    threshold: float | None = Field(default=None, description="Spread/total line value, if applicable")
    side: Side | None = None
    team: Side | None = Field(default=None, description="Side.HOME or Side.AWAY; required only for team_total")
    semantic_operator: str | None = Field(default=None, description="e.g. '>' -- see contract_semantics.py")

    model_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    executable_yes_price: float | None = Field(default=None, ge=0.0, le=1.0)
    executable_no_price: float | None = Field(default=None, ge=0.0, le=1.0)
    market_midpoint: float | None = Field(default=None, ge=0.0, le=1.0)
    research_probability_gap: float | None = Field(
        default=None,
        description=(
            "model_probability - executable_yes_price, in probability points. Deliberately NOT "
            "named 'edge' -- see kalshi/research_ledger.py's module docstring."
        ),
    )

    fee_status: str = Field(
        ..., description="'unverified' (this mission's finding) or a future verified fee-schedule identifier"
    )

    model_version: ModelVersion | None = None
    training_cutoff: str | None = None

    coverage_outcome: CoverageOutcome
    coverage_reason: str | None = None
    parse_status: str = Field(..., description="e.g. 'confirmed_live', 'unconfirmed', 'unresolved'")
    pricing_status: str = Field(..., description="e.g. 'model_priced', 'unsupported_population', 'not_priced'")

    provenance: DataProvenance
    uncertainty: UncertaintyProfile | None = None

    def to_prospective_snapshot(self, *, market_snapshot_id: str) -> ProspectiveSnapshot:
        """Produces a real, schema-valid ProspectiveSnapshot (Milestone A)
        from this observation -- only callable once model_probability/
        model_version/uncertainty are all populated (i.e. pricing_status
        == "model_priced"), matching that schema's own required fields."""
        if self.model_probability is None or self.model_version is None or self.uncertainty is None:
            raise ValueError(
                "to_prospective_snapshot requires model_probability, model_version, and uncertainty to "
                "all be set -- this observation was not successfully model-priced"
            )
        if self.family is None:
            raise ValueError("to_prospective_snapshot requires `family` to be set")
        return ProspectiveSnapshot(
            snapshot_id=self.snapshot_id,
            game_id=self.game_id or "",
            model_version=self.model_version,
            projection_timestamp=self.captured_at,
            data_timestamp=self.provenance.data_timestamp,
            provenance=self.provenance,
            market_snapshot_id=market_snapshot_id,
            market_ticker=self.kalshi_market_ticker,
            market_family=self.family,
            fair_probability=self.model_probability,
            executable_price=self.executable_yes_price,
            uncertainty=self.uncertainty,
            captured_at=self.captured_at,
        )
