## CFB projection scorecard — season to date

Generated 2026-11-22T19:33:14Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 44327 across 312 games. Settled: 18946 (void/DNP 150, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 2467 (312) | 4.115 | +0.177 | 2.869 | 78% | — |
| game_total | pf-cfb (challenger) | 312 (312) | 13.000 | +0.112 | 9.059 | 81% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 310 (310) | 13.232 | +0.282 | — | — |  |
| margin | pf-cfb (challenger) | 312 (312) | 12.702 | +2.550 | 9.032 | 79% | — |
| margin | script-engine-scoring-baseline (incumbent) | 310 (310) | 12.934 | +2.832 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 546 (306) | 7.205 | +0.561 | 5.143 | 84% | — |
| passing_yards | pf-cfb (challenger) | 546 (306) | 62.441 | +8.086 | 44.584 | 87% | — |
| receiving_yards | pf-cfb (challenger) | 5216 (312) | 18.151 | +0.793 | 12.124 | 82% | — |
| receptions | pf-cfb (challenger) | 5216 (312) | 1.293 | +0.037 | 0.865 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 2467 (312) | 25.863 | +0.858 | 17.725 | 80% | — |
| team_points | pf-cfb (challenger) | 624 (312) | 9.048 | +0.056 | 6.396 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 620 (310) | 9.158 | +0.140 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.550 (95% CI +0.913..+4.103) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
