"""CFB Projection Foundation: leakage, coherence and contract tests (synthetic data; no cache needed)."""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from cfb import joint, ratings  # noqa: E402
from cfb import players as pl  # noqa: E402


def _season(seed=0, n_teams=24, weeks=8, season=2020):
    rng = np.random.default_rng(seed)
    teams = [f"t{i}" for i in range(n_teams)]
    off = rng.normal(0, 6, n_teams)
    de = rng.normal(0, 6, n_teams)
    rows = []
    for w in range(1, weeks + 1):
        perm = rng.permutation(n_teams)
        for i in range(0, n_teams, 2):
            h, a = perm[i], perm[i + 1]
            gid = f"{season}_{w}_{i}"
            ko = pd.Timestamp(f"{season}-09-01", tz="UTC") + pd.Timedelta(days=7 * w)
            for t, o, site in ((h, a, 1), (a, h, -1)):
                pts = 28 + off[t] + de[o] + 1.5 * site + rng.normal(0, 8)
                rows.append({"game_id": gid, "season": season, "week": w, "block": season * 100 + w,
                             "kickoff": ko, "team_id": teams[t], "opp_id": teams[o], "team_div": "fbs",
                             "opp_div": "fbs", "site": site, "pts": pts, "post": False})
    return pd.DataFrame(rows), dict(zip(teams, off)), dict(zip(teams, de))


def _two_seasons():
    a, _, _ = _season(0, season=2019)
    b, off, de = _season(1, season=2020)
    return pd.concat([a, b], ignore_index=True), off, de


def test_ratings_are_point_in_time():
    T, _, _ = _two_seasons()
    R1 = ratings.season_ratings(T, "pts", 2020, alpha=3.0, history=1)
    # doctor every outcome from week 5 on: ratings for blocks <= 5 must not move
    T2 = T.copy()
    fut = (T2["season"] == 2020) & (T2["week"] >= 5)
    T2.loc[fut, "pts"] = T2.loc[fut, "pts"] + 40
    R2 = ratings.season_ratings(T2, "pts", 2020, alpha=3.0, history=1)
    k = ["block", "team"]
    a = R1[R1["block"] <= 202005].sort_values(k).reset_index(drop=True)
    b = R2[R2["block"] <= 202005].sort_values(k).reset_index(drop=True)
    assert len(a) > 0
    np.testing.assert_allclose(a["off_rating"], b["off_rating"])
    np.testing.assert_allclose(a["def_rating"], b["def_rating"])
    assert not np.allclose(R1[R1.block == 202007]["intercept"].iloc[0], R2[R2.block == 202007]["intercept"].iloc[0])


def test_week1_uses_only_prior_seasons_and_future_block_exists():
    T, _, _ = _two_seasons()
    R = ratings.season_ratings(T, "pts", 2020, alpha=3.0, history=1)
    assert R["block"].min() == 202001          # pre-season rating from 2019 only
    assert R["block"].max() == 202009          # 'all data so far' block for future weeks
    X = ratings.attach(T[T.season == 2020], R, "D")
    assert X["D_pred"].notna().all()


def test_ratings_recover_truth_late_season():
    T, off, de = _season(3, weeks=12)
    R = ratings.season_ratings(T, "pts", 2020, alpha=2.0, history=0, use_prior_seasons=False)
    last = R[R["block"] == R["block"].max()].set_index("team")
    r_off = np.corrcoef(last.loc[list(off), "off_rating"], list(off.values()))[0, 1]
    r_def = np.corrcoef(last.loc[list(de), "def_rating"], list(de.values()))[0, 1]
    assert r_off > 0.7 and r_def > 0.7


def test_fcs_pooling_labels():
    T = pd.DataFrame({"team_id": ["a", "b"], "opp_id": ["b", "a"], "team_div": ["fbs", "fcs"], "opp_div": ["fcs", "fbs"]})
    o, d = ratings._labels(T, "pool")
    assert list(o) == ["a", "FCS"] and list(d) == ["FCS", "a"]


