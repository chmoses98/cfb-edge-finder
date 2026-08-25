"""Milestone D: deterministic Kalshi event/market -> canonical GameRecord
mapping.

*** WHY A PURE FUNCTION OVER PRE-FETCHED CANDIDATES ***
This module never fetches anything itself -- it takes a small piece of
evidence extracted from one Kalshi market/event (`KalshiGameEvidence`) and
a list of already-fetched candidate `GameRecord`s (from CFBDClient,
exactly as backtest.py/build_cfb_baseline.py already separate "fetch" from
"compute"), and returns a `KalshiGameMappingResult`. This keeps mapping
logic fully unit-testable against synthetic fixtures (Miami FL vs OH, USC
vs South Carolina, accented/directional/abbreviated names, neutral-site
games, FBS-vs-FCS, rescheduled games) without any live network dependency.

*** WHY NO FUZZY MATCHING ***
Team-name resolution is delegated entirely to
`teams.registry.resolve_team_alias` -- the SAME exact-match-only,
fail-loud resolver Milestone B already built and tested
(`AmbiguousTeamAliasError`/`UnknownTeamAliasError`). This module adds zero
new string-similarity logic; a name this resolver cannot confidently
resolve is always AMBIGUOUS_TEAM_MAPPING, never a best-guess.

*** WHY A DATE WINDOW, NOT AN EXACT TIMESTAMP MATCH ***
A Kalshi market's `close_time` is when the market itself stops trading,
not necessarily kickoff -- and CFBD kickoff times can carry their own
timezone/rounding quirks (see docs/MILESTONE_B.md). Candidate games are
matched by TEAM-PAIR IDENTITY FIRST (the strong signal), with the date
window only used to disambiguate the rare case of the same two programs
meeting twice in one season (e.g. a regular-season game and a rematch in
a conference championship or bowl) -- see `AMBIGUOUS_GAME_MAPPING` in
cfb_coverage_reason.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason
from cfb_edge_finder.schemas.game import GameRecord
from cfb_edge_finder.teams.registry import (
    AmbiguousTeamAliasError,
    UnknownTeamAliasError,
    resolve_team_alias,
)

GAME_DATE_MATCH_WINDOW = timedelta(hours=36)
"""How far a candidate GameRecord's kickoff_utc may sit from the evidence's
own reference timestamp (close_time, or a ticker-parsed date) and still be
considered the same physical game. Wide enough to absorb a market closing
well before kickoff or briefly after it starts, narrow enough that it
cannot span two genuinely different weekend windows."""

_SEPARATOR_PATTERNS = (" at ", " vs. ", " vs ", " v. ", " v ", "@")


@dataclass(frozen=True)
class KalshiGameEvidence:
    """Exactly what one Kalshi market/event offers as team/date evidence --
    the caller (a discovery/ingestion script) is responsible for extracting
    these from the real Kalshi JSON shape; this module has no opinion on
    where they came from."""

    market_ticker: str
    event_ticker: str | None
    title: str | None
    subtitle: str | None
    reference_timestamp: datetime | None
    """A timestamp evidencing when the game is/was -- typically the
    market's close_time or the event's strike_date, whichever the caller's
    discovery step captured."""
    raw_home_name: str | None = None
    raw_away_name: str | None = None
    """Set these directly when the caller's Kalshi payload already
    structures home/away separately (preferred, when available) --
    skips title-string splitting entirely."""


@dataclass(frozen=True)
class KalshiGameMappingResult:
    """`reason=None` means game identity was resolved successfully --
    `game_id`/`home_team_id`/`away_team_id` are then set. This module only
    resolves GAME IDENTITY; it has no opinion on market family or
    FBS-vs-FBS population, so it must never itself claim
    `MAPPED_SUPPORTED` (a downstream step, which also knows the market
    family and the mapped game's classification, makes that call -- see
    `classify_mapped_market` below). Any non-None `reason` means mapping
    failed, `game_id` is None, and `detail` explains why."""

    reason: KalshiCfbCoverageReason | None
    game_id: str | None
    detail: str
    home_team_id: str | None = None
    away_team_id: str | None = None


def _split_title(title: str) -> tuple[str, str] | None:
    """Best-effort split of a free-text title into (first_team, second_team)
    strings, unresolved. Returns None if no known separator is found --
    callers must not guess a split that isn't there."""
    for sep in _SEPARATOR_PATTERNS:
        if sep in title:
            left, _, right = title.partition(sep)
            left, right = left.strip(), right.strip()
            if left and right:
                return left, right
    return None


