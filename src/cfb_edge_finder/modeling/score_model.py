"""Combines team strength (ratings.py), season-carryover priors (priors.py),
QB-continuity uncertainty (qb_continuity.py), and pace into a coherent,
simulated joint (home_score, away_score) distribution (mission spec
section 11).

*** METHOD: BOOTSTRAP FROM PAIRED HISTORICAL RESIDUALS, NOT A NORMAL GUESS ***
Rather than picking an arbitrary parametric shape and a made-up standard
deviation, this module:
  1. Computes each FBS-vs-FBS training game's PAIRED residual --
     (actual_home_points - expected_home_points, actual_away_points -
     expected_away_points) -- using the current fitted ratings.
  2. Bootstrap-samples thousands of these paired residuals (with
     replacement) and adds them to the target game's own expected
     home/away points, rounding to the nearest non-negative integer.
  3. Every downstream probability (win, margin > x, total > y, for
     arbitrary real x/y including exact integers) is then just an exact
     empirical frequency over the simulated sample -- discreteness and
     home/away correlation both fall out of this naturally, with no
     continuity-correction guesswork.
  4. The same simulated sample is ALSO moment-matched (mean/sd/
     correlation) into a real `GameDistribution`
     (schemas/projection.py), so the existing, already-tested
     `projections.distribution.price_market()` pricer keeps working
     unchanged for anything that wants a fast closed-form price instead
     of the raw simulation.

*** MILESTONE C HARDENING: RESIDUALS ARE NOW OUT-OF-SAMPLE ***
The original V1 computed each training game's residual using the SAME
ratings snapshot that game's own row helped fit -- standard for a quick
in-sample diagnostic, but it systematically UNDERSTATES true predictive
residual variance (a regression's fitted residuals on its own training
rows are, by construction, smaller than its residuals on genuinely new
data), which meant the simulated uncertainty bands were narrower than the
model's real out-of-sample error. This module now builds an EXPANDING
WALK-FORWARD residual pool instead: `build_expanding_residual_pool` (for
a single live prediction) and the accumulator `run_walk_forward_backtest`
maintains internally (for the backtest) both compute each historical
game's residual using ratings fit ONLY from games strictly before THAT
game -- i.e. every residual is a genuine out-of-sample prediction error,
never a fitted residual on the same data used to estimate it. See
`_paired_fbs_games`/`_residuals_for_pairs` below (the shared primitives
both callers use) and docs/MILESTONE_C.md "Residual distribution" for the
before/after backtest effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf, assert_strictly_before
from cfb_edge_finder.modeling.priors import DEFAULT_SEASON_SHRINKAGE_K, BlendedRating, blend_team_rating
from cfb_edge_finder.modeling.qb_continuity import QBContinuityState, classify_continuity, uncertainty_multiplier
from cfb_edge_finder.modeling.ratings import (
    DEFAULT_FCS_RIDGE_LAMBDA,
    DEFAULT_PACE_SHRINKAGE_K,
    DEFAULT_RIDGE_LAMBDA,
    RatingsSnapshot,
    fit_fbs_efficiency_ratings,
)
from cfb_edge_finder.schemas.projection import GameDistribution, ProjectionRecord, UncertaintyProfile
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion

DEFAULT_N_SIMULATIONS = 20_000
EARLY_SEASON_UNCERTAINTY_SCALE = 0.30
"""Extra variance inflation applied per unit of "not yet current-season-
evidenced" (1 - carryover weight). A team at pure preseason prior
(weight=0) gets its residual draw scaled by (1 + 0.30) = 1.30x; a team
fully in-season (weight=1) gets no extra inflation from this term. A
round, documented, provisional constant -- see qb_continuity.py's
identical caveat about DEFAULT_RIDGE_LAMBDA-style constants."""

FCS_OPPONENT_UNCERTAINTY_SCALE = 0.35
"""Extra variance inflation applied to BOTH sides' residual draw whenever
either side of the game is not FBS. FBS-vs-FCS games use a coarser,
pooled opponent model (ratings.py's single fcs_offense/fcs_defense
parameter, not an individually fit one) and are far more prone to
garbage-time/backup-heavy scoring patterns that widen real outcome
variance beyond a typical FBS-vs-FBS game -- see mission section 4 and
docs/MILESTONE_C.md "FBS-vs-FCS margin bias" for the evidence this
constant responds to. A round, documented, provisional constant, applied
on top of (not instead of) the mean-bias fix in ratings.py's
DEFAULT_FCS_RIDGE_LAMBDA."""

DEFAULT_RESIDUAL_SCALE = 1.0
"""Milestone C.2 uncertainty-calibration candidate: a single, global
multiplier applied to EVERY simulated residual draw (on top of, not
instead of, the QB-continuity/early-season/FCS-involved multipliers
above -- this one is uniform across all of them). 1.0 (the Milestone C
default) is a true no-op. Values below 1.0 narrow every simulated
interval uniformly, tested via genuine live walk-forward ablation on
development data ONLY (never fit by eyeballing a target coverage number
directly -- see docs/MILESTONE_C2.md "Uncertainty calibration") to bring
margin/total 90% interval coverage closer to nominal without the
per-scenario multipliers above losing their own, separately-justified
relative shape."""

FALLBACK_RESIDUAL_SD = 14.0
DEFAULT_MIN_RESIDUAL_POOL_SIZE = 40
"""Below this many accumulated out-of-sample residual pairs, both
`build_expanding_residual_pool` and `run_walk_forward_backtest`'s internal
accumulator fall back to a wide, documented placeholder pool (roughly
matches typical FBS-vs-FBS score variance) rather than simulating from a
too-thin, too-noisy handful of early-corpus residuals."""


def effective_team_rating(
    team_id: str,
    current: RatingsSnapshot,
    prior_season: RatingsSnapshot | None,
    *,
    season_shrinkage_k: float = DEFAULT_SEASON_SHRINKAGE_K,
) -> BlendedRating:
    prior_off = prior_season.offense.get(team_id) if prior_season is not None else None
    prior_def = prior_season.defense.get(team_id) if prior_season is not None else None
    return blend_team_rating(
        current_offense=current.offense_rating(team_id),
        current_defense=current.defense_rating(team_id),
        prior_season_offense=prior_off,
        prior_season_defense=prior_def,
        games_played_this_season=current.games_played_for(team_id),
        k=season_shrinkage_k,
    )


def _fallback_residual_pool(seed: int = 0, n: int = 5000) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.column_stack([rng.normal(0, FALLBACK_RESIDUAL_SD, n), rng.normal(0, FALLBACK_RESIDUAL_SD, n)])


def _paired_fbs_games(lines: list[TeamGameLine]) -> list[tuple[TeamGameLine, TeamGameLine]]:
    """Groups TeamGameLine rows (both perspectives of the same physical
    game) into (home_line, away_line) pairs, FBS-vs-FBS games only --
    mission section 4: FBS-vs-FCS is not blended into the main residual
    calibration population."""
    by_game: dict[str, dict[str, TeamGameLine]] = {}
    for line in lines:
        if line.team_classification != "fbs" or line.opponent_classification != "fbs":
            continue
        by_game.setdefault(line.source_game_id, {})["home" if line.is_home else "away"] = line
    pairs: list[tuple[TeamGameLine, TeamGameLine]] = []
    for rows in by_game.values():
        home_line, away_line = rows.get("home"), rows.get("away")
        if home_line is not None and away_line is not None:
            pairs.append((home_line, away_line))
    return pairs


def _residuals_for_pairs(
    pairs: list[tuple[TeamGameLine, TeamGameLine]], ratings: RatingsSnapshot
) -> list[tuple[float, float]]:
    """Computes each pair's (home_residual, away_residual) against
    `ratings`. Leakage-safety of this depends entirely on the CALLER
    passing a `ratings` snapshot that was fit strictly before every game
    in `pairs` -- both call sites below (`build_expanding_residual_pool`
    and backtest.py's accumulator) guarantee this by construction (the
    ratings snapshot for walk-forward step N is fit from weeks strictly
    before N, then applied to exactly week N's games)."""
    residuals: list[tuple[float, float]] = []
    for home_line, away_line in pairs:
        home_expected = expected_points(
            ratings,
            home_line.team_id,
            away_line.team_id,
            "fbs",
            home_indicator=(0.0 if home_line.is_neutral_site else 1.0),
        )
        away_expected = expected_points(
            ratings,
            away_line.team_id,
            home_line.team_id,
            "fbs",
            home_indicator=(0.0 if away_line.is_neutral_site else -1.0),
        )
        residuals.append((home_line.team_points - home_expected, away_line.team_points - away_expected))
    return residuals


def build_expanding_residual_pool(
    lines: list[TeamGameLine],
    as_of: AsOf,
    *,
    min_week_for_first_step: int = 1,
    min_pool_size: int = DEFAULT_MIN_RESIDUAL_POOL_SIZE,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    fcs_ridge_lambda: float = DEFAULT_FCS_RIDGE_LAMBDA,
    pace_shrinkage_k: float = DEFAULT_PACE_SHRINKAGE_K,
    fcs_mode: str = "pooled",
    pace_mode: str = "symmetric",
) -> np.ndarray:
    """Standalone, single-shot expanding walk-forward residual pool for a
    live research projection (scripts/build_cfb_baseline.py) -- the same
    algorithm `run_walk_forward_backtest` runs incrementally (and more
    cheaply, since it is already refitting ratings week-by-week for other
    reasons) is repeated here as one self-contained call: walk every
    (season, week) strictly before `as_of`, at each step fit ratings from
    ONLY strictly-prior rows (ratings.py's own leakage contract), then
    compute that week's FBS-vs-FBS games' residuals against those
    out-of-sample ratings. Every residual in the returned pool is
    therefore a genuine out-of-sample prediction error, never a fitted
    residual on the same rows used to estimate the ratings that scored it.
    """
    for line in lines:
        assert_strictly_before(line.as_of, as_of, context="build_expanding_residual_pool row")

    as_of_points = sorted({(ln.season, ln.week) for ln in lines})
    pool: list[tuple[float, float]] = []
    for season, week in as_of_points:
        if week < min_week_for_first_step:
            continue
        step_as_of = AsOf(season=season, week=week)
        history = [ln for ln in lines if ln.as_of.is_strictly_before(step_as_of)]
        if not history:
            continue
        step_ratings = fit_fbs_efficiency_ratings(
            history,
            step_as_of,
            ridge_lambda=ridge_lambda,
            fcs_ridge_lambda=fcs_ridge_lambda,
            pace_shrinkage_k=pace_shrinkage_k,
            fcs_mode=fcs_mode,
            pace_mode=pace_mode,
        )
        week_lines = [ln for ln in lines if ln.as_of == step_as_of]
        pool.extend(_residuals_for_pairs(_paired_fbs_games(week_lines), step_ratings))

    if len(pool) < min_pool_size:
        return _fallback_residual_pool()
    return np.array(pool)


def expected_points(
    ratings: RatingsSnapshot,
    team_id: str,
    opponent_id: str,
    opponent_classification: str | None,
    *,
    home_indicator: float,
) -> float:
    efficiency = (
        ratings.mu
        + ratings.offense_rating(team_id)
        - ratings.opponent_defense_rating(opponent_id, opponent_classification)
        + ratings.hfa * home_indicator
    )
    expected_plays = ratings.expected_plays_for(team_id, opponent_id)
    return max(efficiency * expected_plays, 0.0)


@dataclass(frozen=True)
class SimulatedGameProjection:
    home_id: str
    away_id: str
    as_of: AsOf
    expected_home_points: float
    expected_away_points: float
    home_qb_state: QBContinuityState
    away_qb_state: QBContinuityState
    home_carryover_weight: float
    away_carryover_weight: float
    data_completeness: float
    n_simulations: int
    home_scores: np.ndarray = field(repr=False)
    away_scores: np.ndarray = field(repr=False)

    def prob_home_win(self) -> float:
        return float(np.mean(self.home_scores > self.away_scores) + 0.5 * np.mean(self.home_scores == self.away_scores))

    def prob_away_win(self) -> float:
        return float(np.mean(self.away_scores > self.home_scores) + 0.5 * np.mean(self.home_scores == self.away_scores))

    def prob_margin_greater_than(self, threshold: float) -> float:
        return float(np.mean((self.home_scores - self.away_scores) > threshold))

    def prob_total_greater_than(self, threshold: float) -> float:
        return float(np.mean((self.home_scores + self.away_scores) > threshold))

    def to_game_distribution(self) -> GameDistribution:
        home_sd = float(np.std(self.home_scores, ddof=1))
        away_sd = float(np.std(self.away_scores, ddof=1))
        correlation = float(np.corrcoef(self.home_scores, self.away_scores)[0, 1])
        return GameDistribution(
            home_mean=float(np.mean(self.home_scores)),
            away_mean=float(np.mean(self.away_scores)),
            home_sd=max(home_sd, 1e-3),
            away_sd=max(away_sd, 1e-3),
            correlation=correlation,
        )

    def to_uncertainty_profile(self) -> UncertaintyProfile:
        qb_confirmed = (
            self.home_qb_state != QBContinuityState.UNKNOWN and self.away_qb_state != QBContinuityState.UNKNOWN
        )
        notes = [f"home_qb_state={self.home_qb_state.value}", f"away_qb_state={self.away_qb_state.value}"]
        return UncertaintyProfile(
            data_completeness=self.data_completeness,
            qb_status_confirmed=qb_confirmed,
            early_season_prior_weight=1 - (self.home_carryover_weight + self.away_carryover_weight) / 2,
            notes=notes,
        )

    def to_projection_record(
        self,
        *,
        projection_id: str,
        game_id: str,
        model_version: ModelVersion,
        provenance: DataProvenance,
        projection_timestamp,
    ) -> ProjectionRecord:
        return ProjectionRecord(
            projection_id=projection_id,
            game_id=game_id,
            model_version=model_version,
            provenance=provenance,
            projection_timestamp=projection_timestamp,
            distribution=self.to_game_distribution(),
            uncertainty=self.to_uncertainty_profile(),
        )


def project_game(
    *,
    home_id: str,
    away_id: str,
    home_classification: str | None,
    away_classification: str | None,
    is_neutral_site: bool,
    ratings: RatingsSnapshot,
    prior_season_ratings: RatingsSnapshot | None,
    residual_pool: np.ndarray,
    home_percent_passing_ppa: float | None,
    away_percent_passing_ppa: float | None,
    n_simulations: int = DEFAULT_N_SIMULATIONS,
    seed: int | None = None,
    season_shrinkage_k: float = DEFAULT_SEASON_SHRINKAGE_K,
    residual_scale: float = DEFAULT_RESIDUAL_SCALE,
) -> SimulatedGameProjection:
    """The single entry point research callers (scripts/build_cfb_baseline.py)
    use. `home_classification`/`away_classification` must be genuine,
    leakage-safe classification strings ("fbs"/"fcs") -- an FCS team on
    either side uses the generic FCS pseudo-rating (ratings.py), never an
    individually-fit one, and its offense/defense blend skips the
    season-carryover step entirely (an FCS team was never in the FBS
    rating fit to begin with).
    """
    # Uses the OPPONENT'S OWN team_id (not just a blanket pooled scalar) --
    # in "tiered" fcs_mode (ratings.py Milestone C.2 candidate) this
    # resolves to that specific FCS opponent's own tier; in "pooled" mode
    # (the RatingsSnapshot default) fcs_offense_for/fcs_defense_for are
    # identical to the pooled scalar, so this is a strict generalization,
    # not a behavior change, when fcs_mode="pooled".
    home_blend = (
        effective_team_rating(home_id, ratings, prior_season_ratings, season_shrinkage_k=season_shrinkage_k)
        if home_classification == "fbs"
        else BlendedRating(
            offense=ratings.fcs_offense_for(home_id),
            defense=ratings.fcs_defense_for(home_id),
            weight_on_current_season=1.0,
        )
    )
    away_blend = (
        effective_team_rating(away_id, ratings, prior_season_ratings, season_shrinkage_k=season_shrinkage_k)
        if away_classification == "fbs"
        else BlendedRating(
            offense=ratings.fcs_offense_for(away_id),
            defense=ratings.fcs_defense_for(away_id),
            weight_on_current_season=1.0,
        )
    )

    home_indicator = 0.0 if is_neutral_site else 1.0
    away_indicator = 0.0 if is_neutral_site else -1.0
    # "symmetric" pace_mode (default): expected_plays_for returns the SAME
    # shared (home_pace + away_pace) / 2 value for both calls below,
    # reproducing Milestone C's original behavior exactly. "matchup" mode
    # (Milestone C.2 candidate) lets the two sides genuinely differ -- see
    # RatingsSnapshot.expected_plays_for's docstring.
    home_expected_plays = ratings.expected_plays_for(home_id, away_id)
    away_expected_plays = ratings.expected_plays_for(away_id, home_id)

    home_efficiency = ratings.mu + home_blend.offense - away_blend.defense + ratings.hfa * home_indicator
    away_efficiency = ratings.mu + away_blend.offense - home_blend.defense + ratings.hfa * away_indicator
    expected_home_points = max(home_efficiency * home_expected_plays, 0.0)
    expected_away_points = max(away_efficiency * away_expected_plays, 0.0)

    home_qb_state = classify_continuity(home_percent_passing_ppa)
    away_qb_state = classify_continuity(away_percent_passing_ppa)
    # An FCS opponent on either side means a coarser, pooled opponent
    # model AND more erratic real-world scoring (garbage time, backups) --
    # see FCS_OPPONENT_UNCERTAINTY_SCALE's docstring. Applied to BOTH
    # sides since the whole game's context is atypical, not just the FCS
    # side's own score.
    fcs_involved_scale = (
        1 + FCS_OPPONENT_UNCERTAINTY_SCALE
        if home_classification != "fbs" or away_classification != "fbs"
        else 1.0
    )
    home_scale = (
        uncertainty_multiplier(home_qb_state)
        * (1 + EARLY_SEASON_UNCERTAINTY_SCALE * (1 - home_blend.weight_on_current_season))
        * fcs_involved_scale
        * residual_scale
    )
    away_scale = (
        uncertainty_multiplier(away_qb_state)
        * (1 + EARLY_SEASON_UNCERTAINTY_SCALE * (1 - away_blend.weight_on_current_season))
        * fcs_involved_scale
        * residual_scale
    )

    rng = np.random.default_rng(seed)
    draw_idx = rng.integers(0, len(residual_pool), size=n_simulations)
    drawn = residual_pool[draw_idx]
    home_resid = drawn[:, 0] * home_scale
    away_resid = drawn[:, 1] * away_scale

    home_scores = np.maximum(np.round(expected_home_points + home_resid), 0)
    away_scores = np.maximum(np.round(expected_away_points + away_resid), 0)

    data_completeness = min(
        1.0,
        (home_blend.weight_on_current_season + away_blend.weight_on_current_season) / 2 * 0.7
        + (0.3 if (home_qb_state != QBContinuityState.UNKNOWN and away_qb_state != QBContinuityState.UNKNOWN) else 0.0),
    )

    return SimulatedGameProjection(
        home_id=home_id,
        away_id=away_id,
        as_of=ratings.as_of,
        expected_home_points=expected_home_points,
        expected_away_points=expected_away_points,
        home_qb_state=home_qb_state,
        away_qb_state=away_qb_state,
        home_carryover_weight=home_blend.weight_on_current_season,
        away_carryover_weight=away_blend.weight_on_current_season,
        data_completeness=data_completeness,
        n_simulations=n_simulations,
        home_scores=home_scores,
        away_scores=away_scores,
    )
