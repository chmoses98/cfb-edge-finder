"""Mission section 18: correlation-aware clustering."""

from __future__ import annotations

from cfb_edge_finder.research.correlation import cluster_counts, group_by_game_cluster


def test_one_game_many_spread_rungs_collapses_to_one_game_cluster():
    rows = [(f"MKT-{i}", "game-1", "spread") for i in range(10)]
    counts = cluster_counts(rows)
    assert counts.contract_level_n == 10
    assert counts.game_level_n == 1
    assert counts.effective_sample_size == 1.0


def test_effective_sample_size_never_exceeds_contract_level_n():
    rows = [(f"MKT-{i}", f"game-{i % 3}", "spread") for i in range(30)]
    counts = cluster_counts(rows)
    assert counts.effective_sample_size <= counts.contract_level_n


def test_same_game_different_family_are_separate_clusters():
    rows = [("MKT-1", "game-1", "spread"), ("MKT-2", "game-1", "total")]
    counts = cluster_counts(rows)
    assert counts.game_level_n == 2  # spread and total ladders don't collapse into one


def test_family_level_cluster_counts_distinct_families():
    rows = [("MKT-1", "game-1", "spread"), ("MKT-2", "game-2", "spread"), ("MKT-3", "game-1", "total")]
    counts = cluster_counts(rows)
    assert counts.family_level_n == 2


def test_group_by_game_cluster_groups_tickers():
    rows = [("MKT-1", "game-1", "spread"), ("MKT-2", "game-1", "spread"), ("MKT-3", "game-2", "spread")]
    grouped = group_by_game_cluster(rows)
    assert sorted(grouped["game-1::spread"]) == ["MKT-1", "MKT-2"]
    assert grouped["game-2::spread"] == ["MKT-3"]


def test_empty_rows_zero_counts():
    counts = cluster_counts([])
    assert counts.contract_level_n == 0
    assert counts.game_level_n == 0
    assert counts.effective_sample_size == 0.0


def test_unknown_family_still_bucketed_not_dropped():
    rows = [("MKT-1", "game-1", None)]
    counts = cluster_counts(rows)
    assert counts.contract_level_n == 1
    assert counts.game_level_n == 1
