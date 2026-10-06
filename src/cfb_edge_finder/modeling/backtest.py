"""Walk-forward chronological backtest (mission spec section 13).

*** WHY WALK-FORWARD, NOT A RANDOM TRAIN/TEST SPLIT ***
A random split would let games from later weeks/seasons inform ratings
used to predict earlier ones -- exactly the leakage this whole package
exists to prevent (see leakage.py). This engine instead walks forward one
week at a time: for each (season, week) that has games, it fits ratings
using ONLY strictly-prior weeks (via ratings.fit_fbs_efficiency_ratings,
which itself leakage-checks every row), generates a projection for every
game in that week, then advances. No week's outcome is ever visible to
the ratings used to predict it.

Both the naive benchmark (naive_benchmark.py) and the full model
(ratings.py + priors.py + qb_continuity.py + score_model.py) are evaluated
on the EXACT SAME walk-forward schedule and the exact same held-out games,
so `compare_benchmark_vs_model` is a genuine apples-to-apples comparison,
not two differently-evaluated numbers.

*** MILESTONE C HARDENING: EXPANDING RESIDUAL POOL + CALIBRATION ***
This engine now maintains two additional pieces of state as it walks
forward, both strictly built from games completed before the week being
predicted:
  1. An EXPANDING residual-pool accumulator (score_model.py's
     `_residuals_for_pairs`/`_fallback_residual_pool`) -- each week's
     FBS-vs-FBS games are scored against THAT week's out-of-sample ratings
     (fit from only strictly-prior weeks) to produce genuine out-of-sample
     residuals, which are then added to the pool used for FUTURE weeks'
     simulations. This replaces the original V1's in-sample residual pool
     (see score_model.py's module docstring) -- no week's own outcome
     ever informs its own uncertainty band, and the residual SHAPE is now
     a true predictive-error estimate, not a fitted-residual one.
  2. A calibration model (calibration.py) refit at every step from the
     ACCUMULATED (raw model probability, actual outcome) history so far,
     applied only to the current week's raw probabilities -- see
     `GameOutcome.calibrated_prob_home_win`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np

from cfb_edge_finder.modeling.calibration import calibrate
from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.modeling.margin_calibration import correct_margin
from cfb_edge_finder.modeling.naive_benchmark import fit_naive_benchmark, naive_expected_scores
from cfb_edge_finder.modeling.priors import DEFAULT_SEASON_SHRINKAGE_K
from cfb_edge_finder.modeling.ratings import (
    DEFAULT_FCS_RIDGE_LAMBDA,
    DEFAULT_PACE_SHRINKAGE_K,
    DEFAULT_RIDGE_LAMBDA,
    fit_fbs_efficiency_ratings,
)
from cfb_edge_finder.modeling.score_model import (
    DEFAULT_MIN_RESIDUAL_POOL_SIZE,
    DEFAULT_RESIDUAL_SCALE,
    _fallback_residual_pool,
    _paired_fbs_games,
    _residuals_for_pairs,
    project_game,
)
from cfb_edge_finder.modeling.total_calibration import (
    correct_total_direct,
    correct_total_via_margin_residual,
)

DEFAULT_CALIBRATION_METHOD = "platt"
"""See docs/MILESTONE_C.md "Calibration" for the genuine held-out
comparison against "isotonic" and "none" that this default was chosen
from."""

NAIVE_MARGIN_SD = 17.0
"""A single, fixed, empirically-plausible CFB margin standard deviation
(round number in the range historical FBS-vs-FBS margin SDs typically
fall in) used ONLY to turn the naive benchmark's point margin estimate
into a win probability for log-loss/Brier comparison -- the naive
benchmark is intentionally not opponent-adjusted or team-specific, so its
uncertainty shouldn't be either. See docs/MILESTONE_C.md "Benchmark
comparison" for why this single global constant, rather than the full
model's per-game simulated uncertainty, is the fair thing to compare
against."""

EPS = 1e-9


@dataclass(frozen=True)
class GameOutcome:
    source_game_id: str
    season: int
    week: int
    home_id: str
    away_id: str
    # Historical, season-scoped conference identity ONLY -- copied straight
    # from TeamGameLine.team_conference/opponent_conference/is_conference_game
    # (CFBD's own homeConference/awayConference/conferenceGame fields on the
    # raw game row), never derived from teams.registry's single current
    # (2026) snapshot. See modeling/diagnostics.py's is_conference_game.
    home_conference: str | None
    away_conference: str | None
    is_conference_game: bool | None
    is_neutral_site: bool
    is_fbs_vs_fbs: bool
    actual_home_points: int
    actual_away_points: int
    naive_prob_home_win: float
    naive_margin: float
    naive_total: float
    model_prob_home_win: float
    calibrated_prob_home_win: float
    model_margin_mean: float
    model_total_mean: float
    model_margin_p05: float
    model_margin_p95: float
    model_total_p05: float
    model_total_p95: float
    # Milestone C.2 totals-diagnosis fields: every value here is exactly
    # what the model itself used to build this game's projection (the
    # SAME `ratings` snapshot -- fit strictly before this game's as_of --
    # that produced model_margin_mean/model_total_mean above), threaded
    # through purely for POST-HOC segmentation. Never fed back into any
    # prediction -- see diagnostics.py's prediction-boundary docstring.
    model_expected_plays: float
    """(home pace + away pace) / 2 under whichever pace_mode fit `ratings`
    -- a genuinely pregame-known tempo estimate, usable for a "high-tempo
    vs low-tempo" diagnostic split without any new leakage surface."""
    home_offense_rating: float
    away_offense_rating: float
    home_defense_rating: float
    away_defense_rating: float
    """Higher defense_rating = stronger (more points-suppressing) defense,
    per ratings.py's `points_per_play ~= mu + offense[team] -
    defense[opponent] + hfa*home` sign convention. 0.0 for an FCS
    opponent (ratings.offense_rating/defense_rating default for any
    team_id absent from the fitted FBS dict), consistent with how the
    rest of this module already treats an unrated team."""


