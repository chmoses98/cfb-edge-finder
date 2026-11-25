"""Fast synthetic tests for the CFB challenger (no vault data needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import challenger  # noqa: E402,F401
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from challenger import evaluate as E  # noqa: E402
from challenger import model as M  # noqa: E402

TEAMS = [str(i) for i in range(8)]


def _synthetic(seed=0):
    rng = np.random.default_rng(seed)
    strength = dict(zip(TEAMS, np.linspace(-10, 10, len(TEAMS))))
    rows, gid = [], 0
    for season in (2014, 2015, 2016):
        for wk in range(1, 9):
            order = rng.permutation(TEAMS)
            for h, a in zip(order[::2], order[1::2]):
                gid += 1
                hp = 28 + strength[h] / 2 - strength[a] / 2 + 1.5 + rng.normal(0, 7)
                ap = 28 + strength[a] / 2 - strength[h] / 2 - 1.5 + rng.normal(0, 7)
                for me, op, mp, opp_p, site in ((h, a, hp, ap, "home"), (a, h, ap, hp, "away")):
                    rows.append({"game_id": str(gid), "season": season, "week": wk,
                                 "date": f"{season}-09-{wk + 10:02d}", "postseason": False, "team_id": me,
                                 "opponent_id": op, "site": site, "team_division": "fbs", "opponent_division": "fbs",
                                 "points_for": round(mp), "points_against": round(opp_p), "box_plays": 70.0,
                                 "box_total_yards": 400 + 10 * mp, "pbp_success_plays": 30 + mp / 3,
                                 "pbp_scrim_plays": 70.0, "pbp_drives": 12.0, "pbp_drive_points": mp})
    return pd.DataFrame(rows)


def _pre():
    return pd.DataFrame({"season": [], "team": [], "talent_z": [], "ret_z": []})


def test_no_leakage_future_and_same_day_games_do_not_change_features():
    tg = _synthetic()
    games = M.games_table(tg)
    p = M.Params(lam=2.0)
    base = M.walk_forward_features(tg, games, _pre(), p, [2016]).set_index("game_id")
    target_day = "2016-09-15"
    # scramble every outcome on or after the target day (including the target games themselves)
    tg2 = tg.copy()
    later = (tg2.season == 2016) & (tg2.date >= target_day)
    tg2.loc[later, ["points_for", "points_against", "pbp_drive_points"]] = 99
    tg2.loc[later, "box_total_yards"] = 1.0
    alt = M.walk_forward_features(tg2, M.games_table(tg), _pre(), p, [2016]).set_index("game_id")
    upto = games[(games.season == 2016) & (games.date <= target_day)].game_id
    cols = [c for c in base.columns if c.endswith(("_h", "_a"))]
    assert np.allclose(base.loc[upto, cols].to_numpy(float), alt.loc[upto, cols].to_numpy(float))
    after = games[(games.season == 2016) & (games.date > target_day)].game_id
    assert not np.allclose(base.loc[after, cols].to_numpy(float), alt.loc[after, cols].to_numpy(float))


def test_ratings_recover_known_strength_order():
    t = M.team_game_metrics(_synthetic())
    R = M.fit_ratings(t, np.ones(len(t)), 1.0)
    net = [R["pts"]["O"][x] + R["pts"]["D"][x] for x in TEAMS]
    assert np.corrcoef(net, range(len(TEAMS)))[0, 1] > 0.9
    assert 0.5 < R["pts"]["hfa"] < 3.0


def test_layer_and_prediction_are_coherent():
    tg = _synthetic(1)
    games = M.games_table(tg)
    F = M.walk_forward_features(tg, games, _pre(), M.Params(lam=2.0), [2016]).merge(games, on="game_id")
    coef = M.fit_layer(F, (F.home_pts - F.away_pts).to_numpy(), (F.home_pts + F.away_pts).to_numpy(), 1.0, 3.0)
    pr = M.predict(coef, F)
    assert np.allclose(pr.ch_home - pr.ch_away, pr.ch_margin)
    assert np.allclose(pr.ch_home + pr.ch_away, pr.ch_total)
    assert ((pr.ch_p > 0.5) == (pr.ch_margin > 0.5)).all()


def test_compare_sign_and_calibration_slope():
    rng = np.random.default_rng(3)
    n = 400
    m = rng.normal(0, 14, n)
    df = pd.DataFrame({"home_pts": 28 + (m + rng.normal(0, 10, n)) / 2, "away_pts": 28 - (m + rng.normal(0, 10, n)) / 2})
    for tag, noise in (("ch", 0.0), ("prod", 10.0)):
        mm = m + rng.normal(0, noise, n) if noise else m
        df[f"{tag}_margin"], df[f"{tag}_total"] = mm, 56.0
        df[f"{tag}_home"], df[f"{tag}_away"] = 28 + mm / 2, 28 - mm / 2
        df[f"{tag}_p"] = 1 / (1 + np.exp(-mm / 8))
    out = E.compare(df, n_boot=200)
    assert out["margin_ae"]["diff"] < 0 and out["margin_ae"]["ci95"][1] < 0
    assert abs(E.calibration_slope(1 / (1 + np.exp(-m / 8)), (rng.random(n) < 1 / (1 + np.exp(-m / 8))).astype(float)) - 1) < 0.35