def _resolve_one(raw_name: str) -> tuple[str | None, str | None]:
    """Returns (team_id, error_detail). Exactly one is None."""
    try:
        return resolve_team_alias(raw_name), None
    except AmbiguousTeamAliasError as exc:
        return None, f"{raw_name!r} is ambiguous: {exc}"
    except UnknownTeamAliasError as exc:
        return None, f"{raw_name!r} is unknown: {exc}"


def map_kalshi_event_to_game(
    evidence: KalshiGameEvidence, candidate_games: list[GameRecord]
) -> KalshiGameMappingResult:
    """The single entry point. Never raises -- every failure path returns a
    `KalshiGameMappingResult` with an explicit `reason` and human-readable
    `detail`, so a caller can record it in the coverage ledger without a
    try/except around this call."""
    if evidence.raw_home_name and evidence.raw_away_name:
        first_raw, second_raw = evidence.raw_home_name, evidence.raw_away_name
    elif evidence.title:
        split = _split_title(evidence.title)
        if split is None:
            return KalshiGameMappingResult(
                reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
                game_id=None,
                detail=f"could not split title {evidence.title!r} into two team names via any known separator",
            )
        first_raw, second_raw = split
    else:
        return KalshiGameMappingResult(
            reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
            game_id=None,
            detail="no title and no structured home/away name evidence to parse",
        )

    first_id, first_error = _resolve_one(first_raw)
    second_id, second_error = _resolve_one(second_raw)
    if first_id is None or second_id is None:
        details = [d for d in (first_error, second_error) if d]
        return KalshiGameMappingResult(
            reason=KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING,
            game_id=None,
            detail="; ".join(details),
        )
    if first_id == second_id:
        return KalshiGameMappingResult(
            reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
            game_id=None,
            detail=f"both sides resolved to the same team_id {first_id!r} -- evidence is malformed",
        )

    team_pair = {first_id, second_id}
    matches = [g for g in candidate_games if {g.home_team_id, g.away_team_id} == team_pair]

    if evidence.reference_timestamp is not None and len(matches) > 1:
        matches = [
            g
            for g in matches
            if g.kickoff_utc is not None
            and abs(g.kickoff_utc - evidence.reference_timestamp) <= GAME_DATE_MATCH_WINDOW
        ]

    if len(matches) == 1:
        game = matches[0]
        return KalshiGameMappingResult(
            reason=None,
            game_id=game.game_id,
            detail=f"unique match: {game.home_team_name} vs {game.away_team_name} ({game.game_id})",
            home_team_id=game.home_team_id,
            away_team_id=game.away_team_id,
        )
    if len(matches) == 0:
        return KalshiGameMappingResult(
            reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
            game_id=None,
            detail=(
                f"team names resolved to {first_id!r}/{second_id!r} but no candidate GameRecord "
                f"has exactly that team pair within the date window"
            ),
        )
    return KalshiGameMappingResult(
        reason=KalshiCfbCoverageReason.AMBIGUOUS_GAME_MAPPING,
        game_id=None,
        detail=(
            f"{len(matches)} candidate games match team pair {first_id!r}/{second_id!r} within the "
            f"date window -- {[g.game_id for g in matches]}"
        ),
    )