def _log_loss_term(p: float, outcome: int) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return -(outcome * np.log(p) + (1 - outcome) * np.log(1 - p))


def _group_lines_by_game(lines: list[TeamGameLine]) -> dict[str, dict[str, TeamGameLine]]:
    by_game: dict[str, dict[str, TeamGameLine]] = {}
    for line in lines:
        by_game.setdefault(line.source_game_id, {})["home" if line.is_home else "away"] = line
    return by_game


def run_walk_forward_backtest(
    lines: list[TeamGameLine],
    *,
    min_week_for_first_prediction: int = 1,
    n_simulations: int = 4_000,
    seed: int = 0,
    calibration_method: str = DEFAULT_CALIBRATION_METHOD,
    min_residual_pool_size: int = DEFAULT_MIN_RESIDUAL_POOL_SIZE,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    fcs_ridge_lambda: float = DEFAULT_FCS_RIDGE_LAMBDA,
    pace_shrinkage_k: float = DEFAULT_PACE_SHRINKAGE_K,
    season_shrinkage_k: float = DEFAULT_SEASON_SHRINKAGE_K,
    fcs_mode: str = "pooled",
    pace_mode: str = "symmetric",
    residual_scale: float = DEFAULT_RESIDUAL_SCALE,
    margin_correction_method: str = "none",
    total_correction_method: str = "none",
    total_correction_predictor: str = "total",
) -> list[GameOutcome]:
    """Walks every (season, week) that has completed games, strictly in
    chronological order, fitting fresh ratings/naive-benchmark snapshots
    from only-prior weeks each time. `min_week_for_first_prediction`
    lets a caller skip week 0 of the very first season in the corpus
    (which has zero leakage-safe history and would otherwise only be
    predictable from the pure league-average prior) -- see
    docs/MILESTONE_C.md "Backtest methodology."

    Also maintains, strictly incrementally as it walks forward: an
    expanding out-of-sample residual pool (see module docstring's
    "MILESTONE C HARDENING" note) and a calibration model refit each step
    from the (raw probability, outcome) history accumulated so far
    (calibration.py) -- `calibration_method` of "none" disables the
    latter (calibrated_prob_home_win becomes an identity copy of the raw
    probability).

    Milestone C.2 Part 3: `margin_correction_method` ("none" default,
    "linear", or "isotonic") refits a second walk-forward model
    (margin_calibration.py) at every step from the (projected margin,
    actual margin) history of FBS-vs-FBS outcomes accumulated so far, and
    applies it as a uniform location-shift to `model_margin_mean` AND
    both `model_margin_p05`/`model_margin_p95` (never a rescale -- see
    margin_calibration.py's module docstring for why). FBS-vs-FCS games
    are never corrected. `model_prob_home_win`/`calibrated_prob_home_win`
    are untouched by this correction in every case.

    Milestone C.2 Part 3: `total_correction_method` ("none" default,
    "linear", or "isotonic") analogously refits a walk-forward total-
    points correction (total_calibration.py) from FBS-vs-FBS history,
    applied as a location-shift to `model_total_mean`/`model_total_p05`/
    `model_total_p95`. `total_correction_predictor` selects which
    diagnosed mechanism it targets: "total" (direct fit of actual total
    on projected total -- the high-total shootout under-prediction
    pattern) or "margin_magnitude" (fits the total RESIDUAL as a function
    of |projected margin| -- the large-favorite garbage-time suppression
    pattern). See total_calibration.py's module docstring for why these
    are two independent single-predictor candidates rather than one
    combined model.
    """
    by_game = _group_lines_by_game(lines)
    as_of_points = sorted({(g.season, g.week) for g in lines})

    outcomes: list[GameOutcome] = []
    rng_counter = 0
    residual_accumulator: list[tuple[float, float]] = []

    for season, week in as_of_points:
        if week < min_week_for_first_prediction:
            continue
        as_of = AsOf(season=season, week=week)
        history = [ln for ln in lines if ln.as_of.is_strictly_before(as_of)]
        if not history:
            continue

        ratings = fit_fbs_efficiency_ratings(
            history,
            as_of,
            ridge_lambda=ridge_lambda,
            fcs_ridge_lambda=fcs_ridge_lambda,
            pace_shrinkage_k=pace_shrinkage_k,
            fcs_mode=fcs_mode,
            pace_mode=pace_mode,
        )
        naive = fit_naive_benchmark(history, as_of)
        residual_pool = (
            np.array(residual_accumulator)
            if len(residual_accumulator) >= min_residual_pool_size
            else _fallback_residual_pool()
        )

        # Calibration model for THIS week uses only outcomes already
        # accumulated from strictly-prior weeks -- `outcomes` at this
        # point in the loop contains exactly that (see module docstring).
        history_raw = np.array([o.model_prob_home_win for o in outcomes])
        history_y = np.array(
            [1.0 if o.actual_home_points > o.actual_away_points else 0.0 for o in outcomes]
        )

        # Milestone C.2 Part 3: margin-correction history, FBS-vs-FBS
        # outcomes only (see margin_calibration.py's "WHY FBS-vs-FBS
        # ONLY"), from the same strictly-prior `outcomes` accumulated so
        # far as the probability-calibration history above.
        fbs_history = [o for o in outcomes if o.is_fbs_vs_fbs]
        history_margin_projected = np.array([o.model_margin_mean for o in fbs_history])
        history_margin_actual = np.array(
            [o.actual_home_points - o.actual_away_points for o in fbs_history]
        )

        # Milestone C.2 Part 3: total-correction history, FBS-vs-FBS only,
        # same strictly-prior `fbs_history` as the margin correction above.
        # Both predictor histories are precomputed here so either
        # candidate can be applied below without recomputation.
        history_total_projected = np.array([o.model_total_mean for o in fbs_history])
        history_total_actual = np.array(
            [o.actual_home_points + o.actual_away_points for o in fbs_history]
        )
        history_margin_magnitude = np.abs(history_margin_projected)
        history_total_residual = history_total_actual - history_total_projected

        games_this_week = {
            gid: pair for gid, pair in by_game.items() if pair.get("home", pair.get("away")).as_of == as_of
        }

        week_raw_probs: list[float] = []
        week_pending: list[dict] = []

        for game_id, pair in games_this_week.items():
            home = pair.get("home")
            away = pair.get("away")
            if home is None or away is None:
                continue

            rng_counter += 1
            projection = project_game(
                home_id=home.team_id,
                away_id=home.opponent_id,
                home_classification=home.team_classification,
                away_classification=home.opponent_classification,
                is_neutral_site=home.is_neutral_site,
                ratings=ratings,
                prior_season_ratings=None,
                residual_pool=residual_pool,
                home_percent_passing_ppa=None,
                away_percent_passing_ppa=None,
                n_simulations=n_simulations,
                seed=seed + rng_counter,
                season_shrinkage_k=season_shrinkage_k,
                residual_scale=residual_scale,
            )

            naive_home_pts, naive_away_pts = naive_expected_scores(
                naive, home.team_id, home.opponent_id, is_neutral_site=home.is_neutral_site
            )
            naive_margin = naive_home_pts - naive_away_pts
            # P(home wins) under the naive benchmark's Normal(naive_margin, NAIVE_MARGIN_SD)
            # margin assumption -- P(margin > 0).
            naive_prob_home_win = 1 - NormalDist(naive_margin, NAIVE_MARGIN_SD).cdf(0)

            margins = projection.home_scores - projection.away_scores
            totals = projection.home_scores + projection.away_scores
            raw_prob = projection.prob_home_win()
            week_raw_probs.append(raw_prob)

            week_pending.append(
                {
                    "source_game_id": game_id,
                    "season": season,
                    "week": week,
                    "home_id": home.team_id,
                    "away_id": home.opponent_id,
                    "home_conference": home.team_conference,
                    "away_conference": home.opponent_conference,
                    "is_conference_game": home.is_conference_game,
                    "is_neutral_site": home.is_neutral_site,
                    "is_fbs_vs_fbs": (home.team_classification == "fbs" and home.opponent_classification == "fbs"),
                    "actual_home_points": home.team_points,
                    "actual_away_points": home.opponent_points,
                    "naive_prob_home_win": naive_prob_home_win,
                    "naive_margin": naive_margin,
                    "naive_total": naive_home_pts + naive_away_pts,
                    "model_expected_plays": (
                        ratings.team_pace(home.team_id) + ratings.team_pace(home.opponent_id)
                    )
                    / 2,
                    "home_offense_rating": ratings.offense_rating(home.team_id),
                    "away_offense_rating": ratings.offense_rating(home.opponent_id),
                    "home_defense_rating": ratings.defense_rating(home.team_id),
                    "away_defense_rating": ratings.defense_rating(home.opponent_id),
                    "model_prob_home_win": raw_prob,
                    "model_margin_mean": float(np.mean(margins)),
                    "model_total_mean": float(np.mean(totals)),
                    "model_margin_p05": float(np.percentile(margins, 5)),
                    "model_margin_p95": float(np.percentile(margins, 95)),
                    "model_total_p05": float(np.percentile(totals, 5)),
                    "model_total_p95": float(np.percentile(totals, 95)),
                }
            )

        if week_pending:
            calibrated = calibrate(
                method=calibration_method,
                history_raw_probs=history_raw,
                history_outcomes=history_y,
                target_raw_probs=np.array(week_raw_probs),
            )

            # Milestone C.2 Part 3: margin correction, FBS-vs-FBS games
            # only, applied as a uniform shift to model_margin_mean AND
            # both interval bounds so spread/coverage is unaffected (see
            # margin_calibration.py's "WHY A LOCATION SHIFT" note).
            # model_prob_home_win/calibrated_prob_home_win above are
            # computed independently and are never touched here.
            fbs_indices = [i for i, kw in enumerate(week_pending) if kw["is_fbs_vs_fbs"]]
            if fbs_indices and margin_correction_method != "none":
                targets = np.array([week_pending[i]["model_margin_mean"] for i in fbs_indices])
                corrected = correct_margin(
                    method=margin_correction_method,
                    history_projected=history_margin_projected,
                    history_actual=history_margin_actual,
                    target_projected=targets,
                )
                for idx, corrected_value in zip(fbs_indices, corrected, strict=True):
                    delta = float(corrected_value) - week_pending[idx]["model_margin_mean"]
                    week_pending[idx]["model_margin_mean"] += delta
                    week_pending[idx]["model_margin_p05"] += delta
                    week_pending[idx]["model_margin_p95"] += delta

            # Milestone C.2 Part 3: total correction, FBS-vs-FBS games
            # only, same location-shift-only, win-probability-decoupled
            # design as margin correction above -- see
            # total_calibration.py's module docstring. Uses each game's
            # (possibly already margin-corrected, per the block above)
            # model_margin_mean as the "margin_magnitude" predictor, so
            # the two corrections compose coherently when both are active.
            if fbs_indices and total_correction_method != "none":
                total_targets = np.array([week_pending[i]["model_total_mean"] for i in fbs_indices])
                if total_correction_predictor == "total":
                    corrected_totals = correct_total_direct(
                        method=total_correction_method,
                        history_predictor=history_total_projected,
                        history_actual_total=history_total_actual,
                        target_predictor=total_targets,
                    )
                elif total_correction_predictor == "margin_magnitude":
                    margin_magnitude_targets = np.array(
                        [abs(week_pending[i]["model_margin_mean"]) for i in fbs_indices]
                    )
                    corrected_totals = correct_total_via_margin_residual(
                        method=total_correction_method,
                        history_margin_magnitude=history_margin_magnitude,
                        history_total_residual=history_total_residual,
                        target_margin_magnitude=margin_magnitude_targets,
                        target_projected_total=total_targets,
                    )
                else:
                    raise ValueError(f"unknown total_correction_predictor: {total_correction_predictor!r}")
                for idx, corrected_value in zip(fbs_indices, corrected_totals, strict=True):
                    delta = float(corrected_value) - week_pending[idx]["model_total_mean"]
                    week_pending[idx]["model_total_mean"] += delta
                    week_pending[idx]["model_total_p05"] += delta
                    week_pending[idx]["model_total_p95"] += delta

            for kwargs, cal_p in zip(week_pending, calibrated, strict=True):
                outcomes.append(GameOutcome(calibrated_prob_home_win=float(cal_p), **kwargs))

            # Advance the expanding residual accumulator with THIS week's
            # genuine out-of-sample residuals (scored against `ratings`,
            # which is out-of-sample for these games), for use starting
            # next week -- never for this week's own projections above.
            week_lines = [ln for pair in games_this_week.values() for ln in pair.values()]
            residual_accumulator.extend(_residuals_for_pairs(_paired_fbs_games(week_lines), ratings))

    return outcomes


