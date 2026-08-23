from datetime import UTC, datetime

import pytest

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf, LeakageError
from cfb_edge_finder.modeling.priors import blend_team_rating, season_carryover_weight
from cfb_edge_finder.modeling.ratings import DEFAULT_RIDGE_LAMBDA, FCS_PSEUDO_TEAM_ID, fit_fbs_efficiency_ratings

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _line(team, opp, pts, opp_pts, plays, home, neutral=False, week=1, season=2025, team_class="fbs", opp_class="fbs"):
    return TeamGameLine(
        source_game_id=f"{'-'.join(sorted([team, opp]))}-{week}",
        season=season,
        week=week,
        is_postseason=False,
        team_id=team,
        opponent_id=opp,
        team_classification=team_class,
        opponent_classification=opp_class,
        is_home=home,
        is_neutral_site=neutral,
        team_points=pts,
        opponent_points=opp_pts,
        team_plays=plays,
        captured_at=NOW,
    )


def test_fit_raises_on_future_row_relative_to_as_of():
    future_row = _line("alpha", "beta", 30, 10, 65, True, week=10)
    with pytest.raises(LeakageError):
        fit_fbs_efficiency_ratings([future_row], AsOf(season=2025, week=5))


def test_stronger_team_gets_higher_offense_rating():
    lines = [
        _line("alpha", "beta", 42, 7, 68, True, week=1),
        _line("beta", "alpha", 7, 42, 65, False, week=1),
        _line("alpha", "gamma", 35, 14, 70, False, week=2),
        _line("gamma", "alpha", 14, 35, 66, True, week=2),
        _line("beta", "gamma", 17, 20, 64, True, week=2),
        _line("gamma", "beta", 20, 17, 63, False, week=2),
    ]
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=3))
    assert ratings.offense_rating("alpha") > ratings.offense_rating("beta")


def test_home_field_advantage_is_positive_when_home_teams_outscore_on_average():
    lines = []
    for week in range(1, 6):
        lines.append(_line("home_favored", "visitor", 30, 17, 65, True, week=week))
        lines.append(_line("visitor", "home_favored", 17, 30, 65, False, week=week))
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=6))
    assert ratings.hfa > 0


def test_neutral_site_games_excluded_from_home_indicator():
    # Every game is neutral-site -- HFA should fit near zero, not be
    # inferred from the home/away scoring gap that would exist if the
    # neutral flag were ignored.
    lines = []
    for week in range(1, 8):
        lines.append(_line("teamA", "teamB", 30, 17, 65, True, neutral=True, week=week))
        lines.append(_line("teamB", "teamA", 17, 30, 65, False, neutral=True, week=week))
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=8))
    assert abs(ratings.hfa) < 0.05


def test_fcs_opponents_share_one_pooled_pseudo_rating_not_individual_ones():
    lines = [
        _line("alpha", "fcs_one", 49, 3, 72, True, opp_class="fcs"),
        _line("alpha", "fcs_two", 42, 6, 70, True, week=2, opp_class="fcs"),
    ]
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=3))
    assert "fcs_one" not in ratings.offense
    assert "fcs_two" not in ratings.offense
    assert FCS_PSEUDO_TEAM_ID not in ratings.offense  # internal key, not exposed as a "team"


def test_fcs_pooled_rating_moves_further_from_zero_with_lighter_shrinkage():
    # Stabilizing FBS-vs-FBS games first (mixed home/away, so mu/hfa/
    # offense/defense are well-identified rather than degenerate/collinear
    # -- an all-home-only dataset would make mu and hfa literally the same
    # design-matrix column). Then plenty of FBS-vs-FCS blowouts (home side
    # only, as these are typically scheduled) on top.
    lines = []
    for week in range(1, 5):
        lines.append(_line("alpha", "beta", 28, 24, 65, True, week=week))
        lines.append(_line("beta", "alpha", 24, 28, 65, False, week=week))
    for week in range(5, 13):
        lines.append(_line("alpha", f"fcs_{week}", 49, 6, 72, True, week=week, opp_class="fcs"))

    as_of = AsOf(season=2025, week=13)
    ratings_default = fit_fbs_efficiency_ratings(lines, as_of)
    # Compare against what the OLD (pre-hardening) behavior would have
    # produced: the pooled FCS columns shrunk at the same strength as an
    # individual, thinly-evidenced FBS team's rating.
    ratings_heavy_shrinkage = fit_fbs_efficiency_ratings(lines, as_of, fcs_ridge_lambda=DEFAULT_RIDGE_LAMBDA)

    assert ratings_default.fcs_defense < -0.05
    # Lighter, evidence-proportional shrinkage should pull the pooled FCS
    # defense parameter further from 0.0 (more negative -- FCS "defense"
    # allows a lot) than the old individual-team-strength shrinkage did.
    assert ratings_default.fcs_defense < ratings_heavy_shrinkage.fcs_defense


