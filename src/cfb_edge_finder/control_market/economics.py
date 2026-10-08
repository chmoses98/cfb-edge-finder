"""One-research-contract economics and side-aware CLV for a CONTROL game.

* The fee is the verified July 2026 taker fee, computed at the captured entry
  price of the side actually bought. An uncomputable fee is `None`, never 0.
* Settlement is $1 iff the bought contract pays: YES on the CONTROL team's own
  market pays iff the CONTROL team wins; NO on the opponent's market pays iff
  the opponent does NOT win.
* CLV compares the SAME contract's SAME side: close ask - entry ask.
  A missing close is `None`, never 0.
"""

from __future__ import annotations

import math
from typing import Any

from cfb_edge_finder.control_market import RESEARCH_UNIT_CONTRACTS, SERIES
from cfb_edge_finder.control_market.quotes import whole_cent
from cfb_edge_finder.kalshi.fee_schedule import (
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    calculate_fee_cents,
    get_taker_multiplier,
)

FEE_SCHEDULE = KALSHI_FEE_SCHEDULE_2026_07_07_TAKER
LOGIT_CLAMP = 0.005


def entry_fee(price: float | None) -> float | None:
    """Taker fee in dollars for one contract at `price`; None when it cannot be computed."""
    cents = whole_cent(price)
    if cents is None:
        return None
    multiplier, _ = get_taker_multiplier(SERIES)
    return calculate_fee_cents(cents, RESEARCH_UNIT_CONTRACTS, FEE_SCHEDULE, multiplier) / 100.0


def contract_pays(contract_side: str, contract_is_control_team: bool, control_won: bool) -> bool:
    """Does this contract pay $1? YES pays iff its team wins; NO pays iff its team does not win."""
    team_won = control_won if contract_is_control_team else not control_won
    if contract_side == "yes":
        return team_won
    if contract_side == "no":
        return not team_won
    raise ValueError(f"contract side must be 'yes' or 'no', got {contract_side!r}")


def one_contract(entry_price: float | None, fee: float | None, pays: bool | None) -> dict[str, Any]:
    """Fee-adjusted economics of one research contract. Any missing input -> unavailable, never zero-filled."""
    if entry_price is None:
        return {"available": False, "reason": "ENTRY_QUOTE_UNAVAILABLE"}
    if fee is None:
        return {"available": False, "reason": "FEE_UNAVAILABLE"}
    if pays is None:
        return {"available": False, "reason": "SETTLEMENT_UNAVAILABLE"}
    payout = 1.0 if pays else 0.0
    outlay = entry_price + fee
    return {
        "available": True,
        "contracts": RESEARCH_UNIT_CONTRACTS,
        "entry_price": entry_price,
        "fee": fee,
        "outlay": round(outlay, 6),
        "settlement_value": payout,
        "gross_pnl": round(payout - entry_price, 6),
        "fee_adjusted_pnl": round(payout - outlay, 6),
        "roi_on_outlay": round((payout - outlay) / outlay, 6),
    }


def _logit(p: float) -> float:
    p = min(max(p, LOGIT_CLAMP), 1 - LOGIT_CLAMP)
    return math.log(p / (1 - p))


def clv(entry_price: float | None, closing_price: float | None) -> dict[str, Any]:
    """Side-aware CLV of the same contract side. `closing_price` None -> CLOSE_MISSING (not zero)."""
    if entry_price is None:
        return {"available": False, "reason": "ENTRY_QUOTE_UNAVAILABLE"}
    if closing_price is None:
        return {"available": False, "reason": "CLOSE_MISSING"}
    move = round(closing_price - entry_price, 6)
    return {
        "available": True,
        "entry": entry_price,
        "close": closing_price,
        "clv": move,
        "direction": "FAVORABLE" if move > 0 else ("UNFAVORABLE" if move < 0 else "FLAT"),
        "logit_movement": round(_logit(closing_price) - _logit(entry_price), 6),
    }


def reconcile_settlement(derived_pays: bool | None, official: list[bool]) -> dict[str, Any]:
    """Game-log settlement cross-checked against any official/derived research settlement of the same contract."""
    if derived_pays is None:
        return {"status": "SETTLEMENT_UNAVAILABLE", "pays": None, "cross_checked": len(official)}
    if any(o != derived_pays for o in official):
        return {"status": "SETTLEMENT_MISMATCH", "pays": None, "cross_checked": len(official)}
    return {"status": "SETTLED", "pays": derived_pays, "cross_checked": len(official)}
