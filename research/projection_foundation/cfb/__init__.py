"""College-football Projection Foundation (research only; never betting authority).

Modules
  paths      cache locations + idempotent fetchers (research-data cache, sportsdataverse releases)
  data       team-game / game / player-game frames 2014-2026 with source + point-in-time classes
  ratings    schedule-adjusted ridge ratings (pf_core.rolling_ridge_ratings) with CFB handling:
             season-blocked walk-forward, FCS pooling, preseason priors
  team_models  A..F ladder for team families
  joint      coherent joint score distributions (team points -> total, margin)
  defense_diag root-cause analysis of the script engine's defensive rankings
  players    player-game ladder (passing, rushing, receiving)
  run_walkforward  end-to-end backtest -> results/summary.json + RESULTS_CFB.md
  project    forward sift.projections.v1 for the current week
"""
