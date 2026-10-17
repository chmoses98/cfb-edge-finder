"""SIFT Projection Foundation core (shared, vendored identically into every sport repo).

Modules
  dists     batched discrete distributions (Poisson, NB2, hurdle, discretized normal, empirical)
  metrics   point / distribution / calibration metrics, game-clustered bootstrap comparisons
  pit       point-in-time features, empirical-Bayes shrinkage, schedule-adjusted ridge ratings,
            rolling-origin walk-forward driver
  models    reusable candidate model builders (A..F) operating on PIT feature frames
  registry  immutable experiment records + markdown tables
  gate      Projection Quality Gate (PASS / REVIEW / BLOCK)

The canonical copy lives in sift-sports-intelligence/research/projection_foundation/pf_core.
VERSION is bumped on every change; tests/test_pf_core.py pins behaviour.

1.1.0 (2026-10-17) fixes found by the sport studies:
  * models.group_prior_pit / pit.rolling_opponent_allowed: first-date fallback no longer uses the full-sample mean
    (look-ahead); PIT all-group mean, then caller fallback, else NaN. [NHL]
  * dists: outcomes outside the support score log(EPS), full CRPS tail and PIT 0/1 instead of being clipped. [MLB]
  * pit.lagged_features keeps the caller's index. [MLB, CFB]
  * pit.rolling_ridge_ratings: pool_map, window_seasons, halflife_within_season, home_unpenalized (defaults = 1.0.0). [CFB]
  * metrics.cluster_bootstrap_diff returns NaNs on empty input and treats missing cluster ids as singletons. [CFB, NBA]
  * models.fit_opponent_beta raises on non-finite input instead of returning its first probe. [NBA]
  * models.fit_dist_params records K for poisson/nb. [CFB]
  * gate: sport-aware opponent thresholds, pitcher-family ceilings, graded distribution-mean check. [CFB, MLB]
"""
VERSION = "1.1.0"
