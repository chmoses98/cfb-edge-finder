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
from cfb_edge_finder.modeling.margin_calibration import IsotonicMarginModel, LinearMarginParams
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

DEFAULT_RESIDUAL_SCALE = 0.85
"""Milestone C.2 ADOPTED uncertainty-calibration default: a single,
global multiplier applied to EVERY simulated residual draw (on top of,
not instead of, the QB-continuity/early-season/FCS-involved multipliers
above -- this one is uniform across all of them). 1.0 (the Milestone C
default, still available as an explicit opt-out) is a true no-op. 0.85
was selected on 2022-2024 DEVELOPMENT data ONLY, from a live walk-forward
ablation against 0.90 and 1.0 (never fit by eyeballing a target coverage
number directly), because it dominated both alternatives simultaneously
on winner LL/Brier/margin MAE/RMSE while also bringing margin/total 90%
interval coverage closer to the nominal 90% target -- see
docs/MILESTONE_C2.md "Uncertainty calibration" for the full ablation and
the untouched 2025 confirmation check."""

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
    pace_mode: str = "matchup",
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


@dataclass(frozen=True)
class CorrectedGameProjection:
    """Wraps a `SimulatedGameProjection` with Milestone C.2 Part 3's
    `margin_correction_method="linear"` favorite-tail correction applied
    -- the SAME correction (identically parameterized: this module never
    reimplements `LinearMarginParams`/`IsotonicMarginModel`) that
    `modeling.backtest.run_walk_forward_backtest` validated. `margin_delta`
    is 0.0 for a true no-op (method="none", a non-FBS-vs-FBS game, or an
    as-of predating the frozen artifact's training cutoff -- see
    `apply_margin_correction` below and `margin_correction_artifact.py`'s
    leakage-safety note).

    *** HOW HOME/AWAY SCORE MEANS ARE ADJUSTED, PRESERVING TOTAL ***
    The correction changes the model's projected MARGIN by `margin_delta`
    (corrected margin - raw margin). Since margin = home - away and
    total = home + away, shifting home by +margin_delta/2 and away by
    -margin_delta/2 changes margin by exactly margin_delta while leaving
    total EXACTLY unchanged -- the unique, symmetric way to move margin
    without moving total at all. This matches
    `total_correction_method="none"` (docs/MILESTONE_C2.md section 35):
    the total channel is genuinely untouched, not just "not further
    corrected." Both means are floored at 0.0, mirroring `project_game`'s
    own `max(expected_points, 0.0)` floor.

    *** WHY THE CORRECTION IS APPLIED TO THE DETERMINISTIC POINT ESTIMATE,
    NOT THE SIMULATED MEAN MARGIN *** `run_walk_forward_backtest` fits/
    applies its correction against `model_margin_mean`, the MONTE CARLO
    mean of `home_scores - away_scores`. This module instead applies the
    frozen artifact to `raw.expected_home_points - raw.expected_away_points`
    -- the pre-simulation, deterministic point estimate -- so that
    `expected_home_points - expected_away_points == expected_margin`
    holds EXACTLY (not merely "up to Monte Carlo noise") for every
    live projection, satisfying this pass's "internally coherent"
    requirement without qualification. The two quantities differ only by
    the residual pool's sample mean, which is approximately zero by
    construction (a systematic non-zero residual mean would itself be a
    ratings-fit bias the ridge regression would already have absorbed) --
    a documented, deliberate, and negligible simplification relative to
    training, not a silent one.

    *** WHY WIN PROBABILITY IS UNCHANGED ***
    `prob_home_win`/`prob_away_win` below read directly from the wrapped,
    UNCORRECTED `raw` projection's own simulated home/away score
    comparison -- identical to how `run_walk_forward_backtest` computes
    `model_prob_home_win` BEFORE either correction is applied, and never
    touches it afterward (margin_calibration.py's "WHY THIS TOUCHES ONLY
    THE MARGIN CHANNEL" note). This is deliberate parity: the win-
    probability channel Milestone C.2's backtests validated is exactly
    the channel this live path reports, with zero divergence introduced
    by the margin correction.

    *** WHY MARGIN/TOTAL THRESHOLD PROBABILITIES DON'T MUTATE `raw` ***
    `prob_margin_greater_than`/`to_game_distribution`'s home/away means
    below are computed by adding the scalar `margin_delta` (or +-
    `margin_delta / 2`) to values derived from `raw.home_scores`/
    `raw.away_scores`, never by mutating those arrays in place -- adding a
    constant to every simulated draw before a `>` comparison is exactly
    equivalent to shifting the arrays themselves (order-preserving, so
    threshold probabilities stay monotonic in the threshold), and standard
    deviation/correlation are invariant under a constant shift, so
    `raw.to_game_distribution()`'s spread/correlation numbers are reused
    unchanged rather than recomputed -- exact, not an approximation.
    """

    raw: SimulatedGameProjection
    margin_delta: float
    method: str
    is_fbs_vs_fbs: bool
    correction_applied: bool
    correction_skip_reason: str | None
    artifact_version: str | None

    @property
    def raw_expected_margin(self) -> float:
        return self.raw.expected_home_points - self.raw.expected_away_points

    @property
    def expected_home_points(self) -> float:
        return max(self.raw.expected_home_points + self.margin_delta / 2, 0.0)

    @property
    def expected_away_points(self) -> float:
        return max(self.raw.expected_away_points - self.margin_delta / 2, 0.0)

    @property
    def expected_margin(self) -> float:
        return self.raw_expected_margin + self.margin_delta

    @property
    def expected_total(self) -> float:
        # total_correction_method="none" this pass -- genuinely unchanged,
        # never derived from expected_home_points/expected_away_points
        # above (which individually move) so this stays exactly the raw
        # sum even after the 0.0 floor on either side.
        return self.raw.expected_home_points + self.raw.expected_away_points

    def prob_home_win(self) -> float:
        return self.raw.prob_home_win()

    def prob_away_win(self) -> float:
        return self.raw.prob_away_win()

    def prob_margin_greater_than(self, threshold: float) -> float:
        shifted_margins = (self.raw.home_scores - self.raw.away_scores) + self.margin_delta
        return float(np.mean(shifted_margins > threshold))

    def prob_total_greater_than(self, threshold: float) -> float:
        return self.raw.prob_total_greater_than(threshold)

    def to_game_distribution(self) -> GameDistribution:
        raw_dist = self.raw.to_game_distribution()
        return GameDistribution(
            home_mean=max(raw_dist.home_mean + self.margin_delta / 2, 0.0),
            away_mean=max(raw_dist.away_mean - self.margin_delta / 2, 0.0),
            home_sd=raw_dist.home_sd,
            away_sd=raw_dist.away_sd,
            correlation=raw_dist.correlation,
        )

    def to_uncertainty_profile(self) -> UncertaintyProfile:
        return self.raw.to_uncertainty_profile()

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


