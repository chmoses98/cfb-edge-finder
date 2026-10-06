"""Turning free public payloads into factual context. PURE.

*** NOTHING HERE MAKES A NETWORK CALL ***
Every function takes a payload somebody else fetched and returns a
`ContextField`. That is what makes the factual layer testable without a
credential, without egress, and without a live college-football Saturday --
and it is why a source changing shape is a failing test rather than a slate
full of silently empty domains.

*** WHICH SOURCES, AND WHY THESE ***
Free and keyless first, because the live execution path has always been
credential-free and a paid dependency is the owner's decision, not this
module's:

  ESPN scoreboard         records, scoring, recent results, venue, rest,
  (site.web.api, cdn)     neutral-site, and ESPN's own weather block.
                          ONE request per date covers the whole slate, so a
                          fortnight of history is fourteen requests for 115
                          games -- which is what makes the derived scoring
                          and rest numbers affordable at all.
  ESPN core API           per-team injury lists, and posted odds as a MARKET
                          REFERENCE (never as an input to a fair value).
  Open-Meteo              hourly forecast at the kickoff hour, when a venue's
                          coordinates are known. Keyless.
  CFBD                    OPTIONAL and keyed. Efficiency, situational and
                          turnover/pace context. Absent key -> absent domains,
                          marked missing with that as the reason. The free
                          tier's quota is small, so this is the one source the
                          collector is allowed to skip entirely.

*** EVERYTHING IS A FACT OR ARITHMETIC ON FACTS ***
`points_per_game` is a division. `rest_days` is a subtraction. There is no
opponent adjustment, no rating, no projection and no fitted coefficient
anywhere in this module, and `tests/test_execution_context.py` asserts it.

*** AN EMPTY LIST AND A FAILED FETCH ARE DIFFERENT ***
ESPN returning `injuries: []` is the observation "no injuries listed", quality
`fresh`. ESPN returning 403 is the observation "we could not look", quality
`missing`. Both are recorded; neither is allowed to look like the other.

*** AND A MARKET REFERENCE IS NOT A FAIR VALUE ***
`market_reference` carries what a sportsbook has posted, because a handicapper
asking "am I looking at the same game everyone else is" deserves an answer.
It is a FACT ABOUT THE MARKET and it never reaches the evaluator: no fair
probability in this repository is a function of a posted line, and
`disagreement.py` exists precisely so the comparison is reported rather than
obeyed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cfb_edge_finder.execution.context import ContextField, per_game, rest_days
from cfb_edge_finder.execution.quality import FieldQuality

ESPN_SCOREBOARD = "espn.scoreboard"
ESPN_CORE_INJURIES = "espn.core.injuries"
ESPN_CORE_ODDS = "espn.core.odds"
OPEN_METEO = "open-meteo.forecast"
CFBD_ADVANCED = "cfbd.advanced_team_game_stats"

#: Completed games needed before a scoring average is reported as anything
#: better than `low_coverage`. Two games is a sample of two and the freshness
#: of the observation does not change that.
MIN_GAMES_FOR_SCORING_COVERAGE = 3


def _iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    text = _iso(value)
    return datetime.fromisoformat(text) if text else None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------- ESPN scoreboard


def competitors_of(event: dict[str, Any]) -> list[dict[str, Any]]:
    competitions = event.get("competitions") or []
    if not competitions or not isinstance(competitions[0], dict):
        return []
    return [c for c in (competitions[0].get("competitors") or []) if isinstance(c, dict)]


def team_names(competitor: dict[str, Any]) -> list[str]:
    """Every name ESPN offers for one team, longest first.

    Matching a Kalshi title to an ESPN team is a name problem and this module
    does not pretend otherwise: it publishes the candidates and lets the
    caller's own team registry do the resolving. A single "the" name chosen
    here would be this module quietly owning a mapping it cannot test."""
    team = competitor.get("team") or {}
    out = [
        str(team[key])
        for key in ("displayName", "location", "name", "shortDisplayName", "abbreviation")
        if team.get(key)
    ]
    return sorted(dict.fromkeys(out), key=len, reverse=True)


def record_summary(competitor: dict[str, Any]) -> str | None:
    for record in competitor.get("records") or []:
        if str(record.get("type") or record.get("name") or "").lower() in ("total", "overall"):
            return record.get("summary")
    records = competitor.get("records") or []
    summary = records[0].get("summary") if records and isinstance(records[0], dict) else None
    return str(summary) if summary else None


def identity_field(event: dict[str, Any], *, observed_at: str) -> ContextField:
    """Venue, neutral-site, surface and conference-game, from the scoreboard."""
    competitions = event.get("competitions") or []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    venue = competition.get("venue") or {}
    address = venue.get("address") or {}
    competitors = competitors_of(event)
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    values = {
        "espn_event_id": event.get("id"),
        "venue": venue.get("fullName"),
        "venue_city": address.get("city"),
        "venue_state": address.get("state"),
        "indoor": venue.get("indoor"),
        "neutral_site": competition.get("neutralSite"),
        "conference_game": competition.get("conferenceCompetition"),
        "espn_home": team_names(home)[0] if home else None,
        "espn_away": team_names(away)[0] if away else None,
        "kickoff_utc": _iso(event.get("date")),
    }
    populated = {k: v for k, v in values.items() if v is not None}
    if not populated:
        return ContextField.missing(
            "identity", "the scoreboard event carried no venue or competitor block", ESPN_SCOREBOARD
        )
    quality = (
        FieldQuality.FRESH.value
        if values.get("venue") and values.get("neutral_site") is not None
        else FieldQuality.PARTIAL.value
    )
    return ContextField(
        domain="identity",
        values=values,
        source=ESPN_SCOREBOARD,
        observed_at=observed_at,
        quality=quality,
        detail=None if quality == FieldQuality.FRESH.value else "venue or neutral-site flag absent",
    )


def records_field(event: dict[str, Any], *, observed_at: str) -> ContextField:
    competitors = competitors_of(event)
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    values = {
        "home_record": record_summary(home) if home else None,
        "away_record": record_summary(away) if away else None,
        "home_conference_id": ((home or {}).get("team") or {}).get("conferenceId"),
        "away_conference_id": ((away or {}).get("team") or {}).get("conferenceId"),
    }
    if not values["home_record"] and not values["away_record"]:
        return ContextField.missing(
            "records", "the scoreboard event carried no team records", ESPN_SCOREBOARD
        )
    quality = (
        FieldQuality.FRESH.value
        if values["home_record"] and values["away_record"]
        else FieldQuality.PARTIAL.value
    )
    return ContextField(
        domain="records",
        values=values,
        source=ESPN_SCOREBOARD,
        observed_at=observed_at,
        quality=quality,
        detail=None if quality == FieldQuality.FRESH.value else "one side's record is absent",
    )


def environment_field(event: dict[str, Any], *, observed_at: str) -> ContextField:
    """ESPN's own weather block for the event, if it published one."""
    competitions = event.get("competitions") or []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    weather = event.get("weather") or competition.get("weather") or {}
    venue = competition.get("venue") or {}
    values = {
        "temperature_f": weather.get("temperature") or weather.get("highTemperature"),
        "conditions": weather.get("displayValue") or weather.get("conditionId"),
        "precipitation_probability": weather.get("precipitation"),
        "indoor": venue.get("indoor"),
    }
    if venue.get("indoor") is True:
        # An indoor game HAS an environment and it is "indoors". Recording that
        # as `missing` would charge the batching cost model for research nobody
        # needs to do, and would tell a handicapper to go and look up weather
        # that cannot matter.
        return ContextField(
            domain="environment",
            values={"indoor": True, "conditions": "indoor venue; weather does not apply"},
            source=ESPN_SCOREBOARD,
            observed_at=observed_at,
            quality=FieldQuality.FRESH.value,
        )
    if not any(v is not None for k, v in values.items() if k != "indoor"):
        return ContextField.missing(
            "environment",
            "no weather block on the scoreboard event and no venue coordinates to forecast from",
            ESPN_SCOREBOARD,
        )
    return ContextField(
        domain="environment",
        values=values,
        source=ESPN_SCOREBOARD,
        observed_at=observed_at,
        quality=FieldQuality.FRESH.value,
    )


