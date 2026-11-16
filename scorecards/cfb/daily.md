## CFB projection scorecard — last 7 days

Generated 2026-11-16T22:45:14Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 34643 across 242 games. Settled: 14870 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 554 (67) | 4.209 | +0.146 | 2.931 | 75% | — |
| game_total | pf-cfb (challenger) | 67 (67) | 13.242 | +3.927 | 8.987 | 82% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 67 (67) | 12.807 | +4.461 | — | — |  |
| margin | pf-cfb (challenger) | 67 (67) | 13.459 | +2.306 | 9.708 | 76% | — |
| margin | script-engine-scoring-baseline (incumbent) | 67 (67) | 14.140 | +1.869 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 126 (67) | 7.942 | +1.706 | 5.543 | 80% | — |
| passing_yards | pf-cfb (challenger) | 126 (67) | 64.900 | +32.921 | 45.568 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 1177 (67) | 17.344 | +3.194 | 11.268 | 82% | — |
| receptions | pf-cfb (challenger) | 1177 (67) | 1.234 | +0.133 | 0.817 | 83% | — |
| rushing_yards | pf-cfb (challenger) | 554 (67) | 27.070 | -0.222 | 18.596 | 77% | — |
| team_points | pf-cfb (challenger) | 134 (67) | 9.583 | +1.964 | 6.634 | 78% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 134 (67) | 9.656 | +2.229 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 67 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 67 settled forecasts (< 100)
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +32.921 (95% CI +18.039..+47.455) exceeds 5% of the mean
- receiving_yards: **ALERT** — bias +3.194 (95% CI +1.788..+4.639) exceeds 5% of the mean
- receptions: **ALERT** — bias +0.133 (95% CI +0.046..+0.223) exceeds 5% of the mean
- rushing_yards: **OK** — within expectations
- team_points: **ALERT** — bias +1.964 (95% CI +0.179..+3.769) exceeds 5% of the mean
