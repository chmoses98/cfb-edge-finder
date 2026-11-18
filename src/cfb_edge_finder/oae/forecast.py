"""Game layer, uncertainty and period distributions (numpy + math only), ported from research #307/#308.

Sign convention: margin = home - away (positive favours home); sportsbook spread_home = -expected_margin.
Kalshi "<team> wins by more than X" = P(team - opponent > X) from the joint score PMF.
"""
from __future__ import annotations

import math

import numpy as np

_erfc = np.frompyfunc(math.erfc, 1, 1)
SQRT2 = math.sqrt(2.0)


def norm_cdf(x, loc=0.0, scale=1.0) -> np.ndarray:
    z = (np.asarray(x, float) - loc) / scale
    return 0.5 * np.asarray(_erfc(-z / SQRT2), dtype=float)


# ---------------------------------------------------------------- full game (#307 layer, #308 sigma pack)
def design(f: dict, k: float) -> tuple[np.ndarray, np.ndarray]:
    dec = (k / (k + f["n_h"]) + k / (k + f["n_a"])) / 2
    marg = [f["pts_h"] - f["pts_a"], f["ppd_h"] - f["ppd_a"], f["sr_h"] - f["sr_a"], f["ypp_h"] - f["ypp_a"],
            f["ppd_h"] * f["drives_h"] - f["ppd_a"] * f["drives_a"], f["tal_h"] - f["tal_a"],
            (f["tal_h"] - f["tal_a"]) * dec, (f["ret_h"] - f["ret_a"]) * dec, f["hf"]]
    tot = [f["pts_h"] + f["pts_a"], f["ppd_h"] * f["drives_h"] + f["ppd_a"] * f["drives_a"],
           f["plays_h"] + f["plays_a"], f["sr_h"] + f["sr_a"], f["ypp_h"] + f["ypp_a"],
           (f["tal_h"] + f["tal_a"]) * dec]
    return np.array(marg, float), np.array(tot, float)


def ridge_apply(c: dict, x: np.ndarray) -> float:
    z = np.r_[1.0, (np.asarray(x, float) - np.array(c["mu"])) / np.array(c["sd"])]
    return float(z @ np.array(c["b"]))


def sigma_of(curve: dict, n_avg: float) -> float:
    return float(np.exp(curve["g0"] + curve["g1"] * curve["k"] / (curve["k"] + n_avg)))


def full_game(f: dict, layer: dict, sigma_pack: dict) -> dict:
    xm, xt = design(f, layer["k"])
    m, t = ridge_apply(layer["margin"], xm), ridge_apply(layer["total"], xt)
    n_avg = (f["n_h"] + f["n_a"]) / 2
    sd_m, sd_t = sigma_of(sigma_pack["margin"], n_avg), sigma_of(sigma_pack["total"], n_avg)
    p_home = float(1.0 - norm_cdf(0.5, m, sd_m))            # P(margin > 0), 0.5 continuity correction
    return {"margin": m, "total": t, "home_pts": (t + m) / 2, "away_pts": (t - m) / 2, "sd_m": sd_m, "sd_t": sd_t,
            "rho": float(sigma_pack["rho_mt"]), "p_home_win": p_home}


def joint_from_margin_total(mu_m, sd_m, mu_t, sd_t, rho_mt, n=200) -> np.ndarray:
    """Discretised bivariate normal on integer (home, away) cells, parameterised by margin and total."""
    sd_h2 = 0.25 * (sd_t ** 2 + sd_m ** 2 + 2 * rho_mt * sd_t * sd_m)
    sd_a2 = 0.25 * (sd_t ** 2 + sd_m ** 2 - 2 * rho_mt * sd_t * sd_m)
    cov = 0.25 * (sd_t ** 2 - sd_m ** 2)
    sh, sa = math.sqrt(sd_h2), math.sqrt(sd_a2)
    rho = float(np.clip(cov / (sh * sa), -0.95, 0.95))
    mu_h, mu_a = (mu_t + mu_m) / 2, (mu_t - mu_m) / 2
    h = np.arange(n)
    ph = norm_cdf(h + 0.5, mu_h, sh) - norm_cdf(h - 0.5, mu_h, sh)
    cm = mu_a + rho * sa / sh * (h - mu_h)
    cs = sa * math.sqrt(max(1 - rho ** 2, 1e-6))
    pa = norm_cdf(h[None, :] + 0.5, cm[:, None], cs) - norm_cdf(h[None, :] - 0.5, cm[:, None], cs)
    return _normalise(ph[:, None] * pa)


# ---------------------------------------------------------------- periods (#308 empirical neighbour PMFs)
class NeighbourPMF:
    def __init__(self, mu_train, y_train, k: int = 1500, nmax: int = 60, smooth: float = 0.02):
        o = np.argsort(mu_train)
        self.mu, self.y = np.asarray(mu_train, float)[o], np.clip(np.asarray(y_train, int)[o], 0, nmax - 1)
        self.k, self.nmax, self.smooth = min(k, len(self.mu)), nmax, smooth
        self.glob = np.bincount(self.y, minlength=nmax) / len(self.y)
        self.cum = np.zeros((len(self.y) + 1, nmax))
        self.cum[1:] = np.cumsum(np.eye(nmax)[self.y], axis=0)

    def pmf(self, m: float) -> np.ndarray:
        i = int(np.searchsorted(self.mu, m))
        lo = int(np.clip(i - self.k // 2, 0, len(self.mu) - self.k))
        h = (self.cum[lo + self.k] - self.cum[lo]) / self.k
        return (1 - self.smooth) * h + self.smooth * self.glob


def period_means(f: dict, fg: dict, seg: str, coef: dict) -> tuple[float, float]:
    out = []
    for side, o, sgn, ff, fa in (("h", "a", 1.0, fg["home_pts"], fg["away_pts"]),
                                 ("a", "h", -1.0, fg["away_pts"], fg["home_pts"])):
        x = np.array([ff, fa, f[f"pp_{seg}_{side}"], f[f"pp_{seg}_{o}"], sgn * f["hf"]], float)
        out.append(ridge_apply(coef, x))
    return out[0], out[1]


def period_joint(nb: NeighbourPMF, mh: float, ma: float, tie_scale: float) -> np.ndarray:
    J = np.outer(nb.pmf(mh)[:70], nb.pmf(ma)[:70])
    if tie_scale != 1.0:
        d = np.diag_indices_from(J)
        J[d] *= tie_scale
    return _normalise(J / J.sum())


def _normalise(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), 0, None)
    return p / p.sum()


# ---------------------------------------------------------------- contract probabilities from a joint PMF
def _grid(J):
    return np.arange(J.shape[0])[:, None], np.arange(J.shape[1])[None, :]


def p_margin_gt(J, x: float) -> float:
    h, a = _grid(J)
    return float(J[(h - a) > x].sum())


def p_away_margin_gt(J, x: float) -> float:
    h, a = _grid(J)
    return float(J[(h - a) < -x].sum())


def p_total_gt(J, x: float) -> float:
    h, a = _grid(J)
    return float(J[(h + a) > x].sum())


def p_tie(J) -> float:
    h, a = _grid(J)
    return float(J[(h - a) == 0].sum())
