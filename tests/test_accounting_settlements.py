"""A settlement is a LATER and SEPARATE observation than the wager it settles.

The wager ledger is append-only because a wager record is a claim about money
that already moved. That reasoning does not stop applying just because the new
information is welcome, so settlement is a separate row rather than an edit.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from cfb_edge_finder.accounting import report, settlement, store
from cfb_edge_finder.accounting.import_settlements import (
    SettlementRefused,
    build_record,
    import_rows,
    mint_settlement_id,
)

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
)

KEY = "kalshi:v1:3a1f7c5d2e8b49a0c6f1d84b7e2905cfa3b6d17e"
TICKER = "KXNCAAFGAME-26SEP12ALAUGA-ALA"
SEASON = 2026


def wager_row(**overrides):
    row = {
        "wager_id": "routed-abc", "schema_version": "cfb_accounted_wager.v1",
        "source_bet_key": KEY, "import_batch_id": "batch",
        "entry_method": "IMPORTED_RECEIPT", "game_date": "2026-09-12",
        "market_ticker": TICKER, "side": "YES",
        "executed_at": "2026-09-12T20:40:11Z", "contracts": 25.0,
        "execution_price": 0.47, "stake": 11.94, "fees_paid": 0.19,
    }
    row.update(overrides)
    return row


#: Exactly the shape kalshi_router.settlement.WagerSettlement produces.
ROUTER_ROW = {
    "source_bet_key": KEY,
    "market_ticker": TICKER,
    "side": "YES",
    "settlement_status": "SETTLED",
    "settled_at": "2026-09-13T02:00:00Z",
    "result": "WON",
    "gross_return": 25.0,
    "net_profit_loss": 13.06,
    "refusals": [],
}


def seeded(tmp_path, **overrides):
    store.append_wagers(tmp_path, SEASON, [wager_row(**overrides)])
    return tmp_path


# ------------------------------------------------------------- the record

def test_the_exact_exchange_numbers_survive():
    record = build_record(ROUTER_ROW)

    assert record.result == "WON"
    assert record.gross_return == 25.0
    assert record.net_profit_loss == 13.06
    assert record.settled_at == "2026-09-13T02:00:00Z"
    assert record.schema_version == "cfb_wager_settlement.v1"


def test_the_id_comes_from_the_wagers_key_not_from_the_payout():
    """A correction to a result or a payout must land on the same row rather
    than beside it."""
    base = mint_settlement_id(KEY)

    assert mint_settlement_id(KEY) == base
    assert base != mint_settlement_id("kalshi:v1:other")
    assert base.startswith("stl-")


def test_no_model_provenance_is_fabricated():
    """A settlement proves what the exchange paid. It proves nothing about what
    recommended the bet."""
    record = build_record(ROUTER_ROW).to_dict()

    for field in settlement.FORBIDDEN_PROVENANCE_FIELDS:
        assert field not in record


def test_a_pending_row_is_refused_outright():
    """An unsettled wager is recorded by having NO settlement row.

    A PENDING row would have to be superseded when the market settles, in a
    store whose entire guarantee is that rows are not superseded.
    """
    with pytest.raises(SettlementRefused, match="having no settlement row"):
        build_record(dict(ROUTER_ROW, settlement_status="PENDING"))


def test_a_missing_figure_must_carry_its_reason():
    """A null with no explanation is the shape a reader fills in with a zero."""
    with pytest.raises(SettlementRefused, match="no refusal recorded"):
        build_record(dict(ROUTER_ROW, net_profit_loss=None))


def test_a_settled_row_with_a_refused_figure_is_written():
    """"The market settled and the return could not be established" is a
    durable fact with a durable cause, and worth recording."""
    record = build_record(dict(ROUTER_ROW, net_profit_loss=None,
                               refusals=["shared_position_fee"]))

    assert record.gross_return == 25.0
    assert record.net_profit_loss is None
    assert record.refusals == ["shared_position_fee"]


def test_a_refusal_alongside_a_complete_figure_is_itself_refused():
    """A reason for an absence, next to no absence, means one of the two is
    wrong and neither may be assumed."""
    with pytest.raises(SettlementRefused, match="nothing to refuse"):
        build_record(dict(ROUTER_ROW, refusals=["no_settlement_price"]))


def test_a_field_this_ledger_does_not_model_is_refused():
    with pytest.raises(SettlementRefused, match="unknown field"):
        build_record(dict(ROUTER_ROW, model_supported=True))


# -------------------------------------------------------------- the import

def test_a_settlement_for_a_known_wager_is_written(tmp_path):
    result = import_rows(seeded(tmp_path), [ROUTER_ROW], season=SEASON)

    assert (result["written"], result["refused"]) == (1, 0)
    rows = store.read_rows(store.settlement_ledger_path(tmp_path, SEASON))
    assert len(rows) == 1 and rows[0]["result"] == "WON"


def test_a_settlement_for_a_wager_this_ledger_never_saw_is_refused(tmp_path):
    """THE test. A payout attributed to a bet this repository has no record of
    would count in every total while belonging to nothing."""
    result = import_rows(
        seeded(tmp_path), [dict(ROUTER_ROW, source_bet_key="kalshi:v1:ghost")],
        season=SEASON,
    )

    assert (result["written"], result["refused"]) == (0, 1)
    assert "no wager with this source_bet_key" in result["refusals"][0][1]
    assert not store.settlement_ledger_path(tmp_path, SEASON).exists()


def test_running_the_settlement_pass_twice_writes_one_row(tmp_path):
    """A market settles once, so a second observation is a duplicate rather
    than a correction."""
    root = seeded(tmp_path)
    first = import_rows(root, [ROUTER_ROW], season=SEASON)
    second = import_rows(root, [ROUTER_ROW], season=SEASON)

    assert (first["written"], first["already_present"]) == (1, 0)
    assert (second["written"], second["already_present"]) == (0, 1)
    assert len(store.read_rows(store.settlement_ledger_path(tmp_path, SEASON))) == 1


def test_one_refused_settlement_does_not_stop_the_others(tmp_path):
    root = tmp_path
    store.append_wagers(root, SEASON, [
        wager_row(), wager_row(wager_id="routed-b", source_bet_key="kalshi:v1:b"),
    ])
    result = import_rows(root, [
        dict(ROUTER_ROW, source_bet_key="kalshi:v1:ghost"),
        dict(ROUTER_ROW, source_bet_key="kalshi:v1:b"),
    ], season=SEASON)

    assert (result["written"], result["refused"]) == (1, 1)


def test_the_wager_ledger_itself_is_never_touched(tmp_path):
    """Settlement is a separate row. The wager's own bytes do not move."""
    root = seeded(tmp_path)
    before = store.ledger_path(root, SEASON).read_bytes()

    import_rows(root, [ROUTER_ROW], season=SEASON)

    assert store.ledger_path(root, SEASON).read_bytes() == before


