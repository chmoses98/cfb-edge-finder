"""Out-of-sample evaluation for preseason-prior candidates.

*** INCREMENTAL OUT-OF-SAMPLE VALUE IS THE ONLY CRITERION ***

Not in-sample fit. Every metric here is computed on games the candidate
did not see, and every candidate is reported against the SAME control on
the SAME games -- paired, so the comparison is not contaminated by one
arm happening to draw an easier slate.

*** WHY GAME-CLUSTERED, PAIRED DIFFERENCES ***

Two contracts on one game share one football outcome, and control and
candidate see identical games. The quantity of interest is therefore the
PAIRED difference per game, and its uncertainty comes from variation
across games. Reporting two independent standard errors and eyeballing
the overlap would be both wrong and systematically over-conservative.

*** NOTHING HERE DECIDES ANYTHING ***

These functions compute numbers. Accept/reject is a separate, explicit
step in `ablation.py` that applies a rule fixed before results are seen.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

EPS = 1e-12


@dataclass(frozen=True)
class GamePrediction:
    """One model's prediction for one game, plus the realised outcome."""

    game_id: str
    season: int
    week: int
    home_win_probability: float
    projected_margin: float
    projected_total: float
    actual_home_margin: int
    actual_total: int
    is_neutral_site: bool = False
    both_fbs: bool = True

    @property
    def home_won(self) -> bool:
        """Settlement assigns a zero margin to AWAY, matching
        research/settlement.py. Restating that rule differently here
        would make evaluation disagree with the ledger."""
        return self.actual_home_margin > 0


@dataclass(frozen=True)
class WinnerMetrics:
    n: int
    log_loss: float
    brier: float
    calibration_bins: tuple[tuple[str, int, float, float], ...]
    """(bin label, n, mean predicted, observed rate)."""


@dataclass(frozen=True)
class MarginMetrics:
    n: int
    mae: float
    rmse: float
    bias: float
    favorite_tail_bias: float
    """Mean signed error on games the model projected by 14+ points --
    where a margin model most often fails, and where the control's own
    diagnostics already look."""


@dataclass(frozen=True)
class TotalMetrics:
    n: int
    mae: float
    rmse: float
    bias: float


CALIBRATION_EDGES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
FAVORITE_TAIL_MARGIN = 14.0


def winner_metrics(predictions: list[GamePrediction]) -> WinnerMetrics:
    if not predictions:
        return WinnerMetrics(0, float("nan"), float("nan"), ())
    ll = 0.0
    brier = 0.0
    for p in predictions:
        prob = min(max(p.home_win_probability, EPS), 1 - EPS)
        outcome = 1.0 if p.home_won else 0.0
        ll -= outcome * math.log(prob) + (1 - outcome) * math.log(1 - prob)
        brier += (prob - outcome) ** 2
    n = len(predictions)

    bins: list[tuple[str, int, float, float]] = []
    for low, high in zip(CALIBRATION_EDGES, CALIBRATION_EDGES[1:], strict=False):
        bucket = [p for p in predictions if low <= p.home_win_probability < high]
        if not bucket:
            continue
        bins.append((
            f"[{low:.1f},{high:.1f})",
            len(bucket),
            statistics.fmean(p.home_win_probability for p in bucket),
            statistics.fmean(1.0 if p.home_won else 0.0 for p in bucket),
        ))
    return WinnerMetrics(n, ll / n, brier / n, tuple(bins))


def margin_metrics(predictions: list[GamePrediction]) -> MarginMetrics:
    if not predictions:
        return MarginMetrics(0, float("nan"), float("nan"), float("nan"), float("nan"))
    errors = [p.projected_margin - p.actual_home_margin for p in predictions]
    tail = [
        p.projected_margin - p.actual_home_margin
        for p in predictions
        if abs(p.projected_margin) >= FAVORITE_TAIL_MARGIN
    ]
    return MarginMetrics(
        n=len(predictions),
        mae=statistics.fmean(abs(e) for e in errors),
        rmse=math.sqrt(statistics.fmean(e * e for e in errors)),
        bias=statistics.fmean(errors),
        favorite_tail_bias=statistics.fmean(tail) if tail else float("nan"),
    )


def total_metrics(predictions: list[GamePrediction]) -> TotalMetrics:
    if not predictions:
        return TotalMetrics(0, float("nan"), float("nan"), float("nan"))
    errors = [p.projected_total - p.actual_total for p in predictions]
    return TotalMetrics(
        n=len(predictions),
        mae=statistics.fmean(abs(e) for e in errors),
        rmse=math.sqrt(statistics.fmean(e * e for e in errors)),
        bias=statistics.fmean(errors),
    )


def interval_coverage(
    predictions: list[GamePrediction], lower: list[float], upper: list[float]
) -> float:
    """Share of realised margins inside the model's stated interval.

    A model whose point estimate is mediocre but whose interval is honest
    is more useful here than the reverse, because the control's stated
    contribution in Week 1 is uncertainty widening."""
    if not predictions:
        return float("nan")
    inside = sum(
        1 for p, lo, hi in zip(predictions, lower, upper, strict=True)
        if lo <= p.actual_home_margin <= hi
    )
    return inside / len(predictions)


@dataclass(frozen=True)
class PairedComparison:
    """Control vs candidate on the SAME games."""

    n_games: int
    metric: str
    control: float
    candidate: float
    mean_paired_difference: float
    """candidate - control. Negative is an improvement for error metrics."""
    cluster_se: float | None
    ci_low: float | None
    ci_high: float | None

    @property
    def improves(self) -> bool:
        """Improvement means the paired interval excludes zero on the
        favourable side. A point estimate that merely leans the right way
        is not evidence."""
        return self.ci_high is not None and self.ci_high < 0

    @property
    def degrades(self) -> bool:
        return self.ci_low is not None and self.ci_low > 0


def paired_comparison(
    *, metric: str, control_errors: list[float], candidate_errors: list[float]
) -> PairedComparison:
    """Paired per-game difference with a normal-approximation interval.

    Requires identical length and ordering: the pairing is the whole
    point, and silently zipping mismatched lists would compare different
    games to each other."""
    if len(control_errors) != len(candidate_errors):
        raise ValueError(
            f"paired comparison needs identical games: got {len(control_errors)} control "
            f"and {len(candidate_errors)} candidate errors"
        )
    n = len(control_errors)
    if n == 0:
        return PairedComparison(0, metric, float("nan"), float("nan"), float("nan"), None, None, None)
    diffs = [c - k for k, c in zip(control_errors, candidate_errors, strict=True)]
    mean_diff = statistics.fmean(diffs)
    if n < 2:
        return PairedComparison(
            n, metric, statistics.fmean(control_errors), statistics.fmean(candidate_errors),
            mean_diff, None, None, None,
        )
    se = statistics.stdev(diffs) / math.sqrt(n)
    return PairedComparison(
        n_games=n,
        metric=metric,
        control=statistics.fmean(control_errors),
        candidate=statistics.fmean(candidate_errors),
        mean_paired_difference=mean_diff,
        cluster_se=se,
        ci_low=mean_diff - 1.96 * se,
        ci_high=mean_diff + 1.96 * se,
    )


def naive_baseline_probability(is_neutral_site: bool) -> float:
    """The floor any model must clear: home teams win ~0.57 of FBS games,
    and a neutral site is a coin flip. Stated as a reference point, not
    fitted here -- a baseline tuned on the evaluation data would not be a
    baseline."""
    return 0.5 if is_neutral_site else 0.57
