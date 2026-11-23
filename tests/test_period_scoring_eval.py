import importlib.util
import pathlib
import random

import numpy as np

_SPEC = importlib.util.spec_from_file_location(
    "period_scoring_eval", pathlib.Path(__file__).resolve().parents[1] / "scripts/research/period_scoring_eval.py"
)
pe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pe)


def _tg(gid, week, home_pts, away_pts, hp, ap, division="fbs"):
    base = {"game_id": gid, "week": week, "team_division": division}
    return [
        {**base, "team_id": f"H{gid}", "site": "home", "points_for": home_pts, "period_points": hp},
        {**base, "team_id": f"A{gid}", "site": "away", "points_for": away_pts, "period_points": ap},
    ]


def test_load_games_rejects_periods_that_do_not_sum_to_final():
    ok = _tg("1", 1, 17, 10, [7, 3, 7, 0], [3, 7, 0, 0])
    bad = _tg("2", 1, 17, 10, [7, 3, 7, 1], [3, 7, 0, 0])  # home quarters sum to 18
    g = pe.load_games(ok + bad)
    assert [x["gid"] for x in g] == ["1"]
    assert g[0]["q"] == (7, 3) and g[0]["h"] == (10, 10)


def test_load_games_uses_backfill_and_rejects_team_mismatch():
    rows = _tg("3", 1, 14, 7, None, None)
    for r in rows:
        r["period_points"] = None
    good = {
        "game_id": "3",
        "home_team_id": "H3",
        "away_team_id": "A3",
        "home_periods": [7, 0, 7, 0],
        "away_periods": [0, 7, 0, 0],
    }
    bad = {**good, "home_team_id": "X"}
    assert len(pe.load_games(rows, [good])) == 1
    assert pe.load_games(rows, [bad]) == []


def _synthetic(n_weeks=10, per_week=40, seed=1):
    rnd = random.Random(seed)
    rows = []
    gid = 0
    for w in range(1, n_weeks + 1):
        for _ in range(per_week):
            gid += 1
            hq = [rnd.choice([0, 0, 3, 7, 7, 10, 14]) for _ in range(4)]
            aq = [rnd.choice([0, 0, 3, 7, 7, 10, 14]) for _ in range(4)]
            rows += _tg(str(gid), w, sum(hq), sum(aq), hq, aq)
    return pe.load_games(rows)


def test_only_earlier_weeks_are_used_for_each_test_week():
    games = _synthetic()
    full = pe.shape_logscores(games, "q", "total", [7])
    # Rewriting every game from week 7 onward must not change the week-7 scores' training set: change weeks 8+ only.
    altered = [dict(g) for g in games]
    for g in altered:
        if g["week"] >= 8:
            g["q"] = (99, 99)
    same = pe.shape_logscores(altered, "q", "total", [7])
    assert np.allclose(full[0], same[0]) and np.allclose(full[1], same[1])


def test_discrete_pmf_beats_normal_on_lumpy_scores():
    e, n = pe.shape_logscores(_synthetic(), "q", "total", [7, 8, 9])
    assert len(e) == 120
    assert (n - e).mean() > 0  # the synthetic scores are multiples of 3/7: a Normal must lose


def test_pmfs_normalise():
    tr = np.array([0, 3, 7, 7, 10, 14, 7, 3])
    assert abs(pe.empirical_pmf(tr, -5, 30).sum() - 1) < 1e-12
    assert abs(pe.normal_pmf(tr, -5, 30, "total").sum() - 1) < 1e-12
    assert pe.normal_pmf(tr, -5, 30, "total")[:5].max() <= 1e-5  # no mass on negative totals (floor only)


def test_run_reports_all_cells_and_is_deterministic():
    games = _synthetic()
    a = pe.run(games, [5, 6], [7, 8, 9])
    b = pe.run(games, [5, 6], [7, 8, 9])
    assert a == b
    assert set(a["shape"]) == {"q|total", "q|margin", "h|total", "h|margin"}
    assert a["n_heldout"] == 120
    assert "Held-out" in pe.render(a)


def test_lambda_is_chosen_without_touching_heldout_weeks():
    games = _synthetic()
    lam = pe.choose_lambda(games, "h", "total", [5, 6])
    altered = [dict(g) for g in games]
    for g in altered:
        if g["week"] >= 7:
            g["h"] = (50, 50)
    assert pe.choose_lambda(altered, "h", "total", [5, 6]) == lam
