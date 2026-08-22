from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from cfb_edge_finder.schemas.common import MarketFamily, MarketStatus, Side


class MarketRecord(BaseModel):
    """One discovered Kalshi contract. Exists for EVERY discovered ticker,
    including ones that end up REJECTED or UNSUPPORTED_MARKET -- markets are
    archived even when never recommended (mission spec section 1.5).
    """

    market_ticker: str = Field(..., description="Kalshi's own ticker; the primary external key")
    event_ticker: str | None = None
    series_ticker: str | None = None
    game_id: str | None = Field(default=None, description="None until the ticker is mapped to a game")
    market_family: MarketFamily | None = None
    line: float | None = Field(default=None, description="Spread/total line value, if applicable")
    side: Side | None = None
    discovered_at: datetime
    last_seen_at: datetime
    status: MarketStatus
    status_reason: str | None = None
