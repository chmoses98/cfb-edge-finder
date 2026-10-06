"""Kalshi-native physical-game identity -- no external schedule provider.

*** WHY THIS REPLACES THE CFBD-BASED MAPPING ***
The previous live path established game identity by fetching a CFBD
schedule and resolving Kalshi's team strings against an FBS-only alias
registry. That path needed `CFBD_API_KEY`, was FBS-only by construction,
and a single live capture produced 3,447 unresolved observations
(FCS-vs-FCS games could never resolve at all, because neither the alias
registry nor the candidate game pool contained them).

Kalshi answers the identity question itself, twice over:

  1. Every game-level event ticker is `<SERIES>-<GAMEKEY>`, where GAMEKEY
     is `YYMONDD` + team codes -- e.g. KXNCAAFSPREAD-26SEP17SYRPITT and
     KXNCAAF1HTOTAL-26SEP17SYRPITT share `26SEP17SYRPITT`. The suffix is
     the join key across every series for one physical game.
  2. A `football_game` milestone whose `details.league == "NCAAFB"` names
     that game's events directly in `primary_event_tickers` /
     `related_event_tickers`, and carries `details.conference`,
     `details.division` (FBS/FCS), `details.season.{year,type,week}`,
     `details.status` and `details.tier`.

Both are Kalshi's own data, so an FCS-vs-FCS or FBS-vs-FCS game is
identified exactly as well as a marquee SEC game, and no API key is
required for any of it.

*** WHY HUMAN-READABLE NAMES COME FROM THE EVENT, NOT A REGISTRY ***
An event carries `title` ("Syracuse vs Pittsburgh") and `sub_title`
("SYR vs PITT (Sep 17)"). Those are Kalshi's own labels for the contract a
reader is being asked to price, so they are copied verbatim rather than
re-derived from team codes -- a name this catalog prints should be the
name the exchange prints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

GAME_KEY_PATTERN = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<game_key>\d{2}[A-Z]{3}\d{2}[A-Z0-9]+)$")
"""`<SERIES>-<YYMONDD><TEAMCODES>`. Verified against 1,946 live open CFB
event tickers: 1,669 matched and the 277 that did not were all
season-level inventory (KXNCAAFAWARD-26APPOY, KXNCAAFSEC-26,
KXNCAAFBIGTENWINS-26W10), which correctly have no physical game."""

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_DATE_PREFIX = re.compile(r"^(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<teams>[A-Z0-9]*)$")

# Game keys that parse as a date but are NOT a physical game. Weekly poll
# and award events reuse the date grammar with a round/slot suffix
# (KXNCAAFCFPPOLL-26NOV17R1, KXNCAAFSTADIUM-29APR01ALOHA), so the DATE
# pattern alone is not sufficient evidence of a game.
_NON_GAME_SERIES_MARKERS = (
    "POLL",
    "RANK",
    "AWARD",
    "SEED",
    "STADIUM",
    "CHAMPION",
    "QUAL",
    "WINS",
    "UPSET",
    "COACH",
    "CONF",
    "PLAYOFF",
    "LEADER",
    "REGTOP",
    "FINALIST",
    "JOINCONF",
    "TFUT",
    "H2HWINS",
)


@dataclass(frozen=True)
class EventTickerParts:
    series_ticker: str
    game_key: str
    team_codes: str
    game_date: datetime | None


def parse_event_ticker(event_ticker: str) -> EventTickerParts | None:
    """Split a game-level event ticker into series + physical game key.
    Returns None for season-level inventory, which has no physical game."""
    if not event_ticker:
        return None
    match = GAME_KEY_PATTERN.match(event_ticker.upper())
    if not match:
        return None
    series_ticker = match.group("series")
    game_key = match.group("game_key")
    if any(marker in series_ticker for marker in _NON_GAME_SERIES_MARKERS):
        return None

    date_match = _DATE_PREFIX.match(game_key)
    game_date: datetime | None = None
    team_codes = ""
    if date_match:
        team_codes = date_match.group("teams")
        month = _MONTHS.get(date_match.group("mon"))
        if month:
            try:
                game_date = datetime(
                    2000 + int(date_match.group("yy")), month, int(date_match.group("dd")), tzinfo=UTC
                )
            except ValueError:
                game_date = None
    # A game key with no team codes at all (KXNCAAFCFPPOLL-26NOV17R1 style
    # already excluded above) is not a matchup.
    if not team_codes:
        return None
    return EventTickerParts(
        series_ticker=series_ticker, game_key=game_key, team_codes=team_codes, game_date=game_date
    )


CFB_COMPETITIONS = frozenset({"NCAA FOOTBALL", "COLLEGE FOOTBALL", "COLLEGE FOOTBALL PLAYOFFS"})
"""`product_metadata.competition` values that mean college football.

