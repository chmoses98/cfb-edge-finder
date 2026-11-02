"""Period (quarter / half) scoring evidence, from the venue's own linescores. Fail-closed.

``TeamGame.period_points`` is captured from ESPN's per-period linescore at collection time (and only when the
periods sum to the final score). This module turns two team rows of one game into a game-level period record and
REPORTS COVERAGE. It never estimates a period from the final score, from a half-time guess, or from a league
average: a game without captured periods has no period record, and 1Q / 1H pricing (``build_period_distribution``)
stays refused until enough real, held-out-validated period data exists.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from cfb_edge_finder.scripting.gamelog import TeamGame

#: Smallest number of games with captured periods before a period model may even be FIT (not priced from).
#: An owner-adjustable floor; fitting is research, pricing additionally needs held-out validation.
MIN_GAMES_TO_FIT_PERIODS = 300


def period_games(rows: list[TeamGame]) -> list[dict[str, Any]]:
    by_game: dict[str, list[TeamGame]] = defaultdict(list)
    for r in rows:
        by_game[r.game_id].append(r)
    out: list[dict[str, Any]] = []
    for gid, rs in by_game.items():
        if len(rs) != 2 or any(r.period_points is None for r in rs):
            continue
        rs = sorted(rs, key=lambda r: {"home": 0, "away": 1}.get(r.site, 2))
        h, a = rs
        hp, ap = h.period_points or (), a.period_points or ()
        if len(hp) != len(ap) or len(hp) < 4:
            continue
        out.append(
            {
                "game_id": gid,
                "season": h.season,
                "kickoff": h.kickoff_utc,
                "home": h.team_id,
                "away": a.team_id,
                "neutral": h.site == "neutral",
                "home_q1": hp[0], "away_q1": ap[0],
                "home_1h": hp[0] + hp[1], "away_1h": ap[0] + ap[1],
                "home_pts": sum(hp), "away_pts": sum(ap),
                "overtime": len(hp) > 4,
            }
        )
    out.sort(key=lambda g: (g["kickoff"], g["game_id"]))
    return out


def coverage(rows: list[TeamGame]) -> dict[str, Any]:
    games: dict[str, list[TeamGame]] = defaultdict(list)
    for r in rows:
        games[r.game_id].append(r)
    with_periods = len(period_games(rows))
    total = len(games)
    seasons: dict[int, dict[str, int]] = defaultdict(lambda: {"games": 0, "with_periods": 0})
    have = {g["game_id"] for g in period_games(rows)}
    for gid, rs in games.items():
        s = seasons[rs[0].season]
        s["games"] += 1
        s["with_periods"] += int(gid in have)
    return {
        "games": total,
        "games_with_periods": with_periods,
        "games_without_periods": total - with_periods,
        "by_season": {str(k): v for k, v in sorted(seasons.items())},
        "enough_to_fit": with_periods >= MIN_GAMES_TO_FIT_PERIODS,
        "min_games_to_fit": MIN_GAMES_TO_FIT_PERIODS,
        "policy": "1Q/1H pricing stays refused until a period model is fitted AND validated on held-out games",
    }
