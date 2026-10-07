"""Small, dependency-free statistics for the historical study. PURE."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def wilson(hits: int, n: int, z: float = 1.959964) -> list[float] | None:
    """Wilson score 95% interval for a proportion."""
    if n <= 0:
        return None
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def rate(hits: int, n: int) -> dict[str, object]:
    return {"n": n, "hits": hits, "rate": round(hits / n, 4) if n else None, "ci95": wilson(hits, n)}


def _gammq(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) (Numerical Recipes gser/gcf)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1:
        ap, total, delta = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-12:
                break
        return 1.0 - total * math.exp(-x + a * math.log(x) - gln)
    b = x + 1 - a
    c, d = 1.0 / 1e-300, 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-12:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def chi2_sf(x: float, df: int) -> float:
    return _gammq(df / 2.0, x / 2.0) if df > 0 else 1.0


def homogeneity(groups: Sequence[tuple[int, int]]) -> dict[str, object]:
    """Chi-square test that several (hits, n) proportions are equal."""
    groups = [(h, n) for h, n in groups if n > 0]
    if len(groups) < 2:
        return {"chi2": None, "df": 0, "p": None}
    hits, total = sum(h for h, _ in groups), sum(n for _, n in groups)
    p = hits / total
    if p in (0.0, 1.0):
        return {"chi2": 0.0, "df": len(groups) - 1, "p": 1.0}
    chi2 = sum((h - n * p) ** 2 / (n * p * (1 - p)) for h, n in groups)
    df = len(groups) - 1
    return {"chi2": round(chi2, 3), "df": df, "p": round(chi2_sf(chi2, df), 4)}


def quantiles(values: Sequence[float]) -> dict[str, object]:
    if not values:
        return {"n": 0}
    a = np.asarray(values, dtype=float)
    q = np.percentile(a, [10, 25, 50, 75, 90])
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 2),
        "sd": round(float(a.std(ddof=1)), 2) if a.size > 1 else None,
        "p10": round(float(q[0]), 1),
        "p25": round(float(q[1]), 1),
        "median": round(float(q[2]), 1),
        "p75": round(float(q[3]), 1),
        "p90": round(float(q[4]), 1),
    }


def entropy(counts: dict[str, int]) -> dict[str, object]:
    total = sum(counts.values())
    if not total:
        return {"n": 0}
    ps = [c / total for c in counts.values() if c]
    h = -sum(p * math.log2(p) for p in ps)
    return {
        "n": total,
        "entropy_bits": round(h, 3),
        "max_share": round(max(ps), 4),
        "classes": len(ps),
    }


def bootstrap_lift(
    flagged: np.ndarray, hit: np.ndarray, base_hit: np.ndarray, resamples: int, seed: int
) -> list[float] | None:
    """95% percentile interval of PPV / base, resampling games.

    `flagged[i]` = number of scripts of the archetype in game i, `hit[i]` = how
    many of them realized, `base_hit[i]` = the base-rate indicator of game i."""
    if flagged.sum() == 0 or base_hit.mean() == 0:
        return None
    rng = np.random.default_rng(seed)
    n = flagged.size
    idx = rng.integers(0, n, size=(resamples, n))
    f, h, b = flagged[idx].sum(axis=1), hit[idx].sum(axis=1), base_hit[idx].mean(axis=1)
    ok = (f > 0) & (b > 0)
    lifts = (h[ok] / f[ok]) / b[ok]
    if lifts.size < resamples * 0.9:
        return None
    lo, hi = np.percentile(lifts, [2.5, 97.5])
    return [round(float(lo), 3), round(float(hi), 3)]


def kmeans(x: np.ndarray, k: int, seed: int, iters: int = 200) -> tuple[np.ndarray, np.ndarray, float]:
    """k-means++ / Lloyd. Returns (labels, centroids, inertia). Deterministic for a seed."""
    rng = np.random.default_rng(seed)
    n = x.shape[0]
    centroids = [x[rng.integers(0, n)]]
    for _ in range(1, k):
        d2 = np.min(((x[:, None, :] - np.asarray(centroids)[None, :, :]) ** 2).sum(axis=2), axis=1)
        centroids.append(x[rng.choice(n, p=d2 / d2.sum())])
    c = np.asarray(centroids)
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        dist = ((x[:, None, :] - c[None, :, :]) ** 2).sum(axis=2)
        new = dist.argmin(axis=1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for j in range(k):
            if np.any(labels == j):
                c[j] = x[labels == j].mean(axis=0)
    inertia = float(((x - c[labels]) ** 2).sum())
    return labels, c, inertia
