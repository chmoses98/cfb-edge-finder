"""ESPN unofficial/hidden scoreboard API -- fallback/cross-check source.

STATUS: same caveat as cfbd_client.py -- not independently live-verified
this session (network egress blocked), built from ESPN's well-documented
hidden-endpoint shape as used throughout the open-source CFB analytics
community. This is explicitly a FALLBACK per docs/DATA_SOURCES.md: no
authentication, but also no ToS grant and no stability guarantee. Used
for cross-checking dates/venue/neutral-site, never as the sole source of
truth -- see cfb_edge_finder.ingestion.reconciliation.
"""

from __future__ import annotations

import requests

DEFAULT_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
FBS_GROUP_ID = 80


class ESPNClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout_seconds: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def fetch_scoreboard(self, date_yyyymmdd: str, group_id: int = FBS_GROUP_ID) -> dict:
        """Raw ESPN scoreboard response for one calendar date. Shape (not
        independently verified this session): a dict with an "events" list,
        each event carrying id, date, name, competitions[0].venue,
        competitions[0].neutralSite, and competitions[0].competitors
        (each tagged homeAway: "home"/"away").
        """
        response = requests.get(
            f"{self._base_url}/scoreboard",
            params={"groups": group_id, "dates": date_yyyymmdd},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
