"""Point-in-time matchup evidence: what exists, as of when, for how many teams -- and what is MISSING.

Walk-forward only: every profile is built from rows that ``gamelog.before`` admits, i.e. games that had certainly
finished before the cutoff. Nothing here fits a model or produces a probability; it is the evidence layer a
model would read, with coverage counted honestly.

INPUT FAMILIES
  pace                  plays per game, seconds per play (box score; drives when a play log reconciled)
  efficiency            success rate / early-down success rate / yards per play (play log), points per drive
  special_teams         FG, XP, punt, return counts (box score ``kicking/punting/kickReturns/puntReturns``)
  injuries              the availability capture (status + observed_at), per team, as of its observation time
  period_scoring        per-quarter points (venue linescore) -- see research/period_backfill.py
  EPA / PPA             MISSING_SOURCE: not in ESPN's summary; needs CFBD advanced stats, collected in Actions only
  havoc                 FORBIDDEN (owner rule); never read here
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from cfb_edge_finder.scripting.gamelog import TeamGame, before

#: Minimum finished games before a team's profile is reported as usable (owner-adjustable).
MIN_GAMES = 3

MISSING_SOURCE_EPA = "MISSING_SOURCE_EPA_REQUIRES_CFBD_ADVANCED_STATS"


def _mean(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def team_profile_asof(rows: list[TeamGame], team_id: str, cutoff: datetime | str) -> dict[str, Any]:
    """Pace / efficiency / special teams for one team from games finished before ``cutoff``."""
    mine = [r for r in before(rows, cutoff) if r.team_id == team_id]
    flags: list[str] = []
    n = len(mine)
    if n < MIN_GAMES:
        flags.append(f"FEWER_THAN_{MIN_GAMES}_GAMES")

    plays = [r.box.get("plays") for r in mine if r.box.get("plays") is not None]
    secs = [r.box.get("possession_seconds") for r in mine if r.box.get("possession_seconds") is not None]
    pbp = [r.pbp for r in mine if r.pbp is not None]
    drives = sum(p.get("drives") or 0 for p in pbp)
    drive_pts = sum(p.get("drive_points") or 0 for p in pbp)
    early_plays = sum(p.get("early_plays") or 0 for p in pbp)
    early_succ = sum(p.get("early_success") or 0 for p in pbp)
    st_rows = [r.special_teams for r in mine if r.special_teams]
    if not pbp:
        flags.append("NO_PLAY_LOG")
    if not st_rows:
        flags.append("NO_SPECIAL_TEAMS_CAPTURED")
    flags.append(MISSING_SOURCE_EPA)

    def total(key: str) -> float:
        return sum(s.get(key, 0.0) for s in st_rows)

    fg_att = total("fg_att")
    return {
        "team_id": team_id,
        "as_of": str(cutoff),
        "games": n,
        "last_game_observed_at": max((r.observed_at or "" for r in mine), default="") or None,
        "pace": {
            "plays_per_game": _mean([float(x) for x in plays]),
            "possession_seconds_per_play": (sum(secs) / sum(plays)) if plays and secs and sum(plays) else None,
            "games_with_value": len(plays),
        },
        "efficiency": {
            "early_down_success_rate": (early_succ / early_plays) if early_plays else None,
            "points_per_drive": (drive_pts / drives) if drives else None,
            "games_with_play_log": len(pbp),
        },
        "special_teams": {
            "games_with_value": len(st_rows),
            "fg_pct": (total("fg_made") / fg_att) if fg_att else None,
            "fg_att": fg_att if st_rows else None,
            "punt_avg": (total("punt_yards") / total("punts")) if st_rows and total("punts") else None,
            "punt_return_avg": (total("pr_yards") / total("pr_n")) if st_rows and total("pr_n") else None,
            "kick_return_avg": (total("kr_yards") / total("kr_n")) if st_rows and total("kr_n") else None,
        },
        "flags": flags,
    }


def evidence_coverage(rows: list[TeamGame], availability: dict[str, Any] | None = None) -> dict[str, Any]:
    """Counts of team-games and teams per input family. Missing is reported, not guessed."""
    n = len(rows)
    teams = {r.team_id for r in rows}
    by_team_st: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.special_teams:
            by_team_st[r.team_id] += 1
    avail = availability or {}
    observed = {t: v for t, v in avail.items() if isinstance(v, dict) and v.get("status") == "OBSERVED"}
    return {
        "team_game_rows": n,
        "teams": len(teams),
        "pace": {"rows_with_plays": sum(1 for r in rows if r.box.get("plays") is not None)},
        "efficiency": {"rows_with_play_log": sum(1 for r in rows if r.pbp is not None)},
        "special_teams": {
            "rows_with_value": sum(1 for r in rows if r.special_teams),
            "teams_with_any": len(by_team_st),
            "note": "captured from collection time onward; earlier rows are missing until re-fetched",
        },
        "period_scoring": {"rows_with_periods": sum(1 for r in rows if r.period_points is not None)},
        "injuries": {
            "teams_in_capture": len(avail),
            "teams_observed": len(observed),
            "teams_with_listed_items": sum(1 for v in observed.values() if v.get("items")),
            "qb_status_known": sum(1 for v in observed.values() if v.get("qb_status")),
            "note": "a single capture per run; there is no point-in-time injury HISTORY before it",
        },
        "epa": MISSING_SOURCE_EPA,
        "havoc": "FORBIDDEN_NEVER_READ",
    }
