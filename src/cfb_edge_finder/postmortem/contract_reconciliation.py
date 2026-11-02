"""One immutable, evidence-flagged reconciliation record per executed contract.

Joins what the ledger knows (wager, settlement, amendment) with what the research stores may
know (recommendation, model snapshot, injuries, closing quote) and states, per field, whether
the evidence EXISTS. A missing field is listed in ``missing`` -- it is never inferred, defaulted
or back-filled from a neighbouring record.

Money rules (docs/SETTLEMENT_ECONOMICS.md): ``stake`` already contains the entry fee, so
``net = gross_return - stake``; fees are shown as a component of stake, NEVER subtracted again.
``check_economics`` re-derives the identity from the row's own numbers and flags a mismatch.

Records are derived artifacts: ``record_id`` is a content hash, the input rows are not modified,
and ``append_records`` only ever appends rows whose ids are new.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cfb_edge_finder.expression.fair_value import order_fee_dollars
from cfb_edge_finder.expression.ticker_ontology import Family, parse_ticker

SCHEMA_VERSION = "cfb_contract_reconciliation/1.0.0"
MONEY_TOLERANCE = 0.011  # dollars; stake is rounded to 4dp, fees to 4dp, contracts to 2dp
FEE_TOLERANCE = 0.011


def _close_price_for_side(close_quote: dict[str, Any] | None, side: str) -> float | None:
    """Mid of the quoted book for the side bought, or None when the book is not two-sided."""
    if not close_quote:
        return None
    bid, ask = close_quote.get("yes_bid"), close_quote.get("yes_ask")
    if bid is None or ask is None:
        return None
    mid_yes = (float(bid) + float(ask)) / 2
    return mid_yes if side.upper() == "YES" else 1.0 - mid_yes


def check_economics(wager: dict[str, Any], settlement: dict[str, Any] | None) -> list[str]:
    """Flags if the row's own numbers contradict the stake-includes-fee identity."""
    issues: list[str] = []
    contracts, price, fees, stake = (wager.get(k) for k in ("contracts", "average_price", "fees", "stake"))
    if None not in (contracts, price, fees, stake):
        if abs(contracts * price + fees - stake) > MONEY_TOLERANCE + 0.0005 * contracts * 0.01:
            issues.append("STAKE_NE_COST_PLUS_FEES")
    if settlement and settlement.get("gross_payout") is not None and settlement.get("net_pnl") is not None:
        if stake is not None and abs(settlement["gross_payout"] - stake - settlement["net_pnl"]) > MONEY_TOLERANCE:
            issues.append("NET_NE_GROSS_MINUS_STAKE")
    return issues


