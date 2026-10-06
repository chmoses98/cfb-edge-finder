"""ESPN summary payload -> two `TeamGame` rows. PURE (no network).

Written against real payloads captured into `tests/fixtures/espn/` (the
2026-10-03 slate), not against memory of the format.

CORE counts come from `boxscore.teams[].statistics` and the `defensive`
category totals in `boxscore.players`. ENHANCED counts are derived from
`drives.previous[].plays[]` and are produced only when the play log passes
a plausibility check against the box score; a play log that does not
reconcile is dropped (`pbp=None`) and the reason recorded, never half-used.

*** PLAY CONVENTIONS (documented in docs/SCRIPT_ENGINE.md) ***
  scrimmage play   a play with down 1-4 whose type is a rush, pass, sack,
                   interception or fumble -- never a penalty, kick, punt,
                   timeout or two-point try
  success          gains >= 50% of distance on 1st down, >= 70% on 2nd,
                   100% on 3rd/4th; a touchdown is a success; a turnover
                   is not
  explosive        a designed run of 12+ yards or a completed pass of 16+
  garbage time     the score margin at the snap exceeds 38 in Q2, 28 in Q3
                   or 22 in Q4 -- excluded from every rate (counted in
                   `garbage_plays_excluded`)
  neutral          quarters 1-3, margin within 14
  opportunity      a drive with a snap at or inside the opponent's 40
  drive points     TD = 7, FG = 3 (extra points and two-point tries are not
                   attributed; defensive and return scores are not the
                   offense's)
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.scripting.gamelog import BOX_KEYS, PBP_KEYS, TeamGame

SOURCE_BOX = "espn.summary.boxscore"
SOURCE_PBP = "espn.summary.drives"

#: ESPN conference ids whose members are FBS (2026). Measured from the
#: 2026-10-03 FBS (group 80) and FCS (group 81) scoreboards.
FBS_CONFERENCE_IDS = frozenset({"1", "4", "5", "8", "9", "12", "15", "17", "18", "37", "151"})

EXPLOSIVE_RUSH_YARDS = 12
EXPLOSIVE_PASS_YARDS = 16
GARBAGE_MARGIN = {2: 38, 3: 28, 4: 22}
NEUTRAL_MARGIN = 14
OPPORTUNITY_YARDS_TO_GOAL = 40

_RUSH_TYPES = {"Rush", "Rushing Touchdown"}
_PASS_TYPES = {
    "Pass Reception",
    "Pass Incompletion",
    "Passing Touchdown",
    "Pass Interception Return",
    "Interception Return Touchdown",
    "Interception",
    "Pass",
    "Pass Completion",
}
_SACK_TYPES = {"Sack", "Sack Touchdown"}
_FUMBLE_TYPES = {"Fumble Recovery (Own)", "Fumble Recovery (Opponent)", "Fumble Return Touchdown"}
_INTERCEPTIONS = {"Pass Interception Return", "Interception Return Touchdown", "Interception"}
_COMPLETIONS = {"Pass Reception", "Passing Touchdown", "Pass Completion"}
_OFFENSIVE_TDS = {"Rushing Touchdown", "Passing Touchdown"}


def _int(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _pair(value: Any, sep: str = "-") -> tuple[float | None, float | None]:
    text = str(value or "")
    for s in (sep, "/", "-"):
        if s in text:
            left, right = text.split(s, 1)
            return _int(left), _int(right)
    return None, None


def _clock_seconds(value: Any) -> float | None:
    text = str(value or "")
    if ":" not in text:
        return None
    minutes, seconds = text.split(":", 1)
    m, s = _int(minutes), _int(seconds)
    if m is None or s is None:
        return None
    return m * 60 + s


def division_of(conference_id: Any) -> str:
    if conference_id is None or conference_id == "":
        return "unknown"
    return "fbs" if str(conference_id) in FBS_CONFERENCE_IDS else "fcs"


def _box_counts(team_block: dict[str, Any], defensive_totals: dict[str, float | None]) -> dict[str, float | None]:
    stats = {s.get("name"): s.get("displayValue") for s in team_block.get("statistics") or [] if isinstance(s, dict)}
    out: dict[str, float | None] = {k: None for k in BOX_KEYS}
    out["first_downs"] = _int(stats.get("firstDowns"))
    out["third_conv"], out["third_att"] = _pair(stats.get("thirdDownEff"))
    out["fourth_conv"], out["fourth_att"] = _pair(stats.get("fourthDownEff"))
    out["total_yards"] = _int(stats.get("totalYards"))
    out["pass_yards"] = _int(stats.get("netPassingYards"))
    out["pass_cmp"], out["pass_att"] = _pair(stats.get("completionAttempts"), "/")
    out["rush_yards"] = _int(stats.get("rushingYards"))
    out["rush_att"] = _int(stats.get("rushingAttempts"))
    out["penalties"], out["penalty_yards"] = _pair(stats.get("totalPenaltiesYards"))
    out["turnovers"] = _int(stats.get("turnovers"))
    out["fumbles_lost"] = _int(stats.get("fumblesLost"))
    out["ints_thrown"] = _int(stats.get("interceptions"))
    out["possession_seconds"] = _clock_seconds(stats.get("possessionTime"))
    if out["rush_att"] is not None and out["pass_att"] is not None:
        out["plays"] = out["rush_att"] + out["pass_att"]
    out["def_sacks"] = defensive_totals.get("sacks")
    out["def_tfl"] = defensive_totals.get("tacklesForLoss")
    out["def_passes_defended"] = defensive_totals.get("passesDefended")
    out["def_qb_hurries"] = defensive_totals.get("hurries", defensive_totals.get("QBHurries"))
    return out


def _player_categories(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """team id -> {category name -> category block}."""
    out: dict[str, dict[str, Any]] = {}
    for team in (summary.get("boxscore") or {}).get("players") or []:
        team_id = str((team.get("team") or {}).get("id") or "")
        out[team_id] = {st.get("name"): st for st in team.get("statistics") or [] if isinstance(st, dict)}
    return out


def _defensive_totals(category: dict[str, Any] | None) -> dict[str, float | None]:
    if not category:
        return {}
    keys = category.get("keys") or []
    totals = category.get("totals") or []
    return {k: _int(v) for k, v in zip(keys, totals, strict=False)}


def _primary_passer(category: dict[str, Any] | None) -> tuple[str | None, float | None]:
    if not category:
        return None, None
    keys = category.get("keys") or []
    try:
        idx = keys.index("completions/passingAttempts")
    except ValueError:
        return None, None
    best: tuple[str | None, float | None] = (None, None)
    for athlete in category.get("athletes") or []:
        stats = athlete.get("stats") or []
        if idx >= len(stats):
            continue
        _, attempts = _pair(stats[idx], "/")
        name = (athlete.get("athlete") or {}).get("displayName")
        if attempts is not None and (best[1] is None or attempts > best[1]):
            best = (name, attempts)
    return best


# ----------------------------------------------------------- play log


def _offense_of(play: dict[str, Any], drive_team: str) -> str:
    for participant in play.get("teamParticipants") or []:
        if participant.get("type") == "offense" and participant.get("id"):
            return str(participant["id"])
    start_team = ((play.get("start") or {}).get("team") or {}).get("id")
    return str(start_team or drive_team)


def _kind(play: dict[str, Any]) -> str | None:
    """rush | pass | sack | None (not a scrimmage play)."""
    text = (play.get("type") or {}).get("text") or ""
    if play.get("isPenalty") and text == "Penalty":
        return None
    if text in _RUSH_TYPES:
        return "rush"
    if text in _PASS_TYPES:
        return "pass"
    if text in _SACK_TYPES:
        return "sack"
    if text in _FUMBLE_TYPES:
        body = str(play.get("text") or "").lower()
        if "sacked" in body:
            return "sack"
        if " pass " in f" {body} ":
            return "pass"
        return "rush"
    return None


def _successful(kind: str, down: int, distance: float, yards: float, text: str) -> bool:
    if text in _OFFENSIVE_TDS:
        return True
    if text in _INTERCEPTIONS or kind == "sack":
        return False
    if text in _FUMBLE_TYPES and "Opponent" in text:
        return False
    if distance <= 0:
        return yards > 0
    need = {1: 0.5, 2: 0.7}.get(down, 1.0) * distance
    return yards >= need


def _empty_pbp() -> dict[str, float]:
    return {k: 0.0 for k in PBP_KEYS}


def play_log_counts(
    summary: dict[str, Any], home_id: str, away_id: str
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Per-team ENHANCED counts from the drive/play log, plus any problems."""
    drives = (summary.get("drives") or {}).get("previous") or []
    problems: list[str] = []
    if not drives:
        return {}, ["no drive log published"]
    counts = {home_id: _empty_pbp(), away_id: _empty_pbp()}
    score = {"home": 0.0, "away": 0.0}

    for drive in drives:
        drive_team = str((drive.get("team") or {}).get("id") or "")
        offense_plays = 0
        reached_opportunity = False
        for play in drive.get("plays") or []:
            text = (play.get("type") or {}).get("text") or ""
            start = play.get("start") or {}
            period = int(((play.get("period") or {}).get("number")) or 0)
            offense = _offense_of(play, drive_team)
            pre_home, pre_away = score["home"], score["away"]
            post_home, post_away = _int(play.get("homeScore")), _int(play.get("awayScore"))
            if post_home is not None and post_away is not None:
                score["home"], score["away"] = post_home, post_away
            kind = _kind(play)
            down = int(start.get("down") or 0)
            if kind is None or not 1 <= down <= 4 or offense not in counts:
                continue
            offense_plays += 1
            distance = float(start.get("distance") or 0)
            yards = float(play.get("statYardage") or 0)
            to_goal = start.get("yardsToEndzone")
            if isinstance(to_goal, (int, float)) and 0 < to_goal <= OPPORTUNITY_YARDS_TO_GOAL:
                reached_opportunity = True
            margin = abs(pre_home - pre_away)
            mine = counts[offense]
            if margin > GARBAGE_MARGIN.get(period, 999):
                mine["garbage_plays_excluded"] += 1
                continue
            dropback = kind in ("pass", "sack")
            success = _successful(kind, down, distance, yards, text)
            mine["scrim_plays"] += 1
            mine["scrim_yards"] += yards
            mine["success_plays"] += success
            if down <= 2:
                mine["early_plays"] += 1
                mine["early_success"] += success
            if dropback:
                mine["pass_plays"] += 1
                mine["pass_success"] += success
                mine["pass_yards"] += yards
                if kind == "sack":
                    mine["sacks_taken"] += 1
                elif text in _COMPLETIONS and yards >= EXPLOSIVE_PASS_YARDS:
                    mine["explosive_pass"] += 1
                    mine["explosive_yards"] += yards
            else:
                mine["rush_plays"] += 1
                mine["rush_success"] += success
                mine["rush_yards"] += yards
                if yards >= EXPLOSIVE_RUSH_YARDS:
                    mine["explosive_rush"] += 1
                    mine["explosive_yards"] += yards
            if period <= 3 and margin <= NEUTRAL_MARGIN:
                mine["neutral_plays"] += 1
                mine["neutral_pass_plays"] += dropback

        if drive_team not in counts or offense_plays == 0:
            continue
        mine = counts[drive_team]
        result = str(drive.get("result") or "").upper()
        points = 7.0 if result == "TD" else (3.0 if result == "FG" else 0.0)
        mine["drives"] += 1
        mine["drive_points"] += points
        mine["drive_plays"] += float(drive.get("offensivePlays") or offense_plays)
        mine["drive_seconds"] += _clock_seconds((drive.get("timeElapsed") or {}).get("displayValue")) or 0.0
        if reached_opportunity or points > 0:
            mine["scoring_opps"] += 1
            mine["opp_points"] += points
    return counts, problems


