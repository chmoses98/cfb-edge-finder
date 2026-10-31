"""Synthetic tests: read-only recent wager analysis (no account access)."""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_routed_cfb_bets import audit, eastern_date  # noqa: E402


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in rows))


def test_loss_review_and_amendment(tmp_path):
    base = tmp_path
    shared = {"executed_at": "2026-10-24T22:00:00Z", "fees_paid": 1, "game_date": "2026-10-24"}
    wagers = [
        dict(shared, source_bet_key="a", market_ticker="KXNCAAFSPREAD-26OCT24FOOBAR-FOO8", side="YES", stake=75, execution_price=.52),
        dict(shared, source_bet_key="b", market_ticker="KXNCAAFTEAMTOTAL-26OCT24FOOBAR-BAR21", side="NO", stake=150, execution_price=.55),
        dict(shared, source_bet_key="c", market_ticker="KXNCAAFGAME-26OCT24FOOBAR-FOO", side="YES", stake=75, execution_price=.5),
    ]
    settlements = [
        {"source_bet_key": "a", "settlement_id": "s1", "result": "LOST", "net_profit_loss": -75},
        {"source_bet_key": "b", "settlement_id": "s2", "result": "WON", "net_profit_loss": 120},
    ]
    amendments = [
        {"source_bet_key": "b", "amends_settlement_id": "s2", "result": "WON", "net_profit_loss": 135},
    ]
    _write(base / "wagers/2026.jsonl", wagers)
    _write(base / "settlements/2026.jsonl", settlements)
    _write(base / "settlement_amendments/2026.jsonl", amendments)
    out = audit(base, "2026-10-22", unit=Decimal("75"))
    assert out["overall"]["wagers"] == 3
    assert out["overall"]["settled"] == 2
    assert out["overall"]["pending"] == 1
    assert Decimal(out["overall"]["realized_pnl_dollars"]) == 60
    assert out["families"]["team_total"]["realized_pnl_dollars"] == "135"
    assert out["flags"]["multi_bet_games"][0]["bet_count"] == 3
    assert audit(base, "2026-10-25")["overall"]["wagers"] == 0


def test_eastern_execution_dates():
    assert eastern_date("2026-10-22T02:00:00Z") == "2026-10-21"
    assert eastern_date("2026-10-22T23:00:00Z") == "2026-10-22"


def test_fail_closed_on_orphan_amendment(tmp_path):
    _write(tmp_path / "wagers/2026.jsonl", [
        {"source_bet_key": "k", "executed_at": "2026-10-24T22:00:00Z",
         "market_ticker": "KXNCAAFGAME-26OCT24FOOBAR-FOO", "side": "YES", "stake": 75, "fees_paid": 2}
    ])
    _write(tmp_path / "settlements/2026.jsonl", [])
    _write(tmp_path / "settlement_amendments/2026.jsonl", [
        {"source_bet_key": "k", "amends_settlement_id": "s", "result": "WON", "net_profit_loss": 50}
    ])
    import pytest
    with pytest.raises(ValueError, match="Orphan"):
        audit(tmp_path, "2026-10-22")
