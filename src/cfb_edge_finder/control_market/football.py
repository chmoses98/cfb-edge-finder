"""The 2026 CONTROL population: retrospective replay and prospective V2 ledger rows.

*** LEAKAGE BOUNDARY ***
The replay is the Wave 1/2 walk-forward replay unchanged:
`archetype_research.replay.history_for` keeps only games that finished before
the production cutoff of the target (and raises `LeakageError` otherwise), and
`build_pregame_content` runs the unmodified production `build_content` on that
history alone. The CONTROL claim is the production `derive_claims` rule on the
resulting findings. The target game's score is never an input here: it is read
only by `outcome_for`, which the study calls at the REVEAL stage.

*** PROSPECTIVE ROWS ARE NEVER RECOMPUTED ***
A V2 ledger FINAL_PREGAME row is read as written; its CONTROL tier is copied.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from cfb_edge_finder.archetype_research import replay
from cfb_edge_finder.archetype_research.v2.claims import control_claim as research_control_claim
from cfb_edge_finder.control_market import METHODOLOGY_VERSION, POP_PROSPECTIVE, POP_REPLAY, V2_LEDGER_START, sha256
from cfb_edge_finder.scripting.claims import derive_claims
from cfb_edge_finder.scripting.football import LeagueFitCache
from cfb_edge_finder.scripting.gamelog import TeamGame, parse_utc


class ControlMismatch(RuntimeError):
    """Production and research CONTROL rules disagreed on a replayed game."""


def schedule_meta(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Orientation and context from the frozen ESPN schedule (identity only; no score is read)."""
    meta = {}
    for event in events:
        sides = {c.get("home_away"): c for c in event.get("competitors") or []}
        if set(sides) != {"home", "away"}:
            continue
        meta[str(event["id"])] = {
            "home_id": str(sides["home"]["team_id"]),
            "away_id": str(sides["away"]["team_id"]),
            "neutral_site": bool(event.get("neutral_site")),
            "conference_game": event.get("conference_game"),
        }
    return meta


def replay_targets(
    rows: Iterable[TeamGame], meta: dict[str, dict[str, Any]], kickoff_before: str = V2_LEDGER_START
) -> tuple[list[replay.Target], dict[str, int]]:
    """Every game in the log with both teams present and oriented by the schedule, kicked off before the V2 ledger.

    Completion is established from the presence of both box-score rows; the
    score values themselves are not read here."""
    by_game: dict[str, dict[str, TeamGame]] = {}
    for row in rows:
        by_game.setdefault(row.game_id, {})[row.team_id] = row
    skipped = {"no_schedule_meta": 0, "missing_side": 0, "kickoff_not_before_v2_ledger": 0}
    limit = parse_utc(kickoff_before)
    out = []
    for game_id, sides in by_game.items():
        info = meta.get(game_id)
        if info is None:
            skipped["no_schedule_meta"] += 1
            continue
        home, away = sides.get(info["home_id"]), sides.get(info["away_id"])
        if home is None or away is None:
            skipped["missing_side"] += 1
            continue
        if not parse_utc(home.kickoff_utc) < limit:
            skipped["kickoff_not_before_v2_ledger"] += 1
            continue
        out.append(
            replay.Target(
                game_id=game_id,
                season=int(home.season),
                week=home.week,
                season_type="regular",
                kickoff_utc=home.kickoff_utc,
                home_id=home.team_id,
                away_id=away.team_id,
                home_name=home.team,
                away_name=away.team,
                neutral_site=bool(info.get("neutral_site")),
                conference_game=info.get("conference_game"),
                home_division=home.team_division,
                away_division=away.team_division,
            )
        )
    return sorted(out, key=lambda t: (t.kickoff_utc, t.game_id)), skipped


