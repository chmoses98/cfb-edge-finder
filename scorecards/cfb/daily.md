## CFB projection scorecard — last 7 days

Generated 2026-11-15T19:28:49Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 34643 across 242 games. Settled: 14804 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 546 (66) | 4.205 | +0.155 | 2.929 | 75% | — |
| game_total | pf-cfb (challenger) | 66 (66) | 13.306 | +3.850 | 9.041 | 82% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 66 (66) | 12.876 | +4.403 | — | — |  |
| margin | pf-cfb (challenger) | 66 (66) | 13.604 | +2.400 | 9.794 | 76% | — |
| margin | script-engine-scoring-baseline (incumbent) | 66 (66) | 14.277 | +1.974 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 124 (66) | 7.926 | +1.589 | 5.541 | 81% | — |
| passing_yards | pf-cfb (challenger) | 124 (66) | 64.287 | +31.791 | 45.308 | 85% | — |
| receiving_yards | pf-cfb (challenger) | 1158 (66) | 17.359 | +3.031 | 11.310 | 82% | — |
| receptions | pf-cfb (challenger) | 1158 (66) | 1.235 | +0.118 | 0.820 | 83% | — |
| rushing_yards | pf-cfb (challenger) | 546 (66) | 27.044 | -0.064 | 18.545 | 78% | — |
| team_points | pf-cfb (challenger) | 132 (66) | 9.659 | +1.925 | 6.684 | 77% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 132 (66) | 9.739 | +2.200 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 66 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 66 settled forecasts (< 100)
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +31.791 (95% CI +18.759..+47.066) exceeds 5% of the mean
- receiving_yards: **ALERT** — bias +3.031 (95% CI +1.835..+4.312) exceeds 5% of the mean
- receptions: **ALERT** — bias +0.118 (95% CI +0.029..+0.204) exceeds 5% of the mean
- rushing_yards: **OK** — within expectations
- team_points: **ALERT** — bias +1.925 (95% CI +0.136..+3.825) exceeds 5% of the mean
