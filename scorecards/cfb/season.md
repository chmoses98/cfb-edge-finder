## CFB projection scorecard — season to date

Generated 2026-11-12T00:09:30Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 30973 across 242 games. Settled: 10620 (void/DNP 78, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1378 (175) | 4.083 | +0.223 | 2.855 | 79% | — |
| game_total | pf-cfb (challenger) | 175 (175) | 13.618 | -1.365 | 9.570 | 78% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 175 (175) | 14.014 | -1.225 | — | — |  |
| margin | pf-cfb (challenger) | 175 (175) | 12.851 | +2.759 | 9.138 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 175 (175) | 12.959 | +3.591 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 299 (170) | 7.366 | -0.114 | 5.185 | 84% | — |
| passing_yards | pf-cfb (challenger) | 299 (170) | 62.319 | +1.212 | 44.635 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 2933 (175) | 18.429 | +0.105 | 12.372 | 81% | — |
| receptions | pf-cfb (challenger) | 2933 (175) | 1.312 | -0.007 | 0.881 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1378 (175) | 25.290 | +1.665 | 17.325 | 81% | — |
| team_points | pf-cfb (challenger) | 350 (175) | 9.269 | -0.682 | 6.602 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 350 (175) | 9.351 | -0.615 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.759 (95% CI +0.582..+5.084) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **ALERT** — bias +1.665 (95% CI +0.201..+3.189) exceeds 5% of the mean
- team_points: **OK** — within expectations