def apply_margin_correction(
    projection: SimulatedGameProjection,
    *,
    is_fbs_vs_fbs: bool,
    method: str,
    correction_model: LinearMarginParams | IsotonicMarginModel | None,
    artifact_version: str | None,
    as_of: AsOf,
    training_cutoff: AsOf | None,
) -> CorrectedGameProjection:
    """The single entry point `scripts/build_cfb_baseline.py` uses to
    apply Milestone C.2 Part 3's margin correction to a live projection --
    mirrors `margin_calibration.correct_margin`'s role in
    `run_walk_forward_backtest`, but against a frozen `correction_model`
    (see margin_correction_artifact.py) instead of a walk-forward-fit one.

    Skips (returns `margin_delta=0.0`, `correction_applied=False`) with an
    explicit, distinguishable `correction_skip_reason` when:
      - `method == "none"` (correction disabled entirely) -- reason
        "method_none".
      - `not is_fbs_vs_fbs` -- FBS-vs-FCS games are never corrected,
        matching margin_calibration.py's FBS-vs-FBS-only fit population
        and backtest.py's identical restriction -- reason "not_fbs_vs_fbs".
      - `training_cutoff is not None and as_of.is_strictly_before(training_cutoff)`
        -- the frozen artifact's training data would be at or after this
        projection's own as-of point, which would be leakage -- reason
        "as_of_predates_training_cutoff".
      - `correction_model is None` -- reason "no_correction_model".
      - `correction_model.is_identity_fallback` -- the underlying fit
        itself identity-fell-back (below minimum history / degenerate
        slope) -- reason "identity_fallback".
    """
    if method == "none":
        return CorrectedGameProjection(
            raw=projection,
            margin_delta=0.0,
            method=method,
            is_fbs_vs_fbs=is_fbs_vs_fbs,
            correction_applied=False,
            correction_skip_reason="method_none",
            artifact_version=artifact_version,
        )
    if not is_fbs_vs_fbs:
        return CorrectedGameProjection(
            raw=projection,
            margin_delta=0.0,
            method=method,
            is_fbs_vs_fbs=is_fbs_vs_fbs,
            correction_applied=False,
            correction_skip_reason="not_fbs_vs_fbs",
            artifact_version=artifact_version,
        )
    if training_cutoff is not None and as_of.is_strictly_before(training_cutoff):
        return CorrectedGameProjection(
            raw=projection,
            margin_delta=0.0,
            method=method,
            is_fbs_vs_fbs=is_fbs_vs_fbs,
            correction_applied=False,
            correction_skip_reason="as_of_predates_training_cutoff",
            artifact_version=artifact_version,
        )
    if correction_model is None:
        return CorrectedGameProjection(
            raw=projection,
            margin_delta=0.0,
            method=method,
            is_fbs_vs_fbs=is_fbs_vs_fbs,
            correction_applied=False,
            correction_skip_reason="no_correction_model",
            artifact_version=artifact_version,
        )
    if correction_model.is_identity_fallback:
        return CorrectedGameProjection(
            raw=projection,
            margin_delta=0.0,
            method=method,
            is_fbs_vs_fbs=is_fbs_vs_fbs,
            correction_applied=False,
            correction_skip_reason="identity_fallback",
            artifact_version=artifact_version,
        )

    raw_margin = projection.expected_home_points - projection.expected_away_points
    corrected_margin = float(correction_model.apply(np.array([raw_margin]))[0])
    return CorrectedGameProjection(
        raw=projection,
        margin_delta=corrected_margin - raw_margin,
        method=method,
        is_fbs_vs_fbs=is_fbs_vs_fbs,
        correction_applied=True,
        correction_skip_reason=None,
        artifact_version=artifact_version,
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
