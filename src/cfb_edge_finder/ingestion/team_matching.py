"""Thin wrapper around cfb_edge_finder.teams for use inside ingestion.

Kept as its own module (rather than calling teams.resolve_team_alias
directly from game_normalization) so the exact point where "vendor string
-> canonical team_id" resolution happens is easy to find and reuse for
ESPN or any future source, and so ingestion-specific error context (which
source, which raw game) can be attached without polluting
cfb_edge_finder.teams itself.
"""

from __future__ import annotations

from cfb_edge_finder.ids import slugify_team
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


def resolve_team_id_for_game(raw_name: str, source: str, classification: str | None) -> str:
    """Like resolve_team_id, but games involving an FBS team must not be
    silently discarded merely because the OPPONENT is FCS (mission
    directive) -- this registry only curates FBS programs, so an
    FCS/lower-division opponent will never appear in it by design, not by
    omission. When `classification` clearly indicates a non-FBS opponent
    and the name is simply unrecognized (UnknownTeamAliasError), a
    deterministic slug is generated instead of raising, so the game still
    normalizes.

    Ambiguity is NOT downgraded this way: AmbiguousTeamAliasError always
    still raises regardless of classification, because a genuinely
    ambiguous name is an identity risk independent of subdivision, and an
    FBS team's own opponent field must never be guessed.

    An unresolved name with classification == "fbs" (or missing/unclear)
    still raises as before -- an unrecognized FBS program name is exactly
    the case this project wants surfaced, not silently slugged.
    """
    try:
        return resolve_team_alias(raw_name)
    except AmbiguousTeamAliasError as exc:
        raise TeamResolutionError(raw_name, source, exc) from exc
    except UnknownTeamAliasError as exc:
        if isinstance(classification, str) and classification.strip().lower() != "fbs":
            return slugify_team(raw_name)
        raise TeamResolutionError(raw_name, source, exc) from exc


def resolve_team_conference(team_id: str) -> str | None:
    team = get_team(team_id)
    return team.conference if team is not None else None
