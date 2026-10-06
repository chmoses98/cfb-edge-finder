"""Calibration MEASUREMENT (mission sections 7, 8).

Distinct from `modeling/calibration.py`, which FITS a recalibration map
(Platt/isotonic). This module never fits anything -- it asks how well a
set of probabilities already matched observed outcomes.

*** WHY KALSHI PRICES ARE COMPARED BUT NOT CALLED PROBABILITIES ***
An executable Kalshi price is not a fair probability. It carries the
bid/ask spread, taker fees, and whatever the order book happens to look
like; YES and NO quotes on the same contract routinely sum to well over
1. Scoring it with Brier/log loss is still the right benchmark -- it is
the price you could actually have transacted at -- but every result
carries that caveat, and `MARKET_PRICE_CAVEAT` is emitted alongside any
comparison so it cannot be quoted without it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

MARKET_PRICE_CAVEAT = (
    "Executable Kalshi prices are not fair probabilities: they embed the bid/ask spread, taker fees and "
    "order-book microstructure (YES and NO quotes on one contract routinely sum to more than 1). They are "
    "compared here as a transactable market benchmark, not as a calibrated forecast."
)

LOG_LOSS_EPS = 1e-15
"""Clip bound for log loss. A probability of exactly 0 or 1 that turns
out wrong gives infinite loss, which would make one row dominate every
aggregate. Clipping is standard and is stated rather than hidden."""

DEFAULT_BINS: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


@dataclass(frozen=True)
class CalibrationBin:
    label: str
    lower: float
    upper: float
    count: int
    mean_predicted: float | None
    observed_rate: float | None
    calibration_error: float | None
    """observed_rate - mean_predicted. Positive means the forecaster was
    UNDER-confident in this bin (events happened more often than it said)."""


@dataclass(frozen=True)
class CalibrationReport:
    label: str
    n: int
    bins: tuple[CalibrationBin, ...]
    brier: float | None
    log_loss: float | None
    expected_calibration_error: float | None
    """Sample-weighted mean |observed - predicted| across non-empty bins
    (ECE). Bins with no observations contribute nothing rather than
    contributing a zero, which would dilute the error toward 0."""
    max_calibration_error: float | None
    caveats: tuple[str, ...] = field(default_factory=tuple)


def brier_score(predictions: list[float], outcomes: list[bool]) -> float | None:
    if not predictions:
        return None
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in zip(predictions, outcomes, strict=True)) / len(predictions)


def log_loss(predictions: list[float], outcomes: list[bool], *, eps: float = LOG_LOSS_EPS) -> float | None:
    """Clipped at `eps` so a confident-and-wrong boundary prediction
    cannot return infinity and swamp the aggregate."""
    if not predictions:
        return None
    total = 0.0
    for p, o in zip(predictions, outcomes, strict=True):
        clipped = min(max(p, eps), 1.0 - eps)
        total += -math.log(clipped) if o else -math.log(1.0 - clipped)
    return total / len(predictions)


def _bin_label(lower: float, upper: float) -> str:
    return f"{lower:.0%}-{upper:.0%}"


def build_calibration_report(
    *,
    label: str,
    predictions: list[float],
    outcomes: list[bool],
    bins: tuple[float, ...] = DEFAULT_BINS,
    caveats: tuple[str, ...] = (),
) -> CalibrationReport:
    """Binned calibration plus Brier, log loss and ECE.

    Empty bins are RETAINED in the output with `count=0` and `None`
    statistics. Dropping them would hide exactly the thing a reader needs
    to see -- that a probability range was never predicted at all."""
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must be the same length")

    bin_rows: list[CalibrationBin] = []
    weighted_error = 0.0
    max_error: float | None = None
    n = len(predictions)

    for i in range(len(bins) - 1):
        lower, upper = bins[i], bins[i + 1]
        is_last = i == len(bins) - 2
        # Upper edge inclusive only on the FINAL bin, so a prediction of
        # exactly 1.0 lands somewhere instead of being silently dropped.
        def _in_bin(p: float, lo: float = lower, hi: float = upper, last: bool = is_last) -> bool:
            return lo <= p <= hi if last else lo <= p < hi

        members = [(p, o) for p, o in zip(predictions, outcomes, strict=True) if _in_bin(p)]
        if members:
            mean_pred = sum(p for p, _ in members) / len(members)
            observed = sum(1 for _, o in members if o) / len(members)
            error = observed - mean_pred
            weighted_error += abs(error) * len(members)
            max_error = abs(error) if max_error is None else max(max_error, abs(error))
        else:
            mean_pred = observed = error = None
        bin_rows.append(
            CalibrationBin(
                label=_bin_label(lower, upper),
                lower=lower,
                upper=upper,
                count=len(members),
                mean_predicted=mean_pred,
                observed_rate=observed,
                calibration_error=error,
            )
        )

    return CalibrationReport(
        label=label,
        n=n,
        bins=tuple(bin_rows),
        brier=brier_score(predictions, outcomes),
        log_loss=log_loss(predictions, outcomes),
        expected_calibration_error=(weighted_error / n) if n else None,
        max_calibration_error=max_error,
        caveats=caveats,
    )


@dataclass(frozen=True)
class ModelMarketComparison:
    """Head-to-head on the SAME settled observations."""

    n: int
    model: CalibrationReport
    market: CalibrationReport
    brier_difference: float | None
    """model.brier - market.brier. NEGATIVE means the model scored better
    (Brier is a loss). Named as a plain difference rather than
    "model_advantage" so the sign has to be read, not assumed."""
    log_loss_difference: float | None
    caveats: tuple[str, ...]


def compare_model_to_market(
    *,
    model_probabilities: list[float],
    market_probabilities: list[float],
    outcomes: list[bool],
    bins: tuple[float, ...] = DEFAULT_BINS,
    extra_caveats: tuple[str, ...] = (),
) -> ModelMarketComparison:
    """Both forecasters scored on identical rows, so the comparison is
    paired by construction. No significance claim is made here -- see
    analytics/uncertainty.py for cluster-aware intervals."""
    model = build_calibration_report(label="model", predictions=model_probabilities, outcomes=outcomes, bins=bins)
    market = build_calibration_report(
        label="market_executable_price",
        predictions=market_probabilities,
        outcomes=outcomes,
        bins=bins,
        caveats=(MARKET_PRICE_CAVEAT,),
    )
    brier_diff = (
        None if model.brier is None or market.brier is None else model.brier - market.brier
    )
    ll_diff = None if model.log_loss is None or market.log_loss is None else model.log_loss - market.log_loss
    return ModelMarketComparison(
        n=len(outcomes),
        model=model,
        market=market,
        brier_difference=brier_diff,
        log_loss_difference=ll_diff,
        caveats=(MARKET_PRICE_CAVEAT, *extra_caveats),
    )