def build_reconciliation(
    wager: dict[str, Any],
    settlement: dict[str, Any] | None,
    *,
    recommendation: dict[str, Any] | None = None,
    injuries: dict[str, Any] | None = None,
    close_quote: dict[str, Any] | None = None,
    combo_legs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the record. ``recommendation``/``injuries``/``close_quote``/``combo_legs`` are optional
    evidence; absence is recorded, not repaired."""
    ticker = wager["kalshi_ticker"]
    info = parse_ticker(ticker)
    side = str(wager.get("selection") or "").upper()
    missing: list[str] = []
    linkage = wager.get("linkage") or {}

    def need(flag: str, present: bool) -> None:
        if not present:
            missing.append(flag)

    need("MISSING_RECOMMENDATION_ID", bool(wager.get("recommendation_id")) or bool(recommendation))
    need(
        "MISSING_MODEL_RUN_ID",
        bool(wager.get("model_run_id")) or bool(recommendation and recommendation.get("model_run_id")),
    )
    need("MISSING_MODEL_PRICE_ID", bool(wager.get("model_price_id")))
    need("MISSING_RECOMMENDATION_TIME", bool(linkage.get("recommendation_generated_at")) or bool(recommendation))
    need(
        "MISSING_MODEL_INPUTS_AS_OF",
        bool(linkage.get("model_inputs_as_of")) or bool(recommendation and recommendation.get("model_inputs_as_of")),
    )
    need("MISSING_MARKET_CAPTURED_AT", bool(linkage.get("market_captured_at")))
    need("MISSING_INJURY_SNAPSHOT", injuries is not None)
    need("MISSING_ORDER_ID", bool(wager.get("kalshi_order_id")))
    need("MISSING_FILL_IDS", bool(wager.get("kalshi_fill_ids")))
    need("MISSING_CLOSE_QUOTE", close_quote is not None)
    need("MISSING_SETTLEMENT", settlement is not None)

    close_for_side = _close_price_for_side(close_quote, side) if close_quote else None
    if close_quote is not None and close_for_side is None:
        missing.append("CLOSE_QUOTE_NOT_TWO_SIDED")
    fill = wager.get("average_price")
    clv = None if close_for_side is None or fill is None else round(close_for_side - fill, 6)

    decoded_legs = None
    if info.family is Family.COMBO_UNDECODED:
        if combo_legs:
            decoded_legs = combo_legs
        else:
            missing.append("UNDECODED_COMBO_LEGS")

    recomputed_fee = None
    if wager.get("contracts") and fill:
        recomputed_fee = order_fee_dollars(float(fill), float(wager["contracts"]), info.series)
    fee_matches = (
        None
        if recomputed_fee is None or wager.get("fees") is None
        else (abs(recomputed_fee - wager["fees"]) <= FEE_TOLERANCE)
    )

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_bet_key": wager.get("source_bet_key"),
        "wager_id": wager.get("wager_id"),
        "ticker": ticker,
        "ontology": {
            "family": info.family.value,
            "period": info.period.value if info.period else None,
            "game_key": info.game_key,
            "ticker_game_date": info.game_date,
            "team": info.team,
            "opponent": info.opponent,
            "strike": info.strike,
            "decoded": info.decoded,
        },
        "side": side or None,
        "execution": {
            "placed_at": wager.get("placed_at"),
            "contracts": wager.get("contracts"),
            "average_price": fill,
            "fees_paid": wager.get("fees"),
            "stake": wager.get("stake"),
            "order_id": wager.get("kalshi_order_id"),
            "fill_ids": wager.get("kalshi_fill_ids") or [],
            "fee_recomputed_per_order": recomputed_fee,
            "fee_matches_schedule": fee_matches,
        },
        "attribution": {
            "recommendation_id": wager.get("recommendation_id") or (recommendation or {}).get("recommendation_id"),
            "model_run_id": wager.get("model_run_id") or (recommendation or {}).get("model_run_id"),
            "model_price_id": wager.get("model_price_id"),
            "recommended_at": linkage.get("recommendation_generated_at") or (recommendation or {}).get("generated_at"),
            "model_inputs_as_of": linkage.get("model_inputs_as_of") or (recommendation or {}).get("model_inputs_as_of"),
        },
        "injuries": injuries,
        "settlement": None
        if settlement is None
        else {
            "result": settlement.get("result"),
            "gross_return": settlement.get("gross_payout"),
            "net_pnl": settlement.get("net_pnl"),
            "as_filed_net_pnl": (settlement.get("extensions") or {}).get("as_filed_net_profit_loss"),
            "economics_version": (settlement.get("extensions") or {}).get("economics_version"),
            "amendment_id": (settlement.get("source_ids") or {}).get("canonical_amendment_id"),
            "settled_at": settlement.get("settled_at"),
            "verification_status": settlement.get("verification_status"),
        },
        "close": {
            "quote": close_quote,
            "close_price_for_side_mid": close_for_side,
            "clv_mid_vs_fill": clv,
        },
        "combo_legs": decoded_legs,
        "economics_issues": check_economics(wager, settlement),
        "missing": sorted(set(missing)),
    }
    record["record_id"] = (
        "rec-" + hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()[:24]
    )
    return record


def append_records(path: Path, records: list[dict[str, Any]]) -> int:
    """Append-only write: rows whose ``record_id`` is already present are skipped, never replaced."""
    existing: set[str] = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                existing.add(json.loads(line)["record_id"])
    new = [r for r in records if r["record_id"] not in existing]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            for r in new:
                fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    return len(new)
