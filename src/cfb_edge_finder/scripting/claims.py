"""Script Engine V2: independent football claims from the frozen V1 artifact. PURE, market-blind.

Design of record: docs/SCRIPT_ENGINE_V2_MIGRATION.md.

*** A DIFFERENT READING OF THE SAME FINDINGS ***
V2 adds no metric and changes no threshold. Its only inputs are the frozen
V1 football content (findings, data-quality assessment, published V1
scripts) and the promoted CONTROL calibration artifact. It states claims
along INDEPENDENT dimensions instead of choosing mutually exclusive
narrative scripts:

  CONTROL                side + strength of the sustained-efficiency finding,
                         with the frozen historical margin range of that tier
  CLOSENESS              EVEN_MATCHUP that resolves (closeness_grants_margin)
  PACE                   HIGH / LOW possession environment
  SCORING_ENVIRONMENT    ELEVATED (incremental) / SUPPRESSED (descriptive only)
  DEFENSIVE_SUPPRESSION  BOTH_DEFENSES_CONTROL, with no GRIND exclusivity
  DISRUPTION_EDGE        per side, no volatility requirement, no winner authority
  EXPLOSIVE_UPSET        the V1 script, carried verbatim (historically untested)

Any combination may co-occur; no combination is a separately calibrated
archetype and no claim carries a probability. `story` renders the claims as
prose from fixed templates and cites every claim it uses; it adds nothing.

*** ACTIVATION IS NOT HERE ***
The artifact says `activation: SHADOW`. V1 remains the active publication.
"""

from __future__ import annotations

import hashlib
from typing import Any

from cfb_edge_finder.scripting import (
    CLAIMS_SCHEMA_VERSION,
    METHODOLOGY_V2_VERSION,
    SCRIPT_ARTIFACT_SCHEMA_VERSION,
)
from cfb_edge_finder.scripting.calibration import historical_range, reference
from cfb_edge_finder.scripting.findings import CATEGORY_DATA
from cfb_edge_finder.scripting.freeze import canonical_bytes
from cfb_edge_finder.scripting.scripts import closeness_grants_margin

SHADOW = "SHADOW"
CLAIMS_PUBLISHED = "CLAIMS_PUBLISHED"
NO_SUPPORTED_CLAIM = "NO_SUPPORTED_CLAIM"
NO_CLAIM_STATEMENT = "No supported matchup claim cleared the evidence requirements."
NO_CLAIM_MEANING = (
    "The current evidence taxonomy did not authorize a claim for this game. It does not mean the game is "
    "unusually unpredictable."
)
EXPLOSIVE_UPSET_STATUS = "UNTESTED_HISTORICALLY_DATA_UNAVAILABLE"

SIDES = ("home", "away")
STRENGTHS = ("MODERATE", "STRONG")
ELEVATED_ACTIVATORS = ("HIGH_SCORING_ENVIRONMENT", "BOTH_OFFENSES_EFFICIENT")
SUPPRESSED_ACTIVATORS = ("LOW_SCORING_ENVIRONMENT",)
PACE_ACTIVATORS = (("HIGH_POSSESSION_ENVIRONMENT", "HIGH"), ("LOW_POSSESSION_ENVIRONMENT", "LOW"))

#: Same-side unit findings CONTROL lists as drill-down context. Listed, never
#: weighed: the historical range is conditional on the control finding alone.
CONTROL_CONTEXT_STUBS = (
    "EARLY_DOWN_ADVANTAGE",
    "RUSH_ADVANTAGE",
    "PASS_ADVANTAGE",
    "FINISHING_ADVANTAGE",
    "EXPLOSIVE_ADVANTAGE",
    "PASS_EXPLOSIVE_ADVANTAGE",
    "RUSH_EXPLOSIVE_ADVANTAGE",
    "DISRUPTION_ADVANTAGE",
    "DEFENSIVE_CONTROL",
    "SCORING_ADVANTAGE",
)

DATA_QUALITY_DESCRIBES = (
    "the quality and completeness of the football evidence (coverage, sample, adjustment stability, availability, "
    "identity) -- never how likely a claim is to come true"
)
OUTCOME_STRENGTH_SOURCE = "CONTROL.strength"

