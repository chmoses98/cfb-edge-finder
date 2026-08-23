"""CollegeFootballData.com (CFBD) REST API v2 client -- primary schedule/team source.

STATUS: this client's request-building/response-shape assumptions were
NOT independently live-verified this session -- this environment's network
egress to api.collegefootballdata.com and collegefootballdata.com is
blocked (see docs/DATA_SOURCES.md, docs/MILESTONE_B.md). They are built
from CFBD's well-documented, community-standard REST v2 schema (the same
one cfbfastR/cfbd Python clients target), not fabricated. `tests/` cover
this module with recorded/fixture responses only -- there is no live
integration test, and none of this module's docstrings claim one.

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
