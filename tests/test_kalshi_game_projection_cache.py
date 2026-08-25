"""GameProjectionCache: one football-model run per (game_id, as_of,
params) tuple, reused across repeated calls -- mission section 17's
scale-architecture requirement, at the cache-identity level."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest
from cfb_edge_finder.modeling.corpus import TeamGameLine

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _line(team, opp, pts, opp_pts, plays, home, week=1):
    return TeamGameLine(
        source_game_id=f"{'-'.join(sorted([team, opp]))}-{week}",
        season=2025,
        week=week,
        is_postseason=False,
        team_id=team,
        opponent_id=opp,
        team_classification="fbs",
        opponent_classification="fbs",
        is_home=home,
        is_neutral_site=False,
        team_points=pts,
        opponent_points=opp_pts,
        team_plays=plays,
        captured_at=NOW,
    )


def _synthetic_history(n_teams=16, n_weeks=6, seed=11):
    rng = np.random.default_rng(seed)
    teams = [f"t{i}" for i in range(n_teams)]
    strength = {t: rng.normal(0, 0.05) for t in teams}
    lines = []
    for week in range(1, n_weeks + 1):
        shuffled = teams[:]
        rng.shuffle(shuffled)
        for i in range(0, len(shuffled), 2):
            home, away = shuffled[i], shuffled[i + 1]
            home_pts = max(int(rng.normal(28 + strength[home] * 180 + 2, 9)), 0)
            away_pts = max(int(rng.normal(24 + strength[away] * 180, 9)), 0)
            lines.append(_line(home, away, home_pts, away_pts, 68, True, week=week))
            lines.append(_line(away, home, away_pts, home_pts, 66, False, week=week))
    return lines


@pytest.fixture(scope="module")
def cache():
    return GameProjectionCache(_synthetic_history())


def _request(**overrides):
    defaults = dict(
        game_id="t0-vs-t1",
        home_id="t0",
        away_id="t1",
        home_classification="fbs",
        away_classification="fbs",
        is_neutral_site=False,
        as_of_season=2025,
        as_of_week=7,
        n_simulations=500,
        seed=0,
    )
    defaults.update(overrides)
    return GameProjectionRequest(**defaults)


def test_same_request_returns_the_same_cached_object(cache):
    request = _request()
    first = cache.get_or_build(request)
    second = cache.get_or_build(request)
    assert first is second
    assert len(cache) == 1


def test_different_game_id_builds_a_new_entry(cache):
    cache.get_or_build(_request(game_id="t0-vs-t1"))
    other = cache.get_or_build(_request(game_id="t2-vs-t3", home_id="t2", away_id="t3"))
    assert other is not cache.get_or_build(_request(game_id="t0-vs-t1"))


def test_is_fbs_vs_fbs_flag_set_correctly(cache):
    result = cache.get_or_build(_request(game_id="fbs-flag-check"))
    assert result.is_fbs_vs_fbs is True


def test_no_history_before_as_of_raises():
    empty_cache = GameProjectionCache([])
    with pytest.raises(ValueError):
        empty_cache.get_or_build(_request(game_id="no-history"))
