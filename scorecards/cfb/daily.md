## CFB projection scorecard — last 7 days

Generated 2026-11-20T00:49:55Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 41743 across 312 games. Settled: 15150 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 545 (66) | 4.125 | +0.064 | 2.886 | 76% | — |
| game_total | pf-cfb (challenger) | 66 (66) | 13.436 | +3.068 | 9.071 | 82% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 66 (66) | 13.000 | +3.470 | — | — |  |
| margin | pf-cfb (challenger) | 66 (66) | 13.502 | +2.199 | 9.648 | 77% | — |
| margin | script-engine-scoring-baseline (incumbent) | 66 (66) | 13.936 | +2.121 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 125 (66) | 7.691 | +2.046 | 5.371 | 84% | — |
| passing_yards | pf-cfb (challenger) | 125 (66) | 62.738 | +31.964 | 43.964 | 87% | — |
| receiving_yards | pf-cfb (challenger) | 1154 (66) | 17.020 | +3.532 | 11.027 | 83% | — |
| receptions | pf-cfb (challenger) | 1154 (66) | 1.213 | +0.161 | 0.801 | 83% | — |
| rushing_yards | pf-cfb (challenger) | 545 (66) | 26.838 | -0.679 | 18.459 | 78% | — |
| team_points | pf-cfb (challenger) | 132 (66) | 9.518 | +1.534 | 6.647 | 78% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 132 (66) | 9.581 | +1.734 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 66 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 66 settled forecasts (< 100)
- passing_attempts: **ALERT** — bias +2.046 (95% CI +0.304..+3.583) exceeds 5% of the mean
- passing_yards: **ALERT** — bias +31.964 (95% CI +19.035..+46.878) exceeds 5% of the mean
- receiving_yards: **ALERT** — bias +3.532 (95% CI +2.220..+4.835) exceeds 5% of the mean
- receptions: **ALERT** — bias +0.161 (95% CI +0.063..+0.246) exceeds 5% of the mean
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
