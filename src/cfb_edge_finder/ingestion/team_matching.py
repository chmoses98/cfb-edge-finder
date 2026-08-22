"""Thin wrapper around cfb_edge_finder.teams for use inside ingestion.

Kept as its own module (rather than calling teams.resolve_team_alias
directly from game_normalization) so the exact point where "vendor string
-> canonical team_id" resolution happens is easy to find and reuse for
ESPN or any future source, and so ingestion-specific error context (which
source, which raw game) can be attached without polluting
cfb_edge_finder.teams itself.
"""

from __future__ import annotations

from cfb_edge_finder.teams import AmbiguousTeamAliasError, UnknownTeamAliasError, get_team, resolve_team_alias


class TeamResolutionError(ValueError):
    """Wraps AmbiguousTeamAliasError/UnknownTeamAliasError with the source
    context (which vendor, which raw game) that produced it.
    """

    def __init__(self, raw_name: str, source: str, cause: Exception):
        self.raw_name = raw_name
        self.source = source
        self.cause = cause
        super().__init__(f"could not resolve team {raw_name!r} from source {source!r}: {cause}")


def resolve_team_id(raw_name: str, source: str) -> str:
    try:
        return resolve_team_alias(raw_name)
    except (AmbiguousTeamAliasError, UnknownTeamAliasError) as exc:
        raise TeamResolutionError(raw_name, source, exc) from exc


def resolve_team_conference(team_id: str) -> str | None:
    team = get_team(team_id)
    return team.conference if team is not None else None