#: Every V1 archetype and what V2 does with it (docs/SCRIPT_ENGINE_V2_MIGRATION.md section 4).
RETIRED_V1 = {
    "HOME_CONTROL": "CONTROL{side: home, strength}",
    "AWAY_CONTROL": "CONTROL{side: away, strength}",
    "FAVORITE_PULLS_AWAY": "RETIRED: folded into CONTROL/STRONG",
    "UNDERDOG_HANGS_AROUND": "RETIRED: no historical lift",
    "COMPETITIVE_TOSSUP": "RETIRED: replaced by CLOSENESS",
    "COMPETITIVE_SHOOTOUT": "RETIRED as a script: SCORING_ENVIRONMENT ELEVATED + PACE + CLOSENESS",
    "COMPETITIVE_GRIND": (
        "RETIRED as a script: SCORING_ENVIRONMENT SUPPRESSED + DEFENSIVE_SUPPRESSION + PACE + CLOSENESS"
    ),
    "PACE_DRIVEN_OVER": "PACE{level: HIGH} (no total band)",
    "DEFENSIVE_SUPPRESSION": "DEFENSIVE_SUPPRESSION (independent claim, no total band)",
    "TURNOVER_DISRUPTION": "DISRUPTION_EDGE (no volatility requirement, no winner, no band)",
    "EXPLOSIVE_UPSET": "EXPLOSIVE_UPSET (unchanged V1 script)",
}

#: The exact content keys of a frozen V2 claims artifact.
CLAIMS_KEYS = (
    "schema_version",
    "methodology_version",
    "activation",
    "source",
    "calibration",
    "event_id",
    "game_key",
    "season",
    "teams",
    "kickoff_utc",
    "football_data_cutoff",
    "market_blind",
    "data_quality",
    "outcome_strength_source",
    "status",
    "status_statement",
    "claims",
    "abstentions",
    "story",
    "retired_v1",
    "probability",
)


def _u(side: str) -> str:
    return side.upper()


def _other(side: str) -> str:
    return "away" if side == "home" else "home"


# --------------------------------------------------------------------------- claims


