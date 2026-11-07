"""Source parsers: ESPN venue/weather/roster lookups and the corrected injury-reference resolution."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_football_gamelog as cg  # noqa: E402

from cfb_edge_finder.research_packet import espn_extras as ex  # noqa: E402


def test_summary_extras_carry_venue_weather_and_the_separate_predictor():
    summary = {
        "gameInfo": {
            "venue": {"fullName": "Mackay Stadium", "address": {"city": "Reno", "state": "NV"}, "grass": False},
            "weather": {
                "temperature": 52,
                "highTemperature": 52,
                "lowTemperature": 50,
                "conditionId": "Mostly clear",
                "gust": 7,
                "precipitation": 0,
            },
        },
        "predictor": {
            "homeTeam": {"id": "2440", "gameProjection": "18.5"},
            "awayTeam": {"id": "167", "gameProjection": "81.5"},
        },
        "pickcenter": [{"spread": -7.5}],
        "odds": [{"overUnder": 47.5}],
    }
    out = ex.parse_event_extras(summary, observed_at="2026-11-06T00:00:00Z")
    assert (
        out["venue"]["city"] == "Reno"
        and out["weather"]["status"] == "FORECAST"
        and out["weather"]["wind_gust_mph"] == 7
    )
    assert (
        out["espn_predictor"]["evidence_tier"] == "EXTERNAL_MODEL_REFERENCE"
        and out["espn_predictor"]["home_win_pct"] == 18.5
    )
    assert "pickcenter" not in str(out) and "overUnder" not in str(out) and "spread" not in str(out), (
        "market lines must never be read"
    )


def test_missing_weather_is_unavailable_with_a_reason_not_a_default():
    out = ex.parse_event_extras({"gameInfo": {"venue": {"fullName": "X"}}}, observed_at="t")
    assert out["weather"]["status"] == "UNAVAILABLE" and out["weather"]["reason"]
    assert out["espn_predictor"]["status"] == "UNAVAILABLE"


def test_roster_lists_qbs_coach_and_flagged_athletes_and_says_empty_is_not_health():
    payload = {
        "coach": [{"firstName": "Jeff", "lastName": "Choate", "experience": 4}],
        "athletes": [
            {
                "position": "offense",
                "items": [
                    {
                        "displayName": "AJ Bianco",
                        "position": {"abbreviation": "QB"},
                        "jersey": "10",
                        "experience": {"displayValue": "Senior"},
                        "status": {"name": "Active"},
                    },
                    {
                        "displayName": "Hurt Guy",
                        "position": {"abbreviation": "OL"},
                        "status": {"name": "Active"},
                        "injuries": [{"status": "Out", "details": {"detail": "knee"}}],
                    },
                ],
            },
            {
                "position": "injuredReserveOrOut",
                "items": [
                    {"displayName": "Gone Guy", "position": {"abbreviation": "WR"}, "status": {"name": "Suspended"}}
                ],
            },
        ],
    }
    out = ex.parse_roster(payload, observed_at="t")
    assert out["head_coach"]["name"] == "Jeff Choate" and [q["name"] for q in out["quarterbacks"]] == ["AJ Bianco"]
    assert {a["name"] for a in out["flagged_athletes"]} == {"Hurt Guy", "Gone Guy"}
    assert "NOT evidence that the team is healthy" in out["flagged_meaning"]


def test_injury_refs_are_followed_two_hops_and_unresolvable_rows_are_marked(monkeypatch):
    responses = {
        "https://core/inj/1": {"status": "Out", "date": "2026-11-05T00:00Z", "athlete": {"$ref": "http://core/ath/9"}},
        "https://core/ath/9": {"displayName": "Starting QB", "position": {"abbreviation": "QB"}},
    }
    monkeypatch.setattr(
        cg.transport,
        "_get",
        lambda url, params=None, **k: (responses.get(url), None if url in responses else "HTTP 404"),
    )
    ok = cg.resolve_injury_item({"$ref": "http://core/inj/1"})
    assert (
        ok["athlete"] == "Starting QB"
        and ok["position"] == "QB"
        and ok["status"] == "Out"
        and ok["unresolved_ref"] is False
    )
    bad = cg.resolve_injury_item({"$ref": "http://core/inj/404"})
    assert bad["athlete"] is None and bad["unresolved_ref"] is True and bad["error"]


def test_a_quarterback_out_is_flagged_uncertain(monkeypatch):
    listing = {"items": [{"$ref": "http://core/inj/1"}]}
    responses = {
        "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/teams/7/injuries": listing,
        "https://core/inj/1": {"status": "Doubtful", "athlete": {"$ref": "http://core/ath/9"}},
        "https://core/ath/9": {"displayName": "Starting QB", "position": {"abbreviation": "QB"}},
    }
    monkeypatch.setattr(
        cg.transport,
        "_get",
        lambda url, params=None, **k: (responses.get(url), None if url in responses else "HTTP 404"),
    )
    out = cg.availability_for("7", "2026-11-06T00:00:00Z")
    assert (
        out["qb_uncertain"] is True
        and out["qb_name"] == "Starting QB"
        and out["unresolved_items"] == 0
        and out["listed_total"] == 1
    )
