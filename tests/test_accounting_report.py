"""The summary is about a BANKROLL, and must not be readable as a model result.

    CFB MODEL STATUS = RESEARCH ONLY / DISABLED
    CFB ACCOUNTING   = ENABLED

A won-lost record computed from this ledger looks, at a glance, exactly like
the number a backtest produces. The difference is that not one of these wagers
was recommended by this repository -- and a difference that exists only in a
docstring is a difference that gets lost.
"""

from __future__ import annotations

import os
import sys

from cfb_edge_finder.accounting import report

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
)


def wager(**overrides):
    row = {
        "source_bet_key": "kalshi:v1:a",
        "market_ticker": "KXNCAAFGAME-26SEP12ALAUGA-ALA",
        "side": "YES",
        "contracts": 25.0,
        "execution_price": 0.47,
        "stake": 11.94,
        "fees_paid": 0.19,
    }
    row.update(overrides)
    return row


def test_the_disclaimer_is_data_and_is_printed():
    """Carried as a FIELD rather than left to a caller's docstring, so a reader
    who sees only the output still sees it."""
    summary = report.summarize([wager()], 2026)

    assert "is not evidence about the CFB model" in summary.not_model_evidence
    rendered = report.render(summary)
    assert rendered.startswith(report.NOT_MODEL_EVIDENCE)


def test_the_money_is_the_ledgers_money():
    summary = report.summarize([wager(), wager(source_bet_key="kalshi:v1:b")], 2026)

    assert summary.wagers == 2
    assert summary.contracts == 50.0
    assert round(summary.staked, 2) == 23.88
    assert round(summary.fees_paid, 2) == 0.38


def test_an_unsettled_wager_is_counted_as_unsettled_not_as_a_loss():
    """Settlement is a LATER observation than the wager. Absent is unknown, and
    unknown counted as a loss would overstate a drawdown that never happened."""
    summary = report.summarize([wager()], 2026)

    assert (summary.settled, summary.unsettled) == (0, 1)
    assert (summary.won, summary.lost) == (0, 0)


def test_a_partial_profit_and_loss_is_never_stated_as_the_seasons():
    """THE DEFECT THIS EXISTS TO PREVENT.

    A total assembled from three of eighteen wagers, printed beside "wagers
    recorded: 18", will be read as the season's. So no total is printed at all
    until every wager's P&L is established, and the count that is missing is
    stated instead.
    """
    rows = [
        wager(settlement_status="SETTLED", result="WON", net_profit_loss=5.00),
        wager(source_bet_key="kalshi:v1:b"),
    ]
    summary = report.summarize(rows, 2026)

    assert summary.profit_loss_established == 1
    assert summary.profit_loss_unestablished == 1
    assert summary.profit_loss_is_complete is False

    rendered = report.render(summary)
    assert "UNESTABLISHED for 1 of 2 wagers" in rendered
    assert "+5.00" not in rendered, rendered


def test_a_complete_profit_and_loss_is_stated():
    """The positive control: without it, the test above would pass just as
    happily against a renderer that never prints a total at all."""
    rows = [
        wager(settlement_status="SETTLED", result="WON", net_profit_loss=5.00),
        wager(source_bet_key="kalshi:v1:b", settlement_status="SETTLED",
              result="LOST", net_profit_loss=-11.94),
    ]
    summary = report.summarize(rows, 2026)

    assert summary.profit_loss_is_complete is True
    assert round(summary.realized_profit_loss, 2) == -6.94
    assert "-6.94 (every wager established)" in report.render(summary)


def test_a_zero_is_never_substituted_for_an_unknown():
    """Zero is a settled result that happens to pay nothing, which is a
    different claim from 'not yet known'."""
    summary = report.summarize([wager(net_profit_loss=None)], 2026)

    assert summary.profit_loss_unestablished == 1
    assert summary.profit_loss_established == 0


def test_two_wagers_on_one_market_and_side_are_visible():
    """It decides whether a settlement can be attributed to ONE wager.

    A Kalshi settlement is per market and per position, not per order, so where
    two orders share a market and side the payout covers both and splitting it
    would be a derivation rather than exchange evidence. The count has to be
    visible before anyone states a per-wager return.
    """
    rows = [wager(), wager(source_bet_key="kalshi:v1:b")]
    summary = report.summarize(rows, 2026)

    assert summary.wagers == 2
    assert len(summary.markets) == 1
    assert list(summary.markets.values()) == [2]


def test_the_report_exposes_no_decision_surface():
    """The same rule the package carries, applied to its newest module: this
    summarizes, it does not size, rank, price or recommend."""
    from tests.test_accounting_isolation import _forbidden_token

    violations = [
        name for name in dir(report)
        if not name.startswith("_") and _forbidden_token(name) is not None
    ]
    assert violations == [], violations


def test_a_season_with_no_ledger_is_not_reported_as_a_season_with_no_wagers(tmp_path, capsys):
    """Different facts. Printing zeros would state the second one."""
    import report_accounted_wagers as script

    assert script.main(["--base-dir", str(tmp_path), "--season", "2026"]) == 1
    assert "no ledger for season 2026" in capsys.readouterr().err


def test_the_script_prints_the_disclaimer_before_any_number(tmp_path, capsys):
    import report_accounted_wagers as script

    from cfb_edge_finder.accounting import store

    store.append_wagers(tmp_path, 2026, [{
        "wager_id": "routed-abc", "schema_version": "cfb_accounted_wager.v1",
        "source_bet_key": "kalshi:v1:a", "import_batch_id": "batch",
        "entry_method": "IMPORTED_RECEIPT", "game_date": "2026-09-12",
        "market_ticker": "KXNCAAFGAME-26SEP12ALAUGA-ALA", "side": "YES",
        "executed_at": "2026-09-12T20:40:11Z", "contracts": 25.0,
        "execution_price": 0.47, "stake": 11.94, "fees_paid": 0.19,
    }])

    assert script.main(["--base-dir", str(tmp_path), "--season", "2026"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith(report.NOT_MODEL_EVIDENCE)
    assert "wagers recorded: 1" in printed
