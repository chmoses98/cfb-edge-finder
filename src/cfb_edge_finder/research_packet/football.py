"""Football sections of a per-game research packet. PURE: rows + a league fit in, plain dicts out.

Everything here is derived from the committed ESPN game log (``data/football/<season>``) and the SAME opponent-adjusted
league fit the script engine uses (``scripting.adjust.fit_league``). No price, line, total, spread or implied
probability is an input to anything in this module, and the owner's forbidden-input rule (2026-11-02,
``modeling/forbidden_features.py``) is enforced by excluding every metric the register matches from every table (raw
defensive counts are published separately and labelled unadjusted).

Evidence tiers used in the output (never mixed inside one field):

* ``VERIFIED_FACT``        -- a result, a box-score count, a schedule. Observed, source-stamped.
* ``HISTORICAL_ESTIMATE``  -- a statistic computed from past games (adjusted rates, splits, comparables).
* ``MODEL_PROJECTION``     -- the repository's descriptive scoring baseline; uncalibrated, with its error evidence.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from cfb_edge_finder.modeling.forbidden_features import forbidden_in
from cfb_edge_finder.scripting.adjust import LeagueFit, rank_in_universe
from cfb_edge_finder.scripting.espn import FBS_CONFERENCE_IDS
from cfb_edge_finder.scripting.gamelog import TeamGame, before, parse_utc
from cfb_edge_finder.scripting.metrics import METRICS

VERIFIED = "VERIFIED_FACT"
ESTIMATE = "HISTORICAL_ESTIMATE"
PROJECTION = "MODEL_PROJECTION"

#: Owner rule (2026-11-02): the forbidden token in the register is never a handicapping input. Every metric whose id the
#: register matches is excluded from every published table; the set is computed, never hand-listed.
EXCLUDED_METRIC_IDS = frozenset(m.metric_id for m in METRICS if forbidden_in([m.metric_id]))

#: The metric ids a handicapper asked for, grouped by what they answer. EPA is NOT available keylessly: success rate
#: is the repository's efficiency measure, and the packet says so rather than renaming it.
INPUT_GROUPS: dict[str, tuple[str, ...]] = {
    "overall_scoring": ("points_per_game", "points_per_drive", "points_per_opportunity"),
    "efficiency": ("success_rate", "early_down_success_rate", "yards_per_play", "first_down_rate", "third_down_rate"),
    "passing": ("pass_success_rate", "yards_per_pass_attempt", "net_yards_per_dropback", "interception_rate"),
    "rushing": ("rush_success_rate", "yards_per_rush", "yards_per_designed_rush"),
    "explosiveness": ("explosive_play_rate", "explosive_rush_rate", "explosive_pass_rate", "explosive_yard_share"),
    "pressure_and_protection": ("sack_rate_allowed", "turnovers_per_game"),
    "finishing_red_zone": ("points_per_opportunity", "points_per_drive"),
    "pace_and_tendency": ("plays_per_game", "seconds_per_play", "pass_rate", "neutral_pass_rate"),
}

EPA_GAP = {
    "status": "UNAVAILABLE",
    "reason": (
        "Expected points added requires play-level expected-points values from a licensed feed (CollegeFootballData "
        "needs an API key and its host is not reachable from the collection environment). ESPN's keyless play log "
        "carries down, distance and yard line but no EPA. Opponent-adjusted SUCCESS RATE (and explosive rate, points "
        "per drive, points per scoring opportunity) are the efficiency measures provided; none is renamed EPA."
    ),
}

_METRIC_BY_ID = {m.metric_id: m for m in METRICS}


def _r(value: float | None, places: int = 4) -> float | None:
    return None if value is None else round(float(value), places)


def metric_row(league: LeagueFit, metric_id: str, team_id: str, unit: str) -> dict[str, Any] | None:
    """One team-unit metric: raw, opponent-adjusted, standard error, rank among FBS, games and quality."""
    fit = league.fits.get(metric_id)
    mdef = _METRIC_BY_ID.get(metric_id)
    if fit is None or mdef is None or metric_id in EXCLUDED_METRIC_IDS:
        return None
    est = fit.get(team_id, unit)
    if est is None:
        return None
    higher_better = mdef.offense_higher_is_better if unit == "offense" else not mdef.offense_higher_is_better
    rank, universe = rank_in_universe(fit, team_id, unit, higher_better) if fit.adjusted else (None, 0)
    return {
        "raw": _r(est.raw),
        "adjusted": _r(est.adjusted) if fit.adjusted else None,
        "adjusted_available": bool(fit.adjusted and est.adjusted is not None),
        "standard_error": _r(est.se),
        "rank": rank,
        "universe": universe,
        "games": est.games,
        "prior_weight": _r(est.prior_weight, 3),
        "quality": est.quality,
        "direction": "higher_is_better" if higher_better else "lower_is_better",
    }


def team_metric_table(league: LeagueFit, team_id: str) -> dict[str, Any]:
    """Offense and defense metric rows for one team, grouped by the handicapping question they answer."""
    out: dict[str, Any] = {}
    for group, ids in INPUT_GROUPS.items():
        rows: dict[str, Any] = {}
        for mid in ids:
            off = metric_row(league, mid, team_id, "offense")
            dfn = metric_row(league, mid, team_id, "defense")
            if off is None and dfn is None:
                continue
            rows[mid] = {"offense": off, "defense": dfn}
        out[group] = rows
    return out


def matchup_table(league: LeagueFit, home_id: str, away_id: str) -> list[dict[str, Any]]:
    """Offense-vs-defense comparisons for both directions, one row per metric, with adjusted values side by side."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group, ids in INPUT_GROUPS.items():
        for mid in ids:
            if mid in seen or mid in EXCLUDED_METRIC_IDS:
                continue
            seen.add(mid)
            for off_side, off_id, def_side, def_id in (
                ("away", away_id, "home", home_id),
                ("home", home_id, "away", away_id),
            ):
                o = metric_row(league, mid, off_id, "offense")
                d = metric_row(league, mid, def_id, "defense")
                if o is None or d is None:
                    continue
                mdef = _METRIC_BY_ID[mid]
                rows.append(
                    {
                        "group": group,
                        "metric": mid,
                        "name": mdef.name,
                        "offense_side": off_side,
                        "defense_side": def_side,
                        "offense_adjusted": o["adjusted"],
                        "offense_raw": o["raw"],
                        "offense_rank": o["rank"],
                        "defense_adjusted_allowed": d["adjusted"],
                        "defense_raw_allowed": d["raw"],
                        "defense_rank": d["rank"],
                        "offense_higher_is_better": mdef.offense_higher_is_better,
                        "games": [o["games"], d["games"]],
                        "quality": [o["quality"], d["quality"]],
                    }
                )
    return rows