# -------------------------------------------------------------- the report

def test_the_report_joins_settlements_to_wagers(tmp_path):
    root = seeded(tmp_path)
    import_rows(root, [ROUTER_ROW], season=SEASON)

    summary = report.summarize(
        store.read_rows(store.ledger_path(root, SEASON)), SEASON,
        settlements=store.read_rows(store.settlement_ledger_path(root, SEASON)),
    )

    assert (summary.settled, summary.unsettled) == (1, 0)
    assert summary.won == 1
    assert summary.profit_loss_is_complete is True
    assert round(summary.realized_profit_loss, 2) == 13.06


def test_a_wager_with_no_settlement_row_is_unsettled(tmp_path):
    """The absence IS the record, which is why no PENDING row is ever written."""
    root = seeded(tmp_path)

    summary = report.summarize(
        store.read_rows(store.ledger_path(root, SEASON)), SEASON, settlements=[],
    )

    assert (summary.settled, summary.unsettled) == (0, 1)
    assert summary.profit_loss_is_complete is False


def test_a_settled_wager_whose_pl_was_refused_is_settled_but_unestablished(tmp_path):
    """Both facts at once, and the report must not collapse them."""
    root = seeded(tmp_path)
    import_rows(root, [dict(ROUTER_ROW, net_profit_loss=None,
                            refusals=["shared_position_fee"])], season=SEASON)

    summary = report.summarize(
        store.read_rows(store.ledger_path(root, SEASON)), SEASON,
        settlements=store.read_rows(store.settlement_ledger_path(root, SEASON)),
    )

    assert summary.settled == 1
    assert summary.won == 1
    assert summary.profit_loss_unestablished == 1
    assert "UNESTABLISHED for 1 of 1 wagers" in report.render(summary)


# -------------------------------------------------------------- the script

def test_the_script_prints_no_payout(tmp_path, capsys):
    import import_routed_settlements as script

    root = seeded(tmp_path)
    payload = tmp_path / "CFB-settlements.json"
    payload.write_text(json.dumps({"settlements": [ROUTER_ROW]}), encoding="utf-8")

    assert script.main([
        "--payload", str(payload), "--base-dir", str(root), "--season", str(SEASON),
    ]) == script.EXIT_OK

    printed = capsys.readouterr().out
    assert "written:         1" in printed
    for sensitive in (TICKER, "25.0", "13.06", KEY):
        assert sensitive not in printed, printed


def test_the_script_fails_when_a_settlement_is_refused(tmp_path):
    import import_routed_settlements as script

    root = seeded(tmp_path)
    payload = tmp_path / "CFB-settlements.json"
    payload.write_text(
        json.dumps({"settlements": [dict(ROUTER_ROW, source_bet_key="kalshi:v1:ghost")]}),
        encoding="utf-8",
    )

    assert script.main([
        "--payload", str(payload), "--base-dir", str(root), "--season", str(SEASON),
    ]) == script.EXIT_REFUSED
