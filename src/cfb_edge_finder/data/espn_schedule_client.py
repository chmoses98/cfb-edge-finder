"""ESPN SCHEDULE surface -- the free fallback source of FRESH kickoff /
status facts when CFBD is unavailable.

*** WHY A SECOND ESPN CLIENT AND NOT `data/espn_client.ESPNClient` ***
`ESPNClient` targets `site.api.espn.com`, which as of a live CI probe on
2026-09-03 (run 33789268655) answers **HTTP 403 "Access Denied"** (Akamai)
to GitHub-hosted runners for every scoreboard request. That host is what
the settlement fallback in `research/result_provider.py` uses; this
module deliberately does NOT touch that code path (settlement semantics
are out of scope here), but the finding is recorded in
docs/DATA_SOURCES.md because it means the settlement fallback is
currently unavailable-from-CI too -- it fails closed, so it produces no
wrong answers, but it produces no answers either.

*** WHAT IS ACTUALLY REACHABLE (live-verified 2026-09-03T18:15Z, run
    33789404748, `scripts/probe_espn_schedule_live.py`) ***
  site.api.espn.com/apis/site/v2/.../scoreboard        -> 403 Akamai
  site.web.api.espn.com/apis/site/v2/.../scoreboard    -> 200, top-level
                                                          "events"
  cdn.espn.com/core/college-football/scoreboard?xhr=1  -> 200, the same
                                                          event shape at
                                                          content.sbData.events
  sports.core.api.espn.com/v2/.../events               -> 200, but items
                                                          are $refs: one
                                                          call per event
                                                          plus further
                                                          $ref hops for
                                                          status and team
                                                          identity

The first two carry the ENTIRE event inline -- id, date, season, and
competitions[0] with date, neutralSite, status.type{id,name,state,
completed} and exactly two competitors tagged home/away with
team.location / team.displayName. That is byte-for-byte the shape
`research/result_provider.parse_espn_event` already parses, so this
module reuses that parser rather than writing a second one. The core API
is not used: 3-4 requests per game is the wrong shape for a 5-minute
loop, and it carries no field the scoreboard lacks.

Hosts are tried in order and the FIRST one that answers with a usable
payload wins; each host records its own outcome so an operator can see
which surface served (and which died) without reading a stack trace.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import requests

FBS_GROUP_ID = 80
"""ESPN's group id for FBS. Kept because the collector's universe is FBS
games, but note the scoreboard returns FBS games against FCS opponents
too (live-verified: "Arkansas-Pine Bluff Golden Lions at Missouri
Tigers" appeared under groups=80), which is exactly the coverage the
collector needs."""

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 4.0
"""Bounded like every other client in the repo, but SHORTER than
ESPNClient's: this runs inside a 5-minute deadline-critical loop where a
slow fallback is itself a risk. 1 initial + 2 retries, at most 1+2=3s
asleep per host."""

DEFAULT_TIMEOUT_SECONDS = 20.0

USER_AGENT = "cfb-edge-finder research collector (read-only schedule fallback)"
"""ESPN's edge rejects some default client UAs. A truthful,
non-impersonating identifier -- never a spoofed browser string."""


@dataclass(frozen=True)
class ScoreboardFetch:
    """One host attempt, recorded whether or not it worked. Failures are
    DATA here, not exceptions: the caller needs to report which surface
    served and fail closed on its own terms, not unwind on the first 403."""

    host: str
    url: str
    date_param: str
    http_status: int | None
    events: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.http_status == 200


class EspnScheduleClient:
    """Read-only, keyless, fail-soft scoreboard reader.

    Never raises for a provider problem: every failure is returned as a
    `ScoreboardFetch` carrying the reason, because the collector must
    decide fail-closed-per-game rather than abort a run that may have
    other work to do."""

    SITE_WEB_API = "site.web.api.espn.com"
    CDN = "cdn.espn.com"

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        hosts: tuple[str, ...] = (SITE_WEB_API, CDN),
        session: requests.Session | None = None,
    ):
        self._timeout_seconds = timeout_seconds
        self._hosts = hosts
        self._session = session or requests.Session()

    # ------------------------------------------------------------ hosts

    def _url_and_params(self, host: str, date_param: str) -> tuple[str, dict]:
        if host == self.SITE_WEB_API:
            return (
                "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
                {"groups": FBS_GROUP_ID, "dates": date_param, "limit": 400},
            )
        if host == self.CDN:
            return (
                "https://cdn.espn.com/core/college-football/scoreboard",
                {"xhr": 1, "groups": FBS_GROUP_ID, "dates": date_param, "limit": 400},
            )
        raise ValueError(f"unsupported ESPN schedule host {host!r}")

    @staticmethod
    def _events_from(host: str, payload: object) -> list[dict] | None:
        """Both reachable hosts carry the SAME event objects; only the
        envelope differs. Returns None when the envelope is not the shape
        this module verified -- never a partial guess."""
        if not isinstance(payload, dict):
            return None
        if host == EspnScheduleClient.SITE_WEB_API:
            events = payload.get("events")
        else:
            events = ((payload.get("content") or {}).get("sbData") or {}).get("events")
        if not isinstance(events, list):
            return None
        return [e for e in events if isinstance(e, dict)]

    # ------------------------------------------------------------ fetch

    def fetch_scoreboard(self, date_param: str) -> ScoreboardFetch:
        """One date bucket (`YYYYMMDD`, or ESPN's `YYYYMMDD-YYYYMMDD`
        range form) from the first host that answers usefully."""
        last: ScoreboardFetch | None = None
        for host in self._hosts:
            url, params = self._url_and_params(host, date_param)
            attempt_result = self._fetch_one_host(host, url, params, date_param)
            if attempt_result.ok:
                return attempt_result
            last = attempt_result
        return last or ScoreboardFetch(
            host="none", url="", date_param=date_param, http_status=None, error="no hosts configured"
        )

    def _fetch_one_host(self, host: str, url: str, params: dict, date_param: str) -> ScoreboardFetch:
        status: int | None = None
        error: str | None = None
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = self._session.get(
                    url, params=params, timeout=self._timeout_seconds, headers={"User-Agent": USER_AGENT}
                )
            except requests.RequestException as exc:
                error = f"{type(exc).__name__}: {exc}"
                status = None
            else:
                status = response.status_code
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        error = f"unparsable JSON: {exc}"
                    else:
                        events = self._events_from(host, payload)
                        if events is None:
                            error = "payload did not carry the verified scoreboard envelope"
                        else:
                            return ScoreboardFetch(
                                host=host, url=url, date_param=date_param, http_status=200, events=events
                            )
                else:
                    error = f"HTTP {response.status_code}"
                    # A 403 is a policy answer, not a transient one: retrying
                    # the same blocked host wastes the deadline budget.
                    if response.status_code in (401, 403, 404):
                        break
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(min(RETRY_BASE_DELAY_SECONDS * (2**attempt), RETRY_MAX_DELAY_SECONDS))
        return ScoreboardFetch(host=host, url=url, date_param=date_param, http_status=status, error=error)


def date_buckets_for(
    kickoffs: list[datetime], *, now: datetime, horizon_hours: float, lookback_hours: float = 24.0
) -> list[str]:
    """The `YYYYMMDD` buckets that can contain the given kickoffs.

    ESPN's `dates=` buckets by US LOCAL date (live-verified in
    `result_provider.scoreboard_dates_for`: a 2026-08-30T02:00Z kickoff
    sat in the 20260829 bucket). US local dates never run AHEAD of UTC,
    so a kickoff's own UTC date plus the day before covers every bucket
    it can be in. The lookback also keeps just-started games visible so
    a status change (in_progress/final) is seen rather than inferred."""
    lo = now - timedelta(hours=lookback_hours)
    hi = now + timedelta(hours=horizon_hours)
    buckets: set[str] = set()
    for kickoff in kickoffs:
        if kickoff is None:
            continue
        if kickoff < lo or kickoff > hi:
            continue
        day = kickoff.astimezone(UTC).date()
        buckets.add(day.strftime("%Y%m%d"))
        buckets.add((day - timedelta(days=1)).strftime("%Y%m%d"))
    return sorted(buckets)
