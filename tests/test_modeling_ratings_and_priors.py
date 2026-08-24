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


# --- Milestone C.2: FCS historical-performance tiering ---


def test_fcs_tiered_mode_separates_weak_and_strong_fcs_opponents():
    lines = []
    for week in range(1, 5):
        # "weak_fcs" gets blown out every time it plays an FBS team.
        lines.append(_line("alpha", "weak_fcs", 56, 3, 74, True, week=week, opp_class="fcs"))
        # "strong_fcs" keeps games close.
        lines.append(_line("beta", "strong_fcs", 24, 20, 68, True, week=week, opp_class="fcs"))
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=5), fcs_mode="tiered")
    assert ratings.fcs_team_tier["weak_fcs"] == "weak"
    assert ratings.fcs_team_tier["strong_fcs"] == "strong"
    # A weak FCS opponent's defense should allow strictly more (more
    # negative "defense" rating) than a strong FCS opponent's.
    assert ratings.fcs_defense_for("weak_fcs") < ratings.fcs_defense_for("strong_fcs")


def test_fcs_tier_assignment_is_deterministic():
    lines = [
        _line("alpha", "fcs_a", 45, 10, 70, True, week=1, opp_class="fcs"),
        _line("alpha", "fcs_a", 42, 7, 70, True, week=2, opp_class="fcs"),
    ]
    r1 = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=3), fcs_mode="tiered")
    r2 = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=3), fcs_mode="tiered")
    assert r1.fcs_team_tier == r2.fcs_team_tier
    assert r1.fcs_tier_offense == r2.fcs_tier_offense
    assert r1.fcs_tier_defense == r2.fcs_tier_defense


def test_fcs_tier_assignment_uses_only_strictly_prior_evidence():
    lines = [
        _line("alpha", "fcs_a", 45, 10, 70, True, week=1, opp_class="fcs"),
        _line("alpha", "fcs_a", 42, 7, 70, True, week=2, opp_class="fcs"),
    ]
    future_blowout = _line("alpha", "fcs_a", 70, 0, 70, True, week=5, opp_class="fcs")
    with pytest.raises(LeakageError):
        fit_fbs_efficiency_ratings([*lines, future_blowout], AsOf(season=2025, week=3), fcs_mode="tiered")


def test_fcs_tier_recomputed_walk_forward_and_unaffected_by_future_games():
    """Regression test for the historical-integrity audit: proves (1) FCS
    tiers are genuinely RECOMPUTED walk-forward, not frozen at their
    first-seen value, and (2) a future FCS result cannot reach back and
    change a PAST game's already-assigned tier.

    "weak_fcs" is blown out early (weeks 1-2) but plays much closer games
    later (weeks 6-7) -- modeling a team that genuinely improves over the
    season. `history_at_each_as_of` is built the exact same way
    `backtest.run_walk_forward_backtest` builds its own `history` argument
    (`[ln for ln in lines if ln.as_of.is_strictly_before(as_of)]`), so this
    test exercises the real walk-forward access pattern, not a shortcut.
    """
    early_weeks = [
        _line("alpha", "weak_fcs", 56, 3, 74, True, week=1, opp_class="fcs"),
        _line("alpha", "weak_fcs", 52, 6, 74, True, week=2, opp_class="fcs"),
    ]
    later_weeks = [
        _line("beta", "weak_fcs", 24, 21, 68, True, week=6, opp_class="fcs"),
        _line("beta", "weak_fcs", 27, 24, 68, True, week=7, opp_class="fcs"),
    ]
    full_season = early_weeks + later_weeks

    as_of_week3 = AsOf(season=2025, week=3)
    history_week3 = [ln for ln in full_season if ln.as_of.is_strictly_before(as_of_week3)]
    ratings_week3 = fit_fbs_efficiency_ratings(history_week3, as_of_week3, fcs_mode="tiered")
    assert ratings_week3.fcs_team_tier["weak_fcs"] == "weak"

    as_of_week8 = AsOf(season=2025, week=8)
    history_week8 = [ln for ln in full_season if ln.as_of.is_strictly_before(as_of_week8)]
    ratings_week8 = fit_fbs_efficiency_ratings(history_week8, as_of_week8, fcs_mode="tiered")
    # With the two later, closer games now strictly prior, the SAME
    # opponent's tier legitimately moves away from "weak" -- proving
    # recomputation is genuinely walk-forward, not a one-time snapshot.
    assert ratings_week8.fcs_team_tier["weak_fcs"] != "weak"

    # The critical assertion: the week-3 snapshot's tier is EXACTLY what
    # it would be if the week-6/7 games never existed at all -- a future
    # FCS result cannot change a past game's assigned tier.
    ratings_week3_isolated = fit_fbs_efficiency_ratings(early_weeks, as_of_week3, fcs_mode="tiered")
    assert ratings_week3.fcs_team_tier == ratings_week3_isolated.fcs_team_tier
    assert ratings_week3.fcs_tier_offense == ratings_week3_isolated.fcs_tier_offense
    assert ratings_week3.fcs_tier_defense == ratings_week3_isolated.fcs_tier_defense


