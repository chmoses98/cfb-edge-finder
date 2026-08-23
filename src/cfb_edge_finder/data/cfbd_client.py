"""CollegeFootballData.com (CFBD) REST API v2 client -- primary schedule/team source.

STATUS: `fetch_games`/`fetch_teams` were live-verified twice against real,
authenticated CFBD responses via a GitHub Actions runner in Milestone B
(this dev environment's own network egress to api.collegefootballdata.com
stays blocked -- see docs/MILESTONE_B.md's "Live validation" section for
the diagnostic output). The three methods added in Milestone C
(`fetch_advanced_team_game_stats`, `fetch_returning_production`,
`fetch_lines`) are built from genuine primary-source documentation
(github.com/CFBD/cfbd-python, reachable from this environment even though
CFBD's own domains are not -- see docs/MILESTONE_C.md's data audit) but
have NOT yet been live-verified the same way; that is called out
explicitly wherever this repo reports results derived from them.

Auth: Bearer token via CFBD_API_KEY (see cfb_edge_finder.config.Settings).
Free tier reported ~1,000 calls/month (see docs/DATA_SOURCES.md).
"""

from __future__ import annotations

import requests

DEFAULT_BASE_URL = "https://api.collegefootballdata.com"


class CFBDAuthError(RuntimeError):
    """Raised when no API key is configured. Distinct from a network/HTTP
    error so callers (and the ingestion CLI) can cleanly fall back to
    fixture mode rather than treating "no key" the same as "API is down".
    """


class CFBDClient:
    def __init__(self, api_key: str | None, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = 30.0):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def _require_key(self) -> str:
        if not self._api_key:
            raise CFBDAuthError(
                "CFBD_API_KEY is not set. This client will not attempt an unauthenticated request -- "
                "CFBD's /games and /teams endpoints require a Bearer token for every tier."
            )
        return self._api_key

    def _get(self, path: str, params: dict[str, object]) -> list[dict]:
        api_key = self._require_key()
        response = requests.get(
            f"{self._base_url}{path}",
            params={k: v for k, v in params.items() if v is not None},
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def fetch_games(
        self, season: int, season_type: str | None = None, division: str = "fbs", week: int | None = None
    ) -> list[dict]:
        """Raw CFBD /games response: a list of game dicts. Shape (per
        CFBD's public v2 schema, not independently verified this session):
        id, season, week, seasonType, startDate, startTimeTBD, neutralSite,
        conferenceGame, venueId, venue, homeId, homeTeam, homeConference,
        homeClassification, awayId, awayTeam, awayConference,
        awayClassification, and score/status fields once played.
        """
        return self._get(
            "/games", {"year": season, "seasonType": season_type, "division": division, "week": week}
        )

    def fetch_teams(self, season: int | None = None) -> list[dict]:
        """Raw CFBD /teams response: id, school, mascot, abbreviation,
        conference, classification, and similar metadata fields.
        """
        return self._get("/teams/fbs", {"year": season})

    def fetch_advanced_team_game_stats(
        self, season: int, week: int | None = None, team: str | None = None, exclude_garbage_time: bool = False
    ) -> list[dict]:
        """Raw CFBD GET /stats/game/advanced response -- per-team-per-game
        advanced metrics, confirmed via genuine primary-source docs
        (github.com/CFBD/cfbd-python, since CFBD's own domains are blocked
        from this environment -- see docs/MILESTONE_C.md's data audit).
        Shape: game_id, season, week, team, opponent, and nested
        offense/defense objects each carrying ppa, success_rate,
        explosiveness, plays, drives, and situational-down splits. This is
        Milestone C's only source for `plays` (the pace signal) -- the
        plain (non-advanced) /games/teams endpoint does not expose it.
        """
        return self._get(
            "/stats/game/advanced",
            {"year": season, "week": week, "team": team, "excludeGarbageTime": exclude_garbage_time},
        )

    def fetch_returning_production(self, season: int, team: str | None = None) -> list[dict]:
        """Raw CFBD GET /player/returning response -- team-level returning-
        production metrics, published before the season starts (a genuine
        preseason-available signal, not leakage). Shape: season, team,
        conference, total_ppa/percent_ppa and passing/receiving/rushing
        splits, usage/passing_usage/etc. No field directly identifies
        "is the starting QB returning" -- percent_passing_ppa and
        passing_usage are used as a documented PROXY for passing-game
        (and by extension, likely QB) continuity, not a direct QB-identity
        signal. See docs/MILESTONE_C.md "QB continuity."
        """
        return self._get("/player/returning", {"year": season, "team": team})

    def fetch_lines(self, season: int, week: int | None = None, season_type: str | None = None) -> list[dict]:
        """Raw CFBD GET /lines response -- historical closing betting lines
        by provider. EVALUATION-ONLY in this codebase: used to compare the
        model's own independently-derived probabilities against a real
        historical market baseline, never as a model input feature (using
        a closing line to predict its own game's outcome would not be an
        independent forecast). Shape: id, season, week, season_type,
        home/away team+score, and a nested `lines` list of
        {provider, spread, spread_open, over_under, over_under_open,
        home_moneyline, away_moneyline}.
        """
        return self._get("/lines", {"year": season, "week": week, "seasonType": season_type})
