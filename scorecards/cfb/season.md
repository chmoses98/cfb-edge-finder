## CFB projection scorecard — season to date

Generated 2026-11-27T01:46:21Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 50164 across 381 games. Settled: 19060 (void/DNP 152, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 2484 (314) | 4.117 | +0.185 | 2.867 | 78% | — |
| game_total | pf-cfb (challenger) | 314 (314) | 12.995 | +0.150 | 9.052 | 81% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 312 (312) | 13.206 | +0.323 | — | — |  |
| margin | pf-cfb (challenger) | 314 (314) | 12.679 | +2.591 | 9.011 | 79% | — |
| margin | script-engine-scoring-baseline (incumbent) | 312 (312) | 12.909 | +2.872 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 549 (308) | 7.177 | +0.550 | 5.128 | 84% | — |
| passing_yards | pf-cfb (challenger) | 549 (308) | 62.463 | +8.214 | 44.558 | 87% | — |
| receiving_yards | pf-cfb (challenger) | 5245 (314) | 18.147 | +0.803 | 12.117 | 82% | — |
| receptions | pf-cfb (challenger) | 5245 (314) | 1.292 | +0.039 | 0.864 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 2484 (314) | 25.871 | +0.863 | 17.717 | 80% | — |
| team_points | pf-cfb (challenger) | 628 (314) | 9.039 | +0.075 | 6.386 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 624 (312) | 9.145 | +0.160 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.591 (95% CI +1.038..+4.404) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
