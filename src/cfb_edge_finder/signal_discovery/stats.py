"""Small, dependency-free (numpy only) statistics for the signal lab. RESEARCH ONLY.

Everything is deterministic given the seed. Normal-approximation p-values are
two-sided. Bootstrap intervals resample GAMES (one row per game in every CFB
test, so the game is the cluster); `cluster_bootstrap` resamples whole
clusters (e.g. team-season) when a test has several rows per cluster.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

SEED = 20261008
N_BOOT = 2000


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def p_two_sided(z: float) -> float:
    return min(1.0, 2.0 * norm_sf(abs(z)))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return centre - half, centre + half


def mean_test(x: Sequence[float]) -> dict[str, float | int | None]:
    """Mean, SE, z and two-sided p for H0: mean = 0."""
    a = np.asarray([v for v in x if v is not None and not math.isnan(v)], dtype=float)
    n = len(a)
    if n < 2:
        return {"n": n, "mean": float(a.mean()) if n else None, "se": None, "z": None, "p": None}
    m, se = float(a.mean()), float(a.std(ddof=1) / math.sqrt(n))
    z = m / se if se > 0 else 0.0
    return {"n": n, "mean": m, "se": se, "z": z, "p": p_two_sided(z)}


def prop_test(k: int, n: int, p0: float) -> dict[str, float | int | None]:
    """One-sample proportion vs p0 (normal approximation)."""
    if n == 0:
        return {"n": 0, "rate": None, "z": None, "p": None}
    rate = k / n
    se = math.sqrt(p0 * (1 - p0) / n)
    z = (rate - p0) / se if se > 0 else 0.0
    return {"n": n, "rate": rate, "z": z, "p": p_two_sided(z)}


def bootstrap_mean_ci(
    x: Sequence[float], *, seed: int = SEED, n_boot: int = N_BOOT, alpha: float = 0.05
) -> tuple[float | None, float | None]:
    a = np.asarray(x, dtype=float)
    if len(a) < 5:
        return None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n_boot, len(a)))
    means = a[idx].mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def bootstrap_ratio_ci(
    num: Sequence[float], den: Sequence[float], *, seed: int = SEED, n_boot: int = N_BOOT
) -> tuple[float | None, float | None]:
    """CI of sum(num)/sum(den) (e.g. ROI = P/L / outlay), resampling rows jointly."""
    a, b = np.asarray(num, dtype=float), np.asarray(den, dtype=float)
    if len(a) < 5:
        return None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n_boot, len(a)))
    r = a[idx].sum(axis=1) / b[idx].sum(axis=1)
    return float(np.quantile(r, 0.025)), float(np.quantile(r, 0.975))


def cluster_bootstrap_mean_ci(
    x: Sequence[float], clusters: Sequence[str], *, seed: int = SEED, n_boot: int = N_BOOT
) -> tuple[float | None, float | None]:
    a = np.asarray(x, dtype=float)
    labels = np.asarray(clusters)
    uniq = np.unique(labels)
    if len(uniq) < 5:
        return None, None
    groups = [a[labels == u] for u in uniq]
    sums = np.array([g.sum() for g in groups])
    counts = np.array([len(g) for g in groups])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(uniq), size=(n_boot, len(uniq)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def ols(y: Sequence[float], X: np.ndarray, *, clusters: Sequence[str] | None = None) -> dict:
    """OLS with an intercept column already in X. HC1 or cluster-robust SEs."""
    yv = np.asarray(y, dtype=float)
    Xm = np.asarray(X, dtype=float)
    n, k = Xm.shape
    xtx_inv = np.linalg.pinv(Xm.T @ Xm)
    beta = xtx_inv @ Xm.T @ yv
    resid = yv - Xm @ beta
    if clusters is None:
        meat = (Xm * resid[:, None]).T @ (Xm * resid[:, None]) * n / max(n - k, 1)
    else:
        labels = np.asarray(clusters)
        meat = np.zeros((k, k))
        uniq = np.unique(labels)
        for u in uniq:
            m = labels == u
            s = Xm[m].T @ resid[m]
            meat += np.outer(s, s)
        g = len(uniq)
        meat *= (g / max(g - 1, 1)) * ((n - 1) / max(n - k, 1))
    cov = xtx_inv @ meat @ xtx_inv
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    z = np.divide(beta, se, out=np.zeros_like(beta), where=se > 0)
    ss_res = float(resid @ resid)
    ss_tot = float(((yv - yv.mean()) ** 2).sum())
    return {
        "beta": beta.tolist(),
        "se": se.tolist(),
        "z": z.tolist(),
        "p": [p_two_sided(float(v)) for v in z],
        "n": n,
        "r2": 1 - ss_res / ss_tot if ss_tot > 0 else None,
        "resid_sd": math.sqrt(ss_res / max(n - k, 1)),
    }


def logistic(y: Sequence[float], X: np.ndarray, *, l2: float = 0.0, iters: int = 50) -> dict:
    """Logistic regression by IRLS (optional ridge on non-intercept terms)."""
    yv = np.asarray(y, dtype=float)
    Xm = np.asarray(X, dtype=float)
    k = Xm.shape[1]
    beta = np.zeros(k)
    pen = np.eye(k) * l2
    pen[0, 0] = 0.0
    for _ in range(iters):
        eta = Xm @ beta
        p = 1 / (1 + np.exp(-eta))
        w = np.clip(p * (1 - p), 1e-9, None)
        grad = Xm.T @ (yv - p) - pen @ beta
        hess = (Xm * w[:, None]).T @ Xm + pen
        step = np.linalg.lstsq(hess, grad, rcond=None)[0]
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            break
    p = 1 / (1 + np.exp(-(Xm @ beta)))
    cov = np.linalg.pinv((Xm * np.clip(p * (1 - p), 1e-9, None)[:, None]).T @ Xm + pen)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    z = np.divide(beta, se, out=np.zeros_like(beta), where=se > 0)
    return {"beta": beta.tolist(), "se": se.tolist(), "z": z.tolist(), "p": [p_two_sided(float(v)) for v in z]}


def predict_logistic(beta: Sequence[float], X: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-(np.asarray(X, dtype=float) @ np.asarray(beta, dtype=float))))


def log_loss(y: Sequence[float], p: Sequence[float]) -> float:
    yv, pv = np.asarray(y, dtype=float), np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return float(-(yv * np.log(pv) + (1 - yv) * np.log(1 - pv)).mean())


def brier(y: Sequence[float], p: Sequence[float]) -> float:
    yv, pv = np.asarray(y, dtype=float), np.asarray(p, dtype=float)
    return float(((pv - yv) ** 2).mean())


def benjamini_hochberg(pvalues: Sequence[float | None]) -> list[float | None]:
    """BH q-values (step-up). None stays None and does not count as a hypothesis."""
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    m = len(idx)
    out: list[float | None] = [None] * len(pvalues)
    if m == 0:
        return out
    order = sorted(idx, key=lambda i: pvalues[i])  # type: ignore[arg-type,return-value]
    q_prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        q = min(q_prev, float(pvalues[i]) * m / rank)  # type: ignore[arg-type]
        out[i] = q
        q_prev = q
    return out


def holm(pvalues: Sequence[float | None]) -> list[float | None]:
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    m = len(idx)
    out: list[float | None] = [None] * len(pvalues)
    order = sorted(idx, key=lambda i: pvalues[i])  # type: ignore[arg-type,return-value]
    running = 0.0
    for rank, i in enumerate(order):
        adj = min(1.0, (m - rank) * float(pvalues[i]))  # type: ignore[arg-type]
        running = max(running, adj)
        out[i] = running
    return out


def quantiles(x: Sequence[float], qs: Sequence[float] = (0.1, 0.25, 0.5, 0.75, 0.9)) -> dict[str, float | None]:
    a = np.asarray(x, dtype=float)
    if len(a) == 0:
        return {f"p{int(q * 100)}": None for q in qs}
    return {f"p{int(q * 100)}": float(np.quantile(a, q)) for q in qs}
