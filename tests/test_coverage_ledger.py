import pytest
from pydantic import ValidationError

from cfb_edge_finder.kalshi.coverage_ledger import CoverageInvariantError, CoverageLedger
from cfb_edge_finder.schemas.common import TERMINAL_COVERAGE_OUTCOMES, CoverageOutcome, RecommendationReadiness


def test_record_discovered_then_transition_updates_history():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    entry = ledger.transition("TICK-1", CoverageOutcome.MAPPED, game_id="cfb-2026-wk01-baylor-at-auburn")
    assert entry.current_outcome == CoverageOutcome.MAPPED
    assert [t.outcome for t in entry.history] == [CoverageOutcome.DISCOVERED, CoverageOutcome.MAPPED]
    assert entry.game_id == "cfb-2026-wk01-baylor-at-auburn"


def test_transitioning_unknown_ticker_fails_loud():
    ledger = CoverageLedger()
    with pytest.raises(CoverageInvariantError):
        ledger.transition("NEVER-SEEN", CoverageOutcome.EVALUATED)


def test_double_discovery_fails_loud():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    with pytest.raises(CoverageInvariantError):
        ledger.record_discovered("TICK-1")


def test_summary_covers_every_outcome_in_the_closed_vocabulary():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    ledger.transition("TICK-1", CoverageOutcome.EVALUATED)
    ledger.record_discovered("TICK-2")
    ledger.transition("TICK-2", CoverageOutcome.MISSING_INPUT)

    summary = ledger.summary()
    assert set(summary.keys()) == set(CoverageOutcome)
    assert summary[CoverageOutcome.EVALUATED] == 1
    assert summary[CoverageOutcome.MISSING_INPUT] == 1
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


def test_every_coverage_outcome_is_reachable_and_categorized_terminal_or_not():
    # Guards against an outcome being added to the enum but never
    # classified as terminal or non-terminal, which would make
    # TERMINAL_COVERAGE_OUTCOMES silently stale.
    non_terminal = {CoverageOutcome.DISCOVERED, CoverageOutcome.MAPPED}
    assert non_terminal | TERMINAL_COVERAGE_OUTCOMES == set(CoverageOutcome)
    assert non_terminal & TERMINAL_COVERAGE_OUTCOMES == set()


# --- Orthogonality: recommendation readiness must never affect coverage accounting ---


def test_recommendation_readiness_requires_evaluated_outcome():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    ledger.transition("TICK-1", CoverageOutcome.MAPPED)
    with pytest.raises(ValidationError):
        ledger.set_recommendation_readiness("TICK-1", RecommendationReadiness.WATCH)


def test_market_at_watch_or_early_value_still_counts_as_evaluated_in_coverage_summary():
    # This is the exact scenario the mission flagged: a market must never
    # disappear from completeness accounting merely because it's WATCH or
    # EARLY_VALUE. Both axes are checked independently here.
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    ledger.transition("TICK-1", CoverageOutcome.EVALUATED)
    ledger.set_recommendation_readiness("TICK-1", RecommendationReadiness.WATCH)

    ledger.record_discovered("TICK-2")
    ledger.transition("TICK-2", CoverageOutcome.EVALUATED)
    ledger.set_recommendation_readiness("TICK-2", RecommendationReadiness.EARLY_VALUE)

    coverage = ledger.summary()
    assert coverage[CoverageOutcome.EVALUATED] == 2  # both fully accounted for, regardless of readiness
    assert sum(coverage.values()) == 2

    readiness = ledger.readiness_summary()
    assert readiness[RecommendationReadiness.WATCH] == 1
    assert readiness[RecommendationReadiness.EARLY_VALUE] == 1
    assert sum(readiness.values()) == 2  # the orthogonal axis also accounts for every entry


def test_transitioning_off_evaluated_clears_recommendation_readiness():
    # If a market is re-evaluated and its outcome moves off EVALUATED (e.g.
    # the game started before a re-price completed), any stale readiness
    # judgment from the prior evaluation must not linger.
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")
    ledger.transition("TICK-1", CoverageOutcome.EVALUATED)
    ledger.set_recommendation_readiness("TICK-1", RecommendationReadiness.ACTIONABLE)

    entry = ledger.transition("TICK-1", CoverageOutcome.GAME_STARTED)
    assert entry.recommendation_readiness is None


def test_readiness_summary_and_coverage_summary_use_independent_denominators():
    ledger = CoverageLedger()
    ledger.record_discovered("TICK-1")  # stays DISCOVERED, never evaluated
    ledger.record_discovered("TICK-2")
    ledger.transition("TICK-2", CoverageOutcome.EVALUATED)
    ledger.set_recommendation_readiness("TICK-2", RecommendationReadiness.PASS)

    assert sum(ledger.summary().values()) == 2
    assert sum(ledger.readiness_summary().values()) == 2
    assert ledger.readiness_summary()[None] == 1  # TICK-1: not yet evaluated, no readiness judgment possible
