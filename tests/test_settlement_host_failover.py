"""Settlement must survive its result host being blocked.

*** THE PROBLEM (live, 2026-09-03) ***
Two independent outages overlapped:

  CFBD  quota exhausted (remaining=0 until 2026-10-01) -- primary gone
  ESPN  site.api.espn.com began answering HTTP 403 "Access Denied"
        (Akamai) to GitHub-hosted runners -- the ONLY host the settlement
        fallback knew about

The fallback failed closed, which is correct and produced no wrong
answers -- but it also produced no answers, so completed games could not
settle at all. A live probe over 11 days (runs 33808382040 and
33808502653) found two other hosts serving the IDENTICAL payload with the
real settlement parsers accepting their finals and failing closed on
nothing:

  site.api.espn.com       0/11 days reachable
  site.web.api.espn.com  11/11 days,  8 settleable finals, 0 fail-closed
  cdn.espn.com           11/11 days, 11 settleable finals, 0 fail-closed

The fix is a TRANSPORT change only. Identity matching, the three-fold
finality requirement, score parsing and every fail-closed rule are
untouched -- these tests pin that they still reject exactly what they
rejected before, whichever host served.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))

from cfb_edge_finder.data.espn_client import SCOREBOARD_HOSTS, ESPNClient, _events_from  # noqa: E402
from cfb_edge_finder.research.result_provider import (  # noqa: E402
    espn_game_result,
    parse_espn_event,
)
from cfb_edge_finder.schemas.settlement import GameFinalStatus  # noqa: E402

NOW = datetime(2026, 9, 3, 21, 0, tzinfo=UTC)


# ---------------------------------------------------------------- fixtures


def _final_event(home="Alabama", away="Arkansas", home_pts="31", away_pts="17", event_id="401"):
    """One completed event in the shape live-verified on every reachable
    host: identical `competitions[0]` regardless of envelope."""
    return {
        "id": event_id,
        "date": "2026-09-03T22:00Z",
        "name": f"{away} at {home}",
        "season": {"year": 2026, "type": 2},
        "competitions": [
            {
                "id": event_id,
                "date": "2026-09-03T22:00Z",
                "status": {
                    "type": {
                        "id": "3",
                        "name": "STATUS_FINAL",
                        "state": "post",
                        "completed": True,
                        "detail": "Final",
                    }
                },
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": home_pts,
                        "winner": int(home_pts) > int(away_pts),
                        "team": {"location": home, "displayName": home},
                        "linescores": [{"value": 7}, {"value": 7}, {"value": 7}, {"value": 10}],
                    },
                    {
                        "homeAway": "away",
                        "score": away_pts,
                        "winner": int(away_pts) > int(home_pts),
                        "team": {"location": away, "displayName": away},
                        "linescores": [{"value": 3}, {"value": 7}, {"value": 0}, {"value": 7}],
                    },
                ],
            }
        ],
    }


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def _install(monkeypatch, by_host):
    """Map host substring -> response. Records the call order so the
    tests can assert WHICH hosts were tried and in what order."""
    from cfb_edge_finder.data import espn_client as ec

    calls: list[str] = []

    def fake_get(url, params=None, timeout=None, headers=None):
        calls.append(url)
        for fragment, response in by_host.items():
            if fragment in url:
                return response
        raise AssertionError(f"unexpected host in {url}")

    monkeypatch.setattr(ec.requests, "get", fake_get)
    monkeypatch.setattr(ec.time, "sleep", lambda _s: None)
    return calls


# ============================================================ test matrix 3
# The blocked host must not stop settlement.


def test_blocked_primary_host_falls_through_to_the_next(monkeypatch):
    """Test matrix 3: site.api 403 -> the alternate path works."""
    calls = _install(
        monkeypatch,
        {
            "site.api.espn.com": _Resp(403),
            "site.web.api.espn.com": _Resp(200, {"events": [_final_event()]}),
        },
    )
    client = ESPNClient()
    body = client.fetch_scoreboard("20260903")

    assert body["events"][0]["id"] == "401"
    assert client.last_host == "site.web.api.espn.com"
    assert len(calls) == 2, "one wasted call on the blocked host, then straight through"


def test_falls_all_the_way_to_the_cdn_envelope(monkeypatch):
    """The cdn host wraps the same events at content.sbData.events. The
    client must normalise that away so every caller downstream -- and
    every fail-closed rule -- is unchanged."""
    _install(
        monkeypatch,
        {
            "site.api.espn.com": _Resp(403),
            "site.web.api.espn.com": _Resp(500),
            "cdn.espn.com": _Resp(200, {"content": {"sbData": {"events": [_final_event()]}}}),
        },
    )
    client = ESPNClient()
    body = client.fetch_scoreboard("20260903")
    assert body == {"events": [_final_event()]}
    assert client.last_host == "cdn.espn.com"


def test_site_api_is_still_tried_first_so_recovery_needs_no_code_change(monkeypatch):
    calls = _install(monkeypatch, {"site.api.espn.com": _Resp(200, {"events": []})})
    client = ESPNClient()
    client.fetch_scoreboard("20260903")
    assert len(calls) == 1
    assert client.last_host == "site.api.espn.com"
    assert SCOREBOARD_HOSTS[0][0] == "site.api.espn.com"


# ============================================================ test matrix 4
# Both free fallbacks unavailable -> fail closed.


def test_every_host_dead_raises_so_the_caller_settles_nothing(monkeypatch):
    _install(
        monkeypatch,
        {
            "site.api.espn.com": _Resp(403),
            "site.web.api.espn.com": _Resp(503),
            "cdn.espn.com": _Resp(503),
        },
    )
    with pytest.raises(requests.HTTPError):
        ESPNClient().fetch_scoreboard("20260903")


def test_a_200_with_the_wrong_envelope_is_not_accepted(monkeypatch):
    """A host that answers 200 with something that is not the verified
    scoreboard shape must be treated as unusable, not as an empty slate.
    An empty slate would silently mean 'nothing to settle'."""
    _install(
        monkeypatch,
        {
            "site.api.espn.com": _Resp(200, {"unexpected": "shape"}),
            "site.web.api.espn.com": _Resp(200, {"events": [_final_event()]}),
        },
    )
    client = ESPNClient()
    body = client.fetch_scoreboard("20260903")
    assert len(body["events"]) == 1
    assert client.last_host == "site.web.api.espn.com"


def test_envelope_reader_rejects_non_lists():
    assert _events_from("site.web.api.espn.com", {"events": "nope"}) is None
    assert _events_from("cdn.espn.com", {"content": {}}) is None
    assert _events_from("site.web.api.espn.com", "not a dict") is None


# ================================================ test matrix 5, 6, 7, 9
# Whichever host served, the settlement rules are unchanged.


def test_identical_result_from_every_host(monkeypatch):
    """The whole safety argument for host failover: the payload is the
    same, so the parsed GameResult is the same. If these ever diverge,
    the failover is not transport-only any more."""
    raw = _final_event()
    results = []
    for envelope in ({"events": [raw]}, {"content": {"sbData": {"events": [raw]}}}):
        host = "site.web.api.espn.com" if "events" in envelope else "cdn.espn.com"
        events = _events_from(host, envelope)
        assert events is not None
        facts = parse_espn_event(events[0])
        result, reason = espn_game_result(
            facts, game_id="g", season=2026, now=NOW, fallback_reason="probe"
        )
        assert reason is None
        results.append(result)

    a, b = results
    assert a.home_points == b.home_points == 31
    assert a.away_points == b.away_points == 17
    assert a.status is b.status is GameFinalStatus.FINAL
    assert a.went_to_overtime is b.went_to_overtime is False
    assert a.source_game_id == b.source_game_id == "401"


def test_non_final_never_settles_whichever_host_served():
    """Test matrix 7."""
    raw = _final_event()
    raw["competitions"][0]["status"]["type"] = {
        "id": "1",
        "name": "STATUS_SCHEDULED",
        "state": "pre",
        "completed": False,
        "detail": "Scheduled",
    }
    facts = parse_espn_event(raw)
    result, reason = espn_game_result(facts, game_id="g", season=2026, now=NOW, fallback_reason="probe")
    assert reason is None
    assert result.status is GameFinalStatus.NOT_YET_FINAL
    assert result.home_points is None


def test_contradictory_finality_fails_closed():
    """Partial finality evidence must never settle -- we cannot tell
    which half of the contradiction is true."""
    raw = _final_event()
    raw["competitions"][0]["status"]["type"]["state"] = "in"
    facts = parse_espn_event(raw)
    result, reason = espn_game_result(facts, game_id="g", season=2026, now=NOW, fallback_reason="probe")
    assert result is None
    assert "contradictory" in reason


def test_unparseable_score_on_a_final_fails_closed():
    raw = _final_event()
    raw["competitions"][0]["competitors"][0]["score"] = "TBD"
    facts = parse_espn_event(raw)
    result, reason = espn_game_result(facts, game_id="g", season=2026, now=NOW, fallback_reason="probe")
    assert result is None
    assert "unparseable score" in reason


def test_winner_flags_contradicting_scores_fail_closed():
    raw = _final_event()
    raw["competitions"][0]["competitors"][0]["winner"] = False
    raw["competitions"][0]["competitors"][1]["winner"] = True
    facts = parse_espn_event(raw)
    result, reason = espn_game_result(facts, game_id="g", season=2026, now=NOW, fallback_reason="probe")
    assert result is None
    assert "contradict scores" in reason


def test_unresolvable_team_makes_the_event_a_non_candidate():
    """Test matrix 5: settlement keeps the STRICTER identity policy --
    both sides must resolve through the registry. A settlement row
    records a score, and a wrong identity there corrupts a settled
    result, so the slug relaxation used by the SCHEDULE fallback is
    deliberately not applied here."""
    raw = _final_event(away="Not A Real School XYZ")
    facts = parse_espn_event(raw)
    assert facts.resolution_error is not None
    assert facts.home_team_id is None and facts.away_team_id is None
