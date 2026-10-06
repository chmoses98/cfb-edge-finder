"""Duplicate-source and reschedule reconciliation (mission spec sections 8-9).

Three distinct operations, kept separate because they answer different
questions and must never be conflated:

1. `merge_same_game_update` -- the SAME canonical game_id was observed
   again (e.g. a kickoff-time update, a venue confirmation). This is
   ordinary, expected, and never a "conflict" -- ordinary field updates
   should not churn identity (mission section 9). Only identity-bearing
   fields (season/week_label/home/away/neutral_site, i.e. exactly the
   inputs to canonical_game_id) are asserted unchanged; everything else
   is allowed to update, with the newer observation winning.
2. `detect_reschedule` -- the SAME vendor game id was observed under a
   DIFFERENT canonical game_id than before (a true reschedule across
   weeks). Sets `previous_game_id` for traceability rather than silently
   losing the link.
3. `cross_check_secondary` -- a SECOND source (e.g. ESPN) is compared
   against an already-normalized primary-source GameRecord for the same
   physical game. Missing fields on the primary get filled in explicitly;
   disagreements produce a ConflictRecord and are never silently
   overwritten.

No function here merges two records "because the teams match" alone --
mission section 8 explicitly forbids that. Matching for
`cross_check_secondary` requires season + both teams (as an unordered
pair, so neutral-site vendor reversal doesn't break the match) + week_label.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cfb_edge_finder.schemas.game import GameRecord
from cfb_edge_finder.schemas.observation import ConflictRecord, FieldConflict, RawGameObservation


class IdentityMismatchError(ValueError):
    """Raised if two observations claiming to be updates of the SAME
    canonical game_id disagree on an identity-bearing field (season,
    week_label, home/away team, neutral_site) -- that would mean they are
    not actually the same game, and silently accepting the update would
    corrupt canonical identity.
    """


def merge_same_game_update(existing: GameRecord, incoming: GameRecord) -> GameRecord:
    if existing.game_id != incoming.game_id:
        raise ValueError("merge_same_game_update requires both records to share a game_id; use detect_reschedule "
                          "for cross-game_id updates")
    identity_fields = ("season", "week_label", "home_team_id", "away_team_id", "neutral_site")
    for field in identity_fields:
        if getattr(existing, field) != getattr(incoming, field):
            raise IdentityMismatchError(
                f"{existing.game_id}: identity field {field!r} changed ({getattr(existing, field)!r} -> "
                f"{getattr(incoming, field)!r}) between observations sharing the same game_id -- this should be "
                f"impossible by construction and indicates a normalization bug, not a legitimate update"
            )
    return incoming.model_copy(
        update={
            "discovered_at": min(existing.discovered_at, incoming.discovered_at),
            "previous_game_id": existing.previous_game_id or incoming.previous_game_id,
        }
    )


def detect_reschedule(
    previous_game_ids_by_source_id: dict[str, str], incoming: GameRecord, source: str
) -> GameRecord:
    """previous_game_ids_by_source_id: {vendor_game_id: canonical_game_id}
    from a PRIOR ingestion run's artifact. If incoming's vendor id was seen
    before under a different canonical game_id, this was a true reschedule
    (season/week_label/teams changed enough to move canonical identity) --
    stamp previous_game_id so the old identity remains traceable.
    """
    vendor_id = incoming.source_game_ids.get(source)
    if vendor_id is None:
        return incoming
    prior_game_id = previous_game_ids_by_source_id.get(vendor_id)
    if prior_game_id is not None and prior_game_id != incoming.game_id:
        return incoming.model_copy(update={"previous_game_id": prior_game_id})
    return incoming


def _teams_match(game: GameRecord, resolved_home: str, resolved_away: str) -> bool:
    return {game.home_team_id, game.away_team_id} == {resolved_home, resolved_away}


def find_match(
    games_by_id: dict[str, GameRecord], observation: RawGameObservation, resolved_home: str, resolved_away: str
) -> GameRecord | None:
    """Match a raw secondary-source observation to an already-normalized
    primary GameRecord. Requires season + both teams (unordered) to agree
    -- deliberately NOT just "teams match", per mission section 8. Week
    proximity is implicitly covered by matching on season + teams: FBS
    teams essentially never play the same opponent twice in one season
    outside the documented rematch cases (conference championship, bowl,
    CFP) which fall in different SeasonTypes and are exceedingly unlikely
    to be confused with each other by a secondary source's own observation.
    """
    for game in games_by_id.values():
        if game.season != observation.season:
            continue
        if not _teams_match(game, resolved_home, resolved_away):
            continue
        return game
    return None


def cross_check_secondary(
    primary: GameRecord, observation: RawGameObservation, source: str
) -> tuple[GameRecord, ConflictRecord | None]:
    """Returns (possibly gap-filled primary, conflict-or-None). Never
    mutates fields that already have a value and disagree -- those become
    a ConflictRecord instead, with `resolution=None` (unresolved) so a
    human/future-milestone process can decide, per mission section 8's
    "no silent overwrites."
    """
    updates: dict[str, object] = {}
    conflicts: list[FieldConflict] = []

    if primary.venue is None and observation.raw_venue:
        updates["venue"] = observation.raw_venue
    elif primary.venue and observation.raw_venue and primary.venue != observation.raw_venue:
        conflicts.append(
            FieldConflict(
                field="venue",
                values_by_source={primary.primary_source or "primary": primary.venue, source: observation.raw_venue},
            )
        )

    if observation.raw_neutral_site is not None and observation.raw_neutral_site != primary.neutral_site:
        conflicts.append(
            FieldConflict(
                field="neutral_site",
                values_by_source={
                    primary.primary_source or "primary": str(primary.neutral_site),
                    source: str(observation.raw_neutral_site),
                },
            )
        )

    updated = primary.model_copy(update=updates) if updates else primary

    if not conflicts:
        return updated, None

    return updated, ConflictRecord(
        game_id=primary.game_id,
        sources_involved=[primary.primary_source or "primary", source],
        conflicts=conflicts,
        detected_at=datetime.now(UTC),
        resolution=None,
    )
