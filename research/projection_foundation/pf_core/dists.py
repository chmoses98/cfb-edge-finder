"""Batched discrete outcome distributions.

Every projection in the Projection Foundation is a full probability distribution over an
integer outcome (receptions, yards, minutes rounded to the second-minute, shots...). A batch
stores one probability row per observation over a shared integer support [lo, lo + K).

Why discrete for everything: Kalshi ladders are integer thresholds ("8+ receptions",
"75+ yards"), CRPS / log score / PIT are exact on a lattice, and threshold probabilities are
coherent by construction (P(X>=k) is monotone because it comes from one pmf).
"""
from __future__ import annotations

import numpy as np
from scipy import stats

EPS = 1e-12


class DistBatch:
    """n distributions over integer support lo .. lo+K-1 (rows sum to 1)."""

    def __init__(self, lo: int, probs: np.ndarray):
        probs = np.asarray(probs, dtype=float)
        if probs.ndim != 2:
            raise ValueError("probs must be (n, K)")
        probs = np.clip(probs, 0.0, None)
        s = probs.sum(axis=1, keepdims=True)
        s[s <= 0] = 1.0
        self.lo = int(lo)
        self.P = probs / s
        self.support = np.arange(self.lo, self.lo + self.P.shape[1])

    # -- basic moments -------------------------------------------------
    def __len__(self) -> int:
        return self.P.shape[0]

    @property
    def mean(self) -> np.ndarray:
        return self.P @ self.support

    @property
    def var(self) -> np.ndarray:
        m = self.mean
        return self.P @ (self.support ** 2) - m ** 2

    @property
    def cdf_matrix(self) -> np.ndarray:
        return np.cumsum(self.P, axis=1)

    def quantile(self, q: float) -> np.ndarray:
        """Smallest x with F(x) >= q."""
        C = self.cdf_matrix
        idx = (C < q - 1e-12).sum(axis=1)
        idx = np.minimum(idx, self.P.shape[1] - 1)
        return self.support[idx]

    @property
    def median(self) -> np.ndarray:
        return self.quantile(0.5)

    def interval(self, level: float) -> tuple[np.ndarray, np.ndarray]:
        a = (1 - level) / 2
        return self.quantile(a), self.quantile(1 - a)

    def prob_ge(self, k) -> np.ndarray:
        """P(X >= k) for scalar k or per-row array k (Kalshi 'k+' semantics)."""
        k = np.broadcast_to(np.asarray(k, dtype=float), (len(self),))
        idx = np.ceil(k).astype(int) - self.lo
        C = self.cdf_matrix
        out = np.ones(len(self))
        below = idx - 1  # P(X >= k) = 1 - F(k-1)
        inside = (below >= 0) & (below < C.shape[1])
        out[inside] = 1.0 - C[np.arange(len(self))[inside], below[inside]]
        out[below >= C.shape[1]] = 0.0
        return np.clip(out, 0.0, 1.0)

    def _index(self, y) -> np.ndarray:
        y = np.asarray(y, dtype=float)
        return np.clip(np.round(y).astype(int) - self.lo, 0, self.P.shape[1] - 1)

    def logpmf(self, y) -> np.ndarray:
        idx = self._index(y)
        return np.log(np.maximum(self.P[np.arange(len(self)), idx], EPS))

    def crps(self, y) -> np.ndarray:
        """Discrete CRPS (= ranked probability score summed over the lattice)."""
        y = np.asarray(y, dtype=float)
        C = self.cdf_matrix
        H = (self.support[None, :] >= y[:, None]).astype(float)
        return ((C - H) ** 2).sum(axis=1)

    def pit(self, y, rng: np.random.Generator | None = None) -> np.ndarray:
        """Randomised PIT; uniform on [0,1] iff the forecast is calibrated."""
        rng = rng or np.random.default_rng(0)
        idx = self._index(y)
        C = self.cdf_matrix
        rows = np.arange(len(self))
        upper = C[rows, idx]
        lower = np.where(idx > 0, C[rows, np.maximum(idx - 1, 0)], 0.0)
        return lower + rng.uniform(size=len(self)) * (upper - lower)

    def subset(self, mask) -> "DistBatch":
        return DistBatch(self.lo, self.P[mask])

    def coherence_violations(self) -> np.ndarray:
        """Rows that are not valid distributions (NaN, negative, not summing to 1)."""
        bad = ~np.isfinite(self.P).all(axis=1)
        bad |= (self.P < -1e-9).any(axis=1)
        bad |= np.abs(self.P.sum(axis=1) - 1) > 1e-6
        return bad


# ---------------------------------------------------------------------------
# constructors
# ---------------------------------------------------------------------------

def _support_size(mu: np.ndarray, var: np.ndarray, floor: int = 10) -> int:
    hi = np.nanmax(mu + 8 * np.sqrt(np.maximum(var, 1e-9)) + 5) if len(mu) else floor
    return int(max(floor, min(hi, 5000)))


def poisson(mu, K: int | None = None) -> DistBatch:
    mu = np.maximum(np.asarray(mu, float), 1e-6)
    K = K or _support_size(mu, mu)
    x = np.arange(K)
    return DistBatch(0, stats.poisson.pmf(x[None, :], mu[:, None]))


