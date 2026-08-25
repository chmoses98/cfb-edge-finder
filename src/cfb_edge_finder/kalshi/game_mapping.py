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
resolve is always AMBIGUOUS_TEAM_MAPPING, never a best-guess -- UNLESS
both raw names are deterministically identified as FCS programs (see
below), in which case the market is a distinct, understood unsupported
population, not an ambiguity.

*** FCS-VS-FCS (Milestone D hardening pass) ***
`teams.registry` is FBS-only by design, and this module's own candidate
`GameRecord` pool is always FBS-scoped (the caller fetches CFBD's
schedule with `division="fbs"`) -- so a genuine FCS-vs-FCS Kalshi market
(e.g. "Cornell vs Colgate") can NEVER resolve team identity via either
path: both raw names raise `UnknownTeamAliasError`, and even if they
somehow did resolve, no FCS-vs-FCS `GameRecord` is ever a candidate. A
first pass over a genuine live capture (3,447 `TICKER_UNRESOLVED`
observations, including these) mischaracterized ALL of them as an
undifferentiated parse failure. `fcs_school_names` (optional, supplied
by the caller from `teams.fcs_identity.build_fcs_school_name_set` over
`CFBDClient.fetch_all_division_teams()`) lets `map_kalshi_event_to_game`
recognize this specific, real, understood case
(`KalshiCfbCoverageReason.FCS_VS_FCS`) and keep it distinct from a
genuinely unresolvable market -- without adding any FCS alias
resolution, fuzzy matching, or statistical modeling (see
`teams/fcs_identity.py`'s own docstring for why this is deliberately
minimal).

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
from cfb_edge_finder.schemas.common import MarketFamily
from cfb_edge_finder.schemas.game import GameRecord
from cfb_edge_finder.teams.fcs_identity import is_known_fcs_school
from cfb_edge_finder.teams.registry import (
    AmbiguousTeamAliasError,
    UnknownTeamAliasError,
    resolve_team_alias,
)

CORE_V1_MARKET_FAMILIES = frozenset({MarketFamily.MONEYLINE, MarketFamily.SPREAD, MarketFamily.TOTAL})
"""The three families kalshi/cfb_market_family_registry.py marks
CORE_V1 (game_winner/point_spread/game_total) -- the only families the
C.2 model is wired to price via kalshi/market_pricing.py. Any other
MarketFamily reaching classify_mapped_market is a real, mapped Kalshi
contract this milestone simply doesn't build pricing for yet, not a
parsing failure -- see MAPPED_UNSUPPORTED_FAMILY below."""

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
    evidence: KalshiGameEvidence,
    candidate_games: list[GameRecord],
    fcs_school_names: frozenset[str] = frozenset(),
) -> KalshiGameMappingResult:
    """The single entry point. Never raises -- every failure path returns a
    `KalshiGameMappingResult` with an explicit `reason` and human-readable
    `detail`, so a caller can record it in the coverage ledger without a
    try/except around this call.

    `fcs_school_names`: optional, exact-match-normalized set of known FCS
    school names (see `teams.fcs_identity`). Defaults to empty -- callers
    that don't supply it get IDENTICAL behavior to before this parameter
    existed (both sides simply fall through to AMBIGUOUS_TEAM_MAPPING, as
    always). When supplied and BOTH raw team names match it exactly, a
    genuine FCS-vs-FCS market is classified as FCS_VS_FCS instead."""
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
        if is_known_fcs_school(first_raw, fcs_school_names) and is_known_fcs_school(second_raw, fcs_school_names):
            return KalshiGameMappingResult(
                reason=KalshiCfbCoverageReason.FCS_VS_FCS,
                game_id=None,
                detail=(
                    f"{first_raw!r} and {second_raw!r} are both deterministically identified as FCS "
                    f"programs (CFBD /teams classification) -- teams.registry is FBS-only by design, "
                    f"so game identity is not further resolved; this is an understood unsupported "
                    f"population, not a parse failure"
                ),
            )
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


def classify_mapped_market(
    mapping: KalshiGameMappingResult,
    *,
    market_family: MarketFamily | None,
    home_classification: str | None,
    away_classification: str | None,
) -> KalshiCfbCoverageReason:
    """The downstream step promised by `KalshiGameMappingResult`'s
    docstring and by `contract_semantics.ParsedContract`'s own module
    docstring -- the ONLY place in this codebase allowed to return
    `MAPPED_SUPPORTED`, because it is the only place three separate
    facts are all available together: game identity (`mapping`), market
    family (`market_family`, from a `ParsedContract`), and both teams'
    classification (from the caller's own CFBD ingestion -- see
    `ingestion.game_normalization.home_classification`/
    `away_classification`; `GameRecord` itself deliberately carries no
    classification field, see schemas/game.py).

    Never raises -- always returns a `KalshiCfbCoverageReason`, so a
    caller can record it directly in the coverage ledger.
    """
    if mapping.reason is not None:
        # Game identity itself never resolved -- pass the mapping
        # failure straight through; family/classification are moot.
        return mapping.reason

    if market_family is None:
        return KalshiCfbCoverageReason.PARSE_UNRESOLVED

    if market_family not in CORE_V1_MARKET_FAMILIES:
        # A real, identity-mapped Kalshi contract in a family this
        # milestone's model simply isn't built to price yet (e.g.
        # ALT_SPREAD, TEAM_TOTAL, FIRST_HALF_*) -- not a parse failure.
        return KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_FAMILY

    if home_classification != "fbs" or away_classification != "fbs":
        # Mapped, and a CORE_V1 family, but the C.2 model is only
        # trained/validated for FBS-vs-FBS -- mission section 9 requires
        # this population to stay in coverage, never silently priced.
        return KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_POPULATION

    return KalshiCfbCoverageReason.MAPPED_SUPPORTED
