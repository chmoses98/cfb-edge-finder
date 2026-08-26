"""Raw CFBD games + advanced team-game stats -> a compact, leakage-labeled
`TeamGameLine` corpus -- the single training/evaluation unit for every
model in this package.

*** DATA PROVENANCE ***
`/games` (points, classification, neutral site) was live-verified twice in
Milestone B. `/stats/game/advanced` (plays -- the only source of the pace
signal used in this package) has NOT been independently live-verified;
its shape is built from genuine primary-source docs
(github.com/CFBD/cfbd-python) -- see docs/MILESTONE_C.md's data audit.
Every corpus artifact this module produces carries a `source` and
`captured_at` field so that distinction is never lost downstream.

Two `TeamGameLine` rows are produced per completed game (one per team's
perspective) -- this is the standard shape for an additive offense/defense
rating fit (see modeling/ratings.py): each row says "this team scored
X points against that opponent, at home/away/neutral, using N plays."

*** WHY TWO ROWS PER GAME, NOT ONE ***
An offense/defense rating model needs "team T's offense against opponent
O" as its own observation, independent of which team is nominally "home"
in the schema -- fitting directly on GameRecord's home/away shape would
bias the regression's row-weighting toward home teams for no principled
reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field

from cfb_edge_finder.ids import slugify_team
from cfb_edge_finder.ingestion.game_normalization import away_classification, home_classification
from cfb_edge_finder.ingestion.team_matching import TeamResolutionError, resolve_team_id_for_game
from cfb_edge_finder.ingestion.week_labels import UnclassifiablePostseasonError, derive_week_metadata
from cfb_edge_finder.modeling.leakage import AsOf, postseason_week_rank

CFBD_SOURCE_NAME = "cfbd"


class TeamGameLine(BaseModel):
    model_config = {"frozen": True}

    source_game_id: str = Field(..., description="Raw CFBD numeric game id, as a string")
    season: int
    week: int = Field(..., description="Regular-season week, or a postseason rank from leakage.postseason_week_rank")
    is_postseason: bool
    team_id: str
    opponent_id: str
    team_classification: str | None = Field(default=None, description="'fbs'/'fcs'/etc, as reported by CFBD")
    opponent_classification: str | None = None
    team_conference: str | None = Field(
        default=None,
        description="This team's CFBD-reported conference AS OF THIS GAME'S SEASON ('homeConference'/"
        "'awayConference' on the raw /games row) -- a season-scoped, pregame-known fact (conference "
        "membership for a season is fixed well before kickoff), NEVER the current/2026 team registry. "
        "Realignment means a team's current conference can differ from its historical one -- see "
        "modeling/diagnostics.py's is_conference_game for why this distinction matters.",
    )
    opponent_conference: str | None = None
    is_conference_game: bool | None = Field(
        default=None,
        description="CFBD's own per-game 'conferenceGame' flag, as reported for that season -- the "
        "authoritative historical source for conference-game classification. None only if CFBD/the "
        "source row didn't report it, in which case a caller should fall back to comparing "
        "team_conference == opponent_conference.",
    )
    is_home: bool
    is_neutral_site: bool
    team_points: int = Field(..., ge=0)
    opponent_points: int = Field(..., ge=0)
    team_plays: int | None = Field(
        default=None, description="From /stats/game/advanced -- None if that endpoint had no row for this team/game"
    )
    kickoff_utc: AwareDatetime | None = None
    source: str = CFBD_SOURCE_NAME
    captured_at: AwareDatetime

    @property
    def as_of(self) -> AsOf:
        """This row only becomes usable as training/rating-fit input for
        predictions strictly AFTER the week it occurred in -- see
        leakage.assert_strictly_before.
        """
        return AsOf(season=self.season, week=self.week)


def _parse_kickoff(raw_start_date: str | None) -> AwareDatetime | None:
    if not raw_start_date:
        return None
    try:
        return datetime.fromisoformat(raw_start_date.replace("Z", "+00:00"))
    except ValueError:
        return None


def _points(raw_game: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = raw_game.get(key)
        if value is not None:
            return int(value)
    return None


def _is_fbs_involved(home_class: str | None, away_class: str | None) -> bool:
    """Same policy as Milestone B's scripts/ingest_schedule.py
    `_is_fbs_involved`: at least one side must be FBS. Excludes the
    non-FBS-vs-non-FBS games (FCS-vs-FCS, D-II, D-III, NAIA) that CFBD's
    `division=fbs` filter does not fully exclude from `/games` -- these
    are not part of Milestone C's modeling population at all, and must not
    be silently miscounted into the "FBS-vs-FCS" segment.
    """
    return (home_class or "").strip().lower() == "fbs" or (away_class or "").strip().lower() == "fbs"


def build_team_game_lines(
    raw_games: list[dict[str, Any]],
    raw_advanced_stats: list[dict[str, Any]],
    *,
    captured_at: AwareDatetime,
) -> tuple[list[TeamGameLine], list[dict[str, str]]]:
    """Builds TeamGameLine rows from raw CFBD /games + /stats/game/advanced
    responses. Only games CFBD reports as `completed` with both scores
    present are included -- an in-progress or future game has no team-game
    line yet by definition (it has no outcome to train or evaluate on).

    Team-name resolution reuses the exact same lenient-for-non-FBS,
    fail-loud-on-ambiguity policy as Milestone B's schedule ingestion
    (`resolve_team_id_for_game`) -- an unresolved *FBS* name or a
    genuinely ambiguous name (e.g. bare "Miami") is skipped and reported,
    never silently guessed. Returns (lines, skipped) where `skipped` is a
    list of {"game_id", "reason"} dicts for anything excluded, so a caller
    can report accounting for every discovered game rather than a game
    silently vanishing.
    """
    plays_by_game_team: dict[tuple[str, str], int] = {}
    for row in raw_advanced_stats:
        game_id = str(row.get("game_id") or row.get("gameId") or "")
        team_name = row.get("team")
        offense = row.get("offense") or {}
        plays = offense.get("plays")
        if game_id and team_name and plays is not None:
            plays_by_game_team[(game_id, team_name)] = int(plays)

    lines: list[TeamGameLine] = []
    skipped: list[dict[str, str]] = []

    for raw in raw_games:
        if not raw.get("completed"):
            continue
        game_id = str(raw.get("id"))
        season = raw.get("season")
        raw_season_type = raw.get("seasonType") or raw.get("season_type")
        is_postseason = raw_season_type == "postseason"
        if season is None:
            skipped.append({"game_id": game_id, "reason": "missing season"})
            continue

        if is_postseason:
            postseason_descriptor = raw.get("notes") or raw.get("name") or raw.get("gameName")
            try:
                meta = derive_week_metadata(
                    season_type_raw=raw_season_type,
                    week_raw=raw.get("week"),
                    postseason_descriptor=postseason_descriptor,
                    playoff=raw.get("playoff"),
                )
                week = postseason_week_rank(meta.season_type, meta.cfp_round)
            except (UnclassifiablePostseasonError, ValueError) as exc:
                skipped.append({"game_id": game_id, "reason": f"unclassifiable postseason game: {exc}"})
                continue
        else:
            week = raw.get("week")
            if week is None:
                skipped.append({"game_id": game_id, "reason": "missing week"})
                continue

        home_name = raw.get("homeTeam")
        away_name = raw.get("awayTeam")
        home_pts = _points(raw, "homePoints", "home_points")
        away_pts = _points(raw, "awayPoints", "away_points")
        if home_pts is None or away_pts is None:
            skipped.append({"game_id": game_id, "reason": "completed but missing a score"})
            continue

        home_class = home_classification(raw)
        away_class = away_classification(raw)
        neutral = bool(raw.get("neutralSite") or raw.get("neutral_site"))
        kickoff = _parse_kickoff(raw.get("startDate") or raw.get("start_date"))
        home_conf = raw.get("homeConference") or raw.get("home_conference")
        away_conf = raw.get("awayConference") or raw.get("away_conference")
        raw_conference_game = raw.get("conferenceGame")
        if raw_conference_game is None:
            raw_conference_game = raw.get("conference_game")
        conference_game_flag = None if raw_conference_game is None else bool(raw_conference_game)

        if not _is_fbs_involved(home_class, away_class):
            # CFBD's own `division=fbs` query parameter does not fully
            # exclude non-FBS-involving games -- Milestone B independently
            # found a genuine Division II game slip through it (see
            # docs/MILESTONE_B.md). Milestone C's corpus is scoped to
            # "at least one side is FBS" by the same explicit policy
            # Milestone B's ingest_schedule.py already applies
            # (`_is_fbs_involved`); this is the modeling-corpus equivalent,
            # not a new decision. A genuinely-FBS-vs-FCS game still passes
            # this check -- only games with NO FBS side at all are excluded.
            skipped.append({"game_id": game_id, "reason": "no FBS side on either team"})
            continue

        try:
            home_id = resolve_team_id_for_game(home_name, CFBD_SOURCE_NAME, home_class)
        except TeamResolutionError as exc:
            skipped.append({"game_id": game_id, "reason": f"home team {home_name!r} unresolved: {exc}"})
            continue
        try:
            away_id = resolve_team_id_for_game(away_name, CFBD_SOURCE_NAME, away_class)
        except TeamResolutionError as exc:
            skipped.append({"game_id": game_id, "reason": f"away team {away_name!r} unresolved: {exc}"})
            continue

        home_plays = plays_by_game_team.get((game_id, home_name))
        away_plays = plays_by_game_team.get((game_id, away_name))

        lines.append(
            TeamGameLine(
                source_game_id=game_id,
                season=season,
                week=week,
                is_postseason=is_postseason,
                team_id=home_id,
                opponent_id=away_id,
                team_classification=home_class,
                opponent_classification=away_class,
                team_conference=home_conf,
                opponent_conference=away_conf,
                is_conference_game=conference_game_flag,
                is_home=True,
                is_neutral_site=neutral,
                team_points=home_pts,
                opponent_points=away_pts,
                team_plays=home_plays,
                kickoff_utc=kickoff,
                captured_at=captured_at,
            )
        )
        lines.append(
            TeamGameLine(
                source_game_id=game_id,
                season=season,
                week=week,
                is_postseason=is_postseason,
                team_id=away_id,
                opponent_id=home_id,
                team_classification=away_class,
                opponent_classification=home_class,
                team_conference=away_conf,
                opponent_conference=home_conf,
                is_conference_game=conference_game_flag,
                is_home=False,
                is_neutral_site=neutral,
                team_points=away_pts,
                opponent_points=home_pts,
                team_plays=away_plays,
                kickoff_utc=kickoff,
                captured_at=captured_at,
            )
        )

    return lines, skipped


def fallback_team_id(raw_name: str) -> str:
    """Used only by research/CLI callers that need a best-effort id for a
    name outside the resolution above (e.g. printing a diagnostic) --
    never used inside build_team_game_lines itself, which always goes
    through the fail-loud/lenient-for-non-FBS policy above.
    """
    return slugify_team(raw_name)
