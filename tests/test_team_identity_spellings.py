"""Kalshi and ESPN spell three schools differently; both name matchers must still find the game.

The 2026-10-08 slate had three games the Script Engine published as IDENTITY_FAIL ("no
football-schedule event matched both teams near kickoff"). In each, one side's Kalshi spelling was
one the matcher could not reach:

    Kalshi "Appalachian St."         ESPN "App State"       FBS: the registry already has
                                                            "App State" as an exact alias, but
                                                            the matcher used only display_name
    Kalshi "Southeastern Louisiana"  ESPN "SE Louisiana"    non-FBS: verified in
    Kalshi "Tennessee-Martin"        ESPN "UT Martin"       fcs_identity.KNOWN_NON_FBS_NAME_VARIANTS,
                                                            never consulted by the matcher

`collect_live_context._kalshi_team_keys` feeds both matchers: the live-context collector that enriches
explorer packets (`_match_event`) and the Script Engine (`match_event`). The fixtures below are the
three real ESPN schedule events, with ESPN's own team ids and names copied verbatim from
data/football/2026/schedule.json, so the test pins the ESPN identity each school resolves to.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import collect_live_context as collector  # noqa: E402
import script_engine  # noqa: E402

# (game key, Kalshi title, kickoff, ESPN event id,
#  (ESPN home team id, ESPN home names), (ESPN away team id, ESPN away names))
GAMES = [
    (
        "26OCT10SELAUTRGV",
        "Southeastern Louisiana at UT Rio Grande Valley",
        "2026-10-11T00:00:00Z",
        "401868249",
        ("292", ["UT Rio Grande Valley Vaqueros", "UT Rio Grande Valley", "UT Rio Grande", "RGV"]),
        ("2545", ["SE Louisiana Lions", "SE Louisiana", "Lions", "SELA"]),
    ),
    (
        "26OCT16APPCCAR",
        "Appalachian St. at Coastal Carolina",
        "2026-10-17T00:00:00Z",
        "401869844",
        ("324", ["Coastal Carolina Chanticleers", "Coastal Carolina", "Chanticleers", "CCU"]),
        ("2026", ["App State Mountaineers", "Mountaineers", "App State", "APP"]),
    ),
    (
        "26OCT17WEBBUTM",
        "Gardner-Webb at Tennessee-Martin",
        "2026-10-17T19:00:00Z",
        "401868189",
        ("2630", ["UT Martin Skyhawks", "UT Martin", "Skyhawks", "UTM"]),
        ("2241", ["Gardner-Webb Runnin' Bulldogs", "Runnin' Bulldogs", "Gardner-Webb", "GWEB"]),
    ),
]


def _schedule_event(eid, date, home, away):
    """A data/football schedule event as `script_engine.load_football` leaves it."""

    def side(home_away, team):
        team_id, names = team
        keys: set[str] = set()
        for n in names:
            keys |= collector.name_variants(n) | collector.name_variants(script_engine._fold(n))
        return {
            "home_away": home_away,
            "team_id": team_id,
            "names": names,
            "name_keys": sorted(keys),
            "abbreviation": names[-1],
        }

    return {"id": eid, "date": date, "neutral_site": False, "competitors": [side("home", home), side("away", away)]}


def _espn_event(eid, date, home, away):
    """The ESPN scoreboard shape the live-context collector reads."""

    def comp(home_away, team):
        team_id, names = team
        return {
            "homeAway": home_away,
            "team": {
                "id": team_id,
                "displayName": names[0],
                "name": names[1],
                "shortDisplayName": names[2],
                "abbreviation": names[-1],
            },
        }

    return {"id": eid, "date": date, "competitions": [{"competitors": [comp("home", home), comp("away", away)]}]}


@pytest.mark.parametrize("key,title,kickoff,eid,home,away", GAMES, ids=[g[0] for g in GAMES])
def test_the_script_engine_finds_the_scheduled_game(key, title, kickoff, eid, home, away):
    schedule = [_schedule_event(g[3], g[2], g[4], g[5]) for g in GAMES]
    request, event, home_keys, away_keys = script_engine.resolve_request(key, title, kickoff, schedule)
    assert event is not None and event["id"] == eid
    check = script_engine.identity_check(request, event, home_keys, away_keys)
    assert check["status"] == "PASS"
    by_side = {c["home_away"]: c["team_id"] for c in event["competitors"]}
    assert (by_side["home"], by_side["away"]) == (home[0], away[0])


@pytest.mark.parametrize("key,title,kickoff,eid,home,away", GAMES, ids=[g[0] for g in GAMES])
def test_the_live_context_collector_finds_the_same_game(key, title, kickoff, eid, home, away):
    away_name, home_name = title.split(" at ")
    packet = {"game_key": key, "kickoff": kickoff, "game_metadata": {"teams": {"home": home_name, "away": away_name}}}
    events = [_espn_event(g[3], g[2], g[4], g[5]) for g in GAMES]
    event = collector._match_event(packet, events)
    assert event is not None and event["id"] == eid


def test_a_registry_team_carries_every_exact_alias():
    keys = collector._kalshi_team_keys({"home": "Appalachian St.", "away": None})["home"]
    assert {"appstate", "appalachianstate", "appalachianst"} <= keys


def test_a_non_fbs_school_carries_its_verified_espn_spelling():
    keys = collector._kalshi_team_keys({"home": "Southeastern Louisiana", "away": "Tennessee-Martin"})
    assert "selouisiana" in keys["home"]
    assert "utmartin" in keys["away"]


def test_an_ambiguous_or_unknown_name_gains_no_spelling():
    keys = collector._kalshi_team_keys({"home": "Miami", "away": "Nowhere Tech"})
    assert keys["home"] == {"miami"}
    assert keys["away"] == {"nowheretech"}


def test_no_two_registry_teams_share_a_matching_key():
    from cfb_edge_finder.teams.registry import REGISTRY

    owner: dict[str, str] = {}
    for team in REGISTRY:
        for key in collector._kalshi_team_keys({"home": team.display_name, "away": None})["home"]:
            assert owner.setdefault(key, team.team_id) == team.team_id, (key, owner[key], team.team_id)
