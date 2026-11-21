## CFB projection scorecard — season to date

Generated 2026-11-21T00:15:38Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 42793 across 312 games. Settled: 15198 (void/DNP 124, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1981 (248) | 4.131 | +0.175 | 2.886 | 78% | — |
| game_total | pf-cfb (challenger) | 248 (248) | 13.543 | -0.073 | 9.411 | 79% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 248 (248) | 13.723 | +0.181 | — | — |  |
| margin | pf-cfb (challenger) | 248 (248) | 12.935 | +2.657 | 9.226 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 248 (248) | 13.227 | +3.177 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 436 (243) | 7.537 | +0.564 | 5.287 | 82% | — |
| passing_yards | pf-cfb (challenger) | 436 (243) | 62.651 | +11.095 | 44.635 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 4190 (248) | 18.059 | +1.076 | 12.012 | 82% | — |
| receptions | pf-cfb (challenger) | 4190 (248) | 1.288 | +0.040 | 0.861 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1981 (248) | 25.931 | +0.882 | 17.774 | 80% | — |
| team_points | pf-cfb (challenger) | 496 (248) | 9.315 | -0.036 | 6.589 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 496 (248) | 9.402 | +0.089 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.657 (95% CI +0.759..+4.665) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +11.095 (95% CI +3.917..+18.977) exceeds 5% of the mean
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
