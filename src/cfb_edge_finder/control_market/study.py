"""FREEZE (no outcome) and REVEAL (settlement join) of one CONTROL game row.

`freeze_row` sees the football record, the captured quotes and the market
orientation, and nothing else: it has no argument through which a score or a
settlement could arrive. `reveal_row` is the only place a result is read, and
it refuses a frozen row whose hash no longer matches.
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.control_market import CHECKPOINTS, PRIMARY_CHECKPOINT, sha256
from cfb_edge_finder.control_market.economics import clv, contract_pays, entry_fee, one_contract, reconcile_settlement
from cfb_edge_finder.control_market.quotes import (
    Quote,
    contract_for,
    minutes_before,
    select_checkpoint,
    side_ask,
    unattributed_market_for,
)
from cfb_edge_finder.control_market.report import price_bucket, prior_bucket, season_block


class FrozenRowMismatch(RuntimeError):
    """A frozen row was altered after the freeze."""


def _quote_view(q: Quote | None, side: str, kickoff: str) -> dict[str, Any] | None:
    if q is None:
        return None
    return {
        "price": side_ask(q, side),
        "captured_at": q.captured_at,
        "minutes_before_kickoff": round(minutes_before(q, kickoff), 2),
        "source": q.source,
        "provenance": q.provenance,
        "mid": q.yes_mid if side == "yes" else None,
    }


def _contract_block(
    contract: tuple[str, str] | None, status: str, quotes_by_ticker: dict[str, list[Quote]], kickoff: str
) -> dict[str, Any]:
    if contract is None:
        empty = {"price": None, "captured_at": None, "source": None, "provenance": None, "mid": None}
        return {
            "contract_status": status,
            "ticker": None,
            "side": None,
            "entry": dict(empty),
            "checkpoints": {cp: None for cp in CHECKPOINTS},
            "fee": None,
            "exclusion": status,
        }
    ticker, side = contract
    quotes = quotes_by_ticker.get(ticker, [])
    checkpoints = {cp: _quote_view(select_checkpoint(quotes, side, kickoff, cp), side, kickoff) for cp in CHECKPOINTS}
    entry = checkpoints[PRIMARY_CHECKPOINT]
    fee = entry_fee(entry["price"]) if entry else None
    if entry is None:
        exclusion = "ENTRY_QUOTE_UNAVAILABLE"
    elif fee is None:
        exclusion = "FEE_UNAVAILABLE"
    else:
        exclusion = None
    return {
        "contract_status": "RESOLVED",
        "ticker": ticker,
        "side": side,
        "entry": entry or {"price": None, "captured_at": None, "source": None, "provenance": None, "mid": None},
        "checkpoints": checkpoints,
        "fee": fee,
        "exclusion": exclusion,
    }


def freeze_row(
    football: dict[str, Any],
    quotes_by_ticker: dict[str, list[Quote]],
    orientation: dict[str, dict[str, Any]],
    abbreviations: set[str] | None = None,
) -> dict[str, Any]:
    """The outcome-blind study row of one CONTROL game."""
    control = football["control"]
    contracts = contract_for(control["team_id"], football["game_id"], orientation)
    if contracts["status"] == "NO_GAME_WINNER_MARKET" and abbreviations:
        hints = {t: qs[0].kickoff_hint for t, qs in quotes_by_ticker.items() if qs}
        if unattributed_market_for(football, abbreviations, orientation, hints):
            contracts = {**contracts, "status": "ORIENTATION_UNRESOLVED"}
    kickoff = football["kickoff_utc"]
    primary = _contract_block(contracts["primary"], contracts["status"], quotes_by_ticker, kickoff)
    secondary = _contract_block(
        contracts["secondary"],
        "RESOLVED" if contracts["secondary"] else contracts["status"],
        quotes_by_ticker,
        kickoff,
    )
    row = {
        "population": football["population"],
        "football_provenance": football["football_provenance"],
        "game_id": football["game_id"],
        "kickoff_utc": kickoff,
        "week": football.get("week"),
        "season_block": season_block(football.get("week")),
        "home": football["home"],
        "away": football["away"],
        "control_team": control["team"],
        "control_team_id": control["team_id"],
        "opponent": control["opponent"],
        "control_side": control["side"],
        "control_strength": control["strength"],
        "tier": control["tier"],
        "data_confidence": football.get("data_confidence"),
        "prior_games": football.get("prior_games"),
        "prior_games_bucket": prior_bucket(football.get("prior_games")),
        "fcs_involved": football.get("fcs_involved"),
        "replay_eligibility": football.get("replay_eligibility"),
        "football_record_hash": football.get("football_record_hash") or football.get("claims_artifact_hash"),
        "football_artifact_hash": football.get("football_artifact_hash") or football.get("football_content_hash"),
        "contract_orientation": contracts["status"],
        "primary": primary,
        "secondary": secondary,
    }
    row["primary"]["bucket"] = price_bucket(primary["entry"]["price"])
    row["frozen_row_hash"] = sha256(row)
    return row


def reveal_row(frozen: dict[str, Any], outcome: dict[str, Any], official: dict[str, list[str]]) -> dict[str, Any]:
    """Join the settlement to a frozen row (hash re-checked) and compute economics and CLV.

    `official[ticker]` lists research-data settlements ("yes"/"no") of that ticker, used only as a cross-check."""
    body = {k: v for k, v in frozen.items() if k != "frozen_row_hash"}
    if sha256(body) != frozen["frozen_row_hash"]:
        raise FrozenRowMismatch(frozen.get("game_id"))
    row = dict(frozen)
    settled = outcome.get("status") == "SETTLED"
    row["settlement"] = {
        "status": outcome.get("status"),
        "control_won": outcome.get("control_won") if settled else None,
        "control_margin": outcome.get("control_margin") if settled else None,
        "control_points": outcome.get("control_points"),
        "opponent_points": outcome.get("opponent_points"),
        "source": outcome.get("source"),
    }
    for name in ("primary", "secondary"):
        block = dict(row[name])
        if block["ticker"] is None:
            block["settlement"] = {"status": "NOT_APPLICABLE", "pays": None}
        else:
            is_control_market = name == "primary"
            derived = contract_pays(block["side"], is_control_market, outcome["control_won"]) if settled else None
            checks = [v == "yes" for v in official.get(block["ticker"], [])]
            block["settlement"] = reconcile_settlement(derived, checks)
        pays = block["settlement"]["pays"]
        econ = one_contract(block["entry"]["price"], block["fee"], pays)
        if not econ["available"] and block["exclusion"] is None:
            block["exclusion"] = block["settlement"]["status"]
        block["economics"] = econ
        close = block["checkpoints"]["CLOSING"]
        if close and block["entry"]["captured_at"] and close["captured_at"] <= block["entry"]["captured_at"]:
            close = None
        block["clv"] = clv(block["entry"]["price"], close["price"] if close else None)
        row[name] = block
    if row["settlement"]["status"] == "SETTLED" and row["primary"]["settlement"].get("status") == "SETTLEMENT_MISMATCH":
        row["settlement"]["status"] = "SETTLEMENT_MISMATCH"
    return row
