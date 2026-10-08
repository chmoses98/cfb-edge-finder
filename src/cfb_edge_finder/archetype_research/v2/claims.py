"""V2 candidate claims from a frozen pregame record. PURE, football only, outcome-blind.

Every claim is read from the production findings already stored in the
frozen pregame record (`archetype_research.replay.pregame_record`). This
module receives no outcome, no box score of the target and no price: its
only input is the pregame record, and a test asserts it never reads the
`outcome` of a study record.

Dimensions are INDEPENDENT. A game may carry a directional CONTROL claim, a
CLOSENESS claim, a scoring environment, a pace claim, a defensive-suppression
claim and disruption claims at the same time; nothing here enforces a
single narrative.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cfb_edge_finder.archetype_research.v2 import EXPLOSIVE_UPSET_STATUS, V2_VERSION

SIDES = ("home", "away")
ELEVATED_ACTIVATORS = ("HIGH_SCORING_ENVIRONMENT", "BOTH_OFFENSES_EFFICIENT")
SUPPRESSED_ACTIVATORS = ("LOW_SCORING_ENVIRONMENT",)


def _other(side: str) -> str:
    return "away" if side == "home" else "home"


def _findings(pre: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["code"]: f for f in pre["findings"]}


def control_claim(f: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Direction + strength of the existing sustained-efficiency finding (at most one side has it)."""
    for side in SIDES:
        code = f"{side.upper()}_SUSTAINED_EFFICIENCY_ADVANTAGE"
        if code in f:
            tier = f[code]["strength"]
            return {"claim": f"{side.upper()}_CONTROL_{tier}", "side": side, "tier": tier, "evidence": [code]}
    return None


def closeness_claim(pre: dict[str, Any], f: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Resolved closeness evidence (production `closeness_grants_margin`, stored in the record)."""
    resolved = pre["closeness_resolved"]
    even = "EVEN_MATCHUP" in f and bool(resolved.get("EVEN_MATCHUP"))
    narrow = "NARROW_EFFICIENCY_GAP" in f and bool(resolved.get("NARROW_EFFICIENCY_GAP"))
    return {
        "CLOSENESS": even or narrow,
        "CLOSENESS_EVEN": even,
        "CLOSENESS_NARROW": narrow,
        "evidence": [c for c, on in (("EVEN_MATCHUP", even), ("NARROW_EFFICIENCY_GAP", narrow)) if on],
    }


def scoring_environment_claim(f: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """ELEVATED / SUPPRESSED from scoring findings only; pace and both-defenses-control are separate dimensions."""
    up = [c for c in ELEVATED_ACTIVATORS if c in f]
    down = [c for c in SUPPRESSED_ACTIVATORS if c in f]
    if up and down:
        return {"claim": None, "status": "MIXED", "activators": up + down}
    if up:
        strengthened = len(up) == 2 or f.get("HIGH_SCORING_ENVIRONMENT", {}).get("strength") == "STRONG"
        return {
            "claim": "ELEVATED_SCORING_ENVIRONMENT",
            "strengthened": strengthened,
            "activators": up,
            "supports": [c for c in ("HIGH_POSSESSION_ENVIRONMENT",) if c in f],
        }
    if down:
        strengthened = "BOTH_DEFENSES_CONTROL" in f or f["LOW_SCORING_ENVIRONMENT"]["strength"] == "STRONG"
        return {
            "claim": "SUPPRESSED_SCORING_ENVIRONMENT",
            "strengthened": strengthened,
            "activators": down,
            "supports": [c for c in ("LOW_POSSESSION_ENVIRONMENT",) if c in f],
        }
    return None


def pace_claim(f: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for code, claim in (("HIGH_POSSESSION_ENVIRONMENT", "PACE_HIGH"), ("LOW_POSSESSION_ENVIRONMENT", "PACE_LOW")):
        if code in f:
            return {"claim": claim, "strength": f[code]["strength"], "evidence": [code]}
    return None


def disruption_claims(f: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for side in SIDES:
        code = f"{side.upper()}_DISRUPTION_ADVANTAGE"
        if code not in f:
            continue
        volatility = [
            c
            for c in (
                f"{_other(side).upper()}_OFFENSE_TURNOVER_PRONE",
                f"{side.upper()}_DEFENSE_TURNOVER_RELIANT",
                "HIGH_VARIANCE_MATCHUP",
            )
            if c in f
        ]
        out.append(
            {
                "claim": f"{side.upper()}_DISRUPTION_EDGE",
                "side": side,
                "strength": f[code]["strength"],
                "volatility": bool(volatility),
                "volatility_findings": volatility,
                "side_has_efficiency_edge": f"{side.upper()}_SUSTAINED_EFFICIENCY_ADVANTAGE" in f,
                "opponent_has_efficiency_edge": f"{_other(side).upper()}_SUSTAINED_EFFICIENCY_ADVANTAGE" in f,
            }
        )
    return out


def v2_claims(pre: dict[str, Any]) -> dict[str, Any]:
    """Every V2 claim for one game, from its frozen pregame record alone."""
    f = _findings(pre)
    finishing_without_edge = any(f"{s.upper()}_FINISHING_ADVANTAGE" in f for s in SIDES) and not any(
        f"{s.upper()}_SUSTAINED_EFFICIENCY_ADVANTAGE" in f for s in SIDES
    )
    return {
        "v2_version": V2_VERSION,
        "game_id": pre["game_id"],
        "control": control_claim(f),
        "closeness": closeness_claim(pre, f),
        "scoring_environment": scoring_environment_claim(f),
        "pace": pace_claim(f),
        "defensive_suppression": {"claim": "DEFENSIVE_SUPPRESSION", "evidence": ["BOTH_DEFENSES_CONTROL"]}
        if "BOTH_DEFENSES_CONTROL" in f
        else None,
        "disruption": disruption_claims(f),
        "explosive_upset": {"status": EXPLOSIVE_UPSET_STATUS},
        "exploratory": {"finishing_advantage_without_efficiency_edge": finishing_without_edge},
        # Data quality is reported beside the claims and never changes them.
        "data_quality": {
            "confidence": pre["confidence"],
            "prior_games_min": min(pre["prior_games"]),
            "adjustment_stable": pre["adjustment_stable"],
        },
        "probability": None,
    }


def claims_hash(claims: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
