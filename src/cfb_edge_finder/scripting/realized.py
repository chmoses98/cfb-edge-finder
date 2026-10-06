"""What actually happened: realized-game features, archetype and script scoring. PURE.

Runs only on COMPLETED games, from the same box-score/play-log rows the
engine learns from -- no human marks a game. Two separate questions:

1. `classify` -- which archetype(s) does the realized game objectively fit?
   Rules use the realized stats plus, where an archetype is defined relative
   to the pregame picture (an "upset" needs a pregame lead side), the FROZEN
   artifact's own lead side and scoring baseline. A game that fits nothing
   cleanly is AMBIGUOUS.

2. `score_scripts` -- did each FROZEN script describe the game? A script
   described the game when every band it stated (margin, total, team points)
   contains the realized value AND its mechanism check holds (e.g. a control
   script's lead side actually won the down-to-down efficiency battle).
   Bands it did not state are not held against it.

`score_findings` checks each matchup finding's direction against the game.

None of this can alter a frozen artifact; the ledger stores hashes.
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.scripting import REALIZED_SCHEMA_VERSION
from cfb_edge_finder.scripting.gamelog import TeamGame

ONE_SCORE = 8
AMBIGUOUS = "AMBIGUOUS"


def _rate(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den


def team_features(row: TeamGame) -> dict[str, Any]:
    box, pbp = row.box, row.pbp or {}
    return {
        "points": row.points_for,
        "plays": box.get("plays"),
        "yards_per_play": _rate(box.get("total_yards"), box.get("plays")),
        "success_rate": _rate(pbp.get("success_plays"), pbp.get("scrim_plays")),
        "rush_success_rate": _rate(pbp.get("rush_success"), pbp.get("rush_plays")),
        "pass_success_rate": _rate(pbp.get("pass_success"), pbp.get("pass_plays")),
        "yards_per_rush": _rate(box.get("rush_yards"), box.get("rush_att")),
        "yards_per_pass_attempt": _rate(box.get("pass_yards"), box.get("pass_att")),
        "explosive_yard_share": _rate(pbp.get("explosive_yards"), pbp.get("scrim_yards")),
        "explosive_play_rate": _rate(
            (pbp.get("explosive_rush") or 0) + (pbp.get("explosive_pass") or 0) if row.pbp else None,
            pbp.get("scrim_plays"),
        ),
        "points_per_drive": _rate(pbp.get("drive_points"), pbp.get("drives")),
        "turnovers": box.get("turnovers"),
        "sacks_made": box.get("def_sacks"),
        "tfl_made": box.get("def_tfl"),
    }


def realized_features(home: TeamGame, away: TeamGame) -> dict[str, Any]:
    h, a = team_features(home), team_features(away)
    margin = (home.points_for or 0) - (away.points_for or 0)
    total = (home.points_for or 0) + (away.points_for or 0)
    eff_key = "success_rate" if h["success_rate"] is not None and a["success_rate"] is not None else "yards_per_play"
    return {
        "game_id": home.game_id,
        "home": h,
        "away": a,
        "home_margin": margin,
        "total_points": total,
        "home_points": home.points_for,
        "away_points": away.points_for,
        "total_plays": (h["plays"] or 0) + (a["plays"] or 0),
        "efficiency_basis": eff_key,
        "efficiency_winner": (
            "home"
            if (h[eff_key] or 0) > (a[eff_key] or 0)
            else ("away" if (a[eff_key] or 0) > (h[eff_key] or 0) else None)
        ),
    }


def _winner(margin: float) -> str | None:
    return "home" if margin > 0 else ("away" if margin < 0 else None)


def classify(features: dict[str, Any], baseline: dict[str, Any], lead_side: str | None) -> dict[str, Any]:
    margin, eff_winner = features["home_margin"], features["efficiency_winner"]
    winner = _winner(margin)
    abs_margin = abs(margin)
    hp, ap = features["home_points"], features["away_points"]
    bh, ba = baseline.get("home_points"), baseline.get("away_points")
    labels: list[str] = []

    if abs_margin <= ONE_SCORE:
        if bh is not None and ba is not None and hp >= bh + 2 and ap >= ba + 2:
            labels.append("COMPETITIVE_SHOOTOUT")
        elif bh is not None and ba is not None and hp <= bh - 2 and ap <= ba - 2:
            labels.append("COMPETITIVE_GRIND")
        else:
            labels.append("COMPETITIVE_TOSSUP")
    if winner and 7 <= abs_margin <= 24 and eff_winner == winner:
        labels.append(f"{winner.upper()}_CONTROL")
    if winner and abs_margin >= 17 and eff_winner == winner and (lead_side is None or lead_side == winner):
        labels.append("FAVORITE_PULLS_AWAY")
    if winner and lead_side and winner != lead_side:
        w, l_ = features[winner], features[lead_side]
        if (
            w["explosive_yard_share"] is not None
            and l_["explosive_yard_share"] is not None
            and w["explosive_yard_share"] - l_["explosive_yard_share"] >= 0.10
            and eff_winner != winner
        ):
            labels.append("EXPLOSIVE_UPSET")
    if winner:
        loser = "away" if winner == "home" else "home"
        w, l_ = features[winner], features[loser]
        if (l_["turnovers"] or 0) - (w["turnovers"] or 0) >= 2 and (
            (w["sacks_made"] or 0) + (w["tfl_made"] or 0) >= (l_["sacks_made"] or 0) + (l_["tfl_made"] or 0) + 3
        ):
            labels.append("TURNOVER_DISRUPTION")
    if lead_side and abs_margin <= ONE_SCORE:
        labels.append("UNDERDOG_HANGS_AROUND")
    labels = list(dict.fromkeys(labels))
    return {"labels": labels or [AMBIGUOUS], "primary": labels[0] if labels else AMBIGUOUS}


def _in(band: list[int] | None, value: float | None) -> bool | None:
    if band is None or value is None:
        return None
    return band[0] <= value <= band[1]


def _mechanism(script: dict[str, Any], features: dict[str, Any]) -> bool | None:
    arch, lead = script["archetype"], script.get("lead_side")
    eff = features["efficiency_winner"]
    if arch in ("HOME_CONTROL", "AWAY_CONTROL", "FAVORITE_PULLS_AWAY", "TURNOVER_DISRUPTION"):
        return eff == lead if lead else None
    if arch == "EXPLOSIVE_UPSET":
        other = "away" if lead == "home" else "home"
        a, b = features[lead]["explosive_yard_share"], features[other]["explosive_yard_share"]
        return None if a is None or b is None else a > b
    if arch == "UNDERDOG_HANGS_AROUND":
        return abs(features["home_margin"]) <= ONE_SCORE
    return None


def score_scripts(scripts: list[dict[str, Any]], features: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for script in scripts:
        bands = script["outcome_shape"].get("bands") or {}
        checks = {
            "home_margin": _in(bands.get("home_margin"), features["home_margin"]),
            "total_points": _in(bands.get("total_points"), features["total_points"]),
            "home_points": _in(bands.get("home_points"), features["home_points"]),
            "away_points": _in(bands.get("away_points"), features["away_points"]),
            "mechanism": _mechanism(script, features),
        }
        stated = [v for v in checks.values() if v is not None]
        out.append(
            {
                "script_id": script["script_id"],
                "role": script["role"],
                "archetype": script["archetype"],
                "checks": checks,
                "described": bool(stated) and all(stated),
                "checks_stated": len(stated),
            }
        )
    return out


_FINDING_METRIC = {
    "SUSTAINED_EFFICIENCY_ADVANTAGE": "success_rate|yards_per_play",
    "EARLY_DOWN_ADVANTAGE": "success_rate|yards_per_play",
    "FINISHING_ADVANTAGE": "points_per_drive",
    "SCORING_ADVANTAGE": "points",
    "EXPLOSIVE_ADVANTAGE": "explosive_play_rate",
    "RUSH_ADVANTAGE": "rush_success_rate|yards_per_rush",
    "PASS_ADVANTAGE": "pass_success_rate|yards_per_pass_attempt",
    "PASS_EXPLOSIVE_ADVANTAGE": "explosive_play_rate",
    "RUSH_EXPLOSIVE_ADVANTAGE": "explosive_play_rate",
    "DEFENSIVE_CONTROL": "success_rate|yards_per_play",
}


def _pick(features: dict[str, Any], side: str, spec: str) -> float | None:
    for key in spec.split("|"):
        value = features[side].get(key)
        if value is not None:
            return value
    return None


def score_findings(
    findings: list[dict[str, Any]], features: dict[str, Any], league_plays: float | None
) -> list[dict[str, Any]]:
    """Was each finding's DIRECTION right in this game? None = not checkable."""
    out = []
    for f in findings:
        code, side = f["code"], f["side"]
        correct: bool | None = None
        if side in ("home", "away"):
            stub = code.split("_", 1)[1]
            spec = _FINDING_METRIC.get(stub)
            other = "away" if side == "home" else "home"
            if spec and stub == "DEFENSIVE_CONTROL":
                a, b = _pick(features, other, spec), _pick(features, side, spec)
                correct = None if a is None or b is None else b > a
            elif spec:
                a, b = _pick(features, side, spec), _pick(features, other, spec)
                correct = None if a is None or b is None else a > b
            elif stub == "DISRUPTION_ADVANTAGE":
                mine = (features[side]["sacks_made"] or 0) + (features[side]["tfl_made"] or 0)
                theirs = (features[other]["sacks_made"] or 0) + (features[other]["tfl_made"] or 0)
                correct = mine > theirs
        elif code in ("HIGH_POSSESSION_ENVIRONMENT", "LOW_POSSESSION_ENVIRONMENT") and league_plays:
            high = features["total_plays"] > 2 * league_plays
            correct = high if code.startswith("HIGH") else not high
        out.append({"code": code, "correct": correct})
    return out


