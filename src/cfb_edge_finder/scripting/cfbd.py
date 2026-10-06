"""CFBD wire rows -> `TeamGame` rows. PURE (no network).

The ENHANCED-tier alternative to the ESPN play log, and the way the
methodology is exercised on whole historical seasons from the research
cache. It is OPT-IN in production for the same reason every other CFBD
surface is: the redistribution licence is unresolved
(`scripts/research_export.py`, `LICENCE_OPT_IN_REASON`), so nothing built
from it is published unless the owner turns it on.

  /games/teams          -> CORE box counts (same categories ESPN publishes)
  /stats/game/advanced  -> success counts (CFBD's own success definition,
                           garbage time excluded when the cache asked for it)
  /drives               -> drives, drive points, scoring opportunities, tempo

CFBD publishes no explosive-play COUNTS (its `explosiveness` is a PPA
magnitude, a different quantity), so explosive metrics stay None for CFBD
rows rather than being approximated from a number that means something else.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from cfb_edge_finder.scripting.espn import _clock_seconds, _int, _pair
from cfb_edge_finder.scripting.gamelog import BOX_KEYS, PBP_KEYS, TeamGame

SOURCE_BOX = "cfbd.games_teams"
SOURCE_PBP = "cfbd.advanced+drives"


def _box(stats: list[dict[str, Any]]) -> dict[str, float | None]:
    raw = {s.get("category"): s.get("stat") for s in stats if isinstance(s, dict)}
    out: dict[str, float | None] = {k: None for k in BOX_KEYS}
    out["first_downs"] = _int(raw.get("firstDowns"))
    out["third_conv"], out["third_att"] = _pair(raw.get("thirdDownEff"))
    out["fourth_conv"], out["fourth_att"] = _pair(raw.get("fourthDownEff"))
    out["total_yards"] = _int(raw.get("totalYards"))
    out["pass_yards"] = _int(raw.get("netPassingYards"))
    out["pass_cmp"], out["pass_att"] = _pair(raw.get("completionAttempts"))
    out["rush_yards"] = _int(raw.get("rushingYards"))
    out["rush_att"] = _int(raw.get("rushingAttempts"))
    out["penalties"], out["penalty_yards"] = _pair(raw.get("totalPenaltiesYards"))
    out["turnovers"] = _int(raw.get("turnovers"))
    out["fumbles_lost"] = _int(raw.get("fumblesLost"))
    out["ints_thrown"] = _int(raw.get("interceptions"))
    out["possession_seconds"] = _clock_seconds(raw.get("possessionTime"))
    if out["rush_att"] is not None and out["pass_att"] is not None:
        out["plays"] = out["rush_att"] + out["pass_att"]
    out["def_sacks"] = _int(raw.get("sacks"))
    out["def_tfl"] = _int(raw.get("tacklesForLoss"))
    out["def_passes_defended"] = _int(raw.get("passesDeflected"))
    out["def_qb_hurries"] = _int(raw.get("qbHurries"))
    return out


def _count(block: dict[str, Any] | None) -> float | None:
    """Plays in a CFBD sub-block, recovered as totalPPA / ppa."""
    if not block:
        return None
    total, per = block.get("totalPPA"), block.get("ppa")
    if not isinstance(total, (int, float)) or not isinstance(per, (int, float)) or abs(per) < 1e-9:
        return None
    return round(total / per)


def _enhanced(advanced: dict[str, Any] | None, drives: list[dict[str, Any]]) -> dict[str, float | None] | None:
    if advanced is None and not drives:
        return None
    out: dict[str, float | None] = {k: None for k in PBP_KEYS}
    offense = (advanced or {}).get("offense") or {}
    plays = offense.get("plays")
    rate = offense.get("successRate")
    if isinstance(plays, (int, float)) and isinstance(rate, (int, float)):
        out["scrim_plays"] = float(plays)
        out["success_plays"] = round(rate * plays)
    for prefix, key in (("rush", "rushingPlays"), ("pass", "passingPlays")):
        block = offense.get(key) or {}
        n = _count(block)
        if n is not None and isinstance(block.get("successRate"), (int, float)):
            out[f"{prefix}_plays"] = float(n)
            out[f"{prefix}_success"] = round(block["successRate"] * n)
    if drives:
        totals = defaultdict(float)
        for drive in drives:
            result = str(drive.get("driveResult") or "").upper()
            points = 7.0 if result == "TD" else (3.0 if result == "FG" else 0.0)
            totals["drives"] += 1
            totals["drive_points"] += points
            totals["drive_plays"] += float(drive.get("plays") or 0)
            elapsed = drive.get("elapsed") or {}
            totals["drive_seconds"] += float(elapsed.get("minutes") or 0) * 60 + float(elapsed.get("seconds") or 0)
            reached = min(float(drive.get("startYardsToGoal") or 100), float(drive.get("endYardsToGoal") or 100))
            if 0 <= reached <= 40 or points > 0:
                totals["scoring_opps"] += 1
                totals["opp_points"] += points
        out.update(totals)
    return out


def team_games_from_cfbd(
    games: list[dict[str, Any]],
    games_teams: list[dict[str, Any]],
    advanced: list[dict[str, Any]] | None = None,
    drives: list[dict[str, Any]] | None = None,
    *,
    observed_at: str,
) -> list[TeamGame]:
    meta = {int(g["id"]): g for g in games if g.get("id") is not None and g.get("completed")}
    adv = {(int(a["gameId"]), str(a["team"])): a for a in advanced or [] if a.get("gameId") is not None}
    by_drive: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for d in drives or []:
        if d.get("gameId") is not None:
            by_drive[(int(d["gameId"]), str(d.get("offense")))].append(d)

    rows: list[TeamGame] = []
    for entry in games_teams:
        gid = int(entry.get("id") or 0)
        game = meta.get(gid)
        teams = entry.get("teams") or []
        if game is None or len(teams) != 2:
            continue
        sides = {t.get("homeAway"): t for t in teams}
        if set(sides) != {"home", "away"}:
            continue
        kickoff = str(game.get("startDate") or "").replace(".000Z", "Z")
        division = {
            "home": str(game.get("homeClassification") or "unknown"),
            "away": str(game.get("awayClassification") or "unknown"),
        }
        for side, other in (("home", "away"), ("away", "home")):
            mine, theirs = sides[side], sides[other]
            name = str(mine.get("team"))
            box = _box(mine.get("stats") or [])
            enhanced = _enhanced(adv.get((gid, name)), by_drive.get((gid, name), []))
            rows.append(
                TeamGame(
                    game_id=str(gid),
                    season=int(game.get("season") or 0),
                    week=game.get("week"),
                    kickoff_utc=kickoff,
                    team_id=str(mine.get("teamId")),
                    team=name,
                    opponent_id=str(theirs.get("teamId")),
                    opponent=str(theirs.get("team")),
                    team_division=division[side] if division[side] in ("fbs", "fcs") else "other",
                    opponent_division=division[other] if division[other] in ("fbs", "fcs") else "other",
                    site="neutral" if game.get("neutralSite") else side,
                    points_for=_int(mine.get("points")),
                    points_against=_int(theirs.get("points")),
                    box=box,
                    pbp=enhanced,
                    source=SOURCE_BOX,
                    pbp_source=SOURCE_PBP if enhanced is not None else None,
                    observed_at=observed_at,
                )
            )
    return rows