def open_meteo_environment(
    payload: dict[str, Any], *, kickoff: datetime | None, observed_at: str
) -> ContextField:
    """The hourly forecast for the kickoff hour, keyless.

    The forecast's own hour is matched to the kickoff rather than the first
    hour returned. A forecast for the wrong hour is worse than none: it is a
    real number about the wrong thing.
    """
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times or kickoff is None:
        return ContextField.missing(
            "environment", "no hourly forecast rows, or no kickoff to match them to", OPEN_METEO
        )
    target = kickoff.astimezone(UTC).strftime("%Y-%m-%dT%H:00")
    try:
        index = list(times).index(target)
    except ValueError:
        return ContextField.missing(
            "environment",
            f"the forecast does not cover the kickoff hour {target}",
            OPEN_METEO,
        )

    def at(key: str) -> Any:
        series = hourly.get(key) or []
        return series[index] if index < len(series) else None

    return ContextField(
        domain="environment",
        values={
            "forecast_hour_utc": target,
            "temperature_c": at("temperature_2m"),
            "wind_speed_10m": at("wind_speed_10m"),
            "wind_gusts_10m": at("wind_gusts_10m"),
            "precipitation_probability": at("precipitation_probability"),
            "precipitation_mm": at("precipitation"),
        },
        source=OPEN_METEO,
        observed_at=observed_at,
        quality=FieldQuality.FRESH.value,
    )


