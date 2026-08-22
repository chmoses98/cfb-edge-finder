"""Prospective snapshot: what the model knew, before kickoff, at one instant.

Mission spec section 8 requires every historical prediction to be
reconstructable enough to answer "what did the model know at the time it
made this estimate?" -- this record is the answer. It intentionally embeds
full ModelVersion and DataProvenance rather than referencing them by ID,
because a snapshot must remain self-describing even if the versioned
records it points to are later pruned from hot storage (see
docs/STORAGE_STRATEGY.md).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from cfb_edge_finder.schemas.common import MarketFamily
from cfb_edge_finder.schemas.projection import UncertaintyProfile
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion


class ProspectiveSnapshot(BaseModel):
    snapshot_id: str
    sport: Literal["cfb"] = "cfb"
    game_id: str
    model_version: ModelVersion
    projection_timestamp: AwareDatetime
    data_timestamp: AwareDatetime
    provenance: DataProvenance
    market_snapshot_id: str = Field(..., description="Identifies the Kalshi price-sweep this was captured against")
    market_ticker: str
    market_family: MarketFamily
    fair_probability: float = Field(..., ge=0.0, le=1.0)
    executable_price: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Price as a probability (cents/100)"
    )
    uncertainty: UncertaintyProfile
    captured_at: AwareDatetime
