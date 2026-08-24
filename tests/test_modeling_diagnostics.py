from datetime import UTC, datetime

import pytest

from cfb_edge_finder.modeling.backtest import GameOutcome
from cfb_edge_finder.modeling.diagnostics import (
    classify_favorite,
    classify_margin_magnitude,
    full_diagnostic_report,
    is_conference_game,
    projected_margin_bin,
    projected_total_bin,
    source_of_margin_bias_summary,
)

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _outcome(
    *,
    home_id="ohio-state",
    away_id="michigan",
    season=2025,
    week=5,
    is_neutral_site=False,
    is_fbs_vs_fbs=True,
    actual_home_points=30,
    actual_away_points=20,
    model_margin_mean=7.0,
    model_total_mean=45.0,
) -> GameOutcome:
    return GameOutcome(
        source_game_id=f"{home_id}-{away_id}-{week}",
        season=season,
        week=week,
        home_id=home_id,
        away_id=away_id,
        is_neutral_site=is_neutral_site,
        is_fbs_vs_fbs=is_fbs_vs_fbs,
        actual_home_points=actual_home_points,
        actual_away_points=actual_away_points,
        naive_prob_home_win=0.6,
        naive_margin=5.0,
        naive_total=44.0,
        model_prob_home_win=0.65,
        calibrated_prob_home_win=0.62,
        model_margin_mean=model_margin_mean,
        model_total_mean=model_total_mean,
        model_margin_p05=model_margin_mean - 14,
        model_margin_p95=model_margin_mean + 14,
        model_total_p05=model_total_mean - 14,
        model_total_p95=model_total_mean + 14,
    )


def test_classify_favorite_uses_the_models_own_projection_not_the_market():
    assert classify_favorite(_outcome(model_margin_mean=7.0)) == "home_favorite"
    assert classify_favorite(_outcome(model_margin_mean=-7.0)) == "home_underdog"
    assert classify_favorite(_outcome(model_margin_mean=0.0)) == "pickem_exact"


def test_classify_margin_magnitude_buckets():
    assert classify_margin_magnitude(_outcome(model_margin_mean=20.0)) == "large_favorite"
    assert classify_margin_magnitude(_outcome(model_margin_mean=8.0)) == "moderate_favorite"
    assert classify_margin_magnitude(_outcome(model_margin_mean=1.0)) == "pickem_like"


def test_projected_margin_bin_and_total_bin_are_deterministic_labels():
    o = _outcome(model_margin_mean=10.0, model_total_mean=50.0)
    assert projected_margin_bin(o) == projected_margin_bin(o)
    assert projected_total_bin(o) == projected_total_bin(o)
    assert "7" in projected_margin_bin(o) or "14" in projected_margin_bin(o)


def test_is_conference_game_true_for_same_conference_fbs_teams():
    same_conf = _outcome(home_id="ohio-state", away_id="michigan")  # both Big Ten
    assert is_conference_game(same_conf) is True


def test_is_conference_game_false_for_different_conference_fbs_teams():
    diff_conf = _outcome(home_id="ohio-state", away_id="texas")  # Big Ten vs SEC
    assert is_conference_game(diff_conf) is False


def test_is_conference_game_none_when_opponent_unresolvable():
    fcs_game = _outcome(home_id="ohio-state", away_id="some_fcs_team_not_in_registry", is_fbs_vs_fbs=False)
    assert is_conference_game(fcs_game) is None


def test_full_diagnostic_report_returns_nonempty_segments_with_positive_n():
    outcomes = [
        _outcome(week=w, season=s, model_margin_mean=(-5 if w % 2 else 12))
        for s in (2024, 2025)
        for w in range(2, 6)
    ]
    reports = full_diagnostic_report(outcomes)
    assert reports
    for r in reports:
        assert r.n > 0
        assert r.metrics.n_games == r.n


def test_source_of_margin_bias_summary_has_all_expected_keys():
    outcomes = [_outcome(week=w, actual_home_points=30 + w, model_margin_mean=5.0) for w in range(2, 8)]
    summary = source_of_margin_bias_summary(outcomes)
    expected_keys = {
        "overall_bias",
        "fbs_vs_fbs_bias",
        "fbs_vs_fcs_bias",
        "home_favorite_bias",
        "home_underdog_bias",
        "large_favorite_bias",
        "pickem_like_bias",
        "early_season_bias",
        "later_season_bias",
    }
    assert expected_keys.issubset(summary.keys())
    assert summary["overall_bias"] is not None


def test_source_of_margin_bias_summary_none_for_empty_subset():
    # Every outcome is a home favorite -- the "home_underdog_bias" bucket
    # must be None (no crash on an empty subset), not a fabricated 0.0.
    outcomes = [_outcome(model_margin_mean=10.0)]
    summary = source_of_margin_bias_summary(outcomes)
    assert summary["home_underdog_bias"] is None


@pytest.mark.parametrize("bad_id", ["totally-fake-team-xyz"])
def test_is_conference_game_none_when_home_team_unresolvable(bad_id):
    o = _outcome(home_id=bad_id, away_id="michigan")
    assert is_conference_game(o) is None
