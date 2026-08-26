"""Frozen, versioned C.2 margin-correction artifact for the LIVE
single-game research projection path (scripts/build_cfb_baseline.py).

*** WHY A FROZEN ARTIFACT, NOT A PER-CALL WALK-FORWARD REFIT ***
Milestone C.2's closure/parity pass requires the live single-game CLI to
apply the SAME `margin_correction_method="linear"` correction validated
end-to-end via genuine walk-forward backtesting (docs/MILESTONE_C2.md
Part 3), without silently diverging from that validated model. Re-running
a full walk-forward refit on every live CLI call would be correct but
prohibitively expensive for a single-game research query -- it would mean
re-fitting ratings and re-simulating every historical FBS-vs-FBS game
from scratch just to answer one matchup. This module instead freezes the
coefficients from ONE walk-forward-produced fit over all available
historical data up to a documented cutoff -- exactly the allowance this
pass's own mission brief describes ("...unless the model artifact is
explicitly versioned/frozen as a preseason-trained artifact with
documented cutoff").

*** HOW THE COEFFICIENTS BELOW WERE PRODUCED -- NO DUPLICATE FIT LOGIC ***
`scripts/fit_margin_correction_artifact.py` reuses `run_walk_forward_backtest`
(called with `margin_correction_method="none"`, so every
`GameOutcome.model_margin_mean` in its output is the model's own RAW,
uncorrected walk-forward margin projection -- the exact quantity
`margin_correction_method="linear"` itself fits against internally) and
`margin_calibration.fit_linear_margin` (the exact function that walk-
forward correction calls at every step) to compute (a, b). This module
just freezes that script's printed output; the fitting math itself is
never reimplemented here or anywhere else.

*** LEAKAGE SAFETY ***
`MARGIN_CORRECTION_TRAINING_CUTOFF` is strictly in the past relative to
any 2026+ live projection. `scripts/build_cfb_baseline.py` only applies
this frozen artifact when the requested `AsOf` is NOT strictly before
this cutoff (see that script's own guard) -- an as-of that predates the
cutoff (e.g. a historical single-game research query into an
already-training-covered season) must never use these coefficients, since
they would encode information from strictly after that as-of point.

*** TO REFIT ***
Re-run `scripts/fit_margin_correction_artifact.py` against live CFBD data
(via the `fit_margin_artifact` input on the `backtest-cfb-baseline-live`
workflow), then update every constant below in one commit and bump
`MARGIN_CORRECTION_ARTIFACT_VERSION` -- never edit the coefficients in
place without also bumping the version string; provenance consumers key
reproducibility off that string, not just the numeric values.
"""

from __future__ import annotations

from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.modeling.margin_calibration import LinearMarginParams

MARGIN_CORRECTION_METHOD = "linear"
"""The final C.2 Part 3 selected margin-correction method
(docs/MILESTONE_C2.md sections 30/35) -- linear was selected over
isotonic for better MAE/RMSE at comparable bias reduction, and both were
selected over "none". FBS-vs-FCS games are never corrected by this
artifact, matching margin_calibration.py's FBS-vs-FBS-only fit
population and backtest.py's identical restriction."""

MARGIN_CORRECTION_ARTIFACT_VERSION = "c2-margin-linear-v1-2022-2025"
"""Bump this identifier (never silently overwrite in place) whenever the
frozen coefficients below are refit against new/different training data."""

MARGIN_CORRECTION_TRAINING_SEASONS = (2022, 2023, 2024, 2025)

MARGIN_CORRECTION_TRAINING_CUTOFF = AsOf(season=2026, week=0)
"""Every training game is strictly before this AsOf (i.e. strictly within
seasons 2022-2025) -- (2026, 0) sorts strictly after every possible
(season, week) pair in 2025, including any postseason phase, regardless
of that season's exact postseason week numbering (see leakage.py's
`postseason_week_rank`). A live projection is only eligible for this
artifact when its own `as_of` is NOT strictly before this cutoff."""

MARGIN_CORRECTION_TRAINING_N = 2935
"""Count of FBS-vs-FBS (model_margin_mean, actual_margin) pairs the fit
below was trained on -- matches the FBS-vs-FBS confirmation-subset size
already reported in docs/MILESTONE_C2.md section 34 (n=2,935), confirming
this is the same underlying population. See
scripts/fit_margin_correction_artifact.py, live run
https://github.com/chmoses98/cfb-edge-finder/actions/runs/32790648892
(job fit-margin-artifact, id 97631365517), captured 2026-08-24. Training
corpus covered through season=2025, week=20 (postseason)."""

FROZEN_MARGIN_CORRECTION_PARAMS = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
"""Reuses margin_calibration.LinearMarginParams directly -- .apply()'s
identity/degenerate-fit fallback logic is therefore shared, not
reimplemented, even though this artifact is frozen rather than refit per
call. a > 1 (amplifying, not shrinking) is the expected sign given this
pass's diagnosis (docs/MILESTONE_C2.md section 29): the model
systematically COMPRESSES large projected margins toward zero, so the
correction must push a large |projected margin| further from zero to
counteract that, exactly what a=1.34 does. The small positive intercept
(b=0.81) nudges every corrected margin slightly toward the home side --
consistent with, though not a full fix for, the diagnosed home-favorite-
specific asymmetry (section 39: "a single scalar correction narrows the
overall pattern without addressing why home favorites compress more than
away favorites")."""
