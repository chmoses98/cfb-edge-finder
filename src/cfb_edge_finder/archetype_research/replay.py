"""Walk-forward pregame replay, freeze, and reveal. RESEARCH ONLY, football only.

*** THE LEAKAGE BOUNDARY ***
`pregame(all_rows, target)` computes the production cutoff for the target,
selects `history = gamelog.before(all_rows, cutoff)` and hands ONLY `history`
to `football.build_content`. The target game and every later game are not in
the builder's input at all, so nothing about them can reach the pregame
record. `build_content` applies `before` again internally; the double
application is a no-op on `history` (tested), so what runs here is exactly
the production path.

*** FREEZE, THEN REVEAL ***
`freeze` hashes the pregame record (canonical JSON). `reveal` refuses a
record whose content no longer matches its hash, and is the only function
that reads the target game's own box-score rows.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cfb_edge_finder.scripting import METHODOLOGY_VERSION
from cfb_edge_finder.scripting.cfbd import team_games_from_cfbd
from cfb_edge_finder.scripting.confidence import assess
from cfb_edge_finder.scripting.football import FootballPacket, LeagueFitCache, build_content
from cfb_edge_finder.scripting.gamelog import TeamGame, before, data_cutoff, parse_utc
from cfb_edge_finder.scripting.matchup import GameIdentity
from cfb_edge_finder.scripting.scripts import closeness_grants_margin

#: The ONLY CFBD cache files the study reads. Market lines (`lines_*`),
#: ratings, rankings, talent and recruiting are never opened.
CFBD_FILES = (
    "games.json.gz",
    "games_teams.json.gz",
    "advanced_regular_nogarbage.json.gz",
    "drives_regular.json.gz",
    "advanced_postseason.json.gz",
    "drives_postseason.json.gz",
)

#: Fields of a CFBD `games` record the study may read (identity and schedule only).
#: Elo, win probability, excitement and line scores are never read.
GAME_META_FIELDS = (
    "id",
    "season",
    "week",
    "seasonType",
    "startDate",
    "completed",
    "neutralSite",
    "conferenceGame",
    "homeId",
    "awayId",
    "homeTeam",
    "awayTeam",
    "homeClassification",
    "awayClassification",
)

OBSERVED_CLEAN = {
    "home": {"status": "OBSERVED", "qb_uncertain": False},
    "away": {"status": "OBSERVED", "qb_uncertain": False},
}

#: Paths the research code must never write under.
FORBIDDEN_OUTPUT_PREFIXES = ("data/scripting/live", "data/scripting/ledger", "data/live", "data/football")


class LeakageError(RuntimeError):
    """A pregame record saw a game that had not finished before its cutoff."""


class FrozenRecordMismatch(RuntimeError):
    """A frozen pregame record was altered after it was frozen."""


def assert_research_output_path(path: Path | str) -> None:
    """Refuse to write anywhere the live publication or the prospective ledger lives."""
    text = Path(path).as_posix()
    for prefix in FORBIDDEN_OUTPUT_PREFIXES:
        if f"/{prefix}/" in f"/{text}/" or text.startswith(prefix):
            raise PermissionError(f"research output may not be written under {prefix}: {text}")


# ------------------------------------------------------------------ loading


def _load_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cfbd_season(directory: Path) -> dict[str, Any]:
    """TeamGame rows, schedule metadata and file digests for one CFBD season cache."""
    raw = {name: _load_gz(directory / name) for name in CFBD_FILES}
    games = [{k: g.get(k) for k in GAME_META_FIELDS} for g in raw["games.json.gz"]]
    rows = team_games_from_cfbd(
        games,
        raw["games_teams.json.gz"],
        raw["advanced_regular_nogarbage.json.gz"] + raw["advanced_postseason.json.gz"],
        raw["drives_regular.json.gz"] + raw["drives_postseason.json.gz"],
        observed_at="historical-cfbd-cache",
    )
    return {
        "rows": rows,
        "games": games,
        "digests": {name: file_digest(directory / name) for name in CFBD_FILES},
    }


# ------------------------------------------------------------------ targets


@dataclass(frozen=True)
class Target:
    game_id: str
    season: int
    week: int | None
    season_type: str | None
    kickoff_utc: str
    home_id: str
    away_id: str
    home_name: str
    away_name: str
    neutral_site: bool
    conference_game: bool | None
    home_division: str
    away_division: str


def schedule_targets(games: list[dict[str, Any]], rows: Iterable[TeamGame]) -> tuple[list[Target], list[dict]]:
    """Every scheduled game, as a target or an exclusion with its reason.

    Orientation (home/away) comes from the schedule; a box-score row whose
    site disagrees is an identity failure, never silently flipped."""
    by_game: dict[str, dict[str, TeamGame]] = defaultdict(dict)
    for row in rows:
        by_game[row.game_id][row.team_id] = row
    targets, excluded = [], []
    for g in sorted(games, key=lambda x: (str(x.get("startDate")), str(x.get("id")))):
        gid = str(g.get("id"))
        base = {"game_id": gid, "season": g.get("season"), "week": g.get("week"), "season_type": g.get("seasonType")}
        if not g.get("completed"):
            excluded.append({**base, "reason": "NOT_COMPLETED"})
            continue
        sides = by_game.get(gid) or {}
        home, away = sides.get(str(g.get("homeId"))), sides.get(str(g.get("awayId")))
        if home is None or away is None:
            excluded.append({**base, "reason": "NO_BOX_SCORE_ROWS"})
            continue
        if not g.get("neutralSite") and (home.site != "home" or away.site != "away"):
            excluded.append({**base, "reason": "IDENTITY_ORIENTATION_MISMATCH"})
            continue
        if home.points_for is None or away.points_for is None:
            excluded.append({**base, "reason": "MISSING_SCORE"})
            continue
        targets.append(
            Target(
                game_id=gid,
                season=int(g.get("season") or home.season),
                week=g.get("week"),
                season_type=g.get("seasonType"),
                kickoff_utc=home.kickoff_utc,
                home_id=home.team_id,
                away_id=away.team_id,
                home_name=home.team,
                away_name=away.team,
                neutral_site=bool(g.get("neutralSite")),
                conference_game=g.get("conferenceGame"),
                home_division=home.team_division,
                away_division=away.team_division,
            )
        )
    return targets, excluded


# ------------------------------------------------------------------ pregame


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def _identity(target: Target) -> GameIdentity:
    return GameIdentity(
        event_id=f"hist_{target.game_id}",
        home_id=target.home_id,
        away_id=target.away_id,
        home_name=target.home_name,
        away_name=target.away_name,
        kickoff_utc=target.kickoff_utc,
        neutral_site=target.neutral_site,
        season=target.season,
    )


def history_for(all_rows: Iterable[TeamGame], target: Target) -> tuple[str, tuple[TeamGame, ...]]:
    """The production cutoff for the target and the rows that finished before it."""
    cutoff = data_cutoff(target.kickoff_utc)
    history = tuple(before(all_rows, cutoff))
    limit = parse_utc(cutoff)
    kickoff = parse_utc(target.kickoff_utc)
    for row in history:
        if row.game_id == target.game_id:
            raise LeakageError(f"target {target.game_id} is inside its own history")
        if not row.kickoff() < kickoff or not row.kickoff() < limit:
            raise LeakageError(f"history row {row.game_id} kicked off at/after target {target.game_id} cutoff")
    return cutoff, history


def build_pregame_content(history: tuple[TeamGame, ...], target: Target, cache: LeagueFitCache) -> dict[str, Any]:
    """The production football artifact content for the target, from `history` alone."""
    packet = FootballPacket(
        identity=_identity(target),
        game_key=None,
        rows=history,
        availability=OBSERVED_CLEAN,
        identity_check={"status": "PASS", "orientation_swapped": False},
        freshness={"status": "FRESH"},
    )
    return build_content(packet, cache)


def _net(dims: dict[str, Any], name: str) -> float | None:
    return (dims.get(name) or {}).get("net_home_advantage")


def _sign_side(value: float | None) -> str | None:
    if value is None or value == 0:
        return None
    return "home" if value > 0 else "away"


def _compact_script(s: dict[str, Any]) -> dict[str, Any]:
    shape = s["outcome_shape"]
    return {
        "script_id": s["script_id"],
        "role": s["role"],
        "rank": s["rank"],
        "archetype": s["archetype"],
        "lead_side": s["lead_side"],
        "evidence_score": s["evidence_score"],
        "required_findings": s["required_findings"],
        "supporting_findings": s["supporting_findings"],
        "contradicting_findings": s["contradicting_findings"],
        "data_confidence": s["data_confidence"],
        "probability": s["probability"],
        "outcome_shape": {
            "winner_lean": shape["winner_lean"],
            "margin_environment": shape["margin_environment"],
            "total_environment": shape["total_environment"],
            "bands": shape["bands"],
            "band_authority": shape["band_authority"],
            "margin_authority_evidence": shape["margin_authority_evidence"],
        },
    }


def pregame_record(content: dict[str, Any], target: Target, cutoff: str, history: tuple[TeamGame, ...]) -> dict:
    """The compact, frozen-ready pregame record: everything the engine knew, nothing it did not."""
    vector = content["matchup_profile"]
    dims = vector["dimensions"]
    findings = content["matchup_findings"]
    names = {"home": target.home_name, "away": target.away_name}
    published = assess(
        vector,
        names=names,
        availability={},
        identity_check={"status": "PASS", "orientation_swapped": False},
        freshness={"status": "FRESH"},
    )
    baseline = vector["scoring_baseline"]
    norms = vector["adjustment"]["league_baselines"]
    prior = {
        side: sum(1 for r in history if r.team_id == tid)
        for side, tid in (("home", target.home_id), ("away", target.away_id))
    }
    by_code = {f["code"]: f for f in findings}
    sus_net = _net(dims, "sustained_efficiency")
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "game_id": target.game_id,
        "season": target.season,
        "week": target.week,
        "season_type": target.season_type,
        "kickoff_utc": target.kickoff_utc,
        "football_data_cutoff": cutoff,
        "home": target.home_name,
        "away": target.away_name,
        "home_id": target.home_id,
        "away_id": target.away_id,
        "neutral_site": target.neutral_site,
        "conference_game": target.conference_game,
        "divisions": [target.home_division, target.away_division],
        "fcs_involved": target.home_division != "fbs" or target.away_division != "fbs",
        "history_games": len({r.game_id for r in history}),
        "history_latest_kickoff": max((r.kickoff_utc for r in history), default=None),
        "rows_fingerprint": content["inputs"]["rows_fingerprint"],
        "content_hash": sha256(content),
        "prior_games": [prior["home"], prior["away"]],
        "confidence": content["data_confidence"],
        "confidence_published": published["level"],
        "adjustment_stable": bool(vector["adjustment"].get("stable")),
        "status": content["game_scripts"]["status"],
        "findings": [
            {
                "code": f["code"],
                "side": f["side"],
                "category": f["category"],
                "strength": f["strength"],
                "value": f["value"],
                "uncertainty": f["uncertainty"],
            }
            for f in findings
        ],
        "closeness_resolved": {
            code: closeness_grants_margin(by_code.get(code)) for code in ("EVEN_MATCHUP", "NARROW_EFFICIENCY_GAP")
        },
        "nets": {
            name: _net(dims, name)
            for name in (
                "sustained_efficiency",
                "scoring",
                "finishing",
                "explosiveness",
                "rushing",
                "passing",
                "disruption",
            )
        },
        "possession_environment": (dims.get("pace") or {}).get("possession_environment"),
        "reference_lead": _sign_side(sus_net),
        "baseline_lead": _sign_side(baseline.get("home_margin")),
        "baseline": {k: baseline.get(k) for k in ("home_points", "away_points", "total_points", "home_margin")},
        "league_norms": {
            mid: (norms.get(mid) or {}).get("mu")
            for mid in ("plays_per_game", "success_rate", "yards_per_play", "points_per_game")
        },
        "scripts": [_compact_script(s) for s in content["game_scripts"]["scripts"]],
        "candidates": content["game_scripts"]["candidates_considered"],
    }


def eligibility(pre: dict[str, Any]) -> str | None:
    """None when the game can be studied, else the pre-registered exclusion reason."""
    if pre["prior_games"][0] == 0 and pre["prior_games"][1] == 0:
        return "NO_PRIOR_HISTORY_BOTH"
    if pre["prior_games"][0] == 0:
        return "NO_PRIOR_HISTORY_HOME"
    if pre["prior_games"][1] == 0:
        return "NO_PRIOR_HISTORY_AWAY"
    if pre["nets"]["sustained_efficiency"] is None:
        return "PROFILE_UNDEFINED"
    if pre["baseline"]["total_points"] is None:
        return "BASELINE_UNDEFINED"
    return None


def freeze(pre: dict[str, Any]) -> dict[str, Any]:
    return {"pregame": pre, "pregame_hash": sha256(pre)}


def pregame(all_rows: Iterable[TeamGame], target: Target, cache: LeagueFitCache | None = None) -> dict[str, Any]:
    """Replay the production engine for one target from strictly earlier games, and freeze it."""
    cache = cache or LeagueFitCache()
    cutoff, history = history_for(all_rows, target)
    content = build_pregame_content(history, target, cache)
    if content["football_data_cutoff"] != cutoff:
        raise LeakageError("production cutoff disagrees with the replay cutoff")
    pre = pregame_record(content, target, cutoff, history)
    pre["eligibility"] = eligibility(pre)
    return freeze(pre)


def target_rows(all_rows: Iterable[TeamGame], target: Target) -> tuple[TeamGame, TeamGame]:
    """The target game's own two rows. Used ONLY by `reveal`."""
    sides = {r.team_id: r for r in all_rows if r.game_id == target.game_id}
    return sides[target.home_id], sides[target.away_id]


def reveal(frozen: dict[str, Any], home: TeamGame, away: TeamGame) -> tuple[dict[str, Any], TeamGame, TeamGame]:
    """Unlock the target's own rows for a frozen pregame record (hash re-checked)."""
    if sha256(frozen["pregame"]) != frozen["pregame_hash"]:
        raise FrozenRecordMismatch(frozen["pregame"].get("game_id"))
    if home.game_id != frozen["pregame"]["game_id"] or away.game_id != frozen["pregame"]["game_id"]:
        raise ValueError("revealed rows belong to a different game")
    return frozen["pregame"], home, away
