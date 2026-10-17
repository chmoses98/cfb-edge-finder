# Projection Foundation — shared contracts (v1)

Every sport repo writes the same two artifacts so SIFT and the cross-sport report can consume
them without per-sport code. The shared statistics library `pf_core/` is vendored byte-identically
into each repo (`pf_core/MANIFEST.json` holds sha256 per file; the canonical copy lives here).

## 1. `research/projection_foundation/results/summary.json` — `sift.projection_foundation.sport_summary.v1`

```jsonc
{
  "schema": "sift.projection_foundation.sport_summary.v1",
  "sport": "NFL",                        // NFL | CFB | NBA | MLB | NHL
  "generated_utc": "2026-10-17T12:00:00Z",
  "code_sha": "<git sha>",
  "pf_core_version": "1.0.0",
  "splits": {                            // chronological, frozen before evaluation
    "development": "2016-2022 seasons",
    "validation":  "2023-2024 seasons",
    "holdout":     "2025 season (evaluated once, frozen candidates only)"
  },
  "inventory": [                         // data-availability matrix rows
    {"dataset": "player box scores", "seasons": "1999-2025", "rows": 0, "pit_class": "archived|reconstructable|retrospective|missing",
     "notes": "..."}
  ],
  "families": [{
    "family": "receptions",              // gate.HARD_MAX_MEAN keys where possible
    "unit": "player-game",
    "opportunity_family": "targets",     // null if the family is itself an opportunity
    "population": "WR/TE/RB with >=1 target in prior 4 games",
    "models": [{
      "model": "B_ewma_shrunk", "split": "validation",
      "n_obs": 0, "n_clusters": 0,
      "mae": 0, "rmse": 0, "bias": 0, "crps": 0, "log_score": 0,
      "cover_50": 0, "cover_80": 0, "cover_90": 0, "pit_var_ratio": 0,
      "vs_baseline": {"mean_diff": 0, "ci95": [0, 0]},   // CRPS (or MAE) difference vs model A; negative = better
      "vs_incumbent": {"mean_diff": 0, "ci95": [0, 0]},  // null when no incumbent exists for this family
      "verdict": "best | competitive | rejected | baseline | incumbent"
    }],
    "best_model": "D_opp_adjusted",
    "holdout": {"model": "D_opp_adjusted", "n_obs": 0, "mae": 0, "crps": 0, "bias": 0, "cover_80": 0,
                "vs_baseline": {}, "vs_incumbent": {}},
    "readiness": "READY | SHADOW VALIDATION | NEEDS IMPROVEMENT | INSUFFICIENT DATA",
    "readiness_reason": "plain English"
  }],
  "experiments": [{"id": "", "question": "", "hypothesis": "", "method": "", "data": "",
                   "result": "", "interpretation": "", "decision": "adopt|reject|investigate|insufficient evidence"}],
  "error_investigations": [{"pattern": "", "frequency": "", "impact": "", "cause": "", "status": "verified defect|hypothesis|ruled out", "action": ""}],
  "opponent_adjustment": {"summary": "", "results": []},
  "opportunity": {"summary": "", "results": []},
  "calibration": {"summary": "", "results": []},
  "failed_experiments": [{"name": "", "what_was_learned": "", "changes_future_decisions": ""}],
  "gate": {"summary": {}, "error_by_status": {}},
  "priorities": [{"rank": 1, "item": "", "impact": "", "evidence": "", "complexity": "", "data": ""}]
}
```

Rules: metrics are computed on independent player-games (never ladder rungs or repeated snapshots);
`n_clusters` = games; confidence intervals are game-clustered bootstrap (`pf_core.metrics.cluster_bootstrap_diff`).
Development results may be iterated on; validation selects; holdout is run once for the frozen winner.

## 2. `sift.projections.v1` — forward projections for the app

```jsonc
{
  "schema": "sift.projections.v1",
  "sport": "NBA",
  "generated_utc": "...",
  "data_asof_utc": "...",                 // newest input used
  "model": {"id": "pf-nba", "version": "pf-1.0.0+<sha>", "authority": "research_only"},
  "families": {"points": {"readiness": "SHADOW VALIDATION", "model": "C_minutes_x_rate", "reason": "..."}},
  "games":   [{"game_id": "", "start_utc": "", "home": "", "away": "",
               "teams": [{"team": "", "family": "team_points", "mean": 0, "p10": 0, "p90": 0}]}],
  "players": [{
    "player_id": "", "name": "", "team": "", "opponent": "", "game_id": "",
    "family": "points", "mean": 0, "median": 0, "p10": 0, "p90": 0,
    "thresholds": {"10": 0.93, "15": 0.78},          // P(X >= k), from one pmf (monotone)
    "opportunity": {"family": "minutes", "mean": 0, "p10": 0, "p90": 0},
    "opponent_adjustment": {"factor": 1.0, "sample_games": 0},
    "adjustments": ["plain-English explanation of each major adjustment"],
    "readiness": "SHADOW VALIDATION",
    "gate": {"status": "PASS|REVIEW|BLOCK", "findings": [{"check": "", "severity": "", "what": "", "category": "", "action": ""}]},
    "data_asof_utc": ""
  }]
}
```

`authority` is always `research_only`: projections are informational and never change betting authority.
A BLOCK projection is still written (so the app can explain why it is withheld) but SIFT must not display its numbers.