@dataclass(frozen=True)
class BacktestMetrics:
    n_games: int
    winner_log_loss: float
    winner_brier: float
    margin_mae: float
    margin_rmse: float
    margin_bias: float
    margin_interval_coverage_90: float
    total_mae: float
    total_rmse: float
    total_bias: float
    total_interval_coverage_90: float
    calibration_bins: list[dict] = field(default_factory=list)


def _calibration_bins(outcomes: list[GameOutcome], prob_attr: str, n_bins: int = 10) -> list[dict]:
    bins: list[dict] = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        in_bin = [
            o for o in outcomes if lo <= getattr(o, prob_attr) < hi or (b == n_bins - 1 and getattr(o, prob_attr) == hi)
        ]
        if not in_bin:
            continue
        observed = float(np.mean([1 if o.actual_home_points > o.actual_away_points else 0 for o in in_bin]))
        predicted = float(np.mean([getattr(o, prob_attr) for o in in_bin]))
        bins.append(
            {
                "bin": f"[{lo:.1f},{hi:.1f})",
                "predicted_prob": predicted,
                "observed_win_rate": observed,
                "n": len(in_bin),
            }
        )
    return bins


def compute_metrics(outcomes: list[GameOutcome], *, prob_attr: str = "model_prob_home_win") -> BacktestMetrics:
    if not outcomes:
        raise ValueError("compute_metrics called with zero outcomes -- nothing to evaluate")

    y = np.array([1 if o.actual_home_points > o.actual_away_points else 0 for o in outcomes])
    p = np.array([getattr(o, prob_attr) for o in outcomes])
    log_loss = float(np.mean([_log_loss_term(pi, yi) for pi, yi in zip(p, y, strict=True)]))
    brier = float(np.mean((p - y) ** 2))

    actual_margin = np.array([o.actual_home_points - o.actual_away_points for o in outcomes])
    actual_total = np.array([o.actual_home_points + o.actual_away_points for o in outcomes])

    if prob_attr in ("model_prob_home_win", "calibrated_prob_home_win"):
        # Calibration (calibration.py) only recalibrates the WIN
        # probability -- it has no margin/total counterpart, so the
        # calibrated view reuses the model's own margin/total prediction
        # unchanged, exactly like the raw view.
        pred_margin = np.array([o.model_margin_mean for o in outcomes])
        pred_total = np.array([o.model_total_mean for o in outcomes])
        margin_lo = np.array([o.model_margin_p05 for o in outcomes])
        margin_hi = np.array([o.model_margin_p95 for o in outcomes])
        total_lo = np.array([o.model_total_p05 for o in outcomes])
        total_hi = np.array([o.model_total_p95 for o in outcomes])
    else:
        pred_margin = np.array([o.naive_margin for o in outcomes])
        pred_total = np.array([o.naive_total for o in outcomes])
        margin_lo = pred_margin - 1.645 * NAIVE_MARGIN_SD
        margin_hi = pred_margin + 1.645 * NAIVE_MARGIN_SD
        total_lo = pred_total - 1.645 * NAIVE_MARGIN_SD
        total_hi = pred_total + 1.645 * NAIVE_MARGIN_SD

    margin_err = actual_margin - pred_margin
    total_err = actual_total - pred_total

    margin_coverage = float(np.mean((actual_margin >= margin_lo) & (actual_margin <= margin_hi)))
    total_coverage = float(np.mean((actual_total >= total_lo) & (actual_total <= total_hi)))

    return BacktestMetrics(
        n_games=len(outcomes),
        winner_log_loss=log_loss,
        winner_brier=brier,
        margin_mae=float(np.mean(np.abs(margin_err))),
        margin_rmse=float(np.sqrt(np.mean(margin_err**2))),
        margin_bias=float(np.mean(margin_err)),
        margin_interval_coverage_90=margin_coverage,
        total_mae=float(np.mean(np.abs(total_err))),
        total_rmse=float(np.sqrt(np.mean(total_err**2))),
        total_bias=float(np.mean(total_err)),
        total_interval_coverage_90=total_coverage,
        calibration_bins=_calibration_bins(outcomes, prob_attr),
    )


def segment(outcomes: list[GameOutcome], predicate) -> list[GameOutcome]:
    return [o for o in outcomes if predicate(o)]
