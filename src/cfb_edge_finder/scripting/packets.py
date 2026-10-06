"""Assembling a `FootballPacket` from stored football data. PURE.

The only thing taken from the Kalshi catalog is IDENTITY: the game key, the
two team names as Kalshi spells them and the scheduled kickoff -- what is
needed to find the same physical game in the football data. `GameRequest`
has no field for a price, and `request_from_catalog_entry` copies only the
identity keys out of a catalog row (`tests/test_script_engine_market_blindness.py`
feeds it a catalog row stuffed with prices and checks they are gone).

Orientation: the football artifact uses the football source's home/away. If
Kalshi's title lists the teams the other way round (a neutral site, or the
"vs" convention), `orientation_swapped` records it, and the market mapper
translates contract team slots through it -- by team identity, never by
position in a string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cfb_edge_finder.scripting.football import FootballPacket
from cfb_edge_finder.scripting.gamelog import FINAL_AFTER, TeamGame, data_cutoff, parse_utc
from cfb_edge_finder.scripting.matchup import GameIdentity

IDENTITY_KEYS = ("game_key", "title", "kickoff")


@dataclass(frozen=True)
class GameRequest:
    game_key: str
    title: str | None
    kalshi_home: str | None
    kalshi_away: str | None
    kickoff_utc: str | None


def request_from_catalog_entry(entry: dict[str, Any], teams: dict[str, Any]) -> GameRequest:
    """Identity only. Any other key in the catalog row is ignored by construction."""
    return GameRequest(
        game_key=str(entry.get("game_key")),
        title=entry.get("title"),
        kalshi_home=teams.get("home_name") or teams.get("home"),
        kalshi_away=teams.get("away_name") or teams.get("away"),
        kickoff_utc=entry.get("kickoff"),
    )


def identity_check(
    request: GameRequest, event: dict[str, Any] | None, home_keys: set[str], away_keys: set[str]
) -> dict[str, Any]:
    """PASS: Kalshi's home is the football home. RESOLVED: reversed, matched by name. FAIL: unmatched."""
    if event is None:
        return {"status": "FAIL", "reason": "no football-schedule event matched both teams near kickoff"}
    by_side = {c["home_away"]: c for c in event["competitors"]}
    espn_home = set(by_side.get("home", {}).get("name_keys") or [])
    espn_away = set(by_side.get("away", {}).get("name_keys") or [])
    if home_keys & espn_home and away_keys & espn_away:
        return {"status": "PASS", "orientation_swapped": False, "espn_event_id": event["id"]}
    if home_keys & espn_away and away_keys & espn_home:
        return {
            "status": "RESOLVED",
            "orientation_swapped": True,
            "espn_event_id": event["id"],
            "reason": (
                "Kalshi lists the teams in the opposite home/away order to the football schedule"
                + (" (neutral site)" if event.get("neutral_site") else "")
                + "; contract team slots are translated by team identity"
            ),
        }
    return {"status": "FAIL", "reason": "the matched event's teams could not be oriented by name"}


def freshness(event: dict[str, Any], schedule: list[dict[str, Any]], rows: list[TeamGame]) -> dict[str, Any]:
    """FRESH when every finished prior game of both teams (with an FBS team in it) is in the log."""
    cutoff = parse_utc(data_cutoff(event["date"]))
    in_log = {r.game_id for r in rows}
    team_ids = {c["team_id"] for c in event["competitors"]}
    expected, missing = 0, []
    for other in schedule:
        if not other.get("completed") or other["id"] == event["id"]:
            continue
        if not team_ids & {c["team_id"] for c in other["competitors"]}:
            continue
        if not any(c.get("fbs") for c in other["competitors"]):
            continue
        if parse_utc(other["date"]) + FINAL_AFTER >= cutoff:
            continue
        expected += 1
        if other["id"] not in in_log:
            missing.append(other["id"])
    return {
        "status": "FRESH" if not missing else "STALE",
        "expected_prior_games": expected,
        "missing_game_ids": sorted(missing),
        "rule": "every finished prior game of either team that involved an FBS team must be in the game log",
    }


def _availability_side(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {"status": "NOT_OBSERVED"}
    keep = ("status", "observed_at", "qb_uncertain", "qb_name", "qb_status")
    out = {k: raw.get(k) for k in keep if k in raw}
    if raw.get("items") is not None:
        out["listed"] = len(raw["items"])
    return out


def build_football_packet(
    request: GameRequest,
    event: dict[str, Any],
    check: dict[str, Any],
    schedule: list[dict[str, Any]],
    rows: tuple[TeamGame, ...],
    availability: dict[str, Any],
    season: int,
) -> FootballPacket:
    by_side = {c["home_away"]: c for c in event["competitors"]}
    home, away = by_side["home"], by_side["away"]
    identity = GameIdentity(
        event_id=event["id"],
        home_id=home["team_id"],
        away_id=away["team_id"],
        home_name=home.get("location") or (home.get("names") or [home["team_id"]])[0],
        away_name=away.get("location") or (away.get("names") or [away["team_id"]])[0],
        kickoff_utc=parse_utc(event["date"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
        neutral_site=bool(event.get("neutral_site")),
        season=season,
    )
    return FootballPacket(
        identity=identity,
        game_key=request.game_key,
        rows=rows,
        availability={
            "home": _availability_side(availability.get(home["team_id"])),
            "away": _availability_side(availability.get(away["team_id"])),
        },
        identity_check=check,
        freshness=freshness(event, schedule, list(rows)),
    )
