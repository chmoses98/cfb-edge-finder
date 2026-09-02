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

import time

import requests

DEFAULT_BASE_URL = "https://api.collegefootballdata.com"

RETRY_ATTEMPTS = 4
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 8.0
"""Bounded retry on genuinely transient statuses (429 and 5xx), mirroring
KalshiClient's schedule: 1 initial + 3 retries, at most 1+2+4=7s asleep.

*** LIVE EVIDENCE THIS IS BUILT FROM (run 33211233986, 2026-08-28) ***
A one-off CFBD `502 Bad Gateway` on GET /games killed an entire
collection-workflow run on the first request, with no retry -- the same
failure mode KalshiClient's own docstring records for its live 429
(job 98618136387). Ahead of a 14-minute closing window a transient 502
must cost seconds, not the whole capture. A 4xx that is not 429 is a
real client error and is still raised immediately."""


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
        last_error: requests.HTTPError | None = None
        for attempt in range(RETRY_ATTEMPTS):
            response = requests.get(
                f"{self._base_url}{path}",
                params={k: v for k, v in params.items() if v is not None},
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                timeout=self._timeout_seconds,
            )
            if response.status_code < 400:
                return response.json()

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                last_error = exc

            retryable = response.status_code == 429 or 500 <= response.status_code < 600
            if not retryable or attempt == RETRY_ATTEMPTS - 1:
                assert last_error is not None
                raise last_error

            time.sleep(self._retry_delay_seconds(response, attempt))

        assert last_error is not None  # loop always raises or returns
        raise last_error

    @staticmethod
    def _retry_delay_seconds(response: requests.Response, attempt: int) -> float:
        """Server-provided `Retry-After` wins over our own backoff, capped
        so a malformed header cannot stall a scheduled run -- same policy
        as KalshiClient._retry_delay_seconds."""
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), RETRY_MAX_DELAY_SECONDS)
            except ValueError:
                pass
        return min(RETRY_BASE_DELAY_SECONDS * (2**attempt), RETRY_MAX_DELAY_SECONDS)

    def fetch_raw(self, path: str, params: dict[str, object] | None = None) -> list[dict] | dict:
        """Generic authenticated GET for research acquisition scripts
        (scripts/fetch_v2_research_cache.py). Same retry/auth discipline
        as every typed fetcher; the caller owns endpoint/param semantics
        and must record timing/leakage classification for whatever it
        pulls. Never logs the key."""
        return self._get(path, dict(params or {}))

    def fetch_account_info(self) -> dict:
        """Raw CFBD GET /info response -- the authenticated account/quota
        surface. LIVE-VERIFIED 2026-08-31 during the real quota outage
        (run 33349348575): returns HTTP 200 even while every metered
        endpoint is 429, with shape {patronLevel, tierName, monthlyLimit,
        remainingCalls, usedCalls, resetAt, sharedPool, products,
        features}; resetAt is the first of the NEXT calendar month at
        00:00:00 UTC (confirmed against the public server source,
        github.com/CFBD/cfb-api-v2 src/app/info/service.ts).

        UNMETERED: /info sits in the quota middleware's ignoredPaths
        (server source src/config/middleware/quotas.ts), and the live run
        proved remainingCalls does not decrease across consecutive calls
        -- so this is safe to use as the quota-recovery probe without
        spending quota."""
        result = self._get("/info", {})
        if not isinstance(result, dict):
            raise ValueError(f"unexpected /info response shape: {type(result).__name__}")
        return result

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
        """Raw CFBD /teams/fbs response (FBS ONLY): id, school, mascot,
        abbreviation, conference, classification, and similar metadata
        fields.
        """
        return self._get("/teams/fbs", {"year": season})

    def fetch_all_division_teams(self, season: int | None = None) -> list[dict]:
        """Raw CFBD GET /teams response -- covers ALL divisions (FBS AND
        FCS), unlike `fetch_teams()` above which is deliberately pinned
        to the FBS-only /teams/fbs endpoint (per CFBD/cfbd-python's own
        TeamsApi docs: get_teams -> GET /teams spans both divisions,
        get_fbs_teams -> GET /teams/fbs is FBS-only). Used ONLY for a
        minimal, deterministic team-name -> classification identity
        lookup (see teams/fcs_identity.py) so a genuine FCS-vs-FCS Kalshi
        market can be classified as a distinct, understood
        MAPPED_UNSUPPORTED_POPULATION-family outcome instead of an
        unexplained parse/ambiguity failure -- NOT to ingest or model the
        FCS statistical universe (this codebase's predictive model
        remains FBS-only). Same dict shape as fetch_teams(): id, school,
        mascot, abbreviation, conference, classification, and similar
        metadata fields; `classification` here can be "fcs" (and other
        divisions) in addition to "fbs"."""
        return self._get("/teams", {"year": season})

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

    def fetch_talent(self, season: int) -> list[dict]:
        """Raw CFBD GET /talent response -- team recruiting-talent composite
        for a season.

        PRESEASON-SAFE TIMING: the composite for season S is settled by the
        S-1 signing cycle and published before S begins, so it is genuine
        preseason information. Milestone C's data audit classified it
        available and leakage-safe but deliberately left it unwired,
        to avoid piling features onto an unvalidated baseline.

        Shape (per primary-source docs): year, school, talent. NOT
        live-verified before this milestone -- the first fetch records a
        schema fingerprint so a shape change is detectable rather than
        silently mis-parsed.
        """
        return self._get("/talent", {"year": season})

    def fetch_coaches(self, season: int) -> list[dict]:
        """Raw CFBD GET /coaches response -- coaching records by season.

        PRESEASON-SAFE TIMING: a hire is public before the season starts,
        and the endpoint is season-scoped, so comparing season S against
        S-1 identifies a head-coach change using only information that
        predates S.

        Shape (per primary-source docs): first_name, last_name, and a
        nested `seasons` list carrying school/year/games and outcome
        stats. ONLY the identity/school/year fields are preseason-safe --
        the per-season win/loss and ranking fields inside `seasons` are
        POSTGAME for their own season and must never be read as preseason
        information. Milestone C listed /coaches as not separately
        audited, so the schema is fingerprinted on first fetch.
        """
        return self._get("/coaches", {"year": season})

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
