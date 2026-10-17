"""Behaviour pins for the shared Projection Foundation core."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pf_core import dists, gate, metrics, models, pit  # noqa: E402


def test_poisson_moments_and_thresholds():
    d = dists.poisson(np.array([2.0, 5.0]))
    assert np.allclose(d.mean, [2.0, 5.0], atol=1e-3)
    p1 = d.prob_ge(1)
    assert np.allclose(p1, 1 - np.exp(-np.array([2.0, 5.0])), atol=1e-6)
    ks = [d.prob_ge(k) for k in range(0, 10)]
    assert all((ks[i] >= ks[i + 1] - 1e-12).all() for i in range(9)), "thresholds must be monotone"
    assert not d.coherence_violations().any()


def test_negbin_variance():
    d = dists.negbin(np.array([4.0]), 0.5)
    assert abs(d.mean[0] - 4.0) < 1e-2
    assert abs(d.var[0] - (4 + 0.5 * 16)) < 0.1


def test_crps_and_pit_calibrated_on_own_draws():
    rng = np.random.default_rng(1)
    mu = rng.uniform(1, 8, 20000)
    y = rng.poisson(mu)
    d = dists.poisson(mu)
    m = metrics.dist_metrics(y, d, thresholds=[1, 3, 5])
    assert m["pit_max_dev"] < 0.01
    assert abs(m["cover_80"] - 0.8) < 0.01
    assert m["range_cover_80"] >= 0.8  # integer ranges are conservative
    # a too-narrow forecast must show PIT variance > 1
    narrow = dists.discretized_normal(mu, np.sqrt(mu) * 0.5, 0, 40)
    assert metrics.dist_metrics(y, narrow)["pit_var_ratio"] > 1.1


def test_hurdle_adds_zero_mass():
    base = dists.poisson(np.array([3.0]))
    h = dists.hurdle([0.3], base)
    assert abs(h.P[0, 0] - (0.3 + 0.7 * np.exp(-3))) < 1e-9


def test_quantiles_and_interval():
    d = dists.DistBatch(0, np.array([[0.1, 0.2, 0.4, 0.2, 0.1]]))
    assert d.median[0] == 2
    lo, hi = d.interval(0.8)
    assert lo[0] == 0 and hi[0] == 3  # q(0.1)=0 (F(0)=0.1), q(0.9)=3


def test_lagged_features_no_leakage():
    df = pd.DataFrame({"p": ["a"] * 4 + ["b"] * 3, "season": [1, 1, 2, 2, 1, 1, 1],
                       "d": pd.to_datetime(["2020-01-01", "2020-01-08", "2021-01-01", "2021-01-08",
                                            "2020-01-01", "2020-01-08", "2020-01-15"]),
                       "x": [1.0, 3.0, 5.0, 7.0, 10.0, np.nan, 20.0]})
    out = pit.lagged_features(pit.sort_chrono(df, "p", "d"), "p", ["x"], halflives=(2,), season_col="season")
    a = out[out.p == "a"].reset_index(drop=True)
    assert np.isnan(a.loc[0, "x_career"])
    assert a.loc[1, "x_career"] == 1.0
    assert a.loc[3, "x_career"] == 3.0       # (1+3+5)/3: current game excluded
    assert a.loc[2, "x_std_n"] == 0 and a.loc[3, "x_std"] == 5.0
    assert a.loc[2, "x_prev"] == 2.0 and a.loc[2, "x_prev_n"] == 2
    b = out[out.p == "b"].reset_index(drop=True)
    assert b.loc[2, "x_n"] == 1 and b.loc[2, "x_career"] == 10.0  # NaN game skipped


def test_eb_shrink_and_k():
    assert np.isclose(pit.eb_shrink(10.0, 0, 4.0, 5.0), 4.0)
    assert np.isclose(pit.eb_shrink(10.0, 5, 4.0, 5.0), 7.0)
    rng = np.random.default_rng(0)
    true = rng.normal(5, 1, 300)
    vals, grp = [], []
    for i, t in enumerate(true):
        v = rng.normal(t, 3, 40)
        vals += list(v)
        grp += [i] * 40
    k = pit.estimate_shrinkage_k(pd.Series(vals), pd.Series(grp))
    assert 5 < k < 15  # truth 9/1 = 9


def test_ridge_ratings_schedule_adjustment():
    # team D only plays the weak offence W; raw allowed looks great, adjusted should not
    rng = np.random.default_rng(0)
    rows = []
    off = {"S": 7.0, "W": -7.0, "M": 0.0, "D": 0.0}
    deff = {"S": 0.0, "W": 0.0, "M": 0.0, "D": 0.0}
    teams = list(off)
    blk = 0
    for wk in range(12):
        for o in teams:
            for d in teams:
                if o == d:
                    continue
                if d == "D" and o != "W":
                    continue
                rows.append({"blk": wk, "o": o, "d": d, "y": 24 + off[o] + deff[d] + rng.normal(0, 3)})
    g = pd.DataFrame(rows)
    r = pit.rolling_ridge_ratings(g, "blk", "o", "d", "y", alpha=1.0)
    last = r[r.blk == r.blk.max()].set_index("team")
    assert abs(last.loc["D", "def_rating"]) < 2.5  # not credited for facing W


def test_rolling_origin_is_chronological():
    df = pd.DataFrame({"blk": [1, 1, 2, 2, 3], "y": [1, 2, 3, 4, 5]})
    seen = []

    def fp(train, test, state):
        seen.append((train.blk.max(), test.blk.min()))
        return test.assign(pred=train.y.mean()), None
    out = pit.rolling_origin(df, "blk", fp)
    assert all(tr < te for tr, te in seen)
    assert list(out.pred) == [1.5, 1.5, 2.5]


def test_assert_point_in_time():
    with pytest.raises(AssertionError):
        pit.assert_point_in_time(pd.Series(["2026-01-02T00:00Z"]), pd.Series(["2026-01-01T00:00Z"]))


def test_cluster_bootstrap_detects_difference():
    rng = np.random.default_rng(0)
    cl = np.repeat(np.arange(200), 10)
    a = rng.normal(1.0, 1, 2000)
    b = a + 0.2 + rng.normal(0, 0.1, 2000)
    r = metrics.cluster_bootstrap_diff(a, b, cl)
    assert r["ci95"][1] < 0 and r["n_clusters"] == 200


def test_models_stack_and_beta():
    rng = np.random.default_rng(0)
    base = rng.uniform(2, 6, 5000)
    lf = rng.normal(0, 0.2, 5000)
    y = rng.poisson(base * np.exp(0.8 * lf))
    beta = models.fit_opponent_beta(y, base, lf)
    assert 0.6 < beta < 1.0
    w = models.fit_stack_weights(y, np.column_stack([base, base * np.exp(0.8 * lf)]))
    assert w[1] > w[0] and abs(w.sum() - 1) < 1e-9


def test_fit_and_make_dists():
    rng = np.random.default_rng(0)
    mu = rng.uniform(1, 10, 4000)
    y = rng.negative_binomial(1 / 0.3, 1 / (1 + 0.3 * mu))
    p = models.fit_dist_params("nb", y, mu)
    assert 0.2 < p["alpha"] < 0.4
    for kind, kw in [("nb_binned", {}), ("normal_binned", {"K": 80}), ("empirical_ratio", {"K": 80})]:
        prm = models.fit_dist_params(kind, y, mu, **kw)
        d = models.make_dist(prm, mu[:50])
        assert not d.coherence_violations().any()


def test_gate_statuses():
    ok = gate.GateInput("NBA", "points", "1", "X", "BOS", "g", "2026-11-01T23:00:00Z", 20.0,
                        data_asof_utc="2026-11-01T12:00:00Z", injury_status="ACTIVE",
                        expected_opportunity=32, opportunity_history=[30, 33, 31, 34, 32],
                        stat_history=[18, 22, 25, 19, 21], model_n_obs=40,
                        thresholds={"10": 0.9, "20": 0.5, "30": 0.1})
    assert gate.evaluate(ok, now_utc="2026-11-01T13:00:00Z")["status"] == gate.PASS
    bad = gate.GateInput(**{**ok.__dict__, "thresholds": {"10": 0.5, "20": 0.6}})
    assert gate.evaluate(bad, now_utc="2026-11-01T13:00:00Z")["status"] == gate.BLOCK
    out = gate.GateInput(**{**ok.__dict__, "injury_status": "OUT"})
    assert gate.evaluate(out, now_utc="2026-11-01T13:00:00Z")["status"] == gate.BLOCK
    role = gate.GateInput(**{**ok.__dict__, "expected_opportunity": 44})
    r = gate.evaluate(role, now_utc="2026-11-01T13:00:00Z")
    assert r["status"] == gate.REVIEW and r["findings"][0]["check"] == "role"
    leak = gate.GateInput(**{**ok.__dict__, "data_asof_utc": "2026-11-02T00:00:00Z"})
    assert gate.evaluate(leak, now_utc="2026-11-01T13:00:00Z")["status"] == gate.BLOCK
    assert gate.team_reconciliation(100, 110, "pts")["status"] == gate.PASS
    assert gate.team_reconciliation(60, 110, "pts")["status"] == gate.BLOCK


def test_vendored_copy_matches_manifest():
    """pf_core is shared byte-identically across sport repos; edits must go through the canonical copy."""
    import hashlib
    import json
    base = os.path.join(os.path.dirname(__file__), "..", "pf_core")
    man = json.load(open(os.path.join(base, "MANIFEST.json")))
    for name, sha in man["sha256"].items():
        assert hashlib.sha256(open(os.path.join(base, name), "rb").read()).hexdigest() == sha, name
