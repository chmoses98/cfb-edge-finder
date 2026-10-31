#!/usr/bin/env python3
"""Read-only, settlement-corrected CFB accounting analysis.

Example:
  python scripts/audit_routed_cfb_bets.py --ledger-dir /path/to/accounting-data --since 2026-10-22

Loads only local ledger JSONL. Does not contact Kalshi or modify anything.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
ZERO = Decimal("0")


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing accounting file: {path}")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def keyed(rows: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        key = row["source_bet_key"]
        if key in result:
            raise ValueError(f"Duplicate source key in {label}")
        result[key] = row
    return result


def eastern_date(iso: str) -> str:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(EASTERN).date().isoformat()


def family(ticker: str) -> str:
    if ticker.startswith("KXMVECROSSCATEGORY-"):
        return "multivariate_combo"
    prefix = ticker.split("-", 1)[0]
    return {
        "KXNCAAFGAME": "game_moneyline",
        "KXNCAAFSPREAD": "game_spread",
        "KXNCAAFTOTAL": "game_total",
        "KXNCAAFTEAMTOTAL": "team_total",
        "KXNCAAF1HSPREAD": "first_half_spread",
        "KXNCAAF1HTOTAL": "first_half_total",
        "KXNCAAF1QSPREAD": "quarter_spread",
        "KXNCAAF1QTOTAL": "quarter_total",
    }.get(prefix, "other")


def game_key(ticker: str) -> str:
    parts = ticker.split("-")
    return parts[1] if len(parts) > 2 and ticker.startswith("KXNCAAF") else "MULTI_EVENT"


def audit(base: Path, since: str, through: str | None = None, unit: Decimal = Decimal("75")) -> dict:
    if unit <= ZERO:
        raise ValueError("Unit must be positive")
    wagers = keyed(read_jsonl(base / "wagers/2026.jsonl"), "wagers")
    settlements = keyed(read_jsonl(base / "settlements/2026.jsonl"), "settlements")
    amendments = keyed(read_jsonl(base / "settlement_amendments/2026.jsonl"), "amendments")
    selected = []
    for key, wager in wagers.items():
        date = eastern_date(wager["executed_at"])
        if date < since or (through is not None and date > through):
            continue
        original = settlements.get(key)
        correction = amendments.get(key)
        if correction is not None and (
            original is None or correction["amends_settlement_id"] != original["settlement_id"]
        ):
            raise ValueError("Orphan or contradictory settlement amendment")
        settlement = correction or original
        pnl = Decimal(str(settlement["net_profit_loss"])) if settlement and settlement["net_profit_loss"] is not None else None
        result = settlement["result"] if settlement else "PENDING"
        if settlement and pnl is None:
            raise ValueError("Settlement without established P&L")
        ticker = wager["market_ticker"]
        selected.append({
            "date": date,
            "game": game_key(ticker),
            "ticker": ticker,
            "family": family(ticker),
            "side": wager["side"],
            "stake": Decimal(str(wager["stake"])),
            "fee": Decimal(str(wager["fees_paid"])),
            "pnl": pnl,
            "result": result,
        })

    def summary(rows):
        settled = [x for x in rows if x["pnl"] is not None]
        settled_stake = sum((x["stake"] for x in settled), ZERO)
        net = sum((x["pnl"] for x in settled), ZERO)
        return {
            "wagers": len(rows),
            "settled": len(settled),
            "pending": len(rows) - len(settled),
            "wins": sum(x["result"] == "WON" for x in settled),
            "losses": sum(x["result"] == "LOST" for x in settled),
            "exposure_dollars": str(sum((x["stake"] for x in rows), ZERO)),
            "settled_stake_dollars": str(settled_stake),
            "realized_pnl_dollars": str(net),
            "realized_roi": str(net / settled_stake) if settled_stake else None,
        }

    def group(field):
        groups = defaultdict(list)
        for item in selected:
            groups[item[field]].append(item)
        return {k: summary(v) for k, v in sorted(groups.items())}

    sides = defaultdict(set)
    games = defaultdict(list)
    for item in selected:
        sides[(item["date"], item["ticker"])].add(item["side"])
        games[(item["date"], item["game"])].append(item)
    daily = group("date")
    return {
        "period": {"since": since, "through": through, "unit": str(unit)},
        "overall": summary(selected),
        "days": daily,
        "families": group("family"),
        "flags": {
            "opposite_sides_same_ticker": [
                {"date": d, "ticker": t} for (d, t), observed in sorted(sides.items()) if len(observed) > 1
            ],
            "multi_bet_games": [
                {"date": d, "game": g, "bet_count": len(v),
                 "gross_exposure": str(sum((x["stake"] for x in v), ZERO))}
                for (d, g), v in sorted(games.items()) if len(v) > 1
            ],
            "stake_over_5u": [
                {"date": x["date"], "ticker": x["ticker"], "stake": str(x["stake"])}
                for x in selected if x["stake"] > unit * 5
            ],
            "gross_day_over_15u": [
                {"date": d, "gross_exposure": s["exposure_dollars"]}
                for d, s in daily.items() if Decimal(s["exposure_dollars"]) > unit * 15
            ],
        },
        "limitations": [
            "Only actual executions, not all recommendations or cancellations",
            "No closing lines or independent fair probabilities",
            "Historical market-family ROI is not prospective profit evidence",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-dir", type=Path, required=True)
    parser.add_argument("--since", required=True, help="Inclusive Eastern execution date")
    parser.add_argument("--through", help="Optional inclusive Eastern execution date")
    parser.add_argument("--unit", default="75")
    args = parser.parse_args()
    print(json.dumps(audit(args.ledger_dir, args.since, args.through, Decimal(args.unit)), indent=2))


if __name__ == "__main__":
    main()