def negbin(mu, alpha, K: int | None = None) -> DistBatch:
    """NB2: var = mu + alpha * mu^2. alpha -> 0 gives Poisson."""
    mu = np.maximum(np.asarray(mu, float), 1e-6)
    alpha = np.broadcast_to(np.asarray(alpha, float), mu.shape)
    pois = alpha < 1e-6
    a = np.maximum(alpha, 1e-6)
    n = 1.0 / a
    p = n / (n + mu)
    K = K or _support_size(mu, mu + a * mu ** 2)
    x = np.arange(K)
    P = stats.nbinom.pmf(x[None, :], n[:, None], p[:, None])
    if pois.any():
        P[pois] = stats.poisson.pmf(x[None, :], mu[pois][:, None])
    return DistBatch(0, P)


def hurdle(p_zero, base: DistBatch) -> DistBatch:
    """Point mass at 0 (e.g. did-not-play / zero opportunity) mixed with a base distribution."""
    p_zero = np.clip(np.asarray(p_zero, float), 0, 1)
    P = base.P * (1 - p_zero)[:, None]
    zi = 0 - base.lo
    if 0 <= zi < P.shape[1]:
        P[:, zi] += p_zero
    return DistBatch(base.lo, P)


def discretized_normal(mu, sd, lo: int, K: int, floor_at: int | None = None) -> DistBatch:
    """Normal on the integer lattice; mass below floor_at is piled on floor_at (e.g. yards can't be < -20)."""
    mu = np.asarray(mu, float)
    sd = np.maximum(np.asarray(sd, float), 1e-3)
    edges = np.arange(lo, lo + K + 1) - 0.5
    C = stats.norm.cdf((edges[None, :] - mu[:, None]) / sd[:, None])
    P = np.diff(C, axis=1)
    P[:, 0] += C[:, 0]
    P[:, -1] += 1 - C[:, -1]
    if floor_at is not None and floor_at > lo:
        j = floor_at - lo
        P[:, j] += P[:, :j].sum(axis=1)
        P[:, :j] = 0
    return DistBatch(lo, P)


def empirical_scale(mu, ratio_samples: np.ndarray, lo: int, K: int, additive: bool = False) -> DistBatch:
    """Nonparametric distribution: y = mu * r (or mu + r) with r drawn from residuals observed
    in training. ratio_samples is either a 1-D array shared by all rows or (n, m) per-row samples.

    This is quantile mapping: it inherits the true shape (skew, zero mass, long tails) of the
    outcome family instead of imposing a parametric family.
    """
    mu = np.asarray(mu, float)
    r = np.asarray(ratio_samples, float)
    if r.ndim == 1:
        r = np.broadcast_to(r, (len(mu), len(r)))
    vals = (mu[:, None] + r) if additive else (mu[:, None] * r)
    idx = np.clip(np.round(vals).astype(int) - lo, 0, K - 1)
    P = np.zeros((len(mu), K))
    rows = np.repeat(np.arange(len(mu)), idx.shape[1])
    np.add.at(P, (rows, idx.ravel()), 1.0)
    # light smoothing so unseen lattice points keep non-zero log score
    P = P + 1e-4
    return DistBatch(lo, P)


def mixture(weights, batches: list[DistBatch]) -> DistBatch:
    """Linear pool of distributions sharing lo and K."""
    w = np.asarray(weights, float)
    w = w / w.sum()
    lo = batches[0].lo
    K = max(b.P.shape[1] for b in batches)
    P = np.zeros((len(batches[0]), K))
    for wi, b in zip(w, batches):
        if b.lo != lo:
            raise ValueError("mixture needs a common lo")
        P[:, : b.P.shape[1]] += wi * b.P
    return DistBatch(lo, P)


# ---------------------------------------------------------------------------
# dispersion fitting
# ---------------------------------------------------------------------------

def fit_nb_alpha(y, mu) -> float:
    """Method-of-moments NB2 dispersion from out-of-sample residuals (0 => Poisson)."""
    y = np.asarray(y, float)
    mu = np.maximum(np.asarray(mu, float), 1e-6)
    num = ((y - mu) ** 2 - y).sum()
    den = (mu ** 2).sum()
    return float(max(0.0, num / den)) if den > 0 else 0.0


def fit_nb_alpha_binned(y, mu, n_bins: int = 6, min_n: int = 200):
    """Dispersion as a step function of the mean (low-volume players are often over-dispersed
    differently from workhorses). Returns (edges, alphas) usable with alpha_from_bins."""
    y = np.asarray(y, float)
    mu = np.asarray(mu, float)
    qs = np.unique(np.quantile(mu, np.linspace(0, 1, n_bins + 1)))
    edges = qs[1:-1]
    alphas = []
    b = np.digitize(mu, edges)
    glob = fit_nb_alpha(y, mu)
    for i in range(len(edges) + 1):
        m = b == i
        alphas.append(fit_nb_alpha(y[m], mu[m]) if m.sum() >= min_n else glob)
    return edges, np.array(alphas)


def alpha_from_bins(mu, edges, alphas) -> np.ndarray:
    return np.asarray(alphas)[np.digitize(np.asarray(mu, float), edges)]


def fit_sd_binned(y, mu, n_bins: int = 6, min_n: int = 200):
    """Residual SD as a step function of the mean, for discretized-normal outcomes."""
    y = np.asarray(y, float)
    mu = np.asarray(mu, float)
    qs = np.unique(np.quantile(mu, np.linspace(0, 1, n_bins + 1)))
    edges = qs[1:-1]
    b = np.digitize(mu, edges)
    glob = float(np.std(y - mu))
    sds = [float(np.std(y[b == i] - mu[b == i])) if (b == i).sum() >= min_n else glob
           for i in range(len(edges) + 1)]
    return edges, np.array(sds)
