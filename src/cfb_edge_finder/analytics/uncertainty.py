"""Cluster-aware uncertainty (mission sections 16, 17, 18).

*** WHY NAIVE STANDARD ERRORS WOULD BE WRONG HERE ***
Two kinds of dependence make contract-level rows far from independent:

1. **Game clustering.** One CFB game produces a moneyline pair plus a
   full spread ladder plus a full total ladder -- 30+ contracts whose
   outcomes are driven by the same final score. If the favourite covers
   by three touchdowns, most spread rungs on that game resolve together.

2. **Checkpoint clustering.** The same contract is captured at
   EARLY_OPEN, T_7D, T_3D, T_24H, T_6H, T_90, T_60, T_30 and CLOSING.
   Those nine rows share one outcome entirely.

Treating those as ~270 independent observations would shrink a
confidence interval by roughly sqrt(270/1) against the truth, which is
how a research system talks itself into a signal that is one game.

*** THE METHOD ***
Nonparametric bootstrap resampling WHOLE CLUSTERS with replacement (the
"cluster bootstrap"). Resampling games keeps every contract and every
checkpoint of a sampled game together, so both dependence structures
above are preserved without having to model either. Deliberately chosen
over an analytic cluster-robust variance because it needs no
distributional assumption and degrades gracefully at the small game
counts this corpus will have for most of a season.

`game_id` is the default cluster because it is the coarsest -- and
therefore most conservative -- unit. Clustering on market ticker instead
would handle checkpoint dependence but still treat 30 contracts on one
game as independent.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

DEFAULT_BOOTSTRAP_ITERATIONS = 2000
MIN_CLUSTERS_FOR_INTERVAL = 5
"""Below this many distinct clusters a bootstrap interval is not
meaningful -- resampling 3 games mostly reproduces the same 3 games. The
point estimate is still reported; the interval is withheld with a reason
rather than printed at false precision."""


@dataclass(frozen=True)
class ClusteredEstimate:
    """A point estimate with, where possible, a cluster bootstrap
    interval. `interval_available=False` is a first-class outcome, not an
    error."""

    point_estimate: float | None
    n_observations: int
    n_clusters: int
    interval_available: bool
    reason: str
    lower: float | None = None
    upper: float | None = None
    confidence: float = 0.95
    method: str = "game-cluster nonparametric bootstrap"
    iterations: int = 0


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def cluster_bootstrap_mean(
    values: Sequence[float],
    cluster_ids: Sequence[str],
    *,
    confidence: float = 0.95,
    iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = 0,
    min_clusters: int = MIN_CLUSTERS_FOR_INTERVAL,
    statistic: Callable[[Sequence[float]], float | None] = _mean,
) -> ClusteredEstimate:
    """Bootstrap the mean (or any `statistic`) by resampling clusters.

    `seed` is fixed by default so a report is reproducible: the same
    corpus must produce the same interval twice, or the number is not
    quotable."""
    if len(values) != len(cluster_ids):
        raise ValueError("values and cluster_ids must be the same length")

    point = statistic(values)
    by_cluster: dict[str, list[float]] = {}
    for value, cluster in zip(values, cluster_ids, strict=True):
        by_cluster.setdefault(cluster, []).append(value)
    n_clusters = len(by_cluster)

    if point is None:
        return ClusteredEstimate(None, 0, 0, False, "no observations")
    if n_clusters < min_clusters:
        return ClusteredEstimate(
            point, len(values), n_clusters, False,
            f"only {n_clusters} distinct cluster(s); need >= {min_clusters} for a meaningful interval",
        )

    clusters = list(by_cluster.values())
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        drawn: list[float] = []
        for _ in range(n_clusters):
            drawn.extend(clusters[rng.randrange(n_clusters)])
        stat = statistic(drawn)
        if stat is not None:
            samples.append(stat)

    if not samples:
        return ClusteredEstimate(point, len(values), n_clusters, False, "bootstrap produced no usable samples")

    samples.sort()
    alpha = (1.0 - confidence) / 2.0
    lo_i = max(0, int(alpha * len(samples)) - 1)
    hi_i = min(len(samples) - 1, int((1.0 - alpha) * len(samples)))
    return ClusteredEstimate(
        point_estimate=point,
        n_observations=len(values),
        n_clusters=n_clusters,
        interval_available=True,
        reason="ok",
        lower=samples[lo_i],
        upper=samples[hi_i],
        confidence=confidence,
        iterations=len(samples),
    )


def cluster_bootstrap_rate(
    successes: Sequence[bool],
    cluster_ids: Sequence[str],
    **kwargs,
) -> ClusteredEstimate:
    """Same machinery for a hit rate -- a mean over 0/1."""
    return cluster_bootstrap_mean([1.0 if s else 0.0 for s in successes], cluster_ids, **kwargs)


# --- Sample-size labelling (mission section 19) --------------------------

LOW_SAMPLE_THRESHOLD = 20
CAUTION_SAMPLE_THRESHOLD = 50


@dataclass(frozen=True)
class SampleConfidence:
    label: str
    detail: str


def sample_confidence(
    n: int, n_clusters: int | None = None, *, low: int = LOW_SAMPLE_THRESHOLD, caution: int = CAUTION_SAMPLE_THRESHOLD
) -> SampleConfidence:
    """Labels a slice's sample. Never suppresses the row -- the data is
    always shown, with the confidence attached (mission section 19).

    Cluster count is checked ALONGSIDE row count because 300 contracts
    from 2 games is a small sample wearing a large sample's clothes."""
    if n == 0:
        return SampleConfidence("NO_SAMPLE", "no observations in this slice")
    parts = []
    label = "OK"
    if n < low:
        label = "LOW_SAMPLE"
        parts.append(f"n={n} < {low}")
    elif n < caution:
        label = "CAUTION"
        parts.append(f"n={n} < {caution}")
    if n_clusters is not None and n_clusters < MIN_CLUSTERS_FOR_INTERVAL:
        label = "LOW_SAMPLE"
        parts.append(f"only {n_clusters} distinct game cluster(s)")
    return SampleConfidence(label, "; ".join(parts) if parts else f"n={n}")
