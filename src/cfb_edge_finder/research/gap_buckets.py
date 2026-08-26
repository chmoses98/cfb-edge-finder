"""Milestone E, Part F/G: prospective gap buckets (mission section 17).

Purely descriptive binning -- no ordering claim that a larger bucket is
"better" is encoded anywhere here (see research/reporting.py, which
reports calibration/hit-rate PER bucket rather than ranking them).
"""

from __future__ import annotations

from dataclasses import dataclass

GAP_BUCKET_LABELS: tuple[str, ...] = ("<2%", "2-5%", "5-8%", "8-12%", "12%+")


@dataclass(frozen=True)
class GapBucketBound:
    label: str
    lower_abs_gap: float
    """Inclusive lower bound on abs(gap), as a probability fraction (0.02 == 2%)."""
    upper_abs_gap: float | None
    """Exclusive upper bound, or None for the open-ended top bucket."""


GAP_BUCKETS: tuple[GapBucketBound, ...] = (
    GapBucketBound("<2%", 0.0, 0.02),
    GapBucketBound("2-5%", 0.02, 0.05),
    GapBucketBound("5-8%", 0.05, 0.08),
    GapBucketBound("8-12%", 0.08, 0.12),
    GapBucketBound("12%+", 0.12, None),
)


def gap_bucket_for(gap: float) -> str:
    """Buckets on abs(gap) -- direction (model above vs below market) is a
    separate, orthogonal axis a caller can still filter on using the raw
    signed gap; the bucket itself is magnitude-only."""
    magnitude = abs(gap)
    for bound in GAP_BUCKETS:
        if bound.upper_abs_gap is None:
            if magnitude >= bound.lower_abs_gap:
                return bound.label
        elif bound.lower_abs_gap <= magnitude < bound.upper_abs_gap:
            return bound.label
    raise AssertionError(f"gap {gap!r} did not fall into any bucket -- bounds are not exhaustive")
