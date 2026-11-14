## CFB projection scorecard — last 28 days

Generated 2026-11-14T18:54:11Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 34051 across 242 games. Settled: 11162 (void/DNP 82, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1451 (184) | 4.165 | +0.218 | 2.911 | 78% | — |
| game_total | pf-cfb (challenger) | 184 (184) | 13.833 | -1.275 | 9.713 | 77% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 184 (184) | 14.249 | -1.107 | — | — |  |
| margin | pf-cfb (challenger) | 184 (184) | 12.724 | +2.925 | 9.052 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 184 (184) | 12.980 | +3.673 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 315 (179) | 7.526 | +0.003 | 5.286 | 83% | — |
| passing_yards | pf-cfb (challenger) | 315 (179) | 63.372 | +2.937 | 45.287 | 85% | — |
| receiving_yards | pf-cfb (challenger) | 3079 (184) | 18.463 | +0.226 | 12.372 | 82% | — |
| receptions | pf-cfb (challenger) | 3079 (184) | 1.315 | +0.001 | 0.881 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1451 (184) | 25.746 | +1.341 | 17.641 | 80% | — |
| team_points | pf-cfb (challenger) | 368 (184) | 9.340 | -0.637 | 6.626 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 368 (184) | 9.433 | -0.555 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.925 (95% CI +0.754..+5.222) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
