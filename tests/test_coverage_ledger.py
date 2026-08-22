import pytest

from cfb_edge_finder.kalshi.coverage_ledger import CoverageInvariantError, CoverageLedger
from cfb_edge_finder.schemas.common import TERMINAL_MARKET_STATUSES, MarketStatus


def test_record_discovered_then_transition_updates_history():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    entry = ledger.transition("TICK-1", MarketStatus.MAPPED, game_id="cfb-2026-wk01-baylor-at-auburn")
    assert entry.current_status == MarketStatus.MAPPED
    assert [t.status for t in entry.history] == [MarketStatus.DISCOVERED, MarketStatus.MAPPED]
    assert entry.game_id == "cfb-2026-wk01-baylor-at-auburn"


def test_transitioning_unknown_ticker_fails_loud():
    ledger = CoverageLedger()
    with pytest.raises(CoverageInvariantError):
        ledger.transition("NEVER-SEEN", MarketStatus.ACCEPTED)


def test_double_discovery_fails_loud():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    with pytest.raises(CoverageInvariantError):
        ledger.record_discovered("TICK-1")


def test_summary_covers_every_status_in_the_closed_vocabulary():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    ledger.transition("TICK-1", MarketStatus.ACCEPTED)
    ledger.record_discovered("TICK-2")
    ledger.transition("TICK-2", MarketStatus.REJECTED)

    summary = ledger.summary()
    assert set(summary.keys()) == set(MarketStatus)
    assert summary[MarketStatus.ACCEPTED] == 1
    assert summary[MarketStatus.REJECTED] == 1
    assert sum(summary.values()) == len(ledger)


def test_assert_no_missing_passes_when_everything_discovered():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    ledger.record_discovered("TICK-2")
    ledger.assert_no_missing({"TICK-1", "TICK-2"})


def test_assert_no_missing_fails_loud_on_a_silently_dropped_market():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    # TICK-2 was seen in the raw sweep but never made it into the ledger --
    # this is exactly the "silently dropped market" failure mode the ledger
    # must catch.
    with pytest.raises(CoverageInvariantError, match="TICK-2"):
        ledger.assert_no_missing({"TICK-1", "TICK-2"})


def test_every_market_status_is_reachable_and_categorized_terminal_or_not():
    # Guards against a status being added to the enum but never classified
    # as terminal or non-terminal, which would make TERMINAL_MARKET_STATUSES
    # silently stale.
    non_terminal = {
        MarketStatus.DISCOVERED,
        MarketStatus.TICKER_UNRESOLVED,
        MarketStatus.MAPPED,
        MarketStatus.WATCH,
        MarketStatus.EARLY_VALUE,
    }
    assert non_terminal | TERMINAL_MARKET_STATUSES == set(MarketStatus)
    assert non_terminal & TERMINAL_MARKET_STATUSES == set()
