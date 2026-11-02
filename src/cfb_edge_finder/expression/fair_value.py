"""Fee-inclusive break-even, calibrated fair probability with uncertainty, and bet-up-to.

*** WHY THIS EXISTS (postmortem 2026-11-02, issue #248) ***
``expression/economics.py`` prices ONE contract: its fee is rounded UP to a whole cent
(1 contract at 50c -> 2c, a 4% break-even tax). Kalshi rounds up once per ORDER, so the
fee a real 150-contract order pays is ~1.75c per contract at 50c. Using the one-contract
fee overstates break-even at size; ignoring rounding understates it at one contract.
This module computes the per-order fee, so the break-even matches the money that moved.

*** WHAT IT REFUSES TO DO ***
* It never turns a raw model probability into a "fair" probability by itself. A fair
  probability exists only when a reliability table (calibration bins built from SETTLED,
  out-of-sample outcomes) supports the bin the raw probability falls in. Otherwise the
  estimate is ``UNCALIBRATED_*`` with no point value, and bet-up-to is ``None``.
* Bet-up-to uses the LOWER bound of the interval, not the point estimate: a price is
  only acceptable if it is still +EV at the pessimistic end of what the calibration
  evidence supports.
* No edge, no bet-up-to, no break-even is fabricated when an input is missing.

Pure functions; no I/O. Restriction-only: nothing here can create a recommendation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum

from cfb_edge_finder.kalshi.fee_schedule import (
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    FeeScheduleVersion,
    get_taker_multiplier,
)

#: Owner-adjustable defaults (policy, not physics).
DEFAULT_CONFIDENCE_LEVEL = 0.80
#: A reliability bin with fewer settled observations than this gives no calibrated estimate.
DEFAULT_MIN_BIN_OBSERVATIONS = 30
#: Pseudo-observations of the raw model probability blended into a bin's hit rate. Stops a
#: lucky 31-observation bin from being read as truth. Documented, adjustable.
DEFAULT_PRIOR_STRENGTH = 20.0
#: Required lower-bound margin over fee-inclusive break-even. 0.0 = merely +EV at the lower bound.
DEFAULT_REQUIRED_MARGIN = 0.0

_Z = {0.80: 1.2815515655, 0.90: 1.6448536270, 0.95: 1.9599639845}


def order_fee_dollars(
    price: float, contracts: float, series_ticker: str | None, *, schedule: FeeScheduleVersion | None = None
) -> float | None:
    """Taker fee for ONE order: ``ceil_to_cent(M * rate * C * P * (1-P))``. None if unpriceable.

    ``contracts`` may be fractional (the ledger carries fixed-point fills). None -- never 0 --
    outside (0, 1) or for non-positive size, because an unknown fee must not read as free.
    """
    if not (0.0 < price < 1.0) or not (contracts and contracts > 0):
        return None
    sched = schedule or KALSHI_FEE_SCHEDULE_2026_07_07_TAKER
    multiplier, _ = get_taker_multiplier(series_ticker or "")
    p = Decimal(str(price))
    raw_cents = multiplier * sched.rate * Decimal(str(contracts)) * p * (Decimal(1) - p) * 100
    return float(raw_cents.to_integral_value(rounding=ROUND_CEILING) / 100)


def fee_inclusive_break_even(
    price: float, contracts: float, series_ticker: str | None, *, schedule: FeeScheduleVersion | None = None
) -> float | None:
    """Probability at which buying ``contracts`` at ``price`` has zero expected value: price + fee/contract."""
    fee = order_fee_dollars(price, contracts, series_ticker, schedule=schedule)
    return None if fee is None else price + fee / contracts


class CalibrationStatus(StrEnum):
    CALIBRATED = "CALIBRATED"
    UNCALIBRATED_NO_RELIABILITY_TABLE = "UNCALIBRATED_NO_RELIABILITY_TABLE"
    UNCALIBRATED_NO_RAW_PROBABILITY = "UNCALIBRATED_NO_RAW_PROBABILITY"
    UNCALIBRATED_SPARSE_BIN = "UNCALIBRATED_SPARSE_BIN"


@dataclass(frozen=True)
class ReliabilityBin:
    """Settled, out-of-sample outcomes for model probabilities in [lo, hi)."""

    lo: float
    hi: float
    observations: int
    hits: int


@dataclass(frozen=True)
class ProbabilityEstimate:
    status: CalibrationStatus
    point: float | None
    lower: float | None
    upper: float | None
    confidence_level: float
    bin_observations: int
    method: str = "bin_hit_rate_shrunk_to_raw_wilson"

    @property
    def usable(self) -> bool:
        return self.status is CalibrationStatus.CALIBRATED and self.lower is not None


def _wilson(successes: float, n: float, z: float) -> tuple[float, float]:
    phat = successes / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def calibrated_probability(
    raw_probability: float | None,
    bins: list[ReliabilityBin] | None,
    *,
    min_observations: int = DEFAULT_MIN_BIN_OBSERVATIONS,
    prior_strength: float = DEFAULT_PRIOR_STRENGTH,
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
) -> ProbabilityEstimate:
    """Map a raw model probability to a calibrated estimate with a Wilson interval, or refuse."""
    z = _Z.get(confidence_level)
    if z is None:
        raise ValueError(f"confidence_level must be one of {sorted(_Z)}")

    def refuse(status: CalibrationStatus, n: int = 0) -> ProbabilityEstimate:
        return ProbabilityEstimate(status, None, None, None, confidence_level, n)

    if raw_probability is None or not (0.0 <= raw_probability <= 1.0):
        return refuse(CalibrationStatus.UNCALIBRATED_NO_RAW_PROBABILITY)
    if not bins:
        return refuse(CalibrationStatus.UNCALIBRATED_NO_RELIABILITY_TABLE)
    match = next((b for b in bins if b.lo <= raw_probability < b.hi or (raw_probability == 1.0 == b.hi)), None)
    if match is None:
        return refuse(CalibrationStatus.UNCALIBRATED_NO_RELIABILITY_TABLE)
    if match.observations < min_observations:
        return refuse(CalibrationStatus.UNCALIBRATED_SPARSE_BIN, match.observations)
    n_eff = match.observations + prior_strength
    successes = match.hits + prior_strength * raw_probability
    lower, upper = _wilson(successes, n_eff, z)
    return ProbabilityEstimate(
        CalibrationStatus.CALIBRATED, successes / n_eff, lower, upper, confidence_level, match.observations
    )


def bet_up_to(
    estimate: ProbabilityEstimate | None,
    contracts: float,
    series_ticker: str | None,
    *,
    required_margin: float = DEFAULT_REQUIRED_MARGIN,
    schedule: FeeScheduleVersion | None = None,
) -> float | None:
    """Highest whole-cent price at which the LOWER bound still clears fee-inclusive break-even + margin.

    None when the estimate is missing or uncalibrated, or when no price qualifies. Never a guess.
    """
    if estimate is None or not estimate.usable:
        return None
    best: float | None = None
    for cents in range(1, 100):
        price = cents / 100
        be = fee_inclusive_break_even(price, contracts, series_ticker, schedule=schedule)
        if be is not None and estimate.lower - be >= required_margin:
            best = price
    return best


@dataclass(frozen=True)
class EdgeAssessment:
    price: float
    contracts: float
    break_even: float | None
    estimate: ProbabilityEstimate
    lower_bound_margin: float | None
    point_margin: float | None
    bet_up_to: float | None
    within_bet_up_to: bool | None


def assess_edge(
    price: float,
    contracts: float,
    series_ticker: str | None,
    estimate: ProbabilityEstimate,
    *,
    required_margin: float = DEFAULT_REQUIRED_MARGIN,
) -> EdgeAssessment:
    """Margins are None (not 0) whenever the estimate is uncalibrated: no evidence, no edge."""
    be = fee_inclusive_break_even(price, contracts, series_ticker)
    usable = estimate.usable and be is not None
    cap = bet_up_to(estimate, contracts, series_ticker, required_margin=required_margin)
    return EdgeAssessment(
        price=price,
        contracts=contracts,
        break_even=be,
        estimate=estimate,
        lower_bound_margin=(estimate.lower - be) if usable else None,
        point_margin=(estimate.point - be) if usable else None,
        bet_up_to=cap,
        within_bet_up_to=(price <= cap) if cap is not None else None,
    )
