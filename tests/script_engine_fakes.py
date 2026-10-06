"""A synthetic, fully-known CFB season for the script-engine tests.

Every team has TRUE offensive and defensive effects; each game's box score
and play-log counts are generated from them with seeded noise. Because the
truth is known, the tests can check that the opponent adjustment recovers
it, that a schedule-strength trap is undone, and that the engine's outputs
depend on football inputs only.

Deterministic: numpy's legacy RandomState with a fixed seed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from cfb_edge_finder.scripting.gamelog import BOX_KEYS, PBP_KEYS, TeamGame
from cfb_edge_finder.scripting.matchup import GameIdentity

SEASON_START = datetime(2026, 8, 29, 19, 0, tzinfo=UTC)
WEEKS = 8
UPCOMING_KICKOFF = SEASON_START + timedelta(weeks=WEEKS, hours=4)  # a Saturday night after week 8


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def team_ids(n_fbs: int = 24, n_fcs: int = 4) -> tuple[list[str], list[str]]:
    return [f"F{i:02d}" for i in range(n_fbs)], [f"C{i:02d}" for i in range(n_fcs)]


def true_effects(n_fbs: int = 24, n_fcs: int = 4, seed: int = 7) -> dict[str, dict[str, float]]:
    rng = np.random.RandomState(seed)
    fbs, fcs = team_ids(n_fbs, n_fcs)
    out: dict[str, dict[str, float]] = {}
    for t in fbs:
        out[t] = {"off": float(rng.normal(0, 0.55)), "def": float(rng.normal(0, 0.5)), "pace": float(rng.normal(0, 4))}
    for t in fcs:
        out[t] = {"off": float(rng.normal(-1.3, 0.3)), "def": float(rng.normal(1.2, 0.3)), "pace": 0.0}
    # Two fixed characters for the scripted matchup: F00 is excellent on both
    # sides and slow; F01 is poor on both sides. F02 is a high-tempo team.
    out["F00"] = {"off": 1.1, "def": -0.9, "pace": -6.0}
    out["F01"] = {"off": -0.8, "def": 0.8, "pace": -5.0}
    out["F02"] = {"off": 0.4, "def": 0.5, "pace": 9.0}
    return out


def _counts(
    rng: np.random.RandomState, off: float, dfn: float, pace: float, opp_pace: float, home_edge: float
) -> tuple[dict[str, float], dict[str, float], float]:
    strength = off + dfn + home_edge
    plays = max(45.0, round(66 + 0.5 * (pace + opp_pace) + rng.normal(0, 4)))
    ypp = max(2.5, 5.6 + 0.9 * strength + rng.normal(0, 0.7))
    rush_att = round(plays * (0.52 + rng.normal(0, 0.03)))
    pass_att = plays - rush_att
    total_yards = round(plays * ypp)
    rush_yards = round(total_yards * 0.42)
    pass_yards = total_yards - rush_yards
    success = min(0.7, max(0.2, 0.42 + 0.06 * strength + rng.normal(0, 0.03)))
    explosive = min(0.25, max(0.02, 0.09 + 0.02 * strength + rng.normal(0, 0.015)))
    sacks = max(0, round(2.2 - 0.9 * strength + rng.normal(0, 0.8)))
    turnovers = max(0, round(1.4 - 0.4 * strength + rng.normal(0, 0.9)))
    points = max(0.0, round(plays * ypp * 0.072 + rng.normal(0, 4)))
    box = {k: 0.0 for k in BOX_KEYS}
    box.update(
        {
            "plays": float(plays),
            "total_yards": float(total_yards),
            "rush_att": float(rush_att),
            "rush_yards": float(rush_yards),
            "pass_att": float(pass_att),
            "pass_cmp": float(round(pass_att * 0.62)),
            "pass_yards": float(pass_yards),
            "first_downs": float(round(plays * (0.25 + 0.04 * strength))),
            "third_att": 13.0,
            "third_conv": float(round(13 * min(0.8, max(0.15, 0.4 + 0.07 * strength)))),
            "fourth_att": 1.0,
            "fourth_conv": 0.0,
            "turnovers": float(turnovers),
            "ints_thrown": float(turnovers // 2),
            "fumbles_lost": float(turnovers - turnovers // 2),
            "possession_seconds": float(round(plays * (27 - 0.15 * pace))),
            "penalties": 6.0,
            "penalty_yards": 50.0,
        }
    )
    scrim = plays - 3
    rush_plays = rush_att - 1
    pass_plays = scrim - rush_plays
    pbp = {k: 0.0 for k in PBP_KEYS}
    pbp.update(
        {
            "scrim_plays": float(scrim),
            "success_plays": float(round(scrim * success)),
            "early_plays": float(round(scrim * 0.72)),
            "early_success": float(round(scrim * 0.72 * (success + 0.02))),
            "rush_plays": float(rush_plays),
            "rush_success": float(round(rush_plays * success)),
            "rush_yards": float(rush_yards + 8),
            "pass_plays": float(pass_plays),
            "pass_success": float(round(pass_plays * success)),
            "pass_yards": float(pass_yards - 8),
            "sacks_taken": float(sacks),
            "explosive_rush": float(round(rush_plays * explosive * 0.8)),
            "explosive_pass": float(round(pass_plays * explosive * 1.2)),
            "explosive_yards": float(round(scrim * explosive * 24)),
            "scrim_yards": float(total_yards),
            "drives": 12.0,
            "drive_points": float(points),
            "scoring_opps": float(max(1, round(points / 5))),
            "opp_points": float(points),
            "drive_seconds": float(box["possession_seconds"]),
            "drive_plays": float(plays),
            "neutral_plays": float(round(scrim * 0.6)),
            "neutral_pass_plays": float(round(scrim * 0.6 * 0.45)),
        }
    )
    return box, pbp, points


def synthetic_season(seed: int = 11, weeks: int = WEEKS, with_pbp: bool = True) -> list[TeamGame]:
    effects = true_effects()
    fbs, fcs = team_ids()
    rng = np.random.RandomState(seed)
    rows: list[TeamGame] = []
    game_no = 0
    for week in range(weeks):
        kickoff = SEASON_START + timedelta(weeks=week)
        order = list(fbs)
        rng.shuffle(order)
        pairs = [(order[i], order[i + 1]) for i in range(0, len(order), 2)]
        if week in (0, 2):
            # Two FBS teams play an FCS opponent instead (buy games).
            a, b = pairs.pop()
            pairs += [(a, fcs[week % len(fcs)]), (b, fcs[(week + 1) % len(fcs)])]
        for home, away in pairs:
            game_no += 1
            gid = f"G{game_no:04d}"
            boxes = {}
            for team, opp, edge in ((home, away, 0.15), (away, home, -0.15)):
                box, pbp, pts = _counts(
                    rng,
                    effects[team]["off"],
                    effects[opp]["def"],
                    effects[team]["pace"],
                    effects[opp]["pace"],
                    edge,
                )
                boxes[team] = (box, pbp, pts)
            # Defensive credits come from the opponent's offensive line.
            for team, opp in ((home, away), (away, home)):
                box, pbp, pts = boxes[team]
                opp_box, opp_pbp, opp_pts = boxes[opp]
                box["def_sacks"] = opp_pbp["sacks_taken"]
                box["def_tfl"] = float(opp_pbp["sacks_taken"] + 3)
                box["def_passes_defended"] = 4.0
                box["def_qb_hurries"] = 2.0
                rows.append(
                    TeamGame(
                        game_id=gid,
                        season=2026,
                        week=week + 1,
                        kickoff_utc=_iso(kickoff),
                        team_id=team,
                        team=f"Team {team}",
                        opponent_id=opp,
                        opponent=f"Team {opp}",
                        team_division="fcs" if team.startswith("C") else "fbs",
                        opponent_division="fcs" if opp.startswith("C") else "fbs",
                        site="home" if team == home else "away",
                        points_for=pts,
                        points_against=opp_pts,
                        box=dict(box),
                        pbp=dict(pbp) if with_pbp else None,
                        primary_passer=f"QB {team}",
                        primary_passer_att=box["pass_att"],
                        source="synthetic.box",
                        pbp_source="synthetic.pbp" if with_pbp else None,
                        observed_at="2026-10-25T00:00:00Z",
                    )
                )
    return rows


def identity(home: str = "F00", away: str = "F01", kickoff: datetime = UPCOMING_KICKOFF) -> GameIdentity:
    return GameIdentity(
        event_id="E-UPCOMING",
        home_id=home,
        away_id=away,
        home_name=f"Team {home}",
        away_name=f"Team {away}",
        kickoff_utc=_iso(kickoff),
        neutral_site=False,
        season=2026,
    )


def availability(status: str = "OBSERVED", qb_uncertain: bool = False) -> dict[str, Any]:
    side = {"status": status, "observed_at": "2026-10-23T12:00:00Z", "qb_uncertain": qb_uncertain}
    if qb_uncertain:
        side.update({"qb_name": "QB X", "qb_status": "Questionable"})
    return {"home": dict(side), "away": {"status": status, "observed_at": "2026-10-23T12:00:00Z"}}
