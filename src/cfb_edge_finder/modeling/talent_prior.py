"""Early-season talent margin prior -- the promoted production form of
`research.preseason.shadow_prior`'s frozen candidate.

*** WHAT THIS IS ***
A single frozen slope applied to the MARGIN channel only:

    margin_delta = TALENT_BETA * (home_talent_composite - away_talent_composite)

*** WHY THE BETA IS RESTATED HERE RATHER THAN IMPORTED ***
The obvious move is to import it from
`research.preseason.shadow_prior`, where it was fit. Production must
not do that: this repository enforces a one-way boundary -- research
may import production, production may never import research -- and
several guard tests assert exactly that (see
`test_prospective_shadow.py::test_no_production_package_imports_the_shadow`).
Importing the research module here would make the live pricing path
depend on a package whose whole purpose is to be freely revisable.

The value is therefore duplicated deliberately, and the duplication is
policed rather than trusted: `test_talent_prior_promotion.py` asserts
this constant equals `shadow_prior.TALENT_BETA` exactly. A drifted beta
fails a test instead of silently invalidating every historical result
that justified this promotion.

*** WHY IT WAS PROMOTED (2026-09-02 model-repair mission) ***
Two independent research passes, with different designs, reached the
same conclusion:

  1. The preseason-prior mission (development 2021-2023 / selection 2024
     / confirmation 2025) found a ~1.1 point Week-1 margin-MAE
     improvement whose 95% interval excluded zero on untouched
     confirmation data, decaying to indistinguishable-from-zero by
     Week 4+.
  2. The model-repair mission's rolling-origin evaluation (fit seasons
     strictly prior to each evaluation season; folds 2022, 2023, 2024,
     2025 reported separately) found the Week-1 margin MAE improved in
     4 of 4 folds, pooled paired delta -1.89 points
     [-2.47, -1.31] game-clustered, with Weeks 4+ flat in every fold.
     On synthetic threshold contracts priced through the deployed Kalshi
     pricer, Week-1 spread Brier improved from 0.1359 to 0.1206
     (game-clustered delta -0.0152 [-0.0201, -0.0111]).

The effect DECAYS on its own because the control's own ratings improve
as real games accumulate. No weekly multiplier is imposed and none is
justified -- see the shadow_prior docstring's "decay is measured, not
imposed" note.

*** WHAT THIS DELIBERATELY DOES NOT DO ***
It does not touch the TOTAL channel. `CorrectedGameProjection` shifts
home by +delta/2 and away by -delta/2, which moves the margin by exactly
delta and leaves the total EXACTLY unchanged. That is not an oversight:
the model-repair mission tested an early-season total-bias correction
(rolling-origin, chronologically fit, never hard-coded) and REJECTED it
-- the total bias is not stable enough across seasons to correct, its
sign flipped in 2022, and the correction improved only 2 of 4 folds.
Totals therefore remain exactly as miscalibrated as they were, which is
recorded honestly rather than papered over.

It also does not recalibrate any probability. A monotonic spread
calibration layer was tested and REJECTED: once this point-estimate
repair is in place the layer's additional effect was statistically
indistinguishable from zero (Brier delta -0.0003 [-0.0008, +0.0003]),
and the predeclared simplicity tie-break selects the model with fewer
fitted parameters.

*** LEAKAGE ***
The talent composite for season S is settled in the S-1 signing cycle
and is never retroactively revised (see
`research.preseason.sources`' timing audit and
`research.preseason.features`' `validate_for`, which raises on a season
misalignment). It is a genuine pregame input, not a postgame one.
"""

from __future__ import annotations

__all__ = ["TALENT_BETA", "TALENT_PRIOR_VERSION", "talent_margin_delta"]

TALENT_BETA = 0.018993
"""Points of margin per unit of talent-composite differential.

Fit by least squares (no intercept) on DEVELOPMENT seasons 2021-2023
only (n=2,183 FBS-vs-FBS games) and frozen. Never refit -- not on the
selection or confirmation seasons that validated it, and explicitly not
on 2026. A typical matchup differential of ~141 talent units implies
roughly 2.7 points.

Must equal `research.preseason.shadow_prior.TALENT_BETA`; a test
enforces it (see the boundary note in the module docstring)."""

TALENT_PRIOR_VERSION = "talent-margin-prior-v1"
"""Identifies the promoted form. The beta itself is unchanged from
`shadow-preseason-talent-v1`; this string names the PRODUCTION
application of it, so a row's provenance distinguishes "the shadow
sidecar computed this" from "the control priced this."""


def talent_margin_delta(home_talent: float | None, away_talent: float | None) -> float:
    """Points to add to the projected HOME margin.

    Returns exactly 0.0 whenever either side's talent is unavailable --
    an absent composite must never be imputed to the league average and
    then treated as evidence. A missing input makes this a no-op, which
    keeps the projection identical to the pre-promotion control rather
    than half-applying an adjustment built from one real number and one
    invented one.
    """
    if home_talent is None or away_talent is None:
        return 0.0
    return TALENT_BETA * (float(home_talent) - float(away_talent))
