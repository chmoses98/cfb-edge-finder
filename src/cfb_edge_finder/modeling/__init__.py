"""Milestone C: baseline CFB projection engine.

This package builds real GameDistribution values (mean/sd/correlation per
team score) that cfb_edge_finder.projections.distribution.price_market()
can price -- Milestone A built the pricer against only synthetic
distributions in tests; this is where genuine ones come from.

See docs/MILESTONE_C.md for the full methodology: data audit, leakage
policy, team-strength construction, pace, home-field advantage,
early-season priors, QB continuity, the scoring distribution, and
backtest results.

*** RESEARCH-ONLY. NOT A BETTING ENGINE. ***
Nothing in this package recommends a wager, sizes a stake, classifies an
edge tier, or calls a trading endpoint -- see
tests/test_no_recommendation_surface.py, which scans this package too.
"""
