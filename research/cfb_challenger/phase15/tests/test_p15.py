"""Synthetic tests for the Phase 15 CFB extensions (no vault data needed)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tests"))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import p15model as X  # noqa: E402
from challenger import model as M  # noqa: E402
from periods_data import parse_summary  # noqa: E402
from test_challenger import _pre, _synthetic  # noqa: E402


def _periods(tg):
    rng = np.random.default_rng(5)
    h = tg[tg.site == "home"]
    rows = []
    for r in h.itertuples():
        hq = rng.multinomial(int(r.points_for), [0.25] * 4) if r.points_for > 0 else [0] * 4
        aq = rng.multinomial(int(r.points_against), [0.25] * 4) if r.points_against > 0 else [0] * 4
        d = {"game_id": r.game_id, "home": r.team_id, "away": r.opponent_id, "valid": True}
        for q in range(4):
            d[f"h_q{q + 1}"], d[f"a_q{q + 1}"] = float(hq[q]), float(aq[q])
        rows.append(d)
    return pd.DataFrame(rows)


def test_reduces_exactly_to_pr307_when_extensions_off():
    tg = _synthetic()
    games = M.games_table(tg)
    a = M.walk_forward_features(tg, games, _pre(), M.Params(lam=1.0, w_prev=0.1, w_prev2=0.1), [2016])
    b = X.walk_forward(tg, games, _pre(), X.Params15(), [2016])
    cols = [c for c in a.columns if c != "game_id"]
    assert np.allclose(a[cols].to_numpy(float), b[cols].to_numpy(float))


def test_truncation_all_extensions_on():
    """Removing every team-game on/after a date leaves that date's features unchanged (centre + period ratings)."""
    tg = X.add_period_points(_synthetic(), _periods(_synthetic()))
    games = M.games_table(tg)
    p = X.Params15(lam_c=4.0, rho=0.7, periods=True)
    full = X.walk_forward(tg, games, _pre(), p, [2016]).set_index("game_id")
    for d in ("2016-09-11", "2016-09-14", "2016-09-18"):
        trunc = X.walk_forward(tg[tg.date < d], games, _pre(), p, [2016], dates={d}).set_index("game_id")
        assert len(trunc) > 0
        assert np.allclose(full.loc[trunc.index, trunc.columns].to_numpy(float), trunc.to_numpy(float))


def test_centre_pulls_week1_toward_prior_season():
    tg = _synthetic()
    games = M.games_table(tg)
    w1 = games[(games.season == 2016) & (games.week == 1)].game_id
    a = X.walk_forward(tg, games, _pre(), X.Params15(), [2016]).set_index("game_id").loc[w1]
    b = X.walk_forward(tg, games, _pre(), X.Params15(lam_c=8.0, rho=0.9), [2016]).set_index("game_id").loc[w1]
    # strengths are persistent in the synthetic data, so the centred prior spreads week-1 expectations further apart
    assert (b.pts_h - b.pts_a).abs().mean() > (a.pts_h - a.pts_a).abs().mean()


def test_sigma_curve_recovers_heteroscedasticity():
    rng = np.random.default_rng(0)
    n = rng.integers(0, 12, 20000).astype(float)
    true = np.exp(2.7 + 0.4 * 3 / (3 + n))
    c = X.fit_sigma_curve(rng.normal(0, true), n, 3.0)
    assert abs(c["g1"] - 0.4) < 0.08 and abs(c["g0"] - 2.7) < 0.03
    assert X.sigma_of(c, [0])[0] > X.sigma_of(c, [10])[0]


def test_neighbour_pmf_is_a_pmf_and_tracks_mean():
    rng = np.random.default_rng(1)
    mu = rng.uniform(3, 20, 5000)
    y = rng.poisson(mu)
    nb = X.NeighbourPMF(mu, y, k=500)
    lo, hi = nb.pmf(4.0), nb.pmf(18.0)
    assert abs(lo.sum() - 1) < 1e-9 and (lo >= 0).all()
    assert (np.arange(60) * lo).sum() < (np.arange(60) * hi).sum()
    J = X.joint_independent(lo, hi, 1.3)
    assert abs(J.sum() - 1) < 1e-9


def test_parse_summary_linescores_and_ot():
    def comp(ha, tid, score, ls):
        return {"homeAway": ha, "team": {"id": tid}, "score": str(score), "linescores": [{"displayValue": str(v)} for v in ls]}
    d = {"header": {"id": "1", "season": {"year": 2025}, "competitions": [{
        "id": "1", "date": "2025-09-01T00:00Z", "neutralSite": False, "status": {"type": {"name": "STATUS_FINAL"}},
        "competitors": [comp("home", "10", 31, [7, 7, 3, 7, 7]), comp("away", "20", 24, [0, 10, 7, 7, 0])]}]}}
    r = parse_summary(d)
    assert r["valid"] and r["h_ot"] == 7 and r["a_ot"] == 0 and r["h_q4"] == 7 and r["n_periods"] == 5
    d["header"]["competitions"][0]["competitors"][0]["score"] = "30"
    assert not parse_summary(d)["valid"]


def test_kalshi_contract_outcome_and_probability_conventions():
    import pytest
    R = pytest.importorskip("run_p15", reason="needs the shared Phase 15 p15eval module (P15EVAL_DIR)")
    PE = R.PE
    pm = PE.pmf_from_margin_total(7.0, 10.0, 50.0, 12.0, 0.0, 120)
    # "<team> wins by more than X": home side uses home - away, away side uses away - home
    assert R.contract_outcome("spread", "home", 6.5, 30, 20) == 1.0
    assert R.contract_outcome("spread", "away", 6.5, 30, 20) == 0.0
    assert R.contract_outcome("winner", "tie", 0.0, 7, 7) == 1.0
    ph, pa = R.contract_prob(pm, "spread", "home", 6.5), R.contract_prob(pm, "spread", "away", -6.5)
    assert abs(ph + pa - 1) < 1e-9          # home by > 6.5 and away by > -6.5 partition the integer margins
    assert R.contract_prob(pm, "winner", "home", 0) > R.contract_prob(pm, "winner", "away", 0)
