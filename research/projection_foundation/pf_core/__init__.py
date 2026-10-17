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
"""
VERSION = "1.0.0"
