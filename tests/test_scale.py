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


TIMING_REPEATS = 3

# 1,000 tickers against 12,000 -- the weekly universe this module is about.
# The separation is the point: see the docstring on the test below.
SMALL_GAMES = 10
LARGE_GAMES = 120
MARKETS = 100
INPUT_RATIO = LARGE_GAMES / SMALL_GAMES

# Linear work grows with the input; the ceiling therefore has to grow with it
# too, or the test just measures how big the inputs are. 2.5x headroom over
# perfectly linear absorbs per-item costs that are not constant (dict growth,
# allocation) without admitting a quadratic ledger.
LINEARITY_TOLERANCE = 2.5


def _fastest_full_week(n_games: int, n_markets_per_game: int) -> float:
    """The FASTEST of several runs, which is the honest estimate of cost.

    Scheduler preemption, a noisy neighbour on a shared runner and a GC pause
    can only ever ADD time to a sample; nothing makes a run finish faster than
    the work takes. So the minimum converges on the real cost while the mean
    and a single sample both track whatever else the machine was doing.

    Measured: a single sample gave ratios of 2.4x-5.7x across twelve local
    trials of one commit, and 14.0x once in CI, on a ledger that never
    changed. Best-of-three gave 3.6x-4.5x over the same work.
    """
    return min(
        _run_full_week(n_games, n_markets_per_game)[2] for _ in range(TIMING_REPEATS)
    )


def test_coverage_ledger_operations_scale_roughly_linearly_not_quadratically():
    """An accidental O(n^2) scan in CoverageLedger must fail this test.

    It did not, before. The comparison was 1,000 tickers against 4,000 with a
    flat 10x ceiling, and at that separation the quadratic term is swallowed
    by the linear base: adding a genuine `if t in seen` scan over a list --
    the exact regression the module docstring names -- measured 6.5x and
    sailed under the ceiling. A test that cannot fail on the defect it names
    is worse than no test, because it is read as evidence.

    Twelve times the input rather than four is what separates the two curves.
    Measured on this ledger: linear 15.6x, the same ledger with an O(n^2) scan
    37.4x, ceiling 30x. Both sides of that are checked -- the ceiling is loose
    enough that honest linear work passes, and tight enough that quadratic
    work cannot.
    """
    small_elapsed = _fastest_full_week(SMALL_GAMES, MARKETS)
    large_elapsed = _fastest_full_week(LARGE_GAMES, MARKETS)

    ceiling = INPUT_RATIO * LINEARITY_TOLERANCE
    # Guard against a near-zero small_elapsed making the ratio meaningless.
    if small_elapsed > 0.0005:
        ratio = large_elapsed / small_elapsed
        assert ratio < ceiling, (
            f"runtime did not scale roughly linearly: {small_elapsed:.4f}s @ "
            f"{SMALL_GAMES * MARKETS} tickers vs {large_elapsed:.4f}s @ "
            f"{LARGE_GAMES * MARKETS} tickers (ratio {ratio:.1f}x for a "
            f"{INPUT_RATIO:.0f}x input, ceiling {ceiling:.1f}x, best of {TIMING_REPEATS})"
        )
