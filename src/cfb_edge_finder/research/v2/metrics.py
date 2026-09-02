"""Evaluation metrics for the V2 tournament.

*** POINT METRICS ***
margin: MAE, RMSE, bias (projected - actual), favourite-tail bias (games
the model projects at >= 14 points, signed in the favourite's direction,
projected - actual), plus counts.
total: MAE, RMSE, bias.
winner: log loss, Brier, accuracy.

*** SYNTHETIC CONTRACT GRID ***
Outcome-independent ABSOLUTE half-point thresholds, identical to the
model-repair spec (docs/model_repair_2026_candidate_spec.json). For each
game and each threshold T the model reports P(home margin > T) [and the
mirror P(away margin > T)] and P(total > T). A realised outcome settles
each contract. Game-equal weighting: every game contributes total weight
1.0 split evenly across its contracts, so games with many contracts do
not outvote games with few.

*** CLUSTERED UNCERTAINTY ***
Paired per-game differences are bootstrapped over WHOLE GAMES (2,000
resamples by default) so 25 contracts from one game never pretend to be
25 independent observations.

Nothing here decides anything. Accept/reject rules live in the report.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SPREAD_THRESHOLDS = (0.5, 3.5, 6.5, 9.5, 13.5, 17.5, 21.5, 24.5, 27.5, 31.5, 35.5)
TOTAL_THRESHOLDS = (31.5, 34.5, 37.5, 40.5, 43.5, 46.5, 49.5, 52.5, 55.5, 58.5, 61.5, 64.5, 67.5, 70.5)
CALIBRATION_BINS = ((0.0, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80),
                    (0.80, 0.90), (0.90, 0.95), (0.95, 1.0001))
FAVOURITE_TAIL_MIN = 14.0
EPS = 1e-9


def _clip(p: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)


@dataclass(frozen=True)
class PointMetrics:
    n: int
    mae: float
    rmse: float
    bias: float
    fav_tail_n: int
    fav_tail_bias: float

    def to_dict(self) -> dict:
        return {"n": self.n, "mae": round(self.mae, 4), "rmse": round(self.rmse, 4), "bias": round(self.bias, 4),
                "fav_tail_n": self.fav_tail_n, "fav_tail_bias": round(self.fav_tail_bias, 4)}


def margin_metrics(pred: np.ndarray, actual: np.ndarray) -> PointMetrics:
    pred = np.asarray(pred, float)
    actual = np.asarray(actual, float)
    err = pred - actual
    tail = np.abs(pred) >= FAVOURITE_TAIL_MIN
    signed = np.sign(pred) * err  # >0 => model over-projects the favourite
    return PointMetrics(
        n=int(len(err)),
        mae=float(np.mean(np.abs(err))) if len(err) else float("nan"),
        rmse=float(np.sqrt(np.mean(err**2))) if len(err) else float("nan"),
        bias=float(np.mean(err)) if len(err) else float("nan"),
        fav_tail_n=int(tail.sum()),
        fav_tail_bias=float(np.mean(signed[tail])) if tail.any() else float("nan"),
    )


def total_metrics(pred: np.ndarray, actual: np.ndarray) -> PointMetrics:
    pred = np.asarray(pred, float)
    actual = np.asarray(actual, float)
    err = pred - actual
    return PointMetrics(
        n=int(len(err)),
        mae=float(np.mean(np.abs(err))) if len(err) else float("nan"),
        rmse=float(np.sqrt(np.mean(err**2))) if len(err) else float("nan"),
        bias=float(np.mean(err)) if len(err) else float("nan"),
        fav_tail_n=0,
        fav_tail_bias=float("nan"),
    )


@dataclass(frozen=True)
class WinnerMetrics:
    n: int
    log_loss: float
    brier: float
    accuracy: float

    def to_dict(self) -> dict:
        return {"n": self.n, "log_loss": round(self.log_loss, 5), "brier": round(self.brier, 5),
                "accuracy": round(self.accuracy, 4)}


def winner_metrics(p_home: np.ndarray, home_won: np.ndarray) -> WinnerMetrics:
    p = _clip(p_home)
    y = np.asarray(home_won, float)
    if len(p) == 0:
        return WinnerMetrics(0, float("nan"), float("nan"), float("nan"))
    ll = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
    brier = np.mean((p - y) ** 2)
    acc = np.mean((p > 0.5) == (y > 0.5))
    return WinnerMetrics(int(len(p)), float(ll), float(brier), float(acc))


# ---------------------------------------------------------------------------
# Synthetic contract grid
# ---------------------------------------------------------------------------


@dataclass
class ContractSet:
    """Flattened synthetic contracts for a set of games.

    prob: model probability the contract's YES side settles yes
    hit:  realised 0/1
    game_idx: index of the game each contract belongs to (for clustering)
    weight: game-equal weight (1 / contracts-in-that-game)
    family: 'spread' or 'total'
    """

    prob: np.ndarray
    hit: np.ndarray
    game_idx: np.ndarray
    weight: np.ndarray
    family: np.ndarray

    def subset(self, mask: np.ndarray) -> ContractSet:
        return ContractSet(self.prob[mask], self.hit[mask], self.game_idx[mask], self.weight[mask], self.family[mask])

    @property
    def n_contracts(self) -> int:
        return int(len(self.prob))

    @property
    def n_games(self) -> int:
        return int(len(np.unique(self.game_idx))) if len(self.game_idx) else 0


def build_contracts(
    *,
    spread_prob_fn,
    total_prob_fn,
    actual_margin: np.ndarray,
    actual_total: np.ndarray,
    spread_thresholds=SPREAD_THRESHOLDS,
    total_thresholds=TOTAL_THRESHOLDS,
    include_totals: bool = True,
    include_spreads: bool = True,
) -> ContractSet:
    """`spread_prob_fn(T)` must return an array (n_games,) of P(home margin > T)
    for signed T (negative T means "home margin > -|T|", i.e. the away side
    contract's complement is used for the mirror). `total_prob_fn(T)` returns
    P(total > T)."""
    actual_margin = np.asarray(actual_margin, float)
    actual_total = np.asarray(actual_total, float)
    n = len(actual_margin)
    probs, hits, gidx, fam = [], [], [], []
    idx = np.arange(n)

    def _vec(x) -> np.ndarray:
        return np.broadcast_to(np.asarray(x, float), (n,))
    if include_spreads:
        for T in spread_thresholds:
            # home covers T
            probs.append(_vec(spread_prob_fn(T)))
            hits.append((actual_margin > T).astype(float))
            gidx.append(idx)
            fam.append(np.full(n, "spread"))
            # away covers T == home margin < -T == 1 - P(home margin > -T) (no ties at half points)
            probs.append(1.0 - _vec(spread_prob_fn(-T)))
            hits.append((actual_margin < -T).astype(float))
            gidx.append(idx)
            fam.append(np.full(n, "spread"))
    if include_totals:
        for T in total_thresholds:
            probs.append(_vec(total_prob_fn(T)))
            hits.append((actual_total > T).astype(float))
            gidx.append(idx)
            fam.append(np.full(n, "total"))
    prob = np.concatenate(probs)
    hit = np.concatenate(hits)
    g = np.concatenate(gidx)
    f = np.concatenate(fam)
    counts = np.bincount(g, minlength=n)
    weight = 1.0 / counts[g]
    return ContractSet(_clip(prob), hit, g, weight, f)


@dataclass(frozen=True)
class CalibrationSummary:
    n_contracts: int
    n_games: int
    brier: float
    log_loss: float
    ece: float
    bins: list[dict]
    hit_rate_90_95: float | None
    hit_rate_95_plus: float | None

    def to_dict(self) -> dict:
        return {
            "n_contracts": self.n_contracts, "n_games": self.n_games, "brier": round(self.brier, 5),
            "log_loss": round(self.log_loss, 5), "ece": round(self.ece, 5), "bins": self.bins,
            "hit_rate_90_95": None if self.hit_rate_90_95 is None else round(self.hit_rate_90_95, 4),
            "hit_rate_95_plus": None if self.hit_rate_95_plus is None else round(self.hit_rate_95_plus, 4),
        }


def calibration_summary(cs: ContractSet) -> CalibrationSummary:
    """Game-equal weighted Brier, log loss, ECE and reliability bins."""
    if cs.n_contracts == 0:
        return CalibrationSummary(0, 0, float("nan"), float("nan"), float("nan"), [], None, None)
    p, y, w = cs.prob, cs.hit, cs.weight
    wsum = w.sum()
    brier = float(np.sum(w * (p - y) ** 2) / wsum)
    ll = float(-np.sum(w * (y * np.log(p) + (1 - y) * np.log(1 - p))) / wsum)
    bins = []
    ece = 0.0
    for lo, hi in CALIBRATION_BINS:
        m = (p >= lo) & (p < hi)
        if not m.any():
            bins.append(
                {"bin": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": 0, "predicted": None, "observed": None, "gap": None}
            )
            continue
        bw = w[m].sum()
        pm = float(np.sum(w[m] * p[m]) / bw)
        om = float(np.sum(w[m] * y[m]) / bw)
        ece += (bw / wsum) * abs(pm - om)
        bins.append({"bin": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": int(m.sum()), "predicted": round(pm, 4),
                     "observed": round(om, 4), "gap": round(om - pm, 4)})
    m9 = (p >= 0.90) & (p < 0.95)
    m95 = p >= 0.95
    return CalibrationSummary(
        cs.n_contracts, cs.n_games, brier, ll, float(ece), bins,
        float(np.sum(w[m9] * y[m9]) / w[m9].sum()) if m9.any() else None,
        float(np.sum(w[m95] * y[m95]) / w[m95].sum()) if m95.any() else None,
    )


# ---------------------------------------------------------------------------
# Clustered bootstrap for paired comparisons
# ---------------------------------------------------------------------------


def cluster_bootstrap_mean(values: np.ndarray, clusters: np.ndarray, weights: np.ndarray | None = None,
                           n_boot: int = 2000, seed: int = 20260902) -> tuple[float, float, float]:
    """Weighted mean of `values` with a 95% interval from resampling whole
    clusters (games) with replacement. Returns (point, lo, hi)."""
    values = np.asarray(values, float)
    clusters = np.asarray(clusters)
    if weights is None:
        weights = np.ones_like(values)
    uniq, inv = np.unique(clusters, return_inverse=True)
    k = len(uniq)
    if k == 0:
        return float("nan"), float("nan"), float("nan")
    num = np.bincount(inv, weights=values * weights, minlength=k)
    den = np.bincount(inv, weights=weights, minlength=k)
    point = float(num.sum() / den.sum())
    if k < 5:
        return point, float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, k, size=(n_boot, k))
    boots = num[draws].sum(axis=1) / den[draws].sum(axis=1)
    return point, float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def paired_delta(metric_a: np.ndarray, metric_b: np.ndarray, clusters: np.ndarray,
                 weights: np.ndarray | None = None, n_boot: int = 2000) -> dict:
    """Per-observation metric of B minus A (negative = B better for losses),
    clustered over games."""
    point, lo, hi = cluster_bootstrap_mean(np.asarray(metric_b) - np.asarray(metric_a), clusters, weights, n_boot)
    verdict = "flat"
    if not np.isnan(lo):
        if hi < 0:
            verdict = "improves"
        elif lo > 0:
            verdict = "degrades"
    return {"delta": round(point, 5), "ci95": [round(lo, 5), round(hi, 5)], "verdict": verdict}


def interval_coverage(pred: np.ndarray, actual: np.ndarray, sd: np.ndarray, z: float = 1.6449) -> float:
    """Fraction of actuals within pred +/- z*sd (z=1.6449 -> nominal 90%)."""
    pred, actual, sd = np.asarray(pred, float), np.asarray(actual, float), np.asarray(sd, float)
    if len(pred) == 0:
        return float("nan")
    return float(np.mean(np.abs(actual - pred) <= z * sd))
