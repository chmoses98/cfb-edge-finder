"""Expression labels, ladders and survivors. MARKET SIDE, post-freeze. PURE.

*** A GOOD EXPRESSION IS NOT A +EV BET ***
Every label here describes how a contract relates to the FROZEN football
scripts. None of them is a price judgement:

  MULTI_SCRIPT        survives most meaningful scripts, PRIMARY included,
                      and neither PRIMARY nor SECONDARY contradicts it
  BEST_EXPRESSION     the best representation of one football thesis
                      (highest script survival; between rungs with identical
                      survival, the cheaper entry -- its extra requirement is
                      inside every script that supports it)
  SCRIPT_ALIGNED      PRIMARY supports it (fully or partly), uncontradicted
  AGGRESSIVE          a stronger version of a supported thesis: its win set
                      is a nested tail of the thesis's best expression and it
                      survives fewer scripts
  SCRIPT_DEPENDENT    exactly one script supports it
  NARROW_SCRIPT       only partially supported by any script
  CONTRADICTED        PRIMARY contradicts it, or at least half the
                      meaningful scripts do
  LOW_DATA_CONFIDENCE the football evidence is LOW confidence
  MARKET_DISAGREEMENT the football PRIMARY materially disagrees with the
                      market baseline (a flag; the artifact is untouched)

`HIGH_PROBABILITY_EXPRESSION`, `+EV`, fair probability and bet-up-to require
a legitimate, identified pricing source. V1 has none, so they are never
emitted: `pricing.source` is null on every expression and
`tests/test_script_engine_expressions.py` proves the vocabulary cannot leak.

*** PRICE ENTERS ONLY HERE, AND ONLY TO COMPARE RUNGS OF ONE THESIS ***
A price can order two expressions of the same thesis and can quantify what
an aggressive rung pays for its extra requirement. It cannot create a
thesis, re-rank scripts, or change a compatibility.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from cfb_edge_finder.execution.card_review import relate
from cfb_edge_finder.scripting.market_map import CONTRADICTED, PARTIAL, SUPPORTED

MULTI_SCRIPT = "MULTI_SCRIPT"
BEST_EXPRESSION = "BEST_EXPRESSION"
SCRIPT_ALIGNED = "SCRIPT_ALIGNED"
AGGRESSIVE = "AGGRESSIVE"
SCRIPT_DEPENDENT = "SCRIPT_DEPENDENT"
NARROW_SCRIPT = "NARROW_SCRIPT"
CONTRADICTED_LABEL = "CONTRADICTED"
LOW_DATA_CONFIDENCE = "LOW_DATA_CONFIDENCE"
MARKET_DISAGREEMENT = "MARKET_DISAGREEMENT"
RESEARCH_ONLY = "RESEARCH_ONLY"
HIGH_PROBABILITY_EXPRESSION = "HIGH_PROBABILITY_EXPRESSION"

LABELS = (
    MULTI_SCRIPT,
    BEST_EXPRESSION,
    SCRIPT_ALIGNED,
    AGGRESSIVE,
    SCRIPT_DEPENDENT,
    NARROW_SCRIPT,
    CONTRADICTED_LABEL,
    LOW_DATA_CONFIDENCE,
    MARKET_DISAGREEMENT,
    RESEARCH_ONLY,
)
"""Every label V1 can emit. HIGH_PROBABILITY_EXPRESSION is deliberately absent."""

#: Moneyline mid at or below this, on the side the football PRIMARY backs, is
#: a material disagreement with the market baseline.
DISAGREE_MONEYLINE_MID = 0.40


def _status(expr: dict[str, Any], role: str) -> str | None:
    return next((c["status"] for c in expr["compatibility"] if c["role"] == role), None)


def base_labels(expr: dict[str, Any], data_confidence: str) -> list[str]:
    if expr["unmappable_reason"] is not None:
        return [LOW_DATA_CONFIDENCE] if data_confidence == "LOW" else []
    surv = expr["script_survival"]
    meaningful = surv["meaningful_scripts"]
    labels: list[str] = []
    primary, secondary = _status(expr, "PRIMARY"), _status(expr, "SECONDARY")
    contradicted = primary == CONTRADICTED or (meaningful > 0 and surv["contradicted"] * 2 >= meaningful)
    if contradicted:
        labels.append(CONTRADICTED_LABEL)
    else:
        if (
            primary == SUPPORTED
            and secondary != CONTRADICTED
            and surv["supported"] >= max(2, math.ceil(0.6 * meaningful))
        ):
            labels.append(MULTI_SCRIPT)
        if primary in (SUPPORTED, PARTIAL):
            labels.append(SCRIPT_ALIGNED)
        if surv["supported"] == 1:
            labels.append(SCRIPT_DEPENDENT)
        if surv["supported"] == 0 and surv["partial"] > 0:
            labels.append(NARROW_SCRIPT)
    if data_confidence == "LOW":
        labels.append(LOW_DATA_CONFIDENCE)
    return labels


def _breadth(expr: dict[str, Any]) -> float:
    """How many integer outcomes the win set covers (wider = less required)."""
    ws = expr.get("wins_when") or {}
    lo, hi = ws.get("at_least"), ws.get("at_most")
    if lo is None or hi is None:
        return math.inf if (lo is None) != (hi is None) or (lo is None and hi is None) else 0.0
    return float(hi - lo + 1)


def _requirement(expr: dict[str, Any]) -> float:
    """Distance of the win set's finite edge into the tail, for ordering rungs."""
    ws = expr.get("wins_when") or {}
    lo, hi = ws.get("at_least"), ws.get("at_most")
    if lo is not None and hi is None:
        return float(lo)
    if hi is not None and lo is None:
        return float(-hi)
    return 0.0