"College Football Playoffs" is in this set because of a real near-miss: a
live sweep of 1,505 KXNCAAFGAME events returned competition="NCAA
Football" for 1,495 of them and "College Football Playoffs" for the other
10. Matching only the majority string would have silently dropped every
CFP game -- the highest-profile games of the season -- from the catalog."""


def is_cfb_event(event: dict[str, Any]) -> bool:
    """Is this event college football, on Kalshi's own say-so?

    `product_metadata.competition` is the authoritative signal and is
    checked first. The ticker-shape fallback exists for an event that
    omits the metadata: refusing such an event would be exactly the
    "a missing field means no market exists" assumption this mission
    forbids."""
    metadata = event.get("product_metadata") or {}
    competition = str(metadata.get("competition") or "").strip().upper()
    if competition:
        return competition in CFB_COMPETITIONS
    ticker = str(event.get("event_ticker") or event.get("ticker") or "").upper()
    series = str(event.get("series_ticker") or "").upper()
    return any(t.startswith(("KXNCAAF", "KXCFB", "KXCFP")) for t in (ticker, series))


@dataclass(frozen=True)
class MilestoneGame:
    """One physical college-football game, as Kalshi's milestone sees it."""

    milestone_id: str
    title: str | None
    start_date: datetime | None
    status: str | None
    league: str | None
    division: str | None
    conference: str | None
    tier: str | None
    season_year: int | None
    season_type: str | None
    season_week: int | None
    main_game_event_ticker: str | None
    event_tickers: tuple[str, ...]
    """Union of primary_event_tickers and related_event_tickers, order
    preserved and de-duplicated. Both are unioned rather than trusting
    `related` alone -- a milestone whose primary tickers sat outside its
    related list would otherwise lose them."""
    raw: dict[str, Any]

    @property
    def game_key(self) -> str | None:
        for ticker in ((self.main_game_event_ticker,) + self.event_tickers):
            if not ticker:
                continue
            parts = parse_event_ticker(ticker)
            if parts:
                return parts.game_key
        return None


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.year <= 1:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def milestone_from_payload(payload: dict[str, Any]) -> MilestoneGame:
    details = payload.get("details") or {}
    season = details.get("season") or {}
    primary = [t for t in (payload.get("primary_event_tickers") or []) if isinstance(t, str)]
    related = [t for t in (payload.get("related_event_tickers") or []) if isinstance(t, str)]
    ordered = list(dict.fromkeys(primary + related))
    return MilestoneGame(
        milestone_id=str(payload.get("id") or ""),
        title=payload.get("title"),
        start_date=_parse_ts(payload.get("start_date")),
        status=details.get("status"),
        league=details.get("league"),
        division=details.get("division"),
        conference=details.get("conference"),
        tier=details.get("tier"),
        season_year=season.get("year"),
        season_type=season.get("type"),
        season_week=season.get("week"),
        main_game_event_ticker=details.get("main_game_event_ticker"),
        event_tickers=tuple(ordered),
        raw=dict(payload),
    )


CFB_MILESTONE_LEAGUES = frozenset({"NCAAFB"})
"""`details.league` values that mean college football.

This is the filter the mission's documented `competition="College
Football"` parameter was meant to be. A live probe established there is no
`competition` FIELD on a milestone at all, which is why that filter
returned HTTP 200 with zero rows: the league lives here, spelled
"NCAAFB", and `type=football_game` narrows the exchange's 24,000+
milestones to ~2,052 (of which 1,519 are NCAAFB) in about eleven pages."""

MILESTONE_TYPE_FOOTBALL_GAME = "football_game"

LIVE_MILESTONE_STATUSES = frozenset({"scheduled", "created", "inprogress", "time-tbd"})
"""`details.status` values that mean the game has not finished. Observed
live across 1,519 NCAAFB milestones: scheduled (751), closed (542),
complete (126), created (48), time-tbd (41), inprogress (11).

`time-tbd` is included deliberately: a game whose kickoff is not yet
scheduled is still an upcoming game with tradeable markets, and excluding
it would drop real inventory for a purely administrative reason."""


def is_upcoming_cfb_milestone(milestone: MilestoneGame, as_of: datetime, horizon_days: float | None = None) -> bool:
    """Is this a college-football game we should publish a menu for?

    A game qualifies on its STATUS, with the date window applied only when
    a horizon is requested. Status is the primary test because a milestone
    start_date can be a placeholder (live evidence includes start dates of
    2000-01-01 on non-game milestones), and a game in progress still has
    tradeable markets."""
    if (milestone.league or "").upper() not in CFB_MILESTONE_LEAGUES:
        return False
    if (milestone.status or "").lower() not in LIVE_MILESTONE_STATUSES:
        return False
    if horizon_days is None:
        return True
    start = milestone.start_date
    if start is None:
        # No date but a live status: keep it. Dropping an undated, live game
        # would be a silent loss, which this catalog never does.
        return True
    delta_days = (start - as_of).total_seconds() / 86400.0
    return -1.0 <= delta_days <= horizon_days
