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

*** A REAL, DOCUMENTED LIMITATION: RESIDUALS ARE IN-SAMPLE ***
Step 1 uses the SAME ratings snapshot to compute both the training
residuals (step 1) and the live prediction (this is standard practice for
estimating a residual distribution's SHAPE, but is not itself a
walk-forward estimate the way the backtest's win/margin/total point
predictions are -- see backtest.py, which refits ratings weekly and is
the actual leakage-safe evaluation). A true walk-forward residual
estimate (refitting ratings before computing each training game's own
residual) is a real next-step improvement -- see
docs/MILESTONE_C.md "Recommendation for next model improvement." This
does NOT affect the leakage safety of the win/margin/total POINT
predictions, only the precision of the uncertainty band around them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf, assert_strictly_before
from cfb_edge_finder.modeling.priors import BlendedRating, blend_team_rating
from cfb_edge_finder.modeling.qb_continuity import QBContinuityState, classify_continuity, uncertainty_multiplier
from cfb_edge_finder.modeling.ratings import RatingsSnapshot
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


def effective_team_rating(
    team_id: str, current: RatingsSnapshot, prior_season: RatingsSnapshot | None
) -> BlendedRating:
    prior_off = prior_season.offense.get(team_id) if prior_season is not None else None
    prior_def = prior_season.defense.get(team_id) if prior_season is not None else None
    return blend_team_rating(
        current_offense=current.offense_rating(team_id),
        current_defense=current.defense_rating(team_id),
        prior_season_offense=prior_off,
        prior_season_defense=prior_def,
        games_played_this_season=current.games_played_for(team_id),
    )


def build_residual_pool(lines: list[TeamGameLine], ratings: RatingsSnapshot) -> np.ndarray:
    """Paired (home_residual, away_residual) array, one row per completed
    FBS-vs-FBS game in `lines` -- see module docstring for the in-sample
    caveat. Only FBS-vs-FBS games are pooled (mission section 4: FBS-vs-FCS
    is not blended into the main calibration population); FBS-vs-FCS
    projections reuse this SAME pool (a documented simplification -- see
    docs/MILESTONE_C.md) rather than requiring a second, thinner pool.
    """
    # Deliberately uses the RAW (unblended) in-season fitted rating here,
    # not the priors.py season-carryover blend `project_game` applies for
    # a live prediction -- a week-1 training game's residual under an
    # unblended (games_played=0, rating=0.0) expectation is naturally
    # larger, which is an honest reflection of real early-season
    # uncertainty, not a bug. See module docstring's in-sample-residual
    # caveat for the closely related simplification this compounds with.
    for line in lines:
        assert_strictly_before(line.as_of, ratings.as_of, context="build_residual_pool row")

    by_game: dict[str, dict[str, TeamGameLine]] = {}
    for line in lines:
        if line.team_classification != "fbs" or line.opponent_classification != "fbs":
            continue
        by_game.setdefault(line.source_game_id, {})[line.team_id if line.is_home else f"away:{line.team_id}"] = line

    residuals: list[tuple[float, float]] = []
    for rows in by_game.values():
        home_line = next((v for k, v in rows.items() if not k.startswith("away:")), None)
        away_line = next((v for k, v in rows.items() if k.startswith("away:")), None)
        if home_line is None or away_line is None:
            continue
        home_expected = expected_points(
            ratings,
            home_line.team_id,
            away_line.team_id,
            "fbs",
            home_indicator=(0.0 if home_line.is_neutral_site else 1.0),
            away_team_id_for_pace=away_line.team_id,
        )
        away_expected = expected_points(
            ratings,
            away_line.team_id,
            home_line.team_id,
            "fbs",
            home_indicator=(0.0 if away_line.is_neutral_site else -1.0),
            away_team_id_for_pace=home_line.team_id,
        )
        residuals.append((home_line.team_points - home_expected, away_line.team_points - away_expected))

    if not residuals:
        # No FBS-vs-FBS history yet -- fall back to a wide, documented
        # placeholder spread (roughly matches typical CFB score variance)
        # rather than an empty pool a caller could divide-by-zero on.
        rng = np.random.default_rng(0)
        return np.column_stack([rng.normal(0, 14, 5000), rng.normal(0, 14, 5000)])
    return np.array(residuals)


def expected_points(
    ratings: RatingsSnapshot,
    team_id: str,
    opponent_id: str,
    opponent_classification: str | None,
    *,
    home_indicator: float,
    away_team_id_for_pace: str,
) -> float:
    efficiency = (
        ratings.mu
        + ratings.offense_rating(team_id)
        - ratings.opponent_defense_rating(opponent_id, opponent_classification)
        + ratings.hfa * home_indicator
    )
    expected_plays = (ratings.team_pace(team_id) + ratings.team_pace(away_team_id_for_pace)) / 2
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
) -> SimulatedGameProjection:
    """The single entry point research callers (scripts/build_cfb_baseline.py)
    use. `home_classification`/`away_classification` must be genuine,
    leakage-safe classification strings ("fbs"/"fcs") -- an FCS team on
    either side uses the generic FCS pseudo-rating (ratings.py), never an
    individually-fit one, and its offense/defense blend skips the
    season-carryover step entirely (an FCS team was never in the FBS
    rating fit to begin with).
    """
    home_blend = (
        effective_team_rating(home_id, ratings, prior_season_ratings)
        if home_classification == "fbs"
        else BlendedRating(offense=ratings.fcs_offense, defense=ratings.fcs_defense, weight_on_current_season=1.0)
    )
    away_blend = (
        effective_team_rating(away_id, ratings, prior_season_ratings)
        if away_classification == "fbs"
        else BlendedRating(offense=ratings.fcs_offense, defense=ratings.fcs_defense, weight_on_current_season=1.0)
    )

    home_indicator = 0.0 if is_neutral_site else 1.0
    away_indicator = 0.0 if is_neutral_site else -1.0
    expected_plays = (ratings.team_pace(home_id) + ratings.team_pace(away_id)) / 2

    home_efficiency = ratings.mu + home_blend.offense - away_blend.defense + ratings.hfa * home_indicator
    away_efficiency = ratings.mu + away_blend.offense - home_blend.defense + ratings.hfa * away_indicator
    expected_home_points = max(home_efficiency * expected_plays, 0.0)
    expected_away_points = max(away_efficiency * expected_plays, 0.0)

    home_qb_state = classify_continuity(home_percent_passing_ppa)
    away_qb_state = classify_continuity(away_percent_passing_ppa)
    home_scale = uncertainty_multiplier(home_qb_state) * (
        1 + EARLY_SEASON_UNCERTAINTY_SCALE * (1 - home_blend.weight_on_current_season)
    )
    away_scale = uncertainty_multiplier(away_qb_state) * (
        1 + EARLY_SEASON_UNCERTAINTY_SCALE * (1 - away_blend.weight_on_current_season)
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