def net_rating(league: LeagueFit, team_id: str) -> float | None:
    """Opponent-adjusted points scored minus points allowed per game (against an average opponent)."""
    fit = league.fits.get("points_per_game")
    if fit is None:
        return None
    off, dfn = fit.get(team_id, "offense"), fit.get(team_id, "defense")
    if off is None or dfn is None or off.adjusted is None or dfn.adjusted is None:
        return None
    return float(off.adjusted) - float(dfn.adjusted)


def net_rating_ranks(league: LeagueFit) -> dict[str, tuple[int, float]]:
    """FBS teams ordered by adjusted net points rating: team_id -> (rank, rating). Rank 1 = best."""
    fit = league.fits.get("points_per_game")
    rated: list[tuple[str, float]] = []
    if fit is not None:
        for team in fit.fbs_teams:
            rating = net_rating(league, team)
            if rating is not None:
                rated.append((team, rating))
    rated.sort(key=lambda tv: (-tv[1], tv[0]))
    return {team: (i + 1, round(rating, 2)) for i, (team, rating) in enumerate(rated)}


def _expected_points(league: LeagueFit, team_id: str, opp_id: str) -> float | None:
    fit = league.fits.get("points_per_game")
    if fit is None or fit.mu is None:
        return None
    off, dfn = fit.get(team_id, "offense"), fit.get(opp_id, "defense")
    if off is None or dfn is None or off.adjusted is None or dfn.adjusted is None:
        return None
    return float(off.adjusted) + float(dfn.adjusted) - float(fit.mu)


def _rate(num: float | None, den: float | None) -> float | None:
    if num is None or not den:
        return None
    return round(num / den, 4)


