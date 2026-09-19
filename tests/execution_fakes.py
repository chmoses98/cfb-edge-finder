"""A small, fully-controlled Kalshi catalog for the execution tests.

Built by hand rather than sampled from `data/live`, so a test that says
"143 discovered, 17 excluded, 126 eligible" means exactly that and keeps
meaning it next Saturday.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
CAPTURED_AT = datetime(2026, 9, 19, 11, 45, tzinfo=UTC)

GAME_KEY = "26SEP19LSUMISS"
EVENT = "KXNCAAFGAME-" + GAME_KEY


def fee_block(
    support: str = "supported",
    model: str | None = "quadratic",
    yes_ask: float | None = 0.5,
    no_ask: float | None = 0.5,
) -> dict[str, Any]:
    def trade_fee(price: float | None) -> float | None:
        if price is None or support != "supported":
            return None
        return round(0.07 * price * (1 - price) + 5e-7, 6)

    return {
        "model": model,
        "multiplier": 1.0,
        "support": support,
        "model_trade_fee_at_yes_ask": trade_fee(yes_ask),
        "model_trade_fee_at_no_ask": trade_fee(no_ask),
        "basis_yes_ask": yes_ask,
        "basis_no_ask": no_ask,
        "excludes": ["rounding_fee", "rebate"],
    }


def market(
    ticker: str,
    *,
    family: str,
    period: str = "full_game",
    title: str,
    yes_sub_title: str | None = None,
    floor_strike: float | None = None,
    cap_strike: float | None = None,
    strike_type: str = "greater",
    custom_strike: dict[str, Any] | None = None,
    yes_bid: float | None = 0.48,
    yes_ask: float | None = 0.5,
    no_bid: float | None = 0.48,
    no_ask: float | None = 0.5,
    status: str = "active",
    event_ticker: str | None = None,
    series_ticker: str | None = None,
    close_time: str = "2026-09-21T23:30:00Z",
    updated_time: str = "2026-09-19T11:40:00Z",
    fee: dict[str, Any] | None = None,
    is_alternate_line: bool = False,
) -> dict[str, Any]:
    return {
        "market_ticker": ticker,
        "event_ticker": event_ticker or ticker.rsplit("-", 1)[0],
        "series_ticker": series_ticker or ticker.split("-", 1)[0],
        "family": family,
        "period": period,
        "title": title,
        "yes_sub_title": yes_sub_title if yes_sub_title is not None else title,
        "no_sub_title": yes_sub_title if yes_sub_title is not None else title,
        "floor_strike": floor_strike,
        "cap_strike": cap_strike,
        "strike_type": strike_type,
        "custom_strike": custom_strike,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "status": status,
        "close_time": close_time,
        "updated_time": updated_time,
        "is_alternate_line": is_alternate_line,
        "fee": fee if fee is not None else fee_block(yes_ask=yes_ask, no_ask=no_ask),
        "rules_primary": title,
    }


def standard_markets(game_key: str = GAME_KEY) -> list[dict[str, Any]]:
    """One of everything: moneyline (with a tie), a spread ladder on both
    teams, a total ladder, team totals, and families that can only be
    priced explicitly.

    `game_key` rewrites the tickers so several fixture games can coexist
    in one catalog. Tickers are the join key AND the duplicate key -- two
    fixture games sharing them would both be right and would reconcile to
    nonsense."""
    markets = [
        market(
            f"KXNCAAFGAME-{game_key}-LSU",
            family="game_moneyline",
            title="Will LSU win the LSU vs Ole Miss college football game?",
            yes_sub_title="LSU",
            strike_type="structured",
            yes_ask=0.59,
            yes_bid=0.58,
            no_ask=0.42,
            no_bid=0.41,
        ),
        market(
            f"KXNCAAFGAME-{game_key}-MISS",
            family="game_moneyline",
            title="Will Ole Miss win the LSU vs Ole Miss college football game?",
            yes_sub_title="Ole Miss",
            strike_type="structured",
            yes_ask=0.42,
            no_ask=0.59,
        ),
    ]
    for line in (2.5, 6.5, 9.5):
        markets.append(
            market(
                f"KXNCAAFSPREAD-{game_key}-LSU{int(line + 1)}",
                family="game_spread",
                title=f"LSU wins by over {line} points",
                floor_strike=line,
                is_alternate_line=True,
                yes_ask=0.4,
                no_ask=0.61,
            )
        )
        markets.append(
            market(
                f"KXNCAAFSPREAD-{game_key}-MISS{int(line + 1)}",
                family="game_spread",
                title=f"Ole Miss wins by over {line} points",
                floor_strike=line,
                is_alternate_line=True,
                yes_ask=0.33,
                no_ask=0.68,
            )
        )
    for line in (44.5, 51.5):
        markets.append(
            market(
                f"KXNCAAFTOTAL-{game_key}-{int(line + 1)}",
                family="game_total",
                title=f"Over {line} points scored",
                floor_strike=line,
                is_alternate_line=True,
                yes_ask=0.55,
                no_ask=0.46,
            )
        )
    for team, label in (("LSU", "LSU"), ("MISS", "Ole Miss")):
        for line in (20.5, 27.5):
            markets.append(
                market(
                    f"KXNCAAFTEAMTOTAL-{game_key}-{team}{int(line + 1)}",
                    family="team_total",
                    title=f"{label} scores over {line} points",
                    yes_sub_title=f"{label} over {line} points scored",
                    floor_strike=line,
                    is_alternate_line=True,
                    yes_ask=0.5,
                    no_ask=0.51,
                )
            )
    markets.append(
        market(
            f"KXNCAAF1H-{game_key}-TIE",
            family="first_half_moneyline",
            period="first_half",
            title="Tie in the 1st half",
            yes_sub_title="Tie 1st Half",
            strike_type="structured",
            yes_ask=0.09,
            no_ask=0.93,
        )
    )
    markets.append(
        market(
            f"KXNCAAFFIRSTTDTEAM-{game_key}-LSU",
            family="touchdown_scorer",
            title="LSU scores first TD",
            strike_type="structured",
            yes_ask=0.62,
            no_ask=0.46,
        )
    )
    markets.append(
        market(
            f"KXNCAAF1HFT-{game_key}-LSULSU",
            family="unknown",
            period="unknown",
            title="LSU wins 1st Half / LSU wins game",
            strike_type="custom",
            custom_strike={"1st Half Result": "LSU wins 1st Half"},
            yes_ask=0.5,
            no_ask=0.65,
        )
    )
    return markets


def catalog_dir(
    tmp_path: Path,
    markets: list[dict[str, Any]] | None = None,
    *,
    kickoff: str = "2026-09-19T23:30:00Z",
    captured_at: datetime = CAPTURED_AT,
    title: str = "LSU at Ole Miss",
    game_key: str = GAME_KEY,
    extra_games: list[tuple[str, str, str, list[dict[str, Any]]]] | None = None,
) -> Path:
    """Write a catalog directory the slate builder can read."""
    rows = markets if markets is not None else standard_markets(game_key)
    games = [(game_key, title, kickoff, rows)]
    games.extend(extra_games or [])

    directory = tmp_path / "live"
    (directory / "games").mkdir(parents=True, exist_ok=True)

    index_games = []
    for key, game_title, game_kickoff, game_markets in games:
        detail = {
            "game_key": key,
            "title": game_title,
            "kickoff": game_kickoff,
            "market_count": len(game_markets),
            "markets": game_markets,
            "events": [
                {
                    "event_ticker": f"KXNCAAFGAME-{key}",
                    "competition_scope": "Game",
                    "title": game_title,
                }
            ],
            "completeness": {
                "native_game_markets_complete": True,
                "markets_discovered": len(game_markets),
                "api_failures": 0,
                "failed_event_tickers": [],
            },
        }
        (directory / "games" / f"{key}.json").write_text(json.dumps(detail), encoding="utf-8")
        index_games.append(
            {
                "game_key": key,
                "title": game_title,
                "kickoff": game_kickoff,
                "market_count": len(game_markets),
                "markets_file": f"games/{key}.json",
                "identity": {
                    "conference": "SEC",
                    "division": "FBS",
                    "season_year": 2026,
                    "season_week": 3,
                    "season_type": "REG",
                    "tier": "A",
                    "milestone_status": "scheduled",
                    "main_game_event_ticker": f"KXNCAAFGAME-{key}",
                },
            }
        )

    index = {
        "schema_version": "cfb_market_catalog/1.0.0",
        "capture": {
            "captured_at": captured_at.isoformat(),
            "content_fingerprint": "fingerprint",
        },
        "completeness": {"capture_complete": True},
        "games": index_games,
        "season_level_events": [],
    }
    (directory / "cfb_market_catalog.json").write_text(json.dumps(index), encoding="utf-8")
    return directory
