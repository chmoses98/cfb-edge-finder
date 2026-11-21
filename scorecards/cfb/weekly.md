## CFB projection scorecard — last 28 days

Generated 2026-11-21T00:15:38Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 42793 across 312 games. Settled: 15198 (void/DNP 124, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1907 (239) | 4.140 | +0.170 | 2.893 | 77% | — |
| game_total | pf-cfb (challenger) | 239 (239) | 13.485 | +0.022 | 9.350 | 79% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 239 (239) | 13.649 | +0.388 | — | — |  |
| margin | pf-cfb (challenger) | 239 (239) | 13.054 | +2.636 | 9.316 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 239 (239) | 13.270 | +3.200 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 418 (234) | 7.522 | +0.530 | 5.275 | 82% | — |
| passing_yards | pf-cfb (challenger) | 418 (234) | 62.827 | +10.549 | 44.663 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 4020 (239) | 18.036 | +1.066 | 12.007 | 82% | — |
| receptions | pf-cfb (challenger) | 4020 (239) | 1.285 | +0.044 | 0.859 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1907 (239) | 25.994 | +0.891 | 17.834 | 80% | — |
| team_points | pf-cfb (challenger) | 478 (239) | 9.310 | +0.011 | 6.590 | 79% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 478 (239) | 9.377 | +0.192 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.636 (95% CI +0.660..+4.611) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +10.549 (95% CI +3.201..+17.883) exceeds 5% of the mean
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
