"""Script <-> market compatibility. MARKET SIDE: runs only on a frozen artifact.

    frozen football artifact (hash verified)  +  normalized Kalshi contracts
        -> every eligible contract, both sides, classified against every script

*** ORDER IS ENFORCED, NOT ASSUMED ***
`map_game` refuses an envelope whose content no longer matches its hash, and
refuses to map before the artifact's `generated_at`. It never writes to the
football content: the market map is a separate document that cites the
artifact hash it was built from.

*** SETTLEMENT SEMANTICS, NOT TICKER NAMES ***
What a contract pays on comes from `execution.semantics` (the normalized
contract terms the evaluator already uses) through `card_review.win_set`:
the exact set of integer outcomes on which a side pays, on one variable --
home margin, total points, or one team's points. Nothing here parses a
ticker.

*** CLASSIFICATION ***
For a script whose outcome band on that variable is [lo, hi] (integers):
    SUPPORTED     every outcome in the band pays
    PARTIAL       some do (coverage = share of the band that pays)
    CONTRADICTED  none do
    NEUTRAL       the script states no band for this variable
    UNMAPPABLE    the contract's terms are not a single interval on a
                  full-game variable (props, first-half markets in V1,
                  tie/overtime, a two-piece NO), with the reason
The classification never looks at a price.
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.execution.candidates import driver_and_direction, thesis_direction
from cfb_edge_finder.execution.card_review import win_set
from cfb_edge_finder.scripting import MARKET_MAP_SCHEMA_VERSION
from cfb_edge_finder.scripting.freeze import verify
from cfb_edge_finder.scripting.gamelog import parse_utc

SUPPORTED = "SUPPORTED"
PARTIAL = "PARTIAL"
CONTRADICTED = "CONTRADICTED"
NEUTRAL = "NEUTRAL"
UNMAPPABLE = "UNMAPPABLE"
COMPAT_CODES = {SUPPORTED: "S", PARTIAL: "P", CONTRADICTED: "C", NEUTRAL: "N", UNMAPPABLE: "U"}

#: How heavily each script role counts when SORTING. The raw map is always
#: published alongside; this weighting only orders lists.
ROLE_WEIGHT = {"PRIMARY": 1.0, "SECONDARY": 0.7, "ALTERNATE": 0.5, "DANGER": 0.35}
COMPAT_VALUE = {SUPPORTED: 1.0, CONTRADICTED: -1.0, NEUTRAL: 0.0, UNMAPPABLE: 0.0}

SUPPORTED_PERIOD = "full_game"


class MappingOrderError(RuntimeError):
    """The market map was asked to run against an unfrozen or altered artifact."""


def _band_for(variable: str, bands: dict[str, Any]) -> list[int] | None:
    _, quantity = variable.split(":", 1)
    return bands.get(quantity)


def classify(ws_low: float, ws_high: float, band: list[int] | None) -> tuple[str, float | None]:
    if band is None:
        return NEUTRAL, None
    lo, hi = band
    inter_lo, inter_hi = max(lo, ws_low), min(hi, ws_high)
    paying = max(0, int(inter_hi - inter_lo) + 1) if inter_hi >= inter_lo else 0
    size = hi - lo + 1
    if paying >= size:
        return SUPPORTED, 1.0
    if paying == 0:
        return CONTRADICTED, 0.0
    return PARTIAL, round(paying / size, 3)


def _orient(team: str, swapped: bool) -> str:
    if not swapped or team not in ("home", "away"):
        return team
    return "away" if team == "home" else "home"


def expression_row(contract: dict[str, Any], side: str, swapped: bool) -> dict[str, Any]:
    sem = contract.get("semantics") or {}
    return {
        "ticker": contract.get("ticker"),
        "game_key": contract.get("game_id"),
        "family": contract.get("family"),
        "kind": sem.get("kind"),
        "period": sem.get("period"),
        "team": _orient(str(sem.get("team") or "none"), swapped),
        "comparator": sem.get("comparator"),
        "line": sem.get("line"),
        **({"cap": sem.get("cap")} if "cap" in sem else {}),
        "best_side": side,
        "is_alternate_line": bool(contract.get("is_alternate_line")),
        "means": sem.get("yes_means") if side == "yes" else sem.get("no_means"),
    }


def _price(contract: dict[str, Any], side: str) -> dict[str, Any] | None:
    executable = (contract.get("executable") or {}).get(side)
    if not executable:
        return None
    quote = contract.get("quote") or {}
    return {
        "entry": executable.get("entry"),
        "fee": executable.get("fee"),
        "cost_per_contract": executable.get("cost_per_contract"),
        "max_profit_per_contract": executable.get("max_profit_per_contract"),
        "breakeven_probability": executable.get("breakeven_probability"),
        "quote_timestamp": quote.get("quote_timestamp"),
    }


def survival(compat: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {k: 0 for k in (SUPPORTED, PARTIAL, CONTRADICTED, NEUTRAL, UNMAPPABLE)}
    weighted = 0.0
    for entry in compat:
        counts[entry["status"]] += 1
        value = 0.5 * (entry.get("coverage") or 0.0) if entry["status"] == PARTIAL else COMPAT_VALUE[entry["status"]]
        weighted += ROLE_WEIGHT.get(entry["role"], 0.0) * value
    total = len(compat)
    return {
        "supported": counts[SUPPORTED],
        "partial": counts[PARTIAL],
        "contradicted": counts[CONTRADICTED],
        "neutral": counts[NEUTRAL],
        "unmappable": counts[UNMAPPABLE],
        "total_scripts": total,
        "meaningful_scripts": total - counts[NEUTRAL] - counts[UNMAPPABLE],
        "weighted_score": round(weighted, 4),
        "weighting": "PRIMARY 1.0, SECONDARY 0.7, ALTERNATE 0.5, DANGER 0.35; SUPPORTED +1, PARTIAL +0.5 x coverage, "
        "CONTRADICTED -1. Used to SORT only; the raw per-script map is the record.",
    }


def map_game(
    envelope: dict[str, Any],
    contracts: list[dict[str, Any]],
    *,
    orientation_swapped: bool,
    mapped_at: str,
    excluded_contracts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not verify(envelope):
        raise MappingOrderError("the football artifact does not match its hash; refusing to map")
    if parse_utc(mapped_at) < parse_utc(envelope["generated_at"]):
        raise MappingOrderError("market mapping may not precede the frozen football artifact")
    content = envelope["content"]
    scripts = (content.get("game_scripts") or {}).get("scripts") or []
    expressions: list[dict[str, Any]] = []

    for contract in sorted(contracts, key=lambda c: str(c.get("ticker"))):
        for side in ("yes", "no"):
            row = expression_row(contract, side, orientation_swapped)
            reason = None
            ws = None
            if row["period"] != SUPPORTED_PERIOD:
                reason = f"V1 scripts describe full-game outcomes only; this contract settles on {row['period']}"
            else:
                ws = win_set(row)
                if ws is None:
                    reason = (
                        f"the {side.upper()} side of a {row['kind']} contract is not a single interval on a "
                        f"full-game margin, total or team-points variable"
                    )
            compat = []
            for script in scripts:
                if ws is None:
                    status, coverage = UNMAPPABLE, None
                else:
                    band = _band_for(ws.variable, script["outcome_shape"]["bands"])
                    status, coverage = classify(ws.low, ws.high, band)
                compat.append(
                    {"script_id": script["script_id"], "role": script["role"], "status": status, "coverage": coverage}
                )
            driver, direction = driver_and_direction(row)
            expressions.append(
                {
                    "expression_id": f"{row['ticker']}:{side.upper()}",
                    "ticker": row["ticker"],
                    "side": side.upper(),
                    "family": row["family"],
                    "kind": row["kind"],
                    "period": row["period"],
                    "team": row["team"],
                    "line": row["line"],
                    "is_alternate_line": row["is_alternate_line"],
                    "means": row["means"],
                    "wins_when": {**ws.as_dict(), "variable": ws.variable} if ws else None,
                    "driver": driver,
                    "direction": direction,
                    "thesis": thesis_direction(row, driver, direction),
                    "unmappable_reason": reason,
                    "compatibility": compat,
                    "script_survival": survival(compat),
                    "price": _price(contract, side),
                    "pricing": {
                        "status": "RESEARCH_ONLY",
                        "source": None,
                        "fair_probability": None,
                        "expected_value": None,
                        "note": (
                            "No validated pricing source exists for CFB. Script survival is not a probability: "
                            "3 of 4 scripts does not mean 75%."
                        ),
                    },
                    "_row": row,
                }
            )

    mapped = sum(1 for e in expressions if e["unmappable_reason"] is None)
    return {
        "schema_version": MARKET_MAP_SCHEMA_VERSION,
        "artifact_hash": envelope["artifact_hash"],
        "artifact_generated_at": envelope["generated_at"],
        "mapped_at": mapped_at,
        "orientation_swapped": orientation_swapped,
        "scripts": [
            {"script_id": s["script_id"], "rank": s["rank"], "role": s["role"], "archetype": s["archetype"]}
            for s in scripts
        ],
        "coverage": {
            "contracts_eligible": len(contracts),
            "contracts_excluded_mechanically": len(excluded_contracts or []),
            "expressions_evaluated": len(expressions),
            "expressions_mapped": mapped,
            "expressions_unmappable": len(expressions) - mapped,
            "invariant": "expressions_evaluated == 2 x contracts_eligible == mapped + unmappable",
            "balanced": len(expressions) == 2 * len(contracts) == mapped + (len(expressions) - mapped),
        },
        "expressions": expressions,
    }
