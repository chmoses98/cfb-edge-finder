from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from cfb_edge_finder.ids import canonical_game_id
from cfb_edge_finder.schemas.common import SeasonType

GameStatus = Literal["scheduled", "in_progress", "final", "postponed", "canceled"]


class GameRecord(BaseModel):
    """A single game, keyed by the canonical game_id (see cfb_edge_finder.ids).

    source_game_ids cross-references vendor-specific IDs (e.g.
    {"cfbd": "401520147"}) without making the system dependent on any one
    vendor's ID scheme for identity.
    """

    game_id: str
    season: int
    week_label: str
    season_type: SeasonType
    home_team_id: str = Field(..., description="Normalized team slug, see cfb_edge_finder.ids.slugify_team")
    away_team_id: str
    home_team_name: str = Field(..., description="Display name as reported by the schedule source")
    away_team_name: str
    neutral_site: bool = False
    kickoff_utc: datetime | None = Field(
        default=None, description="Deliberately excluded from game_id -- see cfb_edge_finder.ids"
    )
    venue: str | None = None
    source_game_ids: dict[str, str] = Field(default_factory=dict)
    status: GameStatus = "scheduled"
    discovered_at: datetime
    last_updated_at: datetime

    @model_validator(mode="after")
    def _game_id_matches_components(self) -> GameRecord:
        expected = canonical_game_id(self.season, self.week_label, self.away_team_id, self.home_team_id)
        if self.game_id != expected:
            raise ValueError(
                f"game_id {self.game_id!r} does not match canonical_game_id() of its own "
                f"components ({expected!r}); construct GameRecord via canonical_game_id()"
            )
        return self