def _game_line(row: TeamGame, league: LeagueFit, ranks: dict[str, tuple[int, float]]) -> dict[str, Any]:
    pbp = row.pbp or {}
    box = row.box or {}
    opp_rank = ranks.get(row.opponent_id)
    pts_off = league.fits.get("points_per_game")
    opp_off = pts_off.get(row.opponent_id, "offense") if pts_off else None
    opp_def = pts_off.get(row.opponent_id, "defense") if pts_off else None
    off_rank = rank_in_universe(pts_off, row.opponent_id, "offense", True)[0] if pts_off and pts_off.adjusted else None
    def_rank = rank_in_universe(pts_off, row.opponent_id, "defense", False)[0] if pts_off and pts_off.adjusted else None
    margin = None if row.points_for is None or row.points_against is None else row.points_for - row.points_against
    expected = _expected_points(league, row.team_id, row.opponent_id)
    return {
        "week": row.week,
        "kickoff_utc": row.kickoff_utc,
        "game_id": row.game_id,
        "site": row.site,
        "opponent": row.opponent,
        "opponent_id": row.opponent_id,
        "opponent_division": row.opponent_division,
        "points_for": row.points_for,
        "points_against": row.points_against,
        "margin": margin,
        "result": None if margin is None else ("W" if margin > 0 else "L" if margin < 0 else "T"),
        "opponent_net_rating_rank": None if opp_rank is None else opp_rank[0],
        "opponent_net_rating": None if opp_rank is None else opp_rank[1],
        "opponent_offense_rank_points": off_rank,
        "opponent_defense_rank_points": def_rank,
        "opponent_adjusted_ppg_scored": None if opp_off is None else _r(opp_off.adjusted, 2),
        "opponent_adjusted_ppg_allowed": None if opp_def is None else _r(opp_def.adjusted, 2),
        "points_vs_adjusted_expectation": None
        if expected is None or row.points_for is None
        else round(row.points_for - expected, 1),
        "plays": box.get("plays"),
        "yards_per_play": _rate(box.get("total_yards"), box.get("plays")),
        "success_rate": _rate(pbp.get("success_plays"), pbp.get("scrim_plays")),
        "explosive_rate": _rate(
            (pbp.get("explosive_rush") or 0) + (pbp.get("explosive_pass") or 0) if pbp else None, pbp.get("scrim_plays")
        ),
        "third_down": None
        if not box.get("third_att")
        else f"{int(box.get('third_conv') or 0)}/{int(box['third_att'])}",
        "turnovers": box.get("turnovers"),
        "sacks_taken": pbp.get("sacks_taken"),
        "def_sacks": box.get("def_sacks"),
        "def_qb_hurries": box.get("def_qb_hurries"),
        "drives": pbp.get("drives"),
        "primary_passer": row.primary_passer,
        "period_points": list(row.period_points) if row.period_points else None,
        "source": row.source,
        "observed_at": row.observed_at,
    }


def _tier(rank: int | None, universe: int) -> str | None:
    if rank is None or not universe:
        return None
    return "TOP_THIRD" if rank <= universe / 3 else "MIDDLE_THIRD" if rank <= 2 * universe / 3 else "BOTTOM_THIRD"


