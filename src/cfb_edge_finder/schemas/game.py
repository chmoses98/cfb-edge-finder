from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from cfb_edge_finder.ids import canonical_game_id
from cfb_edge_finder.schemas.common import CFPRound, SeasonType

GameStatus = Literal["scheduled", "in_progress", "final", "postponed", "canceled"]


class GameRecord(BaseModel):
    """A single game, keyed by the canonical game_id (see cfb_edge_finder.ids).

    source_game_ids cross-references vendor-specific IDs (e.g.
    {"cfbd": "401520147"}) without making the system dependent on any one
    vendor's ID scheme for identity.

    Neutral-site semantics: home_team_id/away_team_id are ALWAYS required,
    even when neutral_site is True -- for a neutral-site game they are a
    bookkeeping designation only (which side of the box score a team is
    printed on), never evidence of a real home-field edge. Any future
    rating/projection code MUST check `neutral_site` before applying
    home-field advantage; see
    `cfb_edge_finder.ratings.home_field_advantage_points`, which is the
    single enforced choke point for that rule.
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
    kickoff_utc: AwareDatetime | None = Field(
        default=None, description="Deliberately excluded from game_id -- see cfb_edge_finder.ids"
    )
    venue: str | None = None
    source_game_ids: dict[str, str] = Field(default_factory=dict)
    status: GameStatus = "scheduled"
    previous_game_id: str | None = Field(
        default=None,
        description=(
            "Set when a schedule change (e.g. postponement moving the game to a different "
            "week_label) caused this game's canonical game_id to change from an earlier one. "
            "Preserves traceability to any ProjectionRecord/ProspectiveSnapshot captured "
            "against the prior game_id -- see docs/SCHEMAS.md."
        ),
    )
    week_number: int | None = Field(
        default=None,
        description="Structured regular-season week integer (0-15ish), independent of the week_label slug. "
        "None for postseason games.",
    )
    cfp_round: CFPRound | None = Field(
        default=None, description="Structured CFP round identity; only meaningful when season_type is CFP"
    )
    bowl_display_name: str | None = Field(
        default=None,
        description="Human-readable, possibly-sponsor-branded bowl name (e.g. 'Duke's Mayo Bowl'), kept SEPARATE "
        "from the stable week_label slug used in game_id specifically because sponsor names change year to "
        "year -- see docs/SCHEMAS.md 'Canonical game ID' bowl-volatility note.",
    )
    kickoff_source_raw: str | None = Field(
        default=None,
        description="The as-received datetime string from the primary source before UTC normalization, retained "
        "for auditability of timezone/offset handling -- kickoff_utc itself is always UTC.",
    )
    primary_source: str | None = Field(
        default=None,
        description="Which key in source_game_ids/vendor is currently treated as authoritative for this record's "
        "home/away/venue/kickoff designation -- see cfb_edge_finder.ingestion.reconciliation.",
    )
    discovered_at: AwareDatetime
    last_updated_at: AwareDatetime

    @model_validator(mode="after")
    def _game_id_matches_components(self) -> GameRecord:
        expected = canonical_game_id(
            self.season, self.week_label, self.away_team_id, self.home_team_id, neutral_site=self.neutral_site
        )
        if self.game_id != expected:
            raise ValueError(
                f"game_id {self.game_id!r} does not match canonical_game_id() of its own "
                f"components ({expected!r}); construct GameRecord via canonical_game_id()"
            )
        return self