def control_claim(
    by_code: dict[str, dict[str, Any]], names: dict[str, str], calibration: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    present = [s for s in SIDES if f"{_u(s)}_SUSTAINED_EFFICIENCY_ADVANTAGE" in by_code]
    if not present:
        return None, []
    if len(present) > 1:
        return None, [{"family": "CONTROL", "reason": "CONTROL_BOTH_SIDES: sustained-efficiency edge on both sides"}]
    side = present[0]
    code = f"{_u(side)}_SUSTAINED_EFFICIENCY_ADVANTAGE"
    strength = by_code[code]["strength"]
    if strength not in STRENGTHS:
        return None, [{"family": "CONTROL", "reason": f"unknown finding strength {strength!r}"}]
    tier = f"{_u(side)}_CONTROL_{strength}"
    abstentions = []
    rng = historical_range(calibration, tier) if calibration is not None else None
    if rng is None:
        abstentions.append({"family": "CONTROL", "reason": f"no calibrated historical range for {tier}"})
    other = _other(side)
    return {
        "family": "CONTROL",
        "side": side,
        "strength": strength,
        "tier": tier,
        "evidence": [code],
        "statement": (
            f"{names[side]} owns the opponent-adjusted sustained-efficiency matchup ({strength.lower()} edge)."
        ),
        "context": {
            "rule": (
                "listed for drill-down; not weighed (the historical range is conditional on the control finding alone)"
            ),
            "same_side": [f"{_u(side)}_{s}" for s in CONTROL_CONTEXT_STUBS if f"{_u(side)}_{s}" in by_code],
            "opposing": [f"{_u(other)}_{s}" for s in CONTROL_CONTEXT_STUBS if f"{_u(other)}_{s}" in by_code],
        },
        "historical_range": rng,
    }, abstentions


def closeness_claim(by_code: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    abstentions = []
    if "NARROW_EFFICIENCY_GAP" in by_code:
        abstentions.append(
            {"family": "CLOSENESS", "reason": "NARROW_EFFICIENCY_GAP is informational; it never activates CLOSENESS"}
        )
    even = by_code.get("EVEN_MATCHUP")
    if even is None:
        return None, abstentions
    if not closeness_grants_margin(even):
        abstentions.append(
            {
                "family": "CLOSENESS",
                "reason": (
                    "EVEN_MATCHUP does not resolve: efficiency gap + uncertainty exceeds the strong-edge threshold"
                ),
            }
        )
        return None, abstentions
    return {
        "family": "CLOSENESS",
        "evidence": ["EVEN_MATCHUP"],
        "value": even.get("value"),
        "uncertainty": even.get("uncertainty"),
        "statement": "The evidence supports a relatively close game.",
        "claims_winner": False,
    }, abstentions


def pace_claim(by_code: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for code, level in PACE_ACTIVATORS:
        if code in by_code:
            direction = "more" if level == "HIGH" else "fewer"
            return {
                "family": "PACE",
                "level": level,
                "strength": by_code[code]["strength"],
                "evidence": [code],
                "statement": f"Expected possession volume: {direction} plays than an average FBS game.",
            }
    return None


def scoring_environment_claim(
    by_code: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    up = [c for c in ELEVATED_ACTIVATORS if c in by_code]
    down = [c for c in SUPPRESSED_ACTIVATORS if c in by_code]
    if up and down:
        return None, [
            {"family": "SCORING_ENVIRONMENT", "reason": f"MIXED: elevated {up} and suppressed {down} both present"}
        ]
    if up:
        high = by_code.get("HIGH_SCORING_ENVIRONMENT") or {}
        return {
            "family": "SCORING_ENVIRONMENT",
            "level": "ELEVATED",
            "strengthened": len(up) == 2 or high.get("strength") == "STRONG",
            "evidence": up,
            "supports": [c for c in ("HIGH_POSSESSION_ENVIRONMENT",) if c in by_code],
            "incremental_over_baseline": True,
            "statement": "Scoring environment: elevated, beyond what the opponent-adjusted scoring baseline expects.",
            "numeric_band": None,
        }, []
    if down:
        return {
            "family": "SCORING_ENVIRONMENT",
            "level": "SUPPRESSED",
            "strengthened": "BOTH_DEFENSES_CONTROL" in by_code or by_code[down[0]]["strength"] == "STRONG",
            "evidence": down,
            "supports": [c for c in ("LOW_POSSESSION_ENVIRONMENT",) if c in by_code],
            "incremental_over_baseline": False,
            "statement": (
                "Scoring environment: lower-scoring. The scoring baseline already expects this; the label adds no "
                "validated information beyond it."
            ),
            "numeric_band": None,
        }, []
    return None, []


def defensive_suppression_claim(by_code: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if "BOTH_DEFENSES_CONTROL" not in by_code:
        return None
    return {
        "family": "DEFENSIVE_SUPPRESSION",
        "evidence": ["BOTH_DEFENSES_CONTROL"],
        "statement": "Both defenses out-rate the offense they face: a lower-scoring tendency, stated without a number.",
        "numeric_band": None,
    }


def disruption_claims(
    by_code: dict[str, dict[str, Any]], names: dict[str, str], control: dict[str, Any] | None
) -> list[dict[str, Any]]:
    out = []
    for side in SIDES:
        code = f"{_u(side)}_DISRUPTION_ADVANTAGE"
        if code not in by_code:
            continue
        other = _other(side)
        candidates = (
            f"{_u(other)}_OFFENSE_TURNOVER_PRONE",
            f"{_u(side)}_DEFENSE_TURNOVER_RELIANT",
            "HIGH_VARIANCE_MATCHUP",
        )
        volatility = [c for c in candidates if c in by_code]
        out.append(
            {
                "family": "DISRUPTION_EDGE",
                "side": side,
                "strength": by_code[code]["strength"],
                "evidence": [code],
                "volatility_context": volatility,
                "aligned_with_control": None if control is None else control["side"] == side,
                "winner_authority": False,
                "statement": (
                    f"{names[side]} has a repeatable pressure/disruption advantage (sacks, tackles for loss, negative "
                    f"plays) against {names[other]}'s protection. It does not by itself say who wins."
                ),
            }
        )
    return out


def explosive_upset_claim(v1_scripts: list[dict[str, Any]]) -> dict[str, Any]:
    """The V1 EXPLOSIVE_UPSET script(s) exactly as the V1 artifact published them."""
    return {
        "status": EXPLOSIVE_UPSET_STATUS,
        "methodology": "V1 rule and V1 script, unchanged (historical explosive-play counts unavailable)",
        "scripts": [s for s in v1_scripts if s.get("archetype") == "EXPLOSIVE_UPSET"],
    }


def derive_claims(
    findings: list[dict[str, Any]],
    names: dict[str, str],
    calibration: dict[str, Any] | None,
    v1_scripts: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Every V2 claim from a game's findings. Data-quality findings never activate a claim."""
    by_code = {f["code"]: f for f in findings if f.get("category") != CATEGORY_DATA}
    control, a1 = control_claim(by_code, names, calibration)
    closeness, a2 = closeness_claim(by_code)
    scoring, a3 = scoring_environment_claim(by_code)
    claims = {
        "control": control,
        "closeness": closeness,
        "pace": pace_claim(by_code),
        "scoring_environment": scoring,
        "defensive_suppression": defensive_suppression_claim(by_code),
        "disruption": disruption_claims(by_code, names, control),
        "explosive_upset": explosive_upset_claim(v1_scripts or []),
    }
    return claims, a1 + a2 + a3


def has_claim(claims: dict[str, Any]) -> bool:
    return bool(
        claims["control"]
        or claims["closeness"]
        or claims["pace"]
        or claims["scoring_environment"]
        or claims["defensive_suppression"]
        or claims["disruption"]
        or claims["explosive_upset"]["scripts"]
    )


# --------------------------------------------------------------------------- story


def story(claims: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    """Prose from fixed templates in a fixed order. Every clause cites the claims it renders and
    states nothing they do not."""
    clauses: list[dict[str, Any]] = []
    environment: list[tuple[str, str]] = []
    pace = claims["pace"]
    if pace:
        faster = pace["level"] == "HIGH"
        environment.append(("a faster possession environment" if faster else "a slower possession environment", "pace"))
    scoring = claims["scoring_environment"]
    if scoring:
        environment.append(
            (
                "an elevated-scoring environment"
                if scoring["level"] == "ELEVATED"
                else "a lower-scoring environment the baseline already expects",
                "scoring_environment",
            )
        )
    if claims["defensive_suppression"]:
        environment.append(("defensive suppression", "defensive_suppression"))
    env_text = _join([t for t, _ in environment])
    env_keys = [k for _, k in environment]

    control, closeness = claims["control"], claims["closeness"]
    subject, subject_keys = None, []
    if control:
        subject = f"{names[control['side']]} holds a {control['strength'].lower()} sustained-efficiency control edge"
        subject_keys.append("control")
    if closeness:
        subject = "Close-game profile" if subject is None else f"{subject}; the evidence also supports a close game"
        subject_keys.append("closeness")
    if subject is not None:
        clauses.append(
            {"text": f"{subject}, with {env_text}." if env_text else f"{subject}.", "claims": subject_keys + env_keys}
        )
    elif env_text:
        clauses.append(
            {"text": f"No side or closeness claim cleared; the game profile shows {env_text}.", "claims": env_keys}
        )
    for d in claims["disruption"]:
        name = names[d["side"]]
        text = (
            f"{name}'s disruption edge points the same way."
            if d["aligned_with_control"]
            else f"{name} carries a disruption edge (no winner implied)."
        )
        clauses.append({"text": text, "claims": [f"disruption.{d['side']}"]})
    for s in claims["explosive_upset"]["scripts"]:
        clauses.append(
            {
                "text": (
                    f"{names[s['lead_side']]} keeps the explosive-play upset path (V1 rule; untested historically)."
                ),
                "claims": ["explosive_upset"],
            }
        )
    if not clauses:
        clauses.append({"text": NO_CLAIM_STATEMENT, "claims": []})
    return {
        "headline": " ".join(c["text"] for c in clauses),
        "clauses": clauses,
        "generated_from": "V2 claims only; deterministic templates; no market input; not a calibrated archetype",
    }


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


# --------------------------------------------------------------------------- artifact


def build_claims(football: dict[str, Any], calibration: dict[str, Any]) -> dict[str, Any]:
    """The V2 claims content for one game, from its frozen V1 football ENVELOPE alone."""
    content = football["content"]
    if content.get("schema_version") != SCRIPT_ARTIFACT_SCHEMA_VERSION:
        raise ValueError(f"unsupported football artifact schema {content.get('schema_version')!r}")
    teams = content["teams"]
    names = {side: teams[side]["name"] for side in SIDES}
    v1_scripts = (content.get("game_scripts") or {}).get("scripts") or []
    claims, abstentions = derive_claims(content["matchup_findings"], names, calibration, v1_scripts)
    published = has_claim(claims)
    confidence = content["confidence"]
    return {
        "schema_version": CLAIMS_SCHEMA_VERSION,
        "methodology_version": METHODOLOGY_V2_VERSION,
        "activation": SHADOW,
        "source": {
            "football_artifact_hash": football["artifact_hash"],
            "football_methodology_version": content["methodology_version"],
            "football_schema_version": content["schema_version"],
        },
        "calibration": reference(calibration),
        "event_id": content["event_id"],
        "game_key": content["game_key"],
        "season": content["season"],
        "teams": teams,
        "kickoff_utc": content["kickoff_utc"],
        "football_data_cutoff": content["football_data_cutoff"],
        "market_blind": True,
        "data_quality": {
            "level": confidence["level"],
            "describes": DATA_QUALITY_DESCRIBES,
            "all_gates_pass": confidence["all_gates_pass"],
            "reasons": confidence["reasons"],
        },
        "outcome_strength_source": OUTCOME_STRENGTH_SOURCE,
        "status": CLAIMS_PUBLISHED if published else NO_SUPPORTED_CLAIM,
        "status_statement": None if published else NO_CLAIM_STATEMENT,
        "claims": claims,
        "abstentions": abstentions,
        "story": story(claims, names),
        "retired_v1": RETIRED_V1,
        "probability": None,
    }


def claims_hash(content: dict[str, Any]) -> str:
    missing = [k for k in CLAIMS_KEYS if k not in content]
    extra = sorted(set(content) - set(CLAIMS_KEYS))
    if missing or extra:
        raise ValueError(f"V2 claims content: missing {missing}, unexpected {extra}")
    return hashlib.sha256(canonical_bytes(content)).hexdigest()


def freeze_claims(
    content: dict[str, Any], *, generated_at: str, previous_envelope: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Wrap V2 content in an envelope; an unchanged picture keeps its hash and first `generated_at`."""
    digest = claims_hash(content)
    if (
        previous_envelope is not None
        and previous_envelope.get("artifact_hash") == digest
        and str(previous_envelope.get("generated_at") or "") <= generated_at
    ):
        return previous_envelope
    previous = (previous_envelope or {}).get("content")
    reasons = ["FIRST_PUBLICATION"] if previous is None else _claims_reasons(previous, content)
    history = list((previous_envelope or {}).get("regeneration", {}).get("history") or [])
    if previous_envelope is not None:
        history.append(
            {
                "replaced_hash": previous_envelope.get("artifact_hash"),
                "replaced_generated_at": previous_envelope.get("generated_at"),
                "reasons": reasons,
                "at": generated_at,
            }
        )
    return {
        "artifact_hash": digest,
        "generated_at": generated_at,
        "regeneration": {"reasons": reasons, "history": history[-20:]},
        "content": content,
    }


def _claims_reasons(previous: dict[str, Any], content: dict[str, Any]) -> list[str]:
    reasons = []
    if previous.get("methodology_version") != content.get("methodology_version"):
        reasons.append("METHODOLOGY_CHANGED")
    if (previous.get("calibration") or {}).get("sha256") != (content.get("calibration") or {}).get("sha256"):
        reasons.append("CALIBRATION_CHANGED")
    if (previous.get("source") or {}).get("football_artifact_hash") != content["source"]["football_artifact_hash"]:
        reasons.append("FOOTBALL_ARTIFACT_CHANGED")
    return reasons or ["CONTENT_CHANGED_WITHOUT_INPUT_CHANGE"]


def verify_claims(envelope: dict[str, Any]) -> bool:
    try:
        return claims_hash(envelope["content"]) == envelope["artifact_hash"]
    except (KeyError, ValueError, TypeError):
        return False