def team_schedule(
    history: list[TeamGame],
    team_id: str,
    league: LeagueFit,
    ranks: dict[str, tuple[int, float]],
    upcoming_opponent_id: str,
) -> dict[str, Any]:
    """Every completed game of one team with opponent quality, plus strength-of-schedule and inflation diagnostics."""
    games = sorted((r for r in history if r.team_id == team_id), key=lambda r: (r.kickoff_utc, r.game_id))
    lines = [_game_line(r, league, ranks) for r in games]
    universe = len(ranks)
    fbs_lines = [g for g in lines if g["opponent_net_rating_rank"] is not None]
    avg_opp_rank = (
        round(sum(g["opponent_net_rating_rank"] for g in fbs_lines) / len(fbs_lines), 1) if fbs_lines else None
    )
    avg_opp_off = [g["opponent_offense_rank_points"] for g in lines if g["opponent_offense_rank_points"]]
    avg_opp_def = [g["opponent_defense_rank_points"] for g in lines if g["opponent_defense_rank_points"]]
    non_fbs = sum(1 for g in lines if g["opponent_division"] != "fbs")
    weak = sum(1 for g in fbs_lines if universe and g["opponent_net_rating_rank"] > 2 * universe / 3)
    strong = sum(1 for g in fbs_lines if universe and g["opponent_net_rating_rank"] <= universe / 3)
    upcoming = ranks.get(upcoming_opponent_id)
    upcoming_tier = _tier(upcoming[0], universe) if upcoming else None
    similar = [
        g for g in fbs_lines if upcoming_tier and _tier(g["opponent_net_rating_rank"], universe) == upcoming_tier
    ]

    def avg(vals: list[float | None]) -> float | None:
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    wins = sum(1 for g in lines if g["result"] == "W")
    sos_flag = None
    if lines:
        if (non_fbs + weak) / len(lines) >= 0.5:
            sos_flag = (
                "SCHEDULE_SOFT: at least half of the games were against FCS/non-FBS or bottom-third FBS opponents"
            )
        elif strong / len(lines) >= 0.5:
            sos_flag = "SCHEDULE_HARD: at least half of the games were against top-third FBS opponents"
    return {
        "evidence_tier": VERIFIED,
        "team_id": team_id,
        "record": f"{wins}-{len(lines) - wins}",
        "games": lines,
        "strength_of_schedule": {
            "evidence_tier": ESTIMATE,
            "games": len(lines),
            "fbs_universe": universe,
            "avg_opponent_net_rating_rank": avg_opp_rank,
            "avg_opponent_offense_rank_points": None
            if not avg_opp_off
            else round(sum(avg_opp_off) / len(avg_opp_off), 1),
            "avg_opponent_defense_rank_points": None
            if not avg_opp_def
            else round(sum(avg_opp_def) / len(avg_opp_def), 1),
            "games_vs_non_fbs": non_fbs,
            "games_vs_bottom_third_fbs": weak,
            "games_vs_top_third_fbs": strong,
            "flag": sos_flag,
        },
        "against_similar_opponents": {
            "evidence_tier": VERIFIED,
            "definition": f"games against FBS opponents in the same adjusted-net-rating tier as the upcoming opponent "
                f"({upcoming_tier})",
            "upcoming_opponent_tier": upcoming_tier,
            "games": [
                {
                    k: g[k]
                    for k in (
                        "week",
                        "opponent",
                        "site",
                        "points_for",
                        "points_against",
                        "result",
                        "opponent_net_rating_rank",
                        "success_rate",
                    )
                }
                for g in similar
            ],
            "avg_points_for": avg([g["points_for"] for g in similar]),
            "avg_points_against": avg([g["points_against"] for g in similar]),
            "n": len(similar),
            "small_sample_warning": len(similar) < 3,
        },
    }


