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

# *** HOST FAILOVER (live-verified 2026-09-03, runs 33808382040 / 33808502653) ***
# `site.api.espn.com` began answering HTTP 403 "Access Denied" (Akamai)
# to GitHub-hosted runners. Because CFBD is simultaneously
# quota-exhausted, that left settlement with NO reachable result source:
# it failed closed correctly, but completed games could not settle at
# all. Two other hosts serve the IDENTICAL scoreboard payload and were
# reachable on 11 of 11 probed days, with the real settlement parsers
# accepting their finals and failing closed on nothing:
#
#   site.api.espn.com       0/11 days reachable  (403 every day)
#   site.web.api.espn.com  11/11 days,  8 settleable finals, 0 fail-closed
#   cdn.espn.com           11/11 days, 11 settleable finals, 0 fail-closed
#
# Order matters. `site.api` stays FIRST so the moment ESPN restores it
# the original host is used again with no code change. `site.web.api` is
# second because it honours `dates=` precisely (per-day slates), which is
# what settlement queries by. `cdn.espn.com` is a week-oriented view --
# it returns a superset and is therefore a sound last resort, but a
# worse first choice.
#
# This is a TRANSPORT change only: the payload shape, the identity
# matching, the three-fold finality requirement and every fail-closed
# rule in research/result_provider.py are untouched.
SCOREBOARD_HOSTS: tuple[tuple[str, str], ...] = (
    ("site.api.espn.com", "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"),
    (
        "site.web.api.espn.com",
        "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
    ),
    ("cdn.espn.com", "https://cdn.espn.com/core/college-football/scoreboard"),
)

_CDN_HOST = "cdn.espn.com"

USER_AGENT = "cfb-edge-finder research settlement (read-only result fallback)"
"""A truthful, non-impersonating identifier -- never a spoofed browser
string, and never an attempt to defeat the 403 on the blocked host."""

NON_RETRYABLE_STATUSES = frozenset({401, 403, 404})
"""A policy answer, not a transient one: retrying a blocked host wastes
the settlement window. Move to the next host instead."""

RETRY_ATTEMPTS = 4
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 8.0
"""Same bounded transient-retry schedule as CFBDClient/KalshiClient
(1 initial + 3 retries, at most 1+2+4=7s asleep): a one-off 5xx on a
keyless CDN-backed endpoint must cost seconds, not a settlement run."""


def _events_from(host: str, payload: object) -> list | None:
    """Both alternate hosts carry the SAME event objects; only the
    envelope differs (`events` at the top level vs
    `content.sbData.events`). Returns None when the envelope is not the
    shape live verification established -- never a partial guess."""
    if not isinstance(payload, dict):
        return None
    events = ((payload.get("content") or {}).get("sbData") or {}).get("events") if host == _CDN_HOST else payload.get("events")
    if not isinstance(events, list):
        return None
    return [e for e in events if isinstance(e, dict)]


class ESPNClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
        hosts: tuple[tuple[str, str], ...] = SCOREBOARD_HOSTS,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._hosts = hosts
        self.last_host: str | None = None
        """Which host actually served the most recent successful fetch --
        persisted as settlement provenance so an audit can tell a
        site.api-sourced result from a cdn-sourced one."""

    def fetch_scoreboard(self, date_yyyymmdd: str, group_id: int = FBS_GROUP_ID) -> dict:
        """Raw ESPN scoreboard response for one calendar date (US-local
        bucketing -- see module docstring), from the first host that
        answers with the verified `events` envelope.

        Always returns the SAME shape -- `{"events": [...]}` -- whichever
        host served, so every caller downstream is unchanged. The serving
        host is recorded on `last_host` for provenance; a run that
        exhausts every host raises the last error exactly as before, so
        `research/result_provider.py` still fails closed and settles
        nothing.
        """
        last_error: Exception | None = None
        for host, url in self._hosts:
            events, error = self._fetch_host(host, url, date_yyyymmdd, group_id)
            if events is not None:
                self.last_host = host
                return {"events": events}
            last_error = error
        assert last_error is not None
        raise last_error

    def _fetch_host(
        self, host: str, url: str, date_yyyymmdd: str, group_id: int
    ) -> tuple[list | None, Exception | None]:
        params: dict = {"groups": group_id, "dates": date_yyyymmdd, "limit": 400}
        if host == _CDN_HOST:
            params["xhr"] = 1
        last_error: Exception | None = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = requests.get(
                    url, params=params, timeout=self._timeout_seconds, headers={"User-Agent": USER_AGENT}
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt == RETRY_ATTEMPTS - 1:
                    return None, exc
                time.sleep(min(RETRY_BASE_DELAY_SECONDS * (2**attempt), RETRY_MAX_DELAY_SECONDS))
                continue

            if response.status_code < 400:
                try:
                    payload = response.json()
                except ValueError as exc:
                    return None, requests.HTTPError(f"{host}: unparsable JSON: {exc}", response=response)
                events = _events_from(host, payload)
                if events is None:
                    return None, requests.HTTPError(
                        f"{host}: payload did not carry the verified scoreboard envelope", response=response
                    )
                return events, None

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                last_error = exc

            retryable = response.status_code not in NON_RETRYABLE_STATUSES and (
                response.status_code == 429 or 500 <= response.status_code < 600
            )
            if not retryable or attempt == RETRY_ATTEMPTS - 1:
                return None, last_error

            time.sleep(self._retry_delay_seconds(response, attempt))
        return None, last_error

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
