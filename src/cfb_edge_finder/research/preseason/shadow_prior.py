"""SHADOW candidate: a talent-composite preseason prior. Research only.

*** WHAT SURVIVED, AND WHAT THAT MEANS ***

Of six individually-tested preseason families, exactly one replicated on
the untouched confirmation season: the recruiting-talent composite. It
adjusts the CONTROL's projected margin by

    delta = TALENT_BETA * (home_talent - away_talent)

and nothing else. `TALENT_BETA` was fit by least squares on development
seasons only and is FROZEN here; it is never refit.

*** WHY THIS IS NOT PRODUCTION ***

The control remains canonical. This module is imported by no production
path -- not by `modeling/`, not by `projections/`, not by
`recommendation/`, not by `kalshi/` -- and a test enforces that. It
produces a SECOND, side-by-side number for research comparison. The live
`model_probability` written to the corpus is unchanged.

*** THE DECAY IS MEASURED, NOT ASSUMED ***

The effect is large in Week 1, roughly half as large across Weeks 1-3,
and indistinguishable from zero by Week 4+ -- which is what a preseason
prior SHOULD do as on-field evidence accumulates. That shape was
measured, not imposed: the same single beta is applied at every week, and
the decay emerges because the control's own ratings improve. No weekly
multiplier was invented.

*** WHAT THIS DOES NOT LICENSE ***

Not a bet, not an edge, not a recommendation. A margin improvement of
about a point on 47 confirmation games is a research finding that has
never faced a live market, and the natural prospective corpus was
deliberately not used to select it.
"""

from __future__ import annotations

from dataclasses import dataclass

SHADOW_MODEL_VERSION = "shadow-preseason-talent-v1"
"""Deliberately distinct from the control's
`0.4.0-milestone-c2-live-margin-correction`, so any output carrying this
string is unmistakably not the production model."""

TALENT_BETA = 0.018993
"""Points of margin per unit of talent-composite differential.

Fit by least squares (no intercept) on DEVELOPMENT seasons 2021-2023
only, n=2183 FBS-vs-FBS games, then frozen. Never refit on selection or
confirmation data. A typical matchup differential of ~141 talent units
implies roughly 2.7 points."""

DEVELOPMENT_SEASONS = (2021, 2022, 2023)
SELECTION_SEASON = 2024
CONFIRMATION_SEASON = 2025

CONFIRMATION_RESULT = {
    "week_1": {
        "n": 47,
        "control_margin_mae": 14.32,
        "candidate_margin_mae": 13.23,
        "paired_delta": -1.091,
        "ci": (-2.035, -0.147),
        "log_loss_delta": -0.0412,
    },
    "weeks_1_3": {
        "n": 140,
        "control_margin_mae": 14.88,
        "candidate_margin_mae": 14.08,
        "paired_delta": -0.800,
        "ci": (-1.425, -0.175),
        "log_loss_delta": -0.0114,
    },
    "weeks_4_plus": {
        "n": 590,
        "control_margin_mae": 13.91,
        "candidate_margin_mae": 14.02,
        "paired_delta": +0.105,
        "ci": (-0.105, 0.314),
        "log_loss_delta": +0.0089,
    },
}
"""The untouched-confirmation result, recorded so any later reader can
see exactly what this candidate earned rather than taking it on trust.
Weeks 4+ straddles zero -- the prior correctly stops mattering once
on-field evidence exists."""


@dataclass(frozen=True)
class ShadowAdjustment:
    """The candidate's adjustment for one game, alongside the control."""

    control_margin: float
    talent_differential: float | None
    delta: float
    shadow_margin: float
    model_version: str = SHADOW_MODEL_VERSION

    @property
    def applied(self) -> bool:
        return self.talent_differential is not None

    def to_dict(self) -> dict:
        return {
            "shadow_model_version": self.model_version,
            "control_projected_margin": self.control_margin,
            "talent_differential": self.talent_differential,
            "shadow_delta": self.delta,
            "shadow_projected_margin": self.shadow_margin,
            "applied": self.applied,
            "is_production": False,
        }


def shadow_margin(
    *, control_margin: float, home_talent: float | None, away_talent: float | None
) -> ShadowAdjustment:
    """Compute the shadow candidate's margin beside the control's.

    A missing talent value on either side yields NO adjustment: the
    differential is undefined, and treating an unknown team as league
    average would be a modelling claim rather than an absence. The
    control's own number is returned unchanged in that case."""
    if home_talent is None or away_talent is None:
        return ShadowAdjustment(
            control_margin=control_margin,
            talent_differential=None,
            delta=0.0,
            shadow_margin=control_margin,
        )
    differential = float(home_talent) - float(away_talent)
    delta = TALENT_BETA * differential
    return ShadowAdjustment(
        control_margin=control_margin,
        talent_differential=differential,
        delta=delta,
        shadow_margin=control_margin + delta,
    )