def results_from_schedule(events: list[dict[str, Any]], team_id: str, before_utc: str) -> dict[str, Any]:
    """Exact results for a team the game log holds NO box scores for (both sides outside the FBS).

    The scoreboard sweep covers FCS events, so final scores and opponents exist; box scores, play logs and therefore
    every opponent-adjusted metric do not. This is a results list and nothing more, and it says so."""
    cutoff = parse_utc(before_utc)
    games: list[dict[str, Any]] = []
    for ev in sorted(events, key=lambda e: (e.get("date") or "", e.get("id") or "")):
        if not ev.get("completed") or not ev.get("date") or parse_utc(ev["date"]) >= cutoff:
            continue
        me = next((c for c in ev["competitors"] if str(c.get("team_id")) == str(team_id)), None)
        opp = next((c for c in ev["competitors"] if c is not me), None) if me else None
        if me is None or opp is None:
            continue
        try:
            pf, pa = float(me["score"]), float(opp["score"])
        except (KeyError, TypeError, ValueError):
            continue
        games.append(
            {
                "kickoff_utc": parse_utc(ev["date"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "game_id": ev.get("id"),
                "site": "neutral" if ev.get("neutral_site") else me.get("home_away"),
                "opponent": opp.get("location") or (opp.get("names") or [None])[0],
                "opponent_id": opp.get("team_id"),
                "opponent_in_fbs": str(opp.get("conference_id")) in FBS_CONFERENCE_IDS,
                "points_for": pf,
                "points_against": pa,
                "margin": pf - pa,
                "result": "W" if pf > pa else "L" if pf < pa else "T",
            }
        )
    wins = sum(1 for g in games if g["result"] == "W")
    return {
        "evidence_tier": VERIFIED,
        "results_only": True,
        "team_id": team_id,
        "record": f"{wins}-{len(games) - wins}",
        "games": games,
        "points_for_per_game": round(sum(g["points_for"] for g in games) / len(games), 1) if games else None,
        "points_against_per_game": round(sum(g["points_against"] for g in games) / len(games), 1) if games else None,
        "limits": (
            "final scores only: the game log collects FBS-involved games, so there are no box scores, no play log "
                "and no "
            "opponent-adjusted metrics for this team. Opponent quality is not measured; do not infer it from this list"
        ),
    }


def recent_form(schedule: dict[str, Any]) -> dict[str, Any]:
    """Last-3 versus season, in raw terms and against the opponent-adjusted points expectation, with n stated.

    The adjusted expectation uses the same full-season fit, so a sub-window residual is in-sample for the games that
    shaped the fit: it describes recent over/under-performance, it does not predict it."""
    games = schedule["games"]
    last = games[-3:]

    def avg(rows: list[dict[str, Any]], key: str) -> float | None:
        vals = [g[key] for g in rows if g.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "evidence_tier": ESTIMATE,
        "season_games": len(games),
        "last_games": len(last),
        "season": {
            k: avg(games, k)
            for k in (
                "points_for",
                "points_against",
                "success_rate",
                "yards_per_play",
                "points_vs_adjusted_expectation",
            )
        },
        "last3": {
            k: avg(last, k)
            for k in (
                "points_for",
                "points_against",
                "success_rate",
                "yards_per_play",
                "points_vs_adjusted_expectation",
            )
        },
        "small_sample_warning": len(last) < 3 or len(games) < 6,
        "caution": "three games is a small sample; weigh against the full-season opponent-adjusted profile, never "
            "instead of "
            "it",
    }


def split_table(history: list[TeamGame], team_id: str) -> dict[str, Any]:
    """Raw home / away / neutral splits (n stated). Unadjusted: the opponents differ between the splits."""
    by_site: dict[str, list[TeamGame]] = defaultdict(list)
    for r in history:
        if r.team_id == team_id:
            by_site[r.site].append(r)
    out: dict[str, Any] = {"evidence_tier": VERIFIED, "adjusted": False}
    for site in ("home", "away", "neutral"):
        rs = by_site.get(site, [])
        if not rs:
            continue
        pf = [r.points_for for r in rs if r.points_for is not None]
        pa = [r.points_against for r in rs if r.points_against is not None]
        plays = sum((r.box or {}).get("plays") or 0 for r in rs)
        yards = sum((r.box or {}).get("total_yards") or 0 for r in rs)
        succ = sum((r.pbp or {}).get("success_plays") or 0 for r in rs)
        scrim = sum((r.pbp or {}).get("scrim_plays") or 0 for r in rs)
        out[site] = {
            "games": len(rs),
            "points_for_per_game": round(sum(pf) / len(pf), 1) if pf else None,
            "points_against_per_game": round(sum(pa) / len(pa), 1) if pa else None,
            "yards_per_play": _rate(yards, plays),
            "success_rate": _rate(succ, scrim),
            "small_sample_warning": len(rs) < 3,
        }
    return out


def period_splits(history: list[TeamGame], team_id: str) -> dict[str, Any]:
    """Raw per-quarter / per-half scoring for and against, from the venue linescores captured in the log.

    UNADJUSTED (the opponents differ game to game) and only over games whose linescore was captured and reconciled to
    the final score: ``games`` says how many. Opening-drive tendencies are not captured and are not inferred from
    these."""
    rows = [r for r in history if r.team_id == team_id and r.period_points and len(r.period_points) >= 4]
    index = {(r.game_id, r.team_id): r for r in history}
    usable = []
    for r in rows:
        opp = index.get((r.game_id, r.opponent_id))
        if opp is None or not opp.period_points or len(opp.period_points) != len(r.period_points):
            continue
        usable.append((r.period_points, opp.period_points))
    if not usable:
        return {
            "evidence_tier": VERIFIED,
            "adjusted": False,
            "games": 0,
            "status": "UNAVAILABLE",
            "reason": "no captured linescores for this team",
        }
    n = len(usable)

    def avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 2)

    out: dict[str, Any] = {"evidence_tier": VERIFIED, "adjusted": False, "games": n, "small_sample_warning": n < 4}
    for label, pick in (
        ("q1", lambda p: p[0]),
        ("q2", lambda p: p[1]),
        ("q3", lambda p: p[2]),
        ("q4", lambda p: p[3]),
        ("first_half", lambda p: p[0] + p[1]),
        ("second_half", lambda p: p[2] + p[3]),
    ):
        pf = [pick(f) for f, _ in usable]
        pa = [pick(a) for _, a in usable]
        totals = [x + y for x, y in zip(pf, pa, strict=True)]
        out[label] = {"points_for": avg(pf), "points_against": avg(pa), "total": avg(totals)}
    first_half_for = sum(f[0] + f[1] for f, _ in usable)
    total_for = sum(sum(f[:4]) for f, _ in usable)
    out["share_of_own_regulation_points_in_first_half"] = round(first_half_for / total_for, 3) if total_for else None
    out["games_scoreless_first_quarter_for"] = sum(1 for f, _ in usable if f[0] == 0)
    out["games_leading_at_half"] = sum(1 for f, a in usable if f[0] + f[1] > a[0] + a[1])
    out["games_trailing_at_half"] = sum(1 for f, a in usable if f[0] + f[1] < a[0] + a[1])
    return out


def rest_and_situation(history: list[TeamGame], team_id: str, kickoff_utc: str) -> dict[str, Any]:
    games = sorted((r for r in history if r.team_id == team_id), key=lambda r: (r.kickoff_utc, r.game_id))
    kickoff = parse_utc(kickoff_utc)
    if not games:
        return {"evidence_tier": VERIFIED, "days_since_last_game": None, "note": "no completed games in the log"}
    last = parse_utc(games[-1].kickoff_utc)
    days = round((kickoff - last).total_seconds() / 86400, 1)
    return {
        "evidence_tier": VERIFIED,
        "last_game": games[-1].kickoff_utc,
        "days_since_last_game": days,
        "short_week": days < 6.0,
        "coming_off_bye": days >= 12.0,
        "last_game_site": games[-1].site,
        "travel": "NOT_COMPUTED: venue coordinates are not published keylessly; venue city/state are in `environment`",
    }


def pace_environment(league: LeagueFit, home_id: str, away_id: str, history: list[TeamGame]) -> dict[str, Any]:
    """Pace, tendencies and expected possessions for the pair."""

    def drives(team: str) -> tuple[float | None, int]:
        rs = [r for r in history if r.team_id == team and r.pbp and r.pbp.get("drives")]
        return (round(sum(r.pbp["drives"] for r in rs) / len(rs), 2), len(rs)) if rs else (None, 0)

    def both_ways(team: str) -> dict[str, Any]:
        out = {}
        for mid in ("plays_per_game", "seconds_per_play", "pass_rate", "neutral_pass_rate"):
            out[mid] = {"offense": metric_row(league, mid, team, "offense")}
        return out

    h_drives, hn = drives(home_id)
    a_drives, an = drives(away_id)
    both = [d for d in (h_drives, a_drives) if d is not None]
    # Opposing drives alternate, so one team's drives per game is the game's possessions per side.
    expected = round(sum(both) / len(both), 1) if both else None
    return {
        "evidence_tier": ESTIMATE,
        "home": both_ways(home_id),
        "away": both_ways(away_id),
        "drives_per_game": {"home": h_drives, "away": a_drives, "games": {"home": hn, "away": an}},
        "expected_possessions_per_team": expected,
        "expected_possessions_basis": "mean of the two teams' observed drives per game (raw); a pace-clash estimate, "
            "not a forecast",
    }


def passers_summary(history: list[TeamGame], team_id: str) -> dict[str, Any]:
    """The quarterback evidence the box scores carry. This is INFERENCE; no confirmed-starter source exists."""
    games = sorted((r for r in history if r.team_id == team_id), key=lambda r: (r.kickoff_utc, r.game_id))
    log = [
        {
            "week": r.week,
            "opponent": r.opponent,
            "passer": r.primary_passer,
            "attempts": r.primary_passer_att,
            "kickoff_utc": r.kickoff_utc,
        }
        for r in games
    ]
    attempts: dict[str, float] = defaultdict(float)
    for r in games:
        if r.primary_passer:
            attempts[r.primary_passer] += r.primary_passer_att or 0.0
    leader = max(attempts, key=lambda k: attempts[k]) if attempts else None
    last = next((g for g in reversed(log) if g["passer"]), None)
    recent = [g["passer"] for g in log[-3:] if g["passer"]]
    changed = len(set(recent)) > 1
    return {
        "evidence_tier": VERIFIED,
        "projected_starter": None if last is None else last["passer"],
        "projected_starter_basis": "primary passer (most attempts) in the team's most recent completed game",
        "confirmed_starter": None,
        "confirmed_starter_status": "NOT_CONFIRMED: no keyless source publishes a confirmed CFB starting quarterback. "
            "Verify by web research "
            "and cite the source.",
        "season_attempt_leader": leader,
        "season_attempts_by_passer": {k: v for k, v in sorted(attempts.items(), key=lambda kv: -kv[1])},
        "changed_in_last_3_games": changed,
        "leader_differs_from_last_game": bool(leader and last and leader != last["passer"]),
        "game_log": log,
    }


def asof(value: str | datetime) -> str:
    return parse_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def history_before(rows: tuple[TeamGame, ...], cutoff: str) -> list[TeamGame]:
    return list(before(rows, cutoff))