def realized_record(
    home: TeamGame, away: TeamGame, ledger_row: dict[str, Any], league_plays: float | None
) -> dict[str, Any]:
    features = realized_features(home, away)
    primary = next((s for s in ledger_row["scripts"] if s["role"] == "PRIMARY"), None)
    lead = None
    if primary is not None:
        lean = primary["outcome_shape"].get("winner_lean")
        lead = lean.lower() if lean in ("HOME", "AWAY") else None
        if primary["archetype"] in ("UNDERDOG_HANGS_AROUND", "EXPLOSIVE_UPSET"):
            lead = "away" if primary["lead_side"] == "home" else "home"
    scored = score_scripts(ledger_row["scripts"], features)
    return {
        "schema_version": REALIZED_SCHEMA_VERSION,
        "game_key": ledger_row["game_key"],
        "event_id": ledger_row["event_id"],
        "artifact_hash": ledger_row["artifact_hash"],
        "features": features,
        "realized_archetype": classify(features, ledger_row.get("scoring_baseline") or {}, lead),
        "scripts": scored,
        "primary_described": any(s["described"] for s in scored if s["role"] == "PRIMARY"),
        "primary_or_secondary_described": any(s["described"] for s in scored if s["role"] in ("PRIMARY", "SECONDARY")),
        "any_script_described": any(s["described"] for s in scored),
        "findings": score_findings(ledger_row.get("findings") or [], features, league_plays),
    }


def settle_expression(wins_when: dict[str, Any] | None, features: dict[str, Any]) -> bool | None:
    """Did this side of the contract pay, from its exact win set and the final score."""
    if not wins_when:
        return None
    variable = str(wins_when.get("variable") or "")
    quantity = variable.split(":", 1)[-1]
    value = {
        "home_margin": features["home_margin"],
        "total_points": features["total_points"],
        "home_points": features["home_points"],
        "away_points": features["away_points"],
    }.get(quantity)
    if value is None:
        return None
    lo, hi = wins_when.get("at_least"), wins_when.get("at_most")
    return (lo is None or value >= lo) and (hi is None or value <= hi)