# ------------------------------------------- derived from prior scoreboards


def completed_games_for(team_names_wanted: set[str], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every completed prior game one team played, newest first.

    Matched on ESPN's own names, which is safe here because BOTH sides of the
    comparison came from ESPN: the team names in the upcoming event and the
    team names in the historical one. No cross-provider name mapping happens
    in this function, which is why it can be exact.
    """
    out: list[dict[str, Any]] = []
    for event in events:
        status = ((event.get("status") or {}).get("type") or {})
        if not status.get("completed"):
            continue
        competitors = competitors_of(event)
        mine = next(
            (c for c in competitors if set(team_names(c)) & team_names_wanted), None
        )
        if mine is None:
            continue
        theirs = next((c for c in competitors if c is not mine), None)
        out.append(
            {
                "date": _iso(event.get("date")),
                "points_for": _score_of(mine),
                "points_against": _score_of(theirs),
                "opponent": team_names(theirs)[0] if theirs else None,
                "home_away": mine.get("homeAway"),
                "neutral_site": ((event.get("competitions") or [{}])[0] or {}).get("neutralSite"),
            }
        )
    return sorted(out, key=lambda row: str(row["date"] or ""), reverse=True)


def _score_of(competitor: dict[str, Any] | None) -> float | None:
    """ESPN spells a score either as a string or as {"value": 24.0}.

    Both shapes appear on the same endpoint depending on the host and the
    season, so both are read. An unreadable score is None and the game is then
    excluded from the average, rather than counted as a shutout."""
    if competitor is None:
        return None
    score = competitor.get("score")
    if isinstance(score, dict):
        return _number(score.get("value"))
    return _number(score)


def scoring_field(
    home_history: list[dict[str, Any]],
    away_history: list[dict[str, Any]],
    *,
    observed_at: str,
) -> ContextField:
    """Points for and against per game. A division, and nothing else.

    NOT opponent-adjusted, and it says so in the field itself rather than only
    in a docstring: a reader who sees `points_for_per_game` and assumes it has
    been adjusted for schedule strength would be reading a number this
    repository has never computed.
    """

    def side(history: list[dict[str, Any]]) -> dict[str, Any]:
        scored = [row for row in history if row.get("points_for") is not None]
        return {
            "games_observed": len(scored),
            "points_for_per_game": per_game(
                sum(float(r["points_for"]) for r in scored) or 0.0, len(scored)
            ),
            "points_against_per_game": per_game(
                sum(float(r["points_against"] or 0.0) for r in scored) or 0.0, len(scored)
            ),
        }

    home = side(home_history)
    away = side(away_history)
    values = {
        "home_games_observed": home["games_observed"],
        "home_points_for_per_game": home["points_for_per_game"],
        "home_points_against_per_game": home["points_against_per_game"],
        "away_games_observed": away["games_observed"],
        "away_points_for_per_game": away["points_for_per_game"],
        "away_points_against_per_game": away["points_against_per_game"],
        "opponent_adjusted": False,
        "note": (
            "raw per-game scoring over the observed window. NOT adjusted for opponent quality, "
            "pace or garbage time; this repository computes no such adjustment."
        ),
    }
    thin = min(home["games_observed"], away["games_observed"])
    if thin == 0:
        return ContextField.missing(
            "scoring",
            "no completed games found for at least one team in the observed window",
            ESPN_SCOREBOARD,
        )
    quality = (
        FieldQuality.FRESH.value
        if thin >= MIN_GAMES_FOR_SCORING_COVERAGE
        else FieldQuality.LOW_COVERAGE.value
    )
    return ContextField(
        domain="scoring",
        values=values,
        source=ESPN_SCOREBOARD,
        observed_at=observed_at,
        quality=quality,
        detail=(
            None
            if quality == FieldQuality.FRESH.value
            else f"a team has only {thin} completed game(s) in the observed window"
        ),
    )


def recent_results_field(
    home_history: list[dict[str, Any]],
    away_history: list[dict[str, Any]],
    *,
    observed_at: str,
    limit: int = 4,
) -> ContextField:
    if not home_history and not away_history:
        return ContextField.missing(
            "recent_results", "no completed prior games in the observed window", ESPN_SCOREBOARD
        )
    return ContextField(
        domain="recent_results",
        values={
            "home_recent": home_history[:limit],
            "away_recent": away_history[:limit],
        },
        source=ESPN_SCOREBOARD,
        observed_at=observed_at,
        quality=(
            FieldQuality.FRESH.value
            if home_history and away_history
            else FieldQuality.PARTIAL.value
        ),
        detail=None if home_history and away_history else "one side has no prior game in the window",
    )


def rest_field(
    kickoff: datetime | None,
    home_history: list[dict[str, Any]],
    away_history: list[dict[str, Any]],
    *,
    observed_at: str,
) -> ContextField:
    """Rest days, and travel ONLY where it is reliably derivable.

    Travel is deliberately reported as the previous game's venue city rather
    than as a distance: a mileage number would need coordinates for both
    venues, and a mileage computed from a city name nobody geocoded is a
    fabricated fact with three significant figures.
    """
    home_last = _parse_dt(home_history[0]["date"]) if home_history else None
    away_last = _parse_dt(away_history[0]["date"]) if away_history else None
    values = {
        "home_rest_days": rest_days(kickoff, home_last),
        "away_rest_days": rest_days(kickoff, away_last),
        "home_last_game": home_history[0]["date"] if home_history else None,
        "away_last_game": away_history[0]["date"] if away_history else None,
        "away_previous_home_away": away_history[0]["home_away"] if away_history else None,
        "travel_distance": None,
        "travel_note": (
            "distance is not derived: venue coordinates for both games would be needed, and a "
            "mileage inferred from a city name is a fabricated fact"
        ),
    }
    if values["home_rest_days"] is None and values["away_rest_days"] is None:
        return ContextField.missing(
            "rest_and_travel",
            "neither team has a prior completed game in the observed window",
            ESPN_SCOREBOARD,
        )
    quality = (
        FieldQuality.FRESH.value
        if values["home_rest_days"] is not None and values["away_rest_days"] is not None
        else FieldQuality.PARTIAL.value
    )
    return ContextField(
        domain="rest_and_travel",
        values=values,
        source=ESPN_SCOREBOARD,
        observed_at=observed_at,
        quality=quality,
        detail=None if quality == FieldQuality.FRESH.value else "one side has no prior game",
    )


# --------------------------------------------------------- availability


def availability_field(
    home_payload: dict[str, Any] | None,
    away_payload: dict[str, Any] | None,
    *,
    observed_at: str,
) -> ContextField:
    """Injury lists, with the empty list recorded as an observation.

    *** THE ONE SUBSTITUTION THIS REPOSITORY MUST NEVER MAKE ***
    A payload we could not fetch is `missing`. A payload that came back with
    an empty `items` list is `fresh` and says "no injuries listed". Those are
    different facts and the difference is worth a touchdown on a college line.
    """
    if home_payload is None and away_payload is None:
        return ContextField.missing(
            "availability",
            "the injury endpoint could not be read for either team; this is NOT 'no injuries'",
            ESPN_CORE_INJURIES,
        )

    def listing(payload: dict[str, Any] | None) -> list[dict[str, Any]] | None:
        if payload is None:
            return None
        items = payload.get("items")
        if not isinstance(items, list):
            return None
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            athlete = item.get("athlete") or {}
            out.append(
                {
                    "athlete": athlete.get("displayName") or athlete.get("fullName"),
                    "position": ((athlete.get("position") or {}).get("abbreviation")),
                    "status": item.get("status") or (item.get("type") or {}).get("description"),
                    "detail": (item.get("details") or {}).get("type"),
                    "date": _iso(item.get("date")),
                }
            )
        return out

    home = listing(home_payload)
    away = listing(away_payload)
    if home is None and away is None:
        return ContextField.missing(
            "availability",
            "the injury endpoint answered but carried no readable items list",
            ESPN_CORE_INJURIES,
        )
    quality = (
        FieldQuality.FRESH.value
        if home is not None and away is not None
        else FieldQuality.PARTIAL.value
    )
    return ContextField(
        domain="availability",
        values={
            "home_injuries": home,
            "away_injuries": away,
            "home_listed": None if home is None else len(home),
            "away_listed": None if away is None else len(away),
            "empty_means": (
                "an empty list is the observation 'no injuries listed by this source', not an "
                "assertion that the team is healthy. A team whose list could not be read is null, "
                "never empty."
            ),
        },
        source=ESPN_CORE_INJURIES,
        observed_at=observed_at,
        quality=quality,
        detail=None if quality == FieldQuality.FRESH.value else "one team's list could not be read",
    )


# ------------------------------------------------------ market reference


def market_reference_field(payload: dict[str, Any] | None, *, observed_at: str) -> ContextField:
    """What a sportsbook has posted. A FACT ABOUT THE MARKET, not a fair value.

    Never reaches the evaluator. It is here so a handicapper can notice they
    are looking at a different game from everyone else before they write a
    distribution -- and `disagreement.py` does the same job after the fact,
    from the repository's own side.
    """
    if payload is None:
        return ContextField.missing(
            "market_reference", "no odds endpoint response", ESPN_CORE_ODDS
        )
    items = payload.get("items") or []
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "provider": (item.get("provider") or {}).get("name"),
                "spread": item.get("spread"),
                "over_under": item.get("overUnder"),
                "details": item.get("details"),
            }
        )
    if not rows:
        return ContextField(
            domain="market_reference",
            values={"posted": [], "observed": "no book has posted a line for this game yet"},
            source=ESPN_CORE_ODDS,
            observed_at=observed_at,
            quality=FieldQuality.FRESH.value,
        )
    return ContextField(
        domain="market_reference",
        values={
            "posted": rows,
            "not_a_fair_value": (
                "a posted line is the market's opinion. No fair probability in this repository is "
                "a function of it, and disagreement with it is reported rather than corrected."
            ),
        },
        source=ESPN_CORE_ODDS,
        observed_at=observed_at,
        quality=FieldQuality.FRESH.value,
    )


# ------------------------------------------------------------- CFBD


_EFFICIENCY_KEYS = (
    "successRate",
    "explosiveness",
    "ppa",
    "totalPPA",
    "lineYards",
    "openFieldYards",
)
_SITUATIONAL_KEYS = ("standardDowns", "passingDowns", "secondLevelYards", "powerSuccess", "stuffRate")


def _team_block(rows: list[dict[str, Any]], team: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("team") or "").strip().lower() == team.strip().lower():
            return row
    return None


def cfbd_efficiency_field(
    rows: list[dict[str, Any]] | None,
    home: str | None,
    away: str | None,
    *,
    observed_at: str,
) -> ContextField:
    """Per-play efficiency as CFBD publishes it, copied and not recomputed.

    `successRate`, `explosiveness` and `ppa` are CFBD's own derived columns.
    Copying them is reporting somebody else's published measurement, with the
    attribution on the row; recomputing them here would be this repository
    building the opponent-adjusted machinery it is not allowed to have.
    """
    if rows is None:
        return ContextField.missing(
            "efficiency",
            "CFBD was not consulted (no API key, or the quota gate was shut)",
            CFBD_ADVANCED,
        )
    home_block = _team_block(rows, home) if home else None
    away_block = _team_block(rows, away) if away else None
    if home_block is None and away_block is None:
        return ContextField.missing(
            "efficiency", "neither team appears in the CFBD response", CFBD_ADVANCED
        )

    def pick(block: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
        if block is None:
            return None
        offense = block.get("offense") or {}
        defense = block.get("defense") or {}
        return {
            "offense": {k: offense.get(k) for k in keys if offense.get(k) is not None},
            "defense": {k: defense.get(k) for k in keys if defense.get(k) is not None},
        }

    quality = (
        FieldQuality.FRESH.value
        if home_block is not None and away_block is not None
        else FieldQuality.PARTIAL.value
    )
    return ContextField(
        domain="efficiency",
        values={
            "home": pick(home_block, _EFFICIENCY_KEYS),
            "away": pick(away_block, _EFFICIENCY_KEYS),
            "attribution": "collegefootballdata.com advanced team game stats, copied verbatim",
        },
        source=CFBD_ADVANCED,
        observed_at=observed_at,
        quality=quality,
        detail=None if quality == FieldQuality.FRESH.value else "one team is absent from the response",
    )


def cfbd_situational_field(
    rows: list[dict[str, Any]] | None,
    home: str | None,
    away: str | None,
    *,
    observed_at: str,
) -> ContextField:
    if rows is None:
        return ContextField.missing(
            "situational",
            "CFBD was not consulted (no API key, or the quota gate was shut)",
            CFBD_ADVANCED,
        )
    home_block = _team_block(rows, home) if home else None
    away_block = _team_block(rows, away) if away else None
    if home_block is None and away_block is None:
        return ContextField.missing(
            "situational", "neither team appears in the CFBD response", CFBD_ADVANCED
        )

    def pick(block: dict[str, Any] | None) -> dict[str, Any] | None:
        if block is None:
            return None
        offense = block.get("offense") or {}
        return {k: offense.get(k) for k in _SITUATIONAL_KEYS if offense.get(k) is not None}

    return ContextField(
        domain="situational",
        values={"home": pick(home_block), "away": pick(away_block)},
        source=CFBD_ADVANCED,
        observed_at=observed_at,
        quality=(
            FieldQuality.FRESH.value
            if home_block is not None and away_block is not None
            else FieldQuality.PARTIAL.value
        ),
    )


def cfbd_turnovers_field(
    rows: list[dict[str, Any]] | None,
    home: str | None,
    away: str | None,
    *,
    observed_at: str,
) -> ContextField:
    if rows is None:
        return ContextField.missing(
            "turnovers_and_pace",
            "CFBD was not consulted (no API key, or the quota gate was shut)",
            CFBD_ADVANCED,
        )
    home_block = _team_block(rows, home) if home else None
    away_block = _team_block(rows, away) if away else None
    if home_block is None and away_block is None:
        return ContextField.missing(
            "turnovers_and_pace", "neither team appears in the CFBD response", CFBD_ADVANCED
        )

    def pick(block: dict[str, Any] | None) -> dict[str, Any] | None:
        if block is None:
            return None
        offense = block.get("offense") or {}
        return {
            "plays": offense.get("plays"),
            "drives": offense.get("drives"),
            "total_ppa": offense.get("totalPPA"),
        }

    return ContextField(
        domain="turnovers_and_pace",
        values={
            "home": pick(home_block),
            "away": pick(away_block),
            "note": "play and drive counts as published. No pace index is computed here.",
        },
        source=CFBD_ADVANCED,
        observed_at=observed_at,
        quality=(
            FieldQuality.FRESH.value
            if home_block is not None and away_block is not None
            else FieldQuality.PARTIAL.value
        ),
    )


def coaching_field(
    rows: list[dict[str, Any]] | None,
    home: str | None,
    away: str | None,
    *,
    observed_at: str,
) -> ContextField:
    """Who the coaches are, and how long they have been there. No tendencies.

    A "tendency" this repository could compute from play-by-play would be a
    derived behavioural claim, which is the predictive territory the live path
    stays out of. Tenure and name are registry facts.
    """
    if rows is None:
        return ContextField.missing(
            "coaching", "CFBD was not consulted (no API key)", "cfbd.coaches"
        )

    def find(team: str | None) -> dict[str, Any] | None:
        if not team:
            return None
        for row in rows:
            seasons = row.get("seasons") or []
            for season in seasons:
                if str(season.get("school") or "").strip().lower() == team.strip().lower():
                    return {
                        "name": f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip(),
                        "school": season.get("school"),
                        "year": season.get("year"),
                    }
        return None

    home_coach = find(home)
    away_coach = find(away)
    if home_coach is None and away_coach is None:
        return ContextField.missing(
            "coaching", "neither team appears in the CFBD coaches response", "cfbd.coaches"
        )
    return ContextField(
        domain="coaching",
        values={
            "home_coach": home_coach,
            "away_coach": away_coach,
            "tendencies": None,
            "tendencies_note": (
                "no behavioural tendency is derived here. A computed fourth-down or tempo "
                "tendency is a predictive claim, and the live path does not make them."
            ),
        },
        source="cfbd.coaches",
        observed_at=observed_at,
        quality=(
            FieldQuality.FRESH.value
            if home_coach and away_coach
            else FieldQuality.PARTIAL.value
        ),
    )
