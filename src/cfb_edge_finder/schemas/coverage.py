"""Coverage-ledger data model. Invariant-checking logic lives in
cfb_edge_finder.kalshi.coverage_ledger.CoverageLedger; this module only
defines the append-only record shape.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from cfb_edge_finder.schemas.common import MarketStatus


class StatusTransition(BaseModel):
    status: MarketStatus
    at: datetime
    reason: str | None = None


class CoverageLedgerEntry(BaseModel):
    """Append-only status history for one discovered market ticker.

    current_status must always equal history[-1].status -- this is the
    schema-level half of the "no silently dropped market" invariant; the
    ledger-level half (every discovered ticker has an entry at all) is
    enforced by CoverageLedger.assert_no_missing().
    """

    market_ticker: str
    game_id: str | None = None
    current_status: MarketStatus
    history: list[StatusTransition] = Field(default_factory=list)

    @model_validator(mode="after")
    def _history_matches_current(self) -> CoverageLedgerEntry:
        if not self.history:
            raise ValueError("history must not be empty: every entry starts with a DISCOVERED transition")
        if self.history[-1].status != self.current_status:
            raise ValueError(
                f"current_status {self.current_status!r} does not match the last history entry "
                f"({self.history[-1].status!r})"
            )
        return self
