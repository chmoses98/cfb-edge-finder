"""Pre-registered V2 success criteria and tolerances. Frozen with the protocol; never tuned on the holdout."""

from __future__ import annotations

#: CONTROL tiers, in report order.
CONTROL_TIERS = ("HOME_CONTROL_MODERATE", "HOME_CONTROL_STRONG", "AWAY_CONTROL_MODERATE", "AWAY_CONTROL_STRONG")
#: Supported-side margin thresholds stored for every directional distribution ("> 0" for the first).
MARGIN_THRESHOLDS = (0, 3, 7, 10, 14, 17, 21, 24, 28)
#: Quantiles stored for every distribution; the two central intervals are frozen from them.
QUANTILES = (10, 25, 50, 75, 90)
INTERVALS = {"central_50": (25, 75), "central_80": (10, 90)}
#: Holdout coverage tolerance around nominal (inclusive interval [lo, hi] on the integer margin).
COVERAGE_TOLERANCE = {"central_50": (0.42, 0.58), "central_80": (0.72, 0.88)}
#: No tier may fall outside these even when the family-level rule passes.
COVERAGE_HARD_LIMITS = {"central_50": (0.35, 0.65), "central_80": (0.65, 0.93)}
INTERVAL_TIERS_REQUIRED = 3

#: Season-stability rules: a season is evaluable for a claim when it has at least this many units.
SEASON_MIN_N = {"control": 15, "closeness": 20, "environment": 20, "suppression": 10, "disruption": 15}
SEASONS_REQUIRED = 5  # of the 7 holdout seasons
MIN_EVALUABLE_SEASONS = 5

ONE_SCORE = 8
CLOSENESS_MIN_LIFT = 1.10
EXTREME_THIRD_MIN_LIFT = 1.15
LABEL_MIN_LIFT = 1.20

#: V1 hand-set bands used only as the comparison in section 10 of the protocol.
V1_BANDS = {"CONTROL": (7, 24), "FAVORITE_PULLS_AWAY": (17, 45), "ONE_SCORE": (-8, 8)}

FAMILIES = (
    "CONTROL",
    "CONTROL_INTERVALS",
    "CLOSENESS",
    "CLOSENESS_EVEN",
    "ELEVATED_SCORING_ENVIRONMENT",
    "SUPPRESSED_SCORING_ENVIRONMENT",
    "PACE_HIGH",
    "PACE_LOW",
    "DEFENSIVE_SUPPRESSION",
    "DISRUPTION",
)


def describe() -> dict[str, object]:
    return {
        "control_tiers": list(CONTROL_TIERS),
        "margin_thresholds": list(MARGIN_THRESHOLDS),
        "quantiles": list(QUANTILES),
        "intervals": {k: list(v) for k, v in INTERVALS.items()},
        "coverage_tolerance": {k: list(v) for k, v in COVERAGE_TOLERANCE.items()},
        "coverage_hard_limits": {k: list(v) for k, v in COVERAGE_HARD_LIMITS.items()},
        "interval_tiers_required": INTERVAL_TIERS_REQUIRED,
        "season_min_n": SEASON_MIN_N,
        "seasons_required": SEASONS_REQUIRED,
        "min_evaluable_seasons": MIN_EVALUABLE_SEASONS,
        "one_score": ONE_SCORE,
        "closeness_min_lift": CLOSENESS_MIN_LIFT,
        "extreme_third_min_lift": EXTREME_THIRD_MIN_LIFT,
        "label_min_lift": LABEL_MIN_LIFT,
        "v1_bands": {k: list(v) for k, v in V1_BANDS.items()},
        "families": list(FAMILIES),
    }
