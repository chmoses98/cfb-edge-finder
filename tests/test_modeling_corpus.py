from datetime import UTC, datetime

from cfb_edge_finder.modeling.corpus import build_team_game_lines
from cfb_edge_finder.modeling.leakage import AsOf

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _raw_regular_game(**overrides):
    raw = {
        "id": 1,
        "season": 2025,
        "week": 1,
        "seasonType": "regular",
        "startDate": "2025-08-30T19:00:00.000Z",
        "neutralSite": False,
        "completed": True,
        "homeTeam": "Ohio State",
        "homeClassification": "fbs",
        "homePoints": 45,
        "awayTeam": "Texas",
        "awayClassification": "fbs",
        "awayPoints": 21,
    }
    raw.update(overrides)
    return raw


def test_completed_game_produces_two_paired_rows():
    lines, skipped = build_team_game_lines([_raw_regular_game()], [], captured_at=NOW)
    assert skipped == []
    assert len(lines) == 2
    home_row = next(ln for ln in lines if ln.is_home)
    away_row = next(ln for ln in lines if not ln.is_home)
    assert home_row.team_id == "ohio-state" and home_row.opponent_id == "texas"
    assert away_row.team_id == "texas" and away_row.opponent_id == "ohio-state"
    assert home_row.team_points == 45 and home_row.opponent_points == 21
    assert away_row.team_points == 21 and away_row.opponent_points == 45
    assert home_row.as_of == AsOf(season=2025, week=1)


def test_incomplete_game_is_excluded_not_erroring():
    raw = _raw_regular_game(completed=False)
    lines, skipped = build_team_game_lines([raw], [], captured_at=NOW)
    assert lines == []
    assert skipped == []  # not "completed" is a silent, expected exclusion, not a reportable skip


def test_ambiguous_team_name_is_skipped_and_reported_not_silently_dropped():
    raw = _raw_regular_game(homeTeam="Miami")
    lines, skipped = build_team_game_lines([raw], [], captured_at=NOW)
    assert lines == []
    assert len(skipped) == 1
    assert "Miami" in skipped[0]["reason"]


def test_fbs_vs_fcs_game_retained_with_fcs_classification():
    raw = _raw_regular_game(awayTeam="Furman", awayClassification="fcs", awayPoints=3)
    lines, skipped = build_team_game_lines([raw], [], captured_at=NOW)
    assert skipped == []
    assert len(lines) == 2
    away_row = next(ln for ln in lines if not ln.is_home)
    assert away_row.team_id == "furman"
    assert away_row.team_classification == "fcs"


def test_plays_joined_from_advanced_stats_by_game_id_and_team_name():
    raw_games = [_raw_regular_game()]
    raw_advanced = [
        {"game_id": 1, "team": "Ohio State", "offense": {"plays": 70}},
        {"game_id": 1, "team": "Texas", "offense": {"plays": 65}},
    ]
    lines, _ = build_team_game_lines(raw_games, raw_advanced, captured_at=NOW)
    home_row = next(ln for ln in lines if ln.is_home)
    away_row = next(ln for ln in lines if not ln.is_home)
    assert home_row.team_plays == 70
    assert away_row.team_plays == 65


def test_missing_advanced_stats_row_leaves_plays_none_not_an_error():
    lines, skipped = build_team_game_lines([_raw_regular_game()], [], captured_at=NOW)
    assert skipped == []
    assert all(ln.team_plays is None for ln in lines)


def test_postseason_cfp_game_gets_a_week_rank_strictly_after_regular_season():
    from cfb_edge_finder.modeling.leakage import REGULAR_SEASON_WEEK_CEILING

    raw = _raw_regular_game(
        week=None,
        seasonType="postseason",
        neutralSite=True,
        playoff={"competition": "cfp", "round": "quarterfinal"},
    )
    lines, skipped = build_team_game_lines([raw], [], captured_at=NOW)
    assert skipped == []
    assert all(ln.week > REGULAR_SEASON_WEEK_CEILING for ln in lines)
    assert all(ln.is_postseason for ln in lines)


def test_unclassifiable_postseason_game_is_skipped_and_reported():
    raw = _raw_regular_game(week=None, seasonType="postseason", notes="totally unparseable free text")
    lines, skipped = build_team_game_lines([raw], [], captured_at=NOW)
    assert lines == []
    assert len(skipped) == 1
    assert "unclassifiable" in skipped[0]["reason"].lower()


def test_neutral_site_flag_carried_onto_both_rows():
    raw = _raw_regular_game(neutralSite=True)
    lines, _ = build_team_game_lines([raw], [], captured_at=NOW)
    assert all(ln.is_neutral_site for ln in lines)


def test_game_with_no_fbs_side_is_excluded_not_miscounted_as_fbs_vs_fcs():
    # CFBD's division=fbs filter does not fully exclude non-FBS-involving
    # games (a real, independently-observed gap -- see corpus.py's
    # _is_fbs_involved docstring). A Division-II-vs-FCS game must be
    # dropped entirely, never retained and miscounted as FBS-vs-FCS.
    raw = _raw_regular_game(
        homeTeam="University of Mary", homeClassification="ii", awayTeam="Furman", awayClassification="fcs"
    )
    lines, skipped = build_team_game_lines([raw], [], captured_at=NOW)
    assert lines == []
    assert len(skipped) == 1
    assert "no fbs side" in skipped[0]["reason"].lower()


def test_genuine_fbs_vs_fcs_game_is_still_retained():
    raw = _raw_regular_game(awayTeam="Furman", awayClassification="fcs")
    lines, skipped = build_team_game_lines([raw], [], captured_at=NOW)
    assert skipped == []
    assert len(lines) == 2
