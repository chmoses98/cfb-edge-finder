#!/usr/bin/env python3
"""Fail-closed eligibility gate for CFB betting-card publication.

Research artifacts may still be shown, but must never be labeled executable.
Usage: python scripts/check_cfb_execution_gate.py --board app/latest/board.json --games "evt_...,evt_..." --now 2026-10-17T15:00:00Z
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

def evaluate(board, game_ids, now, max_quote_age_minutes=15):
    failures = []
    if board.get("bet_authority") != "EXECUTABLE":
        failures.append("RESEARCH_ONLY_OR_UNKNOWN_AUTHORITY")
    items = {item.get("event_id"): item for item in board.get("items", [])}
    for game_id in game_ids:
        game = items.get(game_id)
        if game is None:
            failures.append(f"{game_id}:MISSING_GAME")
            continue
        if game.get("status") not in ("SCHEDULED", "PRE_GAME", "PREGAME"):
            failures.append(f"{game_id}:NOT_PREGAME")
        if not game.get("markets_available"):
            failures.append(f"{game_id}:NO_MARKET_INVENTORY")
        if not game.get("markets_priced"):
            failures.append(f"{game_id}:NO_CALIBRATED_MARKET_PRICES")
        if game.get("health_flags"):
            failures.append(f"{game_id}:HEALTH_FLAGS:{','.join(game['health_flags'])}")
        captured = game.get("market_captured_at")
        if not captured:
            failures.append(f"{game_id}:MISSING_CAPTURE_TIMESTAMP")
        else:
            try:
                age = (now - parse_time(captured)).total_seconds() / 60
                if age < -1 or age > max_quote_age_minutes:
                    failures.append(f"{game_id}:QUOTE_AGE_{age:.0f}_MINUTES")
            except (ValueError, TypeError):
                failures.append(f"{game_id}:INVALID_CAPTURE_TIMESTAMP")
    return {"status": "BLOCKED" if failures else "BOARD_PRECHECK_PASSED",
            "note": "Passing this board precheck is NOT permission to publish a betting card. Full contract, injury, opponent-quality, fee, liquidity, script and staking gates remain mandatory.",
            "games_requested": len(game_ids), "failures": failures}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", type=Path, default=Path("app/latest/board.json"))
    parser.add_argument("--games", required=True, help="Comma-separated event IDs for the requested slate")
    parser.add_argument("--now", help="UTC ISO timestamp; defaults to current UTC")
    parser.add_argument("--max-quote-age-minutes", type=int, default=15)
    args = parser.parse_args()
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    result = evaluate(json.loads(args.board.read_text()), [g.strip() for g in args.games.split(",") if g.strip()], now, args.max_quote_age_minutes)
    print(json.dumps(result, indent=2))
    return 1 if result["status"] == "BLOCKED" else 0

if __name__ == "__main__":
    sys.exit(main())
