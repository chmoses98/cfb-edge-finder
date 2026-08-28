"""THE single definition of the talent-shadow transformation.

Both the historical parity test and the live scanner call this. A second
implementation would be free to drift from the one that was actually
validated, which is exactly the failure this module exists to prevent.

*** TRANSFORMATION ORDER, TRACED FROM THE HISTORICAL CODE ***

The historical experiment (scripts/run_preseason_experiments.py) built
its margin samples as

    raw.home_scores - raw.away_scores + margin_delta

and `candidates.apply_candidate` then added `beta * differential` to
THAT. So the talent delta is applied **after** the Milestone C.2 margin
correction, to the CORRECTED margin distribution.

*** A SUBTLETY THAT HAD TO BE CHECKED RATHER THAN ASSUMED ***

Across every historically evaluated season the C.2 correction was a
NO-OP: its frozen artifact carries a training cutoff of AsOf(2026, 0),
and `apply_margin_correction` skips any as-of that predates it, for
leakage safety. Measured on 2025 Week 1, mean |margin_delta| = 0.000.

So the historical run could not distinguish "before C.2" from "after
C.2" -- both were identical there. For 2026 the correction becomes
active, so the order genuinely matters, and it is resolved from what the
code DID rather than from what reads nicely: **after**.

*** THE WINNER CHANNEL, AND A REAL INCONSISTENCY IN THE HISTORICAL RUN ***

The historical CONTROL probability came from
`CorrectedGameProjection.prob_home_win()`, which delegates to the RAW
projection and SPLITS simulated ties 50/50. The historical SHADOW
probability was `mean(corrected_margin + delta > 0)` -- ties resolve to
AWAY, matching settlement.

Those are not the same basis. Measured on 2025 Week 1 the gap is a mean
|Δp| of 0.0095, exactly half the 1.89% simulated tie mass. It ran
AGAINST the shadow: the control's probability was systematically ~0.0095
higher, so the reported log-loss improvement was achieved despite a
small handicap rather than because of one.

Live capture therefore records THREE probabilities:

  control_probability_canonical  -- production's own number, tie-split,
                                    exactly what model_probability holds
  control_probability_basis      -- P(corrected margin > 0), the same
                                    basis the shadow uses
  shadow_probability             -- P(corrected margin + delta > 0)

The paired comparison uses BASIS vs SHADOW so the two arms differ only
by the talent delta. The canonical value is stored unchanged so nothing
about production is restated or lost.

*** TOTAL IS PRESERVED BY CONSTRUCTION ***

margin = home - away and total = home + away, so shifting home by
+delta/2 and away by -delta/2 moves the margin by exactly delta and
leaves the total EXACTLY unchanged -- the same symmetric device
CorrectedGameProjection already uses for C.2. The candidate is a
margin-only prior, so total probabilities are identical between arms by
construction. `total_probabilities_identical` states that as a fact the
caller can assert rather than assume.

*** VARIANCE IS PRESERVED ***

Adding a constant to every simulated draw shifts the distribution
without changing its spread or the home/away correlation. Threshold
probabilities stay monotonic in the threshold.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cfb_edge_finder.research.preseason.shadow_prior import TALENT_BETA

TRANSFORM_VERSION = "talent_shadow_transform_v1"

APPLIED_AFTER_MARGIN_CORRECTION = True
"""Traced from the historical implementation. See the module docstring."""

TOTAL_CHANNEL_UNCHANGED = True
"""The candidate is margin-only. Asserted by test, not assumed."""


@dataclass(frozen=True)
class ShadowTransform:
    """One game's transformed projection, beside the control's."""

    beta: float
    talent_differential: float
    delta: float

    control_margin_corrected: float
    shadow_margin: float

    control_probability_canonical: float
    control_probability_basis: float
    shadow_probability: float

    control_expected_home: float
    control_expected_away: float
    shadow_expected_home: float
    shadow_expected_away: float

    control_total: float
    shadow_total: float

    transform_version: str = TRANSFORM_VERSION

    @property
    def shadow_minus_control_margin(self) -> float:
        return self.delta

    @property
    def shadow_minus_control_probability(self) -> float:
        """Against the BASIS control probability, so the two arms differ
        only by the talent delta."""
        return self.shadow_probability - self.control_probability_basis

    @property
    def total_probabilities_identical(self) -> bool:
        return abs(self.shadow_total - self.control_total) < 1e-9

    def probability_margin_greater_than(self, threshold: float, margins: np.ndarray) -> tuple[float, float]:
        """(control, shadow) P(margin > threshold) for a spread contract.

        Both read the SAME corrected margin draws; only the shadow's are
        shifted. Computing them from different arrays would let sampling
        noise masquerade as a model difference."""
        control = float(np.mean(margins > threshold))
        shadow = float(np.mean((margins + self.delta) > threshold))
        return control, shadow


def transform(
    *,
    corrected_margin_samples: np.ndarray,
    control_margin_corrected: float,
    control_probability_canonical: float,
    control_expected_home: float,
    control_expected_away: float,
    home_talent: float,
    away_talent: float,
    beta: float = TALENT_BETA,
) -> ShadowTransform:
    """Apply the frozen talent prior to an already-corrected projection.

    `corrected_margin_samples` MUST be the C.2-corrected margin draws
    (raw home - raw away + margin_delta), matching the historical
    construction. Passing raw draws would silently evaluate a different
    candidate from the one that was validated."""
    margins = np.asarray(corrected_margin_samples, dtype=float)
    differential = float(home_talent) - float(away_talent)
    delta = beta * differential

    # Symmetric split: margin moves by delta, total does not move at all.
    half = delta / 2.0
    return ShadowTransform(
        beta=beta,
        talent_differential=differential,
        delta=delta,
        control_margin_corrected=control_margin_corrected,
        shadow_margin=control_margin_corrected + delta,
        control_probability_canonical=control_probability_canonical,
        control_probability_basis=float(np.mean(margins > 0)),
        shadow_probability=float(np.mean((margins + delta) > 0)),
        control_expected_home=control_expected_home,
        control_expected_away=control_expected_away,
        shadow_expected_home=control_expected_home + half,
        shadow_expected_away=control_expected_away - half,
        control_total=control_expected_home + control_expected_away,
        shadow_total=(control_expected_home + half) + (control_expected_away - half),
    )


def historical_equivalent_shadow_probability(
    corrected_margin_samples: np.ndarray, delta: float
) -> float:
    """Exactly what `candidates.apply_candidate` computed.

    Kept as a separate one-line function purely so the parity test can
    assert the live path reproduces it, rather than the test restating
    the formula and agreeing with itself."""
    return float(np.mean((np.asarray(corrected_margin_samples, dtype=float) + delta) > 0))
