"""ESPN unofficial/hidden scoreboard API -- fallback/cross-check source.

STATUS: LIVE-VERIFIED 2026-08-30 against real responses (GitHub Actions
run 33330066488, `scripts/validate_espn_results_live.py`, dates
20260829/20260830) after CFBD's sustained HTTP 429 quota outage made a
settlement-result fallback necessary. What that probe established, from
genuine payloads rather than community folklore:

  * Each event: id (string numeric), date ("2026-08-29T19:00Z" -- ISO to
    the minute, Z suffix), name, season.year, week.number, and
    competitions[0] carrying date, neutralSite, venue, status, and
    exactly two competitors tagged homeAway "home"/"away".
  * Finality is explicit and three-fold on competitions[0].status.type:
    a completed game showed id="3", name="STATUS_FINAL", state="post",
    completed=true, detail/shortDetail "Final" on every one of the 8
    events for the completed 2026-08-29 slate.
  * competitor.score is a STRING ("42"); competitor.winner is a bool;
    competitor.team carries id/location/name/displayName/abbreviation,
    with location matching CFBD school naming including unicode forms
    ("San José State", "Hawai'i").
  * `dates=YYYYMMDD` buckets by US LOCAL date, not UTC: the 20260829
    scoreboard contained Memphis@UNLV (kickoff 2026-08-30T02:00Z) while
    20260830 returned zero events. Callers must query the prior day too.

This is explicitly a FALLBACK per docs/DATA_SOURCES.md: no
authentication, but also no ToS grant and no stability guarantee. Used
for result/identity cross-checking under strict fail-closed validation
(see research/result_provider.py), never as an override of a valid
primary-source answer.
"""

from __future__ import annotations

import time

import requests

DEFAULT_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
FBS_GROUP_ID = 80

RETRY_ATTEMPTS = 4
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 8.0
"""Same bounded transient-retry schedule as CFBDClient/KalshiClient
(1 initial + 3 retries, at most 1+2+4=7s asleep): a one-off 5xx on a
keyless CDN-backed endpoint must cost seconds, not a settlement run."""


class ESPNClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def fetch_scoreboard(self, date_yyyymmdd: str, group_id: int = FBS_GROUP_ID) -> dict:
        """Raw ESPN scoreboard response for one calendar date (US-local
        bucketing -- see module docstring). Shape live-verified 2026-08-30:
        a dict with an "events" list as documented above.
        """
        last_error: requests.HTTPError | None = None
        for attempt in range(RETRY_ATTEMPTS):
            response = requests.get(
                f"{self._base_url}/scoreboard",
                params={"groups": group_id, "dates": date_yyyymmdd},
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
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), RETRY_MAX_DELAY_SECONDS)
            except ValueError:
                pass
        return min(RETRY_BASE_DELAY_SECONDS * (2**attempt), RETRY_MAX_DELAY_SECONDS)
