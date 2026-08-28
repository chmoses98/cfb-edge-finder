"""Run the CONTROL model, and candidate variants, over historical seasons.

*** THE CONTROL PATH IS THE PRODUCTION PATH ***

`control_projection` calls the same `fit_fbs_efficiency_ratings`,
`build_expanding_residual_pool`, `project_game` and
`apply_margin_correction` that `scripts/build_cfb_baseline.py` uses, with
the frozen parameters. A reimplementation here would measure a model
nobody runs.

*** LEAKAGE IS ENFORCED BY THE PRODUCTION CODE, NOT BY THIS MODULE ***

`fit_fbs_efficiency_ratings` raises on any row that is not strictly
before its `as_of`, so a history filter bug fails loudly rather than
quietly training on the future. This module builds the history slice and
lets that check police it.

*** WHY EVERY CANDIDATE SHARES ONE RATINGS FIT PER (season, week) ***

Control and candidate must see identical games and identical prior
information; the only difference permitted is the candidate's own
preseason adjustment. Refitting per candidate would let simulation noise
and fit differences masquerade as candidate effect. The fit is therefore
computed once per as-of and reused, which is also what makes the run
tractable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.modeling.margin_correction_artifact import (
    FROZEN_MARGIN_CORRECTION_PARAMS,
    MARGIN_CORRECTION_ARTIFACT_VERSION,
    MARGIN_CORRECTION_TRAINING_CUTOFF,
)
from cfb_edge_finder.modeling.ratings import RatingsSnapshot, fit_fbs_efficiency_ratings
from cfb_edge_finder.modeling.score_model import (
    apply_margin_correction,
    build_expanding_residual_pool,
    project_game,
)
from cfb_edge_finder.research.preseason.corpus import HistoricalGame
from cfb_edge_finder.research.preseason.evaluation import GamePrediction

RESEARCH_N_SIMULATIONS = 4000
"""Fewer than production's 20,000, purely for runtime across thousands of
games. This is a deliberate DEVIATION from the frozen control and is
reported as such: it adds Monte Carlo noise to both arms equally, and
because every comparison is PAIRED on the same games with the same seed,
that shared noise largely cancels. It would be dishonest to call this
'the control' without saying so."""

RESEARCH_SEED = 20260828
"""Fixed so control and candidate draw identical residuals for a game."""


@dataclass
class AsOfFit:
    """One leakage-safe ratings fit, reused by every arm."""

    as_of: AsOf
    ratings: RatingsSnapshot
    residual_pool: np.ndarray
    history_rows: int


def build_fit(lines: list[TeamGameLine], as_of: AsOf) -> AsOfFit | None:
    """Fit ratings from strictly-prior rows only.

    Returns None when no prior history exists -- the first cached season
    has nothing to learn from and is therefore not evaluable, which is a
    fact about the cache, not a model failure."""
    history = [ln for ln in lines if ln.as_of.is_strictly_before(as_of)]
    if not history:
        return None
    return AsOfFit(
        as_of=as_of,
        ratings=fit_fbs_efficiency_ratings(history, as_of),
        residual_pool=build_expanding_residual_pool(history, as_of),
        history_rows=len(history),
    )


def control_projection(
    game: HistoricalGame,
    fit: AsOfFit,
    *,
    home_passing_ppa: float | None,
    away_passing_ppa: float | None,
    n_simulations: int = RESEARCH_N_SIMULATIONS,
):
    """The frozen control, via the production entry points."""
    raw = project_game(
        home_id=game.home_team,
        away_id=game.away_team,
        home_classification=game.home_classification,
        away_classification=game.away_classification,
        is_neutral_site=game.neutral_site,
        ratings=fit.ratings,
        prior_season_ratings=None,
        residual_pool=fit.residual_pool,
        home_percent_passing_ppa=home_passing_ppa,
        away_percent_passing_ppa=away_passing_ppa,
        n_simulations=n_simulations,
        seed=RESEARCH_SEED,
    )
    return apply_margin_correction(
        raw,
        is_fbs_vs_fbs=game.both_fbs,
        method="linear",
        correction_model=FROZEN_MARGIN_CORRECTION_PARAMS,
        artifact_version=MARGIN_CORRECTION_ARTIFACT_VERSION,
        as_of=fit.as_of,
        training_cutoff=MARGIN_CORRECTION_TRAINING_CUTOFF,
    )


def to_prediction(game: HistoricalGame, projection) -> GamePrediction:
    """Convert a projection plus the realised result into an evaluable row.

    Uses the projection's OWN simulated quantities rather than a
    re-derived distribution: `prob_home_win` splits simulated ties, and
    the means come straight from the simulated score arrays, so the
    evaluated numbers are exactly what the model produced."""
    dist = projection.to_game_distribution()
    return GamePrediction(
        game_id=game.game_id,
        season=game.season,
        week=game.week,
        home_win_probability=projection.prob_home_win(),
        projected_margin=dist.home_mean - dist.away_mean,
        projected_total=dist.home_mean + dist.away_mean,
        actual_home_margin=game.home_margin,
        actual_total=game.total_points,
        is_neutral_site=game.neutral_site,
        both_fbs=game.both_fbs,
    )


SEGMENTS = {
    "week_1": lambda p: p.week <= 1,
    "weeks_2_3": lambda p: 2 <= p.week <= 3,
    "weeks_1_3": lambda p: p.week <= 3,
    "weeks_4_plus": lambda p: p.week >= 4,
    "neutral_site": lambda p: p.is_neutral_site,
}
"""Reported separately for every arm. Week 1 is the primary focus, but a
candidate that helps Week 1 and harms later weeks has not helped."""


def segment(predictions: list[GamePrediction], name: str) -> list[GamePrediction]:
    keep = SEGMENTS[name]
    return [p for p in predictions if p.both_fbs and keep(p)]
