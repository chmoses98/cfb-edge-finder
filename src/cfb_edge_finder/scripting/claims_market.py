"""V2 claim <-> market authority. MARKET SIDE: runs only on frozen V1 and V2 artifacts.

Design of record: docs/SCRIPT_ENGINE_V2_MIGRATION.md section 10.

*** CONSERVATIVE BY CONSTRUCTION ***
For each mapped contract side, V2 decides which football authority -- if
any -- a claim has over it:

  DIRECT_FOOTBALL_AUTHORITY  CONTROL x moneyline: DIRECTIONAL only. The
                             contract pays on the control side winning
                             (ALIGNED) or not (OPPOSED). Not a probability.
  RESEARCH_CONTEXT_ONLY      CONTROL x spread / alt spread / margin band: the
                             control tier's historical empirical range is
                             shown beside the contract. No per-contract
                             relation is computed, nothing is scored -- an
                             interval endpoint is not a pays/doesn't-pay line.
  RESEARCH_UNCALIBRATED      every total and team-total contract: environment
                             claims (pace, scoring, defensive suppression) are
                             qualitative context; no band exists to classify
                             against, and the scoring-band promotion gate has
                             not passed.
  NO_MARKET_AUTHORITY        everything else: CLOSENESS and DISRUPTION_EDGE on
                             every family, any claim on a family it says
                             nothing about.
  UNMAPPABLE                 the V1 map could not reduce the contract to one
                             full-game interval.

EXPLOSIVE_UPSET keeps its V1 classification verbatim (the V1 map's status
for that script), because its V1 behaviour is unchanged.

There is no survival score, no label and no "best expression" here, and no
price is read: the V1 map's settlement semantics (`kind`, `wins_when`) are
the only market input. The V1 market map remains the active one.
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.scripting import CLAIMS_MARKET_SCHEMA_VERSION
from cfb_edge_finder.scripting.claims import verify_claims
from cfb_edge_finder.scripting.gamelog import parse_utc
from cfb_edge_finder.scripting.market_map import COMPAT_CODES, MappingOrderError

DIRECT = "DIRECT_FOOTBALL_AUTHORITY"
RESEARCH_CONTEXT = "RESEARCH_CONTEXT_ONLY"
RESEARCH_UNCALIBRATED = "RESEARCH_UNCALIBRATED"
NO_AUTHORITY = "NO_MARKET_AUTHORITY"
UNMAPPABLE = "UNMAPPABLE"
AUTHORITIES = (DIRECT, RESEARCH_CONTEXT, RESEARCH_UNCALIBRATED, NO_AUTHORITY, UNMAPPABLE)

ALIGNED = "ALIGNED"
OPPOSED = "OPPOSED"

MARKET_FAMILIES = ("moneyline", "spread", "total", "team_total")
CLAIM_FAMILIES = (
    "CONTROL",
    "CLOSENESS",
    "PACE",
    "SCORING_ENVIRONMENT",
    "DEFENSIVE_SUPPRESSION",
    "DISRUPTION_EDGE",
    "EXPLOSIVE_UPSET",
)

#: The decision table, published with every map so a consumer never infers it.
POLICY: dict[str, dict[str, str]] = {
    "moneyline": {
        "CONTROL": DIRECT,
        "CLOSENESS": NO_AUTHORITY,
        "PACE": NO_AUTHORITY,
        "SCORING_ENVIRONMENT": NO_AUTHORITY,
        "DEFENSIVE_SUPPRESSION": NO_AUTHORITY,
        "DISRUPTION_EDGE": NO_AUTHORITY,
        "EXPLOSIVE_UPSET": "V1_UNCHANGED",
    },
    "spread": {
        "CONTROL": RESEARCH_CONTEXT,
        "CLOSENESS": NO_AUTHORITY,
        "PACE": NO_AUTHORITY,
        "SCORING_ENVIRONMENT": NO_AUTHORITY,
        "DEFENSIVE_SUPPRESSION": NO_AUTHORITY,
        "DISRUPTION_EDGE": NO_AUTHORITY,
        "EXPLOSIVE_UPSET": "V1_UNCHANGED",
    },
    "total": {
        "CONTROL": NO_AUTHORITY,
        "CLOSENESS": NO_AUTHORITY,
        "PACE": RESEARCH_UNCALIBRATED,
        "SCORING_ENVIRONMENT": RESEARCH_UNCALIBRATED,
        "DEFENSIVE_SUPPRESSION": RESEARCH_UNCALIBRATED,
        "DISRUPTION_EDGE": NO_AUTHORITY,
        "EXPLOSIVE_UPSET": "V1_UNCHANGED",
    },
    "team_total": {
        "CONTROL": NO_AUTHORITY,
        "CLOSENESS": NO_AUTHORITY,
        "PACE": NO_AUTHORITY,
        "SCORING_ENVIRONMENT": RESEARCH_UNCALIBRATED,
        "DEFENSIVE_SUPPRESSION": RESEARCH_UNCALIBRATED,
        "DISRUPTION_EDGE": NO_AUTHORITY,
        "EXPLOSIVE_UPSET": "V1_UNCHANGED",
    },
}
POLICY_RULE = (
    "Only CONTROL x moneyline carries direct football authority, and only as a direction (ALIGNED/OPPOSED). "
    "Historical CONTROL ranges are context beside spreads, never a classification. Totals and team totals stay "
    "RESEARCH_UNCALIBRATED. No relation here is a probability, a fair price or an expected value."
)


def _market_family(kind: str | None, variable: str) -> str | None:
    quantity = variable.split(":", 1)[1]
    if quantity == "home_margin":
        return "moneyline" if kind == "moneyline" else "spread"
    if quantity == "total_points":
        return "total"
    if quantity in ("home_points", "away_points"):
        return "team_total"
    return None


def control_relation(wins_when: dict[str, Any], side: str) -> str | None:
    """ALIGNED when the contract pays only on the control side winning, OPPOSED when only otherwise."""
    low, high = wins_when.get("at_least"), wins_when.get("at_most")
    if side == "away":
        low, high = (None if high is None else -high), (None if low is None else -low)
    if low is not None and low >= 1:
        return ALIGNED
    if high is not None and high <= 0:
        return OPPOSED
    return None


def _environment_context(claims: dict[str, Any], family: str) -> list[str]:
    out = []
    if family == "total" and claims.get("pace"):
        out.append(f"PACE:{claims['pace']['level']}")
    if claims.get("scoring_environment"):
        out.append(f"SCORING_ENVIRONMENT:{claims['scoring_environment']['level']}")
    if claims.get("defensive_suppression"):
        out.append("DEFENSIVE_SUPPRESSION")
    return out


def classify_expression(expression: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"expression_id": expression["expression_id"], "kind": expression.get("kind")}
    ws = expression.get("wins_when")
    if expression.get("unmappable_reason") is not None or not ws:
        return {**out, "market_family": None, "authority": UNMAPPABLE, "relations": {}, "context": []}
    family = _market_family(expression.get("kind"), ws["variable"])
    out["market_family"] = family
    control = claims.get("control")
    relations: dict[str, str] = {}
    context: list[str] = []
    if family == "moneyline" and control:
        relation = control_relation(ws, control["side"])
        if relation is not None:
            relations["CONTROL"] = relation
            authority = DIRECT
        else:
            authority = NO_AUTHORITY
    elif family == "spread" and control:
        authority = RESEARCH_CONTEXT
        context.append(f"CONTROL:{control['tier']}:historical_range")
    elif family in ("total", "team_total"):
        authority = RESEARCH_UNCALIBRATED
        context.extend(_environment_context(claims, family))
    else:
        authority = NO_AUTHORITY
    return {**out, "authority": authority, "relations": relations, "context": context}


def map_claims(
    claims_envelope: dict[str, Any],
    market_map: dict[str, Any],
    *,
    mapped_at: str,
) -> dict[str, Any]:
    """The V2 authority map for one game. Refuses an altered or mismatched artifact."""
    if not verify_claims(claims_envelope):
        raise MappingOrderError("the V2 claims artifact does not match its hash; refusing to map")
    content = claims_envelope["content"]
    if market_map.get("artifact_hash") != content["source"]["football_artifact_hash"]:
        raise MappingOrderError("the V1 market map was not built from the football artifact these claims cite")
    if parse_utc(mapped_at) < parse_utc(claims_envelope["generated_at"]):
        raise MappingOrderError("market mapping may not precede the frozen V2 claims artifact")
    claims = content["claims"]
    upset_ids = {s["script_id"] for s in claims["explosive_upset"]["scripts"]}
    rows = []
    for e in market_map.get("expressions") or []:
        row = classify_expression(e, claims)
        if upset_ids:
            row["explosive_upset_v1"] = [
                c["status"] for c in e.get("compatibility") or [] if c.get("script_id") in upset_ids
            ]
        rows.append(row)
    counts = {a: 0 for a in AUTHORITIES}
    for r in rows:
        counts[r["authority"]] += 1
    return {
        "schema_version": CLAIMS_MARKET_SCHEMA_VERSION,
        "claims_artifact_hash": claims_envelope["artifact_hash"],
        "football_artifact_hash": market_map["artifact_hash"],
        "mapped_at": mapped_at,
        "activation": content["activation"],
        "policy": POLICY,
        "rule": POLICY_RULE,
        "counts": counts,
        "expressions": rows,
    }


def compact(authority_map: dict[str, Any] | None) -> dict[str, Any] | None:
    """What SIFT needs: the policy, the counts, and the moneyline directions by expression id."""
    if not authority_map:
        return None
    return {
        "schema_version": authority_map["schema_version"],
        "policy": authority_map["policy"],
        "rule": authority_map["rule"],
        "counts": authority_map["counts"],
        "moneyline": [
            [r["expression_id"], r["relations"]["CONTROL"]]
            for r in authority_map["expressions"]
            if r["authority"] == DIRECT
        ],
        "explosive_upset_v1": [
            [r["expression_id"], "".join(COMPAT_CODES[s] for s in r["explosive_upset_v1"])]
            for r in authority_map["expressions"]
            if r.get("explosive_upset_v1")
        ],
    }