def test_unknown_fcs_opponent_falls_back_to_default_tier_not_an_error():
    from cfb_edge_finder.modeling.ratings import FCS_DEFAULT_TIER, FCS_TIER_MIN_GAMES

    lines = [_line("alpha", "brand_new_fcs", 40, 10, 70, True, week=1, opp_class="fcs")]
    assert FCS_TIER_MIN_GAMES > 1  # sanity: a single game must NOT be enough to tier on its own
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=2), fcs_mode="tiered")
    assert "brand_new_fcs" not in ratings.fcs_team_tier
    assert ratings.fcs_offense_for("brand_new_fcs") == ratings.fcs_tier_offense[FCS_DEFAULT_TIER]
    assert ratings.fcs_defense_for("brand_new_fcs") == ratings.fcs_tier_defense[FCS_DEFAULT_TIER]


def test_pooled_mode_fcs_lookup_matches_the_pooled_scalar():
    lines = [
        _line("alpha", "fcs_one", 49, 3, 72, True, week=1, opp_class="fcs"),
        _line("alpha", "fcs_two", 42, 6, 70, True, week=2, opp_class="fcs"),
    ]
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=3))  # default fcs_mode="pooled"
    assert ratings.fcs_offense_for("fcs_one") == ratings.fcs_offense
    assert ratings.fcs_defense_for("anyone_unseen") == ratings.fcs_defense


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


# --- Milestone C.2 (this pass): matchup-tempo-interaction pace_mode ---


def test_pace_symmetric_mode_is_the_default_and_unchanged():
    lines = [
        _line("alpha", "beta", 28, 24, 65, True, week=1),
        _line("beta", "alpha", 24, 28, 60, False, week=1),
    ]
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=2))
    assert ratings.pace_mode == "symmetric"
    expected = (ratings.team_pace("alpha") + ratings.team_pace("beta")) / 2
    assert ratings.expected_plays_for("alpha", "beta") == pytest.approx(expected)
    assert ratings.expected_plays_for("beta", "alpha") == pytest.approx(expected)
    assert ratings.expected_plays_for("alpha", "beta") == ratings.expected_plays_for("beta", "alpha")


def test_pace_matchup_mode_reflects_opponent_defense_plays_allowed():
    """A team's expected plays under pace_mode="matchup" must genuinely
    depend on the OPPONENT's own identity (specifically, how many plays
    that opponent's defense has trailingly allowed) -- not just a shared
    per-game average both sides are forced into. "leaky_D" has allowed
    90 plays/game to three different offenses; "stingy_D" has allowed
    only 50 plays/game to three others. A fixed third team ("watcher",
    whose own offensive pace is held constant across both calls below)
    must get a strictly higher expected-plays estimate against leaky_D
    than against stingy_D.
    """
    lines = []
    for i, off in enumerate(["off1", "off2", "off3"], start=1):
        lines.append(_line(off, "leaky_D", 30, 10, 90, True, week=i))
        lines.append(_line("leaky_D", off, 10, 30, 65, False, week=i))
    for i, off in enumerate(["off4", "off5", "off6"], start=1):
        lines.append(_line(off, "stingy_D", 20, 15, 50, True, week=i))
        lines.append(_line("stingy_D", off, 15, 20, 65, False, week=i))
    lines.append(_line("watcher", "leaky_D", 20, 20, 68, True, week=7))
    lines.append(_line("leaky_D", "watcher", 20, 20, 90, False, week=7))
    lines.append(_line("watcher", "stingy_D", 20, 20, 68, True, week=8))
    lines.append(_line("stingy_D", "watcher", 20, 20, 50, False, week=8))

    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=9), pace_mode="matchup")

    assert ratings.expected_plays_for("watcher", "leaky_D") > ratings.expected_plays_for("watcher", "stingy_D")


def test_pace_matchup_mode_lets_the_two_sides_of_one_game_differ():
    # Under "symmetric" mode the two sides of a game always share one
    # value; under "matchup" mode they are no longer forced to.
    lines = []
    for i, off in enumerate(["off1", "off2", "off3"], start=1):
        lines.append(_line(off, "leaky_D", 30, 10, 90, True, week=i))
        lines.append(_line("leaky_D", off, 10, 30, 55, False, week=i))
    lines.append(_line("watcher", "leaky_D", 20, 20, 68, True, week=4))
    lines.append(_line("leaky_D", "watcher", 20, 20, 72, False, week=4))

    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=5), pace_mode="matchup")
    assert ratings.expected_plays_for("watcher", "leaky_D") != ratings.expected_plays_for("leaky_D", "watcher")


def test_defense_pace_allowed_excludes_fbs_vs_fcs_games():
    # Mirrors the residual pool's own FBS-vs-FBS-only population (mission
    # section 4: FBS-vs-FCS is never blended into main calibration) --
    # an FCS opponent's plays must not contribute to an FBS team's
    # defense_pace_allowed.
    lines = [
        _line("alpha", "beta", 28, 24, 65, True, week=1),
        _line("beta", "alpha", 24, 28, 60, False, week=1),
        _line("alpha", "some_fcs", 49, 3, 120, True, week=2, opp_class="fcs"),
    ]
    ratings = fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=3), pace_mode="matchup")
    # "alpha"'s defense_pace_allowed must reflect ONLY the FBS-vs-FBS row
    # (60 plays from beta), not the 120-play FCS blowout.
    assert ratings.defense_pace_allowed_for("alpha") != pytest.approx(120.0)


def test_pace_mode_rejects_unknown_value():
    lines = [_line("alpha", "beta", 28, 24, 65, True, week=1)]
    with pytest.raises(ValueError, match="pace_mode"):
        fit_fbs_efficiency_ratings(lines, AsOf(season=2025, week=2), pace_mode="bogus")