def test_joint_distribution_is_coherent():
    rng = np.random.default_rng(0)
    mh, ma = rng.uniform(15, 40, 3000), rng.uniform(10, 35, 3000)
    yh, ya = np.maximum(mh + rng.normal(0, 10, 3000), 0), np.maximum(ma + rng.normal(0, 10, 3000), 0)
    J = joint.JointModel("empirical", m=800).fit(mh, ma, yh, ya)
    D = J.distributions(np.array([30.0, 20.0]), np.array([20.0, 31.0]))
    assert np.allclose(D["total"].mean, D["home"].mean + D["away"].mean, atol=1e-2)
    assert np.allclose(D["margin"].mean, D["home"].mean - D["away"].mean, atol=1e-2)
    for d in D.values():
        assert not d.coherence_violations().any()
        p = [d.prob_ge(k) for k in range(-20, 80, 3)]
        assert all((p[i] >= p[i + 1] - 1e-12).all() for i in range(len(p) - 1))
    # spread side: favourite of 10 covers -3 more than half the time
    assert D["margin"].prob_ge(4)[0] > 0.5


def test_player_candidates_use_only_prior_games():
    T = pd.DataFrame({"game_id": ["g1", "g2", "g3"], "team_id": ["x"] * 3, "season": [2020] * 3, "week": [1, 2, 3],
                      "kickoff": pd.to_datetime(["2020-09-01", "2020-09-08", "2020-09-15"], utc=True),
                      "block": [202001, 202002, 202003], "post": [False] * 3, "opp_id": ["o"] * 3,
                      "team_div": ["fbs"] * 3, "opp_div": ["fbs"] * 3, "site": [1] * 3, "fbs_game": [True] * 3})
    P = pd.DataFrame({"game_id": ["g1", "g2"], "team_id": ["x", "x"], "player_id": ["qb", "qb"], "season": [2020, 2020],
                      "name": ["Q", "Q"], "pass_cmp": [10, 12], "pass_att": [20, 25], "pass_yds": [200, 210],
                      "carries": [0, 0], "rush_yds": [0, 0], "receptions": [0, 0], "rec_yds": [0, 0],
                      "has_pass": [True, True], "has_rush": [False, False], "has_rec": [False, False]})
    U = pl.build_panel(P, T).set_index("game_id")
    assert not U.loc["g1", "passer"]          # no prior game -> not a candidate
    assert U.loc["g2", "passer"] and U.loc["g3", "passer"]
    # absent passer: not observed (NaN), never a zero in his history; QB props void when he does not play
    assert not U.loc["g3", "appeared"] and np.isnan(U.loc["g3", "pass_att"])


def test_projection_contract_example_if_present():
    p = os.path.join(HERE, "..", "results", "projections_latest.json")
    if not os.path.exists(p):
        pytest.skip("no projections written yet")
    d = json.load(open(p))
    assert d["schema"] == "sift.projections.v1" and d["sport"] == "CFB"
    assert d["model"]["authority"] == "research_only"
    for g in d["games"]:
        for t in g["teams"]:
            assert t["p10"] <= t["mean"] <= t["p90"] or t["family"] == "margin"
    for pl_ in d["players"]:
        ks = sorted(pl_["thresholds"], key=float)
        ps = [pl_["thresholds"][k] for k in ks]
        assert all(ps[i] >= ps[i + 1] - 1e-9 for i in range(len(ps) - 1))
        assert pl_["gate"]["status"] in ("PASS", "REVIEW", "BLOCK")


def test_summary_schema_if_present():
    p = os.path.join(HERE, "..", "results", "summary.json")
    if not os.path.exists(p):
        pytest.skip("no summary written yet")
    d = json.load(open(p))
    assert d["schema"] == "sift.projection_foundation.sport_summary.v1" and d["sport"] == "CFB"
    for k in ("splits", "inventory", "families", "experiments", "error_investigations", "opponent_adjustment",
              "opportunity", "calibration", "failed_experiments", "gate", "priorities"):
        assert k in d
    for f in d["families"]:
        assert f["readiness"] in ("READY", "SHADOW VALIDATION", "NEEDS IMPROVEMENT", "INSUFFICIENT DATA")
        for m in f["models"]:
            assert m["split"] in ("development", "validation", "holdout", "holdout_2025", "live_2026", "covid_2020")