def replay_game(
    all_rows: Iterable[TeamGame], target: replay.Target, cache: LeagueFitCache, calibration: dict[str, Any]
) -> dict[str, Any]:
    """The frozen pregame football record and CONTROL claim for one replayed game. No outcome is read."""
    rows = tuple(all_rows)
    cutoff, history = replay.history_for(rows, target)
    content = replay.build_pregame_content(history, target, cache)
    if content["football_data_cutoff"] != cutoff:
        raise replay.LeakageError("production cutoff disagrees with the replay cutoff")
    pre = replay.pregame_record(content, target, cutoff, history)
    pre["eligibility"] = replay.eligibility(pre)
    names = {"home": target.home_name, "away": target.away_name}
    claims, _ = derive_claims(content["matchup_findings"], names, calibration)
    control = claims["control"]
    research = research_control_claim({f["code"]: f for f in pre["findings"]})
    prod_tier = control["tier"] if control else None
    if prod_tier != (research or {}).get("claim"):
        raise ControlMismatch(f"{target.game_id}: production {prod_tier} != research {research}")
    sus = next((f for f in pre["findings"] if f["code"].endswith("_SUSTAINED_EFFICIENCY_ADVANTAGE")), None)
    record = {
        "population": POP_REPLAY,
        "football_provenance": "RETROSPECTIVE_REPLAY_AT_PRODUCTION_CUTOFF",
        "methodology_version": METHODOLOGY_VERSION,
        "game_id": target.game_id,
        "season": target.season,
        "week": target.week,
        "kickoff_utc": target.kickoff_utc,
        "football_data_cutoff": cutoff,
        "history_games": pre["history_games"],
        "history_latest_kickoff": pre["history_latest_kickoff"],
        "home": target.home_name,
        "away": target.away_name,
        "home_id": target.home_id,
        "away_id": target.away_id,
        "neutral_site": target.neutral_site,
        "divisions": pre["divisions"],
        "fcs_involved": pre["fcs_involved"],
        "prior_games": pre["prior_games"],
        "data_confidence": pre["confidence_published"],
        "replay_eligibility": pre["eligibility"],
        "football_content_hash": pre["content_hash"],
        "pregame_record_hash": sha256(pre),
        "sustained_efficiency": (
            {"value": sus["value"], "uncertainty": sus["uncertainty"], "strength": sus["strength"], "side": sus["side"]}
            if sus
            else None
        ),
        "control": control_view(control, target),
    }
    record["football_record_hash"] = sha256({k: v for k, v in record.items()})
    return record


def control_view(control: dict[str, Any] | None, target: replay.Target) -> dict[str, Any] | None:
    if control is None:
        return None
    side = control["side"]
    return {
        "side": side,
        "strength": control["strength"],
        "tier": control["tier"],
        "team_id": target.home_id if side == "home" else target.away_id,
        "team": target.home_name if side == "home" else target.away_name,
        "opponent_id": target.away_id if side == "home" else target.home_id,
        "opponent": target.away_name if side == "home" else target.home_name,
    }


def prospective_rows(ledger_rows: Iterable[dict[str, Any]], completed_game_keys: set[str]) -> list[dict[str, Any]]:
    """PROSPECTIVE_V2_CONTROL: FINAL_PREGAME V2 ledger rows of completed games, copied verbatim (never recomputed)."""
    out = []
    for row in ledger_rows:
        if row.get("kind") != "FINAL_PREGAME" or row.get("methodology_version") != METHODOLOGY_VERSION:
            continue
        if row.get("game_key") not in completed_game_keys:
            continue
        control = (row.get("claims") or {}).get("control")
        teams = row.get("teams") or {}
        view = None
        if control:
            side = control["side"]
            other = "away" if side == "home" else "home"
            view = {
                "side": side,
                "strength": control["strength"],
                "tier": control["tier"],
                "team_id": str(teams[side]["team_id"]),
                "team": teams[side]["name"],
                "opponent_id": str(teams[other]["team_id"]),
                "opponent": teams[other]["name"],
            }
        out.append(
            {
                "population": POP_PROSPECTIVE,
                "football_provenance": "PROSPECTIVE_V2_LEDGER_FINAL_PREGAME",
                "methodology_version": row["methodology_version"],
                "game_id": str(row.get("event_id")),
                "game_key": row.get("game_key"),
                "kickoff_utc": row.get("kickoff_utc"),
                "home": (teams.get("home") or {}).get("name"),
                "away": (teams.get("away") or {}).get("name"),
                "home_id": str((teams.get("home") or {}).get("team_id")),
                "away_id": str((teams.get("away") or {}).get("team_id")),
                "data_confidence": row.get("data_quality"),
                "football_artifact_hash": row.get("football_artifact_hash"),
                "claims_artifact_hash": row.get("claims_artifact_hash"),
                "recorded_at": row.get("recorded_at"),
                "control": view,
            }
        )
    return out


def outcome_for(rows: Iterable[TeamGame], game_id: str, control_team_id: str) -> dict[str, Any]:
    """REVEAL ONLY: the final score of the game from the CONTROL side's perspective."""
    sides = {r.team_id: r for r in rows if r.game_id == game_id}
    own = sides.get(control_team_id)
    if own is None or own.points_for is None or own.points_against is None:
        return {"status": "SETTLEMENT_UNAVAILABLE", "reason": "score missing"}
    margin = float(own.points_for) - float(own.points_against)
    if margin == 0:
        return {"status": "SETTLEMENT_UNAVAILABLE", "reason": "tie"}
    return {
        "status": "SETTLED",
        "control_points": float(own.points_for),
        "opponent_points": float(own.points_against),
        "control_margin": margin,
        "control_won": margin > 0,
        "source": "frozen production game log (ESPN final score)",
    }