def _entry(expr: dict[str, Any]) -> float:
    price = expr.get("price") or {}
    return float(price.get("cost_per_contract") or price.get("entry") or 1.0)


def _profit_multiple(expr: dict[str, Any]) -> float | None:
    price = expr.get("price") or {}
    cost, profit = price.get("cost_per_contract"), price.get("max_profit_per_contract")
    if not cost or profit is None or cost <= 0:
        return None
    return round(profit / cost, 3)


def _sort_key(expr: dict[str, Any]) -> tuple[float, float, float, str]:
    """Most script support first. Between rungs with IDENTICAL support, the
    cheaper entry: its extra requirement is inside every script that supports
    it, so it is the more efficient expression of the same thesis. Price can
    only break a tie in football support -- it never outranks it."""
    return (-expr["script_survival"]["weighted_score"], _entry(expr), _requirement(expr), expr["expression_id"])


def market_disagreement(
    content: dict[str, Any], expressions: list[dict[str, Any]], orientation_swapped: bool
) -> dict[str, Any] | None:
    """Does the market baseline materially disagree with the football PRIMARY?

    Read AFTER the freeze, from full-game moneyline mids. The flag is
    published beside the artifact; nothing in the artifact moves."""
    primary = next(
        (s for s in (content.get("game_scripts") or {}).get("scripts") or [] if s["role"] == "PRIMARY"), None
    )
    if primary is None:
        return None
    lean = primary["outcome_shape"]["winner_lean"]
    if lean not in ("HOME", "AWAY"):
        return None
    side = lean.lower()
    mids = []
    for e in expressions:
        if e["kind"] == "moneyline" and e["period"] == "full_game" and e["side"] == "YES" and e["team"] == side:
            price = e.get("price") or {}
            if price.get("entry") is not None:
                mids.append(price["entry"])
    if not mids:
        return None
    market = min(mids)
    if market > DISAGREE_MONEYLINE_MID:
        return None
    name = content["teams"][side]["name"]
    return {
        "flag": MARKET_DISAGREEMENT,
        "football_primary": primary["archetype"],
        "football_winner_lean": lean,
        "market_moneyline_ask": market,
        "rule": f"PRIMARY backs {name} while {name}'s moneyline asks {market:.2f} (<= {DISAGREE_MONEYLINE_MID:.2f})",
        "note": "Reported, never obeyed: the football artifact is not rewritten because the market disagrees.",
    }


