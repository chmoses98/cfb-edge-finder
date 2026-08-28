"""Scale regression tests for the corpus-wide commands.

The scanner performance problem this repository already fixed came from
re-reading the ledger per market. These tests exist so a future change
cannot quietly reintroduce that shape: they assert LINEAR-ish growth on
synthetic data at season scale, not an absolute runtime, so they do not
become flaky on a slow runner.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from cfb_edge_finder.decision.portfolio import build_portfolio_view
from cfb_edge_finder.expression.corpus import load_contract_snapshots
from cfb_edge_finder.modeling.live_diagnostics import run_model_health
from cfb_edge_finder.research.checkpoint_manifest import (
    ManifestCompletenessReport,
    manifest_from_corpus_row,
)

QUADRATIC_TOLERANCE = 8.0
"""A 4x input must not cost more than 8x. Linear is 4x and quadratic is
16x, so this catches the shape while leaving generous room for constant
factors, GC and a noisy CI runner."""


def synthetic_rows(n_games: int, contracts_per_game: int) -> list[dict]:
    return [
        {
            "observation_key": f"k{g}-{c}",
            "schema_version": "research_corpus_v2",
            "capture_mode": "PROSPECTIVE",
            "run_id": "r",
            "season": 2026,
            "kickoff_utc_at_capture": "2026-09-05T16:00:00+00:00",
            "observation": {
                "game_id": f"g{g}",
                "captured_at": "2026-09-04T16:00:00+00:00",
                "snapshot_timing": {"label": "T_24H", "hours_before_kickoff": 24.0},
                "kalshi_market_ticker": f"T-{g}-{c}",
                "family": "spread",
                "team": "home",
                "threshold": float(c),
                "semantic_operator": ">",
                "parse_status": "confirmed_live",
                # Non-increasing in threshold, matching real ladders.
                "model_probability": max(0.01, 0.99 - c * 0.01),
                "executable_yes_price": 0.5,
                "executable_no_price": 0.52,
                "market_status": "active",
                "fee_status": "VERIFIED_CURRENT",
                "fee_schedule_version": "kalshi_fee_schedule_2026_07_07_taker",
                "model_version": {"model_version": "m1"},
                "pricing_status": "model_priced",
                "snapshot_id": "s1",
            },
        }
        for g in range(n_games)
        for c in range(contracts_per_game)
    ]


def write_rows(rows: list[dict]) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for row in rows:
        handle.write(json.dumps(row) + "\n")
    handle.close()
    return Path(handle.name)


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


@pytest.fixture(scope="module")
def scaled():
    """Two corpora, the larger 4x the smaller."""
    small_path = write_rows(synthetic_rows(150, 20))
    large_path = write_rows(synthetic_rows(600, 20))
    yield small_path, large_path
    small_path.unlink()
    large_path.unlink()


def test_corpus_load_is_not_quadratic(scaled):
    small, large = scaled
    _, t_small = timed(lambda: load_contract_snapshots(small))
    result, t_large = timed(lambda: load_contract_snapshots(large))
    assert result.rows_read == 12_000
    assert result.ledger_load_count == 1, "the ledger must be read exactly once"
    assert t_large <= t_small * QUADRATIC_TOLERANCE


def test_portfolio_grouping_is_not_quadratic(scaled):
    small, large = scaled
    a = [s.semantics for s in load_contract_snapshots(small).snapshots]
    b = [s.semantics for s in load_contract_snapshots(large).snapshots]
    _, t_small = timed(lambda: build_portfolio_view(a))
    view, t_large = timed(lambda: build_portfolio_view(b))
    assert view.contract_count == 12_000
    assert t_large <= t_small * QUADRATIC_TOLERANCE


def test_model_health_is_not_quadratic(scaled):
    small, large = scaled
    a = load_contract_snapshots(small).snapshots
    b = load_contract_snapshots(large).snapshots
    _, t_small = timed(lambda: run_model_health(a))
    report, t_large = timed(lambda: run_model_health(b))
    assert report.contracts_checked == 12_000
    assert t_large <= t_small * QUADRATIC_TOLERANCE


def test_manifest_building_is_not_quadratic():
    small = synthetic_rows(150, 20)
    large = synthetic_rows(600, 20)
    _, t_small = timed(lambda: ManifestCompletenessReport([manifest_from_corpus_row(r) for r in small]))
    report, t_large = timed(
        lambda: ManifestCompletenessReport([manifest_from_corpus_row(r) for r in large])
    )
    assert report.complete_count == 12_000
    assert t_large <= t_small * QUADRATIC_TOLERANCE


def test_ladder_checking_handles_a_deep_ladder():
    """One game with a very deep ladder is the worst case for the
    monotonicity check, which sorts rather than compares all pairs."""
    path = write_rows(synthetic_rows(1, 400))
    try:
        snapshots = load_contract_snapshots(path).snapshots
        _, elapsed = timed(lambda: run_model_health(snapshots))
        assert elapsed < 5.0
    finally:
        path.unlink()


def test_direction_conflicts_is_not_used_on_a_corpus_scale_path():
    """`portfolio.direction_conflicts` compares pairs and is O(n^2) by
    nature. That is fine for one game's contracts and unacceptable across
    a corpus, so it must not appear in any whole-ledger command."""
    import ast

    repo_root = Path(__file__).resolve().parents[1]
    for script in ("run_cfb.py", "week1_ops_health.py", "build_research_decision_report.py"):
        tree = ast.parse((repo_root / "scripts" / script).read_text())
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "direction_conflicts" not in called, script
