"""Raw, pre-normalization source observations and cross-source conflict
records.

These are deliberately separate from GameRecord (mission audit section 8):
GameRecord is the clean, single, reconciled canonical record; a
RawGameObservation is what one specific vendor said, kept only long enough
to detect and report disagreement, never merged silently into the
canonical record.
"""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, Field


class RawGameObservation(BaseModel):
    """Exactly what one vendor reported for one game, before any
    normalization or team-alias resolution. `raw_home_team`/`raw_away_team`
    are the vendor's own strings, not yet resolved to canonical team_ids --
    resolution happens downstream in ingestion.team_matching and can itself
    fail loudly (AmbiguousTeamAliasError/UnknownTeamAliasError) independent
    of whether the observation itself is otherwise well-formed.
    """

    source: str = Field(..., description="e.g. 'cfbd', 'espn'")
    source_game_id: str
    observed_at: AwareDatetime
    season: int
    raw_week: str | int | None = None
    raw_season_type: str | None = None
    raw_home_team: str
    raw_away_team: str
    raw_neutral_site: bool | None = None
    raw_venue: str | None = None
    raw_kickoff: str | None = Field(default=None, description="Unparsed, as-received kickoff datetime string")
    raw_status: str | None = None


class FieldConflict(BaseModel):
    field: str
    values_by_source: dict[str, str] = Field(..., description="{'cfbd': 'value', 'espn': 'other_value'}")


class ConflictRecord(BaseModel):
    """One unresolved disagreement between two or more sources for what is
    believed to be the same physical game. Produced by
    cfb_edge_finder.ingestion.reconciliation -- never silently dropped or
    auto-resolved by picking one source arbitrarily.
    """

    game_id: str | None = Field(default=None, description="Canonical game_id if identity itself is not in question")
    sources_involved: list[str]
    conflicts: list[FieldConflict]
    detected_at: AwareDatetime
    resolution: str | None = Field(
        default=None,
        description="How this was (or should be) resolved, e.g. 'primary_source_wins:cfbd'. None means unresolved.",
    )
