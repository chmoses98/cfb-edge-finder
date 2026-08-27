"""A container for future score components. It computes no score.

*** WHY NO WEIGHTS ***
Combining components into one number requires knowing how much each
matters, and that is an empirical question this corpus cannot yet answer.
Picking weights now would bury an unvalidated judgement inside a number
that later looks objective. So components can be STORED, `composite` is
always None, and `status` is SCORING_DISABLED_PENDING_VALIDATION.
"""

from __future__ import annotations

from dataclasses import dataclass

SCORING_DISABLED = "SCORING_DISABLED_PENDING_VALIDATION"


@dataclass(frozen=True)
class ScoreComponents:
    """Individually meaningful measurements, deliberately uncombined."""

    model_minus_break_even: float | None = None
    prospective_clv_metric: float | None = None
    calibration_reliability: float | None = None
    family_reliability: str | None = None
    timing_reliability: str | None = None
    quote_quality: str | None = None


@dataclass(frozen=True)
class ResearchScore:
    components: ScoreComponents
    composite: None = None
    """Always None. Typed as None rather than `float | None` so that any
    attempt to assign a composite is a type error, not a silent change."""
    status: str = SCORING_DISABLED
    detail: str = (
        "score components may be recorded, but combining them requires validated weights that no "
        "prospective evidence yet supports"
    )


def build_score(components: ScoreComponents) -> ResearchScore:
    """Wrap components. Never combines them."""
    return ResearchScore(components=components)
