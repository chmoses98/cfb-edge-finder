from datetime import UTC, datetime

import pytest

from cfb_edge_finder.modeling.backtest import GameOutcome
from cfb_edge_finder.modeling.diagnostics import (
    absolute_projected_margin_bin,
    actual_total_bin,
    classify_favorite,
    classify_margin_magnitude,
    favorite_direction_margin_error,
    favorite_tail_margin_diagnosis,
    full_diagnostic_report,
    is_conference_game,
    projected_margin_bin,
    projected_total_bin,
    source_of_margin_bias_summary,
    source_of_total_bias_summary,
)
from cfb_edge_finder.teams.registry import get_team

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
    home_conference=None,
    away_conference=None,
    is_conference_game_flag=None,
    model_expected_plays=70.0,
    home_offense_rating=0.0,
    away_offense_rating=0.0,
    home_defense_rating=0.0,
    away_defense_rating=0.0,
) -> GameOutcome:
    return GameOutcome(
        source_game_id=f"{home_id}-{away_id}-{week}",
        season=season,
        week=week,
        home_id=home_id,
        away_id=away_id,
        home_conference=home_conference,
        away_conference=away_conference,
        is_conference_game=is_conference_game_flag,
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
        model_expected_plays=model_expected_plays,
        home_offense_rating=home_offense_rating,
        away_offense_rating=away_offense_rating,
        home_defense_rating=home_defense_rating,
        away_defense_rating=away_defense_rating,
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


def test_is_conference_game_true_via_historical_conference_strings():
    same_conf = _outcome(home_conference="Big Ten", away_conference="Big Ten")
    assert is_conference_game(same_conf) is True


def test_is_conference_game_false_via_historical_conference_strings():
    diff_conf = _outcome(home_conference="Big Ten", away_conference="SEC")
    assert is_conference_game(diff_conf) is False


def test_is_conference_game_prefers_cfbd_flag_over_conference_strings():
    # CFBD's own conferenceGame flag is the authoritative historical
    # source (see is_conference_game's docstring) -- it must win even when
    # the two conference-name strings alone would suggest the opposite
    # (e.g. a genuine cross-conference "championship" edge case CFBD
    # itself classifies differently than a naive string comparison would).
    flag_says_conference = _outcome(
        home_conference="Big Ten", away_conference="SEC", is_conference_game_flag=True
    )
    assert is_conference_game(flag_says_conference) is True

    flag_says_not = _outcome(
        home_conference="Big Ten", away_conference="Big Ten", is_conference_game_flag=False
    )
    assert is_conference_game(flag_says_not) is False


def test_is_conference_game_none_when_no_historical_source_available():
    # No CFBD conferenceGame flag AND no historical conference strings
    # (e.g. an FCS opponent, or a row CFBD didn't report the field for) --
    # must be None, not silently guessed.
    fcs_game = _outcome(home_conference=None, away_conference=None, is_conference_game_flag=None)
    assert is_conference_game(fcs_game) is None


def test_diagnostics_conference_realignment_safety():
    """Regression test: historical conference classification must NOT be
    rewritten by a team's CURRENT (2026) registry conference.

    Texas State is a real, in-repo-documented realignment case
    (teams/registry.py): Sun Belt through the 2024 season, Pac-12 as of
    the 2026 registry. A 2023 Texas State vs. Troy game (Troy has always
    been Sun Belt) was a genuine CONFERENCE game at the time it was
    played. If diagnostics classified conference games from the CURRENT
    registry (as the pre-audit implementation did), this game would be
    misclassified as non-conference, because the registry now disagrees
    with Troy's conference. Using the historical CFBD-reported conference
    fields (as this implementation does) gets it right regardless of what
    the registry says today.
    """
    current_texas_state_conference = get_team("texas-state").conference
    current_troy_conference = get_team("troy").conference
    assert current_texas_state_conference != current_troy_conference, (
        "test setup assumes Texas State's CURRENT registry conference differs from Troy's "
        "(the realignment this test is guarding against) -- if the registry changes, update this test"
    )

    historical_2023_game = _outcome(
        home_id="texas-state",
        away_id="troy",
        season=2023,
        home_conference="Sun Belt",
        away_conference="Sun Belt",
    )
    assert is_conference_game(historical_2023_game) is True

    # The CFBD conferenceGame flag path must be equally realignment-safe.
    historical_2023_game_via_flag = _outcome(
        home_id="texas-state",
        away_id="troy",
        season=2023,
        is_conference_game_flag=True,
    )
    assert is_conference_game(historical_2023_game_via_flag) is True


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
def test_is_conference_game_none_for_unresolvable_team_with_no_historical_conference(bad_id):
    o = _outcome(home_id=bad_id, away_id="michigan", home_conference=None, away_conference=None)
    assert is_conference_game(o) is None


# --- Milestone C.2 (this pass): totals-diagnosis segmentation ---


def test_actual_total_bin_is_deterministic_and_distinct_from_projected():
    o = _outcome(actual_home_points=35, actual_away_points=28, model_total_mean=50.0)
    assert actual_total_bin(o) == actual_total_bin(o)
    # actual total (63) falls in a higher bin than the projected total (50).
    assert actual_total_bin(o) != projected_total_bin(o)


def test_full_diagnostic_report_includes_tempo_and_offense_defense_segments():
    outcomes = [
        _outcome(week=w, model_expected_plays=plays, home_offense_rating=off, away_offense_rating=0.0)
        for w, (plays, off) in enumerate(
            [(60.0, -0.05), (65.0, -0.02), (70.0, 0.0), (75.0, 0.02), (80.0, 0.05)], start=2
        )
    ]
    reports = full_diagnostic_report(outcomes)
    labels = {r.label for r in reports}
    assert any("high tempo" in label for label in labels)
    assert any("low tempo" in label for label in labels)
    assert any("combined offense" in label for label in labels)
    assert any("combined defense" in label for label in labels)


def test_source_of_total_bias_summary_has_all_expected_keys():
    outcomes = [
        _outcome(week=w, actual_home_points=30, actual_away_points=20 + w, model_total_mean=48.0)
        for w in range(2, 8)
    ]
    summary = source_of_total_bias_summary(outcomes)
    expected_keys = {
        "overall_bias",
        "fbs_vs_fbs_bias",
        "fbs_vs_fcs_bias",
        "conference_game_bias",
        "non_conference_game_bias",
        "neutral_site_bias",
        "early_season_bias",
        "later_season_bias",
        "large_projected_margin_bias",
        "close_projected_margin_bias",
        "high_tempo_bias",
        "low_tempo_bias",
        "strong_combined_offense_bias",
        "weak_combined_offense_bias",
        "strong_combined_defense_bias",
        "weak_combined_defense_bias",
    }
    assert expected_keys.issubset(summary.keys())
    assert summary["overall_bias"] is not None


def test_source_of_total_bias_summary_none_for_empty_subset():
    # Every outcome is FBS-vs-FBS -- the "fbs_vs_fcs_bias" bucket must be
    # None (no crash on an empty subset), not a fabricated 0.0.
    outcomes = [_outcome(is_fbs_vs_fbs=True)]
    summary = source_of_total_bias_summary(outcomes)
    assert summary["fbs_vs_fcs_bias"] is None


# --- Milestone C.2 Part 3 (this pass): favorite-tail margin-bias diagnosis ---


def test_absolute_projected_margin_bin_is_symmetric_in_favorite_direction():
    # A home favorite by 10 and an away favorite by 10 must land in the
    # SAME |margin| bin -- the whole point of using abs(), per mission
    # section 2's request for symmetric bins.
    assert absolute_projected_margin_bin(_outcome(model_margin_mean=10.0)) == absolute_projected_margin_bin(
        _outcome(model_margin_mean=-10.0)
    )
    assert absolute_projected_margin_bin(_outcome(model_margin_mean=10.0)) == "[7,14)"
    assert absolute_projected_margin_bin(_outcome(model_margin_mean=30.0)) == "[28,999)"


def test_favorite_direction_margin_error_flips_sign_for_away_favorites():
    # Home favorite projected +10, actual home margin +15 (favorite won by
    # MORE than projected -- model under-predicted/compressed the
    # favorite's margin): favorite-direction error must be positive.
    home_fav_underpredicted = _outcome(model_margin_mean=10.0, actual_home_points=35, actual_away_points=20)
    assert favorite_direction_margin_error(home_fav_underpredicted) == pytest.approx(5.0)

    # Away favorite projected -10 (home margin), actual home margin -15
    # (away favorite won by MORE than projected, same real-world
    # direction as above) -- favorite-direction error must ALSO be
    # positive, even though the raw (home-signed) error here is -5, not
    # +5. This is exactly the sign-mixing problem this function exists
    # to fix.
    away_fav_underpredicted = _outcome(model_margin_mean=-10.0, actual_home_points=20, actual_away_points=35)
    assert favorite_direction_margin_error(away_fav_underpredicted) == pytest.approx(5.0)


def test_favorite_tail_margin_diagnosis_reports_all_five_slices():
    outcomes = [
        _outcome(model_margin_mean=10.0, is_fbs_vs_fbs=True, is_neutral_site=False),
        _outcome(model_margin_mean=-10.0, is_fbs_vs_fbs=True, is_neutral_site=False),
        _outcome(model_margin_mean=5.0, is_fbs_vs_fbs=False, is_neutral_site=True),
    ]
    reports = favorite_tail_margin_diagnosis(outcomes)
    slice_names = {r.slice_name for r in reports}
    assert slice_names == {"home_favorite", "away_favorite", "neutral_site", "fbs_vs_fbs", "fbs_vs_fcs"}
    # Every bin actually present must carry a real n and a finite bias,
    # never a fabricated placeholder for an empty bin.
    for r in reports:
        assert r.n > 0
        assert r.favorite_direction_bias == r.favorite_direction_bias  # not NaN
