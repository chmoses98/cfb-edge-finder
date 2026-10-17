"""Coherent joint score distributions.

Team points, game total and margin are all read off ONE joint distribution of (home points, away
points), so P(total >= k), P(margin >= k) and each team's ladder can never contradict each other and
mean(total) == mean(home) + mean(away) exactly.

joint_empirical: standardised residual PAIRS from development games (z_h, z_a) = (y - mu)/sd(mu), which
keeps the empirical within-game correlation and the shapes of football scoring. For a new game:
   home_k = mu_h + sd(mu_h) z_h,k ;  away_k = mu_a + sd(mu_a) z_a,k   (k = 1..M, floored at 0, rounded)
bivariate_normal: the same with Gaussian (z_h, z_a) and the development correlation rho.
independent: rho = 0 (the comparator that ignores shared game script/weather/pace).
"""
from __future__ import annotations

import numpy as np

from pf_core import dists

TEAM_K = 101          # team points support 0..100
TOT_K = 161           # total 0..160
MAR_LO, MAR_K = -100, 201


class JointModel:
    def __init__(self, kind: str = "empirical", m: int = 2000, seed: int = 7):
        self.kind, self.m, self.seed = kind, m, seed

    def fit(self, mu_h, mu_a, y_h, y_a, n_bins: int = 8):
        mu = np.r_[mu_h, mu_a]
        y = np.r_[y_h, y_a]
        self.edges, self.sds = dists.fit_sd_binned(y, mu, n_bins=n_bins, min_n=300)
        zh = (np.asarray(y_h) - mu_h) / self._sd(mu_h)
        za = (np.asarray(y_a) - mu_a) / self._sd(mu_a)
        self.rho = float(np.corrcoef(zh, za)[0, 1])
        rng = np.random.default_rng(self.seed)
        if self.kind == "empirical":
            idx = rng.choice(len(zh), size=min(self.m, len(zh)), replace=False)
            self.Z = np.c_[zh[idx], za[idx]]
        else:
            rho = 0.0 if self.kind == "independent" else self.rho
            # deterministic, well-spread Gaussian pairs
            u = (np.arange(self.m) + 0.5) / self.m
            from scipy import stats
            z1 = stats.norm.ppf(u)
            z2 = stats.norm.ppf(rng.permutation(u))
            self.Z = np.c_[z1, rho * z1 + np.sqrt(1 - rho ** 2) * z2]
        return self

    def _sd(self, mu):
        return np.asarray(self.sds)[np.digitize(np.asarray(mu, float), np.asarray(self.edges))]

    def samples(self, mu_h, mu_a):
        mu_h, mu_a = np.asarray(mu_h, float), np.asarray(mu_a, float)
        H = np.maximum(np.rint(mu_h[:, None] + self._sd(mu_h)[:, None] * self.Z[None, :, 0]), 0)
        A = np.maximum(np.rint(mu_a[:, None] + self._sd(mu_a)[:, None] * self.Z[None, :, 1]), 0)
        return H.astype(int), A.astype(int)

    def distributions(self, mu_h, mu_a, chunk: int = 1500):
        """Returns DistBatch for home, away, total, margin (rows aligned with inputs). Flooring at 0
        lifts the distribution mean slightly above mu for very low projections (reported by the gate)."""
        outs = {k: [] for k in ("home", "away", "total", "margin")}
        for i in range(0, len(mu_h), chunk):
            H, A = self.samples(mu_h[i:i + chunk], mu_a[i:i + chunk])
            outs["home"].append(_pmf(H, 0, TEAM_K))
            outs["away"].append(_pmf(A, 0, TEAM_K))
            outs["total"].append(_pmf(H + A, 0, TOT_K))
            outs["margin"].append(_pmf(H - A, MAR_LO, MAR_K))
        lo = {"home": 0, "away": 0, "total": 0, "margin": MAR_LO}
        return {k: dists.DistBatch(lo[k], np.vstack(v)) for k, v in outs.items()}


def _pmf(S, lo, K):
    n, m = S.shape
    idx = np.clip(S - lo, 0, K - 1)
    P = np.zeros((n, K))
    np.add.at(P, (np.repeat(np.arange(n), m), idx.ravel()), 1.0)
    return (P + 1e-4) / m
