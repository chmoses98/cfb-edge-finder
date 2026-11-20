## CFB projection scorecard — season to date

Generated 2026-11-20T00:49:55Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 41743 across 312 games. Settled: 15150 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1971 (247) | 4.126 | +0.171 | 2.883 | 78% | — |
| game_total | pf-cfb (challenger) | 247 (247) | 13.566 | -0.041 | 9.426 | 79% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 247 (247) | 13.752 | +0.209 | — | — |  |
| margin | pf-cfb (challenger) | 247 (247) | 12.958 | +2.638 | 9.244 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 247 (247) | 13.240 | +3.150 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 435 (242) | 7.548 | +0.572 | 5.293 | 82% | — |
| passing_yards | pf-cfb (challenger) | 435 (242) | 62.773 | +11.098 | 44.689 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 4181 (247) | 18.051 | +1.089 | 12.001 | 82% | — |
| receptions | pf-cfb (challenger) | 4181 (247) | 1.287 | +0.041 | 0.860 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1971 (247) | 25.886 | +0.878 | 17.748 | 80% | — |
| team_points | pf-cfb (challenger) | 494 (247) | 9.337 | -0.020 | 6.600 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 494 (247) | 9.419 | +0.102 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.638 (95% CI +0.984..+4.603) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +11.098 (95% CI +3.112..+19.006) exceeds 5% of the mean
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