def test_fcs_rating_fit_is_deterministic_and_leakage_safe():
    lines = [
        _line("alpha", "fcs_one", 49, 3, 72, True, week=1, opp_class="fcs"),
        _line("alpha", "fcs_two", 42, 6, 70, True, week=2, opp_class="fcs"),
    ]
    r1 = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=3))
    r2 = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=3))
    assert r1.fcs_offense == r2.fcs_offense
    assert r1.fcs_defense == r2.fcs_defense
    # A week-3 FCS rating must not change if a future (week 4) FCS game is
    # appended -- it is never consulted (assert_strictly_before already
    # enforces this at the row level; this checks the FCS columns too).
    future_game = _line("alpha", "fcs_three", 56, 0, 74, True, week=4, opp_class="fcs")
    r3 = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=3))
    assert r3.fcs_offense == r1.fcs_offense
    with pytest.raises(LeakageError):
        fit_fbs_efficiency_ratings([*lines, future_game], AsOf(season=2025, week=3))


def test_fcs_ridge_lambda_is_separate_from_and_smaller_than_the_team_ridge_lambda():
    from cfb_edge_finder.modeling.ratings import DEFAULT_FCS_RIDGE_LAMBDA

    assert DEFAULT_FCS_RIDGE_LAMBDA < DEFAULT_RIDGE_LAMBDA


def test_empty_history_returns_neutral_snapshot_not_a_crash():
    ratings = fit_fbs_efficiency_ratings([], AsOf(season=2025, week=1))
    assert ratings.n_training_rows == 0
    assert ratings.offense_rating("anyone") == 0.0


def test_pace_shrinks_toward_league_average_for_thin_schedules():
    lines = [
        _line("fastteam", "opp", 30, 20, 90, True, week=1),
        _line("opp", "fastteam", 20, 30, 65, False, week=1),
    ]
    for week in range(1, 10):
        lines.append(_line("otherteam", "someopp", 20, 20, 65, True, week=week))
        lines.append(_line("someopp", "otherteam", 20, 20, 65, False, week=week))
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=10))
    # fastteam had ONE game at 90 plays -- shrunk pace should sit well below 90.
    assert ratings.team_pace("fastteam") < 85


def test_season_carryover_weight_increases_with_games_played():
    w0 = season_carryover_weight(0)
    w4 = season_carryover_weight(4)
    w12 = season_carryover_weight(12)
    assert w0 == 0.0
    assert w0 < w4 < w12 < 1.0


def test_season_carryover_weight_rejects_negative_games():
    with pytest.raises(ValueError, match="must be >= 0"):
        season_carryover_weight(-1)


def test_blend_with_no_prior_season_data_uses_league_average_zero():
    blended = blend_team_rating(
        current_offense=0.05,
        current_defense=-0.02,
        prior_season_offense=None,
        prior_season_defense=None,
        games_played_this_season=0,
    )
    assert blended.offense == 0.0
    assert blended.defense == 0.0
    assert blended.weight_on_current_season == 0.0


def test_blend_with_prior_season_data_is_pure_prior_at_zero_games():
    blended = blend_team_rating(
        current_offense=0.10,
        current_defense=-0.05,
        prior_season_offense=0.03,
        prior_season_defense=0.01,
        games_played_this_season=0,
    )
    assert blended.offense == pytest.approx(0.03)
    assert blended.defense == pytest.approx(0.01)


def test_blend_moves_toward_current_season_as_games_accumulate():
    def blend_at(n):
        return blend_team_rating(
            current_offense=1.0,
            current_defense=0.0,
            prior_season_offense=0.0,
            prior_season_defense=0.0,
            games_played_this_season=n,
        ).offense

    assert blend_at(0) < blend_at(2) < blend_at(8) < 1.0
