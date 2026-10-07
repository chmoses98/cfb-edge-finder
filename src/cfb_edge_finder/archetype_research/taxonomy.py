"""Realized taxonomy and script scoring for the historical study. RESEARCH ONLY.

Production rules are reused unchanged (`scripting.realized.realized_features`,
`classify`, `score_scripts`). What this module adds, as pre-registered in
docs/ARCHETYPE_HISTORICAL_PROTOCOL.md section 4-5:

* a REFERENCE lead side for every game (the sign of the pregame
  sustained-efficiency net), so realized labels exist for every game and not
  only for games where a PRIMARY happened to be published;
* two research environment descriptors for the scripts production
  `realized.py` never classifies (PACE_DRIVEN_OVER, DEFENSIVE_SUPPRESSION);
* the three realization yardsticks: LABEL_MATCH (primary), DEFINITION_CHECK,
  PRODUCTION_DESCRIBED.

Everything here runs after `replay.reveal`; it never feeds a pregame record.
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.scripting.gamelog import TeamGame
from cfb_edge_finder.scripting.realized import AMBIGUOUS, classify, realized_features, score_scripts

PRODUCTION_LABELS = (
    "HOME_CONTROL",
    "AWAY_CONTROL",
    "FAVORITE_PULLS_AWAY",
    "UNDERDOG_HANGS_AROUND",
    "EXPLOSIVE_UPSET",
    "TURNOVER_DISRUPTION",
    "COMPETITIVE_SHOOTOUT",
    "COMPETITIVE_GRIND",
    "COMPETITIVE_TOSSUP",
)
ENVIRONMENT_LABELS = ("PACE_DRIVEN_OVER", "DEFENSIVE_SUPPRESSION")
ALL_ARCHETYPES = PRODUCTION_LABELS + ENVIRONMENT_LABELS

#: Archetypes whose realized label belongs to one side of the game.
SIDED = {"FAVORITE_PULLS_AWAY", "UNDERDOG_HANGS_AROUND", "EXPLOSIVE_UPSET", "TURNOVER_DISRUPTION"}


def other(side: str | None) -> str | None:
    return None if side is None else ("away" if side == "home" else "home")


def _winner(margin: float) -> str | None:
    return "home" if margin > 0 else ("away" if margin < 0 else None)


def environment_labels(features: dict[str, Any], pre: dict[str, Any]) -> list[str]:
    """PACE_DRIVEN_OVER / DEFENSIVE_SUPPRESSION realized descriptors (protocol section 4)."""
    out = []
    norms, base_total = pre["league_norms"], pre["baseline"]["total_points"]
    total = features["total_points"]
    plays_mu = norms.get("plays_per_game")
    if plays_mu and base_total is not None and features["total_plays"] > 2 * plays_mu and total > base_total:
        out.append("PACE_DRIVEN_OVER")
    basis = features["efficiency_basis"]
    mu = norms.get(basis)
    h, a = features["home"].get(basis), features["away"].get(basis)
    if mu is not None and h is not None and a is not None and base_total is not None:
        if h < mu and a < mu and total < base_total:
            out.append("DEFENSIVE_SUPPRESSION")
    return out


def label_sides(labels: list[str], features: dict[str, Any], lead: str | None) -> dict[str, str | None]:
    """The side each realized label belongs to (None = the whole game)."""
    winner = _winner(features["home_margin"])
    sides: dict[str, str | None] = {}
    for label in labels:
        if label == "HOME_CONTROL":
            sides[label] = "home"
        elif label == "AWAY_CONTROL":
            sides[label] = "away"
        elif label in ("FAVORITE_PULLS_AWAY", "EXPLOSIVE_UPSET", "TURNOVER_DISRUPTION"):
            sides[label] = winner
        elif label == "UNDERDOG_HANGS_AROUND":
            sides[label] = other(lead)
        else:
            sides[label] = None
    return sides


def realize(pre: dict[str, Any], home: TeamGame, away: TeamGame, lead_basis: str = "reference_lead") -> dict:
    """Multi-label realized classification of one game, under the pre-registered reference lead."""
    features = realized_features(home, away)
    lead = pre[lead_basis]
    prod = classify(features, pre["baseline"], lead)
    labels = [x for x in prod["labels"] if x != AMBIGUOUS]
    env = environment_labels(features, pre)
    return {
        "lead_basis": lead_basis,
        "lead": lead,
        "labels": labels,
        "primary": prod["primary"],
        "ambiguous": not labels,
        "environment": env,
        "sides": label_sides(labels + env, features, lead),
    }


def production_faithful(pre: dict[str, Any], features: dict[str, Any]) -> dict[str, Any] | None:
    """Labels exactly as `realized.realized_record` would assign them: lead from the PRIMARY."""
    primary = next((s for s in pre["scripts"] if s["role"] == "PRIMARY"), None)
    if primary is None:
        return None
    lean = primary["outcome_shape"].get("winner_lean")
    lead = lean.lower() if lean in ("HOME", "AWAY") else None
    if primary["archetype"] in ("UNDERDOG_HANGS_AROUND", "EXPLOSIVE_UPSET"):
        lead = other(primary["lead_side"])
    return classify(features, pre["baseline"], lead)


def label_match(archetype: str, side: str | None, realized: dict[str, Any]) -> bool:
    """Does the realized label set contain this archetype, on this side where sidedness applies?"""
    labels = realized["labels"] + realized["environment"]
    if archetype not in labels:
        return False
    if archetype in SIDED:
        return realized["sides"].get(archetype) == side
    return True


def oriented_side(archetype: str, pre: dict[str, Any], lead_basis: str = "reference_lead") -> str | None:
    """How a script of this archetype would be oriented in this game (for base rates)."""
    lead = pre[lead_basis]
    if archetype == "HOME_CONTROL":
        return "home"
    if archetype == "AWAY_CONTROL":
        return "away"
    if archetype == "FAVORITE_PULLS_AWAY":
        return lead
    if archetype in ("UNDERDOG_HANGS_AROUND", "EXPLOSIVE_UPSET"):
        return other(lead)
    return None


def definition_check(script: dict[str, Any], features: dict[str, Any], pre: dict[str, Any]) -> bool | None:
    """Production score_scripts on the archetype-definition checks only (protocol section 5.2)."""
    shape = script["outcome_shape"]
    margin_only = {
        **script,
        "outcome_shape": {**shape, "bands": {"home_margin": (shape.get("bands") or {}).get("home_margin")}},
    }
    checks = score_scripts([margin_only], features)[0]["checks"]
    stated = [v for k, v in checks.items() if k in ("home_margin", "mechanism") and v is not None]
    if stated:
        return all(stated)
    base_total = pre["baseline"]["total_points"]
    if base_total is None:
        return None
    env = shape.get("total_environment")
    if env == "ELEVATED":
        return features["total_points"] > base_total
    if env == "SUPPRESSED":
        return features["total_points"] < base_total
    return None


def score_game(pre: dict[str, Any], home: TeamGame, away: TeamGame) -> dict[str, Any]:
    """Every realized quantity the study needs for one revealed game."""
    features = realized_features(home, away)
    realized = realize(pre, home, away)
    sensitivity = realize(pre, home, away, lead_basis="baseline_lead")
    production = score_scripts(pre["scripts"], features)
    scripts = []
    for script, prod in zip(pre["scripts"], production, strict=True):
        scripts.append(
            {
                "role": script["role"],
                "archetype": script["archetype"],
                "lead_side": script["lead_side"],
                "evidence_score": script["evidence_score"],
                "label_match": label_match(script["archetype"], script["lead_side"], realized),
                "definition_check": definition_check(script, features, pre),
                "production_described": prod["described"],
                "production_checks": prod["checks"],
            }
        )
    faithful = production_faithful(pre, features)
    return {
        "features": _compact_features(features, home, away),
        "realized": realized,
        "realized_baseline_lead": {k: sensitivity[k] for k in ("lead", "labels", "primary", "environment", "sides")},
        "production_faithful": faithful,
        "scripts": scripts,
    }


def _r(x: float | None, places: int = 4) -> float | None:
    return None if x is None else round(float(x), places)


def _compact_features(features: dict[str, Any], home: TeamGame, away: TeamGame) -> dict[str, Any]:
    """Per-game realized features kept in the row-level output."""
    out: dict[str, Any] = {
        "home_points": features["home_points"],
        "away_points": features["away_points"],
        "home_margin": features["home_margin"],
        "total_points": features["total_points"],
        "total_plays": features["total_plays"],
        "efficiency_basis": features["efficiency_basis"],
        "efficiency_winner": features["efficiency_winner"],
    }
    sr = {s: features[s]["success_rate"] for s in ("home", "away")}
    out["success_rate_winner"] = (
        None if None in sr.values() or sr["home"] == sr["away"] else ("home" if sr["home"] > sr["away"] else "away")
    )
    for side, row in (("home", home), ("away", away)):
        f = features[side]
        pbp = row.pbp or {}
        out[side] = {
            "success_rate": _r(f["success_rate"]),
            "yards_per_play": _r(f["yards_per_play"]),
            "explosive_play_rate": _r(f["explosive_play_rate"]),
            "explosive_yard_share": _r(f["explosive_yard_share"]),
            "points_per_drive": _r(f["points_per_drive"]),
            "points_per_opportunity": _r(
                (pbp.get("opp_points") / pbp["scoring_opps"]) if pbp.get("scoring_opps") else None
            ),
            "turnovers": f["turnovers"],
            "sacks_tfl_made": (f["sacks_made"] or 0) + (f["tfl_made"] or 0)
            if f["sacks_made"] is not None or f["tfl_made"] is not None
            else None,
            "plays": f["plays"],
            "has_play_log": row.pbp is not None,
        }
    return out
