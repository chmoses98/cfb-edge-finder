## CFB projection scorecard — last 28 days

Generated 2026-11-26T01:14:34Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 50132 across 381 games. Settled: 19060 (void/DNP 152, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 2007 (254) | 4.117 | +0.203 | 2.869 | 77% | — |
| game_total | pf-cfb (challenger) | 254 (254) | 12.454 | +0.682 | 8.649 | 84% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 252 (252) | 12.631 | +0.931 | — | — |  |
| margin | pf-cfb (challenger) | 254 (254) | 12.820 | +3.081 | 9.153 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 252 (252) | 13.225 | +3.197 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 448 (249) | 7.078 | +0.694 | 5.068 | 84% | — |
| passing_yards | pf-cfb (challenger) | 448 (249) | 61.678 | +9.493 | 43.934 | 88% | — |
| receiving_yards | pf-cfb (challenger) | 4225 (254) | 18.159 | +0.911 | 12.109 | 82% | — |
| receptions | pf-cfb (challenger) | 4225 (254) | 1.287 | +0.052 | 0.862 | 82% | — |
| rushing_yards | pf-cfb (challenger) | 2007 (254) | 25.778 | +1.021 | 17.633 | 80% | — |
| team_points | pf-cfb (challenger) | 508 (254) | 8.955 | +0.341 | 6.305 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 504 (252) | 9.099 | +0.464 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +3.081 (95% CI +1.351..+5.009) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
