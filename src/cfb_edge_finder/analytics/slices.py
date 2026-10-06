"""Descriptive slices of the settled corpus (mission sections 9-15,
19, 20).

*** DESCRIPTIVE, NEVER SELECTIVE ***
Every function returns ALL slices, in a fixed order, with sample sizes
and confidence labels attached. Nothing sorts by performance, nothing
returns a "best" slice, and nothing compares slices to a cutoff.
Threshold design is a separate, later, deliberate mission -- see
tests/test_analytics_safety.py, which fails if a selection-shaped
function appears here.

*** MULTIPLE COMPARISONS ARE FLAGGED, NOT SILENTLY GENERATED ***
Family x gap bucket x timing x price bucket x direction is well over a
thousand cells. Some will look excellent by chance alone. Every slice
therefore carries `analysis_status`: CORE for the small preregistered set
(family-level and calibration), EXPLORATORY for everything else. A slice
marked EXPLORATORY has not been corrected for multiplicity and must not
be read as a finding.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from statistics import median

from cfb_edge_finder.analytics.dataset import AnalysisRow
from cfb_edge_finder.analytics.uncertainty import (
    ClusteredEstimate,
    cluster_bootstrap_mean,
    cluster_bootstrap_rate,
    sample_confidence,
)

CORE = "CORE"
EXPLORATORY = "EXPLORATORY"

EXPLORATORY_CAVEAT = (
    "EXPLORATORY slice: not corrected for multiple comparisons. This analysis generates hundreds of cells "
    "across family x gap x timing x price x direction; some will look strong by chance. Do not read a single "
    "cell as a finding."
)

# --- Bucket definitions --------------------------------------------------

SIGNED_GAP_BUCKETS: tuple[tuple[str, float | None, float | None], ...] = (
    ("<0%", None, 0.0),
    ("0-2%", 0.0, 0.02),
    ("2-4%", 0.02, 0.04),
    ("4-6%", 0.04, 0.06),
    ("6-8%", 0.06, 0.08),
    ("8-10%", 0.08, 0.10),
    ("10-15%", 0.10, 0.15),
    ("15%+", 0.15, None),
)
"""SIGNED, so the `<0%` bucket (model below market) stays a distinct
population rather than being folded in with a same-magnitude positive
gap. Mission section 10: direction must not be collapsed."""

ABSOLUTE_GAP_BUCKETS: tuple[tuple[str, float | None, float | None], ...] = (
    ("0-2%", 0.0, 0.02),
    ("2-4%", 0.02, 0.04),
    ("4-6%", 0.04, 0.06),
    ("6-8%", 0.06, 0.08),
    ("8-10%", 0.08, 0.10),
    ("10-15%", 0.10, 0.15),
    ("15%+", 0.15, None),
)

PRICE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-10c", 0.00, 0.105), ("11-20c", 0.105, 0.205), ("21-30c", 0.205, 0.305),
    ("31-40c", 0.305, 0.405), ("41-50c", 0.405, 0.505), ("51-60c", 0.505, 0.605),
    ("61-70c", 0.605, 0.705), ("71-80c", 0.705, 0.805), ("81-90c", 0.805, 0.905),
    ("91-99c", 0.905, 1.01),
)

TIMING_ORDER: tuple[str, ...] = (
    "EARLY_OPEN", "T_7D", "T_3D", "T_24H", "T_6H", "T_90", "T_60", "T_30", "CLOSING",
)

FAMILY_READINESS = {
    "moneyline": "research validated",
    "spread": "research validated",
    "total": "WEAKER -- research primitive only; the totals model underperformed the naive benchmark "
             "in Milestone C.2 backtesting and has not been validated for pricing",
}
"""Carried into every family report so a totals number can never be read
without its caveat (mission section 12)."""


def _bucket_for(value: float, buckets) -> str | None:
    for label, lower, upper in buckets:
        lower_ok = lower is None or value >= lower
        upper_ok = upper is None or value < upper
        if lower_ok and upper_ok:
            return label
    return None


def signed_gap_bucket(gap: float) -> str | None:
    return _bucket_for(gap, SIGNED_GAP_BUCKETS)


def absolute_gap_bucket(gap: float) -> str | None:
    return _bucket_for(abs(gap), ABSOLUTE_GAP_BUCKETS)


def price_bucket(price: float) -> str | None:
    return _bucket_for(price, PRICE_BUCKETS)


# --- Slice summary --------------------------------------------------------


@dataclass(frozen=True)
class SliceSummary:
    """One descriptive cell. Every aggregate is accompanied by the count
    it was computed from, because a mean over 3 rows and a mean over 300
    must never look alike in a table."""

    label: str
    dimension: str
    analysis_status: str
    n: int
    n_games: int
    confidence_label: str
    confidence_detail: str

    mean_model_probability: float | None = None
    mean_entry_price: float | None = None
    mean_signed_gap: float | None = None
    observed_event_rate: float | None = None
    calibration_error: float | None = None
    """observed_event_rate - mean_model_probability, over this slice."""

    clv_n: int = 0
    mean_clv: float | None = None
    median_clv: float | None = None
    favorable_clv_rate: float | None = None
    clv_interval: ClusteredEstimate | None = None

    gross_unit_pnl: float | None = None
    fee_adjusted_unit_pnl: float | None = None
    fee_adjusted_roi: float | None = None
    roi_interval: ClusteredEstimate | None = None
    pnl_n: int = 0

    caveats: tuple[str, ...] = field(default_factory=tuple)


def _safe_mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize_slice(
    label: str,
    dimension: str,
    rows: Sequence[AnalysisRow],
    *,
    side: str = "yes",
    analysis_status: str = EXPLORATORY,
    bootstrap: bool = True,
    extra_caveats: tuple[str, ...] = (),
) -> SliceSummary:
    """Summarize one slice from ONE side's perspective.

    `side` matters: YES and NO have independent prices, gaps, CLV and
    economics. Mixing them into one row would average two different
    positions together."""
    games = [r.game_id for r in rows]
    n_games = len(set(games))
    confidence = sample_confidence(len(rows), n_games)

    if not rows:
        return SliceSummary(
            label=label, dimension=dimension, analysis_status=analysis_status, n=0, n_games=0,
            confidence_label=confidence.label, confidence_detail=confidence.detail,
            caveats=extra_caveats + ((EXPLORATORY_CAVEAT,) if analysis_status == EXPLORATORY else ()),
        )

    is_yes = side == "yes"
    entry_prices = [p for p in ((r.entry_yes_price if is_yes else r.entry_no_price) for r in rows) if p is not None]
    gaps = [
        g for g in ((r.gaps.yes_probability_gap if is_yes else r.gaps.no_probability_gap) for r in rows)
        if g is not None
    ]
    # The event the SIDE wins on: YES wins when the contract's condition
    # held; NO wins when it did not.
    side_outcomes = [r.event_true if is_yes else (not r.event_true) for r in rows]
    model_probs = [r.model_probability if is_yes else (1.0 - r.model_probability) for r in rows]

    clv_rows = [(r, (r.yes_clv if is_yes else r.no_clv)) for r in rows]
    clv_available = [(r, c) for r, c in clv_rows if c.available and c.raw_price_movement is not None]
    clv_values = [c.raw_price_movement for _, c in clv_available]
    clv_games = [r.game_id for r, _ in clv_available]
    favorable = [c.favorable for _, c in clv_available if c.favorable is not None]

    pnl_pairs = [
        (r, (r.yes_fee_adjusted_research_unit_pnl if is_yes else r.no_fee_adjusted_research_unit_pnl))
        for r in rows
    ]
    pnl_available = [(r, v) for r, v in pnl_pairs if v is not None]
    fee_adj = [v for _, v in pnl_available]
    pnl_games = [r.game_id for r, _ in pnl_available]
    gross = [
        v for v in ((r.yes_research_unit_pnl if is_yes else r.no_research_unit_pnl) for r in rows) if v is not None
    ]
    capital = [
        p for p in ((r.entry_yes_price if is_yes else r.entry_no_price) for r, _ in pnl_available) if p is not None
    ]

    observed = sum(1 for o in side_outcomes if o) / len(side_outcomes)
    mean_model = _safe_mean(model_probs)

    return SliceSummary(
        label=label,
        dimension=dimension,
        analysis_status=analysis_status,
        n=len(rows),
        n_games=n_games,
        confidence_label=confidence.label,
        confidence_detail=confidence.detail,
        mean_model_probability=mean_model,
        mean_entry_price=_safe_mean(entry_prices),
        mean_signed_gap=_safe_mean(gaps),
        observed_event_rate=observed,
        calibration_error=None if mean_model is None else observed - mean_model,
        clv_n=len(clv_values),
        mean_clv=_safe_mean(clv_values),
        median_clv=median(clv_values) if clv_values else None,
        favorable_clv_rate=(sum(1 for f in favorable if f) / len(favorable)) if favorable else None,
        clv_interval=(
            cluster_bootstrap_mean(clv_values, clv_games) if bootstrap and clv_values else None
        ),
        gross_unit_pnl=_safe_mean(gross),
        fee_adjusted_unit_pnl=_safe_mean(fee_adj),
        # ROI on deployed capital: total fee-adjusted P/L over total entry
        # cost. Undefined at zero capital rather than infinite.
        fee_adjusted_roi=(sum(fee_adj) / sum(capital)) if capital and sum(capital) > 0 else None,
        roi_interval=(cluster_bootstrap_mean(fee_adj, pnl_games) if bootstrap and fee_adj else None),
        pnl_n=len(fee_adj),
        caveats=extra_caveats + ((EXPLORATORY_CAVEAT,) if analysis_status == EXPLORATORY else ()),
    )


def slice_by(
    rows: Sequence[AnalysisRow],
    key_fn: Callable[[AnalysisRow], str | None],
    dimension: str,
    ordered_labels: Sequence[str],
    *,
    side: str = "yes",
    analysis_status: str = EXPLORATORY,
    bootstrap: bool = True,
) -> list[SliceSummary]:
    """Group rows and summarize EVERY label in `ordered_labels`.

    Labels with no rows are still returned, with n=0. An absent bucket is
    information -- it says the model never disagreed by that much -- and
    dropping it would make the table look denser than the data is."""
    grouped: dict[str, list[AnalysisRow]] = {label: [] for label in ordered_labels}
    for row in rows:
        key = key_fn(row)
        if key is not None and key in grouped:
            grouped[key].append(row)
    return [
        summarize_slice(
            label, dimension, grouped[label], side=side, analysis_status=analysis_status, bootstrap=bootstrap
        )
        for label in ordered_labels
    ]


def hit_rate_interval(rows: Sequence[AnalysisRow], *, side: str = "yes") -> ClusteredEstimate:
    is_yes = side == "yes"
    outcomes = [r.event_true if is_yes else (not r.event_true) for r in rows]
    return cluster_bootstrap_rate(outcomes, [r.game_id for r in rows])
