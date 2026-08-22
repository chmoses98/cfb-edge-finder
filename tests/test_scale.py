"""Deterministic synthetic scale test (mission audit section 7): a
realistic weekly universe -- 80 games, ~150 markets/game (12,000 tickers,
consistent with "thousands to tens of thousands of Kalshi contracts") --
run through the id/coverage-ledger layer, checking for duplicate ids,
silent drops, invalid transitions, and grossly non-linear runtime. This is
a representative check, not a benchmark suite: thresholds are generous on
purpose to avoid CI flakiness while still catching a real regression (e.g.
an accidental O(n^2) scan introduced into CoverageLedger).
"""

from __future__ import annotations

import time

from cfb_edge_finder.ids import assert_unique_game_ids, canonical_game_id, slugify_team
from cfb_edge_finder.kalshi.coverage_ledger import CoverageLedger
from cfb_edge_finder.schemas.common import CoverageOutcome, RecommendationReadiness

GAMES_PER_WEEK = 80
MARKETS_PER_GAME = 150  # moneyline + spread + several alt-spreads + total + several alt-totals + team totals


def _synthetic_game_ids(n_games: int, week_label: str = "wk01", season: int = 2026) -> list[str]:
    game_ids = []
    for i in range(n_games):
        away = slugify_team(f"Away Team {i}")
        home = slugify_team(f"Home Team {i}")
        game_ids.append(canonical_game_id(season, week_label, away, home))
    return game_ids


def _synthetic_tickers_for_game(game_id: str, n_markets: int) -> list[str]:
    return [f"{game_id}-MKT-{j}" for j in range(n_markets)]


def _run_full_week(n_games: int, n_markets_per_game: int) -> tuple[CoverageLedger, list[str], float]:
    game_ids = _synthetic_game_ids(n_games)
    assert_unique_game_ids(game_ids)  # no duplicate game IDs, even at scale

    all_tickers: list[str] = []
    for game_id in game_ids:
        all_tickers.extend(_synthetic_tickers_for_game(game_id, n_markets_per_game))

    ledger = CoverageLedger()
    start = time.perf_counter()
    for i, ticker in enumerate(all_tickers):
        game_id = game_ids[i // n_markets_per_game]
        ledger.record_discovered(ticker, game_id=game_id)
        ledger.transition(ticker, CoverageOutcome.MAPPED, game_id=game_id)
        ledger.transition(ticker, CoverageOutcome.EVALUATED, game_id=game_id)
        ledger.set_recommendation_readiness(ticker, RecommendationReadiness.PASS)
    elapsed = time.perf_counter() - start

    return ledger, all_tickers, elapsed


def test_full_week_scale_no_duplicates_no_drops_no_invalid_transitions():
    ledger, all_tickers, elapsed = _run_full_week(GAMES_PER_WEEK, MARKETS_PER_GAME)
    n_tickers = GAMES_PER_WEEK * MARKETS_PER_GAME

    assert len(ledger) == n_tickers  # nothing merged/overwritten silently
    assert len(set(all_tickers)) == n_tickers  # the synthetic tickers themselves are unique

    ledger.assert_no_missing(set(all_tickers))  # no silent drops

    summary = ledger.summary()
    assert summary[CoverageOutcome.EVALUATED] == n_tickers
    assert sum(summary.values()) == n_tickers

    readiness = ledger.readiness_summary()
    assert readiness[RecommendationReadiness.PASS] == n_tickers

    # Generous wall-clock bound: this is pure in-memory dict/pydantic work
    # on 12,000 tickers with 3 transitions + 1 readiness set each --
    # should complete in well under a second on any reasonable machine.
    # 15s is a deliberately loose ceiling to absorb CI noise while still
    # catching a real pathological-runtime regression.
    assert elapsed < 15.0, f"full-week scale run took {elapsed:.2f}s, expected well under 15s"


def test_coverage_ledger_operations_scale_roughly_linearly_not_quadratically():
    _, _, small_elapsed = _run_full_week(n_games=10, n_markets_per_game=100)  # 1,000 tickers
    _, _, large_elapsed = _run_full_week(n_games=40, n_markets_per_game=100)  # 4,000 tickers (4x)

    # A quadratic implementation would take ~16x as long for 4x the input;
    # linear/near-linear should take roughly 4x, plus interpreter/timing
    # noise. 10x is a generous ceiling that still clearly fails on O(n^2).
    # Guard against a near-zero small_elapsed making the ratio meaningless.
    if small_elapsed > 0.0005:
        assert large_elapsed < small_elapsed * 10, (
            f"runtime did not scale roughly linearly: {small_elapsed:.4f}s @ 1k tickers vs "
            f"{large_elapsed:.4f}s @ 4k tickers (ratio {large_elapsed / small_elapsed:.1f}x)"
        )
