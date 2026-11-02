"""Per-period scoring capture: ESPN linescores -> TeamGame.period_points -> period records. Never inferred."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cfb_edge_finder.projections.period_data import MIN_GAMES_TO_FIT_PERIODS, coverage, period_games
from cfb_edge_finder.scripting.espn import period_points_from_competitor, team_games_from_summary
from cfb_edge_finder.scripting.gamelog import TeamGame

FIXTURES = Path(__file__).parent / "fixtures" / "espn"


def ls(*vals):
    return {"linescores": [{"displayValue": str(v)} for v in vals]}


def test_parser_accepts_only_linescores_that_sum_to_the_final_score():
    assert period_points_from_competitor(ls(14, 21, 14, 14), 63) == (14.0, 21.0, 14.0, 14.0)
    assert period_points_from_competitor(ls(7, 7, 7, 7, 3), 31) == (7.0, 7.0, 7.0, 7.0, 3.0)  # overtime reconciles
    assert period_points_from_competitor(ls(14, 21, 14, 14), 62) is None  # does not add up -> not evidence
    assert period_points_from_competitor(ls(14, 21, 14), 49) is None  # fewer than four periods
    assert period_points_from_competitor(ls(14, "x", 14, 14), 42) is None
    assert period_points_from_competitor(ls(14, 3.5, 14, 14), 45) is None  # fractional points are not football
    assert period_points_from_competitor(ls(14, -1, 14, 14), 41) is None
    assert period_points_from_competitor({}, 10) is None and period_points_from_competitor(ls(7, 7, 7, 7), None) is None


@pytest.mark.parametrize("name", ["summary_401856706.json", "summary_401856705.json"])
def test_real_espn_summary_fixtures_yield_reconciled_periods(name):
    summary = json.loads((FIXTURES / name).read_text())
    rows = team_games_from_summary(summary, observed_at="2026-11-02T00:00:00Z")
    assert len(rows) == 2
    for r in rows:
        assert r.period_points is not None, f"{name}: the fixture carries linescores that sum to the score"
        assert int(sum(r.period_points)) == int(r.points_for)


def test_team_game_roundtrip_is_additive_and_old_rows_are_unchanged():
    summary = json.loads((FIXTURES / "summary_401856706.json").read_text())
    row = team_games_from_summary(summary, observed_at="2026-11-02T00:00:00Z")[0]
    d = row.as_dict()
    assert isinstance(d["period_points"], list)
    assert TeamGame.from_dict(json.loads(json.dumps(d))).period_points == row.period_points
    # a historical row (written before the field existed) has no key and reads back as None, byte shape unchanged
    old = {k: v for k, v in d.items() if k != "period_points"}
    assert TeamGame.from_dict(old).period_points is None
    assert "period_points" not in TeamGame.from_dict(old).as_dict()


def _row(gid, site, team, pts, periods):
    return TeamGame(
        game_id=gid, season=2026, week=1, kickoff_utc="2026-09-05T16:00:00Z", team_id=team, team=team,
        opponent_id="x", opponent="x", team_division="fbs", opponent_division="fbs", site=site,
        points_for=pts, points_against=0, box={}, pbp=None, period_points=periods,
    )


def test_period_games_and_coverage_never_estimate_a_missing_period():
    rows = [
        _row("g1", "home", "H", 24, (7.0, 10.0, 0.0, 7.0)), _row("g1", "away", "A", 17, (3.0, 7.0, 7.0, 0.0)),
        _row("g2", "home", "H2", 30, None), _row("g2", "away", "A2", 10, None),  # no periods: no record
        _row("g3", "home", "H3", 27, (7.0, 7.0, 7.0, 3.0, 3.0)), _row("g3", "away", "A3", 24, (7.0, 7.0, 7.0, 3.0, 0.0)),
    ]
    games = {g["game_id"]: g for g in period_games(rows)}
    assert set(games) == {"g1", "g3"}  # g2 is absent, not zero-filled and not derived from the final score
    assert games["g1"]["home_q1"] == 7.0 and games["g1"]["home_1h"] == 17.0 and games["g1"]["away_1h"] == 10.0
    assert games["g3"]["overtime"] is True and games["g1"]["overtime"] is False
    cov = coverage(rows)
    assert cov["games"] == 3 and cov["games_with_periods"] == 2 and cov["games_without_periods"] == 1
    assert cov["enough_to_fit"] is False and cov["min_games_to_fit"] == MIN_GAMES_TO_FIT_PERIODS


def test_period_pricing_is_still_refused_without_a_fitted_validated_model():
    from cfb_edge_finder.projections.discrete_scoring import MissingPeriodCalibration, ShapeParams, build_period_distribution

    with pytest.raises(MissingPeriodCalibration):
        build_period_distribution(28.0, 24.0, ShapeParams(0.15, 0.0, 0.0), period_share=None)


def test_collector_backfill_is_opt_in_and_off_by_default():
    src = (Path(__file__).parents[1] / "scripts" / "collect_football_gamelog.py").read_text()
    assert '"--backfill-periods"' in src and "default=0" in src.split('"--backfill-periods"', 1)[1][:400]