def ladder(thesis_exprs: list[dict[str, Any]], core: dict[str, Any]) -> list[dict[str, Any]]:
    """Each rung of one thesis relative to its core expression (rule 12).

    What the cheaper or bolder rung additionally requires, which scripts it
    loses, what it pays per dollar risked -- and nothing about whether that
    is worth it, because V1 has no price it could compare it to."""
    core_supported = {c["script_id"] for c in core["compatibility"] if c["status"] == SUPPORTED}
    out = []
    for expr in sorted(thesis_exprs, key=lambda e: (_requirement(e), e["expression_id"])):
        rel = relate(expr["_row"], core["_row"], same_thesis=True)
        supported = {c["script_id"] for c in expr["compatibility"] if c["status"] == SUPPORTED}
        out.append(
            {
                "expression_id": expr["expression_id"],
                "is_core": expr is core,
                "wins_when": (expr.get("wins_when") or {}).get("description"),
                "relation_to_core": rel["relation"],
                "additional_requirement_points": rel["extension_points"],
                "cashes_when_core_fails": rel["cashes_when_reference_fails"],
                "scripts_supported": len(supported),
                "scripts_lost_vs_core": sorted(core_supported - supported),
                "profit_per_dollar": _profit_multiple(expr),
                "core_profit_per_dollar": _profit_multiple(core),
            }
        )
    return out


def annotate(content: dict[str, Any], market_map: dict[str, Any]) -> dict[str, Any]:
    """Labels, best expression per thesis, ladders, correlations and survivors."""
    confidence = content.get("data_confidence") or "LOW"
    expressions = market_map["expressions"]
    for expr in expressions:
        expr["labels"] = base_labels(expr, confidence)

    theses: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for expr in expressions:
        if expr["unmappable_reason"] is None and expr["script_survival"]["meaningful_scripts"] > 0:
            theses[expr["thesis"]].append(expr)

    thesis_blocks = []
    for thesis, members in sorted(theses.items()):
        aligned = [
            e for e in members if CONTRADICTED_LABEL not in e["labels"] and e["script_survival"]["supported"] > 0
        ]
        if not aligned:
            continue
        core = sorted(aligned, key=_sort_key)[0]
        core["labels"].insert(0, BEST_EXPRESSION)
        core_weight = core["script_survival"]["weighted_score"]
        same_driver = [e for e in members if e["driver"] == core["driver"] and e["direction"] == core["direction"]]
        for expr in same_driver:
            if expr is core or CONTRADICTED_LABEL in expr["labels"]:
                continue
            rel = relate(expr["_row"], core["_row"], same_thesis=True)
            if rel["tail_extension"] and expr["script_survival"]["weighted_score"] < core_weight:
                expr["labels"].append(AGGRESSIVE)
        thesis_blocks.append(
            {
                "thesis": thesis,
                "core_expression": core["expression_id"],
                "core_survival": core["script_survival"],
                "ladder": ladder(same_driver, core),
            }
        )

    disagreement = market_disagreement(content, expressions, market_map.get("orientation_swapped", False))
    if disagreement is not None:
        lean = disagreement["football_winner_lean"].lower()
        for expr in expressions:
            if expr["thesis"] == f"side:{lean}":
                expr["labels"].append(MARKET_DISAGREEMENT)

    featured = [e for e in expressions if {BEST_EXPRESSION, MULTI_SCRIPT} & set(e["labels"])]
    for expr in featured:
        expr["correlation"] = [
            {
                "with": other["expression_id"],
                **{
                    k: v
                    for k, v in relate(
                        expr["_row"],
                        other["_row"],
                        same_thesis=expr["thesis"] == other["thesis"],
                        opposing=expr["driver"] == other["driver"] and expr["direction"] != other["direction"],
                    ).items()
                    if k in ("relation", "both_can_cash", "both_lose_when", "cashes_when_reference_fails")
                },
            }
            for other in featured
            if other is not expr
        ]

    survivors = sorted(
        [e for e in expressions if {MULTI_SCRIPT, BEST_EXPRESSION} & set(e["labels"])],
        key=_sort_key,
    )
    for expr in expressions:
        expr.pop("_row", None)
    market_map["theses"] = thesis_blocks
    market_map["market_disagreement"] = disagreement
    market_map["survivors"] = [
        {
            "expression_id": e["expression_id"],
            "labels": e["labels"],
            "script_survival": e["script_survival"],
            "means": e["means"],
            "price": e["price"],
        }
        for e in survivors
    ]
    market_map["labels_vocabulary"] = list(LABELS)
    market_map["research_only"] = True
    return market_map
