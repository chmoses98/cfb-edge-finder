## CFB projection scorecard — season to date

Generated 2026-11-18T23:49:15Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 40477 across 312 games. Settled: 15038 (void/DNP 122, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1956 (245) | 4.130 | +0.183 | 2.884 | 78% | — |
| game_total | pf-cfb (challenger) | 245 (245) | 13.542 | +0.093 | 9.417 | 79% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 245 (245) | 13.712 | +0.362 | — | — |  |
| margin | pf-cfb (challenger) | 245 (245) | 12.990 | +2.731 | 9.260 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 245 (245) | 13.234 | +3.187 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 431 (240) | 7.560 | +0.520 | 5.299 | 82% | — |
| passing_yards | pf-cfb (challenger) | 431 (240) | 63.062 | +10.908 | 44.871 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 4152 (245) | 18.074 | +1.068 | 12.020 | 82% | — |
| receptions | pf-cfb (challenger) | 4152 (245) | 1.290 | +0.037 | 0.862 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1956 (245) | 25.853 | +1.011 | 17.725 | 80% | — |
| team_points | pf-cfb (challenger) | 490 (245) | 9.340 | +0.047 | 6.603 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 490 (245) | 9.421 | +0.179 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.731 (95% CI +0.659..+4.806) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +10.908 (95% CI +3.170..+19.603) exceeds 5% of the mean
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