def _reconciles(pbp: dict[str, float], box: dict[str, float | None]) -> str | None:
    """None when the play log is consistent with the box score, else why not."""
    plays = box.get("plays")
    if plays is None or plays <= 0:
        return None
    logged = pbp["scrim_plays"] + pbp["garbage_plays_excluded"]
    if logged < 0.75 * plays or logged > 1.25 * plays:
        return f"play log has {int(logged)} scrimmage plays against {int(plays)} in the box score"
    if pbp["drives"] < 4:
        return "play log has fewer than four offensive drives"
    return None


def team_games_from_summary(
    summary: dict[str, Any],
    *,
    observed_at: str,
    divisions: dict[str, str] | None = None,
    week: int | None = None,
) -> list[TeamGame]:
    """Two rows (one per team) from one completed game's summary, or []."""
    header = summary.get("header") or {}
    competitions = header.get("competitions") or []
    if not competitions:
        return []
    comp = competitions[0]
    status = (comp.get("status") or {}).get("type") or {}
    if status and not status.get("completed", True):
        return []
    competitors = [c for c in comp.get("competitors") or [] if isinstance(c, dict)]
    if len(competitors) != 2:
        return []
    by_side = {c.get("homeAway"): c for c in competitors}
    if set(by_side) != {"home", "away"}:
        return []
    neutral = bool(comp.get("neutralSite"))
    game_id = str(header.get("id") or comp.get("id") or "")
    season = int(((header.get("season") or {}).get("year")) or 0)
    kickoff = str(comp.get("date") or "")
    if kickoff and len(kickoff) == 17:  # "2026-10-03T16:45Z"
        kickoff = kickoff[:-1] + ":00Z"
    week = week if week is not None else header.get("week")

    box_teams = {
        str((t.get("team") or {}).get("id") or ""): t
        for t in (summary.get("boxscore") or {}).get("teams") or []
        if isinstance(t, dict)
    }
    categories = _player_categories(summary)
    divisions = divisions or {}

    ids = {side: str((by_side[side].get("team") or {}).get("id") or by_side[side].get("id") or "") for side in by_side}
    names = {side: _team_name(by_side[side].get("team") or {}) for side in by_side}
    scores = {side: _int(by_side[side].get("score")) for side in by_side}
    if None in scores.values():
        return []

    pbp, problems = play_log_counts(summary, ids["home"], ids["away"])
    rows = []
    boxes = {}
    for side in ("home", "away"):
        team_id = ids[side]
        block = box_teams.get(team_id)
        if block is None:
            return []
        boxes[side] = _box_counts(block, _defensive_totals(categories.get(team_id, {}).get("defensive")))

    for side, other in (("home", "away"), ("away", "home")):
        team_id = ids[side]
        notes = list(problems)
        team_pbp: dict[str, float] | None = pbp.get(team_id) if pbp else None
        if team_pbp is not None:
            reason = _reconciles(team_pbp, boxes[side])
            if reason:
                notes.append(f"play log dropped: {reason}")
                team_pbp = None
        passer, passer_att = _primary_passer(categories.get(team_id, {}).get("passing"))
        rows.append(
            TeamGame(
                game_id=game_id,
                season=season,
                week=int(week) if isinstance(week, (int, float)) else None,
                kickoff_utc=kickoff,
                team_id=team_id,
                team=names[side],
                opponent_id=ids[other],
                opponent=names[other],
                team_division=divisions.get(team_id, "unknown"),
                opponent_division=divisions.get(ids[other], "unknown"),
                site="neutral" if neutral else side,
                points_for=scores[side],
                points_against=scores[other],
                box=boxes[side],
                pbp=team_pbp,
                primary_passer=passer,
                primary_passer_att=passer_att,
                source=SOURCE_BOX,
                pbp_source=SOURCE_PBP if team_pbp is not None else None,
                observed_at=observed_at,
                notes=tuple(notes),
            )
        )
    return rows


def _team_name(team: dict[str, Any]) -> str:
    return str(team.get("location") or team.get("shortDisplayName") or team.get("displayName") or team.get("id") or "")


def divisions_from_scoreboard(events: list[dict[str, Any]]) -> dict[str, str]:
    """ESPN team id -> fbs | fcs, from the conference ids on scoreboard events."""
    out: dict[str, str] = {}
    for event in events:
        for comp in event.get("competitions") or []:
            for competitor in comp.get("competitors") or []:
                team = competitor.get("team") or {}
                team_id = str(team.get("id") or competitor.get("id") or "")
                if team_id:
                    out[team_id] = division_of(team.get("conferenceId"))
    return out
