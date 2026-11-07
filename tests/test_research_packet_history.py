"""Empirical reference classes and period views (research_packet.history)."""

from __future__ import annotations

import pytest

from cfb_edge_finder.research_packet import history as h


def game(i, pm, pt, home, away, hp=None, ap=None):
    return {
        "game_id": str(i), "season": 2025, "week": 5, "home": f"H{i}", "away": f"A{i}",
        "pred_home": (pt + pm) / 2, "pred_away": (pt - pm) / 2, "pred_total": pt,
        "actual_home": home, "actual_away": away,
        "home_periods": hp, "away_periods": ap,
    }


def test_quantiles_and_exceedance_are_empirical_and_exact():
    q = h.quantile_table([1, 2, 3, 4, 5])
    assert q["n"] == 5 and q["p50"] == 3.0 and q["mean"] == 3.0
    assert h.exceedance([1, 2, 3, 4], [2.5]) == {"2.5": 0.5}
    assert h.quantile_table([]) is None and h.exceedance([], [1.5]) == {}


def test_period_view_second_half_excludes_overtime():
    g = game(1, 3, 50, 30, 27, [7, 10, 6, 4, 3], [7, 3, 10, 4, 3])
    v = h.period_view(g)
    assert v["ot"] is True and v["h1"] == (17, 10) and v["h2"] == (10, 14) and v["q1"] == (7, 7)


def test_period_view_is_none_without_captured_linescores():
    assert h.period_view(game(1, 3, 50, 30, 27)) is None
    assert h.period_view(game(1, 3, 50, 30, 27, [7, 7, 7], [3, 3, 3])) is None


def test_frequency_orientation_for_the_away_team():
    chosen = [game(i, 7, 50, 30, 20 if i % 2 else 33) for i in range(10)]  # home margin 10 (5x) or -3 (5x)
    away_by_over_2 = h.frequency_for(chosen, {"type": "margin_over", "side": "away", "threshold": 2.5, "period": "game"})
    home_by_over_9 = h.frequency_for(chosen, {"type": "margin_over", "side": "home", "threshold": 9.5, "period": "game"})
    assert away_by_over_2["yes_frequency"] == 0.5 and home_by_over_9["yes_frequency"] == 0.5
    assert h.frequency_for(chosen, {"type": "win", "side": "away", "period": "game"})["yes_frequency"] == 0.5
    tie = h.frequency_for(chosen, {"type": "win", "outcome": "tie", "period": "h1"})
    assert tie is None  # no linescores in these rows


def test_total_and_team_total_frequency():
    chosen = [game(i, 3, 50, 28, 24, [7, 7, 7, 7], [7, 7, 5, 5]) for i in range(5)]
    assert h.frequency_for(chosen, {"type": "total_over", "threshold": 51.5, "period": "game"})["yes_frequency"] == 1.0
    assert h.frequency_for(chosen, {"type": "total_over", "threshold": 52.5, "period": "game"})["yes_frequency"] == 0.0
    assert h.frequency_for(chosen, {"type": "team_points_over", "side": "home", "threshold": 13.5, "period": "h1"})["yes_frequency"] == 1.0
    assert h.frequency_for(chosen, {"type": "total_over", "threshold": 23.5, "period": "h1"})["yes_frequency"] == 1.0  # 14+14=28


def test_comparables_widen_until_enough_games_and_report_thin_classes():
    games = [game(i, 7 + (i % 5) * 0.2, 50 + (i % 4) * 0.5, 31, 21, [7, 7, 7, 10], [7, 7, 0, 7]) for i in range(80)]
    summary, chosen = h.comparables(games, 7.0, 50.0)
    assert summary["status"] == "CONDITIONAL" and len(chosen) == 80
    assert summary["first_half"]["games_with_linescores"] == 80
    assert summary["closest_games"] and "caveat" in summary
    thin, few = h.comparables(games[:10], 7.0, 50.0)
    assert thin["status"] == "THIN_REFERENCE_CLASS" and len(few) == 10


def test_no_baseline_means_unavailable_not_a_default():
    summary, chosen = h.comparables([game(1, 3, 50, 30, 27)], None, None)
    assert summary["status"] == "UNAVAILABLE" and chosen == []


def test_baseline_accuracy_reports_bias_and_naive_comparison():
    games = [game(i, 3, 50, 30, 20 + i) for i in range(20)]
    acc = h.baseline_accuracy(games)
    assert acc["n"] == 20 and "naive_league_total_mae" in acc["total_points"] and acc["reading"]


def test_committed_reference_has_linescores_for_every_game():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "data/research_packets/reference/history_games.jsonl.gz"
    games = h.load_games(path)
    assert len(games) > 1500
    assert all(g["pred_total"] is not None for g in games)
    with_p = sum(1 for g in games if h.period_view(g))
    assert with_p / len(games) > 0.95
    for g in games[:200]:
        v = h.period_view(g)
        if v and not v["ot"]:
            assert v["h1"][0] + v["h2"][0] == g["actual_home"]
