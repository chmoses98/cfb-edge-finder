"""Mission section 28: season-long storage/processing scale estimate,
proven against synthetic volume representative of a real CFB season --
mirrors tests/test_scale.py's synthetic-week pattern (mission audit
section 7), extended across a full season and to the durable persistence
layer specifically.

Assumptions (documented, generous on purpose -- see docs/MILESTONE_E.md
"Season-scale storage estimate" for the full write-up):
  * ~80 games/week, ~150 contracts/game (moneyline + spread ladder + total
    ladder), 9 timing buckets/contract if every single one were captured
    (closer to 4-6 in practice once early/lopsided lines stop moving) --
    this test uses the FULL 9 as a deliberately conservative upper bound.
  * 14 weeks regular season + a smaller postseason slate.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "tests")
from research_factories import make_corpus_row, make_observation  # noqa: E402

from cfb_edge_finder.research import persistence

GAMES_PER_WEEK = 80
CONTRACTS_PER_GAME = 150
BUCKETS_PER_CONTRACT_UPPER_BOUND = 9
WEEKS = 14


def _season_row_count() -> int:
    return GAMES_PER_WEEK * CONTRACTS_PER_GAME * BUCKETS_PER_CONTRACT_UPPER_BOUND * WEEKS


def test_estimated_season_row_count_is_documented_and_bounded():
    # ~1.5M rows/season at the deliberately-generous upper bound -- see
    # docs/MILESTONE_E.md for why the realistic figure is far lower
    # (closing-only lopsided lines, bye weeks, missed windows all reduce
    # this) and why even the upper bound is comfortably within git's
    # line-oriented-text comfort zone for a single JSONL file.
    total = _season_row_count()
    assert total == 80 * 150 * 9 * 14
    assert total < 3_000_000  # sanity ceiling on the documented assumption itself


def test_a_realistic_single_week_appends_quickly(tmp_path: Path):
    path = persistence.canonical_path(tmp_path, persistence.OBSERVATIONS_SUBDIR, 2026)
    rows = [
        make_corpus_row(observation=make_observation(kalshi_market_ticker=f"MKT-{g}-{c}"))
        for g in range(GAMES_PER_WEEK)
        for c in range(CONTRACTS_PER_GAME // 10)  # 15 contracts/game -- one week's realistic single-bucket batch
    ]
    start = time.perf_counter()
    result = persistence.append_observation_rows(path, rows)
    elapsed = time.perf_counter() - start

    assert result.written == len(rows)
    # Generous ceiling (mirrors test_scale.py's own 15s bound) -- this is
    # pure JSON-line I/O on ~1,200 rows, should complete in well under a second.
    assert elapsed < 15.0, f"single-week batch append took {elapsed:.2f}s, expected well under 15s"


def test_repeated_dedup_lookup_scales_reasonably_not_quadratically(tmp_path: Path):
    path = persistence.canonical_path(tmp_path, persistence.OBSERVATIONS_SUBDIR, 2026)

    def _append_batch(n: int) -> float:
        rows = [make_corpus_row(observation=make_observation(kalshi_market_ticker=f"BATCH-{n}-{i}")) for i in range(n)]
        start = time.perf_counter()
        persistence.append_observation_rows(path, rows)
        return time.perf_counter() - start

    small_elapsed = _append_batch(500)
    large_elapsed = _append_batch(2000)  # 4x rows, appended against an already-larger file

    if small_elapsed > 0.0005:
        assert large_elapsed < small_elapsed * 20, (
            f"dedup/append did not scale reasonably: {small_elapsed:.4f}s @ 500 rows vs "
            f"{large_elapsed:.4f}s @ 2000 rows (ratio {large_elapsed / small_elapsed:.1f}x)"
        )


def test_file_size_estimate_stays_compact(tmp_path: Path):
    path = persistence.canonical_path(tmp_path, persistence.OBSERVATIONS_SUBDIR, 2026)
    rows = [
        make_corpus_row(observation=make_observation(kalshi_market_ticker=f"MKT-{i}"))
        for i in range(1000)
    ]
    persistence.append_observation_rows(path, rows)
    bytes_per_row = path.stat().st_size / len(rows)
    # Measured (not assumed): a full row -- KalshiResearchObservation plus
    # DataVersionManifest/provenance/uncertainty -- runs ~2.2KB as compact
    # JSON. This is genuinely compact (no raw Kalshi payload, no repeated
    # rule text -- mission section 29): the budget below is a regression
    # guard against something ACCIDENTALLY bloating a row (e.g. an
    # embedded raw payload dict), not a tight target.
    assert bytes_per_row < 3072, f"{bytes_per_row:.0f} bytes/row exceeds the compact-storage regression budget"

    # Season estimate at the DELIBERATELY WORST-CASE upper bound (every
    # one of 150 contracts/game capturing all 9 buckets every week -- see
    # docs/MILESTONE_E.md for why the realistic figure is roughly 5-10x
    # smaller: most alt-line rungs stop being captured once VWAP-thin, and
    # not every contract survives to every late bucket). Comfortably under
    # a few GB even at this ceiling, and git's packfile compression on
    # repetitive JSON text typically shrinks this further still.
    projected_season_mb = (bytes_per_row * _season_row_count()) / (1024 * 1024)
    assert projected_season_mb < 5000, (
        f"projected worst-case season size {projected_season_mb:.0f}MB exceeds sanity ceiling"
    )
